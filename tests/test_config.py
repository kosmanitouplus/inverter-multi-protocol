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


@pytest.mark.parametrize('name,identifier',[('Onduleur Étage 1','Onduleur_Etage_1'),
                                         ('Multi Onduleur Robuste','Multi_Onduleur_Robuste'),
                                         ('INVERTER_1','INVERTER_1'),('Client / Atelier','Client_Atelier')])
def test_readable_names_get_safe_ids(tmp_path,name,identifier):
    _, entries=load(tmp_path,{'inverter_name':name})
    assert entries[0]['name']==identifier and entries[0]['display_name']==name


def test_explicit_id_keeps_identity_when_display_name_changes(tmp_path):
    _,entries=load(tmp_path,{'inverters':[dict(name='Onduleur Étage',id='INVERTER_1',port='/dev/one')]})
    assert entries[0]['name']=='INVERTER_1' and entries[0]['display_name']=='Onduleur Étage'
    _,legacy=load(tmp_path,{'inverter_name':'Atelier','inverter_id':'INVERTER_1'})
    assert legacy[0]['name']=='INVERTER_1'


@pytest.mark.parametrize('name',['','   ',None,123,'One\nTwo'])
def test_invalid_display_names_have_clear_errors(tmp_path,name):
    with pytest.raises(ValueError):load(tmp_path,{'inverter_name':name})


def test_identity_collisions_require_explicit_ids(tmp_path):
    values={'inverters':[dict(name='Onduleur 1',port='/dev/one'),dict(name='Onduleur_1',port='/dev/two')]}
    with pytest.raises(ValueError,match='Duplicate inverter identifier'):load(tmp_path,values)
    values['inverters'][1]['id']='SECOND'
    assert len(load(tmp_path,values)[1])==2


def test_non_ascii_only_names_have_deterministic_ids(tmp_path):
    from inverter_runtime.config import inverter_identity
    name='逆变器'
    assert inverter_identity(name)==inverter_identity(name)
    assert inverter_identity(name).startswith('inverter_')
