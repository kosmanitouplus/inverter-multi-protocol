"""Isolated mpp-solar codecs. Automatic probes never use a setter."""
import copy
import importlib
import math
import re
import sys
import threading
from datetime import datetime

from mppsolar.protocols.protocol_helpers import crcPI

PROTOCOLS = ('PI16', 'PI17', 'PI17INFINI', 'PI17M058', 'PI18', 'PI18SV',
             'PI18LVX', 'PI30', 'PI30MAX', 'PI30REVO', 'PI30M044',
             'PI30M045', 'PI30MST', 'PI41')
_LOCK = threading.Lock()


class ResponseError(ValueError):
    pass


class UnsupportedCommand(ResponseError):
    pass


def load_codec(name):
    name = name.upper()
    if name not in PROTOCOLS:
        raise ValueError(f'Unsupported protocol: {name}')
    with _LOCK:
        module = importlib.import_module('mppsolar.protocols.' + name.lower())
        # Upstream constructors mutate shared module command tables, including
        # parent tables. Restore their originals and detach each instance.
        originals = []
        for key, loaded in list(sys.modules.items()):
            if key.startswith('mppsolar.protocols.'):
                for attr, value in vars(loaded).items():
                    if 'COMMANDS' in attr and isinstance(value, dict):
                        originals.append((value, copy.deepcopy(value)))
        try:
            codec = getattr(module, name.lower())()
            codec.COMMANDS = copy.deepcopy(codec.COMMANDS)
            return codec
        finally:
            for value, original in originals:
                value.clear()
                value.update(original)


def values(decoded):
    if not isinstance(decoded, dict):
        raise ResponseError('Decoder did not return a dictionary')
    for key in ('ERROR', 'WARNING', 'validity check'):
        if key in decoded:
            raise ResponseError(str(decoded[key][0]))
    result = {}
    for key, item in decoded.items():
        if key.startswith('_') or key == 'raw_response':
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        value, unit = item[:2]
        if isinstance(value, bytes):
            value = value.decode('ascii', errors='replace')
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if value is None or value in ('NAK', 'No response'):
            continue
        result[key] = (value, unit)
    if not result:
        raise ResponseError('No usable decoded values')
    return result


class PIProtocol:
    def __init__(self, name):
        self.name = name.upper()
        self.codec = load_codec(self.name)
        self.primary = 'QPIGS' if 'QPIGS' in self.codec.COMMANDS else 'GS'
        self.rating = 'QPIRI' if 'QPIRI' in self.codec.COMMANDS else 'PIRI'

    def definition(self, command, writing=False):
        if not re.fullmatch(r'[A-Za-z0-9.+-]{1,64}', command):
            raise ValueError('Invalid command characters/length')
        definition = self.codec.get_command_defn(command)
        wanted = {'SETTER'} if writing else {'QUERY', 'QUERYEN'}
        if not definition or definition.get('type') not in wanted:
            raise ValueError('Command is not an approved ' + ('setter' if writing else 'query'))
        pattern = definition.get('regex')
        if pattern and not re.fullmatch(pattern, command):
            raise ValueError('Command parameters do not match the protocol')
        return definition

    def request(self, command, writing=False):
        self.definition(command, writing)
        # Needed by upstream codecs that use the current definition for framing.
        self.codec._command_defn = self.codec.get_command_defn(command)
        frame = self.codec.get_full_command(command)
        if not frame or len(frame) > 128:
            raise ValueError('Invalid generated request')
        return frame

    def decode(self, raw, command, writing=False):
        self.definition(command, writing)
        if not raw or not raw.endswith(b'\r') or len(raw) > 8192:
            raise ResponseError('Incomplete/oversize response')
        if b'NAK' in raw or raw.startswith(b'^0'):
            raise UnsupportedCommand('Inverter rejected the command')
        # Some upstream protocols check only non-emptiness. Never detect a
        # family or accept a setter ACK on that weak criterion.
        if self.name == 'PI30REVO':
            valid, _ = self.codec.check_response_valid(raw)
        else:
            valid = len(raw) >= 5 and raw[-3:-1] == bytes(crcPI(raw[:-3]))
        if not valid:
            raise ResponseError('Response checksum mismatch')
        if writing:
            if raw[:-3] not in (b'(ACK', b'^1'):
                raise ResponseError('Setter did not return a recognized ACK')
            return {'ack': ('ACK', '')}
        if not raw.startswith((b'(', b'^D')):
            raise ResponseError('Unexpected response framing')
        # Reject truncated primary records instead of silently fabricating values.
        fields = self.codec.get_responses(raw)
        definition = self.codec.get_command_defn(command)
        if command == self.primary and len(fields) < 10:
            raise ResponseError('Truncated status record')
        return values(self.codec.decode(raw, command))

    def read_commands(self):
        """Only queries; upstream SETTINGS_COMMANDS sometimes contains setters."""
        result = []
        now = datetime.now()
        dates = {'QEY': now.strftime('%Y'), 'EY': now.strftime('%Y'),
                 'QEM': now.strftime('%Y%m'), 'EM': now.strftime('%Y%m'),
                 'QED': now.strftime('%Y%m%d'), 'ED': now.strftime('%Y%m%d'),
                 'QLY': now.strftime('%Y'), 'QLM': now.strftime('%Y%m'),
                 'QLD': now.strftime('%Y%m%d')}
        for command, definition in self.codec.COMMANDS.items():
            if definition.get('type') not in ('QUERY', 'QUERYEN'):
                continue
            if definition.get('regex'):
                if command not in dates:
                    continue  # parallel addressing/history parameters require explicit config
                command += dates[command]
            try:
                self.definition(command)
            except ValueError:
                continue
            result.append(command)
        return list(dict.fromkeys([self.primary] + result))

    def identify(self, exchange):
        command = 'QPI' if 'QPI' in self.codec.COMMANDS else 'PI'
        result = self.decode(exchange(self.request(command)), command)
        identity = ' '.join(str(v[0]) for v in result.values()).upper()
        family = re.match(r'PI\d+', self.name)[0]
        number = family[2:]
        if family not in identity and identity.strip() != number:
            raise ResponseError(f'Identity {identity!r} does not match {family}')
        return result


def detect(exchange_factory, baudrates, should_stop=lambda: False):
    """Identify the base family only. Firmware variants cannot be guessed from QPI."""
    for baud in baudrates:
        for name in ('PI30', 'PI18', 'PI17', 'PI16', 'PI41'):
            if should_stop():
                raise InterruptedError('Detection stopped')
            protocol = PIProtocol(name)
            try:
                exchange = exchange_factory(baud)
                identity = protocol.identify(exchange)
                protocol.decode(exchange(protocol.request(protocol.primary)), protocol.primary)
                return protocol, baud, identity
            except (OSError, ValueError, TimeoutError):
                continue
    raise ResponseError('No protocol identified; configure the documented family/variant explicitly')
