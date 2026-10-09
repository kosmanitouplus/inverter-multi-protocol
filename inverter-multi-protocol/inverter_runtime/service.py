"""Read-only workers and hotplug supervision; ports never supply inverter identity."""
import hashlib
import json
import logging
import os
import signal
import threading
import time
import uuid
from datetime import date
from pathlib import Path

from .config import load_config
from .modbus import ModbusProtocol
from .mqtt import Broker, IdentityCollision
from .protocols import PIProtocol, ResponseError, AutoDetector, DetectionPending
from .ports import discover_ports
from .transport import Transport

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, config, broker, transport_factory=Transport):
        self.config, self.broker = dict(config), broker
        self.slot = config['name']
        self.name = self.slot
        self.transport_factory = transport_factory
        self.stop = None
        self.protocol = None
        self.commands, self.controls = [], {}
        self.snapshot, self.query_topics, self.query_due, self.query_errors = {}, {}, {}, {}
        self.availability = f'inverter/{self.name}/availability'
        self.confirmed, self.online = False, False
        self.last_good, self.failures, self.next_retry = 0, 0, 0
        self.generation = broker.generation
        self.serial = None
        self.identity_status = 'unknown'
        self.identity = {}
        self.last_log, self.plan_date = 0, None
        self.transport = self.make_transport(config['baud'])
        self.detector = AutoDetector(config)
        broker.register(self)

    def make_transport(self, baud, parity=None, stopbits=None):
        return self.transport_factory(self.config['port'], baud=baud, timeout=self.config['timeout'],
                                      parity=parity or self.config['parity'],
                                      stopbits=stopbits or self.config['stopbits'])

    def query_key(self, command):
        from .mqtt import key
        import re
        if command not in self.config['commands']:
            command = re.sub(r'^((?:Q[EL][YMD]|E[YMD]))[0-9]{4,8}$', r'\1', command)
        return key(command)

    def read(self, command):
        if isinstance(self.protocol, ModbusProtocol):
            return self.protocol.read(command, self.transport)
        return self.protocol.decode(self.transport.exchange(self.protocol.request(command)), command)

    def offline(self):
        self.online = False
        self.broker.publish(self.availability, 'offline', retain=True, qos=1)
        for topic in self.query_topics.values():
            self.broker.publish(topic, 'offline', retain=True, qos=1)

    def bind_identity(self, serial):
        self.offline()
        self.serial = serial
        preferred = self.slot if serial and serial == self.config.get('expected_serial') else None
        identity_key = self.protocol.family+':'+serial if serial else None
        identifier = (self.broker.identifier(identity_key, preferred) if serial
                      else 'session_'+uuid.uuid4().hex)
        self.broker.bind(self, identifier)
        self.name = identifier
        self.availability = f'inverter/{identifier}/availability'
        self.snapshot, self.query_topics, self.query_due, self.query_errors = {}, {}, {}, {}
        self.identity_status = getattr(self.protocol, 'confidence', 'probable')
        self.identity = {'status': self.identity_status, 'protocol': self.protocol.name,
                         'candidates': getattr(self.protocol, 'candidates', [self.protocol.name]),
                         'serial_number': serial, 'unique_identity': bool(serial),
                         'anonymous_session': not bool(serial), 'port': self.config['port'],
                         'baud': self.transport.baud, 'parity': self.transport.parity,
                         'stopbits': self.transport.stopbits}
        self.broker.publish(f'inverter/{self.name}/identity', self.identity, retain=True, qos=1)

    def prepare(self):
        selected = self.config['protocol']
        if selected.startswith('MODBUS'):
            self.protocol = ModbusProtocol(selected, self.config['profile_data'], self.config['unit_id'])
            self.read(self.protocol.primary)
            serial = None
        else:
            if selected == 'AUTO':
                self.protocol, self.transport = self.detector.step(self.make_transport,
                    should_stop=lambda: self.stop is not None and self.stop.is_set())
            else:
                self.protocol = PIProtocol(selected)
                self.protocol.identify(self.transport.exchange)
                self.read(self.protocol.primary)
                self.protocol.confidence = 'identified'
            try:
                first = self.protocol.serial_number(self.transport.exchange)
            except (OSError, ValueError, TimeoutError):
                serial = None
            else:
                second = self.protocol.serial_number(self.transport.exchange)
                if first != second:
                    raise ResponseError('Serial changed during identification')
                serial = first
        expected = self.config.get('expected_serial')
        if expected and serial != expected:
            raise ResponseError('Connected serial does not match expected_serial; no history binding')
        self.bind_identity(serial)
        self.update_plan()
        LOG.info('%s: %s at %s/%s/%s, %s, identity=%s; read-only', self.slot, self.protocol.name,
                 self.transport.baud, self.transport.parity, self.transport.stopbits,
                 self.identity_status, serial or 'anonymous session')

    def update_plan(self):
        commands = list(dict.fromkeys(self.protocol.read_commands()+self.config['commands']))
        for command in self.config['commands']:
            if isinstance(self.protocol, ModbusProtocol):
                if command not in self.protocol.sensors:
                    raise ValueError('Unknown profile register')
            else:
                self.protocol.definition(command)
        for old in set(self.commands)-set(commands):
            self.snapshot.pop(old, None)
            self.query_due.pop(old, None)
            if old in self.query_topics:
                self.broker.publish(self.query_topics.pop(old), 'offline', retain=True, qos=1)
        self.commands = commands
        self.query_topics.update({c: f'inverter/{self.name}/availability/{self.query_key(c)}' for c in commands})
        self.plan_date = date.today()

    def verify_identity(self):
        if self.stop is not None and self.stop.is_set():
            raise InterruptedError('Worker stopping')
        if self.serial and self.protocol.serial_number(self.transport.exchange) != self.serial:
            raise ResponseError('Different inverter connected; rediscovering identity')
        if not self.broker.unique_claim(self):
            raise IdentityCollision('Serial number reported on multiple ports; identity ambiguous')

    def poll(self):
        if self.protocol is None:
            self.prepare()
        if self.stop is not None and self.stop.is_set():
            return
        if self.plan_date != date.today():
            self.update_plan()
        # Bracket reads so a same-family swap never knowingly reaches the previous identity.
        self.verify_identity()
        primary = self.read(self.protocol.primary)
        self.verify_identity()
        self.broker.readings(self, self.protocol.primary, primary)
        self.last_good, self.online, self.failures = time.monotonic(), True, 0
        self.broker.publish(self.availability, 'online', retain=True, qos=1)
        now = time.monotonic()
        slow = [c for c in self.commands if c != self.protocol.primary and self.query_due.get(c, 0) <= now]
        if slow and not (self.stop is not None and self.stop.is_set()):
            command = min(slow, key=lambda c: self.query_due.get(c, 0))
            try:
                readings = self.read(command)
                self.verify_identity()
                self.broker.readings(self, command, readings)
                self.query_errors[command] = 0
                self.query_due[command] = time.monotonic()+self.config['query_interval']
            except (OSError, ValueError, TimeoutError) as exc:
                self.verify_identity()
                errors = self.query_errors.get(command, 0)+1
                self.query_errors[command] = errors
                pause = min(900, max(self.config['query_interval'], 5)*2**min(errors, 6))
                self.query_due[command] = time.monotonic()+pause
                self.broker.publish(self.query_topics[command], 'offline', retain=True, qos=1)
                if errors == 1:
                    LOG.warning('%s: optional query %s unavailable (%s); backing off', self.slot, command, exc)
        self.broker.publish(f'inverter/{self.name}/diagnostics',
                            {'protocol': self.protocol.name, 'online': self.online,
                             'identification': self.identity_status, 'unique_identity': bool(self.serial),
                             'query_errors': self.query_errors, 'read_only': True})
        self.broker.publish(f'inverter/slots/{self.slot}/diagnostics', self.identity, retain=True, qos=1)

    def run(self, stop):
        self.stop = stop
        deadline = time.monotonic()
        try:
            while not stop.is_set():
                if self.generation != self.broker.generation:
                    self.generation = self.broker.generation
                    self.offline()
                    # MQTT reconnect never resets the polling deadline.
                    if getattr(self, 'identity', None):
                        self.broker.publish(f'inverter/{self.name}/identity', self.identity, retain=True, qos=1)
                if time.monotonic() >= max(deadline, self.next_retry):
                    started = time.monotonic()
                    try:
                        self.poll()
                        self.next_retry = 0
                    except Exception as exc:
                        self.offline()
                        if not isinstance(exc, IdentityCollision):
                            self.broker.unbind(self)
                        self.confirmed = False
                        self.failures += 1
                        pause = self.config['poll_interval'] if isinstance(exc, DetectionPending) else min(60, 2**min(self.failures, 6))
                        self.next_retry = time.monotonic()+pause
                        self.protocol = None
                        self.identity_status = 'unknown'
                        self.broker.publish(f'inverter/slots/{self.slot}/diagnostics',
                                            {'status': 'unknown', 'online': False, 'reason': str(exc)},
                                            retain=True, qos=1)
                        if self.failures == 1 or time.monotonic()-self.last_log >= 60:
                            LOG.warning('%s: offline/unknown (%s); retry in %ss', self.slot, exc, pause)
                            self.last_log = time.monotonic()
                    deadline = max(started+self.config['poll_interval'], time.monotonic())
                stop.wait(.1)
        finally:
            self.offline()
            self.broker.release(self)


def wait_for_valid_config(options_path, profiles_dir, stop):
    last_error = None
    while not stop.is_set():
        try:
            loaded = load_config(options_path, profiles_dir)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            message = str(exc)
            if message != last_error:
                LOG.error('Invalid configuration: %s; service remains alive, correct options to resume', message)
                last_error = message
            stop.wait(2)
            continue
        if last_error is not None:
            LOG.info('Configuration corrected; starting inverter monitoring')
        return loaded
    return None


class Supervisor:
    def __init__(self, entries, broker, scanner=discover_ports, worker_factory=Worker):
        self.entries, self.broker, self.scanner, self.worker_factory = entries, broker, scanner, worker_factory
        self.running = {}
        self.last_ports, self.last_scan_log = [], 0

    def refresh(self):
        desired = {}
        explicit = {os.path.realpath(e['port']) for e in self.entries if e['port'].upper() != 'AUTO'}
        for entry in self.entries:
            if entry['port'].upper() != 'AUTO':
                desired[entry['name']] = entry
            else:
                excluded = {os.path.realpath(v) for v in entry.get('exclude_ports', [])}
                try:
                    self.last_ports = self.scanner()
                except OSError as exc:
                    if time.monotonic()-self.last_scan_log >= 60:
                        LOG.warning('Serial inventory unavailable (%s); keeping previous workers', exc)
                        self.last_scan_log = time.monotonic()
                for port in self.last_ports[:32]:
                    physical = os.path.realpath(port)
                    if physical in explicit or physical in excluded:
                        continue
                    slot = entry['name']+'_'+hashlib.sha256(physical.encode()).hexdigest()[:12]
                    desired[slot] = dict(entry, name=slot, port=port)
        for slot in list(self.running):
            worker, stop, thread = self.running[slot]
            if slot not in desired or worker.config['port'] != desired[slot]['port']:
                stop.set()
                worker.offline()
                if not thread.is_alive():
                    thread.join()
                    del self.running[slot]
            elif not thread.is_alive():
                thread.join()
                del self.running[slot]
        for slot, entry in desired.items():
            if slot not in self.running:
                worker = self.worker_factory(entry, self.broker)
                stop = threading.Event()
                thread = threading.Thread(target=worker.run, args=(stop,), name=slot)
                self.running[slot] = worker, stop, thread
                thread.start()

    def close(self):
        for worker, stop, thread in self.running.values():
            stop.set()
        for worker, stop, thread in self.running.values():
            thread.join()
        self.running.clear()


def main(options_path='/data/options.json', profiles_dir='/config/inverter-profiles'):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)
    for name in ('mppsolar', 'protocols', 'AbstractProtocol', 'pi30'):
        logging.getLogger(name).setLevel(logging.WARNING)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *args: stop.set())
    signal.signal(signal.SIGINT, lambda *args: stop.set())
    while not stop.is_set():
        loaded = wait_for_valid_config(options_path, profiles_dir, stop)
        if loaded is None:
            break
        options, entries = loaded
        broker = Broker(os.environ.get('MQTT_HOST') or options.get('mqtt_host', 'localhost'),
                        os.environ.get('MQTT_PORT') or options.get('mqtt_port', 1883),
                        os.environ.get('MQTT_USER') or options.get('mqtt_user', ''),
                        os.environ.get('MQTT_PASSWORD') or options.get('mqtt_password', ''))
        supervisor = Supervisor(entries, broker)
        broker.start()
        try:
            while not stop.is_set():
                supervisor.refresh()
                if stop.wait(2):
                    break
                try:
                    current = json.loads(Path(options_path).read_text())
                except (OSError, ValueError):
                    current = None
                if current != options:
                    LOG.info('Configuration changed; reloading')
                    break
        finally:
            supervisor.close()
            broker.stop()
    LOG.info('Multi Onduleur Robuste stopped cleanly')
