"""Retained MQTT discovery and offline recovery; no setting subscription or API."""
import hashlib
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


class IdentityCollision(ValueError):
    pass


class Broker:
    def __init__(self, host, port=1883, username='', password='', client=None, manifest_path='/data/inverter-discovery.json'):
        self.client = client or paho.Client(client_id='inverter-multi-protocol', clean_session=True)
        self.connected = threading.Event()
        self.lock = threading.RLock()
        self.discovery, self.discovery_owners, self.identities = {}, {}, {}
        self.persisted_topics, self.tombstones = set(), set()
        self.manifest_path = Path(manifest_path) if manifest_path else None
        if self.manifest_path and self.manifest_path.exists():
            try:
                saved = json.loads(self.manifest_path.read_text())
                if isinstance(saved, list):
                    self.persisted_topics = set(saved)
                else:
                    self.persisted_topics = set(saved['topics'])
                    self.discovery_owners = dict(saved.get('owners', {}))
                    self.discovery = dict(saved.get('discovery', {}))
                    self.identities = dict(saved.get('identities', {}))
                    self.tombstones = set(saved.get('tombstones', []))
            except (OSError, ValueError, TypeError, KeyError):
                LOG.warning('Discovery manifest unreadable; continuing')
        self.workers, self.claims = {}, {}
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
            self.workers[worker.slot] = worker

    def identifier(self, identity_key, preferred=None):
        with self.lock:
            if identity_key not in self.identities:
                identifier = preferred or 'device_'+hashlib.sha256(identity_key.encode()).hexdigest()[:24]
                if identifier in self.identities.values():
                    raise ValueError('MQTT identifier already belongs to a different serial')
                self.identities[identity_key] = identifier
                self.save_manifest()
            return self.identities[identity_key]

    def bind(self, worker, identifier):
        with self.lock:
            previous = self.claims.get(worker.slot)
            self.claims[worker.slot] = identifier
            if previous and previous != identifier and self.anonymous(previous):
                self.retire_session(previous)
            if not self.unique_claim(worker, identifier):
                for slot, value in self.claims.items():
                    if value == identifier and slot in self.workers:
                        self.workers[slot].offline()
                raise IdentityCollision('Duplicate serial across ports: refusing ambiguous history')

    def unique_claim(self, worker, identifier=None):
        with self.lock:
            identifier = identifier or self.claims.get(worker.slot)
            return identifier is None or list(self.claims.values()).count(identifier) == 1

    def unbind(self, worker):
        with self.lock:
            identifier = self.claims.pop(worker.slot, None)
            if identifier and self.anonymous(identifier):
                self.retire_session(identifier)

    def release(self, worker):
        with self.lock:
            self.unbind(worker)
            self.workers.pop(worker.slot, None)

    def anonymous(self, identifier):
        return identifier.startswith('session_') and identifier not in self.identities.values()

    def retire_session(self, identifier):
        owner = 'mpp_'+identifier
        for topic in list(self.persisted_topics):
            if self.discovery_owners.get(topic) == owner:
                self.tombstones.add(topic)
                self.persisted_topics.discard(topic)
                self.discovery.pop(topic, None)
                self.discovery_owners.pop(topic, None)
                self.publish(topic, '', retain=True, qos=1)
        self.save_manifest()

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        if rc:
            LOG.error('MQTT connection refused: %s; will retry', rc)
            return
        with self.lock:
            self.generation += 1
            # Invalidate retained live states from all archived devices before replaying discovery.
            topics = set()
            for payload in self.discovery.values():
                try:
                    definition = json.loads(payload)
                    topics.update(a['topic'] for a in definition.get('availability', []) if a['topic'] != GLOBAL)
                except (TypeError, ValueError, KeyError):
                    continue
            for owner in self.discovery_owners.values():
                if owner.startswith('mpp_'):
                    topics.add(f'inverter/{owner[4:]}/availability')
            for worker in self.workers.values():
                topics.add(worker.availability)
                topics.update(worker.query_topics.values())
            for topic in topics:
                client.publish(topic, 'offline', qos=1, retain=True)
            # Retire anonymous sessions after process restart, and all old writable controls.
            for topic in list(self.persisted_topics):
                owner = self.discovery_owners.get(topic, '')
                if topic.startswith(('homeassistant/select/', 'homeassistant/number/')) or self.anonymous(owner[4:]) and owner[4:] not in self.claims.values():
                    self.tombstones.add(topic)
                    self.persisted_topics.discard(topic)
                    self.discovery.pop(topic, None)
                    self.discovery_owners.pop(topic, None)
            for topic in self.tombstones:
                client.publish(topic, '', qos=1, retain=True)
            for topic, payload in self.discovery.items():
                client.publish(topic, payload, qos=1, retain=True)
            client.subscribe('homeassistant/status', qos=0)
            client.publish(GLOBAL, 'online', qos=1, retain=True)
            self.connected.set()
            self.save_manifest()
        LOG.info('MQTT connected; discovery replayed, fresh readings required')

    def on_disconnect(self, client, userdata, rc):
        self.connected.clear()
        if rc:
            LOG.warning('MQTT disconnected; reconnecting')

    def on_message(self, client, userdata, message):
        if message.topic == 'homeassistant/status' and message.payload == b'online':
            with self.lock:
                for topic, payload in self.discovery.items():
                    client.publish(topic, payload, qos=1, retain=True)
        # No MQTT setting handler, even for a non-retained manually published command.

    def publish(self, topic, payload, retain=False, qos=0):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, allow_nan=False)
        if not self.connected.is_set():
            return False
        result = self.client.publish(topic, payload, retain=retain, qos=qos)
        return result.rc == paho.MQTT_ERR_SUCCESS

    def save_manifest(self):
        if self.manifest_path:
            try:
                self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.manifest_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'version': 3, 'topics': sorted(self.persisted_topics),
                    'owners': self.discovery_owners, 'discovery': self.discovery,
                    'identities': self.identities, 'tombstones': sorted(self.tombstones)}))
                temporary.replace(self.manifest_path)
            except OSError:
                LOG.warning('Cannot persist discovery manifest')

    def discover(self, topic, definition):
        payload = json.dumps(definition, allow_nan=False)
        with self.lock:
            previous = self.discovery.get(topic)
            if previous != payload:
                self.discovery[topic] = payload
                self.persisted_topics.add(topic)
                self.discovery_owners[topic] = definition['device']['identifiers'][0]
                self.tombstones.discard(topic)
                self.save_manifest()
                self.publish(topic, payload, retain=True, qos=1)

    @staticmethod
    def device(worker):
        return {'identifiers': [f'mpp_{worker.name}'],
                'name': worker.config.get('display_name', worker.slot) + (' (anonymous session)' if not worker.serial else ''),
                'manufacturer': worker.config.get('manufacturer', 'Inverter Multi-Protocol'),
                'model': worker.protocol.name, 'sw_version': '0.3.0-rc3',
                **({'serial_number': worker.serial} if worker.serial else {})}

    @staticmethod
    def avail(worker, command):
        return [{'topic': GLOBAL}, {'topic': worker.availability}, {'topic': worker.query_topics[command]}]

    def readings(self, worker, command, readings):
        with self.lock:
            if not self.unique_claim(worker):
                raise ValueError('Ambiguous physical identity: refusing MQTT readings')
            topic = worker.query_topics.setdefault(command, f'inverter/{worker.name}/availability/{worker.query_key(command)}')
            worker.snapshot[command] = {'timestamp': time.time(), 'values':
                                      {f: {'value': v, 'unit': u} for f, (v, u) in readings.items()}}
            for field, (value, unit) in readings.items():
                entity = key(field) if command == worker.protocol.primary else f'{worker.query_key(command)}_{key(field)}'
                binary = unit == 'bool' or value in ('enabled', 'disabled') or isinstance(value, bool)
                component = 'binary_sensor' if binary else 'sensor'
                unique = f'mpp_{worker.name}_{entity}'
                state_topic = f'homeassistant/{component}/{unique}/state'
                definition = {'name': f"{worker.config.get('display_name', worker.slot)} {field}", 'unique_id': unique,
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

    def stop(self):
        if self.connected.is_set():
            info = self.client.publish(GLOBAL, 'offline', qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass
        self.client.disconnect()
        self.client.loop_stop()
