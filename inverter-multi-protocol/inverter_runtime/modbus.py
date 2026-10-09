"""Model-specific register profiles over Modbus RTU and TCP.

There is no universal inverter register map; addresses must come from its manual.
"""
import math
import struct
from decimal import Decimal

FORMATS = {'uint16': ('H', 1), 'int16': ('h', 1), 'uint32': ('I', 2),
           'int32': ('i', 2), 'float32': ('f', 2), 'uint64': ('Q', 4),
           'int64': ('q', 4), 'float64': ('d', 4)}


def crc16(data):
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc.to_bytes(2, 'little')


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError('Profile must be an object')
    sensors = profile.get('sensors')
    if not isinstance(sensors, list) or not 1 <= len(sensors) <= 128:
        raise ValueError('A profile requires 1–128 documented sensors')
    names = set()
    for sensor in sensors:
        name = sensor['name']
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Sensor names must be unique')
        names.add(name)
        if sensor.get('data_type', 'uint16') not in FORMATS:
            raise ValueError('Unsupported register data_type')
        count = FORMATS[sensor.get('data_type', 'uint16')][1]
        if not isinstance(sensor['address'], int) or not 0 <= sensor['address'] <= 65536-count:
            raise ValueError('Register address is out of bounds')
        if sensor.get('function', 3) not in (3, 4):
            raise ValueError('Only register read functions 3/4 are allowed')
        if sensor.get('word_order', 'big') not in ('big', 'little'):
            raise ValueError('Invalid word_order')
        if sensor.get('byte_order', 'big') not in ('big', 'little'):
            raise ValueError('Invalid byte_order')
        if not math.isfinite(float(sensor.get('scale', 1))) or float(sensor.get('scale', 1)) == 0:
            raise ValueError('Invalid scale')
        if not math.isfinite(float(sensor.get('offset', 0))):
            raise ValueError('Invalid offset')
        if sensor.get('write'):
            write = sensor['write']
            if sensor.get('function', 3) != 3 or write.get('function', 6) not in (6, 16):
                raise ValueError('Invalid writable register/function')
            if write.get('function', 6) == 6 and count != 1:
                raise ValueError('Function 6 writes only one register')
            if not all(math.isfinite(float(write[k])) for k in ('min', 'max', 'step')):
                raise ValueError('Writable registers require finite bounds and step')
            if write['min'] >= write['max'] or write['step'] <= 0:
                raise ValueError('Invalid writable register bounds')
    return profile


class ModbusProtocol:
    def __init__(self, name, profile, unit):
        self.name = name
        self.profile = validate_profile(profile)
        if not 1 <= unit <= 247:
            raise ValueError('Modbus unit must be 1–247')
        self.unit, self.transaction = unit, 0
        self.primary = profile['sensors'][0]['name']
        self.rating = None
        self.sensors = {s['name']: s for s in profile['sensors']}

    def _exchange(self, pdu, transport, count):
        if pdu[0] not in (3, 4):
            raise ValueError('Only read functions are permitted')
        if self.name == 'MODBUS_TCP':
            self.transaction = (self.transaction + 1) & 65535
            header = struct.pack('>HHHB', self.transaction, 0, len(pdu)+1, self.unit)
            raw = transport.exchange(header+pdu, expected=9+2*count)
            if len(raw) < 9 or raw[:4] != header[:4] or raw[6] != self.unit:
                raise ValueError('Modbus TCP transaction/unit mismatch')
            if len(raw) != 6 + int.from_bytes(raw[4:6], 'big'):
                raise ValueError('Modbus TCP length mismatch')
            body = raw[7:]
        else:
            request = bytes([self.unit])+pdu
            raw = transport.exchange(request+crc16(request), expected=8 if pdu[0] in (6, 16) else 5+2*count)
            if len(raw) < 5 or raw[0] != self.unit or crc16(raw[:-2]) != raw[-2:]:
                raise ValueError('Modbus RTU checksum/unit mismatch')
            body = raw[1:-2]
        if body[0] == pdu[0] | 128:
            raise ValueError(f'Modbus exception {body[1]}')
        if body[0] != pdu[0]:
            raise ValueError('Modbus function mismatch')
        return body

    @staticmethod
    def _reorder(data, sensor):
        words = [data[i:i+2] for i in range(0, len(data), 2)]
        if sensor.get('byte_order', 'big') == 'little':
            words = [w[::-1] for w in words]
        if sensor.get('word_order', 'big') == 'little':
            words.reverse()
        return b''.join(words)

    def read(self, command, transport):
        sensor = self.sensors[command]
        fmt, count = FORMATS[sensor.get('data_type', 'uint16')]
        pdu = struct.pack('>BHH', sensor.get('function', 3), sensor['address'], count)
        body = self._exchange(pdu, transport, count)
        if len(body) != 2+2*count or body[1] != 2*count:
            raise ValueError('Modbus register response length mismatch')
        value = struct.unpack('>'+fmt, self._reorder(body[2:], sensor))[0]
        value = value * sensor.get('scale', 1) + sensor.get('offset', 0)
        if not math.isfinite(value):
            raise ValueError('Non-finite Modbus value')
        return {command: (value, sensor.get('unit', ''))}

    def write(self, command, value, transport):
        raise ValueError('Registers are read-only in this runtime')

    def read_commands(self):
        return list(self.sensors)
