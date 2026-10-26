# Display and render bring-up (`blazer` / Tensor G5 "laguna")

This is the laguna display/render guide: how a frame gets onto this panel and
how CoreAnimation draws into it. It folds the host-tested DPU power/scanout
plan and the renderer integration into one place, with honest labels:
**[QEMU]** means proven on the QEMU harness, **[host]** means host-tested
code/contract, **[device-pending]** means designed but never run on the phone.
Nothing here authorizes a device run on its own — read `docs/SAFETY.md`
first; the DC9x00 lesson in there is the reason half this file exists.

## 0. Where things stand

**[QEMU]** The stock iOS lock screen renders on the laguna platform: the
blazer-branch kernel (source-integrated tree, built with the `docs/BUILD.md`
recipe) boots the verified userland image on the upstream QEMU harness to a
composited, updating lock screen — wall-clock minute ticks, zero kernel
panics, SpringBoard startup 26.75 s, pixel-match 2.9–3.1/255 against the
independent control boot. Frames flow through stock backboardd/SpringBoard →
`MiniIOMobileFramebuffer`'s CPU-copy presenter into `boot_args.video`. On that
harness the scanout is EDK2's ramfb adopted through GOP — the
"producer-owned linear buffer" role, with no DC9x00 access anywhere
(`io.display.*=0`).

**[device-pending]** Everything that touches the actual DPU: the CPM mailbox
power-on, the first-stage scanout handoff, and the `BlazerPlatformExpert`
engagement (the QEMU loader synthesizes an iPhone-identity ADT, so the
`google,lga-blazer` expert cannot match there — it engages on the first-stage
ADT contract, `docs/BOOT-CONTRACT.md` §2).

## 1. Two independent graphics paths — do not conflate them

1. **Scanout/present** — something owns the DPU and keeps a linear 32-bpp
   framebuffer being fetched. On laguna that "something" is a display-owning
   first stage (Google's own CPM/mailbox, power-controller, SMMU and vsi-drm
   drivers, state preserved across kexec) — or, later and gated, a native XNU
   producer. XNU never discovers a framebuffer by probing hardware.
2. **Render** — backboardd/CoreAnimation draws into IOSurfaces through the
   EAGL/GLI bridge. On laguna this is **Mesa softpipe on the CPU**.

**Never set `gpu=1` on Tensor G5.** That argument enters the Qualcomm
Adreno/GMU/zap/RPMh driver, which does not exist on this SoC. Build with
`GL=1`, boot with `gl=1`, and let Mesa fall back to softpipe
(`glenv=OSMESA_SOFTWARE=1` makes the software choice explicit).

**Never read DC9x00.** On real silicon a DC9x00 register readback is an
asynchronous SError classified as RAS and crash-resets the phone (reboot
reason `0xc00e`, "DER Event 20"). It looks harmless in QEMU and is not. All
probe/splash/readback paths stay default-off forever: `io.display.probe=0
io.display.splash=0` are in every profile, and there is no stage at which
`io.display.probe=1` is required.

```text
first stage (vendor drivers)                      backboardd / QuartzCore
  CPM/goog-mba -> DPU/DSI powered                    |
  dedicated linear scanout allocated                 v
  kexec preserves DPU/SMMU/buffer          inject.c loads libhakios.gl.dylib (gl=1)
        |                                            |
        v                                            v
  128-byte CRC'd handoff record            EAGL/GLI shim -> Mesa softpipe ES3
        |  (CPU PA + DPU IOVA + geometry             |
        |   + cache + ownership + live)              v
        v                                   surfaces drawn into MiniIOSurfaces
  XNU validates, fills boot_args.video               |
        |                                            |
        v                                            v
  MiniIOMFB presenter  <-------- CA SwapBegin/SetLayer/SwapEnd
  (CPU copy/scale into the scanout, cache policy honored)
```

## 2. Gates and boot arguments [host]

Every hardware stage has its own explicit gate. A gate allows a narrow list of
work and forbids the rest; timeouts and malformed responses fail closed.

| Gate | Arguments | Allowed | Forbidden |
|---|---|---|---|
| OFF (baseline) | `io.display.probe=0 io.display.splash=0 io.display.native=0 blazer_cpm=0 blazer_cpm_tx=0` | Handoff validation, Mini*/softpipe on a previously valid buffer, host tests | CPM writes, DPU access, Adreno calls |
| CPM read-only | `blazer_cpm=1` (TX still 0) | Map TX `0x05260000` / RX `0x05261000`, read ring capacities and IRQ config | Queue init, doorbell, power requests, DPU access |
| CPM power | add `blazer_cpm_tx=1` | IRQ config, `0x100` queue reset, the exact power frames below | Any DPU access before power and a reviewed modeset exist |
| Native scanout | add `io.display.native=1` **after review** | Own allocation, DPU SMMU mapping, source-faithful modeset | Old probe/splash paths, guessed register constants |

`gpu=1`, `adreno=*` and `fb_scan=1` are never valid laguna settings.

## 3. CPM transport sequence (goog-mba v2) [host]

The saved live FDT supplies TX `0x05260000`, RX `0x05261000`, global
`0x05250000`, four-word payloads, eight TX and four RX slots. The in-kernel
transport is `xnu/files/bsd/dev/hakios.blazer_cpm.c` (+ `.h`), gated as above.

1. Map only the two client pages — never the host-private region. Read
   `MBOX_BUFFER_WORDS` at client+`0x34`; only if implausible use the
   documented global fallback. Capacities must be nonzero, ≤256 and multiples
   of four words. Stop on mismatch.
2. At the TX gate, set IRQ config bits 8/16/24 (automatic host ACK +
   message/ACK masks). Polled XNU does not route SPI250/251 in this phase.
3. Send the four-word queue reset `{0x00000100,0,0,0}` at the current TX
   index, ring the doorbell (client+0, value 1), wait for TX-done. This is an
   init message, not a CPM service request; on acknowledgement reset next
   TX/channel indices and sequence to 1. Never substitute a power request for
   this handshake.
4. One request at a time (token 0 is safe). Clear stale ACK before reading
   the outstanding count; require queue space; write four words,
   advance/wrap TX, ring once; drain the RX payload before W1C
   acknowledgement; correlate the response header source/destination/token/
   type before using its three service payload words.
5. Timeout or malformed/stale response ⇒ fail closed. Do not issue a
   "compensating" MMIO read to check whether power came on.

## 4. Exact DPU power-on frames [host]

Logical DPU DT ID 5, LPB wire ID 5, LPCM wire ID **0** (not 5). Source
service 7 (AP power-controller), LPB destination 5, LPCM destination 8.
Header = `0xa0000000 | token<<20 | sequence<<16 | 7<<8 | destination`.
Generated by the host-tested `laguna_dpu_power_on_sequence()`; the full
golden-sequence test links Google's unchanged `lga_cpm_mappings.c` and the
extracted power protocol.

| Order | Step | Four words (hex) | Completion required before next step |
|---|---|---|---|
| 0 | queue reset | `00000100 00000000 00000000 00000000` | TX-done; reset next channel and sequence |
| 1 | DPU top ON | `a0010705 00000002 00000005 00000001` | LPB success for ID 5: immediate success, or PWRUP_STARTED plus the matching ON event |
| 2 | DSI0 ON (subdomain 2) | `a0020708 01020001 00000000 00000000` | correlated LPCM payload[0] == 0 |
| 3 | FE0 ON (subdomain 0) | `a0030708 01000001 00000000 00000000` | correlated LPCM payload[0] == 0 |
| 4 (if used) | FE1 ON (subdomain 1) | `a0040708 01010001 00000000 00000000` | correlated LPCM payload[0] == 0 |

Why this order: the saved power DT has `dpu_dsi0_pd` under `sswrp_dpu_pd`,
`dpu_fe0_pd` under DSI0, FE1 under FE0. DSI1/DP stay off (not needed by the
DSI0 panel). LPB payload `[15,15,5]` = immediate success; `[15,18,5]` =
power-up started, wait for the asynchronous `{5,0,...}` event (event-before-ACK
and ACK-before-event are both valid); `[15,19,5]` (`NO_EFFECT`) is
**indeterminate** — stop rather than touch the DPU. Sequences increment 1..13
and wrap to 1.

## 5. Path A — the display-owning first stage (preferred) [device-pending]

The vendor first stage loads Google's mailbox/power/SMMU/vsi-drm drivers and
creates a dedicated, full-panel, linear scanout; kexec preserves it. The
`laguna.kexec_noshutdown=1` design must skip `device_shutdown()` so DRM cannot
blank the panel/DPHY and SMMU cannot be dismantled (the captured ABL
transition ends in `MIPI DPHY Power-off` — "just inherit ABL's live buffer"
is not a plan here).

1. Allocate a **dedicated** physically contiguous scanout: 1280×2856, 32 bpp,
   XR24/AR24, stride 5120, allocation `0xdf2000` bytes, base and IOVA
   256-byte aligned. This is a geometry calculation — the Android primary
   buffer (compressed, wrong size) is not an iOS framebuffer.
2. In the GEM/DRM code, pin the object and validate every physical page.
   `vs_obj->iova` is only the DPU DMA address; derive `cpu_pa` from the
   pinned pages, prove contiguity, and reject if either namespace cannot
   cover the allocation. GETFB2 is not an acceptable exporter.
3. Program FE0 layer 0 through the driver's normal atomic path with the new
   IOVA, linear modifier, stride and dimensions; obtain frame-done/scanout
   evidence from that driver. Never read active/shadow register banks from
   XNU later.
4. Pin the physical reservation, power domains and IOMMU mappings across
   kexec; exclude the range from XNU's free RAM and from root/payload/panic/
   DT ranges. Preserve CPU cache policy: WC, or cached with
   clean-on-present.
5. Fill and CRC-seal the **128-byte handoff record**. All flags are required
   for a usable milestone: `CPU_PA_VALID`, `DPU_IOVA_VALID`, `LINEAR_32BPP`,
   `PHYS_CONTIGUOUS`, `RESERVED`, `DPU_CONFIGURED`, `POWER_RETAINED`,
   `IOMMU_RETAINED`, `SCANOUT_LIVE`. If any cannot be asserted from producer
   state, fail closed and emit no video.

Transport into XNU: the loader/kexec adapter copies the record into reserved
memory and advertises it as a `/chosen` pair — `laguna-display-handoff`
`{PA,size}`, plus `laguna-display-forbidden` ranges. The consumer on this
branch is `hakios_blazer_display_init()`
(`xnu/files/osfmk/arm64/hakios.blazer_display.c`) over the validator in
`laguna_display_handoff.c` / `laguna_render_port.h`: bad magic/version/CRC,
missing ownership/liveness flags, compressed or wrong formats, unaligned
PA/IOVA/stride, non-page-multiple sizes, non-contiguous or unreserved memory,
cache-policy errors and overlap with kernel/root/DT/panic ranges are all
rejected — a valid CRC alone is not a safety decision. Invalid records go
headless; absent records preserve loader GOP/ramfb video.

Only after validation:

```text
v_baseAddr = handoff.cpu_pa   v_width  = handoff.width
v_rowBytes = handoff.stride   v_height = handoff.height
v_depth    = 32               v_display = 1
```

The record is deliberately richer than `boot_args.video`: CPU PA and DPU IOVA
are different namespaces (`IDENTITY` mapping requires them equal;
`FIRST_STAGE_SMMU` permits difference but still requires `IOMMU_RETAINED`),
power/IOMMU retention is explicit, and `commit_generation` + the
NUL-terminated `producer[]` string make provenance auditable. MiniIOMFB must
revalidate before adopting a changed generation.

## 6. Path B — native XNU producer (later, gated) [design]

The exact operation order is specified and host-tested as protocol, but steps
beyond CPM power are **design, not permission**: no guessed register writes.
In brief: complete §3–§4 with `include_fe1=false`; allocate and permanently
reserve a contiguous 256-byte-aligned scanout; establish the DPU SMMU mapping
(stream model, never assumed bypass); program interconnect/DPU clocks from
the actual DT providers (no borrowed Qualcomm RPMh/GCC/DISPCC calls — if the
provider is not implemented, stop); initialize DSI2 host 0, C/D-PHY 0 and the
panel from source/DT; program the mode and FE0 layer 0 through the
driver-equivalent commit path (`dc_hw_enable_shadow_register()`, outpath
start only after FE/BE/DSI state is complete); route frame-start/frame-done/
underrun through the DPU GIA (GIC SPI 820) and use frame-done — not a line
counter — as the liveness proof; on underrun stop presenting and mark the
handoff not-live. Only after a completed frame emit the §5 handoff with
`commit_generation=1`, producer `xnu-vsi-drm`. There is no "read all 12
layers" step and no splash-repoint shortcut.

## 7. The XNU consumer: MiniIOMFB + MiniIOSurface [QEMU]

The presenter contract is SoC-independent and proven on this branch in QEMU:

- `BlazerPlatformExpert` (root compatible `google,lga-blazer`) **inherits**
  `GenericARMPE::start()`, which starts `MiniIOSurface`,
  `MiniIOMobileFramebuffer`, `MiniAppleKeyStore` and the rest directly after
  IOResources is live. Exactly one Mini* start path — do not reintroduce the
  old 0018/0019 personality stack or start the services twice.
- `MiniIOSurfaceStart()` allocates `gSurfaceLock`, `gSurfaceKeptLock` and
  `gSurfaceClearing` **before** `IOCoreSurfaceRoot::start()` calls
  `registerService()` — that ordering is the NULL-lock fix.
- `IOMobileFramebuffer` adopts `vinfo.v_baseAddr`, publishes
  `TimingElements`/`ColorElements`, names itself `disp0`; swaps queue (default
  two) IOSurface layers and the presenter thread CPU-copies/scales BGRA into
  the scanout. Vsync is a timer (selector 72 arms type 5); the 60 Hz default
  is a software beat, not a claim about the physical panel mode (the first
  stage retains the real 120 Hz mode; it can repeat 60 Hz updates).
- Display-config selectors that backboardd's probe requires, all answered in
  this branch's `MiniIOMobileFramebuffer.cpp`: 57 (color remap → scalar 0),
  27/17 (the 3084-byte stateful get/set twin over a zero-initialized 4 KiB
  shadow block), 84/50/68 accept, 79 GetBlock; unknown output-only probes
  zero-fill rather than assert. This is what let stock backboardd probe
  without a FrontBoardServices assert on the QEMU run.
- **Presenter cache hook**: with `cache_kind=WC` a DSB barrier before
  present; `CLEAN_ON_PRESENT` flushes completed frames to PoC after helper
  threads finish. CPU/softpipe completion alone does not prove DPU
  visibility.
- Keep the empty `IOAcceleratorES` (`sgx`) with no `MetalPluginName`/
  `MetalPluginClassName` — this avoids Metal's 30-s poll; do not publish a
  fake Metal device.

## 8. The renderer: Mesa softpipe + the EAGL/GLI shim [QEMU]

Use the upstream bridge, not a second shim: `userland/src/eagl.c` +
`gl_osmesa.c` install `libhakios.gl.dylib`, wire public Mesa `gl*` exports,
handle CA's server-side make-current behavior, IOSurface attachments, the
`-sharegroup` CoreImage fix, and the GLI table CoreAnimation drives. Facts
that make this portable:

- The GLI table was decoded from our own caches: **409 named slots, maximum
  slot 1032, byte-identical between the 20F66 and 20H392 roots** — one table
  serves the image. (Slot 591 carries both `glDeleteProgram` and
  `glDeleteShader`; slot 385 has no Apple ES export and stays a log-once trap
  CA never calls.)
- CoreAnimation obtains contexts through the GLI table
  `{rctx, disp[1036]}` exposed via `getMacroContextPrivate` (slot `n` at
  `8*(n+1)`), API 3. CA never calls `+setCurrentContext:` — including on its
  vsync thread — so the wrappers make the Mesa context current per call;
  this is the guard against the NULL-Mesa-TLS crash class.
- `tools/mk-mesa.sh` builds Mesa with `softpipe` (plus `freedreno`, unused
  here), OSMesa ES support, no LLVM, and a Darwin libdrm shim;
  `gl_osmesa.c` creates the off-screen ES context. `OSMESA_SOFTWARE=1` forces
  the software driver selection; `glwait=1` makes the renderer finish before
  CA hands the surface to the presenter.
- Do not inject the old probe `libsweetgl.dylib`; the production bridge is
  `libhakios.gl.dylib` alone.

**Carrier — `kern.exec_inject`.** launchd strips `DYLD_*` from non-root jobs,
so a `DYLD_INSERT_LIBRARIES` entry in a plist is not enough. The kernel-side
AMFI answer is already in the foundation: `kern_minimac.c` registers policy
name `AMFI`, answers op `0x5a` with `AMFI_DYLD_OUTPUT_ALLOW_ALL = 0xff`, and
`0x60` returns not-enforcing. `GL=1` at userland-build time makes backboardd
the default injection target; `gl=1` at boot makes `inject.c` dlopen the GL
bridge. No second AMFI implementation, no `lagunaamfideny` argument.

## 9. Boot-argument profiles [host]

Baseline softpipe profile (append to the platform/root arguments from
`docs/BOOT-CONTRACT.md` §4 — never to a Qualcomm command line):

```text
gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1 fb_hz=60 fb_threads=3 fb_queue=2 fb_beat=1
io.display.probe=0 io.display.splash=0 io.display.native=0
blazer_cpm=0 blazer_cpm_tx=0
```

Optional first-bring-up observability:

```text
glframes=1 fbtrace=8 touch_lat=1 surftrace=24
```

Rules that keep the baseline honest:

- Start **without** `ca_vbl=1` — CA's own timer is the control; enable it only
  after notification registration and display-link cadence are proven.
- Start **without** `fbscale=2` — keep the known 1125×2436 @3 canvas.
- No `-v`: the first presented frame takes over from the text console.
- Never: `gpu=1`, `adreno=*`, `fb_scan=1`, `metal=1`, Qualcomm `soc_*`/MPSS/
  IPA arguments, `io.display.probe=1`.
- `rdinit=/xni` and the root/cache arguments are delivery requirements
  (`docs/BUILD.md`, `README.md`), not renderer toggles.

## 10. Render-gate checklist [host]

A log line saying "service registered" or "swap accepted" is not proof the
panel shows content. Instrument in order; gates 0–3 kernel-side and gate 6
are mandatory:

| Gate | Required observation | If it fails |
|---|---|---|
| 0. Power + scanout ownership | valid handoff CRC, all ownership/live flags, reserved PA/IOVA, no overlap | stay headless; never start IOMFB on `fb=0`, ABL guesses, compressed FB or a dead DPHY |
| 1. Mini* display contract | IOCoreSurfaceRoot globals non-NULL before registration; 57/27/17/84/50/68 clean | fix matching/globals/selectors before touching CA — a SpringBoard FBS trap is not a Mesa failure |
| 2. Vsync timer | selector 72 arms type 5; `fVSyncRunning=1`, `fVSyncCount` advances | timer/thread_call/notification work — this is timer proof, not DPU proof |
| 3. Display link (only if testing `ca_vbl=1`) | CA uses the notification cadence, not free-run | remove `ca_vbl=1`, return to the CA timer |
| 4. Delegate + renderer | 8/8 EAGL methods, ES3 context non-NULL, "server has an accelerated renderer", no Metal plugin | check exec-inject, `gl=1`, GLI table, empty IOAcceleratorES |
| 5. Drawable + completed work | non-NIL display IOSurface; CA binds the full-screen BGRA surface; `glframes=1` shows draws; `glFinish` completes | surface allocation/format, GLI current-thread handling, softpipe fallback |
| 6. Present | SwapBegin → SwapEnd → non-empty layer → presenter copies; `fSwapsShown` advances; cache clean/barrier done | empty swaps = scene/SpringBoard problem; non-zero pixels + stale panel = handoff/power/IOMMU/cache problem |
| 7. Visible content + cadence | the panel shows the expected frame, then successive frames without tearing | do not call the Apple-logo BootUI frame "SpringBoard content"; continue through BootUI → scene commit → lock/home |

Discrimination: no CA frames + no swaps ⇒ SpringBoard/LS/scene cascade. CA
frames but zero-layer swaps ⇒ BootUI-to-SpringBoard handoff or scene policy
(the panel may legitimately retain the last image). `tl:v` but stale physical
panel ⇒ first-stage shutdown, wrong PA/IOVA, SMMU teardown, format mismatch,
or missing cache maintenance. Works with `ca_vbl=0` but not `1` ⇒
notification/display-link contract.

## 11. What is proven where — summary

| Claim | Where proven |
|---|---|
| Stock lock screen renders end-to-end on the laguna **platform** | **QEMU** (blazer kernel + verified images, Mini CPU-copy presenter, ramfb scanout) |
| CPM transport + DPU power frames + handoff validator correctness | **host** (270 checks incl. every 1-bit corruption of the 128-byte record, normal and ASan/UBSan) |
| `BlazerPlatformExpert` matching, CPM/DC9x00 hardware paths, first-stage DPU producer, panel scanout | **device-pending** — needs the first-stage ADT contract and a gated run |

The executable details — golden-sequence generator, handoff validator, host
tests (`run-host-tests.sh`), and the two boot-arg profiles — live in the
working lanes (`research/laguna-render-port/{DPU-INIT,RENDER-PORT}.md` and
`src/`/`include/`/`tests/`); the kernel-side consumers are on this branch
(`hakios.blazer_cpm.c`, `hakios.blazer_display.c`, `laguna_display_handoff.c`,
`laguna_render_port.h`, `MiniIOMobileFramebuffer.cpp`). Host success does not
establish CPM electrical behavior, DPU power, kexec preservation or panel
pixels — those are the separately gated integration milestones above.
