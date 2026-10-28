# LAUNCHD-NEXT — launchd post-bind readiness on root-v9 (the boot AFTER the indirect fix)

Lane: **K3 (launchd post-bind readiness)** · 2026-10-27 · **host-only** (no device, no boots).
Mission: map exactly what launchd does in its first minutes post-bind, what root-v9 lacks, and
pre-arm the next boot — sequence model + gap table + top-5 walls with A/B discriminators.
Refreshes K5's POST-LAUNCHD-MAP.md W1–W10 for the post-v61 world (**R1 correction folded in**:
`map_with_linking_np` EINVAL is a silent non-fatal fallback — dyld recovers in-process — so the
post-bind sequence starts one dyld-validation after F1's fix point, much sooner than the original
table assumed).

Image inspected: `research/laguna-usb-fix/root-upgrade/root-v9.hfs`
sha-256 `a427241dfa71e5088ff1f42695d43018798b64294f53254c001cfb519bba0c17` — **identical before
and after inspection**. Mount provenance (honesty note): macOS refused a second attach of the
same backing file ("Resource busy"); all inspection went through lane F1's pre-existing
**read-only** mount at `/private/tmp/f1work/v9-extract` (byte-identical, hash re-verified, no
mount created or detached by this lane, F1's mount left alive).

Reads: POST-LAUNCHD-MAP.md (K5) · ../laguna-sigtrap/REPORT.md §1–§24 (the QEMU launchd cascade —
the only launchd-execution evidence that exists) · SYSCALL-DELTA.md (F5) · UPSTREAM-SOURCES.md +
its §4 correction (R1) · f2-qemu-ab + r1/r2 bus entries · dyld-1066.8 source at
`/tmp/r2-dyld-1066` (verified: `PROJECT:dyld-1066.8`, Loader.cpp mwli fallback at 1676–1692) ·
the kernel tree the device runs (`xnu-8796.121.2`, DEVELOPMENT_ARM64_BCM2837 — file:line below).

---

## 0. Verdict (30 seconds)

- **BLOCKING image items: ZERO.** Root-v9 already carries everything launchd needs to reach
  main, parse its config, and run its first minutes in *diagnosis-grade* form. The boot after
  F1's indirect fix needs **no rootfs delta** to be pre-armed — only the EP0 logger running
  from before boot (§7).
- **First post-bind console signature** (the M11 milestone):
  `com.apple.xpc.launchd|1970-01-01 00:00:NN.NNNNNN <Notice>: hello` followed by
  `Darwin Bootstrapper Version 7.0.0: …; root:libxpc_executables-2462.140.4.702.1~1/launchd/RELEASE_ARM64`
  and a **free boot-args echo** (`boot-args = debug=0x8 serial=3 …`) — all verified as strings
  in the actual root-v9 launchd binary and observed live from the same launchd family in the
  sigtrap QEMU runs (serial.log of `evidence/qemu-run-sigtrap-04/`).
- **Log-visibility verdict: dyld's stderr is dead (write(2)=9 = EBADF, bytes dropped — nothing
  "went" anywhere), but launchd's own logs ARE expected console-visible.** The kernel mounts
  devfs *before* exec'ing launchd and devfs auto-creates `/dev/console`; launchd opens it itself
  (os_log_simple → `LogToConsole` → console driver → serial=3 → EP0). Caveat: `/dev/console`
  writes go to the cn console only — **EP0 live.log, not the ramoops console ring** — so the
  ramdump keeps only the kernel-side trace. That trace is rich: `systrace=1` is LIVE in the
  running r4r7 mach (device-proven on v60/v61) and prints *every failing PID-1 syscall* —
  including the `posix_spawn(…) = 2` storm, kernel-side (§3 T8).
- **launchd survives the job-spawn storm by design** (throttles, never exits). Expected terminal
  state after the fix: launchd alive, ~108 eager jobs each failing to spawn with ENOENT, console
  alive indefinitely, reader ladder stays `last=DYLD` (no rung exists past DYLD — the console is
  the evidence; do not misread a successful boot as a stalled one).

---

## 1. What launchd IS on root-v9 (all verified on the bytes this rotation)

| Fact | Value | Evidence |
|---|---|---|
| Binary | `/sbin/launchd`, 541,600 B (= K1's ground-truth size) | mount census |
| Build identity | **Darwin Bootstrapper Version 7.0.0: Thu Feb 1 20:33:09 PST 2024; root:libxpc_executables-2462.140.4.702.1~1/launchd/RELEASE_ARM64** (iOS 16.7-era) | launchd strings 2666–2668 |
| dyld | `/usr/lib/dyld` — `PROJECT:dyld-1066.8` | dyld strings |
| Closure | 36 dylibs (`libSystem.B`, libxpc, libdispatch, libsystem_trace/os_log, …) + dyld | Mach-O scan: 38 Mach-O files = 1 EXEC + 36 DYLIB + 1 dylinker |
| Config source | **`/System/Library/xpc/launchd.plist`** (+ `.sig`), VersionNumber **7** — the consolidated "D2" plist; launchd carries the exact path + all six top keys as strings | strings 1149/1120/1158/1161/1165; plist dump |
| Job census (my method) | **530 LaunchDaemons · 1,684 MachServices · 54 RunAtLoad + 77 KeepAlive = 108 eager** (+ 421 AppExtensions, 16 AppRemovalServices, 1,998 SystemLibraryTreeState) | plistlib on the mounted plist. K5 quoted "1,656 MachServices / 123 eager" on the same bytes — bookkeeping-method difference only (plists are byte-identical v8↔v9 per N1r); substance agrees |
| xpcproxy class | **398/530 jobs declare non-root UserName** → "always use xpcproxy" (absent binary); 132 root jobs → elidable direct-spawn class | plist census; strings 590–592 |
| First eager job (plist order) | `com.apple.factory.NFQRCoded` → `/Developer/usr/local/libexec/NFQRCoded` (RunAtLoad+KeepAlive) | plist order preserved in binary plist |
| `kern.initproc_spawned` | written BY launchd (string 1101); kernel reads it only to word the initproc panic (`kern_exit.c:1306–1320,1809`) — "initproc exited" vs "initproc failed to start" is a **free dyld/launchd boundary discriminator on every panic line** | strings + kernel source |

**K5 census claim — verified independently:** launchd is the only Mach-O **executable** on the
image; `/usr/libexec` is EMPTY; `/sbin` holds only launchd. My full census: 211 paths = 56
regular files + 6 symlinks + 149 dirs (K5's "199 files" was a path-count variant; the claim's
substance — no daemon binaries anywhere — is CONFIRMED).

**Boot-arg refreshes (v58 lesson applied — nothing armed without a reader):**
- `launchd_unsecure_cache=1` — **NO reader in this launchd build** (0 "unsecure" strings in
  3,160). K5's "requires launchd_unsecure_cache=1" refreshes to: *inert but harmless*; the
  consolidated plist is read unconditionally. The `.sig` sibling: no CMS/verify strings either —
  not checked by this build (and the plist is stock build-matched anyway).
- `ignition_level=1` — **no libignition anywhere in the closure** (0 hits in launchd + the four
  candidate dylibs). The sigtrap QEMU launchd (2462.**120**.4, Apr 2023) printed libignition
  lines; ours (2462.**140**.4.702.1, Feb 2024) will NOT — expect no `hello from launchd.ios.1`.
  Inert; harmless.
- Boot-args launchd DOES read (strings 2583–2605): `launchd_disable_all_timeouts=`,
  `launchd_proxyless_spawn=`, `launchd_instance_limit=`, `launchd_usr_abandon_unmanaged=`,
  `launchd_verbose_coalition_logs=`, `launchd_ios_sessions=`, `launchd_upward_connections_support=`
  — all unset, all optional.
- `systrace=1` — **live** in the running mach (v61 console carries its lines; r4r7 has the
  implementation). Prints **failures only** (v61: 12 lines in the ~100 ms dyld window, every
  line an errno). Keep it armed: it is the kernel-side storm trace (§3 T8).

## 2. Log-visibility verdict — where dyld's write(2)=9 went, and why launchd differs

1. **dyld (pre-main): invisible, by construction.** PID 1 is exec'd with an empty fd table;
   dyld's error writes target fd 2 → `EBADF(9)`; the kernel write path drops the bytes —
   nothing went anywhere; there is nothing to retrieve (F5 already marked it). The exit-reason
   description (800-char carrier, `kern_exit.c:1798/1828`) remains the only pre-main dyld
   message channel — exactly how v60/v61's walls were named.
2. **launchd (post-main): console-visible.** Chain, every link verified:
   - kernel mounts devfs **before** `load_init_program` — `bsd_init.c:1002–1006`
     (`devfs_kernel_mount("/dev")`, `#if DEVFS`), plus the hakios device inits immediately after;
   - devfs auto-creates the console node at mount — `devfs_vfsops.c:116` ("console", 0622
     root:wheel);
   - launchd opens `/dev/console` (string 940) and emits via **os_log_simple** (string 2377:
     "…os_log_simple logs dropped") in the `"%s|%s"` console format (string 2381) →
     `LogToConsole` (string 2612) → cn console → serial=3 → **EP0**;
   - **precedent, same launchd family, logd never up**: sigtrap QEMU serial.log shows
     `hello` / Bootstrapper banner / boot-args echo / boot-task ladder / spawn-machine lines —
     all on serial at epoch-clock timestamps (evidence/qemu-run-sigtrap-04/serial.log:971–1009,
     1037–1081).
3. **Channel discipline (operationally critical):** `/dev/console` writes go to the **cn
   console only** — i.e. the EP0 serial stream captured by `live.log` — **NOT** into the
   pstore/ramoops console ring (that records kernel console output). A parked-phone ramdump of
   a successful post-bind boot will contain **kernel systrace lines but no launchd lines**.
   The EP0 logger must be running from before the boot (it already is standard practice —
   every attempt dir carries `live.log`).

## 3. First-minutes sequence model (T-steps, console signatures)

Timeline anchor: v61's dyld phase ran 44.19→44.29 s. After F1's fix passes, dyld applies fixups
in-process (mwli fallback, R1-arbitrated, Loader.cpp:1676–1692), runs initializers
(`runInitializersBottomUpPlusUpwardLinks`, DyldAPIs.cpp:1500/3542) and jumps to LC_MAIN within
milliseconds. T0 = that moment.

| T | What launchd does | Console signature (EP0 live.log) | Kernel-side (ramdump/pstore) |
|---|---|---|---|
| T0 | dyld tail — fixups, initializers, main handoff | *nothing* (stderr dead) | nothing (systrace prints failures only) |
| T1 | main entered; console opened; **banner** | `com.apple.xpc.launchd\|1970-01-01 00:00:NN <Notice>: hello` ← **THE M11 MILESTONE** | `kern.initproc_spawned` flips to 1 (launchd writes it; no reader — surfaces only in future panic wording: "initproc exited") |
| T2 | bootstrapper banner | `Darwin Bootstrapper Version 7.0.0: Thu Feb  1 20:33:09 PST 2024; root:libxpc_executables-2462.140.4.702.1~1/launchd/RELEASE_ARM64` | — |
| T3 | cmdline echo (free pin audit) | `boot-args = debug=0x8 serial=3 cpus=1 io.display.iomfb=1 …` (the FULL payload cmdline — confirms which image actually booted) | — |
| T4 | environment restore | `Restore environment starting.` | — |
| T5 | system domain up | `(system) <Notice>: entering ondemand mode` + `(system) <Notice>: created` | — |
| T6 | boot-task ladder — 12 tasks, each expected to SKIP | `(fsck)`, `(mount-phase-1)`, `(data-protection)`, `(finish-obliteration)`, `(detect-installed-roots)`, `(mount-phase-2)`, `(MSUEarlyBootTask)`, `(fips)`, `(keybag)`, `(usermanagerd)`, `(dirs_cleaner)`, `(xpcroleaccountd)` — each `<Notice>: Skipping boot-task` (+ `[System Event] … [Subsystem: launchd]` mirrors). All 12 task names + helper paths (`/usr/libexec/darwinos-boot-task`, `dirs_cleaner`, `keybagd`, `HorizonSetup`, `cc_fips_test`, `FinishRestoreFromBackup`, `tzinit`, `init_data_protection`) verified as strings in OUR launchd (embedded builtin plist) — helpers all ABSENT from root-v9, but tasks skip **before** exec (QEMU precedent: all 12 skipped) | — |
| T7 | config import: `/System/Library/xpc/launchd.plist` — 530 jobs / 1,684 MachServices | mostly silent on success (QEMU precedent); possible tolerated-fallback lines: `No MRM cache found` / `No service cache` / `Unable to load cache`; on the RO root also possible `Unable to unpack stderr` / stderr-directory warnings (see §4 W-row) | `systrace: launchd[1] open/stat(…) = 2` lines for absent optional paths |
| T8 | spawn machine — **the ENOENT storm** | per eager job (plist order; first = NFQRCoded): `service will attempt to elide xpcproxy` (132 root jobs) or `service will always use xpcproxy because …` (398 non-root) → `service state: spawn scheduled` → `service state: spawning` → then either `posix_spawn(/usr/libexec/xpcproxy) failed: 2: No such file or directory` (non-root class — xpcproxy absent) or `posix_spawn(<program path>) failed: 2: …` (elided class) | **`systrace: launchd[1] posix_spawn(…) = 2`** — every failed spawn, kernel-side, greppable |
| T9 | throttle equilibrium (terminal steady state) | `Service only ran for %llu seconds. Pushing respawn out by %llu seconds.` / `service exceeded successive crash limit. launch will be throttled` / `cannot spawn: service is throttled` (strings 625–628/760–761/1268–1356) — retries settle; **launchd alive, PID 1 never exits, NO panic, indefinitely** | systrace stream dries up as throttles engage |

Launchd's own stderr wiring for jobs (`stderr path = %s`, `Unable to unpack stderr`,
`/private/var/log/com.apple.xpc.launchd` — strings 1604/1032/2573) targets a directory that
**exists** on root-v9 but sits on the read-only mount → per-spawn EROFS warnings, non-fatal
(§4). Note execdiag stays silent during the storm: ENOENT dies at namei, before any Mach-O
gate K1's execdiag covers — absence of execdiag lines is EXPECTED, not a regression.

## 4. Gap table — every early requirement × present/absent × BLOCKING vs DEFERRED

**BLOCKING: none.** Nothing absent from root-v9 can stop launchd from reaching T9.

| # | Requirement | Status in root-v9 | Class | Exact fix / owner |
|---|---|---|---|---|
| G1 | launchd binary + complete closure (binds) | PRESENT (537/537 binds, N1r; 36 dylibs) | — | F1's indirect fix is the only remaining closure defect (dyld-side, not an image-gap) |
| G2 | dyld (`/usr/lib/dyld`, 1066.8) | PRESENT | — | — |
| G3 | Job config (`/System/Library/xpc/launchd.plist` + extensions.plist) | PRESENT, build-matched, VersionNumber 7 | — | — |
| G4 | `/dev` + `/dev/console` at PID-1 exec | PRESENT **kernel-side** (devfs mounted pre-exec, console node auto-created) — image's `/dev` dir itself empty, irrelevant | — | — |
| G5 | Writable launchd state (`/private/var/log/com.apple.xpc.launchd`, `/private/var/db/com.apple.xpc.launchd/config`) | dirs PRESENT; mount is READ-ONLY (`rootrw=1` has **no reader** in-tree — K4/F5) → writes fail EROFS | DEFERRED | kernel-lane lever (honor rootrw / mount rw), NOT an image item (RAM md0 mounts RO by construction). Expected impact: log lines only; launchd is designed for RO system volumes (sealed iOS) — data-volume writes are best-effort |
| G6 | `/private/var/db/diagnostics{,/timesync}` + `uuidtext` (logd stores) | PRESENT (272:272); logd binary ABSENT | DEFERRED | v3.9 full-root; launchd console + systrace cover diagnosis until then |
| G7 | lsd seeds (`/private/var/db/lsd/*.plist`, MobileCoreServices LS plists) | PRESENT (dormant — no lsd binary) | DEFERRED | v3.9 (sigtrap budget: first LS rebuild 25–47 min on a FULL root — irrelevant here) |
| G8 | `/private/etc/passwd,group` | PRESENT | — | — |
| G9 | `/private/var/cores` | **ABSENT** (the v60/61 `coredump … failed: error 2` line) | DEFERRED, cosmetic | empty dir 0755 root:wheel — kills one console noise line; core would land on RAM md0 and is unretrievable via pstore anyway (F5 concurs). **The only candidate image delta** (§6) |
| G10 | `xpcproxy` (`/usr/libexec/xpcproxy`) | ABSENT | DEFERRED, low value | adding it only halves the storm noise and moves the failure one hop later (`xpcproxy could not exec: 2`) — REJECTED for v10-plus (adds a Mach-O exec = new dyld validation surface); v3.9 |
| G11 | 530 job program binaries | ALL ABSENT (by design — 28 MiB diagnosis-grade closure) | DEFERRED | v3.9 UFS transport of full `system.apfs` (2.31 GiB) — frozen plan, ROOT-TRANSPORT.md / ufs-staging/PLAN.md |
| G12 | jetsamproperties plists (`/System/Library/LaunchDaemons/com.apple.jetsamproperties[…]`) | ABSENT | non-issue | launchd falls back to built-in defaults; `kern.jetsam_critical_threshold=1` already in cmdline |
| G13 | cryptex dyld cache dirs | PRESENT and EMPTY (v8 layout kept) | non-issue | 15-dir probe falls through — device-proven ×2 (v60/v61), K5 §2.1; **do NOT recreate** (F5 falsifier) |
| G14 | `/usr/local/lib/libobjc-env.dylib` | stat ENOENT ×3 on v61 | non-issue | proven harmless — **do NOT add** (F5) |
| G15 | libignition | NOT LINKED (absent from closure) | non-issue | no banner line on our build (§1); adding a dylib = new validation surface; leave it |

## 5. Top-5 expected walls AFTER the indirect fix — ranked by boot order

(R1-refreshed: the mwli EINVAL is recovered in-process, so wall #1 sits at dyld's NEXT
validation, not at a bind wall. Signatures are mutually exclusive one-liners wherever possible.)

### W1 — Residual dyld validation or fixup-time death (immediately at the F1 fix point, ~44.3 s)
- **Signatures (three distinct one-liners):**
  (A) `pid 1 exited -- exit reason namespace 6 subcode 0x9, description: indirect symbol has out of range address` — *same as v61* = fix incomplete; another closure image still fails the indirect walk (diag does NOT name the image — stderr dead).
  (B) `pid 1 exited -- no exit reason available -- (signal 11, exit 0)` + panic `initproc failed to start …` — an out-of-range indirect POINTER got *dereferenced* at in-process fixup time (bind-time SIGSEGV — different validator class / bad transplanted-trie content, N1r residual (a)/(b)).
  (C) `description: Library not loaded: <path>` — a closure member missing/regressed (v7-class death; would prove wrong root bytes booted — sha-check the payload first).
- **Cause:** per-image incomplete indirect-symbol-table range repair, or remapped-trie aliasing at bind time. Validators: `MachOAnalyzer.cpp:1788` (the v61 site), `:4813`, `MachOLayout.cpp:1589` (classic-relocs walk, precondition `LC_DYSYMTAB extreloff/nextrel ≠ 0` — R2's predicates).
- **Pre-emptive fix (F1's domain, restated as the A/B stake):** validate EVERY closure image host-side with the R2 3-step recipe (not just launchd); F2 QEMU A/B before the device (both v60/v61 walls already reproduced marker-for-marker in QEMU — same one-liner validates v10).
- **A/B:** A vs B vs C identify fix-incomplete vs fixup-deref vs wrong-image without any further triage. **NONE of them appearing + `hello` appearing = dyld cleared.**

### W2 — launchd early-init abort (between main and the banner)
- **Signature:** panic line reads **`initproc exited`** (NOT "failed to start" — that wording alone proves main was reached, even with zero console lines) — `initproc exited -- exit reason namespace 6 subcode 0x… description: <launchd's own abort string>` or a signal-death line.
- **Cause:** xpc/dispatch/os_log init failure. Lowest-probability wall: the sigtrap QEMU family boots clean to banner on an equally bare kernel.
- **Pre-emptive fix:** none image-side; the description names the abort — decode and return.
- **A/B:** no `hello` + `initproc exited` wording = died in early init; `hello` present moves the wall past T1. (Distinguishable from W1-B by the wording: "exited" vs "failed to start".)

### W3 — config-plist failure (between banner and the boot-task ladder)
- **Signature:** console shows `hello` + `boot-args = …` then death; description candidates: `Root node of launchd.plist is not a dictionary.` (string 553) or cache-loader fallbacks (`Unable to load cache` / `No service cache` — those two are TOLERATED, non-fatal, do not misread).
- **Cause:** wrong bytes served for the plist (mockfs-class theft is dead — HFS owns md0 — but a corrupted/wrong embedded root remains possible), or a VersionNumber mismatch.
- **Pre-emptive fix:** none needed (plist is stock build-matched, verified in-image this rotation); if it fires, re-run `verify-payload.py --expect-root root-v9.hfs` on the payload.
- **A/B:** reaching `Restore environment starting.` = import alive; the T6 ladder lines themselves prove the parse completed (boot-tasks come from the same init flow).

### W4 — a boot-task does NOT skip (ladder → helper-spawn ENOENT)
- **Signature:** a T6 ladder line WITHOUT "Skipping" (prints only the task name), then `pending spawn` / `service state: spawning` for `/usr/libexec/darwinos-boot-task` (or dirs_cleaner/keybagd/init_data_protection), then `posix_spawn(/usr/libexec/darwinos-boot-task) failed: 2` and possibly `failed to reap boot-task (<task>, <state>)`. Worst case: launchd treats a required task as fatal → exit → panic (wording `initproc exited`).
- **Cause:** skip-conditions (`%s not set, Skipping boot-task` — string 2407) evaluate differently on our root (e.g., mount-phase sees `/private/var` as part of root, not a separate mount).
- **Pre-emptive fix:** none (conditions are launchd-internal); the failure wording names the exact task.
- **A/B:** count ladder lines against the QEMU baseline (12 tasks, all skipped); whichever task lacks its "Skipping" line is identified BEFORE any spawn line appears. QEMU precedent says probability is low.

### W5 — the ENOENT storm steady state (expected terminal state — NOT a death)
- **Signature:** hundreds of `posix_spawn(<path>) failed: 2: No such file or directory` (+ kernel-side `systrace: launchd[1] posix_spawn(…) = 2`), interleaved with throttle lines; then a quiet-ish, ALIVE boot with no panic, indefinitely.
- **Cause:** all 530 program paths absent — the by-design limit of the 28 MiB closure.
- **"Fix":** none in-image (v3.9 lane). For diagnosis this state IS success: main + config parse + boot-tasks + spawn machine all proven live.
- **Surprise falsifiers:** (a) launchd *exits* → panic — an accumulated-failure/abort policy; read the description; (b) console silence with no panic — console emission failed (fallback = systrace; if BOTH are silent, launchd hung before the spawn machine — bisect with the last ladder line printed); (c) any job actually executing — impossible on these bytes; it would prove the wrong image booted.

## 6. root-v10-plus delta spec — SEPARATE from F1's candidate (do not touch the indirect-fix candidate)

- **F1's root-v10 stays single-delta:** the indirect-symbol fix ONLY. Nothing below belongs in it.
- **OPTIONAL-1 (the only image addition this lane recommends for a successor root):**
  `mkdir /private/var/cores` — empty dir, root:wheel, 0755, ~4 KiB budget, zero data.
  Kills exactly one console noise line (`coredump … failed: error 2`). Cosmetic.
- **Explicitly REJECTED for any 28 MiB root:** xpcproxy (G10 — new exec surface, moves failure
  one hop), any dylib addition (G15 lesson), any plist edit, cache-dir recreation (G13),
  job binaries (G11 — v3.9 lane).
- The real successor path is the **v3.9 UFS full-root transport** — root-v10-plus should stay
  diagnosis-grade ≤28 MiB.

## 7. Operational notes for the next boot (coordinator + reader)

1. **EP0 logger from before boot** — launchd console lines are EP0-only (`live.log`), never in
   the ramoops ring (§2.3). A parked-phone ramdump alone will show systrace but NOT the launchd
   ladder; do not misread that as launchd silence.
2. **Reader ladder stays `last=DYLD` even on SUCCESS** — no M11 rung exists past DYLD. Success
   reads as: `hello` + Bootstrapper banner + boot-args echo in live.log; systrace storm in
   pstore. (Ask for P5, not this lane: consider a DYLD-MAIN rung keyed on "Darwin Bootstrapper"
   in live-log text.)
3. Boot-args need no changes: `launchd_unsecure_cache=1` and `ignition_level=1` are inert on
   this build (harmless, §1); `systrace=1` is LIVE (r4r7) and is the kernel-side storm trace —
   keep it. The v62 display flags (`io.display.probe/splash`) remain F3's shared-cmdline edit —
   the two-variable call (display series vs root-v10) is the coordinator's.
4. Death-wording cheat-sheet for triage: pre-main death = `initproc failed to start`; post-main
   death = `initproc exited`; signal-death = `no exit reason available -- (signal N, exit 0)`;
   and `minisandbox: request N (first seen from launchd[1])` = syscalls happening (good sign).

---

## 8. §LADDER-DECODE — first 10 post-main signatures + pre-main marker decode (P4, 2026-10-28)

**Context refresh:** §3's T-model was written against root-v9. The current dyld-bootstrap
candidates are **root-v16/v17** (D3/D4 rebuilt dyld-1066.8, shas `5d62fa4873217fd3…` /
`5f82a9016fc76f57…`), which add a PRE-main instrument this map predates: the **boottrace
markers**. This § is the at-a-glance decode for the boot that actually ships: first the
pre-main marker read (root-v17 embeds the **D4 dyld** `7e092f33…`), then the first 10
post-main console signatures — each PASS or a named wall — so the first post-dyld boot is
scored in one pass. Sigtrap precedent for every post-main line: sigtrap-04 serial.log:971–1081
(same launchd family, logd never up).

### 8.1 Pre-main decode (before `hello`): the boottrace markers, read from systrace

PID 1 has no fds, so every marker write FAILS EBADF through the D2 stub and `systrace=1`
prints it on return: `systrace: launchd[1] write(0x…, <len>) = 9 (EBADF)`. **Decode by the
`<len>` field** — no dyld base needed:

| Marker len | Meaning | Terminal state if it's the LAST one |
|---|---|---|
| `0xa`–`0x16` | D3 boottrace M00–M0k (start→allocator→config→apis→snapshot→prepare→mainloader→deps→fixups→initializers→main-handoff + MP per-image map events; expect MP count ≈ 37 = launchd + 36 dylibs) | BOOTSTRAP-LOCKS §6: W-L1 (silence after M0k, k≤6 = pre-prepare hang, named by last marker) · W-L2 (MP<37 or silence after M08/M09 = mapping/binding) · W-L3 (after M10 = libdyld4/NXArgcPtr wire-up on preferred-load windows; fault VA ≥ 0x300000000 = preferred-load worked) · W-L4 (after M11 = initializers on unconstructed runtime) · **M12 printed = launchd main imminent — the handoff to §8.2** |
| `0x17` / `0x18` / `0x19` | D4 allocator markers A1/A2/A3 (A1 count=allocation index; A2 repeat=iterations; A3=no-fit→vm_allocate) | ALLOCATOR-HANG §4 W-A1..A6: repeating A2+A3 = spin (IMPOSSIBLE under the 16-retry guard → its appearance = wrong image); single A2 = BTree-walk hang; A2+A3 then silence = magazine walk; A1 k=1 + silence = ProcessConfig ctor |
| none at all | zero M/A markers | **W-D — the D3/D4 dyld is not resident: WRONG IMAGE booted** → sha-check the payload FIRST (root-v17 = `5f82a901…`) |
| — | `pid 1 exited` line instead of markers | §5 W1 one-liners: (A) same `indirect symbol…` / (B) signal-11 fixup deref / (C) `Library not loaded: <path>` = wrong-image — and (v61 class) `map_with_linking_np(…) = 22` is NON-FATAL (R1) — recovery continues in-process |

### 8.2 The first 10 post-main console signatures (EP0 live.log ONLY — never the ramoops ring)

| # | Console signature (exact) | Verdict |
|---|---|---|
| 1 | `com.apple.xpc.launchd\|1970-01-01 00:00:NN.NNNNNN <Notice>: hello` | **PASS — THE M11 MILESTONE** (launchd main entered; `kern.initproc_spawned`=1). Absence → §8.1 decode, or W2 if a panic says `initproc exited` |
| 2 | `Darwin Bootstrapper Version 7.0.0: Thu Feb  1 20:33:09 PST 2024; root:libxpc_executables-2462.140.4.702.1~1/launchd/RELEASE_ARM64` | **PASS** — banner + build-identity pin |
| 3 | `boot-args = debug=0x8 serial=3 cpus=1 io.display.iomfb=1 io.display.probe=1 io.display.splash=1 rd=md0 … systrace=1` | **PASS + FREE image audit** — the echo must contain BOTH `io.display.probe=1` AND `io.display.splash=1` on the v70-display profile; any drift = wrong cmdline packaged |
| 4 | `Restore environment starting.` | **PASS** (T4) |
| 5 | `(system) <Notice>: entering ondemand mode` + `(system) <Notice>: created` | **PASS** — system domain up (T5) |
| 6 | `(fsck) … <Notice>: Skipping boot-task` (+ `[System Event] … [Subsystem: launchd]` mirrors) | **PASS** — boot-task ladder started (T6); first of 12 |
| 7 | the remaining 11 ladder lines, each `Skipping boot-task`: `mount-phase-1`, `data-protection`, `finish-obliteration`, `detect-installed-roots`, `mount-phase-2`, `MSUEarlyBootTask`, `fips`, `keybag`, `usermanagerd`, `dirs_cleaner`, `xpcroleaccountd` | **PASS** when all 12 skip (QEMU baseline). A ladder line WITHOUT "Skipping" = **W4** — the task's own name identifies the wall before any spawn line appears |
| 8 | `service will attempt to elide xpcproxy` (root class, 132 jobs) / `service will always use xpcproxy because …` (non-root, 398) | **PASS** — spawn machine alive; plist import completed (T7→T8) |
| 9 | `service state: spawn scheduled` → `service state: spawning` → `posix_spawn(<path>) failed: 2: No such file or directory` (console) + `systrace: launchd[1] posix_spawn(…) = 2   (ENOENT)` (kernel ring) | **PASS-EXPECTED — the ENOENT storm (W5 terminal state), NOT a failure.** First eager job = `com.apple.factory.NFQRCoded` → `/Developer/usr/local/libexec/NFQRCoded`. Any job NOT failing ENOENT = wrong image (its binary cannot exist on the 28 MiB closure) |
| 10 | `Service only ran for <n> seconds. Pushing respawn out by <m> seconds.` / `service exceeded successive crash limit. launch will be throttled` / `cannot spawn: service is throttled` | **PASS — throttle equilibrium (T9)**: launchd ALIVE indefinitely, PID 1 never exits, NO panic. Reader ladder stays `last=DYLD` — **the console is the evidence; do not misread a successful boot as a stalled one** |

### 8.3 Wall interleave (mutually-exclusive one-liners; details in §5)

| Console | Verdict |
|---|---|
| `initproc exited -- …` panic wording (with or without `hello`) | **W2** — launchd REACHED main then died (the free wording discriminator; description names the abort) |
| `initproc failed to start -- …` | death BEFORE main — §8.1 marker decode names the stage |
| `hello` + `boot-args = …` then death; `Root node of launchd.plist is not a dictionary.` | **W3** — config import fatal. TOLERATED (do not misread): `No MRM cache found` / `No service cache` / `Unable to load cache` |
| `posix_spawn(/usr/libexec/darwinos-boot-task) failed: 2` (+ `failed to reap boot-task …`) | **W4 confirmed** — the non-skipping task from the ladder line attempted a real spawn |
| launchd EXITS after the storm starts (any panic) | **W5 falsifier (a)** — accumulated-failure abort policy; read the description |
| console silent, no panic, systrace storm continues | **W5 falsifier (b)** — console emission failed; systrace is the fallback channel |
| console AND systrace both silent after the last ladder line | hang before the spawn machine — bisect on the last ladder line printed |

### 8.4 Combined-boot read order (v70-display profile)

One boot now carries BOTH instruments, read top-down: **DISPLAY-READY §5** stages
(G1→P1–P3→S1/S2→C1→I1, kernel-side) → reader rungs → `hfs: mounted iOSRoot` →
**§8.1 markers** (kernel systrace ring) → **§8.2 ladder** (EP0 stream). The two channels
stay separate to the end: launchd lines are EP0-ONLY (§2.3) — a parked-phone ramdump of a
fully successful boot shows kernel systrace + markers but NOT the ladder; the EP0 logger
must run from before the boot.

## Summary update (P4, 2026-10-28, 5 lines)

1. **§LADDER-DECODE added (§8):** pre-main = boottrace markers read from systrace by the
   write(2) length field (M0k = 0xa..0x16 → BOOTSTRAP-LOCKS §6; A1/A2/A3 = 0x17/0x18/0x19 →
   ALLOCATOR-HANG §4; ZERO markers = wrong image, sha-check first); post-main = the first 10
   signatures (hello → banner → boot-args echo → Restore → ondemand → 12× Skipping →
   xpcproxy classes → ENOENT storm → throttle equilibrium), all PASS or PASS-EXPECTED —
   the storm is the DESIGNED terminal state on the 28 MiB closure.
2. **The two PASS-critical decoders:** a ladder line without "Skipping" names its own task
   (W4) before any spawn line; panic wording is the free dyld/launchd boundary discriminator
   (`initproc failed to start` = pre-main, `initproc exited` = post-main).
3. **Falsifiers stay named:** job NOT failing ENOENT = wrong image; launchd exit = W5(a);
   silence-with-systrace = console emission failed (EP0 logger); silence-everywhere = hang
   before the spawn machine.
4. **Combined-boot read order for v70-display:** DISPLAY-READY §5 stages first, then §8.1
   markers, then §8.2 ladder — launchd lines are EP0-ONLY, never the ramoops ring.
5. **Paths:** this §8 in `LAUNCHD-NEXT.md`; display side in `DISPLAY-READY.md` §5; image
   `research/laguna-usb-console/out/next-boot-v70-display/` (r4r8-disp + root-v17, NOT FIRED).

---

## Summary (5 lines)

1. **First post-bind signature:** `com.apple.xpc.launchd|<epoch> <Notice>: hello` →
   `Darwin Bootstrapper Version 7.0.0 … libxpc_executables-2462.140.4.702.1` → a free
   `boot-args = …` echo → 12-task boot-task ladder (all "Skipping") → the `posix_spawn(…) failed: 2`
   ENOENT storm over 108 eager jobs (398/530 jobs are xpcproxy-class), throttled into a stable
   ALIVE state; launchd never exits, so no panic — the reader ladder stays `last=DYLD` and the
   console is the evidence.
2. **Top blocking gap: NONE in the image.** Root-v9 needs zero deltas for launchd's first
   minutes; the only candidate successor-root addition is cosmetic `/private/var/cores`
   (root-v10-plus spec, §6 — F1's candidate untouched); everything else real is the v3.9
   full-root lane, and the writable-root gap (`rootrw=1` has no reader) is a kernel-lane lever.
3. **Walls #2–#3 after the indirect fix:** W1 = residual dyld validation/fixup death with three
   mutually-exclusive one-line signatures (same-indirect-message vs signal-11 fixup-deref vs
   `Library not loaded` wrong-image); W2 = launchd early-init abort, distinguished from W1-B by
   the FREE panic wording `initproc exited` (launchd writes `kern.initproc_spawned`; kernel
   words the panic by it, `kern_exit.c:1809`) vs `initproc failed to start`.
4. **Log visibility verdict:** dyld's `write(2)=9` was EBADF — empty fd table, bytes dropped,
   nothing to retrieve; launchd's own logs ARE console-visible (kernel mounts devfs pre-exec,
   `bsd_init.c:1005`, console node `devfs_vfsops.c:116`; launchd opens `/dev/console`,
   os_log_simple → `%s|%s` format — same family proven on sigtrap QEMU serial) but ONLY on the
   EP0 stream, never the ramoops ring — EP0 logger must run from before boot.
5. **Paths:** map → `research/laguna-usb-fix/LAUNCHD-NEXT.md`; compact →
   `/tmp/fleet/k3-launchdnext-report.md`; bus line → `/tmp/fleet/COORDINATION.md`; image
   inspected through F1's read-only mount `/private/tmp/f1work/v9-extract` (no mount created or
   detached by this lane; sha `a427241dfa71e5088…` re-verified byte-identical after inspection).
