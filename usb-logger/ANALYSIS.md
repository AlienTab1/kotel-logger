# capture01 analysis — 21 October 2026

## Decision

Offline reconstruction and validation work for all frames. Two tables (59 + 41
slots), missing values and standard booleans can be decoded. Ten temperature
slots strongly match the GUI ordering and values. Live USB reads on NanoPi remain unverified.
The semantics of thermostats, relays and protection states are not CONFIRMED.
Do not implement actuator commands. The next step is a clean connection/read capture without TEST.

## Inputs and reproducibility

Supplied: `log/capture01/capture01.pcapng` (565176 B), terminal text (468127 B),
and three PNGs (108744 / 117997 / 116211 B). Visual inspection of the PNGs shows
scheme 521, COM6, and different relay/PT2 states. The text has no timestamps or
protocol headers; it is supporting evidence and cannot be aligned precisely to
the PCAP. Screenshots also lack established measurement times. The brief from
another chat is translated in `ASSIGNMENT.md`; its hypotheses are not measurements.
The original brief is retained locally under `log/ASSIGNMENT.original.cs.md`.

PCAP SHA256: `37b8afb61542738b62fe4435b0e61ae829db547c2d273ce7b667fdcd5a313d58`.
5718 enhanced packets; 455.829728 s; descriptor enumeration is present.
The custom parser checks blocks, duplicated lengths, captured lengths and USB payload lengths.
There are **1553 logical frames**, all with valid checksums, and no pending stream bytes.
From the USB directory, run:
`python tools/analyze_capture.py log/capture01/capture01.pcapng --output reports`,
then `python tools/build_field_map.py`. No network upload or installation was required.

The earlier `JBR1_catch.pcapng`, earlier hex export, original screenshot, manual,
brochure and TCP PCAP were not supplied. They are needed for comparison;
the current analysis does not depend on them or claim to have verified the manual.

## Timeline and provenance

`relative_s` is measured from the PCAP start, not application startup. PCAP timestamps
represent transfers, not guaranteed physical measurement times.

- Packets 22/26/36: USB identity, CDC descriptors and 115200/8N1.
- Packet 51, t=132.888309: several messages in one incoming transfer before the
  first OUT. The initial temperature table has Z1=26.6 and storage top=55.4;
  the next has Z1=39.2 and storage top=48.9. Older buffered/cached data is LIKELY;
  the exact cause is UNKNOWN. Receiving a valid frame alone does not prove measurement freshness.
- 61→63, approximately t=132.901→132.904: OUT 03 → IN 04, `TAB1` identification.
- 65→67, t=133.310948→133.312744: OUT 0c → IN 0d.
- 69→71, t=133.748281→133.750602: OUT 13 → IN 14.
- Each block repeats at a median interval of approximately 0.846 s, alternating
  at approximately 0.42 s. Service blocks 0b/12 also receive telemetry responses,
  so response counts differ from short-request counts.
- OUT 0b starts at packet 1041, t=189.057997; 79 messages through 4997, t=413.768150.
  OUT 12 runs from 1049, t=189.477097 to 4989, t=413.334453 (78 messages).
  These are the complete sets of long OUT messages, not one continuous TEST interval.
- Last regular 0d/14 responses end at 5695/5699, t=449.640/450.087.
  USB shutdown also contains error/cancel URBs, which are not treated as data.

All logical frames: `reports/frames.jsonl` with packet_start/end and timestamp.
All slot changes: `reports/transitions.csv`. Summary: `reports/summary.json`.
Unknown IN types 00/02/04 and long OUT types 0b/12 are inventoried in the protocol document.

## Differential evidence

| Block / index / offset | Change | Packet / time | Finding |
|---|---|---|---|
| 0d / 35 / 161 | 0022:0 → 0122:0 | 1047 / 189.061 | Response after OUT 0b 1041; LIKELY TEST flag |
| 0d / 35 / 161 | 0122:0 → 0022:10 → 0022:0 → 0122:10 | 1387,1399,1411 / 211.977,212.843,213.727 | Ordinary boolean DISPROVED; LIKELY fan based on GUI |
| 0d / 36 / 165 | 0022:0 → 0122:0 | 2699 / 279.477 | LIKELY service flag for second relay; meaning unconfirmed |
| 0d / 42 / 189 | 0 → 1 | 2267 / 258.200 | Candidate actuator 1 direction; no proof of physical movement |
| 14 / 1 / 25 | 0021:350 → 0000:0 | 4733 / 396.339 | Candidate Z2 water target, missing after shutdown |
| 14 / 12 / 69 | 0021:420 → 0021:0 | 4733 / 396.339 | Candidate internal Z2 demand |
| 0d / 26 / 125 | 1 → 0 | 4737 / 396.765 | Candidate PT2 |
| 0d / 38 / 173 | 1 → 0 | 4737 / 396.765 | Candidate Z2 pump, identical correlation with PT2 |
| 0d / 44 / 197 | 1 → 0 | 4737 / 396.765 | Another correlated output; candidate Z2 actuator direction |

The final group matches the difference between terminal01/02 and terminal03:
PT2 and the Z2 pump are off, and Z2 demand is zero. This supports a shared event,
but does not independently identify each bit. The user reports Z1 demand changes;
after the initial table, the data instead shows stable candidate PT1=1 and internal
Z1=41.0. A Z1 transition cannot be established in this PCAP. According to the GUI,
the observed late transition corresponds to Z2.

GUI ordering suggests fan relay index 35. Screenshot terminal02 displays 1, while
the service-mode capture contains raw 10 and 0122. The exact GUI↔raw conversion
and independently timed ON/OFF transitions are unproven. These data do not establish
a return to AUTO after 15 seconds. The second changed output, index 36, needs
clarification: was it also tested manually?

## Map and limitations

`field_map.csv`/`.yaml` cover all 100 slots, offsets relative to magic, tags,
conservative English translations of GUI names, existing web temperature signal
names, units, confidence and evidence sources. `confirmed_by_value` describes
a value match; it does not imply timestamp-synchronized CONFIRMED physical identification.
No protection state, thermostat or pump is CONFIRMED from ordering alone.

Existing project metrics are `kotel_temperature_celsius{device_id,signal}` and
`kotel_signal_state{device_id,signal}`. The USB exporter currently publishes only
slot diagnostics, so hypotheses are not presented as operational aliases.
The web alias map establishes that names exist, not that the sources are synchronized.
Scheme number in block 14/index 20 is raw 521; dividing all 0021 values by ten
does not produce correct physical semantics. Exported slots use a diagnostic conversion.

## First-phase safety boundary

No live JER connection, USB writes or relay control were performed.
TEST, AUTO and disconnect timeout behavior remain UNKNOWN. A systemd restart does
not guarantee transfer of control. Do not recommend live software actuator control
without an independently verified return to AUTO. Future control requires a separate
safety design and qualified review; thresholds from the brief are not approved limits.
