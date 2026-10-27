# Payload admission — the embedded envelope, the window, and the first XNU boot (2026-10-27)

Companion to [`ABL-LPCM-REQUEST.md`](ABL-LPCM-REQUEST.md) (which owns the
cold-path LPCM wire). This doc owns everything between "EP0 console is live"
and "XNU executes": how the payload is delivered to the shim, how the shim
admits it, how the RAM window is chosen against the stock FDT, and what the
v51 attempt proved. Sources: the shim sources at
`hackios-xnu-kernel/blazer/teguload/shim/` (`loader.c`, `payload.h`,
`fdt.c`, `shim.c`, `wdt.c`, `laguna_usb.c`, `tgl_usb_console.c`), the packer
(`blazer/teguload/mkpayload.py`, `shim/build.sh`), the attempt dumps
(`research/laguna-usb-fix/attempts/20261027-094508-v39` … `-103925-v51`),
and the stock FDT
(`research/laguna-foundation/kernel-blazer/devices/blazer/fdt/laguna.dtb`,
decompiled to `/tmp/laguna.dts`). Host-only throughout; every device claim
cites an attempt.

## 0. TL;DR — state after v51 (2026-10-27 10:40)

**XNU boots on the phone.** Attempt v51 (image sha256 `9aa84e828405f90a…`)
passed payload admission end to end: embedded envelope found → validated →
window `0xd0000000 + 0x0A000000` admitted → segments copied → EL2→EL1 drop →
XNU entered and ran the full early-init ladder on real silicon — VM
bootstrap (zones, maps, compressor), IOKit, the three security policies
(vnguard / AMFI / Sandbox stand-ins), PMRD power-state transitions — then
panicked with an assertion (`"%s:%d Assertion failed: %s"`) immediately
after the PMRD ON_STATE ladder and issued `MACH Reboot`. **That assertion is
the current blocker.** The panic was fully captured: XNU-MSGBUF v2 mirror
(seq 6, 7,642 B reconstructed), compact breadcrumbs `XNU_ENTRY(6) →
XNU_INIT(7) → PANIC(12)`, and a 90 KB persistent_ram console (reader verdict
`PANICKED | XNU entered=yes`). Nothing flashed; the phone returned itself to
a force-fastboot park via the panic reset.

The admission gates that v51 passed were each earned by a refusal on a
previous attempt (§4 has the refusal table). Every refusal stamps the first
8 chars of its reason text into the TSHIMST2 trail, so the dump names the
exact failing check.

## 1. Delivery: the embedded TEGUPLD1 envelope

The tegu-proven delivery (XNU-ENTRY-CONTRACT §5) is an envelope glued
directly after the shim image, not a scan of the ABL initrd:

```
_end+0   "TEGUPLD1"          _end+8   u64 payload length
_end+16  TEGUXNU payload blob (mkpayload.py output: header at +0, seg
         table at +0x2a8, segments, ADT last)
```

The packer side (`shim/build.sh`, when given a payload):

1. builds the shim, `objcopy -O binary`, pads the Image to `_end` so
   `.bss`/stacks exist as zeroed bytes;
2. appends `"TEGUPLD1" + u64 len + payload`;
3. **raises the Linux arm64 Image `image_size` field (u64 at `_head+16`)**
   to `end + 16 + len` — ABL's arm64 Image loader uses `image_size` to
   reserve the region, so without this raise the envelope sits in memory
   nothing accounts for (and the embedded probe would read past the
   advertised image);
4. ships an **empty newc cpio as the ramdisk** — tegu semantics:
   `ramdisk_size = 0`, the envelope IS the delivery. Shipping the payload
   twice busts the 64 MiB AVB partition budget (v46: envelope embedded AND
   payload still in the ramdisk produced an 84,480,000-byte boot image —
   larger than the 67,108,864-byte partition, so AVB refused and the
   attempt aborted pre-boot; v46b with the empty cpio came in at
   42,889,216 bytes and booted).

The shim side (`tgl_payload_embedded()` in `payload.h`) reads `image_size`
from `_head+16` and treats `_end` as the envelope only if
`image_size` covers at least 16 bytes past `_end` (≤ 128 MiB cap). When
`image_size == _end - _head` (a diagnostic build with no envelope) it
returns the `NO_EMBEDDED` sentinel **without reading past `_end`**, and
`loader_boot()` falls back to the initrd scan. The envelope then goes
through exactly the same `tgl_payload_validate()` as a scanned payload, and
one extra explicit check refuses an envelope whose bytes overlap the
destination window (the shim would be scribbling over the image it is still
executing).

**v51 trail (decoded expectations):** `payload-admit DONE 0x1` (FDT parsed)
→ `DONE 0x2` (handoff inputs) → `DONE 0x3` (embedded envelope found,
aligned) → `DONE 0x4` (envelope validated) → window checks → … — step ids
3/4 fire only on the embedded path; a scan fallback never emits them.

## 2. Fallback: the ABL-initrd scan and its 64 MiB budget

`tgl_payload_find()` scans `rd_start..rd_end` at 8-byte stride for a valid
TEGUXNU header, bounded by the actual initrd extent **and a 64 MiB search
budget** (`TGL_SCAN_LIMIT = 0x4000000`), never rounding the start down or
reading a candidate past initrd-end.

**Why the fallback was demoted (v45):** ABL concatenates retained vendor
archives before the boot ramdisk, so the initrd extent the shim sees is
*not* the payload; with the archive prefix the TEGUXNU magic sat beyond the
64 MiB budget and the scan refused with the stamped text `no TEGUX` =
`no TEGUXNU payload within scan budget` (v45 dump, seq 75/76,
`payload-admit [REFUSED] arg=0x5855474554206f6e` at ~29.1 s). The envelope
is therefore the primary delivery and the scan only a fallback for
diagnostic images that still ship a payload ramdisk.

## 3. Validation — what a payload must satisfy

`tgl_payload_validate()` (host-mirrored in the QEMU smoke path) refuses, in
order, on:

- truncated/misaligned source, bad magic/version/`nsegs`, truncated segment
  table;
- **fixed kernel base:** `virt_base == 0xfffffff004000000` — the laguna
  lineage is linked at a fixed non-KASLR base (tegu's `__TEXT`-derived rule
  would place this kernel 32 MiB too low and XNU would fetch garbage);
- **window shape:** `phys_base` non-zero and **32 MiB-aligned**
  (`phys_base & 0x1ffffff == 0`), `mem_size` 16 KiB-aligned and ≤ 6 GiB —
  else `invalid kernel window`;
- per-segment: inside the window, non-overlapping, file-backed extents
  sane; entry VA inside a file-backed segment; ADT inside the payload;
  NUL-terminated command line;
- metadata placement: ADT + page-aligned boot_args + top inside the window;
- **headroom:** `window_end - top ≥ 64 MiB` — else
  `insufficient kernel headroom`.

The current payload's numbers (they are the admission budget): kernel
segments end at VA `0xfffffff009C24000`, i.e. `kernel_end - virt_base =
0x5C24000 = 96,522,240 B ≈ 92.1 MiB` of kernel above `virt_base`, plus the
64 MiB headroom ⇒ **the window must be ≥ 156.1 MiB**. v51's 160 MiB window
passes with ~3.9 MiB slack; v48's 112 MiB window refused
(`insuffic…`, seq 76 @15.9 s).

## 4. Window selection against the stock FDT

With the payload validated, `tgl_handoff_window()` (in `fdt.c`,
`tgl_window_check`) re-checks the window against the *device's* truth —
the DTB ABL passes the shim (device-measured at PA `0x8a000000`,
`fdt-parse ENTER 0x8a000000` on every attempt):

1. window fully inside FDT RAM (`memory@80000000` = `0x80000000 + 2 GiB` is
   the low bank the window must live in);
2. **no overlap with any fixed `/reserved-memory` reservation** — the
   reference map is the stock DTB
   (`research/laguna-foundation/kernel-blazer/devices/blazer/fdt/laguna.dtb`;
   decompiled: `/tmp/laguna.dts`, `/reserved-memory` starts at line 55).
   Fixed carveouts near the low bank include `pkvm_guest_firmware
   0x8af00000+0xb4000`, `xhci_dma_dram 0x97000000+4M`, `aoc 0x8d200000+48M`,
   `cdd_header 0x951f8000+32K`, `log_kevents 0x90200000+3M`,
   `famodel 0xc0000000+0x4b00000`, `farawimg 0xc4b00000+0x5000000`,
   `sec_dram 0xf7000000+0x8e00000`, `bl31_dram 0xffe00000+0x127000`, the
   MBA/CPM tracepoint DMA pools, and more;
3. no overlap with resident inputs (shim image, live initrd extent);
4. **unplaced dynamic pools:** `shared-dma-pool` nodes are checked against
   their `alloc` ranges — but an **unresolved pool (no `alloc` property)
   is now NON-FATAL**: the stock kernel places those dynamically, so no
   fixed range exists to conflict with (fdt.c `tgl_window_check` skips
   `p->unresolved`). The stock blazer FDT carries several such nodes —
   `pkvm_iommu_cma` (8 MiB, reusable), `vframe` (532 MiB reusable!),
   `vstream` (72 MiB reusable), plus the `faceauth_*` alloc-ranges pools —
   and the older check that refused on them rejected **every** possible
   window (v49/v50, stamped `FDT dyna` = `FDT dynamic pool unresolved`).
   Skipping them is safe: a reusable CMA pool that Android never opened
   reserves nothing.

Two window-adjacent checks live in `loader.c` instead: the **ramoops zone
`0x95200000 + 4 MiB` overlap is fatal** (it hosts TSHIMST2 at `0x95430000`
and the panic-capture channels — XNU's allocator must never claim it), and
a console-framebuffer overlap inside the window is *reported, not fatal*
(the live scanout may legitimately sit inside kernel-managed RAM; refusing
there would hide every later finding).

### 4.1 The refusal ledger (how the window was found)

Each row is one device attempt; the refusal text is the 8-char stamp
(`LB_FAIL`) decoded from the dump. This is the experiment series that ended
at `0xd0000000 + 0x0A000000`:

| attempt | window (base + size) | refusal (stamped text → full meaning) |
|---|---|---|
| v46b | `0x88000000 + 160 MiB` | `XNU wind…` — **XNU window overlaps FDT reservation** (fixed carveouts in the 0x88-0x92 region) |
| v47 | `0x97400000 + 128 MiB` | `invalid …` — **invalid kernel window**: base not 32 MiB-aligned (`& 0x1ffffff ≠ 0`) |
| v48 | `0x98000000 + 112 MiB` | `insuffic…` — **insufficient kernel headroom**: 92.1 MiB kernel + 64 MiB headroom > 112 MiB |
| v49 | `0xA8000000 + 256 MiB` | `FDT dyna…` — **FDT dynamic pool unresolved** (old fatal check; a pool with no `alloc` refused every window) |
| v50 | `0xD0000000 + 160 MiB` | same `FDT dyna…` refusal (window was already right; the check was wrong) |
| v51 | `0xD0000000 + 160 MiB` | **ADMISSION PASS** — unresolved pools non-fatal; all gates green |

`0xD0000000 + 0x0A000000` satisfies everything at once: 32 MiB-aligned,
inside the low 2 GiB bank, clear of every fixed reservation (the nearest
fixed carveouts are `farawimg` ending `0xc9b00000` below the window and
`cp_rmem_1 0xe8000000+0x2000000` above it — nothing fixed inside
`0xd0000000..0xda000000`), clear of ramoops (`0x95200000+4M`), ≥ 156.1 MiB
for kernel+headroom. `mkadt.py --phys-base=0xd0000000 --mem-size=0x0A000000`
bakes it into the ADT/payload header (`phys_base`/`mem_size`), and the
shim still verifies it against the live FDT at every boot — the ADT is a
request, the FDT checks are the authority.

## 5. Handoff-path instrumentation (what each fix bought)

- **`LB_STEP`/`LB_FAIL` breadcrumbs** (`loader.c`): the fb console is dead
  at handoff (CDPHY off — DISPLAY-PRODUCER), so every completed admission
  step stamps `payload-admit DONE <step id>` (1 FDT parsed, 2 handoff
  inputs, 3 envelope found, 4 envelope validated, 5 window admitted,
  6 ramoops-clear) and every refusal packs the first 8 bytes of its error
  text into a `payload-admit REFUSED <text>` stamp, followed by a second
  REFUSED stamp carrying the shim load PA (device: `0x80000000`) and the
  terminal reason. A parked dump now names its refusing check without any
  guessing.
- **Per-request EP0 stamps** (`laguna_usb.c`): every served control
  transfer stamps `usb-enum-wait ENTER arg = type<<16 | request<<8 |
  (length & 0xff)` and `DONE arg = 0x8000|bytes-served` (or `0x8001` on
  stall) — the host-driven EP0 path is new on hardware, so a wedge names
  the exact transfer (v42+ dumps show the `usb_log.py` polling loop as
  `0xc05a00` ENTER / `0x8180` DONE pairs = GET_LOG 384-byte reads).
- **Transfer-active predicate fix** (`laguna_usb_transfer_active`): the
  old `state != EP0_SETUP` test refused **every** host-connected handoff —
  after `usb_log.py` finishes its last read the endpoint parks in
  `WAIT_STATUS3` (host closed mid-status), which is not an active transfer.
  v44's dump proved `DSTS` parked in WAIT_STATUS3 at handoff time; the
  fixed predicate blocks only a data stage genuinely in flight
  (`state == EP0_DATA`). The 1-second grace wait + check immediately
  before the jump stay (shim.c "post-usb-wait").
- **WDT quiesce-before-wait** (`shim.c`, after v39-v43 forensics): the
  survey finds **three** firmware WDTs but the stage-boundary pat services
  only **two**; the unpatted instance bites ~9 s after the last pat —
  exactly where v39-v43 died (`usb-enum-wait FAULT 0x0` at
  pat@~4.4 s + 9.0 s ≈ 13.4 s on every one of those dumps). The full
  quiesce therefore moved to **before the enumeration wait**, where all
  bounded waits are already behind the shim, and the only recovery tool
  that matters afterwards is the 90 s stamped bring-up deadline.
  `wdt_quiesce_early` (the v3.1 posture) is kept only for the DER-Event-20
  A/B build flag.

## 6. The v51 record — XNU on the phone, and the current blocker

Attempt v51 (2026-10-27 10:39, image
`out/next-boot-v51/laguna-usb-next-boot-avb.img`, sha256
`9aa84e828405f90ad2cdf4c6016eaef2cc3e968c570001a751f5cad468d74429`,
kernel `mach.blazer-r4r5-tcm`, window `0xd0000000+0x0A000000`): admission
passed the whole §3/§4 ladder, the EL2→EL1 drop ran, and XNU executed.

Evidence (attempt dir `20261027-103925-v51/`, `reader-full.txt`, decoded
with `read-ramdump.py` against `mach.blazer-r4r5-tcm`):

- **Reader verdict:** `PANICKED | stage=PANIC(12) | XNU entered=yes |
  panicked=yes ("%s:%d Assertion failed: %s", frames=0)`.
- **XNU console (persistent_ram, 90,402 B):** Darwin Kernel Version 22.5.0
  (xnu-8796.121.2, `DEVELOPMENT_ARM64_BCM2837`, build stamp 2026-10-27
  09:21:34), `gic: v3` + virtual timer on PPI 27, full `VM bootstrap done`,
  zone_init maps, ATM/OSLog/BANK init, `iBoot version: laguna-shim`,
  security policies loaded (`vnguard`, `Apple Mobile File Integrity
  (stand-in)`, `Seatbelt sandbox policy (stand-in)`), then the PMRD
  (`IOPMrootDomain` family) power ladder — `PMRD: PMTraceWorker`,
  `changePowerStateWithTagToPriv(ON_STATE, …)`, OFF_STATE→ON_STATE
  overrides — ending in the **assertion panic** (`panic: message=%s:%d
  Assertion failed: %s`) and `MACH Reboot` at ~103.4 s console time.
- **Mirror v2:** header at pmsg+0x3F800 valid (seq 6), 7,642 B of ring text
  reconstructed; compact breadcrumbs `XNU_ENTRY(6) → XNU_INIT(7) →
  PANIC(12)`; LGLW slots `XNU_INIT CRC ok` / `PANIC CRC ok`. The panic
  capture machinery worked **on a real panic** for the first time on this
  path.
- **What is missing and why:** the TSHIMST2 stamp page decodes as
  "no magic" on the v51 dump — not a channel failure: the stamps live in
  the pmsg zone the running XNU/park session then scratches, and the
  handoff had already succeeded, so the shim's trail was consumed. The
  shim-side story on a successful run is told by the *absence* of a park
  at admission time plus the mirror/console; the RUNBOOK's §4 trails spell
  out both branches. Symbolication of the assertion needs frames the panic
  record didn't carry (`frames=0`) — the next diagnostic step is a
  symbolicated panic path on `mach.blazer-r4r5-tcm` (68,906 symbols
  available) or a targeted assertion-string hook.

**Current blocker:** the init assertion immediately after PMRD starts its
state machine (an IOKit power-management init assert; root `md0` was not
yet mounted at panic time — `root mounted=no`, as expected with the panic
kernel image and `rd=md0` cmdline still pointing at the 24 MiB recovery
root). Everything below it — delivery, USB console, admission, entry
contract, evidence capture — is done and reproducible.
