# Raspberry Pi 1 compatibility and implementation review

Reviewed on 21 October 2026. Primary target: original Raspberry Pi 1, ARMv6,
possibly 256 MB RAM. The board marking `2011.12` alone does not establish model,
revision or RAM. Use `deploy/doctor.py` after booting to record actual details.

## Recommended baseline

| Component | Decision |
|---|---|
| OS | Raspberry Pi OS Lite **32-bit**, Trixie; no desktop |
| Existing compatible installation | Raspberry Pi OS Lite 32-bit Bookworm is also accepted |
| CPU ABI | Pi 1 is ARMv6; use Raspberry Pi OS packages built for that hardware |
| Python | Keep minimum 3.11; Bookworm uses 3.11, Trixie uses 3.13 |
| Serial dependency | OS `python3-serial` (pySerial 3.5); pure Python |
| Installation | System Python plus root-owned application source, no system pip writes |
| Runtime | One receiver loop and one HTTP thread, two latest telemetry blocks |
| Packaging | No Docker, NumPy, pandas, pyshark, compiler or native pip wheels needed on Pi |
| Telemetry history | Existing external Prometheus/Grafana server |
| Logs | JSON stdout to local journald; no Alloy on Pi 1 |
| Hardware watchdog/GPIO | Not automatically configured; separate commissioning |

The official download page lists 32-bit Trixie as compatible with all Pi models.
At review time its Lite image is dated 6 October 2026. Prefer that image for a new
installation. A working Bookworm installation can be retained; the installer checks
the distribution codename and actual Python version. Do not downgrade Python to 3.9
for an obsolete OS, use a 64-bit image, or substitute generic ARMv7 binaries.
[Official OS downloads](https://www.raspberrypi.com/software/operating-systems/)

Python versions are based on the distribution package baselines:
[Bookworm Python](https://packages.debian.org/bookworm/python3),
[Trixie Python](https://packages.debian.org/trixie/python3).
The installer verifies the installed interpreter and pySerial instead of assuming
that a board's age implies an old Python version. The pip/setuptools project remains
available for development, but the Pi deployment does not need its build environment.

## Board identification

Run `sh deploy/install.sh --check`. It reads model, revision, RAM, OS, Python and
serial-port inventory without opening a USB port. A Pi 1 normally reports `armv6l`
from `uname -m`, while Raspberry Pi OS reports the package architecture as `armhf`.
That package label alone does not prove compatibility with generic Debian armhf binaries.

Relevant original Model B codes:

| Revision | Board revision | RAM |
|---|---|---|
| 0002, 0003 | B rev 1.0 | 256 MB |
| 0004, 0005, 0006 | B rev 2.0 | 256 MB |
| 000d, 000e, 000f | B rev 2.0 | 512 MB |

Use the reported code and the [official revision table](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#old-style-revision-codes).
Do not infer the processor from the `Hardware: BCM2835` text alone; later boards
can report the same string. A label date does not identify a particular RAM variant.

## Review of the work completed so far

1. Protocol reconstruction is useful and independent of board architecture: 1553
   valid frames, two telemetry tables, verified byte-sum checksum. The same offline
   fixtures can run on Pi 1. No evidence establishes live polling yet.
2. The existing web image is Linux/amd64 and belongs on the current server/NAS.
   It is not the Pi deployment artifact. GitHub web builds remain unchanged.
3. The old notify/watchdog unit could time out while a passive controller legitimately
   sent nothing. It is replaced by a passive `Type=exec` unit without a telemetry
   watchdog. `/healthz` monitors worker responsiveness; telemetry availability is
   separate. A healthy process with no data is explicitly allowed.
4. The previous HTTP server created a thread per request. It now serializes HTTP
   requests on one background thread with a three-second client timeout. The serial
   worker is independent. This suits a small number of trusted monitoring clients.
5. Replay now streams JSONL rather than loading the whole report. Offline PCAP
   analysis still loads captures into memory: perform large forensic analyses on the PC.
6. Shutdown handles SIGTERM/SIGINT, waits are interruptible, and USB disconnects
   immediately invalidate cached blocks. Missing pySerial is a startup error rather
   than endless reconnect retries. Reconnect attempts back off for five seconds.
7. The new installer preserves configuration, stages versioned source, validates
   imports and the systemd unit, and checks HTTP health when starting. If an update
   fails, an existing managed release/configuration/unit can be restored automatically.
8. Semantic aliases, startup-cache freshness, device identity validation and active
   request/response matching remain unresolved. The passive exporter is diagnostic,
   not a completed web replacement or an input for actuator control.

## Memory, storage and logging

Performance adequacy for a few reads per second is an engineering expectation, not
a benchmark on this particular Pi. The service has no historical queue and stores
only the latest blocks. A 64 MiB service memory cap and 16-task cap are deployment
budgets, not measured usage. Verify that the kernel supports their cgroup enforcement.
Check `systemctl show jer-usb -p MemoryCurrent -p TasksCurrent` during commissioning.
256 MB boards need OS headroom; do not run desktop, local Prometheus, Grafana or Loki.

The application writes no periodic sample files to the SD card. Journald can still
write to disk; an optional bounded/volatile configuration is documented in the install
guide. It is a host-wide policy and is never silently changed by the installer.
Use a reliable power supply, cable and SD card; measure stability on the actual unit.

Current [Alloy installation support](https://grafana.com/docs/alloy/latest/set-up/install/)
lists Linux AMD64 and ARM64, not ARMv6. `deploy/alloy.example` remains a template for
a supported host that already has access to the relevant journal. A remote server
cannot read the Pi's journal merely by copying that configuration. Remote log forwarding
is a separate integration (for example existing syslog infrastructure), not installed here.

## Dallas and watchdog follow-up

USB operation needs no GPIO changes. For a later DS18B20 implementation, verify
the exact header and wiring first. The official `w1-gpio` overlay supports a chosen
BCM GPIO; its default is GPIO4. Consult the installed overlay README and board pinout,
not NanoPi instructions or arbitrary physical pin numbers.
[Official overlays](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README)

The DS18B20 module is still a design, not implemented. No boot configuration is
modified. Likewise, service restart and the BCM2835 hardware watchdog must not be
confused with a verified return of boiler relays to AUTO. Pi 4/5 EEPROM watchdog
instructions do not apply to Pi 1. Defer hardware watchdog activation until storage,
boot and failure recovery have been tested on this board.

## Verification limits

Local tests run on Windows/Python 3.12, not ARMv6. CI is configured for Python
3.11/3.12/3.13 on Linux x86-64; this is interpreter coverage, not Pi hardware coverage.
Installation through apt, systemd startup, USB access, memory use and sustained
operation must still be verified on the actual Pi. Do not claim those checks passed
based only on offline tests or the official OS compatibility statement.
