"""Synthetic standard SMG II register responses; no hardware compatibility claim."""
import struct
import threading
from types import SimpleNamespace
import pytest
from inverter_runtime.modbus import crc16
from inverter_runtime.sumry import SumryProtocol
from inverter_runtime.protocols import AutoDetector, DetectionPending
from inverter_runtime.service import Worker
from test_auto import real_broker
from test_controls import config


class Device:
    def __init__(self):
        self.serial = '92B32509101251'
        self.mode, self.usb, self.cable = 3, True, True
        self.frames = []
        self.bad_crc = False
    def exchange(self, frame, expected=None):
        self.frames.append(frame)
        if not self.usb: raise OSError('USB absent')
        if not self.cable: raise TimeoutError('RS232 cable absent')
        if len(frame) != 8 or frame[1] != 3:
            raise TimeoutError('Not a Modbus read')
        assert frame[0] == 1 and crc16(frame[:-2]) == frame[-2:]
        address, count = struct.unpack('>HH', frame[2:6])
        if address == 201:
            data = struct.pack('>H16h', self.mode, 2300, 5000, 100, 2300, 10, 5000,
                               100, 0, 2300, 10, 5000, 100, 120, 510, -10, -51)
        elif address == 186:
            data = self.serial.encode().ljust(24, b'\0')
        else:
            data = (100).to_bytes(2, 'big')
        assert len(data) == 2*count
        body = bytes((1, 3, len(data)))+data
        result = body+crc16(body)
        return result[:-1]+bytes([result[-1]^1]) if self.bad_crc else result
    def factory(self, port, baud=9600, timeout=3, parity='N', stopbits=1):
        def exchange(frame, expected=None):
            if (baud, parity, stopbits) != (9600, 'N', 1):
                raise TimeoutError('Wrong settings')
            return self.exchange(frame, expected)
        return SimpleNamespace(baud=baud, parity=parity, stopbits=stopbits,
                               timeout=timeout, exchange=exchange)


def test_sumry_units_crc_values_and_serial():
    p, d = SumryProtocol(), Device()
    values = p.read('STATUS', d)
    assert values['Battery voltage'] == (51., 'V')
    assert values['Battery current'] == (-1., 'A')
    assert p.serial_number(d.exchange) == d.serial
    assert p.confidence == 'probable'
    d.bad_crc = True
    with pytest.raises(ValueError, match='checksum'): p.read('STATUS', d)
    d.bad_crc, d.mode = False, 999
    with pytest.raises(ValueError, match='mode'): p.read('STATUS', d)


def test_voltronic_first_then_sumry_auto():
    d = Device()
    detector = AutoDetector(config(auto_baudrates=[2400, 9600]))
    sumry_index = next(i for i, a in enumerate(detector.attempts) if a[-1] is None)
    assert sumry_index == 10
    for _ in range(10):
        try:
            p, transport = detector.step(lambda b, par, st: d.factory('/dev/test', b, parity=par, stopbits=st))
            break
        except DetectionPending: pass
    else: raise AssertionError('Sumry not detected')
    assert p.name == 'SUMRY' and transport.baud == 9600
    modbus_frames = [f for f in d.frames if len(f) == 8 and f[1] == 3]
    assert len(modbus_frames) == 3


def test_sumry_recovery_and_identity_swap(tmp_path):
    d, b = Device(), real_broker(tmp_path)
    w = Worker(config(protocol='SUMRY', baud=9600, unit_id=1), b, d.factory)
    w.poll(); first = w.name
    assert w.serial == d.serial and w.identity_status == 'probable'
    d.cable = False
    with pytest.raises(TimeoutError): w.poll()
    d.cable = True
    d.usb = False
    with pytest.raises(OSError): w.poll()
    d.usb = True
    w.poll(); assert w.name == first
    d.serial = '92B32509999999'
    with pytest.raises(ValueError, match='Different inverter'): w.poll()
    b.unbind(w); w.protocol = None
    w.poll(); assert w.name != first
    assert all(f[1] == 3 for f in d.frames)


def test_serial_placeholder_never_supplies_identity():
    d = Device(); d.serial = '000000000000'
    with pytest.raises(ValueError): SumryProtocol().serial_number(d.exchange)


def test_auto_worker_publishes_sumry_only_after_validation(tmp_path):
    d = Device()
    w = Worker(config(protocol='AUTO', baud=2400, auto_baudrates=[2400, 9600]), real_broker(tmp_path), d.factory)
    for _ in range(10):
        try:
            w.poll()
            break
        except DetectionPending: pass
    assert w.online and w.protocol.name == 'SUMRY' and w.serial == d.serial


@pytest.mark.parametrize('kind', ['unit', 'function', 'length', 'empty'])
def test_unrelated_or_empty_modbus_response_rejected(kind):
    d = Device()
    def exchange(frame, expected=None):
        raw = bytearray(d.exchange(frame, expected))
        if kind == 'unit': raw[0] = 2
        elif kind == 'function': raw[1] = 4
        elif kind == 'length': raw[2] = 2
        else: raw[3:-2] = b'\0'*len(raw[3:-2])
        raw[-2:] = crc16(raw[:-2])
        return bytes(raw)
    with pytest.raises(ValueError):
        SumryProtocol().read('STATUS', SimpleNamespace(exchange=exchange))


def test_manual_sumry_config_defaults_and_setter_rejection(tmp_path):
    import json
    from inverter_runtime.config import load_config
    path = tmp_path/'options.json'
    values = {'protocol': 'SUMRY', 'port': '/dev/serial/by-id/test'}
    path.write_text(json.dumps(values))
    _, entries = load_config(path)
    assert entries[0]['baud'] == 9600 and entries[0]['unit_id'] == 1
    values['inverters'] = [dict(name='test', port='/dev/test', protocol='SUMRY', commands=['SET'])]
    path.write_text(json.dumps(values))
    with pytest.raises(ValueError, match='documented'): load_config(path)


class ExtendedDevice(Device):
    def __init__(self, number=4):
        super().__init__()
        self.number = number
        self.mode = 6  # Communicating with a fault and no output remains valid telemetry.
    def exchange(self, frame, expected=None):
        if not self.usb or not self.cable:
            return super().exchange(frame, expected)
        if len(frame) != 8 or frame[1] != 3:
            raise TimeoutError('Not a Modbus read')
        address, count = struct.unpack('>HH', frame[2:6])
        if address == 184: data = self.number.to_bytes(2, 'big')
        elif address == 201 and count == 1: data = self.mode.to_bytes(2, 'big')
        elif address == 201: data = b'\0'*(count*2)  # Old map looked empty.
        elif address == 338:
            data = struct.pack('>16H', 2280, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        elif address == 277: data = struct.pack('>HhhH', 0, 0, 0, 0)
        else: return super().exchange(frame, expected)
        self.frames.append(frame)
        assert frame[0] == 1 and crc16(frame[:-2]) == frame[-2:]
        assert len(data) == 2*count
        raw = bytes((1, 3, len(data)))+data
        return raw+crc16(raw)


@pytest.mark.parametrize('number', [3, 4, 5, 6])
def test_extended_11kw_fault_with_grid_and_zero_output_is_readable(number):
    d, p = ExtendedDevice(number), SumryProtocol()
    values = p.read('STATUS', d)
    assert values['Grid voltage'] == (228., 'V')
    assert values['Output voltage'] == (0., 'V')
    assert values['Operation mode'] == (6, '')
    assert p.family == 'SUMRY_SMG_II_8_11KW'
    assert p.candidates == [f'SMG_II_8_11KW_P{number}']
    assert p.confidence == 'probable'
    assert all(f[1] == 3 for f in d.frames)
    assert ('PV2 voltage' in p.sensors) == (number in (3, 4))


def test_extended_worker_identity_and_map_changes(tmp_path):
    d = ExtendedDevice()
    w = Worker(config(protocol='SUMRY', baud=9600, unit_id=1), real_broker(tmp_path), d.factory)
    w.poll()
    assert w.online and w.serial == d.serial
    d.number = 6
    with pytest.raises(ValueError, match='protocol number changed'):
        w.poll()


def test_unrecognized_protocol_number_never_guesses_extended_map():
    d = ExtendedDevice(7)
    with pytest.raises(ValueError, match='unconvincing'):
        SumryProtocol().read('STATUS', d)
