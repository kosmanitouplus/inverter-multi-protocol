import threading
import time
from unittest.mock import Mock
import pytest
from inverter_runtime.controls import checked_number, controls_for
from inverter_runtime.protocols import PIProtocol
from inverter_runtime.service import Worker
from inverter_runtime.config import load_config
from test_protocols import valid


def config(**extra):
    c = dict(name='INVERTER_1', port='/dev/test', protocol='PI30', baud=2400,
             timeout=.2, parity='N', stopbits=1, poll_interval=5,
             query_interval=60, commands=[], controls=['output_source_priority'],
             write_limits={}, allow_writes=False)
    c.update(extra)
    return c


def broker():
    b = Mock()
    b.generation = 1
    b.connected.is_set.return_value = True
    return b


def settings(p):
    raw = valid(p.codec.COMMANDS['QPIRI']['test_responses'][0])
    return p.decode(raw, 'QPIRI')


def test_select_options_generated_from_rating_and_setter_regex():
    p = PIProtocol('PI30')
    controls = controls_for(p, settings(p), ['output_source_priority', 'battery_type'], {})
    c = controls['output_source_priority']
    assert c.command('Utility first', p) == 'POP00'
    assert c.command('SBU first', p) == 'POP02'
    with pytest.raises(ValueError):
        c.command('POP03\r', p)
    # Setter supports only first three battery types, not every option in rating.
    assert len(controls['battery_type'].options) == 3


@pytest.mark.parametrize('bad', ['nan', 'inf', '-1', '100', '50.05', 'POP00'])
def test_numeric_bounds_and_step_are_enforced(bad):
    with pytest.raises(ValueError):
        checked_number(bad, dict(min=48, max=58.4, step=.1))


def test_voltage_controls_require_explicit_limits():
    p = PIProtocol('PI30')
    assert not controls_for(p, settings(p), ['battery_bulk_charge_voltage'], {})
    c = controls_for(p, settings(p), ['battery_bulk_charge_voltage'],
                     {'battery_bulk_charge_voltage': dict(min=48., max=58., step=.1)})
    assert c['battery_bulk_charge_voltage'].command('54.2', p) == 'PCVV54.2'


def prepared(**extra):
    w = Worker(config(allow_writes=True, **extra), broker())
    w.protocol = PIProtocol('PI30')
    w.confirmed = w.online = True
    w.last_good = time.monotonic()
    w.controls = controls_for(w.protocol, settings(w.protocol), ['output_source_priority'], {})
    w.query_topics['QPIRI'] = 'availability/qpiri'
    w.snapshot['QPIRI'] = {'timestamp': time.time()}
    return w


def test_ack_and_matching_readback_required():
    w = prepared()
    w.transport = Mock()
    w.transport.exchange.return_value = b'(ACK9 \r'
    w.read = Mock(return_value={'Output Source Priority': ('SBU first', '')})
    w.apply_setting(('output_source_priority', 'SBU first', time.monotonic(), 1), threading.Event())
    assert w.transport.exchange.call_count == 1
    assert w.transport.exchange.call_args.args[0].startswith(b'POP02')
    result = [c.args[1] for c in w.broker.publish.call_args_list if c.args[0].endswith('command_result')][-1]
    assert result['status'] == 'confirmed'


def test_lost_ack_never_retries_a_write():
    w = prepared()
    w.transport = Mock()
    w.transport.exchange.side_effect = TimeoutError('ACK lost')
    w.apply_setting(('output_source_priority', 'SBU first', time.monotonic(), 1), threading.Event())
    assert w.transport.exchange.call_count == 1
    result = [c.args[1] for c in w.broker.publish.call_args_list if c.args[0].endswith('command_result')][-1]
    assert result['status'] == 'unconfirmed'


@pytest.mark.parametrize('change', ['offline', 'unconfirmed', 'expired', 'generation', 'bad_value', 'stopped'])
def test_invalid_or_stale_requests_never_reach_the_wire(change):
    w = prepared()
    w.transport = Mock()
    stop = threading.Event()
    item = ['output_source_priority', 'SBU first', time.monotonic(), 1]
    if change == 'offline': w.online = False
    if change == 'unconfirmed': w.confirmed = False
    if change == 'expired': item[2] -= 31
    if change == 'generation': item[3] = 0
    if change == 'bad_value': item[1] = 'POP02'
    if change == 'stopped': stop.set()
    w.apply_setting(tuple(item), stop)
    w.transport.exchange.assert_not_called()


def test_settings_disabled_by_default():
    w = Worker(config(), broker())
    w.enqueue('output_source_priority', 'SBU first', time.monotonic())
    assert w.setting_queue.empty()


def test_disconnect_during_rate_limit_wait_rejects_write():
    w=prepared()
    w.last_setting=time.monotonic()
    w.transport=Mock()
    stop=Mock()
    stop.is_set.return_value=False
    def wait(duration):
        w.broker.generation+=1
        return False
    stop.wait.side_effect=wait
    w.apply_setting(('output_source_priority','SBU first',time.monotonic(),1),stop)
    w.transport.exchange.assert_not_called()


def capabilities(p):
    return {c:p.decode(valid(p.codec.COMMANDS[c]['test_responses'][0]),c)
            for c in ('QMCHGCR','QMUCHGCR')}


def test_charge_current_options_only_use_reported_values():
    p=PIProtocol('PI30')
    cs=controls_for(p,settings(p),['max_charging_current','max_ac_charging_current'],{},capabilities(p))
    assert cs['max_charging_current'].command('40 A',p)=='MCHGC040'
    assert cs['max_ac_charging_current'].command('2 A',p)=='MUCHGC002'
    assert cs['max_charging_current'].matches('40 A',{'Max Charging Current':(40,'A')})
    assert cs['max_charging_current'].state_value(40)=='40 A'
    assert '120 A' not in cs['max_charging_current'].options
    for bad in ('41 A','120 A','40','MCHGC040','40 A\r'):
        with pytest.raises(ValueError):cs['max_charging_current'].command(bad,p)


def test_three_digit_dialect_explicit_and_parallel_destination():
    p=PIProtocol('PI30')
    cs=controls_for(p,settings(p),['max_charging_current'],{},capabilities(p),command_unit=2,charge_current_command='MNCHGC')
    assert cs['max_charging_current'].command('120 A',p)=='MNCHGC2120'


def test_parallel_machine_not_guessed_and_technician_limits_apply():
    p=PIProtocol('PI30');r=settings(p);r['Output Mode']=('parallel output','')
    assert not controls_for(p,r,['max_charging_current'],{},capabilities(p))
    cs=controls_for(p,r,['max_charging_current'],{'max_charging_current':dict(min=20,max=60,step=10)},capabilities(p),command_unit=1)
    assert set(cs['max_charging_current'].options)=={'20 A','30 A','40 A','50 A','60 A'}
    assert cs['max_charging_current'].command('60 A',p)=='MCHGC160'


def test_no_charge_controls_without_capability_response():
    p=PIProtocol('PI30')
    assert not controls_for(p,settings(p),['max_charging_current'],{})


def test_stale_settings_reject_write():
    w=prepared();w.snapshot['QPIRI']['timestamp']-=1000;w.transport=Mock()
    w.apply_setting(('output_source_priority','SBU first',time.monotonic(),1),threading.Event())
    w.transport.exchange.assert_not_called()


def test_cutoff_voltage_uses_individual_voltage_limits_and_readback():
    p=PIProtocol('PI30')
    for bounds,value,command in [(dict(min=10.,max=12.,step=.1),'11.1','PSDV11.1'),
                                 (dict(min=40.,max=48.,step=.1),'42.5','PSDV42.5')]:
        c=controls_for(p,settings(p),['battery_cutoff_voltage'],{'battery_cutoff_voltage':bounds})['battery_cutoff_voltage']
        assert c.command(value,p)==command
        assert c.matches(value,{'Battery Under Voltage':(float(value),'V')})


@pytest.mark.parametrize('name,field,query,frame',[
    ('max_charging_current','Max Charging Current','QMCHGCR',b'MCHGC040'),
    ('max_ac_charging_current','Max AC Charging Current','QMUCHGCR',b'MUCHGC040')])
def test_charge_setting_requires_ack_readback_and_fresh_options(name,field,query,frame):
    w=prepared();w.controls=controls_for(w.protocol,settings(w.protocol),[name],{},capabilities(w.protocol))
    w.query_topics[query]='capabilities';w.snapshot[query]={'timestamp':time.time()}
    w.transport=Mock();w.transport.exchange.return_value=b'(ACK9 \r'
    w.read=Mock(return_value={field:(40,'A')})
    w.apply_setting((name,'40 A',time.monotonic(),1),threading.Event())
    assert w.transport.exchange.call_args.args[0].startswith(frame)
    result=[c.args[1] for c in w.broker.publish.call_args_list if c.args[0].endswith('command_result')][-1]
    assert result['status']=='confirmed'
    w.transport.reset_mock();w.query_errors[query]=1
    w.apply_setting((name,'40 A',time.monotonic(),1),threading.Event())
    w.transport.exchange.assert_not_called()
