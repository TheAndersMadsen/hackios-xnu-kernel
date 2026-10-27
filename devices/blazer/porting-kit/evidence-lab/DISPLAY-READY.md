# DISPLAY-READY — mach symbol audit + level-1 paint recipe for v56

**Lane:** Q3 (display readiness) · 2026-10-27 · **Host-only.** No device, adb, fastboot,
flash or new QEMU action was performed. QEMU findings below are read-only reads of the
existing v55 evidence (`qemu-validation/evidence/runC-late-v55-nvram/`).

---

## 1. MACH SYMBOL AUDIT — the loud finding

**Binary audited:** `research/laguna-usb-fix/mach.blazer-r4r5-tcm`
sha256 `b39179b5d0268c9c98432c46bda0cfda66eacce299efb1baf79899c5342f465b`,
68,906 symbols (matches the fleet brief), mtime 2026-10-27 09:21. Its own embedded
build path string proves lineage: `<tmp>/opencode/c1-mach/xnu-src/xnu-8796.121.2` —
the **blazer foundation tree** warm copy + `research/laguna-usb-fix/patches/`
(`0001-blazer-r4r5-laguna-evidence-chain.patch`, `0002-blazer-tcm-integrated-tree-form.patch`).

### 1.1 IN the running mach ✅

| Piece | Proof (nm/strings) |
|---|---|
| Display handoff **consumer** (M2-era iteration) | `_hakios_blazer_display_init`, `_laguna_display_validate`, `_laguna_display_handoff_seal`, `_laguna_display_boot_video`, `_hakios_blazer_display_clean_on_present`; strings `/chosen/laguna-display-handoff`, `blazer-display: handoff rejected (%s); video disabled`, `blazer-display: %s: %ux%u stride %u …` |
| `io.display.native` **refusal** (Path B deliberately off) | `blazer-display: verdict=NATIVE-REFUSED` is NOT in this mach — but the refusal logic predates the verdict= taxonomy; only the old `%s:` form is present |
| Stock MiniIOMFB family | `_MiniIOMobileFramebufferShow`, `__Z28MiniIOMobileFramebufferStartP9IOService`, `__GLOBAL__sub_I_MiniIOMobileFramebuffer.cpp` |
| pmsg evidence machinery (evidence-chain patch) | `LAGUNA LOGDUMP: armed (XNU-MSGBUF v2 hdr @pmsg+0x%x …)`, `LAGUNA PANICDUMP: armed`, `blazer-panicdump: …` |

**Caveat on the consumer:** the mach carries the **OLD M2/M5-era validator** — its
strings have no `verdict=RAMCONSOLE/HEADLESS/ADOPTED` taxonomy. The **current**
foundation-tree source (`osfmk/arm64/hakios.blazer_display.c`) is G3's newer form
(`verdict=RAMCONSOLE display-unowned`, `verdict=HEADLESS cold record rejected`, …).
Per `display-revive/notes`: the old validator **headless-rejects the cold record**;
G3 accepts it as RAM-console. v56's rebuild must pick up the current source.

### 1.2 NOT in the running mach ❌ (exist only as source)

| Piece | Lives where | Mach proof of absence |
|---|---|---|
| **DC9x00 bus layer** `laguna_dc9x00.{c,h}` (MMIO windows, ADT gate, boot-arg parser, pmsg-mark guard) | shared laguna141 kernel-wt (`research/laguna-panic-capture/kernel-wt`, tip 8d6d4b2) as **0101** (141ab67); original redwood commit 2334596; patch `research/laguna-xnu-patches-display/0001` | `nm` → **0** hits for `laguna_dc|dc9x00|splash`; `strings` → **0** hits for `LAGUNA DC9x00|dc9x00|io\.display` |
| **Read-only probe** `laguna_dc_probe.c` (`laguna_dc9x00_early_probe`) | same tree as **0102** (5a8bd34) / redwood 3f7ea43 / patch `0002` | same — 0 symbols, 0 strings |
| **Splash** `laguna_dc_splash.c` (`laguna_dc9x00_early_splash`, level-1 fill + live paint, level-2 repoint) | same tree as **0103** (1c4d688) / redwood 16587c5 / patch `0003` | same — 0 symbols, 0 strings |
| **Console fix 0104** (fbcon TTBR1 alias) | redwood/laguna141 lineage only | mach has no laguna fbcon at all (no `fbcon` symbols) |
| **iomfb publish gate 0106/0107** (`laguna_fbswap`, LagunaFramebuffer gate) | laguna141 lineage (fd32595, eb3ad1b) | `nm` → 0 hits `fbswap|LagunaFramebuffer` — so the `io.display.iomfb=1` token present in the v55 QEMU cmdline is **inert** in this mach |

**Consequence (loud):** `io.display.probe` and `io.display.splash` are **unparsed
no-ops in the running mach**. A v56 device attempt firing the current binary
**cannot** produce a level-1 paint or any probe evidence. The level-1 paint
experiment requires the v56 mach rebuild (§4).

**Supporting history (not contradictory):** an older 141-lineage mach DID carry the
series — `research/laguna-usb-fix/attempts/20261027-112139-loop/pre-kernel.log`:
`[983.897099]: LAGUNA DC9x00 probe: ADT laguna-dpu node absent; MMIO probe skipped`.
The patches exist and have fired on device; they were just never folded into the
usb-fix foundation tree that builds the current mach.

**Fresh host verification (today):** `research/dc9x00-xnu-test/build.sh` →
**500 checks, 0 failures, ALL TESTS PASSED** — the patch series' register logic is
still green; only the port + rebuild is missing.

---

## 2. LEVEL-1 PAINT RECIPE (for the v56 attempt, after §4 prerequisites)

Level-1 = **DRAM writes only, zero display-register writes** (`laguna_dc_splash.c`:
"Level 1 (io.display.splash=1) never writes a single display register").

### 2.1 Boot-args (XNU PE boot args = the shim `command_line`)

```
io.display.probe=1 io.display.splash=1
```

appended to the standing contract (`build-next-boot.sh`): `rd=md0 ignition_level=1
launchd_unsecure_cache=1` (+ WITH_ROOT7 extras if that opt-in rides along).
Parsing is `PE_parse_boot_argn` (`laguna_dc9x00_bootarg`); `probe` defaults to 1,
`splash` to 0 — set **both explicitly** so the profile is self-describing.

- **`io.display.splash=2` (repoint) is FORBIDDEN for the first v56 attempt** — it is
  the only DC-register-write path in the series, and DISPLAY-PRODUCER discipline
  (CPM power proof before MMIO) is not yet satisfied on device.
- The shim-side gates `tgl_disp_snap=1` / `tgl_disp_revive=1` go in the **ABL FDT**,
  not this cmdline — leave them **out** for the first v56 attempt: the R1 revive
  ladder stops at R2 UNSUPPORTED anyway, and level-1 needs only the cold handoff
  record the shim already builds.

### 2.2 Expected console signatures (grep-able, in order)

| # | Line (format strings from the applied source) | Proves |
|---|---|---|
| G1 | `LAGUNA DC9x00: mapped fe-be 0xee00000+0x120000 -> <va>, dsi2h0 -> <va>` | ADT gate OPEN + MMIO windows mapped |
| G0 | — **absence** of `LAGUNA DC9x00 probe: ADT laguna-dpu node absent; MMIO probe skipped` | if this line APPEARS, the v56 packaging forgot the `/laguna-dpu` node → attempt yields zero display evidence (this exact line is the historical gate-refusal signature) |
| P1 | `LAGUNA DC9x00 probe: outpath0 ctrl=0x… en=… (live layer=…, line advancing=…)` | DPU domain state + whether outpath0 still advances (scanout liveness ground truth) |
| P2 | `LAGUNA DC9x00 probe: layer[0] pa=0x… stride=…` | **ABL's actual scanout PA** — the decisive comparison against our `0xf0000000` (the S1 hypothesis test from DISPLAY-PRODUCER §3.3) |
| P3 | `LAGUNA DC9x00 probe: dsi2h0 status=0x… cri_busy=…` | DSI2 host state after ABL's CDPHY power-off |
| S1 | `LAGUNA DC9x00 splash: fb = … (PA=0xf0000000, …)` | fixed-FB pattern fill armed (1280×2856×4 = 0xDF2000) |
| S2 | `LAGUNA DC9x00 splash: painted live buffer …` (or `live buffer not painted … (layer[0] PA=0x…)` on refusal) | level-1 paint of ABL's live buffer actually happened — DRAM-only |
| C1 | `blazer-display: verdict=RAMCONSOLE display-unowned …` (G3 build) — never `verdict=ADOPTED scanout-live` | cold record consumed honestly; forbidden flags (`SCANOUT_LIVE`/`DPU_CONFIGURED`) not asserted |
| I1 | `PMRD: tellChangeUp OFF_STATE->ON_STATE` → `cpu_start() cpu: 0` → `MiniIOMobileFramebuffer: display …` | the QEMU-proven attach ladder continues on device |

### 2.3 Physical expectation: the panel stays DARK — and that is success-compatible

Proven end-to-end (DISPLAY-PRODUCER §0, reproduced in 6 captures): ABL powers the
MIPI CDPHY off ~41 ms before the kernel jump, and the panel self-sleeps after link
loss. **No currently-built artifact can light the glass**: the shim's paint is
post-CDPHY-off; R1-R7 revival stops at R2 (clock-domain isolation) = UNSUPPORTED;
level-2 repoint is gated off. So for the v56 attempt:

- **Expected:** dark panel, while DRAM at `0xf0000000` (+ ABL's live buffer if
  probe finds one) carries the pattern. The evidence value of level-1 is the probe
  report (P1-P3): it proves which domain is alive, what PA the DPU still scans, and
  whether the pipe advances — exactly the inputs R2-R7 need.
- **Bonus (not planned):** if the glass DOES show the pattern, that means scanout
  was still live and repainting ABL's buffer hit a live pipe — photograph it
  immediately (panel-photo channel proven, EVIDENCE-MAP) and treat as a major
  discovery that reshapes the revive plan.
- **Fallback — a dark screen must NEVER be scored as boot failure.** The boot
  verdict comes from the USB serial console ladder + pmsg mirrors
  (`lagunalogdump=1`, `LAGUNA LOGDUMP: armed`) + ramoops stamps. Console to 983 s
  has already been retrieved before; the attempt is judged by §2.2 lines, not glass.

---

## 3. QEMU CROSS-CHECK (read-only, existing evidence)

**MiniIOMobileFramebuffer attach COMPLETES in QEMU — no gate stall on the iomfb path.**
Evidence: `qemu-validation/evidence/runC-late-v55-nvram/ring-live.bin` (v55 profile
run, exact v55 ADT), last line of the live ring:

```
[    0.558302]: MiniIOMobileFramebuffer: display 1125x2436 on a panel of 1280x2856 (5120 bytes per row)
```

preceded by `PMRD: tellChangeUp OFF_STATE->ON_STATE` (0.552) and `cpu_start() cpu: 0`
(0.556). The attach line is the ring's final line = the current QEMU frontier
(Q1 owns runs past it). **Post-attach stall signature** (P4, fleet COORDINATION): the
boot stays alive but stalled ~900 s at fb attach — prime suspect ADT-AIC `@0x5880000`
vs virt-GIC interrupt divergence, i.e. a platform-gap wall *after* attach, not an
ADT or iomfb defect. Notes:

- The 1125×2436 vs 1280×2856 mismatch is the ADT `disp0` canvas (mkadt QEMU branch)
  vs the loader's panel record — cosmetic in QEMU, but on device the canvas/mode
  should be 1280×2856×5120 or iomfb will adopt a wrong mode (P2-era item).
- The v55 QEMU cmdline carries `io.display.iomfb=1` (`runC-late-v55-nvram/
  handoff-plan-check.json`) — **inert** in this mach (no `laguna_fbswap` symbols);
  iomfb attached via the stock `boot_args.video` path anyway. Drop or document in v56.
- QEMU deliberately omits `/laguna-dpu`, so the DC9x00 gate takes its ADT refusal in
  QEMU (by design — external-abort protection). Level-1 paint evidence can only come
  from device, or from a future QEMU run with a window-mapped virt (not planned).

---

## 4. PREREQUISITES for v56+ (the build plan change)

1. **Port the DC9x00 series into the blazer foundation tree**
   (`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2`, the tree the
   usb-fix mach is built from). Source of truth: laguna141 re-cut
   (`research/laguna-panic-capture/kernel-wt` 0101/0102/0103 = 141ab67/5a8bd34/1c4d688)
   + 0126b's gate/ADT-contract rework (af99f29) + pmsg-mark guard. Keep: `/laguna-dpu`
   ADT gate, pmsg-mark guard, read-only probe whitelist, level-1 splash.
   **Adapt:** 0104's fbcon TTBR1-alias path does NOT port (the blazer mach has no
   laguna fbcon — console is serial/pmsg); the splash fixed-FB target must be the
   handoff record's `cpu_pa` (0xf0000000) — patch the FB-PA convention together with
   mkadt/mkpayload exactly as the README's "constants that must stay consistent" says.
   Hook points: `arm_init.c` after console-up, respecting the PAN re-arm order.
2. **mkadt.py device path: emit `/laguna-dpu`** (`hackios-xnu-kernel/blazer/teguload/
   mkadt.py`): `reg = <0x0ee00000, 0x120000, 0x0efd8000, 0x800>` (4×u64 absolute,
   fe-be + dsi2h0 windows, matching the kernel's built-in constants). Keep the QEMU
   ADT node-less (the gate must stay exercised in QEMU).
3. **Boot-args:** add `io.display.probe=1 io.display.splash=1` to the v56 shim
   command line; keep `splash=2` and `tgl_disp_snap/revive` OFF for attempt 1.
4. **Rebuild + repackage:** new mach sha → `build-xnu-payload.sh` input swap →
   `build-next-boot.sh` (Q4 owns out/next-boot-v56) → host tests (500-check re-run:
   PASS today) → QEMU smoke of the exact new mach via the existing runner (Q1/Q4 own).
5. **Pick up G3's current validator** (the tree already has it) so the cold record
   lands as `verdict=RAMCONSOLE display-unowned` instead of the mach's old
   headless-reject.
6. Decide `io.display.iomfb=1`: drop from the QEMU profile cmdline or keep + document
   as inert-until-0106-ports.
7. Evidence plan: expect §2.2 lines in `uart.log`/pmsg mirror; score the attempt on
   those; photograph the panel as bonus only.

---

## Summary (5 lines)

1. **AUDIT: PARTIAL — loudly NO for the paint path.** The running mach
   (sha b39179b5…) has the display handoff *consumer* (M2-era form) + stock
   MiniIOMFB + pmsg mirrors, but **ZERO DC9x00 symbols/strings**: the probe/splash
   series (0101/0102/0103, redwood 2334596/3f7ea43/16587c5) exists only as source —
   `io.display.probe/splash` are unparsed no-ops in this binary; a 500-check host
   re-run today passes, so only port+rebuild is missing.
2. **Level-1 recipe:** v56 cmdline `io.display.probe=1 io.display.splash=1` (never
   splash=2 on attempt 1; no tgl_disp_* in ABL FDT) + `/laguna-dpu` ADT node
   (reg fe-be 0xee00000+0x120000, dsi2h0 0xefd8000+0x800) emitted by mkadt on the
   device path only.
3. **Console proof:** `LAGUNA DC9x00: mapped fe-be …` → `probe: outpath0 ctrl=… en=… /
   layer[0] pa=0x… / dsi2h0 status=…` → `splash: painted live buffer …` →
   `blazer-display: verdict=RAMCONSOLE display-unowned …` → `cpu_start() cpu: 0` →
   `MiniIOMobileFramebuffer: display …`; ABSENCE of `ADT laguna-dpu node absent;
   MMIO probe skipped` (the historical gate-refusal line).
4. **Physical: panel DARK is the expected success-compatible outcome** (CDPHY off at
   handoff, proven in 6 captures; revive stops at R2 UNSUPPORTED); visibility =
   bonus → photograph; verdict comes from USB console + pmsg mirrors, never glass.
5. **v56 must change:** port DC9x00 series (+G3 validator, keep ADT gate + pmsg
   guard, drop fbcon-0104 dep) into the foundation tree → emit `/laguna-dpu` in
   mkadt device path → rebuild mach/payload (Q4) → QEMU smoke (Q1/Q4) → fire.
   Paths: `research/laguna-usb-fix/DISPLAY-READY.md` (this), patches
   `research/laguna-xnu-patches-display/`, source `research/laguna-panic-capture/
   kernel-wt`, tree `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2`,
   builder `hackios-xnu-kernel/blazer/teguload/mkadt.py`.
