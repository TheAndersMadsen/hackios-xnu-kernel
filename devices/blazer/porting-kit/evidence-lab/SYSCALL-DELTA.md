# SYSCALL-DELTA — the complete launchd[1] systrace path/errno delta for root-v10

Lane: **F5 (systrace path-delta)** · 2026-10-27 · **host-only** (no device actions; root-v9.hfs inspected read-only at `/private/tmp/fleet/f5-rootv9-mnt`, detached after, no writes).
Mission: so that F1's root-v10 satisfies everything launchd[1] touched BEFORE the fatal `map_with_linking_np` wall, this doc lists **every** systrace line from v60+v61, decodes each errno, and verdicts it: (a) must-exist-in-root, (b) kernel/payload-side response, (c) benign/skippable.

Reads: `attempts/20261027-161053-v60/reader-full.txt` + `attempts/20261027-173349-v61/reader-full.txt` (raw systrace), `POST-LAUNCHD-MAP.md` (K5: cache-probe fall-through semantics, invisible-stderr fact), kernel source `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (file:line below), dyld-1042 source `research/laguna-minicache/ref/dyld-1042.1` (cache probe), and **strings/objdump of the actual dyld-1066.8 binary from root-v9** (`/usr/lib/dyld`, 922,912 B).

---

## 0. Verdict (30 seconds)

- **BLOCKING ITEMS: 0.** Every failing syscall launchd[1] made before the wall is either (i) absorbed by design — dyld falls through cache probes, env-dylib probes, and the map_with_linking_np EINVAL itself (the dyld binary literally contains the fallback string `"__map_with_linking_np(%s) failed, falling back to linking in-process"`), or (ii) a correct kernel response to a probe (ENOENT/EFAULT/ENOTTY). Nothing on root-v9's filesystem stopped PID 1 from reaching the wall; the wall is the closure/dylib **indirect symbol table data** (F1's lane), not a missing path.
- **The one path item worth adding is `/private/var/cores` (0755, root:wheel, empty)** — it kills the coredump ENOENT line — but honest caveat: it is POST-wall (fires at the death handler, cannot prevent anything) and the core file would land on RAM-disk md0, which current evidence channels (ramoops/pmsg at park) cannot retrieve. Verdict: **optional, non-blocking, zero pre-wall effect** — safe to include, wrong to expect from it.
- **Sysctl gaps are payload/ADT, not kernel code and not rootfs**: `sysctl mib 6 138 = 22` is **`hw.osenvironment`** — registered, handler runs, and the handler ITSELF returns EINVAL because our payload ADT `/chosen` has no `osenvironment` property (`kern_mib.c:733-750` + `mkadt.py` chosen node). `sysctl mib 0 3 = 2` is a name2oid lookup for a name our tree doesn't register (name lives in a user buffer, invisible in the trace; all dyld-binary names checked are registered). Both demonstrably non-blocking (boot continued past both).
- **F1 must change ONLY the indirect-symbol data in root-v10.** Do not recreate `/System/Library/Caches/com.apple.dyld/`, do not add `libobjc-env.dylib`, do not populate `/dev` — all proven absorbed; extra deltas destroy the A/B discrimination.

## 1. The raw trace: extraction + dedupe (console-ring noise documented)

- **v60** (`attempts/20261027-161053-v60/reader-full.txt`): 42 raw `systrace:` lines = **14 unique failing calls, each repeated 3×** by console-ring re-reads in the reader dump (identical byte-for-byte blocks at lines ~247–262, ~972–987, ~1213–1228, timestamps identical to the µs). Dedupe rule applied: collapse on (syscall + args + decoded path + retval), keep the block with the tightest neighborhood context (the pre-wall window at t=908.438–908.503).
- **v61** (`attempts/20261027-173349-v61/reader-full.txt`): 15 raw lines = **15 unique** (single ring pass, lines ~245–260, t=44.190–44.291). Same 14 pre-wall calls as v60 (addresses differ, calls identical) + the fatal `map_with_linking_np(0x16ed98780, 0x1, 0x16ed987b0, 0x1118) = 22`.
- Context lines around the blocks (both boots): `minisandbox: request 2 (first seen from launchd[1]): allowed` (K5 W7, GOOD sign); v60 death `subcode 0x4 Symbol not found: _NDR_record`; v61 death `subcode 0x9 indirect symbol has out of range address`; both followed by the coredump attempt at `/private/var/cores`.

## 2. The deduped pre-wall call table (identical in v60 and v61 except where noted)

| # | Call (deduped) | Ret | Decode | Mechanism (source) | Verdict |
|---|---|---|---|---|---|
| 1 | `shared_region_check_np(0x…7bd0, 0x8000, 0x1, 0x0)` | 12 ENOMEM | no shared region mapped for this task | cache-less world; `vm_shared_region.c` prints `check_np … vm_shared_region_start_address() failed` right before each | **SKIPPABLE — by design.** K5 §2: "with no cache, launchd's shared_region_check_np returns un-mapped silently — correct". Real fix = the 2.536 GiB cache via v3.9 UFS transport (separate lane), not root-v10 |
| 2 | `sysctl mib 0 3 = 2` | 2 ENOENT | `name2oid` lookup (`_sysctl.3`, `kern_newsysctl.c:1230`) failed — some `sysctlbyname("X")` for a name not in our OID tree | the requested name is in the old-buffer, not printable at this trace level | **SKIPPABLE — boot continued.** Every name string inside the actual dyld-1066.8 binary was checked: `kern.bootargs` ✓registered (`kern_sysctl.c:2174`), `kern.dyld_flags` ✓ (`kern_sysctl.c:2143`), `hw.osenvironment` ✓, `hw.target` ✓ (`kern_mib.c:999`), `vm.footprint_suspend` ✓ (`vm_unix.c:3766+`). So the failing name is either from launchd's own early code or a name dyld tolerates. Cheap instrument for the next systrace rev (K1-style): print the requested string on name2oid ENOENT |
| 3 | `sysctl mib 6 138 = 22` | 22 EINVAL | **`hw.osenvironment`** — the OID exists and its handler ran, then returned EINVAL itself because `osenvironment_size == 0` (`kern_mib.c:733-750`); the DT loader only fills it from `/chosen` property `osenvironment` (`sysctl_load_devicetree_entries`, `kern_mib.c:694-730`) — **our ADT's `/chosen` (mkadt.py) has no such property** | dyld-1066.8 binary carries the name `hw.osenvironment`; auto-numbering (`kern_newsysctl.c:171-199`, max+1) lands it at 138 in this tree | **SKIPPABLE for the wall (boot continued past it).** OPTIONAL payload/ADT delta: add `/chosen osenvironment` (string, e.g. `"ios"`) to flip this to 0 and give dyld correct OS-variant data. That is a mkpayload/mkadt patch (different artifact, different owner) — **NOT a root-v10 item** |
| 4 | `fsctl("/", _IOR('J',106,8))` | 25 ENOTTY | APFS-era FSIOC (group 'J' = `fsctl.h` family; nr 106 not defined anywhere in our tree — our `hfs_fsctl.h` carries only 'J' 17/18) → filesystem ioctl default → ENOTTY | correct rejection of an unsupported fsctl | **SKIPPABLE — boot continued.** No action |
| 5 | `open("/System/Library/Caches/com.apple.dyld/")` | 2 ENOENT | dyld's cache-dir probe; the dir (and its parent) is ABSENT on root-v9 (legacy dirs removed in R2; only `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld` exists, EMPTY) | `forEachInstalledCacheWithSystemPath` (dyld-1042 `common/ProcessAtlas.cpp:868+`): realpath→skip if missing, opendir→no DT_REG entries if empty; K5 §2.1 source-verified on 1042 + string-anchored in 1066.8 | **NOT A WALL — device-proven twice.** Empty-present AND absent both fall through (v60+v61 continued past). **Do NOT recreate the legacy dir in root-v10** |
| 6 | `shared_region_check_np(0x…7160, 0,0,0)` | 12 ENOMEM | same as #1, second call site | same | **SKIPPABLE** |
| 7 | `fstatat64(0xffffffff, "dyld_shared_cache_arm64.development")` | 9 EBADF | fd arg = -1 while `AT_FDCWD = -2` (`fcntl.h:200`) → `fstatat_internal` (`vfs_syscalls.c:7203-7214`) takes the invalid-fd branch → EBADF instead of ENOENT. Semantics for dyld identical: "no development cache" | dyld probes the dev cache first, then the plain cache | **SKIPPABLE.** Kernel nit (errno 9 vs 2) — do NOT chase; dyld treats any failure as absent. Flagged only so nobody "fixes" it inside root-v10 |
| 8 | `openat(0xffffffff, "dyld_shared_cache_arm64")` | 9 EBADF | same fd pattern as #7; plain-cache probe | same | **SKIPPABLE** |
| 9 | `getattrlist("/dev")` | 22 EINVAL | `/dev` EXISTS on root-v9 as a plain empty HFS dir; ATTR_VOL_* on a directory that is not a mounted volume → EINVAL (devfs not mounted at dyld time) | launchd/dyld probing whether `/dev` is a mounted fs | **SKIPPABLE — boot continued.** Devfs-on-/dev mount timing belongs to the post-wall launchd-main domain (M3+), not root-v10 |
| 10 | `stat64(0x0, …)` (NULL path) | 14 EFAULT | NULL path pointer; kernel correctly EFAULTs on copyin | caller-side NULL (some unset env/path variable in the probe loop) | **BENIGN.** No delta; identical behavior on real iOS |
| 11 | `stat64("/usr/local/lib/libobjc-env.dylib")` | 2 ENOENT | env-override aux dylib probe; dir exists (empty), file absent | dyld-1066 env-override mechanism | **SKIPPABLE — boot continued.** Real iOS 16.7 HAS `/usr/lib/libobjc-env.dylib`; its absence is proven harmless on device (v60+v61). **Do NOT add it to root-v10** |
| 12 | `stat64("/usr/lib/libobjc-env.dylib")` | 2 ENOENT | same probe, second path | same | **SKIPPABLE** |
| 13 | `open("/usr/local/lib/libobjc-env.dylib")` | 2 ENOENT | same probe, open after the stats | same | **SKIPPABLE** |
| 14 | `write(0x2, 0x…67f8, 0x9, …)` | 9 EBADF | PID 1's fd table is empty — dyld's own error print goes nowhere (K5 §2 fact 1: dyld stderr is INVISIBLE pre-main) | the exit-reason description is the only carrier of dyld's message | **EXPECTED.** Optional kernel-side lever for a FUTURE mach rev (K1/coordinator): wire PID 1 fd 0/1/2 → `/dev/console` in `load_init_program` — would make dyld/launchd stderr visible on the serial console. NOT a root-v10 item |
| — | `map_with_linking_np(0x…8780, 0x1, 0x…87b0, 0x1118)` = 22 | 22 EINVAL | **THE FATAL ONE — v61 only (F1 owns).** Key F5 finding: the dyld-1066.8 binary contains `"__map_with_linking_np(%s) failed, falling back to linking in-process"` — dyld TOLERATES this EINVAL and falls back to in-process linking; the death (`indirect symbol has out of range address`, subcode 0x9, OS_REASON_DYLD = dyld abort path, pre-main) came from the closure/dylib indirect-symbol DATA during that fallback, not from the syscall rejection | kernel side: `vm_unix.c:3022-3302` (sanity checks + `p_disallow_map_with_linking` gate at 3048) | **NOT a rootfs-path delta.** No `/System` path or file change can affect it. F1's root-v10 = indirect-symbol-table repair ONLY |

Post-wall context (both boots, after PID-1 death — cannot block anything, listed for completeness): `coredump (launchd, pid 1): failed to open core dump file /private/var/cores/launchd.core: error 2` → `/private/var/cores` ABSENT on root-v9; and v60's `vm_swap_create_file failed @ 908 secs` ×2 with `Failed to open swap file 30` → errno 30 = EROFS: root landed READ-ONLY (K4's known `rootrw=1` no-op note), NOT a path gap — `/private/var/vm` EXISTS on root-v9.

## 3. root-v9 cross-check (mounted read-only, inspected, detached)

| Item | root-v9 state | Needed? | Verdict |
|---|---|---|---|
| `/System/Library/Caches/com.apple.dyld/` | ABSENT (parent `/System/Library/Caches` absent too) | No — dyld fall-through (§2 #5) | leave absent |
| `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld/` | PRESENT, EMPTY (mtime matches v9 build 2026-10-27) | No | leave empty |
| `/dev` | PRESENT, empty plain dir | No (devfs mounts later) | leave |
| `/private/var/cores` | **ABSENT** | Only for the post-death coredump line | **the one optional add** |
| `/usr/lib/libobjc-env.dylib` | ABSENT | No (§2 #11-13) | leave absent |
| `/usr/local/lib/` | PRESENT, empty | — | fine |
| `/usr/lib/dyld` | PRESENT, 922,912 B (dyld-1066.8) | Yes | present |
| `/private/var/vm` | PRESENT (empty) | Swap wants a WRITABLE fs, not the dir | fine — EROFS is a mount-flag issue (K4) |
| `/var`,`/tmp`,`/etc` symlinks | intact → private/var, private/var/tmp, private/etc | Yes | fine |
| `/usr/lib/system/libdispatch.dylib` (K5's W4 discriminator) | PRESENT (v8 lineage) | Yes for load-class deaths | present |

## 4. The delta spec for root-v10 (what F1's builder should do — and NOT do)

**Filesystem deltas (blocking): NONE.**

**Filesystem delta (optional, non-blocking, diagnostic-only):**
1. `mkdir /private/var/cores` — mode `0755`, owner `0:0` (root:wheel), **empty-ok** (kernel coredump runs as root and opens the file with O_CREAT; only the directory must exist — that is exactly what errno 2 at open time proves). Real iOS 16.7 has this dir; K5's semantics work applies unchanged (present-empty is the correct shape). Effect: v10's console shows a successful (or differently-failing) core write instead of `error 2`. **Honest expectations:** the core lands on RAM md0 and is NOT retrievable via ramoops/pmsg at park; this changes one log line, nothing else. If F1 wants the v10 A/B to stay pure, it can be skipped without risk.

**What F1 must NOT change in root-v10 (single-delta discipline — each was absorbed in v60 AND v61):**
- do NOT recreate `/System/Library/Caches/` or `/System/Library/Caches/com.apple.dyld/` (empty-present vs absent both proven harmless; recreating them muddies the cache-probe signal),
- do NOT add `libobjc-env.dylib` (absence proven harmless; real-iOS parity is a full-root v3.9 concern),
- do NOT populate `/dev` or add device nodes (devfs owns this post-mount),
- do NOT touch `/usr/lib/system/libdispatch.dylib` or the closure dylibs' PATHS — only their indirect symbol table data (that is F1's actual fix per the M-wave diagnosis),
- do NOT add xpcproxy/job binaries (unreachable pre-bind; K5 §8 already rejected).

**Kernel/payload gaps (non-blocking, each with owner + source pointer):**
| Gap | Evidence | Owner / vehicle |
|---|---|---|
| `hw.osenvironment` → EINVAL (mib 6.138): ADT `/chosen` lacks `osenvironment` prop | `kern_mib.c:694-750` (DT load + EINVAL-on-empty), `mkadt.py:216-274` (/chosen node has nvram/MobileGestalt/asmb props, no osenvironment) | payload lane: mkadt/mkpayload patch (NOT rootfs, NOT kernel code); would also give dyld correct OS-variant input |
| `name2oid` ENOENT (mib 0.3): one unknown sysctl name, invisible in trace | `kern_newsysctl.c:1080-1230`; all dyld-binary names checked registered | K1-style instrument for next mach rev: systrace prints the requested name string on name2oid failure |
| `shared_region_check_np` ENOMEM ×2: no shared region (cache-less) | K5 §2 table; `vm_shared_region.c` | v3.9 UFS full-root/cache transport lane — not fixable in-image |
| `fstatat64/openat` EBADF(9) vs ENOENT(2) for cache probes (fd=-1 vs AT_FDCWD=-2) | `fcntl.h:200`, `vfs_syscalls.c:7203-7214` | none — dyld-identical semantics; do not chase |
| PID-1 stderr invisible (write(2)=EBADF) | K5 §2 fact 1 | optional kernel lever, future mach rev: wire fd 0/1/2 → /dev/console at `load_init_program` |
| swap create EROFS(30) | v60 console; K4 note (rootrw=1 lands RO) | K4/kernel mount-flag lane; non-blocking pre-M3 |
| fsctl ENOTTY / getattrlist /dev EINVAL / stat64 NULL EFAULT | §2 #4, #9, #10 | none — correct kernel responses |

## 5. Feeds-F1 summary (one paragraph)

root-v10's ONLY pre-wall obligation is F1's indirect-symbol repair; the trace proves no filesystem path blocked launchd[1] on root-v9 — dyld absorbed every ENOENT/EINVAL/EBADF/ENOTTY/ENOMEM probe and reached `map_with_linking_np` identically in v60 and v61. The fatal sequence on v61 is: probes → `map_with_linking_np = 22` → dyld's OWN documented fallback ("falling back to linking in-process") → death on `indirect symbol has out of range address` while linking in-process → therefore the fix is data (indirect symbol table range/alias semantics), and any rootfs "help" beyond (optionally) `/private/var/cores` would be noise, not signal.

---

## Summary (5 lines)

1. **Blocking items: 0** — every pre-wall failing call on root-v9 is absorbed by design (dyld fall-through, correct errno responses, tolerated map_with_linking_np EINVAL — the binary itself contains the in-process-linking fallback string); the fatal `indirect symbol has out of range address` is closure DATA, not a path.
2. **Top 3 optional deltas**: `/private/var/cores` 0755 root:wheel empty (kills coredump ENOENT line; core unretrievable on RAM md0 today); `/chosen osenvironment` ADT prop (flips sysctl 6.138 EINVAL→0, gives dyld OS-variant — payload lane, not rootfs); PID-1 console-fd wiring (future mach rev, makes dyld stderr visible).
3. **Sysctl gaps**: mib 6.138 = `hw.osenvironment` — node EXISTS and its handler returns EINVAL because ADT `/chosen` lacks the prop (`kern_mib.c:694-750`, `mkadt.py` chosen node); mib 0.3 = unknown-name name2oid (name in user buffer; all dyld-binary names verified registered) — recommend systrace prints the name; both non-blocking, boot continued past both.
4. **F1 must NOT change**: `/System/Library/Caches*` (leave absent/empty), `libobjc-env.dylib` (leave absent), `/dev` (leave empty), closure dylib PATHS (data-only fix) — all proven absorbed in v60+v61; root-v10 = indirect-symbol repair ONLY.
5. **Paths**: spec → `research/laguna-usb-fix/SYSCALL-DELTA.md`; compact → `/tmp/fleet/f5-delta-report.md`; raw evidence → `attempts/20261027-{161053-v60,173349-v61}/reader-full.txt`; root-v9 inspected read-only (mount detached, no writes).
