"""Bounded exchanges, shared physical-port locks, no automatic write retries."""
import os
import select
import socket
import threading
import time
from urllib.parse import urlparse
import serial

_LOCKS = {}
_GUARD = threading.Lock()


def port_lock(port):
    key = port if '://' in port else os.path.realpath(port)
    with _GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class Transport:
    def __init__(self, port, baud=2400, timeout=3, parity='N', stopbits=1):
        self.port, self.baud, self.timeout = port, baud, timeout
        self.parity, self.stopbits = parity, stopbits
        self.lock = port_lock(port)

    def exchange(self, request, expected=None, hid=False):
        if not self.lock.acquire(timeout=self.timeout):
            raise TimeoutError('Shared port is busy')
        try:
            if self.port.startswith('tcp://'):
                address = urlparse(self.port)
                with socket.create_connection((address.hostname, address.port), self.timeout) as conn:
                    conn.settimeout(.1)
                    conn.sendall(request)
                    return self._read(conn.recv, expected)
            if hid or self.port.startswith('/dev/hidraw'):
                return self._hid(request)
            # Open on each exchange so USB re-enumeration replaces stale handles.
            with serial.Serial(self.port, self.baud, timeout=.1, write_timeout=self.timeout,
                               parity=self.parity, stopbits=self.stopbits) as conn:
                conn.reset_input_buffer()
                conn.write(request)
                return self._read(conn.read, expected)

        finally:
            self.lock.release()

    def _read(self, read, expected):
        deadline = time.monotonic() + self.timeout
        data = bytearray()
        while time.monotonic() < deadline:
            try:
                chunk = read(1 if expected is None else max(1, expected - len(data)))
            except socket.timeout:
                continue
            if chunk:
                data.extend(chunk)
                if len(data) > 8192:
                    raise ValueError('Oversize response')
                if expected is not None and isinstance(getattr(read, '__self__', None), socket.socket) and len(data) >= 7:
                    expected = 6 + int.from_bytes(data[4:6], 'big')
                    if expected < 9 or expected > 260:
                        raise ValueError('Invalid Modbus TCP length')
                elif expected is not None and len(data) >= 2 and data[1] & 128:
                    expected = 5
                if expected is not None and len(data) >= expected:
                    return bytes(data)
                if expected is None and data.endswith(b'\r'):
                    return bytes(data)
            elif isinstance(getattr(read, '__self__', None), socket.socket):
                raise OSError('Gateway closed the connection')
        raise TimeoutError('Inverter response timeout')

    def _hid(self, request):
        fd = os.open(self.port, os.O_RDWR | os.O_NONBLOCK)
        try:
            for offset in range(0, len(request), 8):
                chunk = request[offset:offset+8].ljust(8, b'\0')
                if os.write(fd, chunk) != len(chunk):
                    raise OSError('Short HID write; command outcome unknown')
                time.sleep(.05)
            deadline = time.monotonic() + self.timeout
            result = bytearray()
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], min(.1, max(0, deadline-time.monotonic())))
                if ready:
                    chunk = os.read(fd, 256)
                    if not chunk:
                        raise OSError('HID disconnected')
                    result.extend(chunk.replace(b'\0', b''))
                    if b'\r' in result:
                        return bytes(result[:result.index(13)+1])
                    if len(result) > 8192:
                        raise ValueError('Oversize HID response')
            raise TimeoutError('HID response timeout')
        finally:
            os.close(fd)
