"""Isolated mpp-solar codecs. Automatic probes never use a setter."""
import copy
import importlib
import logging
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
        # Upstream uses bare module logger names rather than a package hierarchy.
        for logger_name in ('mppsolar', 'protocols', 'AbstractProtocol', *[n.lower() for n in PROTOCOLS]):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
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
        self.family = re.match(r'PI\d+', self.name)[0]
        self.confidence, self.candidates = 'probable', [self.name]
        self.codec = load_codec(self.name)
        self.primary = 'QPIGS' if 'QPIGS' in self.codec.COMMANDS else 'GS'
        self.rating = 'QPIRI' if 'QPIRI' in self.codec.COMMANDS else 'PIRI'

    def definition(self, command, writing=False):
        if not re.fullmatch(r'[A-Za-z0-9.+-]{1,64}', command):
            raise ValueError('Invalid command characters/length')
        definition = self.codec.get_command_defn(command)
        if writing:
            raise ValueError('Firmware settings are disabled: read-only runtime')
        wanted = {'QUERY', 'QUERYEN'}
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
        if not raw.startswith((b'(', b'^D')):
            raise ResponseError('Unexpected response framing')
        # Reject truncated primary records instead of silently fabricating values.
        fields = self.codec.get_responses(raw)
        definition = self.codec.get_command_defn(command)
        if raw.startswith(b'^D') and (not raw[2:5].isdigit() or int(raw[2:5]) != len(raw)-5):
            raise ResponseError('Declared response length mismatch')
        if command == self.primary:
            decoded = self.validate_status(fields, definition)
            for candidate in getattr(self, 'compatible', []):
                if candidate is not self:
                    other = candidate.validate_status(candidate.codec.get_responses(raw), candidate.definition(command))
                    decoded = {k: v for k, v in decoded.items() if other.get(k) == v}
            if not decoded:
                raise ResponseError('Ambiguous variants have no consistent measurements')
            return decoded
        try:
            decoded = values(self.codec.decode(raw, command))
        except (IndexError, KeyError, TypeError, UnicodeError, ArithmeticError) as exc:
            raise ResponseError('Malformed decoded response') from exc
        for field, (value, unit) in decoded.items():
            if unit in ('V', 'A', 'W', 'VA', 'Hz', '%', '°C', 'Deg_C'):
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ResponseError('Invalid numeric measurement')
                limits = {'V': (-1500, 1500), 'A': (-10000, 10000), 'Hz': (0, 100),
                          '%': (0, 100), '°C': (-100, 250), 'Deg_C': (-100, 250)}
                if unit in limits and not limits[unit][0] <= value <= limits[unit][1]:
                    raise ResponseError('Implausible measurement')
        return decoded

    def validate_status(self, fields, definition):
        specs = definition['response']
        if len(fields) != len(specs):
            raise ResponseError('Truncated or unrecognized status record')
        decoded = {}
        for token, spec in zip(fields, specs):
            indexed = isinstance(spec[0], int)
            kind, field, unit = (spec[2], spec[1], spec[3]) if indexed else spec[:3]
            text = token.decode('ascii') if isinstance(token, bytes) else token
            if kind.startswith(('int', 'float')) or kind == '10int':
                # These documented upstream fixtures use placeholders for absent phases/PV inputs.
                if (self.family == 'PI16' and re.fullmatch(r'-+(?:\.-+)?', text)) or (
                        self.family == 'PI17' and not text and field.startswith('AC output current')):
                    continue  # unavailable, never a fabricated zero
                if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
                    raise ResponseError('Malformed numeric status field')
                value = float(text)
                if kind.startswith('int') or kind == '10int':
                    if not re.fullmatch(r'[+-]?\d+', text):
                        raise ResponseError('Non-integer status field')
                    value = int(text)
                if kind == '10int':
                    value /= 10
                elif ':r/' in kind:
                    value /= float(kind.split(':r/', 1)[1])
                if isinstance(unit, str) and unit.startswith('0.1'):
                    value /= 10
                    unit = unit[3:]
                limits = {'V': (-1500, 1500), 'A': (-10000, 10000), 'Hz': (0, 100),
                          '%': (0, 100), '°C': (-100, 250), 'Deg_C': (-100, 250)}
                if not math.isfinite(value) or (unit in limits and not limits[unit][0] <= value <= limits[unit][1]):
                    raise ResponseError('Implausible measurement')
                decoded[field] = (value, unit)
            elif kind == 'flags':
                if not re.fullmatch(r'[01]+', text) or len(text) != len(unit):
                    raise ResponseError('Invalid status flags')
                for bit, label in zip(text, unit):
                    decoded[label] = (bit == '1', 'bool')
            elif kind == 'option':
                if not text.isdigit() or int(text) >= len(unit):
                    raise ResponseError('Invalid status enumeration')
                decoded[field] = (unit[int(text)], '')
            elif kind == 'keyed':
                if text not in unit:
                    raise ResponseError('Unknown status code')
                decoded[field] = (unit[text], '')
            else:
                if not text or not text.isascii() or not text.isprintable():
                    raise ResponseError('Invalid textual status field')
                decoded[field] = (text, unit if isinstance(unit, str) else '')
        if len(decoded) < 10:
            raise ResponseError('Insufficient usable measurements')
        return decoded

    def serial_number(self, exchange):
        command = 'QID' if 'QID' in self.codec.COMMANDS else 'ID'
        result = self.decode(exchange(self.request(command)), command)
        serial = result.get('Serial Number', ('', ''))[0]
        if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9-]{6,32}', serial):
            raise ResponseError('No usable manufacturer serial number')
        if len(set(serial)) == 1 or serial.upper() in ('UNKNOWN', '123456', '1234567890'):
            raise ResponseError('Placeholder serial number')
        return serial

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
        if identity.strip() not in (family, number):
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


class DetectionPending(ResponseError):
    """A bounded portion of the search completed; continue on the next cycle."""


# All standard positive POSIX baud rates supported by pyserial, ordered for inverters first.
from serial import SerialBase
BAUDRATES = tuple(dict.fromkeys((2400, 9600, 19200, 4800, 1200, 38400, 57600, 115200)
                               + tuple(b for b in SerialBase.BAUDRATES if b > 0)))


class AutoDetector:
    def __init__(self, config):
        import itertools
        rates = config.get('auto_baudrates') or BAUDRATES
        rates = list(dict.fromkeys([config['baud']]+list(rates)))
        framings = list(dict.fromkeys([(config['parity'], config['stopbits'])]
                                    + list(itertools.product(('N', 'E', 'O'), (1, 2)))))
        self.codecs = [PIProtocol(n) for n in ('PI30', 'PI18', 'PI17', 'PI16', 'PI41')
                       + tuple(n for n in PROTOCOLS if n not in ('PI30', 'PI18', 'PI17', 'PI16', 'PI41'))]
        groups = {}
        for p in self.codecs:
            command = 'QPI' if 'QPI' in p.codec.COMMANDS else 'PI'
            groups.setdefault(p.request(command), []).append((p, command))
        # Fast pass at standard inverter settings, then extended rates/framing.
        common = [b for b in rates if b in (2400, 9600, 19200, 4800, 1200, 38400, 57600, 115200)]
        extended = [b for b in rates if b not in common]
        settings = [(rate, parity, stop) for parity, stop in framings for rate in common]
        settings += [(rate, parity, stop) for parity, stop in framings for rate in extended]
        self.attempts = [(rate, parity, stop, frame, candidates)
                         for rate, parity, stop in settings for frame, candidates in groups.items()]
        self.cursor = 0
        # First try only common rates with the preferred framing and a short timeout.
        self.fast_limit = len(common)*len(groups)
        self.fast_pass = bool(self.fast_limit)
        self.last_success = None
        self.resume = False
        self.last_attempt = None
        self.last_error = 'no attempt yet'

    def progress(self):
        if self.last_attempt is None:
            return 'not started'
        rate, parity, stop, frame, candidates = self.last_attempt
        families = '/'.join(dict.fromkeys(p.family for p, _ in candidates))
        return f'{rate} baud 8{parity}{stop} probe={families}; {self.last_error}'

    def probe(self, attempt, factory, should_stop):
        self.last_attempt = attempt
        rate, parity, stop, frame, candidates = attempt
        transport = factory(rate, parity, stop)
        raw = transport.exchange(frame)
        compatible = []
        responses = {}
        for p, command in candidates:
            if should_stop():
                raise InterruptedError('Detection stopped')
            try:
                # Family response must match exactly, not merely contain PIxx.
                p.identify(lambda request: raw)
                request = p.request(p.primary)
                if request not in responses:
                    try:
                        responses[request] = transport.exchange(request)
                    except (OSError, ValueError, TimeoutError) as exc:
                        responses[request] = exc
                if isinstance(responses[request], Exception):
                    raise responses[request]
                status = p.validate_status(p.codec.get_responses(responses[request]), p.definition(p.primary))
                p.decode(responses[request], p.primary)
                compatible.append((p, status))
            except (OSError, ValueError, TimeoutError) as exc:
                self.last_error = f'{type(exc).__name__}: {str(exc)[:160]}'
                continue
        if not compatible:
            raise ResponseError(f'Response received but rejected: {self.last_error}')
        p = compatible[0][0]
        p.confidence = 'identified' if len(compatible) == 1 else 'probable'
        p.candidates = [item.name for item, values in compatible]
        # Only publish the intersection of measurements when variants share an identity.
        p.compatible = [item for item, values in compatible]
        return p, transport

    def step(self, factory, should_stop=lambda: False, budget=2):
        if self.last_success is not None and not self.resume:
            self.resume = True
            try:
                return self.probe(self.last_success, factory, should_stop)
            except (OSError, ValueError, TimeoutError):
                pass
        for _ in range(budget):
            if should_stop():
                raise InterruptedError('Detection stopped')
            attempt = self.attempts[self.cursor]
            self.cursor += 1
            try:
                result = self.probe(attempt, factory, should_stop)
                self.last_success = attempt
                self.resume = False
                return result
            except (OSError, ValueError, TimeoutError) as exc:
                # Missing/busy/unopenable hardware needs backoff, not hundreds of probes.
                if isinstance(exc, OSError) and not isinstance(exc, TimeoutError):
                    raise
                self.last_error = f'{type(exc).__name__}: {str(exc)[:240]}'
                if self.fast_pass and self.cursor >= self.fast_limit:
                    self.fast_pass = False
                    self.cursor = 0
                    break
                if self.cursor >= len(self.attempts):
                    self.cursor = 0
                    self.resume = False
                    self.fast_pass = bool(self.fast_limit)
                    raise ResponseError(f'No protocol identified after complete serial search; {self.progress()}')
        raise DetectionPending(f'Serial search {self.cursor}/{self.fast_limit if self.fast_pass else len(self.attempts)} ({"fast" if self.fast_pass else "full"}); identity unknown; {self.progress()}')
