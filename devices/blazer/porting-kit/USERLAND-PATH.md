# USERLAND-PATH — from "kernel survives arm_vm_init" to "SpringBoard renders" (laguna / Pixel 10 Pro)

Lane: B4 (userland path) · 2026-10-27 · **host-only** (no device, no shim-tree changes; this dir only).
Purpose: when the kernel comes alive on the phone, this is the ordered ladder, the config each rung was
proved with, the image content each rung needs, and the channel each rung is visible in — so the fleet
does not re-lose weeks rediscovering userland.

Read first (all verified this rotation):

- `research/laguna-foundation/UPSTREAM-ON-QEMU.md` — upstream c84b463: lock + home screen PASS on QEMU
  (run 04), full reproduced inputs, image hashes, timing.
- `research/laguna-foundation/PORT-CONTRACT.md` — loader→XNU contract, boot-arg discipline, what ports,
  what never does; §5.B: "keep the Mini* IOResources personalities registered … compiling the files
  without registering their personalities is not enough".
- `blazer-port/devices/blazer/docs/RENDER.md` — the blazer-platform render proof (fork/blazer @ `317650c`
  + N14 `0a7313a`: lock screen, Mini* presenter, present swaps 2–7 ms, SpringBoard shell startup
  **26.75 s**) + the gates/boot-arg profiles + render-gate checklist 0–7.
- `research/laguna-render-port/DPU-INIT.md` — CPM transport, exact DPU power frames, 128-byte handoff
  record, Path A/B; 270-check host suite.
- `research/laguna-touch/TOUCH-PORT.md` — TCM driver, verified 198/198, **not landed** on blazer tip.
- `research/laguna-felix-fold/EVALUATION.md` RANK 2 — the 15-item symptom→cause→fix userland matrix.
- `research/laguna-badmacho/DECODE.md` + `research/laguna-deviceroot/{README.md,MANIFEST.json}` — the
  141-lineage phone path: exec ladder, dyld wall, the 24 MiB bootstrap root.
- `research/laguna-usb-fix/{ABL-RUNTIME-FACTS,CPM-FIRST-SPEC}.md` + `../laguna-usb-fix/sim/README.md` —
  the cold-handoff USB death, CPM-first order, evidence stamps.
- `research/laguna-dslocal/` — the dslocal identity kit (940/940 checks) for the HFS-root lineage.

---

## 0. The two ends of the path (facts this map connects)

**End A — QEMU-proven (render).** The blazer-branch kernel (source-integrated, `317650c` lineage) boots
the verified userland image on the upstream QEMU harness to a composited, updating lock screen and home
screen: EDK2 → xnuloader (`platform qemu-virt`) → XNU; resident `system.apfs` at guest-phys
`0xFC000000` = **md0**, small `root.apfs` = **md1** (`/redwood`); stock launchd cascade; backboardd
IOSurfaces; Mini CPU-copy presenter into `boot_args.video`; scanout = EDK2 ramfb via GOP; `io.display.*=0`.
Upstream run 04: Apple logo 120–540 s, PosterBoard 08:37:57, `System shell "com.apple.springboard"
finished startup after 71.05 s`, lock screen PNG at 600 s, home screen at 900 s, QMP swipe unlock at
842.66 s, zero panics. Blazer-platform run: present swaps 2–7 ms continuous, SpringBoard shell startup
**26.75 s**, pixel-match 2.9–3.1/255 vs control. Foundation image build (`userland/build.sh`):
`out/system.apfs` 2.32 GiB (`637103e1…`) + `out/root.apfs` (`de340c95…`), all_pass=true.

**End B — phone shim path (today).** 141.3 laguna kernel (bridge lineage, marker-patched payload via
`build-xnu-payload.sh` + `patch-xnu-marker.py`; boot_args smuggled through the pmsg handoff at
`0x95400120`), delivered by the vendor_boot kexec ladder or the tegu shim from a cold ABL handoff.
Payload cmdline (verbatim, `kexec-xnu.c kx_build_cmdline` template + logdump lane):
`debug=0x8 serial=3 cpus=1 io.display.iomfb=1 rd=md0 rootrw=1 ignition_level=1
launchd_unsecure_cache=1 kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1 lagunalogdump=1`.
Root = the **24 MiB** `xnu.ramdisk` = `bootstrap-root-proven-v6.hfs`
(sha256 `aebc9a8a410369b19c60c3e845cb814896bd9709264d2c085283cf816439b4bf`, 25,165,824 B).
Proven phone-side rungs so far: XNU entry markers, Darwin banner, `BSD root: md0`, HFS mount, launchd
exec, dyld death (cache-less dyld). Not proven: everything from dyld onward.

---

## 1. The milestone ladder

Legend — **[Q]** proven on QEMU; **[H]** host-tested code/contract; **[D]** device-proven;
**[P]** device-pending (designed, never run on the phone). Evidence channels (phone):
**stamps** = RAM diag slot `0x95400100` + pmsg scratch `0x95401000` (read via next-boot MARKER-READ or
fastboot ramdump); **ramoops** = `console-ramoops-0` (pstore `0x95200000`, 2 MiB); **mirror** =
XNU-MSGBUF v1 ring at `0x95440000` (`lagunalogdump=1`, R4 panic-capture lane), retrievable with
`fastboot oem ramdump stage_file kernel.log` — proven live in `ABL-RUNTIME-FACTS.md`; **EP0** = the
shim's USB endpoint-0 console (only after CPM-first lands — see CPM-FIRST-SPEC.md); **serial** =
PL011/DW-APB UART (`serial=3`); **abl.log** = ABL's own ring (`fastboot oem ramdump stage_file abl.log`);
**panel** = the photo window (visual, always the final arbiter).

| # | Milestone | Exit criterion | Status QEMU | Status phone |
|---|---|---|---|---|
| M0 | Kernel alive | `arm_vm_init` done, msgbuf mirror initialized, console live | [Q] | [D] (entry markers + banner) |
| M1 | Root mounted | `BSD root: md0`, volume mounted RW | [Q] | [D] (HFS) / [P] (APFS) |
| M2 | launchd exec | PID 1 exec'd, first launchd/Sandbox lines | [Q] | [D] |
| M3 | dyld + first processes | launchd's dyld completes; jobs spawn | [Q] | **current wall** |
| M4 | IOKit personalities | Mini* services registered (Surface/IOMFB/KeyStore) | [Q] | [P] |
| M5 | backboardd + renderer | IOSurfaces, injected GL bridge, softpipe context | [Q] | [P] |
| M6 | SpringBoard cascade | PosterBoard → SB shell startup complete | [Q] | [P] |
| M7 | Present to scanout | SwapEnd → bytes in the scanout buffer, swaps advancing | [Q] | [P] |
| M8 | Input + unlock | touch/button events → unlock swipe → home screen | [Q] (virtio) | [P] |

### M0 — Kernel alive

- **Config that proved it [Q]:** loader defaults `debug=0x8 serial=3 cpus=1 rd=md0` + payload
  `autounlock=0 launchd_disable_all_timeouts=1` (upstream run 04; `loader_return_after=600` for
  bounded runs). QEMU harness specifics (virtio_input/sound, ramfb, cortex-a76, `-no-reboot`) are
  harness-only and must **not** be copied to the phone. `cortex-a57` PAN-traps; a76 is required there.
- **Root content needed:** none yet beyond the ramdisk being present in memory.
- **Felix playbook:** none (kernel-side). The AppleCLCD `sweetaccel` uninitialized-var fake-IOAcceleratorES
  bug (item **c**) is a *felix-kernel* bug; our 8796 branch has no AppleCLCD::start sweetaccel path, but
  the 141-lineage mach shares the SWEET lineage — if a 141 kernel is ever the render kernel, fix in
  source, don't binary-patch (EVALUATION RANK 2c).
- **Evidence:** phone — stamps (marker `0x5A5A5A5A` + stage word + x0), ramoops from first printf, mirror
  initialized *immediately after `arm_vm_init`, before `serial_init`* (R4), panel fbcon text if the
  display path is up. QEMU — the run log to `== END OF LOG ==`.

### M1 — Root mount (md0)

- **Mechanism [Q][D]:** ADT `/chosen/memory-map` `RAMDisk` = `{pa:u64}{size:u64}` (emitted by the
  loader/shim stage; kexec file-mode reads `/xnu.ramdisk`, cap `xnu.ramdisk_max=`), `rd=md0` sends
  IOFindBSDRoot down the memory-device branch (BSD-service wait cut to 100 ms), `rootrw=1` mounts RW.
  `rd=md0` WITHOUT the ADT RAMDisk property panics IOFindBSDRoot — the tokens never ride a ramdisk-less
  plan (kexec-xnu.c 0002 fix; the historical "Still waiting for root device" reboot).
- **Config [Q]:** same as M0. Foundation: two APFS volumes — big resident image `md0`, payload small
  image `md1` (`/redwood`); `no-effaceable-storage=1` in `/defaults`.
- **24 MiB vs full image:** identical *contract*, different filesystem: the 24 MiB root is case-sensitive
  journaled **HFS+** (`H+/HX` @1024, volname iOSRoot) mounted by the 141 bridge's built-in hfs; the full
  image is **APFS** and needs the foundation kernel's apfs driver (apfs_lib read-back + `apfsck` clean at
  build). An HFS root rides vendor_boot; the APFS `system.apfs` (2.32 GiB) must be staged as resident
  memory (kexec segments — 16-seg cap, ≤½-RAM, base `0x890000000` window math per iosroot manifest — or
  once-per-session staging like upstream's `xl ramdisk`, which needs the USB fastboot transport).
- **Felix playbook:** item **a** (HFS+ journal disable) applies ONLY to HFS images — do it in the image
  build, never on device. APFS foundation: N/A. Item **o** (first/second boot chronology): budget
  ≥ 25–30 min for the first full userland boot on the phone (LS DB compile ~10 min on felix; QEMU TCG
  needed ~11 min just to lock screen).
- **Evidence:** ramoops `BSD root: md0` (+ `hfs: mounted iOSRoot` / APFS mount lines); failure signature
  `Still waiting for root device` → 60 s timeout reboot; mirror carries the same lines.

### M2 — launchd exec

- **Config [Q][D]:** 141 path tokens `ignition_level=1 launchd_unsecure_cache=1` are REQUIRED on the
  **HFS** image (launchd must read the on-disk D2-tweaked `/System/Library/xpc/launchd.plist` instead of
  the dyld cache). On the **APFS foundation** these tokens are the HFS contract, not the fix — the
  foundation boots with the plain loader defaults (run 04 had none of them); `kern.jetsam_critical_threshold=1`
  and `interrupt-masked-debug-mode=1` are harmless hygiene on both.
- **What execs:** 141 path: real `/sbin/launchd` (PerformAlways keybag-boot-task patched). Foundation:
  `stage0` (src/init.c) as `/sbin/launchd` — runs the APFS/mmap/fs/fork-exec/dynamic-link test ladder,
  then `exec /sbin/launchd.ios` (the real launchd, kept as `launchd.ios`).
- **24 MiB vs full image:** the 24 MiB root carries exactly one exec target (launchd, 541,600 B) +
  `/usr/lib/dyld` (922,912 B) + the 36-dylib carved closure + launchd.plist + `/dev` + var skeleton +
  keybag/lsd/LS injections + logd stores. The full image carries the whole stock cascade (~407 REST jobs
  on the Scion sibling; the foundation's curated `IOS_JOBS` list is 22) and nothing is missing at exec
  time (uid-0, mobile identity, real lsd/mapdb already solved in-image).
- **Felix playbook:** item **g** (datamigrator sentinel = AppleKeyStore sel 27 = unlock of the mobile
  session; "NEVER DELETE THE SENTINEL ON FRESH INSTALLATION") — the **foundation solves this a different
  way**: mkjobs.py DENYs `datamigrator` outright and inject_sb.c forces `-[SBSetupManager
  _setSetupRequiredReason:] = 0`; proved in QEMU. Do NOT transplant the sentinel onto the foundation
  image without re-testing, and do NOT port the foundation's deny into a felix-style image blindly —
  pick one contract per image. Items **m** (SetupDone/Activated pre-seed: foundation ships
  `com.apple.purplebuddy.plist SetupDone=True`) and **n** (ACCHWComponentAuthService stash — pocket).
- **Evidence:** ramoops/mirror: `load_init_program`, `LAGUNA Sandbox: launchd op 2`, shared_region calls
  from pid 1. Device-proven already on the 141 path.

### M3 — dyld + first processes (the current phone wall)

- **[D] where the phone stands:** launchd execs; dyld maps the closure and dies — namespace 6
  `Symbol not found: _NDR_record` class on the v1 normalized closure; the closure work (split dylibs,
  tries, normalizer) is a **named dead-end** by ecosystem precedent (upstream-scout: nobody ships a
  cache-less or mini-cache dyld; ios-sweet ships the full IPSW cache and hard-links the cryptex cache —
  `ignition_level=1` makes dyld look there). DECODE.md §7.
- **What the full image has that the 24 MiB root does not:** the **native dyld cache path** —
  cryptex `/private/preboot/Cryptexes/{OS,App}` with the cache hard-linked into
  `/System/Library/Caches/com.apple.dyld`, **no split dylibs**, real dslocal identity records
  (laguna-dslocal kit: 940/940), real lsd/mapdb, Apple uid/gid/mode fidelity, cryptex-native
  SpringBoard/backboardd binaries. Every one of these is a solved in-image fact of
  `out/system.apfs 637103e1…` — not a device problem, a **transport** problem.
- **Config [Q]:** none beyond M2 — dyld bring-up is image content, not boot-args.
- **Felix playbook:** item **b** (HID ServicePlugins stash — ~3 min backboardd dyld stall; pocket,
  apply if seen), **f** (MobileGestalt CacheExtra pre-seed — foundation answers via real identity +
  mobilegestalt.xpc job; pre-seed is a speed item on device), **k** (cfprefsd/lsd/mobilegestalt
  RunAtLoad + `POSIXSpawnType=Interactive` — foundation uses stock plists; pocket if a 15-min ondemand
  stall appears), **j** (logd family) — **the foundation runs the logd family ON and proved it in QEMU**
  (the kernel's Mini paths serve it); felix's removal was for their kernel. Keep the foundation catalog
  as-is; revisit only if the 10-min firehose deadlock signature appears on device.
- **Evidence:** ramoops/mirror carry launchd's job-spawn lines (run-ios.sh prints launchd's log twice —
  dedupe when reading). dyld stderr needs the stderr→/dev/console wiring (deviceroot lever b) to be
  readable at all; without it, only the exit-reason line in the mirror.

### M4 — IOKit personalities (Mini* services)

- **Contract [Q]:** `BlazerPlatformExpert` (root compatible `google,lga-blazer`) inherits
  `GenericARMPE::start()`, which starts `MiniIOSurface`, `MiniIOMobileFramebuffer`,
  `MiniAppleKeyStore` + the rest directly after IOResources is live. **Exactly one Mini* start path** —
  never the old 0018/0019 personality stack, never a double start. `MiniIOSurfaceStart()` allocates
  `gSurfaceLock`/`gSurfaceKeptLock`/`gSurfaceClearing` **before** `IOCoreSurfaceRoot::start()`
  registers — the NULL-lock fix. IOMFB adopts `vinfo.v_baseAddr`, publishes TimingElements/ColorElements,
  names itself `disp0`; backboardd's probe selectors must answer clean: 57 (color remap), 27/17
  (3084-byte stateful get/set twin), 84/50/68 accept, 79 GetBlock; unknown output-only probes zero-fill.
- **Config [Q]:** `io.display.iomfb=1` is the E0 publish opt-in (already in the phone payload); on QEMU
  additionally `io.display.*=0` with ramfb. On device the DC9x00 gate discipline applies: `io.display.probe=0
  io.display.splash=0` **always** (a DC9x00 readback is an async SError → crash-reset, reboot reason
  `0xc00e`, "DER Event 20"; there is no stage at which `io.display.probe=1` is required).
- **24 MiB vs full image:** the 24 MiB root cannot reach this milestone (no backboardd to probe); the
  full image's stock backboardd is what exercises the selector set.
- **Felix playbook:** item **c**'s userland half — the empty `IOAcceleratorES` (`sgx`, **no
  `MetalPluginName`/`MetalPluginClassName`**) is already the blazer branch's design and kills Metal's
  30-s poll per process; do not publish a fake Metal device.
- **Evidence:** kernel printf start lines (ramoops/mirror); the selector probes appear as IOMFB `fbtrace`
  lines only with observability args. QEMU: run log.

### M5 — backboardd + renderer

- **Contract [Q]:** `kern.exec_inject` + the injected libraries (launchd strips `DYLD_*` from non-root
  jobs — a plist env var is NOT enough). Kernel-side AMFI stand-in (`kern_minimac.c`) registers policy
  name `AMFI`, answers op `0x5a` with `AMFI_DYLD_OUTPUT_ALLOW_ALL = 0xff` (and `0x60` = not-enforcing).
  `GL=1` at userland-build time makes backboardd the default injection target; `gl=1` at boot makes
  inject.c dlopen `libhakios.gl.dylib` (`userland/src/eagl.c` + `gl_osmesa.c`); Mesa softpipe ES3 via
  `glenv=OSMESA_SOFTWARE=1`; GLI table 409 slots/1032 max, byte-identical 20F66 vs 20H392; CA never
  calls `+setCurrentContext:` so the wrappers make the context current per call.
- **Config [Q] (device-facing profile, RENDER.md §9):** append to platform/root args:
  `gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1 fb_hz=60 fb_threads=3 fb_queue=2 fb_beat=1
  io.display.probe=0 io.display.splash=0 io.display.native=0 blazer_cpm=0 blazer_cpm_tx=0`;
  observability: `glframes=1 fbtrace=8 touch_lat=1 surftrace=24`. Start WITHOUT `ca_vbl=1`,
  WITHOUT `fbscale=2`, no `-v`. Never: `gpu=1`, `adreno=*`, `fb_scan=1`, `metal=1`.
- **Felix playbook:** item **d** (the CA_NO_ACCEL/CA_FORCE_SOFTWARE_RENDERER/MTL_FORCE_SW_RENDER env set)
  is felix's *image-content* substitute for a working bridge — with the foundation's GL bridge + empty
  IOAcceleratorES it is redundant; pocket it only if CA ever falls through to Metal retries. Item **e**
  (Reduce-Transparency/Motion/CoreMaterial prefs incl. ByHost) — pocket perf item for device first boot;
  QEMU proved without it, device CPU budget may not.
- **24 MiB vs full image:** full image has backboardd + Mesa + the GLI-table bridge baked (GL=1 build);
  the 24 MiB root has none of it.
- **Evidence:** kernel + launchd logs; the shim/inject log lines (`sweetgl:`-class) need the
  `/private/var/tmp` tee or EP0; `glframes=1` census lines in the mirror; CA
  `server has an accelerated renderer`.

### M6 — SpringBoard cascade

- **Contract [Q]:** launchd spawns the stock cascade; PosterBoard/GradientPosterExtension initialize;
  backboardd reports `System shell "com.apple.springboard" finished startup after …`. 600-second
  sessions are TOO SHORT: upstream TCG needed ~11 min to lock screen, 14 min to full wallpaper. The
  blazer-platform run: SB shell startup 26.75 s.
- **Config [Q]:** `launchd_disable_all_timeouts=1` (run 04) and `autounlock=0` matter here — without
  them the cascade's timeouts fire on a slow bring-up. These are **missing from the phone payload
  cmdline today**.
- **Felix playbook:** item **h** (migrator set = {CertUI, 00LaunchServicesMigrator} only; prune
  /Applications) and **i** (ControlCenter pre-seed) — foundation equivalent: the DENY list + inject_sb
  setup-bypass proved the same goal in QEMU; item **l** (16-job essentials) — foundation's 22-job
  `IOS_JOBS` catalog is the same idea and already proven; item **m** (activation) — done in-image.
  Item **o**: treat first device boot as First Boot (≥30 min, no-interrupt window), later boots fast.
- **24 MiB vs full image:** nothing SpringBoard-class exists in the 24 MiB root at all.
- **Evidence:** mirror/ramoops job lines + the shell-startup line; screenshots only via panel photo
  window on device (QEMU: watcher PNGs every 60 s + QMP screendump).

### M7 — Present to scanout

- **Contract [Q]:** CA SwapBegin → SetLayer → SwapEnd; MiniIOMFB presenter thread CPU-copies/scales
  BGRA into `boot_args.video`; vsync is a timer (selector 72 arms type 5, 60 Hz software beat — not a
  panel-mode claim); presenter cache hook: WC ⇒ DSB barrier before present, `CLEAN_ON_PRESENT` ⇒ flush
  to PoC after helper threads finish. Swaps 2–7 ms continuous [Q blazer].
- **Device delta [P]:** the scanout is NOT ramfb. Path A (preferred): a display-owning first stage runs
  the CPM/mailbox power sequence (queue reset `{0x00000100,0,0,0}` → DPU top ON → DSI0 ON → FE0 ON,
  exact frames in DPU-INIT.md §4), allocates a dedicated 1280×2856×32bpp linear scanout (stride 5120,
  `0xdf2000` bytes), and hands XNU a CRC-sealed 128-byte record via `/chosen` `laguna-display-handoff`
  `{PA,size}` + `laguna-display-forbidden`; `hakios_blazer_display_init()` validates (all flags or
  headless). `laguna.kexec_noshutdown=1` must skip `device_shutdown()` so the kexec stage cannot blank
  the panel/DPHY. Gate ladders: `blazer_cpm=1` (read-only) → `+blazer_cpm_tx=1` (power) →
  `io.display.native=1` (after review). Path B (native XNU producer) is design, not permission.
  **Never read DC9x00.**
- **Felix playbook:** none of the 15 items — this is RENDER.md/DPU-INIT.md territory; the felix display
  *pattern* (canvas = panel, refresh via boot-arg, continuously-armed trigger) is advisory only.
- **24 MiB vs full image:** irrelevant here (kernel + first-stage contract).
- **Evidence:** gate 0–7 checklist (RENDER.md §10): stamps for handoff validation; `fbtrace`/`surftrace`
  in the mirror for the selector/swap path; `fSwapsShown` advancing; **panel** for gate 7 — never call
  the Apple-logo BootUI frame "SpringBoard content". Discrimination table in RENDER.md §10 tail.

### M8 — Input + unlock

- **Contract [Q]:** virtio tablet/keyboard devices + `virtio_input=1` (harness-only); QMP-injected swipe
  unlocked at 842.66 s [run 04]; `touch_lat=1` gives `tl:` latency lines.
- **Device [P]:** Synaptics TCM over SSI `0x0db33000` — the port exists as a verified-but-unlanded patch
  (198/198 host checks, `git apply` clean on `317650c`; M2 or parent lands it via
  `git am research/laguna-touch/patches/0001-*.patch` in blazer-port on branch blazer). Gate
  `blazer_touch=1` (NOT `touchscreen=1` — that is the POCO Goodix/FocalTech path), thread starts late
  (`touch_delay`, 20 s default), samples → `/dev/hakios-touch` → inject.c synthesizes IOHID events.
  5-finger 1.5 s hold = exit-to-loader gesture. NEEDS-DEVICE-PROBE: AOC pad-slot layout, TBN bus state,
  CPM mailbox request format (rails/reset), LSIO clocks assumed-on from ABL.
- **Felix playbook:** item RANK 3c patterns only: declare the keymap as DT (`/sweet-keys` shape), clamp
  + scale touch coordinates to the *kernel canvas* (ours = 1280×2856, not 2092); do NOT reuse felix's
  numeric offsets (their frozen SWEET macho ≠ our build).
- **Evidence:** EP0/mirror `tl:` lines and the poll-loop minute status line; then the unlock swipe's
  visible result — panel.

---

## 2. What the 24 MiB bootstrap root has vs what the full image has

| Dimension | 24 MiB v6 `xnu.ramdisk` (`aebc9a8a…`, HFS+ iOSRoot) | Full `system.apfs` (`637103e1…`, APFS, 2.32 GiB) |
|---|---|---|
| Purpose | mount md0 + exec /sbin/launchd, nothing beyond | the whole stock userland cascade to SpringBoard |
| launchd | real `/sbin/launchd` 541,600 B (PerformAlways-patched) | stage0 as `/sbin/launchd` → tests → exec `/sbin/launchd.ios` |
| dyld | `/usr/lib/dyld` 922,912 B, **cache-less** (the wall) | **native cache path**: cryptex cache hard-linked into `/System/Library/Caches/com.apple.dyld` (`ignition_level=1` honored) |
| Libraries | 36 carved closure dylibs (+ tries, __OBJC_RO stripped) | stock cache + real framework dylibs, **no split dylibs** |
| Identity | minimal (launchd.plist D2-tweak) | full: iPhone10,3 identity, **dslocal records** (940/940), passwd/group, **real lsd/mapdb**, uid-0 fidelity, Apple uid/gid/mode |
| Services | keybag systembag.kb, lsd plists, LS seeding, logd stores, container shells (shells only) | 22-job catalog incl. logd family ON, backboardd, SpringBoard, PosterBoard, wifid, ioclass agents |
| Injection | none | `libhakios.gl.dylib` (+ .sb/.media/.app/.tweaks) baked, `GL=1` build |
| SpringBoard/backboardd binaries | **absent** | present (cryptex-native) |
| Delivery size | fits vendor_boot (raw payload < 64 MiB; kexec gz ~8 MB) | 2.32 GiB — kexec segments (16-seg cap, ≤½ RAM) or once-per-session staged transport |

Bottom line: the phone path today ends at M2 because M3's fix is *in the full image*, and the full image
is a transport problem, not a userland problem.

---

## 3. Top-5 gaps: QEMU-proven render setup vs the phone shim payload today

**Gap 1 — the kernel is the wrong lineage for rendering (personalities + AMFI + inject).**
Phone ships the 141.3 bridge mach: no `BlazerPlatformExpert`, no registered Mini* IOResources
personalities, no display-config selector set, no NULL-lock MiniIOSurface ordering, `laguna_amfi.c`
answers only sandbox ops 4/5/6/0x43 — **op 0x5a is unanswered, so dyld refuses DYLD_INSERT and the whole
`kern.exec_inject` renderer carrier is dead**; no `kern.exec_inject` library wiring. The QEMU render
proof ran the blazer branch (`317650c` lineage) where all of the above is solved. *Fix:* deliver the
blazer-branch kernel through the shim/kexec ladder (marker-patched payload + boot_args smuggle as
today), keeping the personalities REGISTERED (PORT-CONTRACT §5.B) and `kern_minimac.c`'s AMFI answer
intact. Feeds M4–M6.

**Gap 2 — the root is the 24 MiB cache-less closure, not the foundation APFS (content + transport).**
The v6 `xnu.ramdisk` provably dies in dyld (M3); the closure line is a dead end by precedent. The fix
already exists as `out/system.apfs 637103e1…` + `root.apfs de340c95…` (all old fixes solved in-image:
uid-0, cryptex-native cache, no split dylibs, mobile identity, real lsd/mapdb). Missing on the shim
path: (a) APFS support in the delivered kernel comes with Gap 1's blazer kernel — the 141 bridge mounts
HFS only; (b) the 2.32 GiB transport: kexec-segment staging per the iosroot manifest (16-seg cap,
≤½-RAM, `0x890000000` window) or upstream's staged-resident model (`xl ramdisk` equivalent), which
needs a working USB device transport (the CPM-first fix this lane owns); (c) the ADT must then carry
RAMDisk = the APFS image (and `RAMDisk1` = root.apfs for `/redwood`). The HFS tokens
`ignition_level=1 launchd_unsecure_cache=1` become the HFS path's contract, not the APFS fix. Feeds M1–M3.

**Gap 3 — no display scanout producer or handoff on the cold path.**
QEMU render had ramfb/GOP video handed over by the loader; the phone payload has `io.display.iomfb=1`
(the E0 opt-in) but **no CPM power path, no dedicated scanout, no 128-byte handoff record, no
`/chosen laguna-display-handoff` emission, no `laguna-display-forbidden` ranges** — the kexec stage
builds the 141-era ADT (`/laguna-fbcon`, `/laguna-dpu` nodes) instead, and ABL's transition ends in
`MIPI DPHY Power-off`, so "inherit ABL's buffer" is not a plan. Without a validated handoff the blazer
kernel must go headless (correct behavior) — i.e., M7 never happens. *Fix:* Path A first stage
(CPM `blazer_cpm=1` → `blazer_cpm_tx=1` power frames → vsi-drm linear scanout → CRC record), the shim
copies + advertises it, `BlazerPlatformExpert` engages on the first-stage ADT (`google,lga-blazer`
root compatible — the QEMU loader synthesizes an iPhone ADT, so the expert can only match on the
device contract). Everything CPM-shaped shares this lane's CPM-first transport work (CPM-FIRST-SPEC).
Feeds M7 (+M4's disp0 adoption).

**Gap 4 — no renderer carrier on the phone cmdline or kernel.**
QEMU-proof chain: `GL=1` image build → `gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1` boot args → exec_inject
loads `libhakios.gl.dylib` into backboardd → Mesa softpipe → GLI table. The phone payload has **none of
the renderer args** (no `gl=1`, no fb/vsync profile, no `fb_hz=60 fb_threads=3 fb_queue=2 fb_beat=1`),
and Gap 1's kernel lacks the AMFI 0x5a answer that makes injection legal. Also missing from the payload:
`autounlock=0` and `launchd_disable_all_timeouts=1` — the two args run 04 needed to survive the slow
cascade (M6). *Fix:* once Gaps 1–2 land, extend
`kx_build_cmdline`'s token list with the RENDER.md §9 baseline profile + the two cascade args; keep
`io.display.probe=0 io.display.splash=0` mandatory. Feeds M5–M6.

**Gap 5 — no input path (touch) on the phone.**
QEMU used `virtio_input=1` + QMP swipe; the phone payload has no input at all. The TCM driver is
verified-but-unlanded (`blazer_touch=1`, 198/198, patch ready to `git am`), its rails/reset ride the
CPM mailbox (request format still unlearned — soft reset is the recovery path), and the AOC pad-slot
layout + TBN bus state need the first device probe. Without it: lock screen renders but cannot be
unlocked, and a hung boot has no exit gesture (fallback stays watchdog/A-B). *Fix:* land the patch,
add `blazer_touch=1 touch_lat=1` (+ `touch_delay` default) to the profile, calibrate coordinates to the
1280×2856 canvas (felix 3c pattern: clamp+scale at source level, never their offsets). Feeds M8.

(Honorable mention — storage/persistence: both ends are RAM-only; `rootrw=1` writes die at reboot.
Tensor UFS ro→rw is a separate later project; felix's `ufs_scsi_write` shape (EVALUATION RANK 3b:
standard HCI offsets, 4 KB PRDT chunking, laguna MMIO base from the stock DT) is the reference when it
starts. Not on the M0→M8 critical path.)

---

## 4. Boot-args delta (QEMU-proven vs phone payload today)

| Token | QEMU render proof | Phone payload today | Verdict |
|---|---|---|---|
| `debug=0x8 serial=3 cpus=1 rd=md0` | yes (loader defaults) | yes | keep |
| `rootrw=1` | yes (root mount RW) | yes | keep (APFS too) |
| `autounlock=0` | yes | **absent** | add — M6 cascade survival |
| `launchd_disable_all_timeouts=1` | yes | **absent** | add — M6 cascade survival |
| `ignition_level=1 launchd_unsecure_cache=1` | no (APFS needs none) | yes | HFS-contract tokens; inert on APFS, keep only while the 24 MiB HFS root is the root |
| `kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1` | — | yes | hygiene, keep |
| `lagunalogdump=1` | — | yes | keep — the mirror is the M0–M6 evidence channel |
| `io.display.iomfb=1` | yes (+ `io.display.*=0` on ramfb harness) | yes | keep; add `io.display.probe=0 io.display.splash=0` explicitly |
| `gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1 fb_hz=60 fb_threads=3 fb_queue=2 fb_beat=1` | yes (§9 baseline) | **absent** | add at M5 |
| `blazer_cpm=0 blazer_cpm_tx=0` (gate OFF) / `=1` ladder | OFF on QEMU (no DPU) | absent | start OFF; the device ladder is Gap 3 |
| `blazer_touch=1` (+ tuning) | virtio instead | **absent** | add at M8 after the patch lands |
| `virtio_input=1 virtio_sound=1` | harness-only | absent | **never** on the phone |
| `gpu=1 adreno=* fb_scan=1 metal=1 io.display.probe=1` | never | absent | never |

---

## 5. Summary (5 lines)

1. The proven path is one image and one kernel: blazer-branch kernel (`317650c` lineage, Mini*
   personalities registered, AMFI 0x5a=0xff, exec_inject) + `out/system.apfs 637103e1…` as resident md0
   (+ root.apfs md1) → stage0 → real launchd → 22-job cascade → softpipe via libhakios.gl.dylib →
   MiniIOMFB CPU-copy presenter; QEMU proved lock screen (upstream ~11 min TCG; blazer SB shell 26.75 s,
   swaps 2–7 ms) and home screen with `debug=0x8 serial=3 cpus=1 rd=md0 autounlock=0
   launchd_disable_all_timeouts=1`.
2. The phone shim payload reaches M2 (launchd exec) on the 141 lineage with the 24 MiB HFS v6 root and
   dies at M3 — cache-less dyld is a precedent-named dead end; the fix is in the full image, making
   Gaps 1–2 (kernel lineage + APFS transport) the critical pair, not userland work.
3. Top-5 gaps in order: (1) 141 mach lacks BlazerPlatformExpert/Mini*/AMFI-0x5a/exec_inject;
   (2) 24 MiB closure root vs 2.32 GiB APFS + its segment/staged transport; (3) no CPM-powered scanout
   producer or 128-byte handoff on the cold path (DPHY dies at ABL transition; never read DC9x00);
   (4) no renderer/cascade boot-args on the payload; (5) no touch — TCM patch verified 198/198 but
   unlanded.
4. Felix's 15-item playbook is mostly already solved *differently* inside the foundation image (datamigrator
   DENYed + setup forced done instead of sentinel; logd family ON and served by Mini kernel paths;
   curated 22-job catalog ≈ felix's 16) — pocket items only: HID-plugin stash, MobileGestalt CacheExtra,
   Reduce-Transparency prefs, CA_NO_ACCEL env set, ACCHW stash; budget ≥30 min first device boot.
5. Evidence channels per rung on the phone: stamps (`0x95400100`/`0x95401000`) for M0, ramoops
   (`0x95200000`) + the `lagunalogdump` mirror (`0x95440000`, retrievable via `fastboot oem ramdump
   stage_file kernel.log` — proven in abl.log) for M1–M6, fbtrace/surftrace + `tl:` for M7–M8, and the
   panel photo window as the only gate-7 arbiter; EP0 joins as the live channel once CPM-first lands.
