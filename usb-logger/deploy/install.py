"""Install/update the passive logger on Raspberry Pi OS Bookworm/Trixie.

Uses distro Python and pySerial: no pip, native builds, Docker or private captures.
Old releases/configuration are retained for manual rollback. Does not enable GPIO,
hardware watchdogs or application-level USB writes.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
from jer_usb.settings import load, validate
from doctor import report

BASE = Path('/opt/jer-usb')
CONFIG = Path('/etc/jer-usb/config.json')
UNIT = Path('/etc/systemd/system/jer-usb.service')


def run(*command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def os_supported(release):
    return release.get('ID') in ('raspbian', 'debian') and release.get('VERSION_CODENAME') in ('bookworm', 'trixie')


def config_for(path, args):
    config = load(path) if path.exists() else {}
    for key, value in (('passive_port', args.port), ('host', args.listen), ('port', args.metrics_port)):
        if value is not None:
            config[key] = value
    return validate(config)


def replace_link(link, target):
    if link.exists() and not link.is_symlink():
        raise ValueError(f'Refusing to replace a real directory: {link}')
    temporary = link.with_name(link.name + f'.new-{os.getpid()}')
    temporary.symlink_to(target, target_is_directory=True)
    temporary.replace(link)


def write_config(path, config):
    temporary = path.with_name(path.name + f'.new-{os.getpid()}')
    temporary.write_text(json.dumps(config, indent=2)+'\n', encoding='utf-8')
    temporary.chmod(0o644)  # Contains a device path/listener only, no credentials.
    temporary.replace(path)


def check_health(config, timeout=15):
    host = '127.0.0.1' if config['host'] == '0.0.0.0' else config['host']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'jer-usb.service']).returncode == 0:
            try:
                with opener.open(f'http://{host}:{config["port"]}/healthz', timeout=2) as response:
                    if response.status == 200 and response.read(128) == b'worker responsive\n':
                        return
            except OSError:
                pass
        time.sleep(1)
    raise RuntimeError('Service health check failed; inspect journalctl -u jer-usb')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only host report; no root required')
    parser.add_argument('--port', help='Actual /dev/serial/by-id/... or by-path/... device')
    parser.add_argument('--listen', help='IPv4 bind address (default: 127.0.0.1)')
    parser.add_argument('--metrics-port', type=int, help='Default: 9109')
    parser.add_argument('--start', action='store_true', help='Start and enable passive service after installation')
    parser.add_argument('--skip-apt', action='store_true', help='Use already installed OS packages')
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(report(), indent=2))
        return
    if platform.system() != 'Linux' or os.geteuid() != 0:
        parser.error('Install on the Raspberry Pi with sudo; use --check for read-only inspection')
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ required; install Raspberry Pi OS Lite 32-bit Bookworm/Trixie')
    release = platform.freedesktop_os_release()
    if not os_supported(release):
        parser.error('Supported installer baseline: Raspberry Pi OS / Debian Bookworm or Trixie')
    if not Path('/run/systemd/system').exists():
        parser.error('A running systemd installation is required')
    os.umask(0o022)
    config = config_for(CONFIG, args)
    if 'REPLACE' in config['passive_port']:
        parser.error('Replace the serial-port placeholder before installation')
    if not Path(config['passive_port']).exists():
        print('NOTE: serial path is currently absent; the passive service will retry until connected.')
    if not args.skip_apt:
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', '--no-install-recommends', 'python3', 'python3-serial', 'ca-certificates')
    # Match the interpreter used by the systemd unit, including distro pySerial.
    run('/usr/bin/python3', '-c',
        'import sys, serial; assert sys.version_info >= (3,11); assert (3,5) <= tuple(map(int,serial.VERSION.split(".")[:2])) < (4,0)')
    import grp
    import pwd
    try:
        grp.getgrnam('dialout')
    except KeyError:
        parser.error('The OS dialout group is missing; configure serial access first')
    try:
        grp.getgrnam('jer-usb')
    except KeyError:
        run('groupadd', '--system', 'jer-usb')
    try:
        account = pwd.getpwnam('jer-usb')
        if account.pw_uid == 0:
            parser.error('jer-usb must not be root')
    except KeyError:
        run('useradd', '--system', '--gid', 'jer-usb', '--groups', 'dialout',
            '--home-dir', '/nonexistent', '--no-create-home', '--shell', '/usr/sbin/nologin', 'jer-usb')

    BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    CONFIG.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    current = BASE/'current'
    if current.exists() and not current.is_symlink():
        parser.error('/opt/jer-usb/current must be a symlink, not a directory')
    previous = current.resolve() if current.is_symlink() else None
    source_files = sorted((SOURCE/'src/jer_usb').glob('*.py'))
    digest = hashlib.sha256(b''.join(p.read_bytes() for p in source_files)).hexdigest()[:12]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = BASE/'releases'/f'{stamp}-{digest}-{os.getpid()}'
    (target/'src/jer_usb').mkdir(parents=True, mode=0o755)
    for source in source_files:
        shutil.copyfile(source, target/'src/jer_usb'/source.name)
    for file in target.rglob('*.py'):
        file.chmod(0o644)
    (target/'SOURCE_SHA256').write_text(digest+'\n')
    run('/usr/bin/python3', '-m', 'jer_usb.service', '--help',
        env={**os.environ, 'PYTHONPATH': str(target/'src'), 'PYTHONDONTWRITEBYTECODE': '1'},
        stdout=subprocess.DEVNULL)
    run('systemd-analyze', 'verify', str(SOURCE/'deploy/jer-usb.service'))
    old_config = CONFIG.read_bytes() if CONFIG.exists() else None
    old_unit = UNIT.read_bytes() if UNIT.exists() else None
    if old_config is not None:
        (target/'previous-config.json').write_bytes(old_config)
    if old_unit is not None:
        (target/'previous.service').write_bytes(old_unit)
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'jer-usb.service']).returncode == 0
    try:
        write_config(CONFIG, config)
        shutil.copyfile(SOURCE/'deploy/jer-usb.service', UNIT)
        UNIT.chmod(0o644)
        replace_link(current, target)
        run('systemctl', 'daemon-reload')
        if was_active or args.start:
            run('systemctl', 'reset-failed', 'jer-usb.service')
            run('systemctl', 'restart', 'jer-usb.service')
            check_health(config)
        if args.start:
            run('systemctl', 'enable', 'jer-usb.service')
    except Exception:
        if previous is not None and old_config is not None and old_unit is not None:
            replace_link(current, previous)
            CONFIG.write_bytes(old_config)
            UNIT.write_bytes(old_unit)
            run('systemctl', 'daemon-reload')
            if was_active:
                run('systemctl', 'restart', 'jer-usb.service')
            else:
                run('systemctl', 'stop', 'jer-usb.service')
            print('Previous release/configuration restored after failed deployment.', file=sys.stderr)
        else:
            subprocess.run(['systemctl', 'stop', 'jer-usb.service'])
            print('First installation did not start successfully; files retained for diagnosis.', file=sys.stderr)
        raise
    if previous:
        replace_link(BASE/'previous', previous)
    print(f'Installed {target}')
    print('Mode: PASSIVE. Healthy process does not mean telemetry is available; no polling is implemented.')
    if not (was_active or args.start):
        print('Installed but not started. Start explicitly: sudo systemctl enable --now jer-usb.service')
    print('Configuration: /etc/jer-usb/config.json; logs: journalctl -u jer-usb.service -f')


if __name__ == '__main__':
    main()
