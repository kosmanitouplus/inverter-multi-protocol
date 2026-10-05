import json
import os
import pty
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock
import pytest
from inverter_runtime.service import Worker
from inverter_runtime.transport import Transport, port_lock
from inverter_runtime.protocols import PIProtocol
from test_controls import config, broker
from test_protocols import valid


def wait_for(predicate, timeout=6):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.02)
    raise AssertionError('Condition did not become true')


class SerialInverter:
    def __init__(self, link):
        self.master, self.slave = pty.openpty()
        self.link = link
        if link.exists() or link.is_symlink(): link.unlink()
        link.symlink_to(os.ttyname(self.slave))
        self.stop = threading.Event()
        self.requests = []
        self.protocol = PIProtocol('PI30')
        self.thread = threading.Thread(target=self.run)
        self.thread.start()
    def run(self):
        data = b''
        while not self.stop.is_set():
            if not select.select([self.master], [], [], .05)[0]: continue
            try: data += os.read(self.master, 256)
            except OSError: continue
            while b'\r' in data:
                frame, data = data.split(b'\r', 1)
                command = frame[:-2].decode('ascii')
                self.requests.append(command)
                definition = self.protocol.codec.COMMANDS.get(command)
                response = valid(definition['test_responses'][0]) if definition and definition.get('test_responses') else b'(NAKss\r'
                os.write(self.master, response)
    def close(self):
        self.stop.set()
        self.thread.join(1)
        os.close(self.master)
        os.close(self.slave)
        if self.link.is_symlink(): self.link.unlink()


@pytest.mark.skipif(sys.platform == 'darwin', reason='macOS PTY open/close behavior differs; exercised on Linux CI')
def test_absent_cable_hot_replug_and_independent_inverter(tmp_path):
    bad_path, good_path = tmp_path/'missing', tmp_path/'good'
    good = SerialInverter(good_path)
    b = broker()
    bad_worker = Worker(config(name='absent', port=str(bad_path), poll_interval=.2), b)
    good_worker = Worker(config(name='good', port=str(good_path), poll_interval=.2), b)
    stop = threading.Event()
    threads = [threading.Thread(target=w.run, args=(stop,)) for w in (bad_worker, good_worker)]
    revived = None
    try:
        for t in threads: t.start()
        wait_for(lambda: good_worker.online and bad_worker.failures > 0)
        assert all(t.is_alive() for t in threads)
        revived = SerialInverter(bad_path)
        wait_for(lambda: bad_worker.online)
        revived.close(); revived = None
        wait_for(lambda: not bad_worker.online)
        assert good_worker.online
        revived = SerialInverter(bad_path)
        wait_for(lambda: bad_worker.online)
        assert all(c.startswith('Q') for c in good.requests+revived.requests)
    finally:
        stop.set()
        for t in threads: t.join(3)
        good.close()
        if revived: revived.close()
    assert all(not t.is_alive() for t in threads)


def test_serial_absence_is_an_exception_not_process_exit(tmp_path):
    with pytest.raises(OSError):
        Transport(str(tmp_path/'missing'), timeout=.2).exchange(b'QPIGS\r')


def test_symlink_ports_share_transaction_lock(tmp_path):
    original = tmp_path/'serial'
    alias = tmp_path/'alias'
    alias.symlink_to(original)
    assert port_lock(str(alias)) is port_lock(str(original))


def test_real_tcp_fragmented_response():
    server = socket.socket()
    server.bind(('127.0.0.1', 0)); server.listen()
    response = valid(PIProtocol('PI30').codec.COMMANDS['QPIGS']['test_responses'][0])
    def serve():
        with server.accept()[0] as conn:
            conn.recv(128)
            for chunk in (response[:7], response[7:]): conn.sendall(chunk)
        server.close()
    t = threading.Thread(target=serve); t.start()
    try:
        assert Transport(f'tcp://127.0.0.1:{server.getsockname()[1]}', timeout=1).exchange(b'QPIGS\r') == response
    finally: t.join(2)


def test_process_lives_without_serial_and_broker_and_stops(tmp_path):
    options = tmp_path/'options.json'
    options.write_text(json.dumps({'port': str(tmp_path/'absent'), 'mqtt_host':'127.0.0.1', 'mqtt_port':1}))
    addon = Path(__file__).resolve().parents[1]/'inverter-multi-protocol'
    proc = subprocess.Popen([sys.executable, '-m', 'inverter_runtime', '--options', str(options)], cwd=addon,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(1)
        assert proc.poll() is None
        proc.terminate()
        output = proc.communicate(timeout=5)[0].decode()
        assert proc.returncode == 0 and 'stopped cleanly' in output
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()


def test_failed_optional_query_keeps_primary_online():
    w = Worker(config(), broker())
    w.protocol = PIProtocol('PI30'); w.commands = ['QPIGS', 'QPIRI']
    w.query_topics = {'QPIGS':'live', 'QPIRI':'settings'}
    w.read = Mock(side_effect=[{'Voltage':(230,'V')}, TimeoutError('unsupported')])
    w.poll()
    assert w.online and w.query_errors['QPIRI'] == 1
    assert w.query_due['QPIRI'] > time.monotonic()


def test_polling_is_start_to_start_without_bursts():
    w = Worker(config(poll_interval=.2), broker())
    starts = []
    stop = threading.Event()
    def poll():
        starts.append(time.monotonic())
        time.sleep(.06)
        if len(starts) == 4: stop.set()
    w.poll = poll
    thread = threading.Thread(target=w.run, args=(stop,)); thread.start(); thread.join(3)
    assert not thread.is_alive() and len(starts) == 4
    assert all(.19 <= b-a < .3 for a,b in zip(starts, starts[1:]))


def test_real_modbus_tcp_shared_gateway_and_write_readback():
    from inverter_runtime.modbus import ModbusProtocol
    from test_modbus import Device, profile
    server = socket.socket(); server.bind(('127.0.0.1',0)); server.listen()
    port = server.getsockname()[1]
    units = {1:Device(1,True), 2:Device(2,True)}
    units[2].value = 2400
    errors = []
    def serve():
        try:
            for _ in range(4):
                with server.accept()[0] as conn:
                    frame = b''
                    while len(frame) < 7 or len(frame) < 6+int.from_bytes(frame[4:6],'big'):
                        chunk = conn.recv(128)
                        if not chunk: raise EOFError()
                        frame += chunk
                    raw = units[frame[6]].exchange(frame)
                    conn.sendall(raw[:3]); conn.sendall(raw[3:])
        except Exception as exc: errors.append(exc)
        finally: server.close()
    thread=threading.Thread(target=serve);thread.start()
    transport=Transport(f'tcp://127.0.0.1:{port}',timeout=1)
    try:
        first=ModbusProtocol('MODBUS_TCP',profile(write=dict(min=200,max=250,step=.1)),1)
        second=ModbusProtocol('MODBUS_TCP',profile(),2)
        assert first.read('Voltage',transport)['Voltage'][0] == 230
        assert second.read('Voltage',transport)['Voltage'][0] == 240
        assert first.write('Voltage','231.2',transport)['Voltage'][0] == pytest.approx(231.2)
    finally:
        thread.join(3)
        if thread.is_alive(): server.close()
    assert not errors and not thread.is_alive()


def test_shared_lock_wait_is_bounded(tmp_path):
    t=Transport(str(tmp_path/'port'),timeout=.2)
    t.lock.acquire()
    try:
        start=time.monotonic()
        with pytest.raises(TimeoutError,match='busy'):t.exchange(b'QPI\r')
        assert time.monotonic()-start < .5
    finally:t.lock.release()


def test_invalid_configuration_waits_then_resumes_without_exit(tmp_path,monkeypatch):
    from inverter_runtime.service import wait_for_valid_config
    import inverter_runtime.service as service
    stop=Mock();stop.is_set.return_value=False
    monkeypatch.setattr(service,'load_config',Mock(side_effect=[ValueError('Bad config'),({'inverters':[]},[])]))
    assert wait_for_valid_config('options','profiles',stop)==({'inverters':[]},[])
    stop.wait.assert_called_once_with(2)


def test_invalid_configuration_wait_is_interruptible(monkeypatch):
    from inverter_runtime.service import wait_for_valid_config
    import inverter_runtime.service as service
    stop=threading.Event()
    def invalid(*args):
        stop.set();raise ValueError('Bad config')
    monkeypatch.setattr(service,'load_config',invalid)
    assert wait_for_valid_config('options','profiles',stop) is None


def test_process_lives_with_invalid_config_then_hot_rename(tmp_path):
    options=tmp_path/'options.json'
    options.write_text(json.dumps({'inverter_name':''}))
    addon=Path(__file__).resolve().parents[1]/'inverter-multi-protocol'
    proc=subprocess.Popen([sys.executable,'-m','inverter_runtime','--options',str(options)],cwd=addon,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        time.sleep(.5);assert proc.poll() is None
        values={'inverter_name':'Onduleur Étage','port':str(tmp_path/'missing'),'mqtt_host':'127.0.0.1','mqtt_port':1}
        options.write_text(json.dumps(values));time.sleep(2.5)
        assert proc.poll() is None
        values['inverter_name']='Onduleur Atelier'
        options.write_text(json.dumps(values));time.sleep(2.5)
        assert proc.poll() is None
        proc.terminate();output=proc.communicate(timeout=5)[0].decode()
        assert proc.returncode==0 and 'Invalid configuration' in output
        assert 'Configuration corrected' in output and 'Configuration changed' in output
        assert 'Onduleur_Etage' in output and 'Onduleur_Atelier' in output
        assert 'Traceback' not in output
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
