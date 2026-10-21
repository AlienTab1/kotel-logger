# JER-1 protocol, capture01

## CONFIRMED: transport and framing

PCAPNG link type 249 (USBPcap), bus 1, address 5. Descriptor packet 22:
VID `0525`, PID `54a7`, bDeviceClass 02. Configuration packet 26: CDC control
interface 0 (02/02/01), CDC data interface 1 (0a), interrupt IN `81`, bulk OUT
`05`, bulk IN `82`, maximum packet size 64 B. This is neither FTDI/CH340 nor HID.
CDC line coding in packets 36/39: `00 c2 01 00 00 00 08` = 115200 baud, 8N1.
The capture does not establish a serial string or Linux by-id path. VID/PID are not unique IDs.

| Offset | Length | Meaning |
|---|---|---|
| 0 | 2 | Magic `28 93` |
| 2 | 2 | Total length including header and checksum, BE |
| 4 | 1 | Message type |
| 5 | 16 | Constant opaque identity/routing field; exact meaning UNKNOWN |
| 21 | N−23 | Payload |
| N−2 | 2 | `(~sum(frame[:-2])) & 0xffff`, stored BE |

The checksum matches all 1553 frames (100%). It is not a CRC. Frame sizes of
23, 31, 36, 37, 53, 187 and 259 B confirm the length field. No discarded or pending stream bytes.
A USB transfer may contain a fragment or multiple frames; URB boundaries do not define frames.
Only successful bulk OUT submissions and IN completions are used; other devices are ignored.

## Types and counts

| Direction | Type | Length | Count | Interpretation |
|---|---|---|---|---|
| OUT | 03 | 23 | 1 | LIKELY echo/identification request |
| IN | 04 | 53 | 1 | Response containing `TAB1`; further version fields UNKNOWN |
| OUT | 0c | 23 | 297 | LIKELY read request for block 0d |
| IN | 0d | 259 | 376 | 59 four-byte slots |
| OUT | 13 | 23 | 297 | LIKELY read request for block 14 |
| IN | 14 | 187 | 377 | 41 four-byte slots |
| OUT | 0b | 259 | 79 | Full transmitted blocks, service-operation risk; never replay |
| OUT | 12 | 187 | 78 | Full transmitted blocks, service-operation risk; never replay |
| IN | 02 | 23 | 1 | UNKNOWN ping/session message |
| IN | 00 | 31/36/37 | 42/2/2 | Text diagnostics |

Actual short requests are in `reports/frames.jsonl`, packets 65 and 69.
Sanitized response examples are in `tests/fixtures.json`; their identities cannot
be used to connect. No general-purpose write or command-replay functions are implemented.

## Values

The payload begins at offset 21; slot i begins at `21+4*i` relative to the magic.
Each slot contains two LE u16 fields: tag and value. `0x0021` matches a number
scaled by /10, but is not always a temperature: scheme 521 has raw 521, not 52.1 °C.
`0x0022` with raw 0/1 is a diagnostic boolean. `0x0041 / 0xfe0b` is a repeated
missing-value combination matching GUI `-x-`. `0x0000 / 0` does not automatically mean false.
`0x0122` and raw 10 in output slots are not ordinary booleans; the decoder rejects
them as unverified. Flag-bit meanings, raw 10, negative temperatures and general
error tags remain UNKNOWN. Observed temperatures are nonnegative; signed decoding is not established.

DISPROVED: the first two bytes of a slot always contain only an immutable type.
DISPROVED: every OUT message is a harmless 23-byte request.
UNKNOWN: whether the identity represents a GUID, authentication or routing;
whether USB and TCP share the complete state machine. No TCP capture was supplied.
