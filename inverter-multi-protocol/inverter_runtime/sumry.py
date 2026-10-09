"""SMG II standard register map, function 3 only, RTU over RS232.

Source: https://github.com/syssi/esphome-smg-ii (README protocol and UART example).
Compatibility is probable: similar maps and SMG II firmware variants exist.
"""
import re
import struct
from types import SimpleNamespace
from .modbus import ModbusProtocol


class SumryProtocol(ModbusProtocol):
    def __init__(self, unit=1):
        super().__init__('SUMRY', {'sensors': [{'name': 'STATUS', 'address': 201}]}, unit)
        self.family = 'SUMRY_SMG_II'
        self.confidence = 'probable'
        self.candidates = ['SUMRY_SMG_II_STANDARD']
        self.sensors.update({name: {'name': name, 'address': addr, 'scale': scale,
                                  'unit': unit_name, 'data_type': dtype}
                             for name, addr, scale, unit_name, dtype in (
                                 ('PV voltage', 219, .1, 'V', 'int16'),
                                 ('PV current', 220, .1, 'A', 'int16'),
                                 ('PV power', 223, 1, 'W', 'int16'),
                                 ('Load percentage', 225, 1, '%', 'int16'),
                                 ('Inverter temperature', 227, 1, '°C', 'int16'),
                                 ('Battery state of charge', 229, 1, '%', 'uint16'))})

    def registers(self, transport, address, count):
        body = self._exchange(struct.pack('>BHH', 3, address, count), transport, count)
        if len(body) != 2+2*count or body[1] != 2*count:
            raise ValueError('SMG II register length mismatch')
        return body[2:]

    @staticmethod
    def validate(values):
        for name, (value, unit) in values.items():
            bounds = {'V': (0, 1000), 'Hz': (0, 70), 'A': (-500, 500),
                      'W': (-32000, 32000), 'VA': (0, 32000), '%': (0, 150), '°C': (-50, 150)}
            if unit in bounds and not bounds[unit][0] <= value <= bounds[unit][1]:
                raise ValueError(f'SMG II implausible {name}')
            if name == 'Battery voltage' and not 0 <= value <= 80:
                raise ValueError('SMG II implausible battery voltage')
            if name == 'Battery state of charge' and not 0 <= value <= 100:
                raise ValueError('SMG II implausible state of charge')
        return values

    def read(self, command, transport):
        if command != self.primary:
            return self.validate(super().read(command, transport))
        data = self.registers(transport, 201, 17)
        mode = int.from_bytes(data[:2], 'big')
        if mode not in range(7):
            raise ValueError('SMG II invalid operation mode')
        words = struct.unpack('>16h', data[2:])
        fields = (
            ('Grid voltage', .1, 'V'), ('Grid frequency', .01, 'Hz'), ('Grid power', 1, 'W'),
            ('Inverter voltage', .1, 'V'), ('Inverter current', .1, 'A'), ('Inverter frequency', .01, 'Hz'),
            ('Inverter power', 1, 'W'), ('Inverter charging power', 1, 'W'),
            ('Output voltage', .1, 'V'), ('Output current', .1, 'A'), ('Output frequency', .01, 'Hz'),
            ('Output power', 1, 'W'), ('Output apparent power', 1, 'VA'),
            ('Battery voltage', .1, 'V'), ('Battery current', .1, 'A'), ('Battery power', 1, 'W'))
        result = {'Operation mode': (mode, '')}
        result.update({name: (value*scale, unit) for value, (name, scale, unit) in zip(words, fields)})
        self.validate(result)
        if not (result['Grid voltage'][0] > 80 or result['Output voltage'][0] > 80
                or result['Battery voltage'][0] > 10):
            raise ValueError('SMG II empty/unconvincing status block')
        return result

    def serial_number(self, exchange):
        raw = self.registers(SimpleNamespace(exchange=exchange), 186, 12)
        text = raw.strip(b'\0 ').decode('ascii')
        if (not re.fullmatch(r'[A-Za-z0-9-]{6,24}', text) or len(set(text.replace('-', ''))) < 2
                or text.upper() in ('UNKNOWN', 'DEFAULT', 'SERIALNUMBER', '1234567890')):
            raise ValueError('SMG II serial absent or placeholder')
        return text
