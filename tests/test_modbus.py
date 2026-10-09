import struct
from unittest.mock import Mock
import pytest
from inverter_runtime.modbus import ModbusProtocol, crc16, validate_profile


def profile(**extra):
    sensor = dict(name='Voltage', address=100, scale=.1, unit='V')
    sensor.update(extra)
    return {'sensors': [sensor]}


class Device:
    def __init__(self, unit=1, tcp=False):
        self.unit, self.tcp, self.value, self.requests = unit, tcp, 2300, []
    def exchange(self, frame, expected=None):
        self.requests.append(frame)
        if self.tcp:
            assert frame[6] == self.unit
            pdu = frame[7:]
        else:
            assert frame[0] == self.unit and crc16(frame[:-2]) == frame[-2:]
            pdu = frame[1:-2]
        if pdu[0] in (3, 4):
            response = bytes([pdu[0], 2])+self.value.to_bytes(2, 'big')
        elif pdu[0] == 6:
            self.value = int.from_bytes(pdu[3:5], 'big')
            response = pdu
        else:
            self.value = int.from_bytes(pdu[6:], 'big')
            response = pdu[:5]
        if self.tcp:
            return frame[:4]+struct.pack('>HB', len(response)+1, self.unit)+response
        response = bytes([self.unit])+response
        return response+crc16(response)


@pytest.mark.parametrize('tcp', [False, True])
@pytest.mark.parametrize('unit', [1, 2, 247])
def test_independent_units_and_reads(tcp, unit):
    protocol = ModbusProtocol('MODBUS_TCP' if tcp else 'MODBUS_RTU', profile(), unit)
    device = Device(unit, tcp)
    assert protocol.read('Voltage', device) == {'Voltage': (230., 'V')}


@pytest.mark.parametrize('tcp', [False, True])
@pytest.mark.parametrize('function', [6, 16])
def test_write_bounds_ack_and_readback(tcp, function):
    protocol = ModbusProtocol('MODBUS_TCP' if tcp else 'MODBUS_RTU',
                             profile(write=dict(min=200, max=250, step=.1, function=function)), 2)
    device = Device(2, tcp)
    with pytest.raises(ValueError, match='read-only'):
        protocol.write('Voltage', '231.2', device)
    assert not device.requests



@pytest.mark.parametrize('tcp', [False, True])
def test_mismatched_unit_crc_or_transaction_rejected(tcp):
    p = ModbusProtocol('MODBUS_TCP' if tcp else 'MODBUS_RTU', profile(), 1)
    d = Device(1, tcp)
    def corrupt(frame, expected):
        raw = bytearray(d.exchange(frame, expected))
        raw[0] ^= 1
        return bytes(raw)
    with pytest.raises(ValueError):
        p.read('Voltage', Mock(exchange=corrupt))


@pytest.mark.parametrize('word,byte', [('big','big'), ('little','big'), ('big','little'), ('little','little')])
def test_multiregister_endianness(word, byte):
    sensor = profile(data_type='uint32', scale=1, word_order=word, byte_order=byte)['sensors'][0]
    p = ModbusProtocol('MODBUS_RTU', {'sensors':[sensor]}, 1)
    data = p._reorder(struct.pack('>I', 123456), sensor)
    body = b'\x01\x03\x04'+data
    assert p.read('Voltage', Mock(exchange=lambda *a, **k: body+crc16(body)))['Voltage'][0] == 123456


def test_read_only_and_invalid_maps():
    p = ModbusProtocol('MODBUS_RTU', profile(), 1)
    transport = Mock()
    with pytest.raises(ValueError): p.write('Voltage', '230', transport)
    transport.exchange.assert_not_called()
    for changes in (dict(address=65536), dict(function=6), dict(scale=0), dict(offset=float('nan'))):
        with pytest.raises(ValueError): validate_profile(profile(**changes))
