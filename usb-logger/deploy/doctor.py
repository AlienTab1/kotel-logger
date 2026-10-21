"""Read-only host inventory. Never opens a serial port or changes the OS."""
import glob
import json
import platform
import sys
from pathlib import Path


def read(path):
    try:
        return Path(path).read_text().strip().strip('\0')
    except OSError:
        return None


def report():
    cpu = read('/proc/cpuinfo') or ''
    revision = next((line.split(':', 1)[1].strip() for line in cpu.splitlines()
                     if line.startswith('Revision')), None)
    memory = read('/proc/meminfo') or ''
    result = dict(model=read('/sys/firmware/devicetree/base/model'), revision=revision,
                  machine=platform.machine(), python=platform.python_version(),
                  os_release=read('/etc/os-release'),
                  memory=[s for s in memory.splitlines() if s.startswith(('MemTotal:', 'MemAvailable:'))],
                  stable_serial_paths=sorted(glob.glob('/dev/serial/by-id/*')+glob.glob('/dev/serial/by-path/*')),
                  systemd=Path('/run/systemd/system').exists())
    try:
        import serial
        from serial.tools.list_ports import comports
        result['pyserial'] = serial.VERSION
        result['serial_devices'] = [dict(device=p.device, vid=p.vid, pid=p.pid,
                                        description=p.description) for p in comports()]
    except ImportError:
        result['pyserial'] = 'missing; installer uses the OS python3-serial package'
    return result


if __name__ == '__main__':
    print(json.dumps(report(), indent=2))
    sys.exit(0 if sys.version_info >= (3, 11) else 1)
