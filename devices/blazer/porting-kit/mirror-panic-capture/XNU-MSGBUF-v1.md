# XNU-MSGBUF v1 — fixed-PA live msgbuf mirror

Lane R4, 2026-10-26. Host-only producer/reader interface. The producer is
`osfmk/arm64/laguna_panicdump.c` on `laguna141-paniccap`; R2 owns the full
RAM-dump reader. This record is additive to PAN1, persistent_ram console data,
and R3's LGLW stage records.

## 1. Physical placement

The whole record is inside the DT ramoops aperture returned by the working
fastboot `kernel.log` staging path:

| Item | Physical address | Offset in 4 MiB `kernel.log` | Size |
|---|---:|---:|---:|
| Header | `0x95440000` | `0x240000` | 64 bytes |
| Byte ring | `0x95440040` | `0x240040` | `0x100000` (1 MiB) |
| End (exclusive) | `0x95540040` | `0x340040` | — |

The offsets are pmsg+`0x40000` and pmsg+`0x40040` respectively. The reserved
area covers XNU `MAX_MSG_BSIZE`; the active ring capacity is the live
`msg_size` (512 KiB by default, up to 1 MiB). It does not overlap PAN1 at
pmsg+`0x30000..0x3203f`, the LGLW slots, the marker scratch, or DIAG.

## 2. Header ABI (all little-endian)

| Offset | Type | Name | Meaning |
|---:|---|---|---|
| `0x00` | `char[16]` | magic | `"XNU-MSGBUF v1"` followed by three NUL bytes |
| `0x10` | `u32` | seq | 1-based source-metadata refresh generation |
| `0x14` | `u32` | flags | flag bits below |
| `0x18` | `u64` | msgbufp_va | live `msgbufp` VA at the last refresh; zero if invalid |
| `0x20` | `u64` | msg_bufc_va | live source-ring VA at the last refresh; zero if invalid |
| `0x28` | `u32` | msg_size | live source-ring size and active mirror capacity |
| `0x2c` | `u32` | msg_bufx | source write index; also advanced by each accepted byte |
| `0x30` | `u64` | total | absolute stream position in the current source-size generation |
| `0x38` | `u32` | tail_off | `(total - tail_len) % msg_size` |
| `0x3c` | `u32` | tail_len | retained byte count, at most `msg_size` |

Flags defined by v1:

| Bit | Name | Meaning |
|---:|---|---|
| `1<<0` | `WRAPPED` | More than 1 MiB has been appended; older bytes were replaced |
| `1<<1` | `PANIC` | A panic rung refreshed the header |
| `1<<2` | `HALT` | A halt/reboot path refreshed the header |
| `1<<3` | `EARLY_REPLAY` | The pre-mirror static msgbuf contents were replayed |
| `1<<4` | `SOURCE_INVALID` | The latest live-source metadata validation failed; fixed ring remains valid |

Unknown flag bits must be ignored for forward compatibility.

## 3. Reading the log

Validate the complete 16-byte magic, nonzero `seq`, `0 < msg_size <= 0x100000`,
`msg_bufx <= msg_size`, `tail_len <= msg_size`, `total >= tail_len`,
`tail_off == (total - tail_len) % msg_size`, and
`msg_bufx % msg_size == total % msg_size`. Seq is not a commit word and one dump
cannot prove its history; it is a nonzero source-metadata generation.

For each output index `i` in `[0, tail_len)`, the oldest-to-newest byte is:

```
ring[(tail_off + i) % msg_size]
```

This yields raw msgbuf bytes, including any embedded NULs. Readers may apply
XNU's usual NUL-skipping text conversion afterward, but must retain byte counts
for the raw record separately.

The producer writes the byte first, then advances `total`, `tail_len`,
`tail_off`, and `msg_bufx` with aligned stores. Therefore `tail_len` never leads
the corresponding accepted byte. `seq` and the live-source VA/size fields are
refreshed after early replay, after OSLOG's optional `msgbuf=n` resize,
periodically, on both panic rungs, and before halt/reboot. If source inspection
fails, the last valid source VA/size fields are retained and `SOURCE_INVALID`
is set; the fixed ring remains decodable.

An OSLOG resize starts a new source-size generation: the producer rebases to
the resized source ring and replays its current contents rather than mixing
incompatible moduli. Thus `total` is absolute within that generation, and the
post-resize `msg_bufx == total % msg_size` invariant still holds. The ordinary
no-resize boot has one uninterrupted generation.

There is deliberately no payload CRC: a continuously live ring would invalidate
it between appends. Magic, geometry checks, and the byte-before-length ordering
are the v1 consistency contract.

## 4. Producer gates and boot stages

`lagunalogdump=1` is the default. `lagunalogdump=0` suppresses the mirror and
all additive LGLW XNU breadcrumbs without disabling `lagunapanicdump` or its
0123/0124 PAN1/console capture. The entry assembly scans the token-bound boot
argument before writing XNU_ENTRY; no boot_args means the default ON.

R3 had already frozen the LGLW ABI at pmsg+`0x1100..0x11ff` before R4
integration. R4 uses that two-slot, CRC/DONE format and also writes the
requested compact trail at pmsg+`0x1050..0x107f`: twelve chronological LE u32
stage-code words using R3's values. Entry clears exactly those 48 bytes each
boot; M1 at +0x1000, the boot counter at +0x1040, and the DIAG counter at
+0x1080 are untouched. Committed LGLW is authoritative if the channels ever
disagree. Stage values and detail semantics are exactly
`research/laguna-ramdump/producer/STAMPS.md`.

## 5. Reader integration requested from R2

Decode this record from the physical pmsg aperture, expose the reconstructed
raw log and header metadata, and use it as XNU runtime evidence even when the
live msgbuf in the kexec window is absent from the 4 MiB `kernel.log` dump.
Keep PAN1 and LGLW as independent channels. R2 also needs the separately
published LGLW decoder; R4 does not edit R2's owned reader.
