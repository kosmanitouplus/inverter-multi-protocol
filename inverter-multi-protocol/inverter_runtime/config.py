import json
import math
import os
import re
import hashlib
import unicodedata
from pathlib import Path
from .protocols import PROTOCOLS, BAUDRATES, PIProtocol
from .modbus import validate_profile


def inverter_identity(display_name, explicit_id=None):
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 128:
        raise ValueError('Inverter name must be non-empty text (maximum 128 characters)')
    if any(unicodedata.category(c).startswith('C') for c in display_name):
        raise ValueError('Inverter name cannot contain control characters')
    if explicit_id is not None:
        if not isinstance(explicit_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', explicit_id):
            raise ValueError('Inverter id must contain 1–64 letters, digits, _ or -')
        return explicit_id
    if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', display_name):
        return display_name  # Preserve every previously valid MQTT identifier.
    text = unicodedata.normalize('NFKD', display_name).encode('ascii', 'ignore').decode()
    identifier = re.sub(r'[^A-Za-z0-9_-]+', '_', text.strip()).strip('_')
    if not identifier:
        identifier = 'inverter_'+hashlib.sha256(display_name.encode()).hexdigest()[:12]
    if len(identifier) > 64:
        identifier = identifier[:51]+'_'+hashlib.sha256(display_name.encode()).hexdigest()[:12]
    return identifier


def load_config(path, profiles_dir='/config/inverter-profiles'):
    options = json.loads(Path(path).read_text())
    if not isinstance(options, dict):
        raise ValueError('Options must be an object')
    entries = options.get('inverters')
    if entries and (not isinstance(entries, list) or not 1 <= len(entries) <= 32):
        raise ValueError('Configure 1–32 inverters')
    if not entries:
        # Keep installed options; history binding additionally requires verified expected_serial.
        entries = [{'name': options.get('inverter_name', 'INVERTER_1'),
                    'id': options.get('inverter_id'),
                    'port': options.get('port', 'AUTO'),
                    'protocol': options.get('protocol', 'AUTO'),
                    'poll_interval': options.get('poll_interval', 5),
                    'expected_serial': options.get('expected_serial'),
                    'auto_baudrates': options.get('auto_baudrates', []),
                    'exclude_ports': options.get('exclude_ports', [])}]
    result, names, ports = [], set(), {}
    for original in entries:
        if not isinstance(original, dict):
            raise ValueError('Each inverter must be a configuration object')
        entry = dict(original)
        entry['display_name'] = entry.get('name')
        name = inverter_identity(entry['display_name'], entry.get('id'))
        if name in names:
            raise ValueError('Duplicate inverter identifier; assign a distinct id to each inverter')
        entry['name'] = name
        names.add(name)
        port = entry['port']
        if not isinstance(port, str) or not port or ('://' in port and not port.startswith('tcp://')):
            raise ValueError('Port must be a serial path or tcp://host:port')
        protocol = entry.get('protocol', 'AUTO').upper()
        if protocol not in PROTOCOLS + ('AUTO', 'SUMRY', 'MODBUS_RTU', 'MODBUS_TCP'):
            raise ValueError(f'Unsupported protocol {protocol}')
        if protocol == 'MODBUS_TCP' and not port.startswith('tcp://'):
            raise ValueError('Modbus TCP requires a tcp:// endpoint')
        if protocol == 'MODBUS_RTU' and port.startswith('tcp://'):
            raise ValueError('Use MODBUS_TCP for Modbus over a network')
        entry['protocol'] = protocol
        entry.setdefault('baud', 9600 if protocol.startswith('MODBUS') or protocol == 'SUMRY' else 2400)
        entry.setdefault('poll_interval', 5)
        entry.setdefault('query_interval', 60)
        entry.setdefault('timeout', 3)
        entry.setdefault('unit_id', 1)
        entry.setdefault('parity', 'N')
        entry.setdefault('stopbits', 1)
        entry.setdefault('allow_writes', False)
        entry.setdefault('expose_controls', True)
        entry.setdefault('charge_current_command', 'MCHGC')
        entry.setdefault('controls', ['output_source_priority', 'charger_source_priority', 'input_voltage_range'])
        entry.setdefault('write_limits', {})
        entry.setdefault('commands', [])
        for key in ('controls', 'commands'):
            if isinstance(entry[key], str):
                entry[key] = [v.strip() for v in entry[key].split(',') if v.strip()]
        if isinstance(entry['write_limits'], str):
            entry['write_limits'] = json.loads(entry['write_limits'])
        if not isinstance(entry['write_limits'], dict):
            raise ValueError('write_limits must be a JSON object')
        for key in ('poll_interval', 'query_interval', 'timeout'):
            value = float(entry[key])
            if not math.isfinite(value) or not .2 <= value <= 3600:
                raise ValueError(f'Invalid {key}')
            entry[key] = value
        if entry['timeout'] > 10:
            raise ValueError('Timeout is limited to 10 seconds for clean shutdown')
        if entry['parity'] not in ('N', 'E', 'O') or entry['stopbits'] not in (1, 2):
            raise ValueError('Invalid serial framing')
        entry['baud'] = int(entry['baud'])
        if entry['baud'] not in BAUDRATES:
            raise ValueError('Unsupported baudrate')
        if entry.get('command_unit') is not None and (type(entry['command_unit']) is not int or not 0 <= entry['command_unit'] <= 9):
            raise ValueError('command_unit must be 0–9')
        if not isinstance(entry['unit_id'], int) or not 1 <= entry['unit_id'] <= 247:
            raise ValueError('unit_id must be 1–247')
        if entry['charge_current_command'] not in ('MCHGC', 'MNCHGC'):
            raise ValueError('charge_current_command must be MCHGC or MNCHGC')
        if not isinstance(entry['expose_controls'], bool):
            raise ValueError('expose_controls must be boolean')
        if not isinstance(entry['allow_writes'], bool):
            raise ValueError('allow_writes must be boolean')
        if entry['allow_writes']:
            raise ValueError('This version is read-only: allow_writes must be false')
        entry['expose_controls'] = False
        if port.upper() == 'AUTO' and protocol != 'AUTO':
            raise ValueError('Automatic port discovery requires protocol AUTO')
        if not isinstance(entry['controls'], list) or any(not isinstance(k, str) for k in entry['controls']):
            raise ValueError('Unknown control name')
        if not isinstance(entry['commands'], list) or len(entry['commands']) > 128:
            raise ValueError('commands must be a list of at most 128 read commands')
        expected = entry.get('expected_serial')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[A-Za-z0-9-]{6,32}', expected)):
            raise ValueError('expected_serial must be a manufacturer serial number')
        if expected and (port.upper() == 'AUTO' or protocol.startswith('MODBUS')):
            raise ValueError('History migration requires an explicit PI port and verified serial')
        rates = entry.setdefault('auto_baudrates', [])
        if not isinstance(rates, list) or any(type(v) is not int or v not in BAUDRATES for v in rates):
            raise ValueError('auto_baudrates must be standard positive baud rates')
        exclusions = entry.setdefault('exclude_ports', [])
        if not isinstance(exclusions, list) or any(not isinstance(v, str) or not v.startswith('/dev/') for v in exclusions):
            raise ValueError('exclude_ports must be serial device paths')
        if not protocol.startswith('MODBUS'):
            for command in entry['commands']:
                if not isinstance(command, str):
                    raise ValueError('Commands must be text')
                if protocol == 'AUTO':
                    if not any(_is_query(name, command) for name in PROTOCOLS):
                        raise ValueError('Only documented read queries are allowed')
                elif protocol == 'SUMRY':
                    from .sumry import SumryProtocol
                    if command not in SumryProtocol().sensors:
                        raise ValueError('Only documented SMG II reads are allowed')
                else:
                    PIProtocol(protocol).definition(command)
        identity = (entry['baud'], entry['parity'], entry['stopbits'])
        physical = port if port.startswith('tcp://') else os.path.realpath(port)
        previous = ports.get(physical)
        if port.upper() == 'AUTO':
            if previous:
                raise ValueError('Use only one AUTO port pool; it handles all discovered ports')
        if previous:
            if not protocol.startswith('MODBUS') or not previous[1].startswith('MODBUS') or previous[0] != identity:
                raise ValueError('Sharing a physical port requires Modbus units with identical serial settings')
            if entry['unit_id'] in previous[2]:
                raise ValueError('Duplicate Modbus unit on a shared port')
            previous[2].add(entry['unit_id'])
        else:
            ports[physical] = (identity, protocol, {entry['unit_id']})
        if protocol.startswith('MODBUS'):
            profile = entry.get('profile', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', profile):
                raise ValueError('Modbus requires a documented profile name')
            entry['profile_data'] = validate_profile(json.loads((Path(profiles_dir)/(profile+'.json')).read_text()))
        result.append(entry)
    if not 1 <= len(result) <= 32:
        raise ValueError('Configure 1–32 inverters')
    return options, result


def _is_query(name, command):
    try:
        PIProtocol(name).definition(command)
        return True
    except ValueError:
        return False
