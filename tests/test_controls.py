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
