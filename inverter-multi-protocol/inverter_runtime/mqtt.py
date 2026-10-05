"""Persistent MQTT, retained discovery, per-query availability, bounded commands."""
import json
import logging
import re
import threading
import time
from pathlib import Path
import paho.mqtt.client as paho

LOG = logging.getLogger(__name__)
GLOBAL = 'inverter_multi_protocol/availability'
UNITS = {'V': ('V', 'voltage'), 'A': ('A', 'current'), 'W': ('W', 'power'),
         'VA': ('VA', 'apparent_power'), 'Hz': ('Hz', 'frequency'),
         'Wh': ('Wh', 'energy'), 'kWh': ('kWh', 'energy'),
         'Deg_C': ('°C', 'temperature'), '°C': ('°C', 'temperature'),
         '%': ('%', None), 'Ah': ('Ah', None), 'min': ('min', 'duration')}


def key(value):
    return re.sub(r'[^a-z0-9_-]', '_', value.lower())


class Broker:
    def __init__(self, host, port=1883, username='', password='', client=None, manifest_path='/data/inverter-discovery.json'):
        self.client = client or paho.Client(client_id='inverter-multi-protocol', clean_session=True)
        self.connected = threading.Event()
        self.lock = threading.RLock()
        self.discovery = {}
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.persisted_topics = set()
        self.discovery_owners = {}
        if self.manifest_path and self.manifest_path.exists():
            try:
                saved = json.loads(self.manifest_path.read_text())
                if isinstance(saved, list):
                    self.persisted_topics = set(saved)  # Migrate the initial manifest format.
                else:
                    self.persisted_topics = set(saved['topics'])
                    self.discovery_owners = dict(saved.get('owners', {}))
            except (OSError, ValueError, TypeError, KeyError):
                LOG.warning('Discovery manifest unreadable; continuing')
        self.workers = {}
        self.generation = 0
        self.host, self.port = host, int(port)
        if username:
            self.client.username_pw_set(username, password)
        self.client.will_set(GLOBAL, 'offline', qos=1, retain=True)
        self.client.reconnect_delay_set(2, 60)
        self.client.max_queued_messages_set(256)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def register(self, worker):
        with self.lock:
            self.workers[worker.name] = worker

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        if rc:
            LOG.error('MQTT connection refused: %s; will retry', rc)
            return
        with self.lock:
            self.generation += 1
            self.cleanup_discovery(client)
            for worker in self.workers.values():
                # Cached discovery/state never means the inverter is still live.
                client.publish(worker.availability, 'offline', qos=1, retain=True)
                for command in list(worker.query_topics):
                    client.publish(worker.query_topics[command], 'offline', qos=1, retain=True)
            for topic, payload in self.discovery.items():
                client.publish(topic, payload, qos=1, retain=True)
            client.subscribe('inverter/+/set/+', qos=0)
            client.subscribe('homeassistant/status', qos=0)
            client.publish(GLOBAL, 'online', qos=1, retain=True)
            self.connected.set()
        LOG.info('MQTT connected')

    def on_disconnect(self, client, userdata, rc):
        self.connected.clear()
        if rc:
            LOG.warning('MQTT disconnected; reconnecting')

    def on_message(self, client, userdata, message):
        if message.topic == 'homeassistant/status' and message.payload == b'online':
            with self.lock:
                for topic, payload in self.discovery.items():
                    client.publish(topic, payload, qos=1, retain=True)
            return
        if message.retain or getattr(message, 'dup', False) or len(message.payload) > 256:
            LOG.warning('Ignoring retained/duplicate/oversize setting request')
            return
        parts = message.topic.split('/')
        if len(parts) != 4 or parts[0] != 'inverter' or parts[2] != 'set':
            return
        try:
            payload = message.payload.decode('utf-8')
            with self.lock:
                worker = self.workers.get(parts[1])
            if worker:
                worker.enqueue(parts[3], payload, time.monotonic())
        except (UnicodeError, ValueError) as exc:
            LOG.warning('Setting request rejected: %s', exc)

    def publish(self, topic, payload, retain=False, qos=0):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, allow_nan=False)
        if not self.connected.is_set():
            return False
        result = self.client.publish(topic, payload, retain=retain, qos=qos)
        return result.rc == paho.MQTT_ERR_SUCCESS

    def cleanup_discovery(self, client):
        for topic in list(self.persisted_topics):
            keep = False
            owner = self.discovery_owners.get(topic)
            for worker in sorted(self.workers.values(), key=lambda w: len(w.name), reverse=True):
                if owner is not None and owner != f'mpp_{worker.name}':
                    continue
                stem = f'mpp_{worker.name}_'
                entity = topic.split('/')[-2] if len(topic.split('/')) == 4 else ''
                if entity.startswith(stem):
                    keep = True
                    self.discovery_owners[topic] = f'mpp_{worker.name}'
                    if entity.startswith(stem+'setting_'):
                        setting = entity[len(stem+'setting_'):]
                        allowed = worker.config['controls']
                        if worker.config['protocol'].startswith('MODBUS'):
                            allowed = [key(v['name']) for v in worker.config['profile_data']['sensors'] if v.get('write')]
                        keep = worker.config['allow_writes'] and worker.config.get('expose_controls', True) and setting in allowed
                    break
            if not keep:
                client.publish(topic, '', qos=1, retain=True)
                self.discovery.pop(topic, None)
                self.persisted_topics.discard(topic)
                self.discovery_owners.pop(topic, None)
        self.save_manifest()

    def save_manifest(self):
        if self.manifest_path:
            try:
                self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.manifest_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'version': 2, 'topics': sorted(self.persisted_topics), 'owners': self.discovery_owners}))
                temporary.replace(self.manifest_path)
            except OSError:
                LOG.warning('Cannot persist discovery manifest')

    def discover(self, topic, definition):
        payload = json.dumps(definition, allow_nan=False)
        with self.lock:
            previous = self.discovery.get(topic)
            self.discovery[topic] = payload
            if topic not in self.persisted_topics:
                self.persisted_topics.add(topic)
                self.discovery_owners[topic] = definition['device']['identifiers'][0]
                self.save_manifest()
        if previous != payload:
            self.publish(topic, payload, retain=True, qos=1)

    @staticmethod
    def device(worker):
        return {'identifiers': [f'mpp_{worker.name}'], 'name': worker.config.get('display_name', worker.name),
                'manufacturer': worker.config.get('manufacturer', 'Inverter Multi-Protocol'),
                'model': worker.protocol.name, 'sw_version': '0.2.3'}

    @staticmethod
    def avail(worker, command):
        return [{'topic': GLOBAL}, {'topic': worker.availability},
                {'topic': worker.query_topics[command]}]

    def readings(self, worker, command, readings):
        topic = worker.query_topics.setdefault(command, f'inverter/{worker.name}/availability/{worker.query_key(command)}')
        now = time.time()
        worker.snapshot[command] = {'timestamp': now, 'values':
                                    {field: {'value': value, 'unit': unit} for field, (value, unit) in readings.items()}}
        for field, (value, unit) in readings.items():
            base = key(field)
            # Primary values preserve the old hass_mqtt unique_id/state topic.
            entity = base if command == worker.protocol.primary else f'{worker.query_key(command)}_{base}'
            binary = unit == 'bool' or value in ('enabled', 'disabled') or isinstance(value, bool)
            component = 'binary_sensor' if binary else 'sensor'
            unique = f'mpp_{worker.name}_{entity}'
            state_topic = f'homeassistant/{component}/{unique}/state'
            definition = {'name': f"{worker.config.get('display_name', worker.name)} {field}", 'unique_id': unique,
                          'state_topic': state_topic, 'device': self.device(worker),
                          'availability': self.avail(worker, command), 'availability_mode': 'all',
                          'expire_after': max(30, int(worker.config['query_interval']*3),
                                              int(worker.config['poll_interval']*len(worker.commands)*3))}
            if not binary and unit in UNITS:
                normalized, device_class = UNITS[unit]
                definition['unit_of_measurement'] = normalized
                if device_class:
                    definition['device_class'] = device_class
                if isinstance(value, (int, float)):
                    definition['state_class'] = 'total_increasing' if device_class == 'energy' else 'measurement'
            self.discover(f'homeassistant/{component}/{unique}/config', definition)
            if binary:
                value = 'ON' if value in (True, 1, '1', 'enabled', 'ON') else 'OFF'
            self.publish(state_topic, value)
        self.publish(topic, 'online', retain=True, qos=1)
        self.publish(f'inverter/{worker.name}/state', worker.snapshot)
        for control in worker.controls.values():
            if control.query == command and control.field in readings:
                self.publish(f'inverter/{worker.name}/settings/{control.key}', control.state_value(readings[control.field][0]))

    def discover_controls(self, worker):
        if not worker.config.get('expose_controls', True):
            return
        for control in worker.controls.values():
            component = 'select' if control.options is not None else 'number'
            definition = {'name': control.key.replace('_', ' '),
                          'unique_id': f'mpp_{worker.name}_setting_{control.key}',
                          'device': self.device(worker), 'entity_category': 'config',
                          'state_topic': f'inverter/{worker.name}/settings/{control.key}',
                          'command_topic': f'inverter/{worker.name}/set/{control.key}',
                          'availability': self.avail(worker, control.query), 'availability_mode': 'all',
                          'optimistic': False, 'retain': False, 'qos': 0}
            if control.capability_query:
                definition['availability'].append({'topic': worker.query_topics[control.capability_query]})
            if control.options is not None:
                definition['options'] = list(control.options)
            else:
                definition.update({k: control.limits[k] for k in ('min', 'max', 'step')})
                if control.unit:
                    definition['unit_of_measurement'] = control.unit
            self.discover(f'homeassistant/{component}/mpp_{worker.name}_setting_{control.key}/config', definition)

    def stop(self):
        if self.connected.is_set():
            info = self.client.publish(GLOBAL, 'offline', qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass
        self.client.disconnect()
        self.client.loop_stop()
