import re
import pytest
from mppsolar.protocols.protocol_helpers import crcPI
from inverter_runtime.protocols import PIProtocol, PROTOCOLS, ResponseError, UnsupportedCommand, detect


def valid(raw):
    if isinstance(raw, str):
        raw = raw.encode('ascii')
    body = raw[:-3]
    return body+bytes(crcPI(body))+b'\r'


def test_upstream_tables_are_isolated():
    base = PIProtocol('PI30')
    before = base.codec.COMMANDS['QPIGS']['response']
    variant = PIProtocol('PI41')
    after = PIProtocol('PI30')
    assert 'L1' in variant.codec.COMMANDS['QPIGS']['response'][0][1]
    assert before == after.codec.COMMANDS['QPIGS']['response']
    assert 'L1' not in before[0][1]
    assert variant.codec.COMMANDS is not base.codec.COMMANDS


@pytest.mark.parametrize('name', PROTOCOLS)
def test_all_protocol_plans_only_contain_read_commands(name):
    p = PIProtocol(name)
    assert p.primary in p.read_commands()
    for command in p.read_commands():
        assert p.definition(command)['type'] in ('QUERY', 'QUERYEN')
        assert p.request(command)
    with pytest.raises(ValueError):
        p.request('POP00')


@pytest.mark.parametrize('name', ['PI16', 'PI17', 'PI18', 'PI30', 'PI41'])
def test_identity_crc_validated(name):
    p = PIProtocol(name)
    cmd = 'QPI' if 'QPI' in p.codec.COMMANDS else 'PI'
    raw = valid(p.codec.COMMANDS[cmd]['test_responses'][0])
    if name == 'PI41':
        raw = valid(b'(PI41xxx')  # upstream fixture reports PI30, not PI41
    assert p.identify(lambda frame: raw)
    damaged = bytearray(raw)
    damaged[2] ^= 1
    with pytest.raises(ResponseError):
        p.identify(lambda frame: bytes(damaged))


def test_detection_uses_two_validated_responses_without_setters():
    p = PIProtocol('PI30')
    frames = []
    def exchange(frame):
        frames.append(frame)
        command = frame[:-3].decode()
        assert command in ('QPI', 'QPIGS')
        return valid(p.codec.COMMANDS[command]['test_responses'][0])
    detected, baud, identity = detect(lambda rate: exchange, [2400])
    assert detected.name == 'PI30' and baud == 2400
    assert len(frames) == 2


def test_detection_does_not_accept_a_single_identity_or_corrupt_status():
    p = PIProtocol('PI30')
    def exchange(frame):
        if frame.startswith(b'QPI') and not frame.startswith(b'QPIGS'):
            return valid(p.codec.COMMANDS['QPI']['test_responses'][0])
        return b'(broken\x00\x00\r'
    with pytest.raises(ResponseError, match='No protocol identified'):
        detect(lambda rate: exchange, [2400])


def test_detection_is_interruptible():
    with pytest.raises(InterruptedError):
        detect(lambda rate: None, [2400], should_stop=lambda: True)


def test_crc_valid_but_truncated_status_is_rejected():
    p = PIProtocol('PI30')
    body = b'(230.0 50.0'
    with pytest.raises(ResponseError, match='Truncated'):
        p.decode(body+bytes(crcPI(body))+b'\r', 'QPIGS')


def test_nak_not_published_as_sensor():
    with pytest.raises(UnsupportedCommand):
        PIProtocol('PI30').decode(b'(NAKss\r', 'QPIGS')
