"""Deterministic inverter/wire simulations; USB presence and RS-side silence are independent."""
import hashlib
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mppsolar.protocols.protocol_helpers import crcPI
from inverter_runtime.config import load_config
from inverter_runtime.mqtt import Broker, IdentityCollision
from inverter_runtime.protocols import PIProtocol, PROTOCOLS, AutoDetector, DetectionPending, ResponseError, BAUDRATES
from inverter_runtime.service import Worker, Supervisor
from inverter_runtime.ports import discover_ports
from test_controls import config
from test_protocols import valid
from test_recovery import wait_for


def packet(body):
    if isinstance(body, str): body = body.encode('ascii')
    if body.startswith(b'^D'):
        body = b'^D'+f'{len(body)-2:03d}'.encode()+body[5:]
    return body+bytes(crcPI(body))+b'\r'


def primary_packet(p):
    raw = p.codec.COMMANDS[p.primary]['test_responses'][0]
    fields = p.codec.get_responses(raw)
    if p.family == 'PI17':
        fields = [v or b'0000' for v in fields]
    if p.name in ('PI30MAX', 'PI30M044', 'PI30M045', 'PI30MST'):
        fields = PIProtocol('PI30').codec.get_responses(PIProtocol('PI30').codec.COMMANDS['QPIGS']['test_responses'][0])
        country = p.codec.COMMANDS[p.primary]['response'][22][3]
        fields += [b'0', str(next(iter(country))).encode(), b'00000']
    if p.family == 'PI18':
        fields[-1] = b'0'
        body = b'^D000'+b','.join(fields)
    elif p.family == 'PI17':
        body = b'^D000'+b','.join(fields)
    else:
        body = b'('+b' '.join(fields)
    return packet(body)


class Device:
    def __init__(self, name='PI30', serial='9293333010501', baud=2400, parity='N', stopbits=1):
        self.p = PIProtocol(name)
        self.serial, self.baud, self.parity, self.stopbits = serial, baud, parity, stopbits
        self.usb, self.cable = True, True
        self.requests = []
        self.lock = threading.Lock()

    def exchange(self, frame, baud, parity, stopbits):
        with self.lock:
            if not self.usb:
                raise OSError('USB adapter absent')
            if not self.cable or (baud, parity, stopbits) != (self.baud, self.parity, self.stopbits):
                raise TimeoutError('No inverter response; USB still present')
            commands = {self.p.request(c): c for c in self.p.read_commands()}
            if frame not in commands:
                allowed = {p.request(c) for p in [PIProtocol(n) for n in PROTOCOLS]
                           for c in ('QPI' if 'QPI' in p.codec.COMMANDS else 'PI', p.primary)}
                assert frame in allowed, 'Only allowlisted read queries may reach the wire'
                raise TimeoutError('Different protocol framing')
            command = commands[frame]
            self.requests.append(command)
            if command in ('QPI', 'PI'):
                return packet((b'('+self.p.family.encode()) if command == 'QPI' else b'^D000'+self.p.family[2:].encode())
            if command == self.p.primary:
                return primary_packet(self.p)
            if command in ('QID', 'ID'):
                if self.serial is None:
                    raise TimeoutError('Serial unsupported')
                return packet((b'(' if command == 'QID' else b'^D000')+self.serial.encode())
            definition = self.p.codec.COMMANDS.get(command)
            if definition and definition.get('test_responses'):
                return packet(definition['test_responses'][0][:-3])
            raise TimeoutError('Optional read unsupported')

    def factory(self, port, baud=2400, timeout=.2, parity='N', stopbits=1):
        return SimpleNamespace(baud=baud, parity=parity, stopbits=stopbits,
                               exchange=lambda frame: self.exchange(frame, baud, parity, stopbits))


def real_broker(tmp_path):
    client = Mock()
    client.publish.return_value.rc = 0
    broker = Broker('localhost', client=client, manifest_path=tmp_path/'manifest.json')
    broker.connected.set()
    return broker


def poll_until_identified(worker):
    for _ in range(40):
        try:
            worker.poll()
            return
        except DetectionPending:
            worker.protocol = None
    raise AssertionError('Detection did not finish')


@pytest.mark.parametrize('name', PROTOCOLS)
def test_strict_status_for_every_supported_codec(name):
    p = PIProtocol(name)
    values = p.decode(primary_packet(p), p.primary)
    assert len(values) >= 10
    assert all(isinstance(value, (str, int, float, bool)) for value, unit in values.values())


@pytest.mark.parametrize('name', ['PI30', 'PI18', 'PI17', 'PI16', 'PI41'])
def test_auto_detects_different_families_in_read_only_mode(tmp_path, name):
    device = Device(name)
    w = Worker(config(protocol='AUTO', auto_baudrates=[2400]), real_broker(tmp_path), device.factory)
    poll_until_identified(w)
    assert w.online and w.protocol.family == name
    assert w.serial == device.serial
    assert set(device.requests) <= set(device.p.read_commands())
    assert w.identity_status in ('identified', 'probable')


@pytest.mark.parametrize('disconnect', ['usb', 'cable'])
def test_both_disconnects_reconnect_without_worker_crash(tmp_path, disconnect):
    device = Device()
    b = real_broker(tmp_path)
    w = Worker(config(protocol='AUTO', poll_interval=.2, auto_baudrates=[2400]), b, device.factory)
    stop = threading.Event()
    thread = threading.Thread(target=w.run, args=(stop,))
    thread.start()
    try:
        wait_for(lambda: w.online)
        identifier = w.name
        setattr(device, disconnect, False)
        wait_for(lambda: not w.online and w.failures > 0)
        assert thread.is_alive()
        assert disconnect != 'cable' or device.usb
        b.client.publish.assert_any_call(f'inverter/{identifier}/availability', 'offline', retain=True, qos=1)
        setattr(device, disconnect, True)
        wait_for(lambda: w.online)
        assert w.name == identifier
    finally:
        stop.set(); thread.join(2)
    assert not thread.is_alive()


def test_silent_same_port_swap_never_publishes_to_previous_serial(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    w = Worker(config(protocol='AUTO', auto_baudrates=[2400]), b, device.factory)
    w.poll()
    original = w.name
    device.serial = '8382222010502'
    before = b.client.publish.call_count
    with pytest.raises(ResponseError, match='Different inverter'):
        w.poll()
    new_calls = b.client.publish.call_args_list[before:]
    assert not any(c.args[0].endswith('/state') for c in new_calls)
    w.offline(); w.protocol = None
    w.poll()
    assert w.name != original
    b.client.publish.assert_any_call(f'inverter/{original}/availability', 'offline', retain=True, qos=1)


def test_device_recognized_with_new_adapter_and_after_process_restart(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    first = Worker(config(name='one', protocol='AUTO', auto_baudrates=[2400]), b, device.factory)
    first.poll(); identifier = first.name
    b.release(first)
    second = Worker(config(name='two', port='/dev/new-adapter', protocol='AUTO', auto_baudrates=[2400]), b, device.factory)
    second.poll()
    assert second.name == identifier
    restarted = real_broker(tmp_path)
    third = Worker(config(name='three', protocol='AUTO', auto_baudrates=[2400]), restarted, device.factory)
    third.poll()
    assert third.name == identifier


def test_no_serial_uses_explicit_anonymous_sessions_without_history_reuse(tmp_path):
    device = Device(serial=None)
    b = real_broker(tmp_path)
    w = Worker(config(protocol='AUTO', auto_baudrates=[2400]), b, device.factory)
    w.poll(); first = w.name
    assert first.startswith('session_') and not w.identity['unique_identity']
    w.offline(); w.protocol = None; w.poll()
    assert w.name != first
    assert not any(f'mpp_{first}_' in t for t in b.discovery)
    assert not b.identities


def test_placeholder_serial_is_never_a_physical_identity(tmp_path):
    device = Device(serial='0000000000000')
    w = Worker(config(protocol='AUTO', auto_baudrates=[2400]), real_broker(tmp_path), device.factory)
    w.poll()
    assert w.serial is None and w.name.startswith('session_')


def test_verified_migration_preserves_023_mqtt_ids(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    w = Worker(config(expected_serial=device.serial), b, device.factory)
    w.poll()
    assert w.name == 'INVERTER_1'
    assert 'homeassistant/sensor/mpp_INVERTER_1_battery_voltage/config' in b.discovery
    device.serial = '8382222010502'; w.protocol = None
    with pytest.raises(ResponseError, match='expected_serial'):
        w.poll()


def test_duplicate_serial_on_two_ports_is_not_merged(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    first = Worker(config(name='one'), b, device.factory)
    second = Worker(config(name='two'), b, device.factory)
    first.poll()
    with pytest.raises(ValueError, match='Duplicate serial'):
        second.poll()
    with pytest.raises(IdentityCollision, match='ambiguous'):
        first.poll()
    b.release(second)
    first.poll()
    assert first.online


def test_independent_port_keeps_polling_during_another_port_failure(tmp_path):
    b = real_broker(tmp_path)
    bad, good = Device(), Device(serial='8382222010502')
    bad.usb = False
    first = Worker(config(name='bad'), b, bad.factory)
    second = Worker(config(name='good'), b, good.factory)
    stop = threading.Event()
    threads = [threading.Thread(target=w.run, args=(stop,)) for w in (first, second)]
    try:
        for thread in threads: thread.start()
        wait_for(lambda: first.failures and second.online)
        assert all(thread.is_alive() for thread in threads)
    finally:
        stop.set()
        for thread in threads: thread.join(2)


def test_progressive_search_is_bounded_and_exhausts_all_standard_serial_settings():
    calls = []
    def factory(baud, parity, stop):
        def exchange(frame):
            calls.append((baud, parity, stop, frame))
            raise TimeoutError('Unknown device')
        return SimpleNamespace(exchange=exchange)
    detector = AutoDetector(config(auto_baudrates=[50, 2400, 4000000]))
    with pytest.raises(DetectionPending): detector.step(factory)
    assert len(calls) == 2
    assert {a[0] for a in detector.attempts} == {50, 2400, 4000000}
    assert {(a[1], a[2]) for a in detector.attempts} == {(p, s) for p in 'NEO' for s in (1, 2)}
    for _ in range(len(detector.attempts)):
        try: detector.step(factory)
        except DetectionPending: continue
        except ResponseError as exc:
            assert 'complete serial search' in str(exc)
            break
    else: raise AssertionError('Search never completed')


def test_inventory_prefers_by_id_and_deduplicates_aliases(monkeypatch):
    import inverter_runtime.ports as ports
    monkeypatch.setattr(ports.glob, 'glob', lambda pattern: ['/dev/serial/by-id/adapter'] if 'by-id' in pattern else ['/dev/ttyXRUSB0'] if 'XRUSB' in pattern else [])
    monkeypatch.setattr(ports.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(ports.os.path, 'realpath', lambda path: '/dev/ttyXRUSB0')
    monkeypatch.setattr(ports.list_ports, 'comports', lambda: [SimpleNamespace(device='/dev/ttyXRUSB0')])
    assert discover_ports() == ['/dev/serial/by-id/adapter']


def test_hotplug_supervisor_replaces_adapter_and_excludes_configured_ports(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    ports = ['/dev/adapter-one']
    def factory(entry, broker): return Worker(entry, broker, device.factory)
    supervisor = Supervisor([config(port='AUTO', protocol='AUTO', auto_baudrates=[2400], exclude_ports=['/dev/battery'])],
                            b, scanner=lambda: list(ports), worker_factory=factory)
    try:
        supervisor.refresh()
        first = next(iter(supervisor.running.values()))[0]
        wait_for(lambda: first.online)
        original = first.name
        ports[:] = ['/dev/battery']
        supervisor.refresh()
        wait_for(lambda: not next(iter(supervisor.running.values()))[2].is_alive())
        supervisor.refresh(); assert not supervisor.running
        ports[:] = ['/dev/adapter-two']
        supervisor.refresh()
        second = next(iter(supervisor.running.values()))[0]
        wait_for(lambda: second.online)
        assert second.name == original
    finally: supervisor.close()


def test_discovery_replayed_after_real_process_manifest_reload(tmp_path):
    device = Device()
    b = real_broker(tmp_path)
    w = Worker(config(), b, device.factory); w.poll()
    original_topics = dict(b.discovery)
    restarted = real_broker(tmp_path)
    restarted.on_connect(restarted.client, None, {}, 0)
    for topic, payload in original_topics.items():
        restarted.client.publish.assert_any_call(topic, payload, qos=1, retain=True)
    restarted.client.publish.assert_any_call(w.availability, 'offline', qos=1, retain=True)


def test_manual_mqtt_setting_commands_have_no_handler(tmp_path):
    b = real_broker(tmp_path)
    device = Device(); w = Worker(config(), b, device.factory)
    b.on_connect(b.client, None, {}, 0)
    b.on_message(b.client, None, SimpleNamespace(topic='inverter/INVERTER_1/set/output_source_priority', payload=b'SBU first'))
    assert not device.requests
    assert b.client.subscribe.call_args_list == [__import__('unittest').mock.call('homeassistant/status', qos=0)]


@pytest.mark.parametrize('bad', ['nan', 'garbage', '99999'])
def test_crc_valid_invalid_numeric_status_is_rejected(bad):
    p = PIProtocol('PI30'); raw = primary_packet(p)
    fields = p.codec.get_responses(raw); fields[1] = bad.encode()
    with pytest.raises(ResponseError): p.decode(packet(b'('+b' '.join(fields)), p.primary)


def test_changed_serial_between_two_status_checks_is_not_published(tmp_path):
    device = Device(); b = real_broker(tmp_path)
    w = Worker(config(), b, device.factory); w.prepare()
    original_exchange = w.transport.exchange
    def swap(frame):
        raw = original_exchange(frame)
        if frame == device.p.request(device.p.primary): device.serial = '8382222010502'
        return raw
    w.transport.exchange = swap
    before = b.client.publish.call_count
    with pytest.raises(ResponseError): w.poll()
    assert not any(c.args[0].endswith('/state') for c in b.client.publish.call_args_list[before:])


def test_inconsistent_serial_during_prepare_does_not_downgrade_to_anonymous(tmp_path):
    device = Device(); b = real_broker(tmp_path)
    count = 0
    def factory(*args, **kwargs):
        transport = device.factory(*args, **kwargs)
        original = transport.exchange
        def exchange(frame):
            nonlocal count
            if frame == device.p.request('QID'):
                count += 1
                if count == 2: device.serial = '8382222010502'
            return original(frame)
        transport.exchange = exchange
        return transport
    w = Worker(config(), b, factory)
    with pytest.raises(ResponseError, match='Serial changed'):
        w.prepare()
    assert not b.discovery and not b.identities


def test_protocol_variant_ambiguity_is_explicit(tmp_path):
    device = Device('PI30'); b = real_broker(tmp_path)
    w = Worker(config(protocol='AUTO', auto_baudrates=[2400]), b, device.factory)
    w.poll()
    assert w.identity_status == 'probable'
    assert {'PI30', 'PI30REVO'} <= set(w.identity['candidates'])
    assert w.identity['unique_identity']  # protocol certainty and physical identity are separate


def test_serial_backed_legacy_id_with_session_prefix_is_not_retired(tmp_path):
    device = Device(); b = real_broker(tmp_path)
    w = Worker(config(name='session_legacy', expected_serial=device.serial), b, device.factory)
    w.poll(); original = dict(b.discovery)
    b.release(w)
    assert b.discovery == original
    restarted = real_broker(tmp_path)
    restarted.on_connect(restarted.client, None, {}, 0)
    assert restarted.discovery == original


def test_inventory_error_keeps_existing_worker(tmp_path):
    device = Device(); b = real_broker(tmp_path)
    scanner = Mock(side_effect=[['/dev/adapter'], OSError('udev unavailable')])
    supervisor = Supervisor([config(port='AUTO', protocol='AUTO', auto_baudrates=[2400])], b,
        scanner=scanner, worker_factory=lambda entry, broker: Worker(entry, broker, device.factory))
    try:
        supervisor.refresh(); first = next(iter(supervisor.running.values()))[0]
        wait_for(lambda: first.online)
        supervisor.refresh()
        assert next(iter(supervisor.running.values()))[0] is first and first.online
    finally: supervisor.close()


def test_duplicate_serial_claim_clears_when_explicit_port_disconnects(tmp_path):
    first_device, second_device = Device(), Device()
    b = real_broker(tmp_path)
    first = Worker(config(name='one', poll_interval=.2), b, first_device.factory)
    second = Worker(config(name='two', poll_interval=.2), b, second_device.factory)
    first.poll()
    stop = threading.Event()
    thread = threading.Thread(target=second.run, args=(stop,))
    thread.start()
    try:
        wait_for(lambda: not b.unique_claim(first))
        second_device.cable = False
        wait_for(lambda: b.unique_claim(first), 6)
        first.poll()
        assert first.online and thread.is_alive()
    finally: stop.set(); thread.join(2)


def test_auto_diagnostics_distinguish_timeout_and_rejected_response():
    detector = AutoDetector(config(auto_baudrates=[2400]))
    def silent(*args):
        def exchange(frame):
            raise TimeoutError('Inverter response timeout')
        return SimpleNamespace(exchange=exchange)
    with pytest.raises(DetectionPending, match=r'2400 baud 8N1 probe=PI18.*TimeoutError'):
        detector.step(silent)
    with pytest.raises(DetectionPending, match='Response received but rejected'):
        detector.step(lambda *args: SimpleNamespace(exchange=lambda frame: b'garbage\r'))


def test_codec_construction_does_not_emit_info_spam(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        AutoDetector(config())
    assert not any('Using protocol' in record.message for record in caplog.records)


def test_fast_pass_retries_slow_inverter_with_normal_timeout(tmp_path):
    device = Device()
    broker = real_broker(tmp_path)
    attempts = []
    def factory(port, baud=2400, timeout=3, parity='N', stopbits=1):
        transport = SimpleNamespace(baud=baud, parity=parity, stopbits=stopbits, timeout=timeout)
        def exchange(frame):
            attempts.append(transport.timeout)
            if transport.timeout < 1:
                raise TimeoutError('Slow device needs normal timeout')
            return device.exchange(frame, baud, parity, stopbits)
        transport.exchange = exchange
        return transport
    worker = Worker(config(protocol='AUTO', timeout=3, auto_baudrates=[2400]), broker, factory)
    for _ in range(20):
        try:
            worker.prepare()
            break
        except DetectionPending:
            continue
    else:
        raise AssertionError('Slow inverter never retried')
    assert .4 in attempts and 3 in attempts
    assert worker.transport.timeout == 3 and worker.serial == device.serial


def test_detection_progress_does_not_wait_for_measurement_interval(tmp_path):
    worker = Worker(config(protocol='AUTO', poll_interval=5), real_broker(tmp_path), Device().factory)
    stop = threading.Event()
    starts = []
    def poll():
        starts.append(time.monotonic())
        if len(starts) >= 4:
            stop.set()
        raise DetectionPending('Search continues')
    worker.poll = poll
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start(); thread.join(2)
    stop.set(); thread.join(1)
    assert len(starts) == 4
    assert starts[-1]-starts[0] < 1


def test_unopenable_port_backs_off_without_scanning_other_settings():
    detector = AutoDetector(config())
    calls = []
    def factory(*args):
        calls.append(args)
        def exchange(frame):
            raise OSError('Adapter absent')
        return SimpleNamespace(exchange=exchange)
    with pytest.raises(OSError, match='Adapter absent'):
        detector.step(factory)
    assert len(calls) == 1
