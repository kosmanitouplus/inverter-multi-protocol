"""Serial inventory; aliases are deduplicated by physical device."""
import glob
import os
from serial.tools import list_ports


def discover_ports():
    found = {}
    for pattern in ('/dev/serial/by-id/*', '/dev/serial/by-path/*'):
        for path in sorted(glob.glob(pattern)):
            if os.path.exists(path):
                found.setdefault(os.path.realpath(path), path)
    for item in sorted(list_ports.comports(), key=lambda p: p.device):
        found.setdefault(os.path.realpath(item.device), item.device)
    # Some multi-channel drivers are absent from pyserial's USB enumeration.
    for pattern in ('/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyXRUSB*', '/dev/ttyAMA*',
                    '/dev/ttySC*', '/dev/ttyTHS*', '/dev/rfcomm*'):
        for path in sorted(glob.glob(pattern)):
            found.setdefault(os.path.realpath(path), path)
    # No consoles, pseudoterminals or arbitrary HID peripherals.
    return list(found.values())
