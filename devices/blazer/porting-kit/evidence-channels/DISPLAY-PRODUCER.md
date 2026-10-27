# DISPLAY-PRODUCER — who paints after ABL jumps (gap #3: cold-path scanout + 128-byte handoff)

**Owner:** C3 (display/scanout) · **Date:** 2026-10-27 · **Host-only.** No device, fastboot,
adb, flash or QEMU action was performed. The shim tree and other lanes' files are untouched.
Read-only inputs: `research/laguna-render-port/DPU-INIT.md` + `RENDER-PORT.md` (M5),
`research/laguna-usb-fix/{ABL-RUNTIME-FACTS,DT-USB-FACTS,ABL-USB-STATE,EVIDENCE-MAP,TIMELINE-ANALYSIS,USERLAND-PATH,XNU-ENTRY-CONTRACT}.md`,
the 2026-10-27 session dumps under `research/session-20261027-usbboot/`, the 2026-10-23/25
ramoops captures, `research/SESSION-STATE.md` (DER Event 20), `/tmp/fleet/COORDINATION.md`,
and a fresh host-side `dtc` decompile of the authoritative blazer MP DTB
(`research/laguna-foundation/kernel-blazer/devices/blazer/fdt/laguna.dtb`, 896,033 B —
DT-USB-FACTS §1).

**Claim labels:** **[LOG]** device log fact from our saved captures · **[DT]** raw cell from
the blazer MP DTB (decompiled today) · **[DISASM]** ABL-USB-STATE static verdict · **[EVID]**
prior recorded evidence · **[DESIGN]** this document's proposal, never yet hardware-validated.
Everything here is a plan, not permission to run.

---

## 0. The problem, proven end to end

B4's gap #3 said "no CPM-powered scanout producer / 128-byte handoff on the cold path —
DPHY dies at the ABL transition." The panel-death half is now **proven from our own captures**,
not inferred:

**[LOG]** `research/session-20261027-usbboot/console-ramoops.txt`, **session 1 = the actual
shim boot** (`fastboot boot` path, v2-cycle; the same session B2/EVIDENCE-MAP §2 correlated):

```text
[   1.494198] <0> [I] [DSI2] Start DSI-0 Init, autoMode Enable: 0, ctrlMode: 0
[   1.519471] <0> [I] [DSI2.CORE_MODE] DSI2 Core operating mode is set: 4
[   1.536139] <0> [I] [gs-panel] panel:google-bzea, id:42a1a00a, rev:05010000
[   1.718060] <1> [I] [DISP] display[0]:width:1280
[   1.718148] <1> [I] [DISP] display[0]:height:2856
[   2.936133] <0> [I] [FB] Accept cmd:download:04000000
[   4.460118] <0> [I] [FB] Accept cmd:boot
[  15.699539] <1> [I] [DSI2.mipi_cdphy] MIPI DPHY Power-off     ← THE PANEL LINK DIES
[  15.736956] <0> [I] [LGA] Handled NOTIFY_SYSTEM_HANDOFF event
[  15.740453] <0> [I] [AND] Starting Linux kernel ...            ← jump to the shim
```

The MIPI CDPHY is powered off **41 ms before the kernel jump**, on the `fastboot boot`
handoff path, in the very session our stamps analyzed (shim `t0` = 15.95 s, Δ≈0.2 s after
the jump — EVIDENCE-MAP §2). Reproduced in every other saved capture:
`session-20261023-ladder{2,3,4}` (33.49–33.58 s), `session-20261025-212520` (33.49 s),
`cr-latest.txt` (33.51 s, followed by `[UFS] shutdown complete` + `NOTIFY_SYSTEM_HANDOFF`).
RENDER-PORT.md §0 already flagged the same line ("the captured handoff logs include
`MIPI DPHY Power-off`").

Consequences, in order:

1. **The shim's paint cannot have been visible.** The shim writes `0xf0000000` from 15.95 s
   onward — 250 ms after the DSI link dropped. A-TIME's proof (fleet COORDINATION, 2026-10-27)
   established `0xf0000000` is real unreserved bank-0 DRAM and the paint completes (~11 µs/
   store uncached; v33 fixed entry→paint-done 26.5 s → 2.4 s), but TIMELINE-ANALYSIS §4
   explicitly recorded that *pixel visibility was never verified on any run*. Now it cannot
   have been: the bytes land in DRAM, the pipe to the glass is off. The framebuffer content
   survives; the delivery does not.
2. **XNU inherits a dead display contract.** `boot_args.video` = `0xf0000000`, 1280x2856,
   rowBytes 5120, depth 32 (XNU-ENTRY-CONTRACT §3.2, `loader.c:160-167`) — correct DRAM,
   no scanout behind it. MiniIOMFB would CPU-copy every frame into a buffer nothing reads.
3. **ABL itself needs a full re-own after handoff.** The park session (abl.log 23.25 s /
   ramoops session 2) re-runs the whole ladder: `[DSI2] Start DSI-0 Init` →
   `DSI2.CORE_MODE set: 4` → `[gs-panel] panel:google-bzea` → `[DISP] display[0] 1280x2856`
   → CPM firmware load (`cpm successfully loaded (528384 bytes)`). That is the textbook
   demonstration that "someone re-owns it" is a full pipeline operation, not a register poke.

### 0.1 What survives the ABL jump vs what dies

| Item | State at shim entry | Basis |
|---|---|---|
| CPM/MBA mailboxes | Live per static analysis — ABL never quiesces (mbox 0x06250000 ×6ch, 0x0e000000 ×1ch, 0x05250000 ×1ch ids 0x12/0x13) | **[DISASM]** ABL-USB-STATE §5 |
| CPM firmware | Loaded by ABL at boot (`cpm successfully loaded`, every session); shim must stand up **transport**, not load FW | **[LOG]** ABL-RUNTIME-FACTS |
| Cold MBA transport read | TX/RX geometry reads return `0x0/0x0` → `cpm-geo REFUSED` | **[LOG]** v34b stamps, ATTEMPTS.md — A-CPM's flagged missing layer (MBA-syscon `0x05250000` bring-up) |
| DPU/DSI/CDPHY power domains | Unknown per-domain; the only display-side teardown **seen** is the CDPHY power-off; no `sswrp_dpu_pd` off line exists in any capture; the 30 handoff notify callbacks contain zero power-domain calls | **[LOG]** §0 + **[DISASM]** ABL-USB-STATE §4 |
| MIPI CDPHY lane state | **OFF** — powered down pre-jump | **[LOG]** §0 |
| DSI2 host / FE / BE register state | Not claimable without DC9x00-class readback → treat as dead, reprogram | DER-20 discipline |
| DRAM at `0xf0000000..+0xdf2000` | Survives; our pixels persist through park/reset (no DRAM scrub observed) | **[EVID]** A-TIME |
| Panel power rails (vci/vddi/vddd) | No regulator-off in any capture; panel loses MIPI only (likely self-sleeps after link loss) | **[LOG]** absence + **[DT]** panel node §1 |
| DPU SMMU (`dpu_smmu@0xed80000`) stream mappings | Unknown; cannot be assumed | **[DT]** §1 + DPU-INIT §6.3 |

---

## 1. Facts inventory (the display chain, from OUR device tree)

Host-side `dtc -I dtb -O dts` decompile of the blazer MP DTB (DT-USB-FACTS §1's authoritative
source), 2026-10-27. Raw cells; phandles read back from the same file.

### 1.1 Pipeline geometry (all [DT])

| Node | reg (child → PA) | Key cells |
|---|---|---|
| `/sswrp_dpu@ec00000` | ranges `<0 0 0xec00000 0x400000>` | simple-bus parent of the whole display block |
| `dc9x00@200000` (the DPU) | → **`0xee00000`** +0x120000, gpcsr `0x390000` → `0xef90000` | compatible `verisilicon,dc9x00`; 36 interrupts incl. `fe0_layer_frm_done0..5`, `outpath0_frm_start/done/underrun`, TE edges; 22 IOMMU specifiers |
| `dpu_fe0@0` / `dpu_fe1@40000` / `dpu_be@80000` | within DPU | `fe_id=<0>`/`<1>`; the FE0-L0 address words live at DPU+`0x18060/0x18064` (DPU-INIT §6.7) |
| `dsi2h0@3d8000` (DSI0 host) | → **`0xefd8000`** +0x800 | `lanes=<4>`, `vid_mode_type=<2>`, `datarate=<0x56e>` (=1390), `lp2hs=<0x9b00d4>`, `hs2lp=<0x6a80cc>`, `tear_effect=<1>`, `mode_ctrl=<3>`, `clk_type=<1>`, irq `dsi0_irq` |
| `dw_mipi_g301_cdphy0@3c0000` (DSI0 PHY) | → **`0xefc0000`** +0x18000, regbank `0x3d8800` → `0xefd8800` | `synopsys,dw-mipi-cdphy-v1`, `is_cphy=<0>`, clock `pllref` |
| panel `panel-gs-bzea` | — | `google,gs-bzea`, `reset-gpios`, `vci/vddi/vddd-supply`, `allowed-hs-clks = <0x56e 0x582>` (1390/1410); ABL sees id `42a1a00a` rev `05010000` **[LOG]** = cmdline `gs_drm_connector.panel0_name/serial` |
| `dpu_smmu@ed80000` | **`0xed80000`** +0x40000 | `arm,smmu-v3`, dma-coherent, `non-coherent-ttw`, max-SID 8 bits, powered by `sswrp_dpu_pd` |
| DPU GIA `dpu-level-aggr-20-intr@ef88000` | `0xef88000` | `google,wide-level-gia`, GIC SPI **0x334 = 820** — the frame-done/underrun source (DPU-INIT §6.9) |

Stream IDs (dc9x00 `iommus`/`iommu-names`): FE0 layer0 = **0x01**, panel0_read = **0x0f**,
blender = 0x14, output0 = **0x17**. Mode **1280x2856** — proven in every ABL session
(`[DISP] display[0]:width:1280 / height:2856`); 1280×2856×4 = **0xDF2000 bytes** = exactly
DPU-INIT §5.1's allocation constant; stride **5120**.

### 1.2 Power topology is the CPM mailbox, not MMIO ([DT]+[DISASM])

`/power_controller` is `google,lga-power-controller` with `mboxes = <0x29>` — the
`goog_mba_cpm_iface` mailbox (DT-USB-FACTS §3). The DPU domain chain hangs off it:

| PD node | sswrp-id | parent | subdomain-id | phandle |
|---|---|---|---|---|
| `sswrp_dpu_pd` | 5 | — (top) | — | 0x2a |
| `dpu_dsi0_pd` | 5 | sswrp_dpu_pd | **2** | 0x3c |
| `dpu_fe0_pd` | 5 | dpu_dsi0_pd | **0** | 0x3e |
| `dpu_fe1_pd` | 5 | dpu_fe0_pd | **1** | 0x3f |
| `dpu_dsi1_pd` / `dpu_dp_pd` | 5 | sswrp_dpu_pd | 3 / (dp) | 0x3d / — |

This **exactly** matches DPU-INIT §4's golden frames (DPU top ON → DSI0 ON subdomain 2 →
FE0 ON subdomain 0 → FE1 ON subdomain 1; LPB wire ID 5, LPCM wire ID 0, source service 7,
LPCM destination 8). ABL's own CPM use is the same transport (ABL-USB-STATE §5:
`0xbe031874` acquire / `0xbe03199c` release / `0xbe031c10` domain on/off; its boot log
`Sending init GMC DVFS command to CPM with PfState7`). **Corollary:** every display
power question — on, off, or state-check — is answerable through the mailbox; no DPU MMIO
is required to establish power state.

### 1.3 The readback hazard is measured, not hypothetical

- teguload v2's DC9x00 scanout-probe readback = **DER Event 20**: async SError → RAS →
  `0xc00e` crash reset ~3 s past ABL; v3 (probe skipped) lived +65 s into the kernel phase.
  Probe confirmed as the trigger; discipline = default-off, boot-arg gated
  (SESSION-STATE.md:1037-1076).
- Even the *corrected* read path is dangerous when power is unproven: the one-page
  PROT_READ CHIP_ID mode on Android mapped fine and then died on the load with async
  SError `0xbe000011` / DPU-config fabric target error (SESSION-STATE.md:114-120,
  `session-20261024-reuse/cycle2/result.json`). Power state was never established first.
- The DPU sits behind `dpu_smmu` and `sswrp_dpu_pd`, both mailbox-managed: **never
  blind-read power-managed IP** — the DC9x00 is the canonical example.

---

## 2. Phase plan — three phases, one owner per phase

```text
P1 (shim)          P2 (XNU cold consumer)            P3 (SpringBoard era)
revive-or-record   validate + adopt, fail closed     full present path
──────────────►    ────────────────────────────►     ────────────────────────
console-init:      handoff record → boot_args/ADT    CA swaps → MiniIOMFB copy
snapshot (gated)   validator already linked (M2)     → live scanout @60 Hz
CPM revive (gated) MiniIOMFB adopts record           native producer = Path B later
128-byte record    no DC9x00 MMIO unless native      frame-done proof (GIA 820)
```

### 2.0 Gate ladder (extends DPU-INIT §2 / RENDER-PORT §3 — unchanged entries restated)

| Gate | Permits | Where |
|---|---|---|
| baseline (today) | handoff **record building** (pure DRAM + payload), headless XNU | shim + kernel, no new access |
| `blazer_cpm=1` (+ A-CPM cold fix) | CPM client mapping/geometry reads | XNU/shim CPM client |
| `blazer_cpm_tx=1` | queue init + DPU-INIT §4 power frames (CPM only) | whoever re-owns power |
| `tgl_disp_snap=1` **[DESIGN]** (shim boot-flag, default **off**) | P1 readback-gated DC9x00/DSI/CDPHY snapshots, only after power proof | shim console-init |
| `tgl_disp_revive=1` **[DESIGN]** (default **off**) | P1 revive: §4 frames + re-init of FE0/DSI0/CDPHY/panel | shim, after snap gate |
| `io.display.native=1` (after review) | native DPU/SMMU/modeset (Path B §6) | XNU producer, P3-era |

`io.display.probe=0` and `io.display.splash=0` remain set in every profile. `gpu=1`,
`fb_scan=1` are never valid. Snap/revive default-off is the DER-20 lesson applied to
ourselves: an async SError in the shim's 40 s photo window is a lost evidence cycle.

---

## 3. P1 — shim side: keep the panel alive through the transition

Reality check first: **we cannot prevent the DPHY power-off** — ABL performs it inside
`NOTIFY_SYSTEM_HANDOFF`, before the shim exists. "Preservation" therefore means:
(a) preserve the *knowledge* of the pipeline (snapshot + record) at console-init,
(b) re-own the pipeline as early as possible (revive), and (c) hand XNU a record that
distinguishes "live scanout" from "RAM buffer" — fail-closed.

### 3.1 What must be preserved, itemized

| Asset | Mechanism | Notes |
|---|---|---|
| Framebuffer `0xf0000000` + content | keep painting; reserve from XNU window | already in XNU-ENTRY-CONTRACT §3.5 item 3; 0xDF2000 bytes, ends exactly at the DPU MMIO fence `0xf0000000` |
| Pipeline *knowledge* (mode, lanes, timings, SIDs, PD chain) | static — this document + DT | nothing to read back for these; DT + ABL logs are the source |
| FE0/DSI2H/CDPHY *live* register config | readback-gated snapshot (§3.3), **only** to verify/refute hypotheses | the old config is not trusted for reuse; snapshot informs, never authorizes blind rewrites |
| Panel identity | `[gs-panel] panel:google-bzea, id:42a1a00a, rev:05010000` — log + DT props | init *command stream* is vendor-driver content, not in the DT (open item D3) |
| CPM/mailbox liveness | A-CPM cold fix (dependency D1) | prerequisite for every power action, revive or state-check |

### 3.2 The 128-byte handoff block — what the shim writes, what XNU reads

Wire layout is **frozen by DPU-INIT §7** (`struct laguna_display_handoff`, magic
`LGDSP001`, CRC-32, all single-bit corruptions rejected by the linked validator — M2's
`hakios_blazer_display_init()` already consumes it; fleet COORDINATION "DISPLAY
INTEGRATION COMPLETE"). The shim-side fill rules are the new content:

| Field | Shim value (cold path) | Assertable? |
|---|---|---|
| `producer[16]` | `"shim-cold"` | yes — provenance is the shim itself |
| `cpu_pa` / `cpu_size` | `0xf0000000` / `0xDF2000` | yes **[EVID]** A-TIME: real bank-0 DRAM |
| `width/height/stride/pixel_format` | 1280 / 2856 / 5120 / XR24 (linear 32bpp) | yes — matches ABL's own mode and our paint format |
| `mapping_kind`, `dpu_iova` | `IDENTITY` requires `dpu_iova == cpu_pa`; on the cold path set `dpu_iova=0` + `mapping_kind=NONE` unless the revive path actually proved a DPU mapping | no mapping claim without proof (DPU-INIT §7: identity needs both; FIRST_STAGE_SMMU needs IOMMU_RETAINED) |
| `cache_kind` | WC (shim paints with MMU off, uncached stores) | yes |
| `commit_generation` | 1 first write; +1 on every later re-own/modeset | yes |
| `flags` — assertable cold: | `CPU_PA_VALID`, `CPU_SIZE`, `LINEAR_32BPP`, `PHYS_CONTIGUOUS`, `RESERVED` | yes — all provable from shim state |
| `flags` — cold path must NOT set: | `DPU_CONFIGURED`, `POWER_RETAINED`, `IOMMU_RETAINED`, `SCANOUT_LIVE` | **fail-closed**: only the revive path may set them, and only after its own proofs (§3.4) |
| `refresh_millihz` | 0 until a measured refresh exists (vsync on this path is a software beat, not a panel-mode claim — USERLAND-PATH M7) | honest zero |

Transport: the record page is embedded in the payload next to the ADT (mkpayload/mkadt
territory — S1's tree); the shim stamps its PA into the ADT `/chosen` property
`laguna-display-handoff` = `{PA,size}` (DPU-INIT §5.2's suggested form), and mirrors the
same 128 bytes into a reserved DRAM page **outside** `0x95200000..0x95600000` (ramoops +
stamps + mirrors live there; B4's mirror is at `0x95440000` — pick a fresh 4 KiB-aligned
PA and coordinate with the kernel lane's memory-map carveout). XNU reads: validator first
(CRC/flags/geometry/forbidden-ranges), then `boot_args.video` only if the record permits;
else headless with stamps (already the linked behavior: "forces invalid records headless").

**Recovery story:** the record lives in DRAM that survives the shim's PSCI reset bounce and
the park — after any outcome, the parked ABL + `kernel.log` aperture can be augmented to
dump the record page next to the stamps (B2/EVIDENCE-MAP owns the channel layout; ask, do
not take their offsets).

### 3.3 P1 preservation checklist — snapshot at console-init, re-write if dead

**Order is the safety mechanism.** Every MMIO item is readback-gated per DER-20; nothing
is read or written until its power state is proven *by the mailbox*, and every access is
stamped before and after (the CPM-FIRST-SPEC §6.3 stamp-before-first-poll pattern).

Pre-conditions for any display MMIO at console-init (all must hold, else skip to record
building and stamp why):

1. `tgl_disp_snap=1` (or `tgl_disp_revive=1`) present — default is **no display MMIO at all**.
2. CPM transport alive (post-D1) and one outstanding-request discipline (CPM-FIRST-SPEC §4).
3. Per-domain power proof via LPCM **state query / power frames** (DPU-INIT §4 words;
   response payload `[15,15,5]` = on/success, `[15,19,5]` = `NO_EFFECT` → **indeterminate:
   stop**, never probe to "check").
4. DAIF state explicit; stamp `DISP_SNAP ENTER` with the domain bitmap before the first
   read; stamp each register's value into the stamp arg (RAM stamps are the only telemetry
   on a dead-USB boot — CPM-FIRST-SPEC §6.5).

Snapshot list — read **only**, one shot, values into stamps + the diag ring; purpose is to
verify/refute the two hypotheses "FE0 still points at ABL's scanout buffer" and "our
0xf0000000 ≠ ABL's buffer":

| # | Where | What | Gate |
|---|---|---|---|
| S1 | DPU `0xee00000` + `0x18060/0x18064` | FE0-L0 address words (DPU-INIT §6.7) — is the live source our buffer? | `sswrp_dpu_pd` + `dpu_fe0_pd` proven on |
| S2 | DPU + FE0-L0 CONFIG/size/stride words | format, tile/swizzle bits (CONFIG bits 29-24/19-16/13-12 per reuse-audit display.md) | same as S1 |
| S3 | DPU outpath0 `CURRENT_COORD` `0xb6068` | is output0 actively scanning? (reuse-audit: corrected offset) | `sswrp_dpu_pd` on |
| S4 | DSI2H0 `0xefd8000` core-mode block | operating mode (ABL sets **4** [LOG]) + link status | `dpu_dsi0_pd` proven on |
| S5 | CDPHY0 regbank `0xefd8800` | lane/PLL state after ABL's power-off | `dpu_dsi0_pd` proven on |

If the panel is dead (it **is**: CDPHY off at 15.699 s [LOG] §0), the **re-write list** is
the revive sequence — each step gated, ordered, and stopping on first refusal:

| # | Action | Content | Proof before next |
|---|---|---|---|
| R1 | CPM queue reset + DPU power frames | DPU-INIT §4 order 0-3: `00000100…` → top ON `a0010705 00000002 00000005 00000001` → DSI0 ON `a0020708 01020001 …` → FE0 ON `a0030708 01000001 …` (FE1 `a0040708 01010001 …` only if used) | correlated LPCM payload==0 or immediate `[15,15,5]`; `NO_EFFECT` ⇒ stop, mark indeterminate |
| R2 | Clock-domain isolation + resets | vsi-drm source order (`vs_dc_res_enable` / `dc_hw_enable_clock_domain_iso(false)` as spec — DPU-INIT §6.5) | source-equivalent completion; unused IRQs stay masked |
| R3 | FE0-L0 program | addr `0xf0000000`, linear modifier, stride 5120, 1280x2856, XR24; through the driver-equivalent plane path, **not** isolated word writes | shadow semantics honored |
| R4 | Commit + outpath start | `dc_hw_enable_shadow_register()` commit, then `dc_hw_set_output_start()` — never the old splash repoint / `0xc4` write / DECON `0x3061` values | commit accepted |
| R5 | DSI2H0 + CDPHY re-init | mode_ctrl 3, lanes 4, `vid_mode_type 2`, datarate 1390, `lp2hs 0x9b00d4` / `hs2lp 0x6a80cc`, TE on; CDPHY power-up + lane state (PPI per `synopsys,dw-mipi-cdphy-v1`) | DSI link up; this is DPU-INIT §6.6's mandatory cold step |
| R6 | Panel re-init | `reset-gpios` sequence + `gs-bzea` init command stream (open item D3 — vendor panel driver extraction; NOT derivable from DT) | panel acks / DSI read path responds |
| R7 | DPU SMMU mapping for FE0-L0 + output0 | SIDs 0x01/0x0f/0x17 at `dpu_smmu@0xed80000`; identity `IOVA==PA` chosen deliberately; if no safe SMMU owner: **stop** (DPU-INIT §6.3) | mapping live before enabling scanout |

Liveness proof (no IRQ framework in the shim): paint pattern A → bounded wait → paint B →
photograph both states through the proven panel channel (EVIDENCE-MAP "panel paint":
photograph before any power cycle). Only after R7 + a photographable alternation does the
record earn `DPU_CONFIGURED` + `POWER_RETAINED` + `SCANOUT_LIVE` with `commit_generation`
bumped; XNU later adds the real frame-done proof via GIA SPI 820 (`outpath0_frm_done`,
DPU-INIT §6.9) when the native path lands.

**Never list (all measured failure modes):** blind reads of unproven-power DC9x00 (DER-20);
readbacks as safety decisions (DPU-INIT §3.5: timeout/malformed ⇒ fail closed, no
compensating MMIO); `NO_EFFECT` treated as success; active/shadow bank reads for reuse;
the splash repoint; any write while `dpu_dsi0_pd` state is merely *assumed*; retry loops
around a faulting modeset (one attempt per boot, park posture — CPM-FIRST-SPEC §4.6).

### 3.4 P1 dependency list

- **D1 (blocking for revive; NOT for record building):** A-CPM cold mailbox fix — v34b
  proved `cpm-geo REFUSED 0x0` (MBA-syscon `0x05250000` layer); counter-evidence to chase:
  ABL statically binds `0x05250000` as a live mailbox (ids 0x12/0x13) and never quiesces it
  (ABL-USB-STATE §5) — the shim's geometry read may be pointed at the wrong register shape
  rather than a dead mailbox. Gap-3 and gap-transport share this work (B4 COORDINATION).
- **D2 (S1 shim tree):** record builder + stamps (`DISP_SNAP/POWER/REVIVE/LIVE`, ids
  continuing `tgl_stamp.h` v3.4 table), boot-flags `tgl_disp_snap/revive`, ADT `/chosen`
  property, record-page PA reservation. Host-only spec above; S1 wires.
- **D3 (research):** `gs-bzea` panel init command stream from the vendor panel driver
  (the DT has identity + rails + gpios, not the DSI init sequence ABL sends).
- **D4 (kernel lane):** memory-map carveout for the record page; keep `0xf0000000`
  excluded (already in XNU-ENTRY-CONTRACT §3.5); MiniIOMFB cache hook (WC⇒DSB,
  `CLEAN_ON_PRESENT`⇒flush) — already linked per M2.

---

## 4. P2 — XNU side: MINIOMFB/producer re-init, gated fail-closed

The consumer half already exists (M2, fleet COORDINATION): `hakios_blazer_display_init()`
consumes `/chosen/laguna-display-handoff` + `/chosen/laguna-display-forbidden`, excludes
kernel/record/ramoops ranges, forces invalid records headless, preserves GOP/ramfb when no
record exists, and implements the cache hook. What remains is the *cold-path decision tree*:

1. **Record present + valid + `SCANOUT_LIVE`** (only possible if P1 revive ran): populate
   `boot_args.video` from the record; MiniIOMFB adopts via `IOMobileFramebuffer::start()`
   (`vinfo.v_baseaddr`, geometry, TimingElements, service `disp0` — RENDER-PORT §1);
   honor `cache_kind` before declaring frames visible; revalidate on any
   `commit_generation` change (DPU-INIT §7).
2. **Record present but not `SCANOUT_LIVE`** (the *expected* cold-path state until revive
   is proven): XNU may use `0xf0000000` as its RAM console exactly as today
   (XNU-ENTRY-CONTRACT §3.2 — never point video at an unproven scanout), but must publish
   "display unowned" in its stamps/mirror so the ladder (USERLAND-PATH M-rungs) records
   the truth. No DC9x00/DSI MMIO from XNU at this stage, period.
3. **No/invalid record:** headless (current linked behavior).
4. **Native producer re-init** (the "re-init per DPU-INIT frames" reading of the mission):
   XNU executes CPM §3-4 (transport + top/DSI0/FE0/FE1 frames with `include_fe1=false`)
   **only** under `io.display.native=1`, then DPU-INIT §6 steps 2-9 (own allocation,
   SMMU streams, clocks from real providers, DSI2H/CDPHY/panel init, modeset via shadow
   commit, outpath start, GIA SPI 820 frame-done proof) — each step a separately reviewed
   gate, stopping at the first unimplemented provider ("If a safe SMMU owner is not
   implemented, stop."). This is Path B, deliberately **later** than P1: on the cold path
   the shim is the natural re-owner (it is already resident at console-init, it already
   has the paint loop, and a XNU-side producer adds MMIO risk inside the fragile
   pre-`arm_vm_init` window).
5. **Fail-closed invariants** (both phases): CRC alone is never a safety decision;
   validator checks against every forbidden range; unknown flag bits rejected; any
   timeout/malformed LPCM reply ⇒ stop, mark indeterminate, never probe.

## 5. P3 — SpringBoard-era: the full present path

Proven in QEMU on the blazer platform (N14 `0a7313a`; USERLAND-PATH M7, fleet INDEX):
CA SwapBegin → SetLayer → SwapEnd, MiniIOMFB presenter CPU-copies/scales IOSurfaces into
`boot_args.video`, vsync = selector-72 timer (60 Hz software beat — explicitly not a
panel-mode claim), **present swaps 2–7 ms continuous**, renderer = Mesa softpipe
(`GL=1` build, `gl=1 glenv=OSMESA_SOFTWARE=1 glwait=1`; **never** `gpu=1` on Tensor).

What P3 adds on the phone once P1/P2 deliver live scanout:

- **Real vsync/liveness:** GIA SPI 820 `outpath0_frm_done` as the frame-done proof
  (DPU-INIT §6.9); underrun ⇒ stop presents, mark the record not-live, no retry storm.
- **Generation discipline:** producer bumps `commit_generation` on each modeset; MiniIOMFB
  revalidates before adopting (DPU-INIT §6.10).
- **Render gates on silicon:** RENDER-PORT §4 gates 0-6 in order, plus the panel
  discrimination rule — "BootUI/logo frame" ≠ SpringBoard content (USERLAND-PATH M7).
- **Later optimizations, in order:** direct scanout of IOSurfaces (skip the CPU copy),
  native Path-B producer owning its own buffer, panel brightness/backlight ownership
  (open item — no backlight/PWM node in the bzea DT snippet; likely DSI-command or
  rail-controlled; must be extracted with D3).
- **Panel-era risks to budget:** panel self-sleep after the ~15.7 s link loss — R6 may
  need the full wake sequence, not just DPHY re-up; TE (`tear_effect=1`) gating for
  present pacing once a real panel beat is wanted.

---

## 6. Verification ladder

1. **Host, now:** DPU-INIT's 270/270 validator checks already cover the record; add
   host tests for the shim-fill table (§3.2): cold flags set, live flags absent,
   `dpu_iova=0`+`IDENTITY` rejection — same single-bit-corruption rigor.
2. **QEMU:** stamp-trail dry-run of the decision tree (record build → flags → consumer
   gate) with the DC9x00 paths stubbed; the ramfb present path keeps passing gates 0-6.
3. **Device, gated rungs:** (a) baseline boots keep building the record + stamps — zero
   new MMIO; (b) `tgl_disp_snap=1` boots: only after CPM alive + power proofs — stamps
   carry S1-S5 values; verdict = the snapshot proves or kills the "FE0 source ==
   0xf0000000" hypothesis; (c) `tgl_disp_revive=1`: R1-R7 with photograph proof; the
   panel photographing channel is already proven (EVIDENCE-MAP); (d) only then P2/P3 on
   the phone ladder.
4. **Evidence integration:** revive outcome lands in ATTEMPTS.md verdicts via new stamp
   ids; the record page dump rides the park retrieval (ask B2/EVIDENCE-MAP owner).

---

## Summary (5 lines)

1. PROVEN [LOG]: ABL powers off the DSI2 MIPI CDPHY at `NOTIFY_SYSTEM_HANDOFF` — 15.699539 s vs jump 15.740453 s in the actual shim boot capture (reproduced in 5 more sessions) — so the shim's `0xf0000000` paint was never panel-visible; the pipeline needs full re-own (park-session ABL re-init is the demo).
2. Chain grounded [DT] (blazer MP DTB, decompiled today): DPU `dc9x00@0xee00000`, DSI2H0 `0xefd8000` (4 lanes, vid_mode_type 2, datarate 1390), CDPHY0 `0xefc0000`, panel `gs-bzea` id 42a1a00a, `dpu_smmu@0xed80000` (FE0-L0 SID 0x01, output0 0x17), GIA `0xef88000` SPI 820; all DPU power = CPM mailbox (`google,lga-power-controller mboxes=<0x29>`) matching DPU-INIT §4's exact top/DSI0/FE0/FE1 frames.
3. P1 (shim): snapshot-at-console-init + 128-byte handoff (DPU-INIT §7 layout; shim asserts only CPU_PA/LINEAR/CONTIG/RESERVED flags — never DPU_CONFIGURED/SCANOUT_LIVE without the revive path's proofs), with an S1-S5 readback-gated snapshot table and R1-R7 revive ladder, all DER-20 readback-gated: mailbox power proof before any MMIO, one attempt per boot, `NO_EFFECT` ⇒ indeterminate ⇒ stop.
4. P2 (XNU): the linked `hakios_blazer_display_init()` validator decides fail-closed (live ⇒ adopt into boot_args.video + MiniIOMFB with cache hook; not-live ⇒ RAM console + "display unowned" stamps, zero DC9x00 MMIO; native producer only under `io.display.native=1` per DPU-INIT §6, deliberately later than the shim).
5. P3 (SpringBoard-era): QEMU-proven present path (MiniIOMFB CPU-copy, selector-72 vsync, 2–7 ms swaps, softpipe via `gl=1`, never `gpu=1`) rides the revived scanout; adds GIA frame-done liveness, commit_generation discipline, underrun stop policy; blockers D1 (A-CPM cold mailbox, shared with gap-transport) and D3 (gs-bzea panel init stream) named with owners.
