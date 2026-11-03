# TELEX application protocol v1

All multibyte fields are little-endian. ESP-NOW payload is one complete packet,
maximum 250 bytes. USB transports COBS(packet) followed by 00; logs must be
separate typed packets. Legacy USB/radio is a separate codec, never COBS wrapped.

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 2 | ASCII TX |
| 2 | 1 | version=1 |
| 3 | 1 | kind |
| 4 | 1 | flags=0 (reserved) |
| 5 | 1 | source role: unknown0, left1, right2, receiver3 |
| 6 | 1 | target role (same values) |
| 7 | 1 | reserved=0 |
| 8 | 4 | session ID |
| 12 | 4 | sequence, per message stream |
| 16 | 4 | monotonic milliseconds modulo 2^32 |
| 20 | 2 | payload length 0..224 |
| 22 | 2 | reserved=0 |
| 24 | N | payload |
| 24+N | 2 | CRC16-CCITT-FALSE over header and payload |

CRC polynomial 1021, init FFFF, no reflection, xorout 0. Check value for
ASCII 123456789 is 29B1. CRC is stored little-endian. Reject trailing bytes,
unknown versions/types/roles, nonzero reserved fields, length mismatch and CRC.

Kinds: 01 identity, 02 capabilities, 10 IMU, 11 status, 20 haptic start,
21 target update, 22 stop, 23 finite action, 30 calibration command,
31 calibration event, 40 clock probe, 7E application acknowledgment, 7F error.
An ACK is not a measurement of mechanical response.

The 168-byte new pose body yields a 194-byte packet. Its detailed payload
layout is owned by the IMU codec. An absent field must carry validity metadata.
Fresh read and actual new sensor sample are different facts.

Frozen vectors.json was generated independently with Python stdlib struct
and binascii.crc_hqx before either production codec. The native test harness
roundtrips each vector; Python additionally compares encoded bytes exactly.

## Control sessions and clock exchange

START (20) body: boot nonce uint64. Session IDs are nonzero and strictly
newer (modulo 2^32 half-range ordering) than the device high-water mark.
STOP (22) has an empty body; ID0 stops any current session. A valid STOP
also tombstones a not-yet-delivered START for that ID. STOP is independent
per destination; two hands do not share a single stop queue slot.
UPDATE (21) body: five uint8 levels, little/ring/middle/index/thumb. The
header timestamp denotes the source sample's acquisition/request time,
NOT the time a receiver forwarded it. Levels <=60 and sum(level^2)<=7200.
Finite action (23): mask:uint8, preset:uint8 (0..3), strength:uint8 (<=40),
duration_ms:uint16 (1..1000). It does not renew the continuous target lease.
ACK (7E): original kind:uint8, state:uint8 (accepted0/applied1/rejected2/unknown3),
reason:uint8; echoes session ID and request sequence. Driver application
results will be supplied by the playback service, not by MAC callbacks.

Clock (40) uses uint64 microseconds in its body: request t0 (8B), response
t0/t1/t2 (24B), then confirmation t0/t1/t2/t3 (32B). The device only accepts
confirmation of its pending tuple and within150ms of t2. Estimates expire
in5s. Offset intervals, not an unqualified offset point, determine maximum
source age. Host must refresh synchronization before controlling actuators.
Timestamp_ms is unwrapped near estimated remote time; future or ambiguous
half-range values are rejected. Receiver forwards the original packet.

### Playback integration details

STOP (0x22): empty payload = emergency stop, no cue. A single zero byte explicitly requests normal end (three bounded cues); invalid lengths/values rejected. Retries never restart a cue. START remains 8-byte boot nonce. Missing motor channels appear in status; selected present channels may be tested, but absent channels never count as healthy. Cues cover the available mask and are explicitly partial if any channel is missing.

Timestamp freshness includes a conservative 1ms timestamp quantization margin and a 200ppm drift allowance within a 5s clock validity window. Hardware timing/clock accuracy still needs physical verification.

### Configuration and calibration completion

0x03 CONFIG is a direct-USB-only 96-byte TXCF settings record. UID must match hardware; known role required; S3 permits one receiver peer, C6 up to two hand peers. Device stops playback, writes alternate NVS slot, commits and reads back before applied ACK, then restarts. Host must re-query identity/configuration after restart. Legacy mode configuration changes use the download workflow.

TXCF remaining fields: 77 trigger preset0..3,78 mask (little..thumb),79 strength,80..81 duration_ms LE,82 key active level,83 key timing/polarity verified boolean,84..85 debounce_ms LE,86..87 long_ms LE;88..93 reserved zero. Zero optional settings normalize to defaults mask31,strength24,duration60ms,debounce30ms,long600ms,unverified active-high. Values are validated against the same haptic budget. This expands unreleased schema1 before publication; no prior deployed TELEX-v1 configs exist.

0x30 CAL request: operation1 STATUS/3 STOP/4 ABORT = one byte. Operation2 START = operation byte plus current boot nonce uint64 LE, a fresh timestamp and nonzero newer session. Sequence fits uint16. Duplicate START reports accepted/duplicate and is not queued again; query STATUS for eventual device state. New START cannot replay after STOP. 0x31 payload is the 87-byte good event followed by switch_ok,save_ok,effect_verified (always0 until independently measured).

0x02 capabilities and0x11 status are compact JSON; empty payload queries them. Identity has uid,role,chip,fw,boot,last_session,configured,haptics. Capabilities additionally exposes mode,imu,rate,motors,cal,rion,key_verified,last_cal,diag. Status includes imu_mask,faults,mv,mv_valid,active,cal,inhibit,dropped,groups,triggers,trigger_result. Unknown SOC remains unknown. Firmware counters are monotonic since boot; group count measures delivered acquisition snapshots, not proven sensor internal refresh rate.

CONFIG applied ACK is99 payload bytes: kind3,state1,reason0 followed by the96-byte TXCF record actually loaded from committed NVS. Host compares UID,config,suite,peers,trigger; generation is assigned by the device. This permits exact persistent readback even when the requested legacy mode stops speaking the new protocol after reboot.

## Workbench integration additions (prerelease 0.1)

START0x20 retains8-byte boot nonce for normal tactile linkage. Normal linkage starts silently and may retain the existing normal-end cue; emergency STOP remains silent. Optional ninth byte1 opens a finite-test-only session: no session cues, no continuous UPDATE; host tests limited to200ms within the unchanged300ms lease. Ninth byte0 is normal; purpose2 is the non-actuating prewarm extension described below; other values are rejected.

DIAGNOSTIC0x41 echoes0..32 payload bytes with identical session/sequence only in new mode with diagnostics enabled. It never renews a motor lease or acts as a haptic keepalive. Workbench sends10 isolated16-byte probes, reporting application roundtrip/confirmed counts separately from physical/RF acceptance.

Host command clocks use the original source acquisition bound; normal stop retries never restart end cues. A missing execution ACK is unknown, not mechanical zero.

## Non-actuating preparation (capability prewarm=true)

Before START, host may send kind0x20 with the same nonzero proposed session and payload nonce-u64 + purpose2. Only stopped/inactive/uninhibited hardware accepts it, with normal nonce, session-tombstone and150ms arrival-age checks. It prepares profiles/RAM without GO, cues, owner acquisition or lease creation; a cloned session validates it without advancing the actual session. ACK may take up to1s at host. Unused preparation powers off after1s. STOP still cuts power and tombstones the proposed session. The subsequent fresh START (purpose0/1) and every vibration command retain150ms freshness and300ms lease. Older firmware without prewarm capability uses the unchanged handshake. Receiver forwards this existing kind without interpretation.
