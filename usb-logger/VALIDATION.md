# Validation

## Raspberry Pi 1 adaptation (0.1.1)

- Local Python 3.12 tests: **25 passed, 3 skipped**. This includes real loopback
  HTTP health/metrics, configuration validation, streaming replay, bounded serial
  reads, transport cleanup, stale/invalidated samples and bundle privacy checks.
- Three POSIX-only symlink/deployment lifecycle cases (including failed-update
  rollback) are skipped on Windows and configured to run in Linux CI.
- Runtime/deployment sources parse with Python 3.11 syntax rules. This is a syntax
  check, not execution under Python 3.11. CI now targets 3.11, 3.12 and 3.13;
  those GitHub jobs have not been run from this environment.
- `sh` wrapper syntax was checked with Git Bash `-n`; it did not run installation.
  `install.py --check` was exercised locally without opening a serial port.
- No Raspberry Pi, ARMv6 execution, apt installation or real systemd deployment
  was available. The compatibility review relies on official OS/package support
  plus the source/test checks above. Hardware acceptance remains necessary.
- The deployment artifact excludes private captures, reports and web code.

## Earlier protocol and web verification

- Offline decoder: 5718 USBPcap packets → 1553 protocol frames,
  checksum 1553/1553, zero unprocessed stream bytes.
- USB tests cover fragmentation at 1/2/3/7/64/187/259/512 bytes, coalesced frames,
  checksum corruption, invalid lengths/types, sentinel/service values and independent
  block expiry. The local test also checks the entire original capture. In CI without
  the private capture, only that test is skipped; sanitized fixture tests still run.
- Relocated web project: 96 passed, 2 skipped. Tests requiring disposable
  Prometheus/MQTT containers were skipped. Local HTTP socket tests passed after
  approved execution outside the sandbox network restriction.
- GitHub Actions uses the updated working directory for web Docker builds and
  a separate USB offline job. Docker builds and CI were not run here.
- Live JER, NanoPi, serial transport, systemd and Alloy have not been verified here.
  No device was opened or controlled.

Raw logs and detailed decoded reports are ignored locally by Git; do not move
them to a public server. The generated summary and maps can be reproduced using
the README commands. Original evidence retains its source language.
