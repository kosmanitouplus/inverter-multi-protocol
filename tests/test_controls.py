"""Read-only contract, including old configurations and manually sent MQTT setters."""
import threading
from unittest.mock import Mock
import pytest
from inverter_runtime.service import Worker
from inverter_runtime.protocols import PIProtocol
from inverter_runtime.config import load_config

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
    b.unique_claim.return_value = True
    b.identifier.side_effect = lambda identity, preferred=None: preferred or 'device_'+__import__('hashlib').sha256(identity.encode()).hexdigest()[:24]
    return b



@pytest.mark.parametrize('name', ['PI30', 'PI18', 'PI17', 'PI30REVO'])
def test_no_setter_can_be_framed_even_with_writing_flag(name):
    p = PIProtocol(name)
    with pytest.raises(ValueError):
        p.request('POP00', writing=True)
    with pytest.raises(ValueError):
        p.request('POP00')


def test_worker_exposes_no_write_api():
    worker = Worker(config(allow_writes=True), broker())
    assert not hasattr(worker, 'apply_setting')
    assert not hasattr(worker, 'enqueue')
    assert not worker.controls
