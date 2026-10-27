# INIT-NVRAM — P1 panic analysis: `IONVRAMCHRPHandler creation failed` (v54 wall)

Lane: P1 (NVRAM panic analyst) · 2026-10-27 · host-only, no device actions.
Evidence: `attempts/20261027-114400-v54/reader-full.txt` (lines 99–101), kernel source
`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch hakios), binary
`research/laguna-usb-fix/mach.blazer-r4r5-tcm` (68906 syms).

---

## (a) VERDICT — exact failure chain with file:line

```
IOPlatformExpertDevice::configureDefaults()            IOPlatformExpert.cpp:2136
 └─ createNVRAM()                                      IOPlatformExpert.cpp:2150-2172
     ├─ requires /options in gIODTPlane                :2158-2160   (v54 ADT provides it — OK)
     ├─ gIOOptionsEntry = new IODTNVRAM                :2164
     └─ gIOOptionsEntry->start()                       :2170
         └─ IODTNVRAM::start                           IONVRAM.cpp:1224
             └─ initImageFormat()                      IONVRAM.cpp:1280 → 1343-1395
                 ├─ /chosen fromPath                   :1353        (exists — OK)
                 ├─ copyProperty("nvram-bank-size")    :1357        ★ MISSING → silent `skip` (no log here)
                 ├─ copyProperty("nvram-proxy-data")   :1366        (skipped too)
                 ├─ IONVRAMV3Handler::isValidImage(null,0) :1375    (false — image null)
                 ├─ IONVRAMCHRPHandler::init(this,nullptr,0) :1379
                 │   └─ IODTNVRAMFormatHandler::getNVRAMProperties IONVRAM.cpp:1139-1180
                 │       ├─ /chosen                    :1147        (OK)
                 │       ├─ "nvram-bank-size"          :1150-1151   ★ LOG #1
                 │       │     "Unable to find nvram-bank-size property"
                 │       │     → ok=false → return false
                 │       └─ (never reached: "nvram-bank-count" :1158, "nvram-current-bank" :1166)
                 ├─ require_action(_format, skip, panic(...)) :1380  ★ LOG #2 + FATAL
                 │     panic("IONVRAMCHRPHandler creation failed\n")
                 └─ IONVRAMCHRPHandler.cpp:init:304    :303-304     LOG "Unable to get NVRAM properties"
                        → exit → delete handler → return nullptr   (logs before caller panics)
```

Matching boot log (`reader-full.txt`):
```
IONVRAM.cpp:getNVRAMProperties:1151 - Unable to find nvram-bank-size property
IONVRAMCHRPHandler.cpp:init:304 - Unable to get NVRAM properties
MACH Reboot
```
(The `FILE:FUNC:LINE - ` prefix is the `DEBUG_ALWAYS/DEBUG_ERROR` IOLog format, IONVRAM.cpp:82-92;
both print unconditionally in this build.)

**Fatality type:** fatal-by-**explicit `panic()`** in the caller — `IONVRAM.cpp:1380`
(`require_action(_format, skip, panic(...))`). The CHRP `init` failure itself (:304) is not a
panic and not a NULL deref; it logs and returns `nullptr`, and the caller panics. No assert
macro involved.

### Where the properties live (P2's question answered)

Read from **`/chosen`**, NOT `/options` — both `initImageFormat` (IONVRAM.cpp:1353) and
`getNVRAMProperties` (IONVRAM.cpp:1147) do `IORegistryEntry::fromPath("/chosen", gIODTPlane)`.
`/options` is only the **attach point** for the IODTNVRAM service (`createNVRAM`,
IOPlatformExpert.cpp:2158) — keep it, but it needs no NVRAM properties.
All three numeric keys are read via `copyProperty` + `OSDynamicCast(OSData)` and consumed as
`*(uint32_t*)data->getBytesNoCopy()` → **little-endian u32 in 4-byte OSData** (ADT format 0).
`nvram-proxy-data` is a raw **OSData blob** whose length must equal `nvram-bank-size` exactly.

### Complete required set, in read order

| # | property (on `/chosen`) | type | meaning | read at |
|---|---|---|---|---|
| 1 | `nvram-bank-size` | u32 LE | size of ONE bank in bytes — must equal `nvram-proxy-data` length **exactly** | IONVRAM.cpp:1150-1156 (+ :1357 early read) |
| 2 | `nvram-bank-count` | u32 LE | number of banks | IONVRAM.cpp:1158-1164 |
| 3 | `nvram-current-bank` | u32 LE | current bank index | IONVRAM.cpp:1166-1172 |
| 4 | `nvram-proxy-data` | OSData blob | the NVRAM bank image (validated later, see below) | IONVRAM.cpp:1366-1372 |

Boot-arg / IORegistry escape: **none exists.** Backend selection is purely image-content-driven
at IONVRAM.cpp:1375 (`IONVRAMV3Handler::isValidImage` needs magic `'NVV3'`,
IONVRAMV3Handler.cpp:31/99; everything else goes to CHRP). `createNVRAM()` is unconditional
(no boot-arg gate); the "x86 init" skip at IONVRAM.cpp:1236 is for AppleEFIRuntime's subclass
and does not apply here (`_controllerLock` is always set by IODTNVRAM::init :1207).

---

## The image itself — the trap after the 3 easy u32s

`IONVRAMCHRPHandler::init` (IONVRAMCHRPHandler.cpp:293-320) after `getNVRAMProperties`:
- **:306** `require_action(length == handler->_bankSize)` — proxy-data length must equal
  bank-size to the byte, else log + `nullptr` → same panic at IONVRAM.cpp:1380
  (log would read `length 0x0 != _bankSize 0x2000`).
- :308-312 `unserializeImage` failure is **non-fatal** (log only, :310).

But a garbage/zeroed image is still fatal, one step later:
- `unserializeImage` (:407-482) with a non-CHRP image: version = Unknown → early exit (:418),
  leaving `_commonPartitionOffset = 0xFFFFFFFF` (:414) and `_commonPartitionSize = 0x800`
  (constructor default, :323; confirmed in binary: `mov w8,#0x800; str w8,[x0,#0x3c]`).
- `unserializeVariables` (:485+) then walks `_nvramImage(nullptr) + 0xFFFFFFFF` with
  size `0x800` → data-abort panic on a wild pointer.
- And a *half-valid* image (name "nvram", cksum ok, but wrong adler) hits an unconditional
  `panic("header adler %#08X != calculated_adler %#08X")` (:431-433).

⇒ The proxy image must be a **fully valid CHRP v1 bank**: bank header named `nvram` with
correct `chrp_checksum`, plus a `common` partition so `_commonPartitionSize/Offset` get set.
`nvram-bank-count=1`, `nvram-current-bank=0` is the natural companion.

Bank image format (all fields LE; verified against IONVRAMCHRPHandler.cpp:31-48, :139-231):
- Block = 16 bytes; `chrp_nvram_header_t` = `sig u8 · cksum u8 · len u16 (blocks) · name[12]`.
- `cksum` = sum(sig, len, name) folded: `while s>0xff: s=(s&0xff)+(s>>8)` (:189-207) — note it
  skips the cksum byte itself.
- v1 bank header: name starts with `"nvram"` (:31, memcmp of 5 bytes at :223); its `len` is its
  own size (2 blocks), so the partition walk (:436) moves on to offset 32.
- adler at offset 16 = standard adler32 over `image[20:]` (:172-187, initial low=1/high=0 —
  identical to `zlib.adler32`). Wrong range or init → **panic** (:432).
- Partition walk from offset 0: headers named `common`/`2common` (:34/36) become the common
  region; `free`/other names are skipped. Data of the common partition = `name=value\0`
  entries; all-zero data = empty NVRAM (parse breaks at first `\0`, :511).
- V3 fallback check `'NVV3'` can never false-match this layout (first u32 = `0x0002_XX70`-ish).

No-controller safety (we have no NVRAM controller driver): `serializeVariables` :615 requires
`_nvramController != nullptr` and just returns `kIOReturnNotReady` → `sync()` is a harmless
no-op ⇒ RAM-only volatile NVRAM. `findCurrentBank` (no null check) is only reachable via
`setController`, which nothing calls. `getSystemPartitionActive()` = `_systemPartitionSize != 0`
→ false → only "options-common" service is created. All safe.

Binary verification (precedent method: INIT-ASSERTION.md §assert-site disassembly):
- 44 `IONVRAMCHRPHandler` symbols present in `mach.blazer-r4r5-tcm`.
- `IONVRAMCHRPHandler::init` @ `0xfffffff0079af884`: IOMallocType → stores `0x800` at +0x3c
  (the constructor `_commonPartitionSize` — the wild-pointer hazard is real in this binary) →
  `bl 0xfffffff0079af54c <IODTNVRAMFormatHandler::getNVRAMProperties>` → `tbz w0,#0` → line
  literal `#0x130 = 304` (the :304 log path) → `ldr w8,[x19,#0x8]; cmp x20,x8` (length vs
  `_bankSize`, the :306 gate, line literal `#0x132`) → `cbz image/length` skip → `bl
  unserializeImage` → non-fatal IOLog path with line literal `#0x136 = 310`. Matches source 1:1.

---

## (b) Ranked minimal fixes (mkadt.py-ready — P2 applies; I did NOT touch mkadt.py)

### FIX 1 (recommended, precedent-proven): add 4 properties to the existing `/chosen` node

Precedents in-repo:
- `research/upstream-scout-20261026/snapshots/UniXNU/scripts/unixnu/devicetree.py:36-49`
  `empty_nvram_proxy()` — byte-level compatible with this exact kernel source (checksum and
  adler math verified above).
- `research/laguna-springboard/work/userspace/kexec-xnu.c:973-976` — the kexec launcher that
  already booted this kernel on device emits the same keys (`nvram-bank-size`,
  `nvram-bank-count` implicit at :974, `nvram-current-bank`, `nvram-proxy-data`).

Add near `u32/u64/cstr` helpers (needs `import zlib`):

```python
def chrp_nvram_bank(size=8192):
    """Minimal valid CHRP v1 bank for IODTNVRAM (volatile RAM-only NVRAM).
    Byte-exact contract of IONVRAMCHRPHandler.cpp (8796.121.2): bank header
    'nvram' len=2 blocks @0; 'common' 128 blocks @32; 'free' @2080;
    adler32(image[20:]) @16 (range/init identical to zlib)."""
    def header(name, total):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, total // 16, name))
        ck = sum(raw)
        while ck > 255:
            ck = (ck & 255) + (ck >> 8)
        raw[1] = ck
        return raw
    image = bytearray(size)
    image[:16]      = header(b'nvram', 32)             # bank header, 2 blocks
    image[32:48]    = header(b'common', 16 + 2048)     # common partition @32, 128 blocks
    free_at         = 32 + 16 + 2048                   # 2080
    image[free_at:free_at + 16] = header(b'free', size - free_at)  # 382 blocks
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return bytes(image)

NVRAM_BANK = chrp_nvram_bank(8192)
```

Then inside `chosen = node('chosen', [ ... ])` add exactly:

```python
        # IODTNVRAM/IONVRAMCHRPHandler (IONVRAM.cpp:1343-1395): reads these from
        # /chosen. bank-size MUST equal proxy-data length to the byte
        # (IONVRAMCHRPHandler.cpp:306), else panic "IONVRAMCHRPHandler creation
        # failed" (IONVRAM.cpp:1380). Image must be fully valid (adler panic at
        # IONVRAMCHRPHandler.cpp:432) or fully invalid-with-partitions (wild
        # deref via _commonPartitionSize=0x800 default). NVRAM stays volatile:
        # sync() no-ops without an IONVRAMController (IONVRAMCHRPHandler.cpp:615).
        ('nvram-bank-size',    u32(len(NVRAM_BANK))),   # 8192 = 0x2000, u32 LE
        ('nvram-bank-count',   u32(1)),                 # u32 LE
        ('nvram-current-bank', u32(0)),                 # u32 LE
        ('nvram-proxy-data',   NVRAM_BANK),             # 8192-byte blob, ADT format 0
```

Exact property list (name → type → value):
1. `/chosen` `nvram-bank-size` → u32 LE → `0x00002000` (8192; == proxy-data length)
2. `/chosen` `nvram-bank-count` → u32 LE → `0x00000001`
3. `/chosen` `nvram-current-bank` → u32 LE → `0x00000000`
4. `/chosen` `nvram-proxy-data` → raw blob (format 0), 8192 bytes → the `NVRAM_BANK` image above

Notes for P2:
- mkadt's `node()` writes props as raw bytes with format 0 — an 8192-byte value is fine
  (same as existing 64-byte `random-seed`). `ph()` is just `('AAPL,phandle', u32)` — irrelevant
  here. Do NOT put these on `/options`; keep the `/options` node as-is
  (IOPlatformExpert.cpp:2158 requires it — that was the v54 fix, don't regress it).
- Bank size 8192 matches UniXNU; anything ≥ 2064 works as long as all three sizes stay
  consistent (len fields in blocks; bank-size == blob length).
- After successful init, `nvram-proxy-data` is removed from /chosen only in RELEASE builds
  (IONVRAM.cpp:1387-1391); our DEVELOPMENT build keeps it — harmless.
- Also satisfies the boot-args consumer: `unserializeVariables` auto-creates an empty
  `boot-args` NVRAM variable if missing (IONVRAMCHRPHandler.cpp:584-590).

### FIX 2 (ranked 2, only if FIX 1 boots but NVRAM reads misbehave)
Emit a `system` partition too (name `"system"` after the common partition) to give
`getSystemPartitionActive()=true` and an "options-system" service. NOT needed for boot;
skip unless something later demands system-partition variables.

### FIX 3 (ranked 3, ADT-only insufficient — fallback)
No boot-arg exists, so a kernel-side change would be required: synthesize the four properties
onto `/chosen` during DT parse (ml/pmap or PE_init_platform territory — P3/P6 coordination
required). Not recommended: FIX 1's layout already booted this exact IONVRAM code on this
device via kexec-xnu.c. A third theoretical route (hand IONVRAMV3Handler a valid `'NVV3'`
store + `nvram-raw` u32, IONVRAMV3Handler.cpp:289-335) is strictly more work — no precedent
in-tree.

---

## (c) Expected log signature after FIX 1

`getNVRAMProperties`'s failure line must disappear entirely. New expected prints
(all `DEBUG_ALWAYS`, format `FILE:FUNC:LINE - `; `DEBUG_INFO` stays silent, `gNVRAMLogging` off):

```
IONVRAM.cpp:initImageFormat:1364 - NVRAM size is 8192 bytes
IONVRAM.cpp:getNVRAMProperties:1176 - _bankSize=0X2000, _bankCount=0X1, _currentBank=0X0
IONVRAMCHRPHandler.cpp:unserializeImage:478 - NVRAM : commonPartitionOffset - 0x30, commonPartitionSize - 0x800, systemPartitionOffset - 0xffffffff, systemPartitionSize - 0x0
```

…then NO `Unable to find nvram-bank-size property`, NO `Unable to get NVRAM properties`,
NO `panic`/`MACH Reboot`; boot proceeds past `IODTNVRAM::start` into IOKit matching
(options-common service, platform UUID, then driver matching on /arm-io).

If instead you see `length 0x... != _bankSize 0x2000` → bank-size landed but proxy-data
didn't (length mismatch). If you see `panic: header adler ...` → checksum/adler range bug in
the generator (must be `zlib.adler32(image[20:])`, init value 1).

---

## (d) Fallback if ADT-only is insufficient

1. **Diagnostic first**: any change in the panic signature vs. the table in (c) localizes the
   missing/wrong property without a kernel change.
2. Kernel-side synthesis of the 4 props onto /chosen (requires P3/P6 coordination; precedent
   for the mechanism: `kexec-xnu.c` proves the consumer side only — the shim-side DT writer
   `hackios-xnu-kernel` xnuloader `loader.c:3087-3090` already emits the identical four keys
   for its DT path, so a shim-side inject into the shim's ADT/staging flow is the natural
   fallback point).
3. Last resort: source patch in `IODTNVRAM::initImageFormat` to skip handler creation —
   rejected: loses NVRAM entirely (userland launchd/Security/IOPlatformExpectedUUID consumers
   read it) and crosses lanes.

Artifacts: none beyond this doc + fleet report. No mkadt.py edits (P2 owns it).
