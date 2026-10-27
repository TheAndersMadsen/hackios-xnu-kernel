# AMFI-PLAN — security-policy map for the hakios kernel (Q2 lane, 2026-10-27)

Kernel audited: `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch hakios),
build `SDKROOT=iphoneos ARCH_CONFIGS=ARM64 MACHINE_CONFIGS=BCM2837 KERNEL_CONFIGS=DEVELOPMENT`
(`kernel-blazer/xnu-build.sh:7`) → **iOS-target DEVELOPMENT kernel** (`XNU_TARGET_OS_IOS`,
`XNU_TARGET_OS_OSX` compiled out — makedefs/MakeInc.def:70/87).

## 0. TL;DR verdict

1. **AMFI cannot panic post-NVRAM in this kernel: there is no AMFI kext.** The hakios branch
   replaced the closed AppleMobileFileIntegrity with two in-kernel stand-ins:
   `libkern/amfi/miniamfi.c` (the `amfi_t` function-pointer interface) and
   `bsd/kern/kern_minimac.c` (MAC policies named "AMFI" and "Sandbox" that deny nothing).
   The pandora `amfi-only-platform-code` wall does not apply to us — that property is read by
   the *real* AMFI kext, which pandora/UniXNU prelink and we do not have. **No /chosen AMFI
   property is read anywhere in this tree** (grep-verified tree-wide, incl. hakios.patch).
   mkadt.py therefore got **zero property additions** (comment-accuracy fix only, byte-identical
   output — see §6).
2. **No boot-arg is required for unsigned/development userland to exec.** No policy ever sets
   CS_KILL/CS_HARD/CS_ENFORCEMENT; every exec kill path is gated on flags nothing sets
   (§3). The existing shim boot-args contract (`rd=md0 rootrw=1 ignition_level=1
   launchd_unsecure_cache=1` + runner flags) is already sufficient. Optional diagnostics:
   `cs_debug=1` (kern_cs.c:182). Optional hard-off switch that DOES work in this build:
   `cs_enforcement_disable=1` (kern_cs.c:166-172 — honored because CONFIG_CSR is not compiled,
   so `PE_i_can_has_debugger()` is unconditionally true, pexpert/i386/pe_init.c:387-399).
3. v55's QEMU frontier already passed AMFI/Sandbox init (brief: "reaches AMFI/Sandbox → PMRD
   ON_STATE"); the device ladder repeats the identical code path — **no AMFI-specific device
   wall is predicted between NVRAM and root-mount**.

## 1. What "AMFI" is in this kernel

| Piece | File | Role |
|---|---|---|
| `amfi_t` interface | libkern/amfi/amfi.c:5-14 | global `amfi` pointer; panics ONLY on double registration (impossible: sole registrant is miniamfi) |
| miniamfi | libkern/amfi/miniamfi.c:258-263 | `amfi_interface_register(&miniamfi)` at `STARTUP(EARLY_BOOT, STARTUP_RANK_FIRST)` — before any consumer can run |
| miniamfi entitlements | libkern/amfi/miniamfi_entitlements.cpp | entitlement queries answered from the process's OWN embedded signature plist; unsigned ⇒ no entitlements |
| "AMFI" MAC policy | bsd/kern/kern_minimac.c:46-62,113-123 | `mpo_vnode_check_signature` accepts EVERY signature: sets CS_SIGNED, and CS_PLATFORM_BINARY for ad-hoc (58-60). Never sets CS_KILL/CS_HARD/CS_ENFORCEMENT/CS_REQUIRE_LV |
| "Sandbox" MAC policy | bsd/kern/kern_minimac.c:147-361 | all `__mac_syscall("Sandbox",…)` allowed (0); req 4 answers container path from `CFFIXED_USER_HOME` env; `mpo_priv_grant`: PRIV_WORK_INTERVAL always, others by embedded entitlement, `privs=0` TUNABLE disables (316) |
| registration | osfmk/kern/startup.c:726-727 | `minimac_policy_init()` in kernel_bootstrap after `mac_policy_initmach()` (718) and `vnguard_policy_init()` (721-723) |

The v54 console "AMFI/Sandbox init lines" = these `kernel_bootstrap_log` stages + "MAC
Framework successfully initialized" (security/mac_base.c:380). Init is unconditional and cannot
fail: `mac_policy_register` panics only on NULL conf fields / EEXIST duplicate name
(mac_base.c:617-626,675-681) — single registrants, all static.

## 2. AMFI-side consumers at runtime (all benign)

- `bsd/kern/kern_trustcache.c:86-89,257-260` — panics only if `amfi == NULL` (never: miniamfi
  registers at EARLY_BOOT) or interface version stale. Trust-cache uploads get
  `kTCReturnUnsupported` (miniamfi.c:128-141) = refused, not fatal.
- `iokit/bsddev/IOKitBSDInit.cpp:1528,1559,1592` — IOVnodeHasEntitlement/IOTaskHasEntitlement
  answered from the process's own signature (miniamfi_entitlements.cpp).
- `osfmk/arm/pmap/pmap.c:12395-12599` — `amfi->TrustCache.*` consumers are all inside
  `#if PMAP_CS_PPL_MONITOR` → **compiled out** (BCM2837 board config sets neither `XNU_MONITOR`
  nor `PMAP_CS`, pexpert/pexpert/arm64/board_config.h:90-98; PMAP_CS_PPL_MONITOR = XNU_MONITOR,
  osfmk/vm/pmap_cs.h:59-65) ⇒ `CODE_SIGNING_MONITOR 0` (bsd/sys/code_signing_internal.h:40-47).
  `csm_initialize_provisioning_profiles`' `coretrust == NULL` panic (kern_codesigning.c:293-296)
  is inside `#if CODE_SIGNING_MONITOR` → compiled out; startup.c:731-734 likewise skipped.

## 3. Exec-path kill audit (why unsigned code runs)

Every place execve/posix_spawn can kill, and why it cannot fire here:

| # | Gate | Site | Why it passes |
|---|---|---|---|
| 1 | `load_code_signature` failure | bsd/kern/mach_loader.c:1288-1294 | downgrade to LOAD_SUCCESS unless `cs_process_global_enforcement()` = `cs_process_enforcement_enable` = **0** (kern_cs.c:111-117; `CONFIG_ENFORCE_SIGNED_CODE` undefined — not even declared in config/MASTER) |
| 2 | invalid initial pages | mach_loader.c:1319-1336 | LOAD_FAILURE only if global enforcement or `CS_HARD\|CS_KILL\|CS_ENFORCEMENT` in csflags — **nothing ever sets those flags** (miniamfi vnode_check_signature sets only CS_SIGNED/CS_PLATFORM_BINARY); worst case is `csflags &= ~CS_VALID` |
| 3 | `ip_mac_return != 0` | bsd/kern/kern_exec.c:6704,7611-7619 | set from `kauth_proc_label_update_execve` → MAC `cred_label_update_execve` hook — **neither stand-in implements it** ⇒ 0 |
| 4 | launch constraints | bsd/kern/kern_exec.c:7628-7631 → security/mac_process.c:902 | iterates registered policies' `mpo_proc_check_launch_constraints` — **none implement it** ⇒ error 0 (the fatal-desc panic at mac_process.c:953 is unreachable with no hook) |
| 5 | PLATFORM_IOS spawn gate | kern_exec.c:7650-7700 | entire block is `#if XNU_TARGET_OS_OSX` → **compiled out** (iphoneos build) |
| 6 | taskgated upcall | kern_exec.c:7686-7737 | unsigned (no CS_SIGNED) ⇒ `task_get_task_access_port` finds no port ⇒ `error = 0` (require_success is FALSE) |
| 7 | x18-ABI check | osfmk/arm64/machine_task.c:262-302 | fails only for `com.apple.private.custom-x18-abi`/`uexc` entitlements under `__ARM_KERNEL_PROTECT__`; miniamfi returns NOT_FOUND for those ⇒ success |
| 8 | CS_KILL/CS_HARD at exit of process_signature | kern_exec.c:7596,7806-7820 | flags never set (see #2); `CS_KILLED` set only on a *previous* error |

System-wide enforcement defaults: `cs_system_enforcement_enable = 1` (kern_cs.c:110 — always),
`cs_process_enforcement_enable = 0` (kern_cs.c:117, no CONFIG_ENFORCE_SIGNED_CODE), library
validation off. The only practical effect of system enforcement: dyld shared-cache mappings must
be signature-covered (bsd/vm/vm_unix.c:2657-2680) and the cache file must be root-owned
(vm_unix.c:2480-2502) / on the root or preboot-cryptex volume (vm_unix.c:2539+ `#else
CONFIG_CSR` branch — **CONFIG_CSR is not compiled in this kernel**: `kern_csr.c optional
config_csr` bsd/conf/files:428; zero `csr*` symbols in mach.blazer-r4r5-tcm, nm-verified).
N1's QEMU run already proved the stock A11 cache passes (cache probed, 0 refusals).
`cs_enforcement_disable=1` zeroes these too (kern_cs.c:166-172) — available as an escape hatch
if we ever map a self-built cache.

## 4. vnguard

`CONFIG_VNGUARD` here = **BSD vnode guards** (kern_guarded.c, `standard` in bsd/conf/files:432),
MAC policy name "vnguard" (kern_guarded.c:1505, registered at startup.c:721-723 →
vnguard_policy_init:1524). It gates guarded_open'd files (rename/unlink/write) per
`vng_policy_flags` — default **0 = deny nothing, kill nothing** (kern_guarded.c:1181,1194-1296;
boot-arg `vng.policy` can arm EPERM/log/kill). It is NOT iOS's VerificationGuard and NOT an exec
gate. No action needed.

## 5. Pandora / UniXNU precedent reconciliation

- pandora (`research/upstream-scout-20261026/snapshots/pandora-data-a-live/tools/
  mkadt_pandora.py:224-228`): `/chosen amfi-only-platform-code = 0` — "read by the ORIGINAL
  AMFI (prelinked), which panics 'no amfi-only-platform-code'".
- UniXNU (UniXNU-sheng/udon/zeta make-*-adt.py; docs/concepts/security.md:38): same property = 1
  + `[kernel.prelink] early_security_init` runs the real AppleMobileFileIntegrity initializer.
- Both run Apple's closed AMFI kext, hence the property. **Our lineage has no prelinked AMFI and
  no reader for that property anywhere** (tree-wide grep incl. hakios.patch: only
  miniamfi/minimac comments match). Adding it would be an unread node — the ADT-NODES.md rule is
  to not add those. Consequence for the v56 prediction list: **the "AMFI
  amfi-only-platform-code wall" predicted in the brief can be struck**; the next walls after
  NVRAM are the /arm-io driver matching and MiniIOMobileFramebuffer attach already identified.

## 6. mkadt.py decision + verification (done this wave)

- **No property additions** (nothing reads AMFI/security /chosen props in this kernel). The
  `/chosen/asmb` node (lp-sip0=0x6f, lp-sip1=1) that mkadt already emits is an **inert** node
  today: its reader is kern_csr.c `csr_bootstrap` (bsd/kern/kern_csr.c:163-218), compiled only
  when CONFIG_CSR is on, which it is not (§3). Its comment claimed the shared cache "can only be
  mapped with CSR_ALLOW_UNRESTRICTED_FS" — wrong for this build; corrected to state inertness +
  the actual vm_unix.c:2539 mount-identity gate. Comment-only change.
- Byte-verification (host, /private/var/folders/…/T/opencode/q2-amfi/):
  1. A/B with fixed seed: pre-edit vs patched mkadt.py — `device-v55` (0xd0000000+0x0A000000),
     `device-root7` (+0x0A400000), `qemu` → **BYTE-IDENTICAL** (18836/18836/13304 B).
  2. Patched mkadt.py at v55 geometry vs the shipped v55 ADT
     (`research/laguna-usb-console/out/next-boot-v55/laguna.adt`, sha e2a41538dcbd3e8b) with
     random-seed normalized (per-boot random by design) → **BYTE-IDENTICAL**.
  3. Property census on a fresh root7-geometry ADT: 15/15 PASS — 4 NVRAM CHRP props
     (bank-size 0x2000 / bank-count 1 / current-bank 0 / proxy-data 8 KiB adler-consistent),
     asmb lp-sip0 0x6f + lp-sip1 1, /options, gic `interrupt-controller "master\0"`, timer,
     cpu-debug-interface, /pram + embedded-panic-log-size 0x80000, dram-size 0x0A400000,
     and `amfi-only-platform-code` correctly ABSENT.
  4. AVB: `avbtool.py verify_image` PASS on the v55 image whose ADT is byte-reproduced by the
     patched generator — a scratch v56 rebuild would be byte-redundant (only the nonce/seed
     differ), so no scratch image was built (disk-tight rule).

## 7. Permissive-development profile for root-v7 (recommendations to Q5/Q4)

Kernel side (this audit): nothing required. Userland-facing recommendations:

1. **Ad-hoc sign every root-v7 binary/dylib** (`codesign -s -`): miniamfi then grants
   CS_SIGNED + CS_PLATFORM_BINARY (kern_minimac.c:57-60). Stock launchd refuses to manage system
   daemons whose executable is not a platform binary — an UNSIGNED dev tool exec'd BY launchd is
   the one trap this kernel does not cover (the kernel itself never kills it).
2. **Entitlements = embed them in the ad-hoc signature** (XML plist in the code-signature blob):
   miniamfi_entitlements.cpp answers entitlement queries from the running process's own blob;
   minisandbox's privilege table (kern_minimac.c:274-315) grants ~35 kernel privs that way.
   Unsigned processes hold no entitlements (by design, not by denial).
3. **`privs=0`** boot-arg exists as fallback if entitlement-derived privileges misbehave
   (leaves only PRIV_WORK_INTERVAL, the pre-privs posture).
4. Boot-args: keep the P3 contract verbatim (`rd=md0 rootrw=1 ignition_level=1
   launchd_unsecure_cache=1` + runner flags `debug=0x8 serial=3 cpus=1
   io.display.iomfb=1 kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1
   lagunalogdump=1`). Do NOT add amfi_*/cs_* bypass args — they parse but are consumed
   nowhere (kern_codesigning.c:75-139 sets CS_CONFIG bits with no reader at monitor=NONE).
   Optional first-boot diagnostics: `cs_debug=1` (verbose page-signature logging, kern_cs.c:182).
5. Sandbox: `__mac_syscall("Sandbox", …)` applying profiles succeeds and applies nothing;
   container queries answer from CFFIXED_USER_HOME (kern_minimac.c:144-225) — set that env var
   for processes that need a container (PlugInKit-hosted extensions).

## 8. Open items

- Q5: confirm root-v7 launchd jobs are ad-hoc signed (platform-binary expectation), and whether
  any daemon needs an embedded entitlement (network delegation etc. — kern_minimac.c:249-300).
- If a future kernel re-enables CONFIG_CSR, `/chosen/asmb` is already on the ADT with
  `csrutil disable` semantics; also re-check `scdir_enforce` (vm_unix.c:408-422).
- If we ever build our own dyld shared cache: either sign it page-consistently or add
  `cs_enforcement_disable=1` (works, §3) — but expect the root-owned + root-volume checks to
  still apply (vm_unix.c:2480-2502).
