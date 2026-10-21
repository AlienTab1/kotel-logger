# Install the USB diagnostic service on Raspberry Pi 1

This installs **passive commissioning**, not active polling. The service may receive
no telemetry until a verified read-request implementation is added. It never sends
application commands. Opening the CDC serial port still configures the serial link.
Read [compatibility and review](RASPBERRY_PI_COMPATIBILITY.md) for the current limits.

## 1. Prepare the Pi

Use Raspberry Pi Imager to write **Raspberry Pi OS Lite (32-bit), Trixie**.
Select a user/password or SSH key, enable SSH, and set a hostname such as `jer-pi`.
Use Ethernet. Do not select the 64-bit or desktop image. An original Model B uses
a full-size SD slot; use suitable media/adapter for the actual board.
Boot, connect over SSH and check:

```sh
uname -m
python3 --version
cat /etc/os-release
free -h
```

Expected CPU for Pi 1: `armv6l`; Python 3.13 on Trixie or 3.11 on Bookworm.
The installer rejects older releases rather than upgrading the entire OS.

## 2. Transfer the current local version

Changes in this chat are not automatically on GitHub. For the local deployment
bundle, run this from the repository root on the development machine:

```powershell
.\.venv\Scripts\python.exe usb-logger/tools/build_deploy_bundle.py
scp .\usb-logger\dist\jer-usb-logger-0.1.1.tar.gz YOUR_USER@jer-pi.local:~/
```

The bundle contains application source, deploy files and installation documentation;
it excludes captures, decoded device identifiers, secrets and the web logger.
Use the Pi's actual IP if `.local` name resolution is unavailable.

On the Pi:

```sh
mkdir -p ~/jer-usb-deploy
tar -xzf ~/jer-usb-logger-0.1.1.tar.gz -C ~/jer-usb-deploy
cd ~/jer-usb-deploy/jer-usb-logger
sh deploy/install.sh --check
```

After these changes are committed and pushed, a repository checkout is another
option: enter its `usb-logger` directory and run the same installer. Do not expect
an older GitHub checkout to contain the new deployment files.

## 3. Select the serial device

Connect the JER USB cable, close other terminal applications, and inspect:

```sh
ls -l /dev/serial/by-id/ /dev/serial/by-path/
sh deploy/install.sh --check
```

The captured JER has VID/PID `0525:54a7` and uses CDC ACM. Linux should bind
`cdc_acm` and expose a ttyACM device. A by-id directory can be absent if the device
has no usable serial string; then use the correct by-path link and keep the same USB socket.
VID/PID alone is not a unique device identity. The installer does not guess which
attached device to open. If no port appears, inspect `sudo journalctl -k -n 80`
and the cable/power; `sudo modprobe cdc_acm` can check driver availability.

## 4. Install and start

Replace the device path below with the actual link from step 3:

```sh
sudo sh deploy/install.sh --port /dev/serial/by-id/YOUR_ACTUAL_DEVICE --start
```

The script installs `python3`, `python3-serial` and `ca-certificates` using apt,
creates a dedicated `jer-usb` account, copies code to `/opt/jer-usb/releases/`,
switches `/opt/jer-usb/current`, writes `/etc/jer-usb/config.json`, and installs
`jer-usb.service`. With `--start`, it starts the service, checks HTTP health and
enables startup at boot. It does not use pip, Docker or a compiler.

By default HTTP binds only to `127.0.0.1:9109`. For Prometheus on your other server,
provide the Pi's fixed/reserved LAN IPv4 address, for example:

```sh
sudo sh deploy/install.sh --port /dev/serial/by-path/YOUR_ACTUAL_DEVICE --listen 192.168.1.50 --start
```

Use your actual LAN address, not the example. The endpoint has no authentication
or TLS; make it reachable only by trusted monitoring clients. The installer does
not change firewall rules. `--metrics-port 9110` changes the port if required.

Without `--start`, a first installation remains stopped. An already active managed
installation is restarted after an update even without `--start`; configuration
and its boot-enabled state are preserved. Explicit command options override only
the corresponding configuration fields. Use `--skip-apt` when dependencies are
already installed and you want to avoid refreshing/installing OS packages.

## 5. Check operation

```sh
systemctl status jer-usb --no-pager
journalctl -u jer-usb -n 30 --no-pager
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9109/healthz').read().decode())"
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9109/metrics').read().decode())"
systemctl show jer-usb -p MemoryCurrent -p TasksCurrent -p NRestarts
```

If you bound a specific LAN address/port, substitute it in the two URLs.
`/healthz` returning 200 only means the worker is responsive. Check
`jer_usb_telemetry_available` and both `jer_usb_block_available` metrics separately.
With a silent passive controller these should remain zero; this is not a reason
to replay a service/relay command from the capture. Repeated `serial_error` events
mean the device cannot be opened/read; inspect port path, permissions and kernel logs.

Useful operations:

```sh
sudo systemctl stop jer-usb
sudo systemctl restart jer-usb
sudo systemctl disable --now jer-usb
journalctl -u jer-usb -f
```

After editing `/etc/jer-usb/config.json`, restart the service. It accepts only
`passive_port`, `host`, `port` and `stale_s`. Serial baud remains 115200/8N1.
The service uses `dialout` as a supplementary group; the interactive user does not
need serial permissions merely to deploy it. Do not run another program on the same port.

## 6. Connect the existing Prometheus server

Add this to the server's existing jobs, replacing the address:

```yaml
- job_name: jer-usb-commissioning
  scrape_interval: 15s
  scrape_timeout: 5s
  static_configs:
    - targets: ['192.168.1.50:9109']
```

An UP target is not proof of available boiler measurements. The current `jer_usb_*`
slot metrics do not populate the existing `kotel_*` dashboard automatically.
Keep web collection running until polling and semantic aliases are verified.

## 7. Updates and rollback

Transfer/extract the new bundle, then rerun:

```sh
sudo sh deploy/install.sh --skip-apt
```

Existing configuration is preserved; an active service is restarted and checked.
Old releases are retained, so updates consume some disk space. On a failed managed
update the installer attempts to restore the previous code/config/unit and running
state. A first-install failure leaves files for diagnosis but stops the service.
No guarantee is made if the OS itself fails or power is lost during installation.

`/opt/jer-usb/previous` points to the prior successful code release. Before a manual
rollback, record the current target. That current release contains
`previous-config.json` and `previous.service` if they existed when it was installed.
Inspect those files and restore them if the older code requires them. To switch code:

```sh
readlink -f /opt/jer-usb/current
readlink -f /opt/jer-usb/previous
sudo systemctl stop jer-usb
sudo ln -sfn "$(readlink -f /opt/jer-usb/previous)" /opt/jer-usb/current
sudo systemctl daemon-reload
sudo systemctl start jer-usb
```

Only do this when `previous` exists and points to the intended release. To remove
the service from operation, use `disable --now`; source releases/configuration remain
available for recovery. The installer intentionally does not delete old releases.

## 8. Limit SD writes (optional host policy)

The application stores no sample history. Journald can still write to the SD card.
For a dedicated logger where logs may disappear after reboot, an administrator can
create `/etc/systemd/journald.conf.d/jer-pi.conf` with:

```ini
[Journal]
Storage=volatile
RuntimeMaxUse=16M
```

Then restart `systemd-journald`. This affects **all host journals**, not only this
service, so the installer does not apply it. Do not install current Alloy binaries
on ARMv6; remote log forwarding is a separate setup. Prometheus/Grafana/Loki stay
on the existing server. No GPIO overlays, automatic OS upgrades, reboot or hardware
watchdog configuration are performed by this installer.

## Acceptance on the actual Pi

Record the `--check` output, OS package versions, service memory and restart count.
Test a reboot, USB unplug/replug during read-only operation, and at least an overnight
run. Confirm stale readings disappear and the service remains responsive. These
checks establish receiver stability only. Active polling, aliases and any actuator
control require their own evidence and are not declared complete by this deployment.
