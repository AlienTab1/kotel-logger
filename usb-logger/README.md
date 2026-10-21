# JER-1 USB logger — initial analysis phase

Working offline PCAPNG decoder and diagnostic exporter. Live polling is not yet
implemented or verified; passive serial reception may not trigger any responses.
No code sends application commands, configuration or relay commands.

Read [ANALYSIS.md](ANALYSIS.md), [protocol.md](protocol.md),
[SERVICE_DESIGN.md](SERVICE_DESIGN.md) and [NEXT_CAPTURE_PLAN.md](NEXT_CAPTURE_PLAN.md).

From the USB project directory, using Python 3.11+:

```sh
python tools/analyze_capture.py log/capture01/capture01.pcapng --output reports
python tools/build_field_map.py
python -m pip install -e '.[test]'
python -m pytest tests -q
jer-usb-logger --replay reports/frames.jsonl
```

Replay exposes `/metrics` at `127.0.0.1:9109`, preserves response intervals,
does not repeat at EOF, and allows readings to become stale. Replay is not live telemetry.
JSON reports contain the original device identifier; sanitize them before sharing.
The identity in `tests/fixtures.json` is zeroed and its checksum recalculated.
The map covers 100 slots, including reserved slots. Names are conservative hypotheses;
only `jer_usb_*` diagnostic slot metrics are exported, not unverified web aliases.
GUI names in the map are English translations of the original terminal labels.

For a future passive Linux test, install the `.[serial]` extra and use
`--passive-port /dev/serial/by-id/…`. Opening the port configures the CDC link;
this is not a USB sniffer, and the port cannot be shared with the original terminal.
Offline analysis uses the standard library; no dependencies needed installation.
Tshark/capinfos were unavailable in the development environment.

## Raspberry Pi 1 deployment

Use [INSTALL_RASPBERRY_PI.md](INSTALL_RASPBERRY_PI.md) for the guided installer,
updates and rollback. [RASPBERRY_PI_COMPATIBILITY.md](RASPBERRY_PI_COMPATIBILITY.md)
reviews ARMv6, 256/512 MB RAM, Python versions and remaining protocol gaps.
The Pi installation uses OS Python and pySerial packages; the pip commands above
are for development. `/healthz` is worker health, not proof of available telemetry.
