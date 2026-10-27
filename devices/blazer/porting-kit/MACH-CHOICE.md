# MACH-CHOICE — which kernel ships on the phone shim path

**Lane:** C1 (mach choice + platform personalities), 2026-10-27 · host-only, no device access.
**Trigger:** B4's finding (USERLAND-PATH.md Gap 1): the phone shim payload ships the 141-lineage
mach (`mach.laguna141-paniccap` `3629fa3d…`, 15,102,200 B) which carries the R4/R5 mirror
(0129/0130, committed at `kernel-wt` tip `8d6d4b2`) but LACKS the blazer platform the QEMU render
proof REQUIRED (BlazerPlatformExpert, Mini* personalities: MiniAppleKeyStore / MiniIOMFB /
IOCoreSurfaceRoot / MiniIOSurface — proven `fork/blazer` @ `317650c`+`0a7313a`, foundation
`xnu-8796.121.2`). Without them: no display, no platform, and (Gap 1/Gap 4) no AMFI-0x5a → no
exec_inject renderer carrier, and (Gap 2) no APFS for the target root.

---

## 0. Verdict

**Ship the blazer/foundation mach — `mach.development.bcm2837`, built from
`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch `hakios`) — as THE shim-path
kernel, and bring the evidence to it** (Option A). The R4/R5 evidence chain and the TCM touch driver
are now PORTED onto that tree and the mach is BUILT; its sha256 is in §5. Option B (platform → 141)
is rejected on measured cost; Option C (two machs, boot-arg switch) is documented as a fallback, not
a plan. B3's Stage-0 framebuffer item dissolves under this choice (§6).

This is also the direction B4's USERLAND-PATH already pointed at ("*Fix:* deliver the blazer-branch
kernel through the shim/kexec ladder (marker-patched payload + boot_args smuggle as today), keeping
the personalities REGISTERED and `kern_minimac.c`'s AMFI answer intact") — this document makes it the
decision, supplies the missing half (evidence + touch on the same mach), and proves it builds.

## 1. What the two lineages actually contain (measured, not assumed)

|  | **141 mach** (`kernel-wt`, base 22.6.0 / xnu-8796.141.3, config `MASTER.arm64.laguna`) | **blazer mach** (foundation, base 22.5.0 / xnu-8796.121.2, config `MASTER.arm64.bcm2837` via `MACHINE_CONFIGS=BCM2837`) |
|---|---|---|
| Platform expert | `LagunaPlatformExpert` (82 ln) matched on `google,lga-blazer` | `BlazerPlatformExpert` ⊃ `GenericARMPE` (425 ln) matched on the same compat — **the shim ADT already carries `google,lga-blazer`** (shim banner `DTCOMPAT`), so the expert engages on the phone path |
| Display/render | `AppleCLCD`+`LagunaFramebuffer` (624 ln, DC9x00 gate) + `LagunaSurface` | `MiniIOSurface` (1,814 ln) + `MiniIOMFB` (1,589 ln CPU-copy presenter, IOMFB selector family incl. the 3084-byte stateful 27/17 twin) + `IOCoreSurfaceRoot` — **the exact stack that composited the QEMU lock screen** |
| AKS | `LagunaKeyStore` stand-in (ops 4/5/6/0x43 + bag sels) | `MiniAppleKeyStore` + `kern_minimac.c` **AMFI 0x5a `CHECK_DYLD_POLICY` → ALLOW_ALL** — dyld accepts DYLD_INSERT (renderer carrier, Gap 4) |
| Filesystem | HFS only ("the 141 bridge mounts HFS only") | **APFS** (`bsd/miscfs/apfs`, 14 files) — the target `system.apfs 637103e1…` root mounts (Gap 2) |
| R4/R5 evidence | **Full final chain** committed: `laguna_panicdump` capture + `laguna_logdump` mirror v2 (0130 windows), LGLW entry record, stage stamps, halt/reboot flushes | Only the 0123/0124-shaped capture (`hakios.blazer_panicdump`, **opt-in** via `blazer_panicdump=1`, default OFF); no mirror ring, no LGLW, no stages |
| Touch | `LagunaHIDTouch` (no TCM) | `hakios.goodix.c` (POCO) — TCM patch existed only in fork-overlay form, **now applied** (§4) |
| Phone-proven | **Yes**: XNU entry markers, banner, `BSD root: md0`, HFS mount, launchd exec (M2) | **No** — QEMU-proven only (render + userland); phone entry is the first phone run of the blazer mach |
| Mach size | 15,102,200 B | 16,396,976 B (`3b8841df…`) |

**Base-version drift is small where it matters.** The IOKit/libkern core the Mini* stack compiles
against is byte-identical between the two trees (`IOPlatformActions.cpp`, `IOPlatformIO.cpp`,
`OSObject.cpp`, `OSDictionary.cpp`: 0 diff lines; `IOPlatformExpert.cpp`: 11 lines, and those are the
foundation's own crypto-provider guard). `panic_validate_ptr` sits at the same line (609) in both
trees' `debug.h`. The "same-generation arm64 XNU" premise holds — in BOTH directions; what differs is
how much ELSE each port must move (§2).

## 2. The three options, costed

### Option A — blazer mach + port the R4/R5 chain (+ TCM) onto it ✅ CHOSEN

New material is almost entirely **additive and self-contained**: 4 files from the 141 final state
(1,764 lines: `laguna_panicdump.{c,h}`, `laguna_lastwords.h`, `laguna_entry_lastwords.s`) + ~90 lines
of hooks into 14 shared files at upstream-stable anchors + the TCM driver (1,448 lines, already
host-tested 198/198). Adaptations found and made during the port (§4): arming site →
`MiniAppleKeyStore::start` (foundation's first IOKit driver) instead of `LagunaKeyStore::start`; the
module's `#if defined(LAGUNA)` guard → `__arm64__` (BCM2837 build has no LAGUNA define — this was the
one silent-failure trap); PAN clear + LGLW call inserted at `start_first_cpu` (foundation's `start.s`
has no first-store-before-VBAR hazard of its own — no fb band exists there — so the one-instruction
`.inst 0xd500409f` covers the only MMU-off store we add). The blazer tree's own opt-in
`hakios.blazer_panicdump` stays untouched — with its default-OFF polarity the system has **exactly
one default panic-capture writer**.

Cost: **one working session** (this one). Risk: low-to-moderate — the golden QEMU render boot must be
re-run once against the new mach to re-verify the platform stack with the mirror appended (the mirror
only adds an arm_init map of 0x95200000–0x95600000 — free RAM on QEMU, below the loader window — plus
putc-time ring writes under the console log lock).

### Option B — 141 mach + port the blazer platform onto it ❌ rejected

Must move, onto a **different base (22.6.0)** and a **different machine config**: the Mini* stack
(~5,274 lines core), `GenericARMPE`/`BlazerPlatformExpert`, **APFS (14 files)**, `kern_minimac`
AMFI-0x5a, `kern.exec_inject` wiring, the display-config selector set and the NULL-lock
`MiniIOSurface` ordering — plus rewiring every file-list/config hook from `MASTER.arm64.bcm2837`
into `MASTER.arm64.laguna` (or a new master), and semantic merges in the same shared files both
lineages already modified differently (`kern_exec.c`, `bsd_init.c`, `debug.c`, `start.s`, …). Realistic
cost: multiple days plus a full QEMU re-proof of the ported platform, i.e. strictly more work and
more risk than A for the SAME end state — and it forfeits the warm QEMU render reference (the
platform would run on a base it never ran on). **Rejected.**

### Option C — ship both machs, switch via boot-arg ⚠️ documented fallback only

Fit is honest and real: 64 MiB boot image with ~41 MB used; +16.4 MB → ~57.4/64 MiB, ~6 MB margin —
**it fits**. But: (1) the selector is not a boot-arg the mach reads — the choice happens *before* any
mach runs, in the kexec stage (`kx_build_cmdline`/payload pick in `blazer/userspace/kexec-xnu.c`) or
the shim — i.e. S1 tooling work this lane must not make unilaterally; (2) it doubles the build trees
to keep alive and splits the evidence formats across two kernels; (3) the 141 mach contributes only
"phone-proven entry + evidence" — which Option A's mach now carries itself, and which it is better to
validate ONCE on the blazer mach than to perpetuate as a second kernel. **Keep as the contingency if
the blazer mach dies at phone entry and the fleet needs a same-day evidence payload**; do not build it
now.

## 3. Why the platform cannot be skipped (B4's contract, restated from source)

`BlazerPlatformExpert` (root compatible `google,lga-blazer`, probe score 10000) inherits
`GenericARMPE::start()`, which starts `MiniIOSurface`, `MiniIOMobileFramebuffer`, `MiniAppleKeyStore`
directly after IOResources is live — exactly one Mini* start path; `MiniIOSurfaceStart()` allocates
`gSurfaceLock`/`gSurfaceKeptLock`/`gSurfaceClearing` BEFORE `IOCoreSurfaceRoot::start()` registers
(the NULL-lock fix). `io.display.iomfb=1` (already in the phone payload cmdline template,
`kexec-xnu.c kx_build_cmdline`) is the E0 publish opt-in; on device the DC9x00 discipline applies
(`io.display.probe=0 io.display.splash=0` always — a DC9x00 readback is an async SError crash-reset).
The QEMU proof ran this entire ladder: `MiniIOSurface` surfaces (backboardd's 1125×2436 BGRA,
11,239,424 B), MiniIOMFB selectors 57/27/17/84/50/68/79 clean, `2 frames in 59980 ms` composited
present, SpringBoard shell 26.75 s, zero panics (`research/laguna-blazer-qemu/RESULTS.md`).

## 4. The concrete port (as executed — reproducible)

**Base:** `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch `hakios`, dirty
working tree = M2's integrated blazer work; built on this host per `laguna-blazer-qemu/RESULTS.md`).
Port performed in an isolated copy at `<tmp>/opencode/c1-mach/` so the shared tree stays untouched;
both deliverable patches in `research/laguna-usb-fix/patches/` apply cleanly to the shared tree.

**New files (4, verbatim final state from `kernel-wt` @ `8d6d4b2`):**
`osfmk/arm64/laguna_panicdump.c` (1,185 ln), `osfmk/arm64/laguna_panicdump.h`,
`osfmk/arm64/laguna_lastwords.h`, `osfmk/arm64/laguna_entry_lastwords.s` (211 ln).

**Hooks (14 files, ~90 lines — all at upstream-stable anchors, all `#if defined(__arm64__)`):**

| file | hook |
|---|---|
| `osfmk/conf/files.arm64` | + `laguna_panicdump.c`, `laguna_entry_lastwords.s` (`standard`) |
| `osfmk/arm64/Makefile` | EXPORT_MD_LIST += `laguna_panicdump.h`, `laguna_lastwords.h` |
| `osfmk/arm/arm_init.c` | `laguna_logdump_early_init()` after `arm_vm_init`; `laguna_panicdump_init()` + `laguna_logdump_late_init()` before the `serialmode` parse |
| `osfmk/arm/model_dep.c` | rung 2: `laguna_panicdump_capture` after the fp-outside-vm else (beside the blazer capture) |
| `osfmk/kern/debug.c` | rung 1 in `debugger_collect_diagnostics`; `laguna_logdump_flush(LAGUNA_LOGDUMP_FLUSH_HALT)` at `kdp_machine_reboot_type` entry |
| `osfmk/arm64/machine_routines.c` | `laguna_logdump_flush` at `halt_all_cpus` entry |
| `osfmk/arm64/start.s` | `.inst 0xd500409f` (PAN clear) + `bl EXT(laguna_logdump_entry)` between `DAIFSet` and `mov x20, x0` in `start_first_cpu` |
| `pexpert/arm/pe_init.c` | `check_for_panic_log`: second fallback arm — `lagunapanicdump` (default ON) with no ADT pram node → `gPanicBase` = the shared 512 KiB .bss region (real pram always wins) |
| `bsd/kern/bsd_init.c` | `LW_STAGE_ROOT_MOUNTED` after `set_rootvnode` |
| `bsd/kern/kern_exec.c` | `LW_STAGE_DYLD_STAGE` (pid 1, `exec_activate_image` ok, `__mac_execve`); `LW_STAGE_LAUNCHD_EXEC` in `load_init_program_at_path` |
| `bsd/vm/vm_unix.c` | `LW_STAGE_DYLD_SHARED_REGION` in `shared_region_check_np` (pid 1) |
| `bsd/kern/subr_log.c` | `laguna_logdump_putc` in `log_putc_locked` (`mbp == msgbufp` only) |
| `iokit/Kernel/MiniAppleKeyStore.cpp` | **arming site**: `laguna_panicdump_bootdone()` at `AppleKeyStore::start` (141 used `LagunaKeyStore::start`; this is the foundation's first IOKit driver — same "earliest fully-threaded hook" property) |

**Adaptations vs the 141 patches (all documented in the patch header):**
1. **The LAGUNA guard trap.** `laguna_panicdump.c`'s body is `#if defined(LAGUNA)` in the 141 tree;
   the blazer build defines no LAGUNA → the port would have silently compiled the stub path. Guard is
   now `#if defined(__arm64__) && !defined(LAGUNA_PANICDUMP_STUB)`.
2. **No `laguna_dc9x00_early_splash` anchor** in foundation `arm_init` — hooks placed at the
   `arm_vm_init` / `serialmode` anchors instead.
3. **`ARM64_BOARD_CONFIG_LAGUNA` → `__arm64__`** in every bsd hook (BCM2837 build defines neither).
4. **panic-region fallback**: the blazer tree already carries a `blazer_panicdump=1`-gated pram
   fallback in `pe_init.c`; the port adds the laguna polarity (default-ON, `=0` disables) reusing the
   same .bss region — the two capture paths are never armed together.
5. **start.s shape**: foundation `start_first_cpu` has `DAIFSet → mov x20, x0` back-to-back (its
   GENERIC_PSCI_SMP branch is not built for BCM2837); the LGLW call needs x0 = boot_args PA, so it
   goes exactly there, preceded by the PAN clear (EL2-ENTRY-DISCIPLINE item 4 — unknown ABL PAN state
   before our first MMU-off store; there is no pre-VBAR fb band in this start.s to worry about).

**Touch (fold-in):** TCM driver applied in integrated-tree form — `bsd/dev/hakios.tcm.{c,h}`
(1,448 ln, byte-exact from the 198/198-tested fleet patch
`research/laguna-touch/patches/0001-blazer-…`), `hakios_tcm_init()` after `hakios_goodix_init()`
(`bsd/dev/hakios.input.c`), declaration in `bsd/sys/hakios.input.h`, `bsd/dev/hakios.tcm.c standard`
in `bsd/conf/files`. Gate stays `blazer_touch=1` (opt-in, device-scoped naming per the port's own
contract). NEEDS-DEVICE-PROBE items unchanged (AOC pad-slot layout; ABL bus park state).

**Conflicts expected when folding into the shared tree:** none textual (anchors verified line-exact
against the shared tree before editing); semantic review wanted from M2 on exactly two things — the
`kern_exec.c` pid-1 stage and the `subr_log.c` putc hook — both are the 141-tree's own committed,
phone-proven code, but they touch files M2 actively works in.

**Build (N15 build-race guard honored — the tree carries the atomic `bsd.syscalls.master` +
atomic `makesyscalls.sh` + `.NOTPARALLEL` generation, verified `research/laguna-foundation/BUILD-RACE-GUARD.md`;
no `-j15` first-build race possible):**

```sh
C1=<tmp>/opencode/c1-mach            # isolated copy of kernel-blazer (BUILD/ kept warm)
source "$C1/xnu-env.sh"
cd "$XNU_SRC"                         # $C1/xnu-src/xnu-8796.121.2
make SDKROOT=iphoneos ARCH_CONFIGS=ARM64 MACHINE_CONFIGS=BCM2837 KERNEL_CONFIGS=DEVELOPMENT \
  ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG=arm64 BUILD_WERROR=0 BUILD_LTO=0 DO_CTFMERGE=0 \
  HOST_CC=clang HOST_FLEX=flex HOST_BISON=bison HOST_GM4=m4 \
  CC=xnu-clang CXX=xnu-clang++ LOGCOLORS=n -j8 -k
K=BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837
shasum -a 256 "$K"
```

(`-k` is the documented post-link norm: the export-list check's RC=2 tail is the known-harmless
baseline. Linux-builder equivalent: same tree in the `hackios-xnu-build` container with a fresh
`BUILD/` — do not reuse macOS objects there.)

## 5. Build result

- Build: `-j8 -k`, **0 compile errors**; `BUILD-RC=2` is the documented post-link export-list
  baseline (known-harmless, per BUILD.md).
- **Artifact:** `BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837`
  sha256 **`b39179b5d0268c9c98432c46bda0cfda66eacce299efb1baf79899c5342f465b`**,
  **16,416,856 B** (+19,880 B over the pre-port blazer mach `3b8841df…`/16,396,976 B).
  Copy preserved at `research/laguna-usb-fix/mach.blazer-r4r5-tcm`.
- Binary verification (strings/bytes): `google,lga-blazer`, `BlazerPlatformExpert`,
  `MiniIOMobileFramebuffer`, `MiniAppleKeyStore` (platform intact); `LAGUNA PAN` ×10,
  `XNU-MSGBUF`, `LAGUNA LOGDUMP: disabled by lagunalogdump=0`, `LAGUNA PANICDUMP: disabled by
  lagunapanicdump=0` (evidence chain in, both gates live); `blazer_touch`, `tcm:` ×32 (TCM in);
  `apfs` ×226 (APFS intact).
- Host suites: TCM suite re-run **PASS** (TouchComm v2 identify, S3908, panel 1280×2856, touch
  reports ready). The panic-capture lane's decoders (`verify-logdump.py`, `logdump_format.py`,
  `verify-panic-record.py`) consume the same windows/format the ported module emits (final 0130
  state taken verbatim).
- **Patch validation:** both deliverable patches were applied (`patch -p1`, dry-run RC=0, zero
  rejects) to a pristine copy of the 16 touched shared-tree files and produced output **byte-identical
  to the tree that built this mach** — the port is reproducible from the patches alone.
- One real bug found and fixed BY this build: the TCM fleet patch had never compiled kernel-side
  (host suite compiles the kernel section out) and carries a static redefinition
  (`tcm_bus_write/read/attn` generic vs kernel) plus an include-order/`kern/misc_protos.h` mismatch —
  fixed in the integrated-tree patch; the touch lane should fold the same three fixes back into
  `research/laguna-touch/patches/` when it lands `blazer-port` proper.

## 6. Fold-in: B3's Stage-0 framebuffer band (fix-or-document → DOCUMENTED, and dissolved)

B3's item: the 141 lineage's `start.s` stores a Stage-0 white band to **hardcoded `0xf0000000`**
("LAGUNA-TODO: runtime fb address") at `kernel-wt/osfmk/arm64/start.s:530` (band) and `:701` (the
32 MB Normal-NC fb alias in the V=P L2), *before VBAR_EL1 exists*. Facts on record:
`0xf0000000` is verified good DRAM on the phone (unreserved gap `0xeb000000..0xf6200000` between
`cp_rmem_1` and `sec_dram`; TIMELINE-ANALYSIS.md §2) — so the store cannot crash, but pixel
visibility was never verified (instrument I-5), and the shim's own fallback PA is the same constant
(`shim.c:45`), with the fb-scan's DTFB candidate chain taking the placeholder regardless
(`shim.c:311-312`).

**Under Option A this hazard leaves the shipped mach entirely**: the blazer `start.s` has no Stage-0
band and no hardcoded fb alias; the display PA flows in at runtime — MiniIOMFB adopts
`boot_args.video.v_baseAddr` (the same field the shim fills), and the phone display end-state is Gap
3's CPM-first scanout handoff (A-CPM lane; CPM-FIRST-SPEC), which advertises the real scanout PA via
`/chosen laguna-display-handoff`. **Remaining action (S1, shim side, unchanged by this decision):**
stop taking the `0xf0000000` placeholder when the live FDT names the panel mode — patch the real PA
into `boot_args.video.v_baseAddr` (and the kexec-stage ADT), or leave video zeroed and go
headless-correct until the CPM scanout exists. No kernel-side fix is required on the chosen mach.

## 7. Delivery contract (unchanged, verified in source)

- The fork's phone delivery chain is **already shaped for this mach**:
  `blazer/userspace/build-xnu-payload.sh` defaults to `prebuilt/mach.development.bcm2837`, and
  `blazer/shim/tests/patch-xnu-marker.py` (v2 stub: base-independent ADR+delta branch + boot_args
  smuggle) is mach-agnostic; `kexec-xnu.c kx_build_cmdline` already emits `io.display.iomfb=1` and
  the root/ignition/jetsam/hygiene tokens.
- Phone cmdline for the render-capable mach: today's template + `autounlock=0
  launchd_disable_all_timeouts=1` (M6's cascade args) +, when the renderer lands, the RENDER.md §9
  `gl=1 glwait=1 glenv=…` profile (Gap 4's list). `lagunalogdump=1` is now meaningful on this mach
  (default ON; `=0` suppresses) — same evidence channels, same tooling
  (`research/laguna-panic-capture/tools/{decode-device-panic,verify-logdump,logdump_format}.py`),
  same windows after 0130 (`XNU-MSGBUF v2`: header `0x95440000`, LGLW `0x95401100/80`, compact trail
  `0x95428000`, pmsg scratch `0x95401000`).
- On-device firsts this mach owes the ladder (each is the next rung's evidence, all QEMU/host-proven,
  none device-proven): kexec entry → LGLW XNU_ENTRY → banner → BlazerPlatformExpert on the shim ADT
  → APFS root on the transported image.

## 8. Risks & watch items

| risk | mitigation |
|---|---|
| blazer mach's first phone entry dies early | LGLW XNU_ENTRY lands at `0x95401100` before ANY C runs (it is the 5th instruction's call); its absence in a dump = death before instruction ~5. Panic path: pe_init fallback (no pram wedge), rungs write reset-safe with `dsb sy`. |
| golden QEMU render boot regressed by the port | one re-run owed before the next flash (`qemu/run-ios.sh` config in `laguna-blazer-qemu/RESULTS.md`); the mirror adds only an arm_init map + lock-held ring writes |
| shared foundation tree drifts under the patches | patches in `research/laguna-usb-fix/patches/` are anchored on verified upstream-stable lines; re-run the anchor checks (§4 table) if M2 lands big changes |
| two panic modules confuse someone into `blazer_panicdump=1` on the phone | documented: opt-in is QEMU-test-only; combined use double-appends the console ring (bounded, but evidence then needs the pmsg channel) |
| disk | port built in a 624 MB warm-copy scratch, ~1 GB build delta; **14 GiB free at build time** — a clean Linux rebuild would want the scratch deleted first |

## 9. Decision record

- Chosen: **A** — one mach: platform (proven render) + APFS + AMFI-0x5a/inject + R4/R5 evidence + TCM touch.
- Rejected: **B** (measured: platform port ≫ mirror port; different base + master rewiring + re-proof).
- Kept as contingency: **C** (fits: ~57.4/64 MiB; needs S1 kexec/shim selector work; only if the blazer
  mach fails phone entry and a same-day evidence payload is needed).
- Next actions: S1 packages the §5 sha256 into the payload (`build-xnu-payload.sh` input swap) +
  §6 shim-side video fix; A-CPM proceeds on the scanout handoff (Gap 3); M2 reviews the two §4
  semantic hooks; a QEMU re-run of the golden boot validates the mach before the next ladder.
- Scratch state: the warm build tree lives at `<tmp>/opencode/c1-mach` (624 MB copy + BUILD); delete
  it first if disk gets tight (~14 GiB free at build time) — the two patches in `patches/` reproduce
  everything byte-exact.

## Files this lane delivered

- `research/laguna-usb-fix/MACH-CHOICE.md` — this document.
- `research/laguna-usb-fix/mach.blazer-r4r5-tcm` — the built mach (sha256 above).
- `research/laguna-usb-fix/patches/0001-blazer-r4r5-laguna-evidence-chain.patch` — the mirror port.
- `research/laguna-usb-fix/patches/0002-blazer-tcm-integrated-tree-form.patch` — TCM in integrated form.
