# Codex brief: Thematronic JER-1 — USB protocol, local exporter and preparation for safe actuator control

English translation of the user-supplied brief from another chat. Its claims are
background hypotheses, not independently verified findings. The original is retained
locally at `log/ASSIGNMENT.original.cs.md`. For actual findings, see `ANALYSIS.md`.

## Task

Analyze the supplied **new USB communication capture** between the original Windows
**JBR1 Terminal** application and the **Thematronic JER-1** controller in this installation.
Also use earlier PCAPs, hexadecimal text dumps, GUI screenshots, the manual and brochure
if supplied. Reproduce **read-only USB communication** on a small Linux SBC (Orange Pi /
NanoPi with Ethernet), obtain **temperatures, booleans and states**, and build standalone
local **Prometheus** telemetry. A separate later phase, after safety verification, may
allow service intervention in mixing-valve actuators during gas-boiler operation.

**Start with thorough log analysis, not premature control-command implementation.**
Distinguish `CONFIRMED` (direct evidence), `LIKELY` (strong hypothesis), `UNKNOWN`
(unverified) and `DISPROVED`. Correct the map and explain any new evidence that contradicts prior conclusions.

## Environment and files

- JER-1 controls an ATMOS wood boiler, a backup gas boiler (boiler 2), one thermal
  storage tank and two mixed heating zones with separate room thermostats, pumps and actuators.
- Its USB port connects to the Windows JBR1 Terminal application, which displays
  temperatures, inputs, outputs and diagnostics and can operate relays in service
  mode. The user previously tested actuator direction through the terminal.
- Windows capture uses Wireshark + USBPcap. Earlier files were `JBR1_catch.pcapng`,
  the timestamped pasted-text export from 2026-10-21 15:20:12 and screenshot
  `image(20261021-151948).png`. **The new capture takes precedence.** Do not assume
  earlier files are available: explicitly request missing ones and continue with available evidence.
- Manufacturer documents: `navod_jer1_cz_ver9 (1).pdf` (old manual dated 5 November
  2014 for firmware 3.0.10) and `prospekt_jer1.pdf`. Current firmware may differ.
  The manual describes manual-relay `TEST`, PT1/PT2 inputs, SMO1-O/SMO1-Z and
  SMO2-O/SMO2-Z actuator outputs, temperature sensors and safety modes.
- Existing `kotel-logger` on the local Linux server reads the **web API**
  `jbr1.thematronic.cz`, exports Prometheus metrics and publishes MQTT/Home Assistant
  data. USB should eventually remove web dependence without breaking dashboards.
  Use actual metric names from the repository/README rather than inventing verified names.
- Thematronic server: `82.202.36.136`; network controller: `172.30.1.144`; a separate
  TCP protocol was observed on port `5555`. **USB and network protocols may differ.**
  Verify rather than assume. A TCP PCAP was being collected on OpenWrt `ubnt-jank` / `br-lan`.
- Local infrastructure: Prometheus, Grafana and Loki; Grafana Alloy collects journald and JSON logs.

## Previous USB findings — hypotheses to verify

The earlier hex export repeats four-byte entries:

```text
21 00 0E 01  -> 0x010E = 270 -> 27.0 °C
21 00 68 01  -> 0x0168 = 360 -> 36.0 °C
21 00 AE 00  -> 0x00AE = 174 -> 17.4 °C
21 00 35 02  -> 0x0235 = 565 -> 56.5 °C
22 00 00 00  -> probably boolean FALSE
22 00 01 00  -> probably boolean TRUE
41 00 0B FE  -> probably absent/invalid sensor
00 00 00 00  -> empty or unused slot, NOT automatically boolean FALSE
```

Working hypothesis: the first two bytes identify the value type (`21 00`, `22 00`,
`41 00`), the 16-bit value is **little-endian**, temperatures use tenths of a degree,
and semantics follow the slot's **position within its particular block**, not a
unique four-byte hex ID. Confirm or disprove tags, signed/negative temperatures,
sentinels and block lengths.

The first temperature block had this **provisional map from GUI ordering and value
matches, not a definitive register map**:

| Zero-based index | Example entry | Proposed meaning |
|---:|---|---|
| 0 | `41 00 0B FE` | Uninstalled DHW sensor |
| 1 | `21 00 0E 01` | Zone 1 outlet, 27.0 °C |
| 2 | `21 00 68 01` | Zone 2 outlet, 36.0 °C |
| 3 | `21 00 AE 00` | Outdoor temperature, 17.4 °C |
| 4 | `21 00 D1 00` | Approximately 20.9 °C; frost-protection label uncertain |
| 5 | `21 00 D7 00` | Boiler outlet, 21.5 °C |
| 6 | `21 00 C9 00` | Boiler return, 20.1 °C |
| 7 | `21 00 35 02` | Storage top, 56.5 °C |
| 8 | `21 00 77 01` | Storage middle, 37.5 °C |
| 9 | `21 00 4C 01` | Storage bottom, 33.2 °C |

Other parts contain `21 00 5E 01`, `21 00 A4 01`, `21 00 09 02`, many
`22 00 xx 00` values and unknown/reserved entries. The complete map is **unconfirmed**.
An earlier screenshot showed zone 2 room thermostat and pump active. Distinguish
the **room thermostat**, internal **zone demand**, **zone pump relay command** and
physical flow; they are not synonyms.

Earlier USBPcap inspection provisionally identified bulk endpoints `0x05` and `0x82`,
repeated 23-byte outgoing frames and command types `0x13`/`0x0C`. Reverify endpoint
direction and contents. Observed data included header `28 93`, text `RX ping`,
`TX ping`, `WIFI CONNECTED`, `WIFI GOT IP`, and these examples:

```text
28 93 00 17 13 0A E0 FF 02 42 A4 1A ...
28 93 00 17 0C 0A E0 FF 02 42 A4 1A ...
28 93 00 BB 14 0A E0 FF 02 42 A4 1A ... [telemetry] ...
28 93 01 03 0D 0A E0 FF 02 42 A4 1A ... [telemetry] ...
```

**Do not assume `00 17` is a length without verifying complete frames.** Investigate
lengths, TCP/USB wrapping, identity, CRC/checksum and separation of USBPcap/URB
metadata from actual protocol bytes. Do not repeat unverified counts such as
“305 outgoing messages” without independently inspecting the PCAP.

Earlier TCP server↔controller communication on port 5555 contained 23-byte frames:

```text
server:     28 93 00 17 01 0A E0 FF 02 42 A4 1A AF AB A6 B2 59 C1 20 00 F5 F7 60
controller: 28 93 00 17 02 0A E0 FF 02 42 A4 1A AF AB A6 B2 59 C1 20 00 F5 F7 5F
```

`01→02` may select message type and `60→5F` may be checksum-related. **This is a
hypothesis, not proof.** Bidirectional traffic on 5555 was visible while the web
service on port 443 was experiencing outages.

## Primary task: forensic analysis of the new USB log

1. Identify each file's type and integrity using tshark, capinfos, pyshark/scapy
   or a custom PCAPNG parser. Document any required dependency installation.
   Do not upload sensitive captures to network services.
2. Identify the USB device by VID/PID, address, endpoints and descriptors; determine
   CDC/USB-serial/FTDI/CH340/HID or proprietary bulk. State if enumeration is absent.
   Select relevant device traffic and distinguish URB submit/complete from payload
   bytes so transfers are not duplicated.
3. Reconstruct host→device and device→host timelines, frame counts, read intervals,
   lengths, repeats, request/response pairing, connection startup/shutdown, ping and
   service messages. Distinguish USB packet, stream and logical frame boundaries.
4. Formalize `28 93` framing, length, type, ID, payload and checksum/CRC. Verify
   hypotheses on **all** relevant frames, not one example. Prove the checksum
   algorithm if possible and report its match rate. Do not send unknown commands live.
5. Decode temperature and boolean fields. Create `field_map.csv` / `.yaml` with
   block, byte offset, index, type, decoding, unit, readable GUI name, reference web
   metric, confidence and evidence source. Mark verification as `confirmed_by_value`,
   `confirmed_by_transition`, `inferred_by_order` or `unknown`.
6. **Differential analysis:** compare snapshots over time and new logs following
   controlled changes of an **ordinary room thermostat**, e.g. PT2 ON→OFF→ON.
   Report exactly which offsets changed. PT2 may change simultaneously with its
   pump, zone demand and internal states; distinguish correlation from causal identification.
7. Compare GUI/web values using **measurement timestamps**, accounting for web
   latency/outages. Do not infer matches from unsynchronized snapshots. Use the
   existing logger's named data model only when actual definitions are available.
8. Inventory all unknown commands/types without actively transmitting them.
   Isolate relay experiments from regular polling and identify their effects.
   **Do not send actuator commands to the live boiler.**

## Read-only Linux SBC service architecture

- `usb_transport`: open USB, receive data, timeouts, reconnect and explicit read-only
  policy. If telemetry queries must be sent, whitelist only empirically confirmed
  requests. No installation configuration, firmware, service TEST or output writes.
- `protocol`: pure parser with real-sample unit tests, length/checksum/sentinel
  validation, rejection of false values and stale-data detection.
- `metrics`: Prometheus `/metrics` with temperatures, states, time since last
  successful read, errors, CRC, reconnects and protocol version. Preserve dashboard
  compatibility where established; never hide missing readings behind zero values.
- `log`: structured JSON to journald and Alloy/Loki. Ordinary temperatures belong
  in Prometheus, not repetitive log entries every few seconds.
- systemd unit, healthchecks and watchdog: heartbeat only after an actually working
  cycle. An SBC hardware watchdog is optional; reboot does not guarantee safe release of JER control.
- Several external Dallas DS18B20 sensors, a few metres of three-wire cable, and
  a 4k7 pull-up to 3.3 V subject to the actual SBC. Proposed locations: ATMOS outlet,
  storage top, gas boiler outlet and Z1/Z2 outlets. Do not use ordinary DS18B20 for
  flue gas; use a suitable high-temperature sensor or JER's reading. Handle CRC,
  startup 85 °C, disconnection, sensor differences and timestamps.
- Optional MQTT/Home Assistant compatibility only after the read-only parser.
- **Use only actually available files and devices.** Without a physically connected
  JER, create an offline replay/test harness and a detailed future SBC procedure.
  Do not claim live communication works without testing it.

## Future actuator control — safety design only, no first-phase actuator implementation

Goal: only when ATMOS is **demonstrably not burning and residual heat does not need
removal**, the gas boiler is actually heating and a zone demands heat, a future system
may open **only that zone's actuator**, or both if both demand heat, after independent
safety authorization. Use PT1/PT2 primarily, cross-checking pump states; internal
zone demand is not guaranteed equivalent to PT. Gas-boiler outlet and zone outlet
temperatures are **diagnostic indications** of opening, not direct valve-position measurements.

Conceptual conditions, **not approved safety limits**: flue temperature below 60 °C,
ATMOS outlet below 40 °C, storage top below 40 °C, relevant burning/running flags,
boiler pumps/fan, overheat and protection modes inactive. JER and independent Dallas
temperatures must agree. UNKNOWN/stale means blocked. Permit intervention only after
conditions remain stable; any known risk requires immediate return of control to JER.
Boiler safety protections have absolute priority.

**Critical unverified point:** the user suspects JER resumes automatic relay control
shortly after a manual terminal action, perhaps after 15 seconds. This is unconfirmed.
Determine OPEN, CLOSE, STOP, AUTO and service TEST behavior, internal timeouts, and
responses to USB disconnection, application failure, SBC reboot and ATMOS firing.
Until **return to AUTO can be independently guaranteed without a functioning SBC**,
do not recommend live software actuator control. Otherwise retain or return to a
hardware fail-safe design (ATtiny/relays defaulting physically to JER control), with
qualified electrical/hydraulic review. Do not automate physical-output tests or inject PCAP commands.

The JER manual describes emergency cooling and TEST. Service overrides must not
bypass internal safety functions. A systemd watchdog or hardware reboot alone is not fail-safe.

## Expected outputs

1. `ANALYSIS.md`: evidence, transport, framing, transitions, conflicting hypotheses,
   safety uncertainties and precise packet/frame/time references.
2. `field_map.csv` and `.yaml`: each quantity, confidence and verification.
3. `protocol.md`: frame format, request/response examples, endian order, types,
   checksum and limitations.
4. `tools/analyze_capture.py`: repeatable offline decoder/diff for new PCAPNG or
   exported raw bytes, optional timestamps and CSV/JSON output, without live writes.
5. `src/` and `tests/`: scaffold or working read-only Python client/exporter according
   to verified knowledge; safely sanitized real-capture fixtures and unit tests.
6. `deploy/`: systemd unit, Alloy journald/Loki example and Prometheus scrape
   configuration, clearly marked as templates requiring local IP/port changes.
7. `NEXT_CAPTURE_PLAN.md`: prioritized GUI/thermostat experiments to identify
   specific unknown fields, what to record, what is safe and what not to attempt live.

## First-phase success criteria

- The offline decoder consistently reads known temperatures, decimal values,
  missing values and selected states from the new log.
- New and old frames have explained structure, or remaining unknowns are documented precisely.
- PT1/PT2, Z1/Z2 pumps, ATMOS running/burning, gas and overheat fields must not be
  CONFIRMED solely from GUI ordering or a single value match.
- Read-only operation: no JER configuration changes, real actuator control,
  automated write tests or unsupported claims of guaranteed fail-safe behavior.
- Finish with a short decision: what is readable, what is mapped with high confidence,
  which evidence is missing and the next practical step.

Work iteratively and document commands/findings so another developer can continue the project.
