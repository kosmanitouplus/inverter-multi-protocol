import json
import logging
import os
import queue
import re
from datetime import date
import signal
import threading
import time
from pathlib import Path

from .config import load_config
from .controls import Control, controls_for
from .modbus import ModbusProtocol
from .mqtt import Broker, key
from .protocols import PIProtocol, ResponseError, detect
from .transport import Transport

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, config, broker, transport_factory=Transport):
        self.config, self.broker = config, broker
        self.name = config['name']
        self.transport_factory = transport_factory
        self.stop = None
        self.protocol = None
        self.plan_date = None
        self.commands, self.controls = [], {}
        self.snapshot, self.query_topics, self.query_due, self.query_errors = {}, {}, {}, {}
        self.availability = f'inverter/{self.name}/availability'
        self.confirmed, self.online = False, False
        self.last_good, self.failures, self.next_retry = 0, 0, 0
        self.setting_queue = queue.Queue(maxsize=32)
        self.last_setting = 0
        self.generation = broker.generation
        self.transport = self.make_transport(config['baud'])
        broker.register(self)

    def make_transport(self, baud):
        return self.transport_factory(self.config['port'], baud=baud,
                                      timeout=self.config['timeout'], parity=self.config['parity'],
                                      stopbits=self.config['stopbits'])

    def query_key(self, command):
        if command not in self.config['commands']:
            command = re.sub(r'^((?:Q[EL][YMD]|E[YMD]))[0-9]{4,8}$', r'\1', command)
        return key(command)

    def read(self, command):
        if isinstance(self.protocol, ModbusProtocol):
            return self.protocol.read(command, self.transport)
        frame = self.protocol.request(command)
        return self.protocol.decode(self.transport.exchange(frame), command)

    def prepare(self):
        selected = self.config['protocol']
        if selected.startswith('MODBUS'):
            self.protocol = ModbusProtocol(selected, self.config['profile_data'], self.config['unit_id'])
            # The exact register map is explicitly supplied, never inferred.
            self.confirmed = True
        elif selected == 'AUTO':
            rates = list(dict.fromkeys([self.config['baud'], 2400, 9600, 19200]))
            self.protocol, baud, identity = detect(lambda rate: self.make_transport(rate).exchange, rates,
                                                   should_stop=lambda: self.stop is not None and self.stop.is_set())
            self.transport = self.make_transport(baud)
            self.broker.publish(f'inverter/{self.name}/identity',
                                {'protocol': self.protocol.name, 'baud': baud, 'identity': identity})
            self.confirmed = False  # automatic family ID never enables writes
        else:
            self.protocol = PIProtocol(selected)
            self.confirmed = False
            try:
                identity = self.protocol.identify(self.transport.exchange)
                self.confirmed = True
                self.broker.publish(f'inverter/{self.name}/identity',
                                    {'protocol': selected, 'identity': identity})
            except (OSError, ValueError, TimeoutError) as exc:
                LOG.warning('%s: identity not confirmed (%s); writes disabled', self.name, exc)
        self.plan_date = date.today()
        self.commands = self.protocol.read_commands()
        for command in self.config['commands']:
            if isinstance(self.protocol, ModbusProtocol):
                if command not in self.protocol.sensors:
                    raise ValueError('Unknown profile register')
            else:
                self.protocol.definition(command)
            if command not in self.commands:
                self.commands.append(command)
        self.query_due = {command: 0 for command in self.commands}
        self.query_topics.update({c: f'inverter/{self.name}/availability/{self.query_key(c)}' for c in self.commands})
        self.controls = {}
        if isinstance(self.protocol, ModbusProtocol) and self.config['allow_writes']:
            for field, sensor in self.protocol.sensors.items():
                if sensor.get('write'):
                    identifier = key(field)
                    if identifier in self.controls:
                        raise ValueError('Modbus control names collide after normalization')
                    self.controls[identifier] = Control(identifier, field, field, field,
                                                        limits=sensor['write'], unit=sensor.get('unit', ''))
        LOG.info('%s: %s, %d read queries, writes=%s', self.name, self.protocol.name,
                 len(self.commands), self.config['allow_writes'] and self.confirmed)

    def enqueue(self, control, payload, received):
        if not self.config['allow_writes'] or control not in self.controls:
            self.result(control, 'rejected', 'Writes disabled or unsupported setting')
            return
        try:
            self.setting_queue.put_nowait((control, payload, received, self.broker.generation))
        except queue.Full:
            self.result(control, 'rejected', 'Setting queue is full')

    def result(self, control, status, message=''):
        self.broker.publish(f'inverter/{self.name}/command_result',
                            {'setting': control, 'status': status, 'message': message, 'timestamp': time.time()})

    def apply_setting(self, item, stop):
        control_key, payload, received, generation = item
        control = self.controls.get(control_key)
        if (stop.is_set() or not self.config['allow_writes'] or not self.confirmed or not self.online
                or not self.broker.connected.is_set() or generation != self.broker.generation
                or time.monotonic()-received > 30 or time.monotonic()-self.last_good > max(15, self.config['poll_interval']*3)):
            self.result(control_key, 'rejected', 'Offline, unconfirmed protocol, or expired request')
            return
        if not control:
            self.result(control_key, 'rejected', 'Setting is no longer available')
            return
        # Validate everything before reaching the wire. No raw MQTT command API.
        try:
            if isinstance(self.protocol, ModbusProtocol):
                from .controls import checked_number
                checked_number(payload, control.limits)
                command = control_key
            else:
                command = control.command(payload, self.protocol)
        except ValueError as exc:
            self.result(control_key, 'rejected', str(exc))
            return
        if self.last_setting and stop.wait(max(0, 2-(time.monotonic()-self.last_setting))):
            return
        if (stop.is_set() or not self.broker.connected.is_set()
                or generation != self.broker.generation or time.monotonic()-received > 30):
            self.result(control_key, 'rejected', 'Connection changed or request expired while waiting')
            return
        self.last_setting = time.monotonic()
        try:
            if isinstance(self.protocol, ModbusProtocol):
                actual = self.protocol.write(control.field, payload, self.transport)
            else:
                frame = self.protocol.request(command, writing=True)
                self.protocol.decode(self.transport.exchange(frame), command, writing=True)
                actual = self.read(control.query)
                if not control.matches(payload, actual):
                    raise ResponseError('Readback differs from requested setting')
            self.broker.readings(self, control.query, actual)
            self.result(control_key, 'confirmed', 'Acknowledged and read back from inverter')
        except (OSError, ValueError, TimeoutError) as exc:
            # An ACK timeout may follow a successful write: never retry a setter.
            self.result(control_key, 'unconfirmed', str(exc) + '; no automatic retry')
            self.broker.publish(self.query_topics[control.query], 'offline', retain=True, qos=1)

    def poll(self):
        if self.protocol is None:
            self.prepare()
        if self.stop is not None and self.stop.is_set():
            return
        if self.plan_date is not None and self.plan_date != date.today():
            old = set(self.commands)
            self.commands = list(dict.fromkeys(self.protocol.read_commands()+self.config['commands']))
            for command in old-set(self.commands):
                self.snapshot.pop(command, None)
                self.query_due.pop(command, None)
                self.broker.publish(self.query_topics.pop(command), 'offline', retain=True, qos=1)
            self.query_topics.update({c: f'inverter/{self.name}/availability/{self.query_key(c)}' for c in self.commands})
            self.plan_date = date.today()
        primary = self.read(self.protocol.primary)
        self.broker.readings(self, self.protocol.primary, primary)
        self.last_good, self.online, self.failures = time.monotonic(), True, 0
        self.broker.publish(self.availability, 'online', retain=True, qos=1)
        # Spread capability discovery over cycles: primary measurements always
        # keep their requested cadence while slow/unsupported queries back off.
        now = time.monotonic()
        if self.stop is not None and self.stop.is_set():
            return
        slow = [c for c in self.commands if c != self.protocol.primary and self.query_due.get(c, 0) <= now]
        if slow:
            command = min(slow, key=lambda c: self.query_due.get(c, 0))
            try:
                readings = self.read(command)
                self.broker.readings(self, command, readings)
                self.query_errors[command] = 0
                self.query_due[command] = time.monotonic()+self.config['query_interval']
                if command == self.protocol.rating and self.config['allow_writes'] and self.confirmed:
                    self.controls = controls_for(self.protocol, readings, self.config['controls'], self.config['write_limits'])
                    self.broker.discover_controls(self)
                    self.broker.readings(self, command, readings)
            except (OSError, ValueError, TimeoutError) as exc:
                self.query_errors[command] = self.query_errors.get(command, 0)+1
                pause = min(900, max(self.config['query_interval'], 5)*2**min(self.query_errors[command], 6))
                self.query_due[command] = time.monotonic()+pause
                self.broker.publish(self.query_topics[command], 'offline', retain=True, qos=1)
                LOG.warning('%s query %s failed: %s; retry in %.0fs', self.name, command, exc, pause)
        if isinstance(self.protocol, ModbusProtocol) and self.controls:
            self.broker.discover_controls(self)
        self.broker.publish(f'inverter/{self.name}/diagnostics',
                            {'protocol': self.protocol.name, 'online': self.online,
                             'identity_confirmed': self.confirmed, 'query_errors': self.query_errors,
                             'controls': list(self.controls), 'read_queries': self.commands})

    def run(self, stop):
        self.stop = stop
        deadline = time.monotonic()
        while not stop.is_set():
            if self.generation != self.broker.generation:
                self.generation = self.broker.generation
                # Broker reconnect invalidates queued commands and cached health.
                self.online = False
                deadline = time.monotonic()
            if time.monotonic() >= max(deadline, self.next_retry):
                started = time.monotonic()
                try:
                    self.poll()
                    self.next_retry = 0
                except Exception as exc:
                    self.online, self.confirmed = False, False
                    self.failures += 1
                    pause = min(60, 2**min(self.failures, 6))
                    self.next_retry = time.monotonic()+pause
                    self.protocol = None
                    self.controls = {}
                    self.broker.publish(self.availability, 'offline', retain=True, qos=1)
                    LOG.warning('%s offline: %s; retry in %ss', self.name, exc, pause)
                # Start-to-start period; slow exchanges never trigger a burst.
                deadline = max(started+self.config['poll_interval'], time.monotonic())
            try:
                item = self.setting_queue.get_nowait()
            except queue.Empty:
                stop.wait(.1)
            else:
                try:
                    self.apply_setting(item, stop)
                except Exception:
                    LOG.exception('%s setting failed unexpectedly; no retry', self.name)
                    self.result(item[0], 'unconfirmed', 'Internal error; no automatic retry')
        self.online = False
        self.broker.publish(self.availability, 'offline', retain=True, qos=1)


def main(options_path='/data/options.json', profiles_dir='/config/inverter-profiles'):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)
    for name in ('mppsolar', 'protocols', 'AbstractProtocol', 'pi30'):
        logging.getLogger(name).setLevel(logging.WARNING)
    options, entries = load_config(options_path, profiles_dir)
    host = os.environ.get('MQTT_HOST') or options.get('mqtt_host', 'localhost')
    port = os.environ.get('MQTT_PORT') or options.get('mqtt_port', 1883)
    user = os.environ.get('MQTT_USER') or options.get('mqtt_user', '')
    password = os.environ.get('MQTT_PASSWORD') or options.get('mqtt_password', '')
    broker = Broker(host, port, user, password)
    stop = threading.Event()
    def terminate(*args):
        stop.set()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    workers = [Worker(entry, broker) for entry in entries]
    threads = [threading.Thread(target=w.run, args=(stop,), name=w.name) for w in workers]
    broker.start()
    try:
        for thread in threads:
            thread.start()
        while not stop.wait(.5):
            for index, thread in enumerate(threads):
                if not thread.is_alive():
                    worker = workers[index]
                    LOG.error('%s worker ended unexpectedly; restarting independently', worker.name)
                    worker.online = worker.confirmed = False
                    worker.protocol = None
                    worker.next_retry = time.monotonic()+5
                    broker.publish(worker.availability, 'offline', retain=True, qos=1)
                    threads[index] = threading.Thread(target=worker.run, args=(stop,), name=worker.name)
                    threads[index].start()
    finally:
        stop.set()
        # Every exchange is bounded; shutdown may wait for the current exchange.
        for thread in threads:
            if thread.ident is not None:
                thread.join()
        broker.stop()
    LOG.info('Inverter Multi-Protocol stopped cleanly')
