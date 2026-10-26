# Tegu-compatible EP0 diagnostic protocol (Laguna profile)

This is the exact control-request ABI used by the Tegu host tools. It uses only
EP0: there are no bulk/interrupt endpoints, no CDC/ECM interface, and no iOS USB
stack. All multi-byte fields in the SETUP packet and protocol payloads are
little-endian.

## Identity and enumeration

- VID:PID is `1209:0001` (the pid.codes test ID, not a product assignment).
- Device descriptor: USB 2.0, EP0 max packet 64, one configuration.
- Configuration is 18 bytes: one vendor-specific interface (`class 0xff`),
  zero endpoints, bus-powered, `bMaxPower = 250` (500 mA).
- Product string is deliberately **`Tegu USB diagnostic`** so the existing tools
  accept the gadget without edits. Laguna's manufacturer string is `ios-laguna`;
  serial is `0001`; language 0 is en-US (`0x0409`).
- Standard handlers are GET_DESCRIPTOR (device/config/string), SET_ADDRESS,
  SET/GET_CONFIGURATION, GET_STATUS and SET_INTERFACE. Other request types stall.

`0xc0` is a device-to-host vendor/device request; `0x40` is host-to-device
vendor/device. In the tables below, **offset32** always means
`wValue | (wIndex << 16)`: wValue is bits 0..15 and wIndex bits 16..31 of a byte
offset, not a transfer length or endpoint index.

Only one host client should own EP0. Stop `usb_log.py` before reboot, hotfix,
pull, or stamping tools. Input writes and RAM stamps are not retried after an
ambiguous USB error.

## Requests used by the minimal Laguna gadget

| bmRequestType / bRequest | Fields and data stage | Meaning / exact response |
|---|---|---|
| `0xc0 / 0x5a` | offset32; `wLength > 0` | Read at most `min(wLength, 512)` log bytes at offset32. Return a short read at the current end and zero bytes at/past the end. |
| `0x40 / 0x5b` | `wValue=0`, `wIndex=0`, 1..64 bytes OUT | Queue one input packet atomically. The queue is 256 bytes; oversize or insufficient free space stalls. The DWC3 OUT residual must prove exactly `wLength` bytes arrived before any byte is committed. |
| `0x40 / 0x5c` | `wValue=0x5447`, `wIndex=1`, `wLength=0` | Request reboot. Bad magic/version, a missing handler, or a second request stalls. The callback runs only after the control status stage completes (therefore after the host ACK), never at SETUP. |
| `0xc0 / 0x5d` | offset32; `wLength > 0` | Read at most `min(wLength, 512)` bytes from the TFB1 stream described below. No source/invalid geometry/end of stream returns zero bytes. |
| `0x40 / 0x5e` | `wValue=slot 0..7`, `wIndex=0x4c47`, exactly 8 bytes OUT | **Laguna extension:** write the little-endian u64 to the configured RAM stamp array at `slot`. No address is supplied by the host. The write and barrier complete before status ACK. No sink, bad magic/slot/length stalls. This is volatile and not a pstore/write-to-disk operation. |

Unsupported requests stall; they do not silently succeed.

### Log offsets and capacity

The first bytes are always `TEGU USB: log32 1048576\n`. The stock
`usb_log.py` initially assumes 65,536 bytes, upgrades to 1,048,576 when it sees
that marker, and splits offsets above 65,535 into wIndex as expected.

The Laguna implementation is **append-only**, not a circular overwrite ring.
This is intentional: the unmodified reader stores an absolute 32-bit cursor and
cannot resynchronize if old bytes are overwritten. At 1 MiB, later bytes are
dropped and counted; earlier bytes and the capability marker remain readable.
`--tail` works because data remains contiguous to a fixed end.

## TFB1 framebuffer stream (`0x5d`)

The virtual stream is a 32-byte header followed immediately by native rows.
The header is `struct.Struct("<4sHHIIIIII")`:

| Offset | Type | Value |
|---:|---|---|
| 0 | `char[4]` | `TFB1` |
| 4 | u16 | header size = 32 |
| 6 | u16 | version = 1 |
| 8 | u32 | width in pixels |
| 12 | u32 | height in pixels |
| 16 | u32 | `row_bytes` (may include padding) |
| 20 | u32 | pixel format `0x42475241` (`BGRA`) |
| 24 | u32 | payload size = `row_bytes * height` |
| 28 | u32 | flags: bit0 top-down, bit1 BGRA8, bit2 scaled |

Required base flags are `TOP_DOWN | BGRA8 = 3`; scaled streams also set bit2.
The host rejects dimensions above 16,384, short strides, inconsistent payload
sizes, and payloads over 64 MiB. PNG conversion drops row padding and swaps
B/R to produce RGBA8. Presentation is not frozen between reads, so a changing
frame can tear vertically.

For the host's `--scale N` (N = 2..15), the stream base is:

```
base = (1u << 31) | (N << 27)
```

The 32-bit offset has bit31 set, scale in bits 27..30, and the ordinary stream
offset in bits 0..26. The header and pixels are then read at `base` and
`base + 32`, respectively. The scaled view is nearest sampled at
`(x*N, y*N)`, has `width/N` by `height/N` pixels (integer division), packed
`row_bytes = (width/N)*4`, and sets `SCALED` in flags.

## Tegu hotfix protocol (`0x60`..`0x63`) — mapped, not enabled on Laguna shim

The stock `usb_hotfix.py` performs the following sequence. **Do not run it
against the minimal shim:** these requests intentionally stall, and no file or
filesystem operation exists in the Laguna gadget.

1. **BEGIN `0x40/0x60`**, wValue/wIndex zero, exactly 48 bytes OUT:
   `<magic:I, version:I, compressed_bytes:I, file_bytes:I, sha256:32s>`.
   Magic is `0x46484754` (`TGHF`). Low 16 bits of version are 1; high 16 bits
   select a fixed destination (0 TeguMetal, 1 containermanagerd flags,
   2 launchd plist, 3 lsd reseed). SHA-256 covers the uncompressed file.
   Device limits are 32 MiB compressed / 64 MiB raw.
2. Poll **STATUS `0xc0/0x63`**, 16 bytes `<state:I, error:i, received:I, magic:I>`.
   States: 0 idle, 1 allocating, 2 receiving, 3 committing, 4 done, 5 failed.
   Wait for receiving (2), or fail on state 5.
3. **DATA `0x40/0x61`** for each zlib chunk, at most 4096 bytes, strictly in
   order at offset32. wValue/wIndex carry the offset as above. Short writes are
   fatal; there is no ambiguous-write retry.
4. **COMMIT `0x40/0x62`**, no data. It is accepted only when every compressed
   byte arrived. The kernel inflates, verifies SHA-256, writes/fsyncs a sibling,
   reads it back and re-verifies, then renames it. Poll status until done (4)
   or failed (5).

The protocol uses larger OUT data stages in the XNU implementation; that is not
part of the minimal 64-byte-OUT shim profile.

## Tegu pull protocol (`0x64`..`0x67`) — mapped, not enabled on Laguna shim

The stock `usb_pull.py` reads one fixed pipeline-cache directory. The minimal
shim has no filesystem and stalls all of these requests.

- **OPEN `0x40/0x64`**: 64-byte NUL-padded plain filename. Empty name means
  directory listing. Names are 1..63 of `[A-Za-z0-9._-]`, cannot start with `.`,
  and all bytes after the first NUL must be zero. Only idle or failed can open.
- **STATUS `0xc0/0x65`**: 16 bytes `<state:I, error:i, size:I, magic:I>`;
  magic `0x4c504754` (`TGPL`). States: 0 idle, 1 loading, 2 ready, 3 failed,
  4 closing.
- **READ `0xc0/0x66`**: offset32, `1 <= wLength <= 4096`; only ready. Returns
  `min(wLength, size-offset)` and zero at exact EOF; offset beyond EOF or a bad
  state stalls. The host lists first, then opens/reads/closes each listed file.
- **CLOSE `0x40/0x67`**: no data; ready becomes closing and the worker releases
  the loaded buffer before returning idle.

## Source map

The ABI above was derived from `research/tegu-ios/tools/tegu/{usb_log.py,
usb_reboot.py, usb_hotfix.py, usb_pull.py, framebuffer_capture.py}` and the
corresponding kernel patches `0073`, `0088`, `0092`, `0094`, `0160`, `0178`,
`0182`, `0186`, `0191`, `0197`, and `0209`. The shim-side register/TRB state
machine is based on `research/tegu-ios/shim/tegu/usb.c`; protocol tests in this
directory execute the Laguna C implementation rather than merely comparing
this document to the tools.
