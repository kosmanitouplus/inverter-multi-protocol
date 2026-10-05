import json
import math
import os
import re
from pathlib import Path
from .protocols import PROTOCOLS
from .controls import SELECTS, NUMBERS
from .modbus import validate_profile


def load_config(path, profiles_dir='/config/inverter-profiles'):
    options = json.loads(Path(path).read_text())
    if not isinstance(options, dict):
        raise ValueError('Options must be an object')
    entries = options.get('inverters')
    if entries and (not isinstance(entries, list) or not 1 <= len(entries) <= 32):
        raise ValueError('Configure 1–32 inverters')
    if not entries:
        # Preserve the installed single-inverter tag, port, protocol and history.
        entries = [{'name': options.get('inverter_name', 'INVERTER_1'),
                    'port': options.get('port', '/dev/ttyUSB0'),
                    'protocol': options.get('protocol', 'PI30'),
                    'poll_interval': options.get('poll_interval', 5)}]
    result, names, ports = [], set(), {}
    for original in entries:
        entry = dict(original)
        name = entry['name']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in names:
            raise ValueError('Unique inverter names must contain letters, digits, _ or -')
        names.add(name)
        port = entry['port']
        if not isinstance(port, str) or not port or ('://' in port and not port.startswith('tcp://')):
            raise ValueError('Port must be a serial path or tcp://host:port')
        protocol = entry.get('protocol', 'AUTO').upper()
        if protocol not in PROTOCOLS + ('AUTO', 'MODBUS_RTU', 'MODBUS_TCP'):
            raise ValueError(f'Unsupported protocol {protocol}')
        if protocol == 'MODBUS_TCP' and not port.startswith('tcp://'):
            raise ValueError('Modbus TCP requires a tcp:// endpoint')
        if protocol == 'MODBUS_RTU' and port.startswith('tcp://'):
            raise ValueError('Use MODBUS_TCP for Modbus over a network')
        entry['protocol'] = protocol
        entry.setdefault('baud', 9600 if protocol.startswith('MODBUS') else 2400)
        entry.setdefault('poll_interval', 5)
        entry.setdefault('query_interval', 60)
        entry.setdefault('timeout', 3)
        entry.setdefault('unit_id', 1)
        entry.setdefault('parity', 'N')
        entry.setdefault('stopbits', 1)
        entry.setdefault('allow_writes', False)
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
        if entry['baud'] not in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
            raise ValueError('Unsupported baudrate')
        if not isinstance(entry['unit_id'], int) or not 1 <= entry['unit_id'] <= 247:
            raise ValueError('unit_id must be 1–247')
        if not isinstance(entry['allow_writes'], bool):
            raise ValueError('allow_writes must be boolean')
        if entry['allow_writes'] and protocol == 'AUTO':
            raise ValueError('Pin the exact documented protocol before enabling writes')
        if not isinstance(entry['controls'], list) or any(k not in SELECTS.keys() | NUMBERS.keys() for k in entry['controls']):
            raise ValueError('Unknown control name')
        if not isinstance(entry['commands'], list) or len(entry['commands']) > 128:
            raise ValueError('commands must be a list of at most 128 read commands')
        identity = (entry['baud'], entry['parity'], entry['stopbits'])
        physical = port if port.startswith('tcp://') else os.path.realpath(port)
        previous = ports.get(physical)
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
