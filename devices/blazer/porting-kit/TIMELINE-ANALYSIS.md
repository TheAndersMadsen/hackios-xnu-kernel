# TIMELINE-ANALYSIS — accounting for the 26.455 s ENTRY→usb-preflight window

A-TIME, 2026-10-27. Host-only. Sources: parked dump
`research/session-20261027-usbboot/v2b-kernel.log` (decoded with
`research/laguna-usb-console/tools/decode-shim-stamps.py` + raw header read),
shim v3.3 tree (READ ONLY: `hackios-xnu-kernel/blazer/teguload/shim/`), live
FDT snapshot `hardware/bootchain-20261022/live-fdt.dtb`, ABL console logs
(`research/tgl-console-v2.txt` = the 2026-10-25 DER run with ABL timestamps;
`session-20261027-usbboot/v2b-abl.log` = the post-reset ramdump-retrieval
session, not the shim boot).

## 1. What the dump actually contains (raw header decode)

```
TSHIMST1 @ PA 0x95430000 (file offset 0x230000), nonce 0x6dbf3154abd454ef
count=8/8  seq=8  dropped=16  reason=3 (usb-fatal)  last_stage=0x7 flags=0x8 (usb-preflight FAULT)
freq=38400000  t0=612369183  tlast=1628253845
accepted trail: entry → cpu-state DONE(0x80d01998) → wdt-survey ENTER/DONE(0x3)
  → fdt-parse ENTER(0x8a000000) → console-init ENTER → wdt-pat DONE(0x2)
  → usb-preflight ENTER          [stamps 9..24 dropped: 8-slot v1 saturating array]
```

- Window arithmetic: (tlast−t0)=1,015,884,662 ticks ÷ 38.4 MHz = **26,455.33 ms**.
- `tlast` only advances on *accepted* stamps (stamp.c:93-95), so 26,455 ms is
  exactly **ENTRY → usb-preflight ENTER of the first boot**. The FAULT stamp
  and the terminal reason fired later (dropped from slots but captured in the
  header `last_stage/flags/reason` — that design worked; keep it).
- **The ABL handoff is excluded by construction.** t0/tlast are the shim's own
  cntvct reads; cntvct is monotonic from SoC reset, so ABL's entire
  OKAY→jump→teardown time sits *before* t0, not inside the window.
  Absolute anchor: t0 = 612,369,183 ticks = **15.95 s SoC uptime** at shim
  ENTRY. If the fleet wants OKAY→pixel accounting, that needs the shim-boot
  ABL console, which was not captured for this run (the v2b-abl.log is the
  ramdump-retrieval boot after the PSCI reset).
- The flashed build was **v3.2** ("laguna teguload v3.2 noras + USB console +
  TSHIMST1" in `out/shim-v32-diagnostic-Image`): 8-slot saturating stamp
  array, no per-stamp timestamps, no CONSOLE_CLS stamp (its presence would
  have evicted usb-preflight ENTER from slot 7 — the dump proves the absence),
  and preflight-before-CPM order. The current tree (v3.3) already fixes the
  channel (64 slots + per-slot t, tgl_stamp.h:5-47) and the order
  (tgl_usb_console.c:124-133). All line refs below are to the current tree.

## 2. Stage-by-stage cost model (ENTRY → usb-preflight ENTER)

Running context for every stage: the shim executes with **MMU off** (measured:
cpu-state arg 0x80d01998 → EL2, SCTLR.M=0), so *every* data access is
Device-nGnRnE — no cache, no write combining, each access one interconnect
transaction waiting full completion.

| # | stage | code | device ops in window | cost model |
|---|-------|------|----------------------|------------|
| 1 | entry | start.S:22-64 | bss zero + 64-B stamp init (DRAM) | sub-ms; image ≈1.29 MB |
| 2 | cpu-state | shim.c:216-226 | 2 MRS | ≈0 |
| 3 | wdt-survey | shim.c:249-257, wdt.c:18-45, 88-94 | ≤8 fault-gated MMIO reads, 2 candidates | ≈0 (µs each) |
| 4 | fdt-parse (admission) | shim.c:262-270 → fdt.c:111-242 | **1,379,866 byte-loads** measured over the real live DTB | see §3 |
| 5 | fdt fb-scan | shim.c:308-313 → fdt.c:305-392 | **1,040,237 byte-loads** measured | see §3 |
| 6 | console-init | shim.c:272-274, console.c:60-65 | none (pointer assign) | ≈0 |
| 7 | **paint: con_clear + banner** | shim.c:345-397, console.c:67-84, 86-107, 129-176 | **≈2.18 M Device stores** | see §3 |
| 8 | wdt-pat (first pat) | shim.c:358-359, wdt.c:49-64, 96-102 | 8 MMIO ops | ≈0 |
| 9 | banner tail → usb-preflight ENTER | shim.c:361-407, tgl_usb_console.c (v3.2: preflight first) | text stores (in #7 census) + a few MMIO reads | ≈0 |

**FDT census (measured, not estimated).** The parser walks ABL's live tree at
0x8a000000. Proxy = preserved live-FDT snapshot: 896,033 B total, 809,096 B
struct block, 86,881 B strings, **3,628 nodes, 25,661 properties**, depth ≤7,
0 memreserve entries, 825 `compatible` properties totaling 15,385 B (the
fb-scan needle-scans every byte of those). I replayed both walkers in Python
against the snapshot and counted every byte the C code touches (tokens 4 B,
node-name stringlen scans, property headers, strings-block name lookups, and
the full `eq()` compare chains against the 11 admission names / 4 scan names):
admission walk **1.38 M byte-loads**, fb-scan walk **1.04 M byte-loads**,
total **2.42 M byte-loads** (lower bound ÷4 = 0.61 M if the compiler merged
the 4-byte `be32` byte loads into word loads; -mstrict-align does not prevent
that). `fdt.h:3` caps the blob at 2 MiB, so this cost is bounded even if ABL's
runtime tree grows ~2× over the snapshot. The admission walk is mandatory
(live-RAM admission gate); **the fb-scan walk is cosmetic in this build** —
in the DER-safe posture its only consumer is the `DTFB` console line
(shim.c:310 → shim.c:394); the candidate chain takes the placeholder
regardless (shim.c:311-312).

**Paint census.** FB = 1280×2856, stride 5120 (console.h:5-7); fallback PA
0xf0000000 (shim.c:45). `con_clear` writes 640 8-byte stores/row;
v3.2's clear extent is not in the current tree (the 24-row cap,
console.c:54-58, is a v3.3 mitigation — "the 2026-10-27 run measured 26.4 s
of paint" — implying v3.2 cleared the full 2,856 rows). `put_char` writes
16×8×3×3 = **1,152 32-bit stores per character**, *including* background
cells (console.c:97-106). Banner text in the window (v3.2 strings, dtfb=0 —
the stock tree has no fb node, fdt.h:40-42): SHIM 55 + MPIDR 20 + WDTPOS 52 +
WDT0 29 + WDT1 29 + DPU 27 + FBPA 20 + FBSRC 30 + DTB 19 + DTCOMPAT 25
(google,lga-blazer) = **306 chars → 352,512 stores (1.41 MB)**. Clear:
full-screen = 1,827,840 8-byte stores (14.6 MB); 24-row (v3.3) = 768,000
(6.1 MB). Window total: **≈2.18 M stores** (v3.2 full-screen) or 1.12 M
(24-row variant).

## 3. Where the 26.455 s went — ranked

**#1 — the framebuffer paint, ≈23.5 s (~89% of the window).**
Arithmetic: 26.455 s − FDT share (see #2) − ~0.2 s misc ⇒ ~23.5 s over
≈2.18 M stores = **~10.8 µs per Device store** (~84k stores/s, ~0.66 µs/byte,
1.5 MB/s to DRAM). Cross-checks that make this the only consistent split:
- The v2 run (2026-10-25) reached mid-`probe_dpu` in ~3 s (shim.c:4-12
  recollection; the ABL log of that run cuts at 33.65 s with the jump right
  after) — that path contains the *same two FDT walks* but crashed **before
  any paint**. That bounds the FDT walks at ≤~2.5 s and hands the remaining
  ~23.5 s to the paint. Load vs store asymmetry (~1 µs/load vs ~11 µs/store)
  is consistent with non-posted single-beat writes draining serially while
  the v2 path was read-dominated.
- S1's own v3.3 comment records the same conclusion from this run
  (console.c:50-52, shim.c:341-344).
- Hardware is NOT inherently slow on this path: in the post-reset ABL session,
  ABL's own USB/PHY/CPM bring-up ran **23.13 → 23.23 s ≈ 100 ms**
  (`v2b-abl.log`: "Hsio_n usb2 phy enabled=0 before teardown" → PHY version
  reads). The 26 s is not device latency; it is our store pattern.
- Plausible amplifier (not proven): ABL drops the cluster into power-saving
  OPPs at handoff (`DVFS: Switching mode to: Power saving`, opp 19/21/13/7 in
  the ABL log) — uncached MMU-off stores at the lowest OPP with per-store
  interconnect round trips would land in exactly this range. Whatever the
  microarchitectural cause, the empirical rate is what to plan against.
- `0xf0000000` itself is *good* DRAM: bank 0 = 0x80000000+0x80000000
  (live-FDT memory nodes, 8×2 GB banks); it lies in the unreserved gap
  0xeb000000..0xf6200000 (`cp_rmem_1` ends 0xeb000000, `sec_dram` starts
  0xf7000000, `bl31_dram` 0xffe00000). Not a decode hole, not a secure
  carveout. But **pixel visibility was never verified** on any run — see
  instrument I-5.

**#2 — the FDT double walk, ≈2.0-2.5 s (~9%).**
2.42 M byte-loads at the ~1 µs/load implied by the v2 bound. Kills two ways:
it is pure DRAM re-reading of an 896 KB blob with byte-granular loads, and
the second walk (fb-scan, 1.04 M loads ≈ ~1 s) buys only the cosmetic DTFB
line in this build (shim.c:310, 394). Post-fix this becomes the dominant
sink; the recommendation list keeps it instrumented and offers a free gate.

**#3 — everything else, ≈0.2 s (<1%).** start.S bss zero, 24 stamp writes
(each ~10 stores + dsb, stamp.c:74-102), wdt survey (≤8 MMIO reads),
wdt-pat (8 MMIO ops), banner arithmetic. No action.

**Explicit non-sinks (checked, absent):** there are **no udelay/mdelay,
photo-window sleeps, or retry loops anywhere on the ENTRY→usb-preflight
path**. `delay_us` exists only in laguna_usb.c:94-111 (post-preflight);
CPM's 3 s deadlines (tgl_cpm.c:30,35,112-133) and the 10-command board
resume are post-preflight in v3.2 (and are ENTER/DONE-stamped); `TGL_USB_
WAIT_SECONDS=8` (shim.c:54-56) and the 40 s photo window (shim.c:115-128)
are post-preflight by construction. Note for v3.3: the CPM-first reorder
moves `tgl_cpm_init` + board resume *into* the pre-preflight region, adding
a worst-case 3 s ACK deadline + up to 10×3 s send budgets between
console-init and usb-preflight ENTER — correctly stamped (CPM_ACK/PHY_RESUME
ENTER+DONE), just don't be surprised if the next pre-preflight window is
~0.1-3 s larger when the mailbox ACKs slowly.

**WDT interplay (healthy, keep it):** cl0=24 s irq / cl1=30 s reset
(wdt.h:14-16); pat reloads the *surveyed* VALUE (wdt.c:53-55); `con_tick`
pats every 32 clear rows and every 256 chars (console.c:80,90; shim.c:345),
so a ~0.22 s tick cadence covers even an 11 µs/store paint — no starvation.
At the observed rates the *terminal* red-screen repaint (~2.2 M stores,
stage_fail shim.c:134-150) costs another ~20-25 s before the 40 s photo
window; the WDT ticks keep that survivable, but budget ~60 s on-panel for
any terminal failure when reading runbook timings.

**What killed it after the window (context, already S1-diagnosed):** v3.2
read DWC3 GSNPSID/GCTL + USB2 PMU/CFG registers *before* CPM clock enable —
unclocked IP from a cold ABL handoff → refusal/fault → `stage_fail`
(reason=usb-fatal, shim.c:409-426). v3.3 reorders CPM init → board resume →
preflight and stamps each read's value (tgl_usb_console.c:124-133, 54-113).
The 16 dropped stamps = refusal stamps + two same-nonce ABL slot-ladder
re-entries appending to the saturated v1 array (tgl_stamp.h:8-10).

## 4. Recommended instrumentation for S1 (they wire it)

Priority order; "elapsed" = cntvct delta × 1000 / freq, computed by the
caller and put in `arg` (per-slot `t` already lands in v3.3 — arg=elapsed is
cheap redundancy that keeps the decoder trivial):

- **I-1 FDT_PARSE DONE + elapsed-ms** — missing today (shim.c:264 has only
  ENTER). One stamp after the admission walk; if S1 keeps the fb-scan
  separate, give it its own DONE (or one DONE with both elapsed values
  packed).
- **I-2 CONSOLE_CLS DONE + elapsed-ms** — exists (shim.c:347) but arg=0;
  this single number is the paint-route meter: µs/store = elapsed/768000
  (v3.3 24-row clear) or /1827840 (full-screen). Add a new
  CONSOLE_TEXT DONE + elapsed after the banner, just before
  `tgl_usb_console_start` — with I-1, ENTRY, and usb-preflight ENTER that
  decomposes the whole window with zero ambiguity.
- **I-3 CONSOLE_INIT DONE** after `con_init` (shim.c:326) — trivial, but
  completes ENTER/DONE pairing on the only stage that currently lacks it
  besides FDT.
- **I-4 FB route probe** at con_init: `probe_write32` a magic word to
  con_fb_pa, `probe_read32` it back, stamp written‖readback in one arg.
  Two accesses. Nothing else proves pixel *visibility*, and the readback
  delta is a direct single-access round-trip measurement on the exact PA
  the paint uses.
- **I-5 wdt-pat DONE arg upgrade** — pack the patted count *and* the
  remaining VALUE ticks (`w->now`, already loaded in pat_one wdt.c:56-57),
  or add one extra stamp. Makes WDT-budget vs stage-duration arithmetic
  visible in every dump (the 24/30 s budgets vs a 26 s window matter).
- **Keep** (device-proven by this dump): header `last_stage/flags/reason`
  terminal-stamp behavior survives slot saturation — the FAULT/usb-fatal
  verdict was readable even with 8/8 slots and dropped=16. Same-nonce
  re-entry APPEND (tgl_stamp.h:45-46) also proven useful (it captured the
  two ABL ladder retries).
- **No timeout reductions are justified on the pre-preflight path** — there
  is nothing sleeping there to shorten. Optional/cosmetic only: tighten
  `CPM_TIMEOUT_MS` 3000→1000 (tgl_cpm.c:30) since a healthy ACK is
  ms-scale; that caps v3.3's newly in-window CPM wait. The expensive stages
  are not waits — they are store/load volume, so the levers are below.

## 5. Cheap bring-up latency levers (host-verifiable before next flash)

Ranked by stores saved, all at the measured ~11 µs/store:

1. **Skip `con_clear` in diagnostic builds** — `put_char` paints opaque
   glyph cells (background included, console.c:99), so text stays readable
   over splash residue. Saves 768k-1.83M stores (35-84% of the paint).
2. **SCALE 3→1** (console.c:9): text 352,512 → 39,168 stores (9×). 8×16
   text on a 1280-wide panel is still legible.
3. **Skip background cells** in put_char when a clear already ran
   (console.c:97-106): another ~40-60% of glyph stores.
4. Combined worst case ≈ 306×128 ≈ 39k stores ≈ **0.5 s** at today's
   measured rate; ENTRY→usb-preflight drops 26.5 s → ~3 s, at which point
   the FDT walk is the new #1 (I-1/I-2 will show it).
5. **Gate the cosmetic fb-scan walk** behind `TGL_SCANOUT_PROBE` (its only
   DER-safe consumer is the DTFB print, shim.c:310→394): saves ~1.04 M
   Device loads (~1 s) for zero diagnostic loss.

## 6. Residual unknowns

- v3.2's con_clear extent (full-screen vs rows): disambiguate by
  disassembling `out/shim-v32-diagnostic-Image` (loop bound 2856 vs 1200) —
  not done here (no aarch64 objdump verified on this host); both variants
  are carried in the arithmetic above and the ambiguity dies with I-2.
- Runtime live-DTB size on 2026-10-27 vs the preserved 896 KB snapshot
  (ABL fixups grow it; admission cost scales linearly; 2 MiB cap bounds it).
- Microarchitectural cause of ~11 µs/store (DVFS OPP, interconnect QoS,
  non-posted drain) — observable but not fixable host-side; the fix is
  volume reduction (§5), and I-4 measures it directly per boot.
- ABL-USB-STATE.md was not present in research/laguna-usb-fix/ at delivery
  time (dir contains CPM-FIRST-SPEC.md, DT-USB-FACTS.md, sim/); the ABL
  findings here are from tgl-console-v2.txt and v2b-abl.log directly.

---

## SUMMARY (5 lines)

1. The 26,455 ms is shim-internal ENTRY→usb-preflight ENTER (t0/tlast at
   38.4 MHz; t0 = 15.95 s SoC uptime); ABL handoff time is excluded by
   construction and no waits/delays/retry loops exist on this path.
2. #1 sink ≈23.5 s (89%): the findings-console paint — ~2.18 M uncached
   MMU-off Device stores (full-screen clear 1.83 M + 306-char banner
   352,512) ⇒ ~11 µs/store empirical; 0xf0000000 is verified real,
   unreserved bank-0 DRAM, so the route is slow, not wrong.
3. #2 sink ≈2.0-2.5 s (9%): two full walks of ABL's 896 KB / 3,628-node /
   25,661-prop live FDT = 2.42 M byte-loads measured; the second walk
   (fb-scan, 1.04 M loads) buys only the cosmetic DTFB line in the DER-safe
   build — gate it off.
4. Death cause after the window is unchanged (v3.2 unclocked-DWC3 preflight
   reads, reason=usb-fatal, FAULT at usb-preflight); v3.3's CPM-first order
   + value stamps address it; 16 dropped stamps = refusals + 2 ABL ladder
   re-entries into the saturated 8-slot v1 array.
5. For S1: FDT_PARSE/CONSOLE_CLS/CONSOLE_TEXT/CONSOLE_INIT DONE stamps with
   elapsed-ms args, FB write+readback probe stamp, wdt-pat arg += remaining
   VALUE ticks, keep header terminal-stamp behavior; latency levers = skip
   clear, SCALE 1, skip bg cells ⇒ ~26.5 s → ~3 s expected.
