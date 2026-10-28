# BOOTSTRAP-LOCKS.md — the silent userland spin (v67b) → bootstrap-correct locks → root-v16

Lane: **D3** (2026-10-28). Predecessors: F1d (`NULL-DEREF.md`, errno/lock +
root-v14) → D2 (`SYSCALL-STUBS.md`, LR-safe stubs + root-v15) → **this lane**.

Candidates (registered; not fired — coordinator owns device/QEMU):
- `/tmp/fleet/d3/dyld.blazer-d3-1066.8` — 882,336 B,
  sha256 `9057e30b4246de4b8e5773e632f7d6cd73c542653424578c308932232de34c77`
- `/tmp/fleet/d3/root-v16-candidate.hfs` — 29,360,128 B,
  sha256 `5d62fa4873217fd33a35e5aff3acebd006a3c25c371f4b65609ff54fdc1a9c01`
  (= pinned root-v15 + the D3 dyld, exactly ONE file differs: `usr/lib/dyld`)

Pins re-hashed unchanged before AND after: root-v15
`89d871b94f0072ab1834990bea12b366986afa8bd3f3aeb063783d696d0dba46`.
Carried forward byte-identical (verified by archive-member cmp): D2's
`syscalls_impl.o` (68/68 stub audit), F1d's `errno.o`.

---

## 1. Mission item 1 verdict: the spin hypothesis is REFUTED (evidence, not vibes)

The prime suspect was F1d's `-DF1D_BOOT_NO_TLS` lock rebuild (constant owner 1)
as a syscall-free infinite spin. Verification says **it cannot be the v67b wall**:

**(a) The exact spin loop does not exist in the binary.** F1d's compiled
libplatform `lock.c` (`/tmp/fleet/f1d/src/lock_f1d.c`, patch
`patches-dyld-errno/0001`) has only three outcomes past the fast-path CAS
(`os_unfair_lock_lock` @D2-dyld 0x55780: `mov w8,#1; ldaxr/cbnz/stxr` — settles
in one pass single-threaded):

| path | mechanism | console visibility |
|---|---|---|
| owner-match on lock (value=1=self, i.e. recursion) | `_os_unfair_lock_recursive_abort` → `__LIBPLATFORM_CLIENT_CRASH__` → `OS_BUG_CLIENT` → **`brk`** (crashlog_private.h os_fatal_trap, arm64) | **LOUD**: usertrap exception line → pid-1 exit → MACH Reboot |
| value ≥ 2 (a "foreign owner") | clear-nowaiters cmpxchg → `__ulock_wait2(..., duration=0)` = **blocks forever in kernel** | silent (never returns → systrace never fires) — but the value can only ever be 0 or 1 with all stores in this build writing `self=1`/`OS_LOCK_NO_OWNER`; a foreign-owner value is impossible without corruption |
| unlock without ownership | `_os_unfair_lock_unowned_abort` → `brk` | LOUD |

**(b) The only unfair-lock acquisitions on the whole pre-libSystem boot path
cannot nest or deadlock.** Full-binary `bl` census: `_os_unfair_lock_lock` /
`_os_unfair_lock_unlock` are called from exactly two sites:
`RuntimeState::vlog+0x28` (`_locks.logSerializer`) and
`RuntimeState::withCurrentProcessSnapshot+0x3c/+0x180`
(`_processSnapshotLock`). Both are single-acquire/single-release with no call
between them that could re-enter (`vlog`'s consumers are all gated by
`config.log.*` — DYLD_PRINT is unset on this boot; `withCurrentProcessSnapshot`
early-outs unless compact info is enabled and its work is file-record lookups
that succeed invisibly). The `lsl::Lock` wrapper (`Allocator.cpp Lock::lock`)
is a **no-op on the boot**: the bootstrap MemoryManager's lock is null
(`MemoryManager(const char**)` ctor never adopts one), and the
`libSystemHelpers`-based lock paths (DyldRuntimeState.cpp:2574+) are only
reachable after libSystem initializers — which never run pre-handoff. The
`OS_UNFAIR_LOCK_INLINE` header fast path is opt-in and **not compiled in**
(zero inline CAS sites; all callers use extern calls).

**(c) Mach-O fact that kills the dependency-recursion and fixup-walk classes
too**: the transplanted v12 cache-slice dylibs carry **zero `LC_LOAD_DYLIB`**
and **no chained-fixups / LC_DYLD_INFO** (only `LC_DYLD_EXPORTS_TRIE`; checked
on the f1c root-v12 mount, `/private/tmp/fleet/d1/scratch/v12-ro`). So
`loadDependents` terminates at depth 1 (libSystem pulls nothing) and
`applyFixups` has no fixup chain to walk for the dylibs — the classic
malformed-chain infinite-EL0-walk cannot fire on them.

**(d) D1's `mach_absolute_time` port** (the other silent-spin candidate — its
"wake-retry" loop) is bounded: every commpage `mode` branch returns
(NONE → kernel ABSTIME trap; any other mode reads cntvct and returns when
off1==off2, and the kernel does not update the offset in a tight loop).

**Conclusion**: v67b's silence is real but **its mechanism is not the locks**.
What the console actually proves is narrower than the mission brief assumed:

## 2. Frontier correction: what v67b's silence does and does not mean

The kernel's systrace bring-up aid (`bsd/dev/arm/systemcalls.c:51`,
`systrace_report`) is wired into the **BSD syscall RETURN path and prints only
FAILED calls** (`systrace_level < 2 && error == 0 → return`; mach traps are
never traced). Therefore v67b (1 systrace line: `shared_region_check_np = 12`,
then 900 s of kernel memory-stat spam, no usertrap, no pid-1 exit, no reboot)
is consistent with ALL of:
1. an EL0 in-memory loop (no syscalls at all),
2. an infinite loop of SUCCESSFUL syscalls (invisible at level 1),
3. a thread blocked forever inside one syscall that never returns
   (ulock_wait2 / mach_msg class — nothing prints),
4. exactly the mission's framing ("spinning without syscalls") — but so are 2
   and 3, which the framing excludes.

v67b cannot discriminate these. A run that hangs the same way again under
root-v16 **will** — via the boottrace instrument (§4): the last marker pins the
hang to the exact function between milestones. (Kernel-side ask for the durable
instrument: §7.)

## 3. The fix (mission item 2): bootstrap-correct lock semantics

`lock_d3.c` (replaces archive member `os_lock.o` wholesale; full 62-symbol
export set preserved — 62/62 old exports covered, verified by nm set-diff;
+8 harmless internal-level extras). Boot dyld = single thread on cpus=1 until
handoff, so locks serialize nothing; the CORRECT semantics are barrier-only:

| family | semantics |
|---|---|
| `os_unfair_lock_lock/_with_options/_with_flags/_no_tsd`, `_os_unfair_lock_*` | `bl _d3_lock_barrier` = `dmb ishld` (acquire fence) + ret. No CAS, no owner word, no slow path, no ulock, no BRK |
| `os_unfair_lock_unlock` family | `dmb ishst` (release fence) + ret |
| `os_unfair_lock_assert_owner/not_owner` | no-ops (ICF-folded together @0x55e8c) — kills F1d's constant-owner BRK-on-stale-word latent wall |
| `os_unfair_lock_trylock` family | barrier + return true |
| `os_unfair_recursive_lock_*` | honest recursion counting (`ourl_count++/--`, no word CAS) — recursive consumers (objc-style) stay CORRECT, not crashed |
| OSSpinLock/`spin_lock` family, `os_lock` generic dispatch, handoff/nospin/spin/unfair members | barrier-only; trylock = 1 |
| `os_once`/`os_once_reset` | honest single-threaded once-gate (0 = not run; set+call), no counter epochs, no locks |
| `os_sync_wait_on_address` family | **EOPNOTSUPP — refuses to block** (a wait can never be woken on a single-threaded boot; blocking here is exactly the silent-hang class). Wake = 0 |
| `*_abort` catchers | `__builtin_trap()` (nobody may reach them) |

Why still ship it when F1d's build "cannot spin": the constant-owner scheme has
two REAL latent walls this kills — (i) any future nested acquisition BRKs
(recursive abort) instead of no-oping, (ii) `assert_not_owner` fires on any
held lock — plus the `__ulock_wait2` blocked-forever class on any future
corrupted lock word. On a single-threaded loader these semantics are also
simply the TRUE ones.

**Boottrace instrument (the decode tool for the next silent hang)** — patch
`patches-d3/0004-d3-boottrace-milestones.patch` (layered on 0001+0002+0003;
15 insertions, 2 files; `git apply --check` PASS on a fresh patched tree) +
`d3_boottrace.c/h` (support member). `d3_mark(tag)` = raw `write(2, tag, len)`;
pid 1 has no fds (kernel: `files 0`), every write FAILS EBADF through D2's
LR-safe stub, and systrace=1 prints every failed BSD syscall on return →
**each milestone becomes a console line**: `systrace: launchd[1] write(0x2,
0x<buf>, 0x<len>) = 9`. Marker identity = the unique LENGTH (10..22 hex) — no
dyld-base knowledge needed (buf addr varies per boot; len doesn't). Zero
behavioral risk: the write path is the exact D2-proven EBADF return; probes sit
at function entries only. Precedent: D2 shipped 49 LR fixes + munmap renumber
in one boot; this ships locks + instrument in one boot because locks-alone
cannot teach anything new about v67b (they were proven innocent).

Also folded in during this rebuild: **0005** = D1's uncommitted
`include/mach-o/{dyld.h,dyld_priv.h,dyld_introspection.h}` fixes (bridgeos
availability removal — the documented DYLD-BUILD.md §5 SDK wall; extracted
from D1's working tree, attributed, `patches-d3/0005`). Without it the fresh
ce1cc20 clone does not compile against iPhoneOS 27.1.

## 4. Validation (all host-side; evidence `/tmp/fleet/d3/verify-d3-linked.json`)

| gate | result |
|---|---|
| header parity | MH_MAGIC_64 ARM64 **DYLINKER** NOUNDEFS DYLDLINK, LC_BUILD_VERSION platform 2 minos 16.5 — D1/F1d/D2 classes ✓ |
| size | 882,336 B (D2 882,768; −432: lock.o shrinks, markers added) |
| stub sweep (re-verified LIVE on this binary, not transitive) | **68/68**: numbers vs kernel tables, `svc #0x80`, x0–x8 untouched, 48/48 BSD `stp/bl _cerror/ldp` (spot-checks: `_close` `_munmap` 0x49 `_shared_region_check_np` 0x126) |
| syscalls_impl.o / errno.o archive members | **byte-identical** to D2's / F1d's validated members (cmp PASS) |
| lock surface (linked) | `_os_unfair_lock_lock` = `bl _d3_lock_barrier`(`dmb ishld`)+ret; unlock = ishst; asserts = folded no-ops; **0 ulock calls, 0 slow-path refs, 0 BRK reachable from any lock function, 0 TPIDRRO** |
| whole-binary `___ulock_wait2`/`___ulock_wake` refs | **0 / 0** (the ulock blocking class is gone from the image) |
| TPIDRRO sweep | exactly the 3 documented dormant sites (`_mig_get_reply_port+0x10`, `_mig_dealloc_reply_port+0x10`, `_mach_msg_overwrite+0x60`) |
| boottrace markers | 13/13 strings present; 13 `bl _d3_mark` call sites; whole-binary brk count = 1 (SDK mig stub, not lock code) |
| F1c mapSegments patch | FIXED-first `mov w3,#0x0` @0x12c68 → `bl _vm_allocate`; gated fallback log string present; stock ANYWHERE `mov w3,#0x1` @0x12ca4 ✓ |
| codesign | adhoc, `codesign -v` PASS |
| **root-v16** | fsck_hfs -fn "appears to be OK"; 198 identical + 1 differing (`usr/lib/dyld`, mode 0755, byte-identical to registered dyld) |
| F1c `build_v12.py verify` on v16 | PASS: tries 10,901/36, predicate 36/36, GOT 3,944/3,944, census 536(+1 weak)=537, pointers 16,606/16,606 in range, symtab 21,048, disjointness 0/0, `__dyld4` pair 0x3003870e8/0x3003858d0 |
| F1d NULL gate on v16 | PASS: 704 census slots → 0 NULL, 504 re-targeted in-window, 71 documented data residual, 0 old-cache-range residuals, `__dyld4` pair exact |
| pins | root-v15 sha unchanged before AND after; v13/v14 untouched (not opened for write) |

## 5. root-v16 pairing rule + builder pin ask

v16 ships the D3 dyld — it IS the image's `/usr/lib/dyld` (D1's pairing rule).
Ask: `WITH_ROOT16` pin from sha `5d62fa4873217fd33a35e5aff3acebd006a3c25c371f
4b65609ff54fdc1a9c01` **only together with dyld.blazer-d3-1066.8**
(`9057e30b4246de4b…`). Firing = coordinator only (device or F2 QEMU first —
recommended: QEMU A/B v15 vs v16 reproduces v67b in ~15 min and the boottrace
decodes the wall without touching the phone).

## 6. Pre-registered signatures for the next run (v67c on root-v16)

Decode order: read `systrace: … write(0x2, 0x…, 0x<len>) = 9` lines' LENGTH
field against the table; MP repeats = mapped-image count.

| marker | len (`hex`) | site |
|---|---|---|
| M01 | 10 (`0xa`) | start() after handleDyldInCache (cacheless path taken) |
| M02 | 11 (`0xb`) | persistent allocator created |
| M03 | 12 (`0xc`) | ProcessConfig constructed |
| M04 | 13 (`0xd`) | APIs/RuntimeState constructed |
| M05 | 14 (`0xe`) | entering withCurrentProcessSnapshot |
| M06 | 15 (`0xf`) | snapshot done |
| M07 | 16 (`0x10`) | prepare() entry |
| M08 | 17 (`0x11`) | launchd JIT loader built |
| M09 | 18 (`0x12`) | loadDependents done (after N MP repeats) |
| M10 | 19 (`0x13`) | applyFixups done |
| M11 | 20 (`0x14`) | initializers about to run |
| M12 | 21 (`0x15`) | handoff to launchd entry imminent |
| MP | 22 (`0x16`) | one image mapped (repeats) |

Expected outcomes, in decodable terms:
1. **W-L1 (pre-prepare silence)** — hang with last marker M0k (k ≤ 6): the wall
   is between milestones; e.g. silence after M05 = `withCurrentProcessSnapshot`
   internal (EphemeralAllocator/serialize), after M03 = ProcessConfig ctor.
   The v67b hang should now land here if the spin is where we think.
2. **W-L2 (mapping/binding)** — MP count < 38 or silence after M08/M09: a
   mapSegments/loader-alloc wall; MP count = 37 (launchd + 36 dylibs) expected.
3. **W-L3 (NXArgcPtr wire-up, F1c's wall-#5 sibling)** — silence after M10:
   the `libdyld4Section` wire-up dereferences the transplanted libdyld's
   `__dyld4` pointers (first live exercise on the v13+ content — F1c's
   preferred-load windows are now ACTUALLY exercised for the first time,
   because F1d's v65c never got this far either). A usertrap at a VA inside
   the v12 dylib windows (0x300000000+) = preferred-load WORKED and the
   pointer content is at fault; a fault at a kernel-chosen VA = the FIXED
   reservation fell back (should not: disjointness 0/0 verified).
4. **W-L4 (initializers / no-initializer gap)** — silence after M11 or a fault
   with pc inside a v12 dylib window: libSystem/dispatch/objc initializers on
   unconstructed runtime (F1c wall-#5) — expect objc/dispatch bootstrap faults.
5. **W-L5 (launchd main)** — M12 printed then silence/fault = launchd main on
   the v13+ runtime; decode against K3's T0–T9 launchd sequence
   (k3-launchdnext-report) and K5's wall table W1–W10.
6. **W-D (stale-image discriminator)** — the SAME single systrace line
   (`shared_region_check_np = 12`) and ZERO M-markers = the embedded dyld is
   NOT D3 (check the root sha `5d62fa4873217fd3…` / `WITH_ROOT16` pairing).
7. **Reassurance**: the marker probes cannot dead-end the boot — worst case a
   marker line is missing because the write stub was never reached; systrace
   line order is monotonic with boot progress.

## 7. Asks

- **Kernel lane (K-successor)**: the durable instrument for ANY future silent
  hang — a pid-1 progress sampler: one timer-driven hunk that, when pid 1 has
  not returned from a syscall for >N s, samples the EL0 PC once per M s and
  printf's `usertrap`-style (`launchd[1] silent pc=0x%llx …`). That converts
  EVERY silent-hang class (spin, blocked-forever, successful-loop) into a
  symbolicated PC — the same lesson as execdiag/systrace (AGENTS.md §3).
  Ship as a patch; coordinator decides whether it rides the next mach build.
- **F2**: QEMU A/B root-v15 vs root-v16 before any device boot (tag copy from
  inputs; v16 embeds the D3 dyld automatically). Success = boot progress past
  the last v67b state (markers marching); a silent repeat decodes via §6.
- **Coordinator**: WITH_ROOT16 pin from sha `5d62fa4873217fd3…` only with the
  D3 dyld in the same image; keep `systrace=1` in boot-args (the markers ride
  on it); keep `cpus=1` for this boot (lock no-op semantics assume the
  single-threaded loader).
- **D1**: your working-tree header fixes are now a durable patch
  (`patches-d3/0005-d1-header-bridgeos-fixes.patch`) — fold into
  `patches-dyld-build/` so the next fresh-clone build stops failing on
  bridgeos; your clone's uncommitted state is a fleet-wide reproducibility gap.

## 8. Files + reproduce

```
/tmp/fleet/d3/dyld.blazer-d3-1066.8        sha 9057e30b4246de4b8e5773e632f7d6cd73c542653424578c308932232de34c77 (882,336 B)
/tmp/fleet/d3/root-v16-candidate.hfs       sha 5d62fa4873217fd33a35e5aff3acebd006a3c25c371f4b65609ff54fdc1a9c01 (29,360,128 B)
/tmp/fleet/d3/lock_d3.c                    (bootstrap-correct lock.c replacement; 62/62 exports + 8 internal extras)
/tmp/fleet/d3/d3_boottrace.c               (write(2) marker emitter)
/tmp/fleet/d3/shims-d3/d3_boottrace.h      (marker table, decode contract in header comment)
/tmp/fleet/d3/patches-d3/0004-d3-boottrace-milestones.patch   (layered: apply after 0001+0002+0003)
/tmp/fleet/d3/patches-d3/0005-d1-header-bridgeos-fixes.patch  (D1's uncommitted include/mach-o fixes, attributed)
/tmp/fleet/d3/dyld-src/                    (clean ce1cc20 + 0001..0005, committed stepwise)
/tmp/fleet/d3/d3-rebuild-dyld.sh           (archive swap + D1 xcodebuild recipe + sign)
/tmp/fleet/d3/d3-build-v16.sh              (v15 pin → v16, all gates)
/tmp/fleet/d3/verify_d3_dyld.py            (linked-binary verification: stubs 68/68 + lock surface + markers + TPIDRRO)
/tmp/fleet/d3/verify-d3-linked.json        (evidence)
/tmp/fleet/d3/os_lock-d2-exports.txt / os_lock-d3-exports.txt  (export set diff inputs)
/tmp/fleet/d3/v12-verify.log               (F1c verify PASS on v16)
research/laguna-usb-fix/patches-d3/        (durable twin of the patch set)
research/laguna-usb-fix/BOOTSTRAP-LOCKS.md (this file)
```

Boundary honored: D1/D2/F1d lane files untouched (the D1 header fixes ship as a
patch EXTRACTED from their tree, not edited in place; the support archive was
copied, members swapped in MY copy). Root-v15/v14/v13 pins re-hashed unchanged.

---

5-line summary:
1. Spin hypothesis REFUTED byte-exactly: F1d's constant-owner lock binary has
   no syscall-free loop (fast CAS settles; recursion → BRK = loud; ulock_wait2
   block needs a foreign-owner value that's impossible cleanly), and the only
   two pre-libSystem unfair-lock sites (vlog logSerializer, snapshot lock)
   cannot nest; v12 dylibs have 0 LC_LOAD_DYLIB + no fixups (no walk to spin
   in); frontier correction: systrace only logs FAILED BSD syscalls on return,
   so v67b's silence is equally consistent with blocked-forever or
   successful-loop spins.
2. Fix shipped anyway (latent-wall defusal + true single-thread semantics):
   os_lock.o replaced by lock_d3.c — barrier-only lock/unlock, no-op asserts,
   recursion-count recursive locks, EOPNOTSUPP (never-block) sync-wait, honest
   once-gate; 62/62 old exports preserved; D2 stubs + F1d errno + F1c
   mapSegments carried byte-identical (68/68 stubs re-verified live; TPIDRRO =
   exactly the 3 dormant sites; 0 ulock refs in the whole image).
3. NEW instrument: boottrace — 13 unique-length write(2) markers riding
   systrace=1's failed-syscall prints (decode = length field 0xa..0x16, no
   dyld base needed), covering start()→allocator→config→apis→snapshot→
   prepare→loader→deps→fixups→initializers→main-handoff + per-image map
   events; any future silent hang now pins to the exact function.
4. root-v16 = pinned root-v15 + dyld.blazer-d3-1066.8 (9057e30b4246de4b…,
   882,336 B, adhoc): fsck OK, exactly-1-file delta, F1c verify PASS
   (36/36 predicate, GOT 3,944, census 537, 16,606 pointers, disjointness
   0/0), NULL gate PASS, pins re-hashed unchanged; candidate sha
   5d62fa4873217fd3….
5. Pre-registered: W-L1..L5 + W-D decode table (§6) — the v67b hang should
   now die as a marker-pinned silence (last M-marker names the function);
   asks: F2 QEMU A/B v15-vs-v16 first, coordinator WITH_ROOT16 pin, kernel
   lane pid-1 PC sampler patch as the durable instrument, D1 fold header
   patch 0005.
