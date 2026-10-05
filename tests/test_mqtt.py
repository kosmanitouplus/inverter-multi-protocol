import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
from inverter_runtime.mqtt import Broker, GLOBAL
from inverter_runtime.service import Worker
from inverter_runtime.protocols import PIProtocol
from test_controls import config


def setup(tmp_path, **extra):
    client = Mock()
    client.publish.return_value.rc = 0
    broker = Broker('localhost', client=client, manifest_path=tmp_path/'manifest.json')
    worker = Worker(config(**extra), broker)
    worker.protocol = PIProtocol('PI30'); worker.commands = ['QPIGS', 'QPIRI']
    worker.query_topics = {'QPIGS':'inverter/INVERTER_1/availability/qpigs',
                           'QPIRI':'inverter/INVERTER_1/availability/qpiri'}
    return client, broker, worker


def test_start_before_broker_then_replay_persistent_discovery(tmp_path):
    client, broker, worker = setup(tmp_path)
    broker.readings(worker, 'QPIGS', {'AC Output Voltage':(230.0,'V')})
    client.publish.assert_not_called()
    broker.on_connect(client, None, {}, 0)
    topic = 'homeassistant/sensor/mpp_INVERTER_1_ac_output_voltage/config'
    configs = [call for call in client.publish.call_args_list if call.args[0] == topic]
    assert configs and configs[-1].kwargs == {'qos':1,'retain':True}
    definition = json.loads(configs[-1].args[1])
    assert definition['unique_id'] == 'mpp_INVERTER_1_ac_output_voltage'
    assert definition['device']['identifiers'] == ['mpp_INVERTER_1']
    assert len(definition['availability']) == 3
    assert definition['availability_mode'] == 'all'
    client.publish.assert_any_call(worker.availability, 'offline', qos=1, retain=True)
    broker.readings(worker, 'QPIGS', {'AC Output Voltage':(231.0,'V')})
    client.publish.assert_any_call('homeassistant/sensor/mpp_INVERTER_1_ac_output_voltage/state',
                                   231., retain=False, qos=0)
    broker.on_disconnect(client, None, 1)
    assert not broker.connected.is_set()
    broker.on_connect(client, None, {}, 0)
    assert broker.generation == 2


def test_retained_duplicate_and_large_commands_rejected(tmp_path):
    client, b, w = setup(tmp_path, allow_writes=True)
    w.enqueue = Mock()
    for attrs in ({'retain':True}, {'dup':True}, {'payload':b'x'*257}):
        values = dict(topic='inverter/INVERTER_1/set/output_source_priority', payload=b'SBU first', retain=False, dup=False)
        values.update(attrs)
        b.on_message(client, None, SimpleNamespace(**values))
    w.enqueue.assert_not_called()
    b.on_message(client, None, SimpleNamespace(topic='inverter/INVERTER_1/set/output_source_priority',
                                              payload=b'SBU first', retain=False, dup=False))
    assert w.enqueue.call_count == 1


def test_disabled_controls_removed_on_next_start(tmp_path):
    manifest = tmp_path/'manifest.json'
    topic = 'homeassistant/select/mpp_INVERTER_1_setting_output_source_priority/config'
    manifest.write_text(json.dumps([topic]))
    client, b, w = setup(tmp_path)
    b.on_connect(client, None, {}, 0)
    client.publish.assert_any_call(topic, '', qos=1, retain=True)
    assert json.loads(manifest.read_text())['topics'] == []


def test_queries_do_not_overwrite_primary_entity(tmp_path):
    client, b, w = setup(tmp_path)
    b.on_connect(client, None, {}, 0)
    b.readings(w, 'QPIGS', {'Battery Voltage':(51.,'V')})
    b.readings(w, 'QPIRI', {'Battery Voltage':(48.,'V')})
    assert 'homeassistant/sensor/mpp_INVERTER_1_battery_voltage/config' in b.discovery
    assert 'homeassistant/sensor/mpp_INVERTER_1_qpiri_battery_voltage/config' in b.discovery
    assert w.snapshot['QPIGS']['values']['Battery Voltage']['value'] == 51


def test_real_mqtt_late_start_and_socket_reconnect(tmp_path):
    import socket
    import threading
    from test_recovery import wait_for
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    stop = threading.Event()
    drop = threading.Event()
    sessions = []
    def packet(conn):
        first = conn.recv(1)
        if not first: raise EOFError()
        length, shift = 0, 0
        while True:
            b = conn.recv(1)[0]; length += (b & 127) << shift
            if not b & 128: break
            shift += 7
        payload = b''
        while len(payload) < length:
            part = conn.recv(length-len(payload))
            if not part: raise EOFError()
            payload += part
        return first[0], payload
    def serve():
        listener.listen(); listener.settimeout(.1)
        while not stop.is_set():
            try: conn, _ = listener.accept()
            except socket.timeout: continue
            with conn:
                conn.settimeout(.1)
                sessions.append(conn)
                try:
                    kind, body = packet(conn)
                    assert kind == 0x10
                    conn.sendall(b'\x20\x02\x00\x00')
                    while not stop.is_set():
                        if drop.is_set(): drop.clear(); break
                        try: kind, body = packet(conn)
                        except socket.timeout: continue
                        if kind & 240 == 0x30 and (kind >> 1) & 3 == 1:
                            n = int.from_bytes(body[:2], 'big')
                            conn.sendall(b'\x40\x02'+body[2+n:4+n])
                        elif kind == 0xc0: conn.sendall(b'\xd0\x00')
                        elif kind == 0x82: conn.sendall(b'\x90\x03'+body[:2]+b'\x00')
                        elif kind == 0xe0: break
                except (EOFError, OSError, IndexError): pass
        listener.close()
    b = Broker('127.0.0.1', port, manifest_path=tmp_path/'real.json')
    thread = threading.Thread(target=serve)
    b.start()
    try:
        time.sleep(.2)
        assert not b.connected.is_set()
        thread.start()
        wait_for(b.connected.is_set, 8)
        first_generation = b.generation
        drop.set()
        wait_for(lambda: b.generation > first_generation, 8)
        assert len(sessions) >= 2
    finally:
        b.stop(); stop.set()
        if thread.ident: thread.join(2)
        else: listener.close()


def test_daily_energy_entities_keep_stable_ids(tmp_path):
    client, b, w = setup(tmp_path)
    b.on_connect(client, None, {}, 0)
    b.readings(w, 'QED20261005', {'PV Energy':(12.,'kWh')})
    b.readings(w, 'QED20261006', {'PV Energy':(13.,'kWh')})
    assert [topic for topic in b.discovery if 'pv_energy' in topic] == ['homeassistant/sensor/mpp_INVERTER_1_qed_pv_energy/config']


def test_client_interface_can_hide_controls_without_disabling_technician_mqtt(tmp_path):
    from inverter_runtime.controls import controls_for
    from test_controls import settings, capabilities
    client,b,w=setup(tmp_path,allow_writes=True,expose_controls=False,
                     controls=['max_charging_current'])
    w.controls=controls_for(w.protocol,settings(w.protocol),w.config['controls'],{},capabilities(w.protocol))
    b.connected.set()
    b.discover_controls(w)
    assert not b.discovery
    w.enqueue('max_charging_current','40 A',time.monotonic())
    assert not w.setting_queue.empty()


def test_current_control_discovery_and_readback_state(tmp_path):
    from inverter_runtime.controls import controls_for
    from test_controls import settings, capabilities
    client,b,w=setup(tmp_path,allow_writes=True)
    w.controls=controls_for(w.protocol,settings(w.protocol),['max_charging_current'],{},capabilities(w.protocol))
    w.query_topics['QMCHGCR']='inverter/INVERTER_1/availability/qmchgcr'
    b.connected.set();b.discover_controls(w)
    d=json.loads(b.discovery['homeassistant/select/mpp_INVERTER_1_setting_max_charging_current/config'])
    assert '40 A' in d['options'] and len(d['availability'])==4
    b.readings(w,'QPIRI',{'Max Charging Current':(40,'A')})
    client.publish.assert_any_call('inverter/INVERTER_1/settings/max_charging_current','40 A',retain=False,qos=0)


import pytest


@pytest.mark.parametrize('new_name', ['Atelier', 'INVERTER'])
def test_rename_removes_old_discovery_and_groups_new_device(tmp_path,new_name):
    client,old,worker=setup(tmp_path)
    old.connected.set();old.readings(worker,'QPIGS',{'AC Output Voltage':(230,'V')})
    old_topic='homeassistant/sensor/mpp_INVERTER_1_ac_output_voltage/config'
    replacement=Broker('localhost',client=client,manifest_path=tmp_path/'manifest.json')
    new=Worker(config(name=new_name,display_name='Onduleur Atelier'),replacement)
    new.protocol=PIProtocol('PI30');new.commands=['QPIGS']
    new.query_topics={'QPIGS':'inverter/Atelier/availability/qpigs'}
    replacement.on_connect(client,None,{},0)
    client.publish.assert_any_call(old_topic,'',qos=1,retain=True)
    replacement.readings(new,'QPIGS',{'AC Output Voltage':(231,'V')})
    definition=json.loads(replacement.discovery[f'homeassistant/sensor/mpp_{new_name}_ac_output_voltage/config'])
    assert definition['device']['name']=='Onduleur Atelier'
    assert definition['device']['identifiers']==[f'mpp_{new_name}']
    assert old_topic not in replacement.persisted_topics
