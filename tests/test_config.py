import json
import pytest
from inverter_runtime.config import load_config


def load(tmp_path, values):
    path=tmp_path/'options.json'; path.write_text(json.dumps(values))
    return load_config(path, tmp_path)


def test_legacy_migration_preserves_name_protocol_port(tmp_path):
    _, entries=load(tmp_path,dict(inverter_name='MY_INVERTER', port='/dev/serial/by-id/usb-original', protocol='PI30', poll_interval=5))
    e=entries[0]
    assert e['name']=='MY_INVERTER' and e['protocol']=='PI30'
    assert e['port']=='/dev/serial/by-id/usb-original'
    assert not e['allow_writes']


@pytest.mark.parametrize('extra', [dict(protocol='AUTO',allow_writes=True), dict(timeout=11),dict(baud=123),
                                  dict(unit_id=0),dict(commands=['POP00']*129), dict(allow_writes='yes')])
def test_invalid_configuration_rejected(tmp_path, extra):
    e=dict(name='one',port='/dev/one');e.update(extra)
    with pytest.raises(ValueError): load(tmp_path,{'inverters':[e]})


def test_shared_modbus_units_and_different_ports(tmp_path):
    (tmp_path/'model.json').write_text(json.dumps({'sensors':[{'name':'Voltage','address':1}]}))
    e=dict(name='one',port='/dev/bus',protocol='MODBUS_RTU',profile='model',unit_id=1)
    second=dict(e,name='two',unit_id=2)
    _,entries=load(tmp_path,{'inverters':[e,second]})
    assert len(entries)==2
    for bad in (dict(second,unit_id=1),dict(second,baud=19200),dict(second,protocol='PI30')):
        with pytest.raises(ValueError):load(tmp_path,{'inverters':[e,bad]})
