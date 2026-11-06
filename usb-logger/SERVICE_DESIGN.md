# SBC service design — Raspberry Pi 1 deployment

## First deployment stage

Raspberry Pi OS Lite 32-bit with `cdc_acm`, system Python 3.11+, dedicated user
`jer-usb`, membership in the distribution's tty access group (verify `dialout`).
Identify the port using `lsusb`, `udevadm info` and `/dev/serial/by-id/`; if no
serial string exists, use the physical USB path/by-path for a stable reference.
Do not identify the port solely by VID/PID or an arbitrary `/dev/ttyACM0`.
The target is now Raspberry Pi 1. Confirm the actual revision and header before GPIO work.
See [installation](INSTALL_RASPBERRY_PI.md) and [compatibility review](RASPBERRY_PI_COMPATIBILITY.md).

`src/jer_usb` contains an offline parser, PCAP reader, diagnostic metrics, replay
and passive transport without application writes. The systemd unit runs **passive commissioning**, not production polling. A silent
controller leaves telemetry unavailable while the process remains healthy. The installer
can enable this diagnostic service explicitly with `--start`. Do not open the same port from two programs.

## Before active polling

1. Use the existing capture01 startup and pre-service segment to inspect 03/04
   initialization, the role of 02, DTR/RTS, identity/routing, and fresh responses
   versus draining buffered data. A new capture is optional validation, not a
   prerequisite for implementation.
2. Implement a fixed whitelist of only 23-byte 0c/13 requests. Obtain the identity
   from explicitly verified configuration, not PCAP replay. Reject other message types.
3. Allow one outstanding request, enforce expected response type, timeout, stable
   identity, exact length and checksum. Late, duplicate and unsolicited responses
   must not establish fresh operational readings. Startup buffers must not revive state.
4. Verify real cycles on Raspberry Pi 1 against simultaneous GUI and physical readings.
   USB disconnect testing verifies telemetry, not relay fail-safe behavior.
5. Only then switch to polling, add stable semantic aliases and verify Grafana.
   Do not scrape web and USB using interchangeable jobs with identical device IDs/aliases
   without a controlled migration.

## Operation

- Reception/polling → Stream → validation → independently timestamped blocks → snapshot.
  HTTP scraping never performs USB I/O. Stale blocks (default 10 s) do not become zero readings.
- `/metrics`: diagnostic slots, block validity, reception age, frame counts,
  protocol errors and reconnects. The protocol version is unknown and must not be invented.
  Build info is exported; before production, separate length/checksum/timeout counters.
- JSON state changes on stdout → journald. Remote log forwarding is a separate
  integration; current Alloy binaries do not support Pi 1 ARMv6. Do not log every temperature
  sample. The current scaffold combines checksum errors with other protocol errors.
- The passive unit uses `Type=exec` and no telemetry watchdog. `/healthz` checks
  worker responsiveness, independently from block availability. A future polling
  watchdog must follow complete correlated request/response cycles, not merely
  process liveness. Reconnect uses 5 s backoff and invalidates old readings;
  automatic crash restarts are bounded by the start limit.
- An SBC hardware watchdog is optional operational supervision, not a boiler safety
  mechanism. No actuator, TEST, time-setting, firmware or configuration commands.
- Add MQTT after semantic verification, using an independent worker so broker outages cannot block USB.

## Dallas DS18B20 — subsequent module

Proposed independent Linux `w1_therm` reads through sysfs, three-wire connection,
3.3 V and typically a 4k7 pull-up. Final GPIO, power, wiring and DT overlay depend
on the actual board. Cable runs of several metres require reliability checks.
Map sensors by ROM ID to ATMOS outlet, storage top, gas boiler outlet, Z1/Z2 outlet,
not by discovery order.

Validate kernel CRC YES, range, disconnection and the time of the last completed
conversion. Do not automatically accept 85 °C after startup as a real temperature;
confirm through subsequent conversions and mark unavailable while uncertain.
Do not permanently suppress genuine 85 °C readings. A missing sensor means missing
data, never a last value marked fresh. Use separate source metrics and JER/Dallas
difference diagnostics, not averaged readings. DS18B20 is not a flue sensor.
This module is designed but not implemented.

## Interface documentation

- [pySerial API](https://pyserial.readthedocs.io/en/stable/pyserial_api.html): timeout/exclusive.
- [systemd.service](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html)
  and [sd_notify](https://www.freedesktop.org/software/systemd/man/257/sd_notify.html): READY/watchdog.
- [Linux w1_therm](https://cdn.kernel.org/doc/html/latest/w1/slaves/w1_therm.html): CRC/conversions.
