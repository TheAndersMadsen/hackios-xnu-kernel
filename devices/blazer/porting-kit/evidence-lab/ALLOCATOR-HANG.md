# ALLOCATOR-HANG.md — the M02→M03 silent hang (v68b) → allocator-path instruments + retry guard → root-v17

Lane: **D4** (2026-10-28). Predecessors: D3 (`BOOTSTRAP-LOCKS.md`, locks + boottrace
+ root-v16) → **this lane**. HOST-ONLY throughout; nothing fired.

Candidate (registered; not fired — coordinator owns device/QEMU):
- `/tmp/fleet/d4/root-v17-candidate.hfs` — 29,360,128 B,
  sha256 `5f82a9016fc76f579937aa036df47a7b3df01b6f4581aad8ba206573c54ac5d9`
  (= pinned root-v16 + the D4 dyld, exactly ONE file differs: `usr/lib/dyld`)
- `/tmp/fleet/d4/dyld.blazer-d4-1066.8` — 882,528 B,
  sha256 `7e092f33f86cf102c16abd3665ef1d6d4b5056bed22a49b83253c90fc8225fba`

Pins re-hashed unchanged before AND after: root-v16
`5d62fa4873217fd33a35e5aff3acebd006a3c25c371f4b65609ff54fdc1a9c01`.
Carried byte-identical (archive members of the untouched D3
`libdyld-support.a`): D2 `syscalls_impl.o` (cmp PASS vs D2's validated
object), F1d `errno.o`, D3 `os_lock.o`, D3 `d3_boottrace.o`.

---

## 1. The evidence window (v68b, mach r4r7-hfs + root-v16, D3's boottrace)

`runC-late-v68b-nvram/mirror-ring-strings.txt` (timestamps 0.6xx s are
current-boot; the reader's STALE-EVIDENCE verdict is a freshness-gate artifact —
this boot's console IS in the ring):

```
[0.618589] shared_region: … [1(launchd)] check_np(0x16f07bbd0) vm_shared_region_start_address() failed
[0.619024] systrace: launchd[1] shared_region_check_np(0x16f07bbd0, 0x4000, 0x1, 0x0) = 12
[0.619536] systrace: launchd[1] write(0x2, 0x100ed85e6, 0xa, 0x0) = 9     ← M01 (post-cache-probe)
[0.622365] systrace: launchd[1] write(0x2, 0x100ed866d, 0xb, 0x0) = 9     ← M02 (persistent allocator created)
…then 890 s of kernel memory-stat spam at CONSTANT free 96 MB. M03 (0xc) never printed.
```

Decoded via BOOTSTRAP-LOCKS.md §6: **M01 and M02 fired, M03 did not.** The
kernel stayed alive (no usertrap, no pid-1 exit, no MACH Reboot), memory stats
flat, zero further systrace lines — i.e. the wall is a **silent, page-silent,
BSD-syscall-free EL0 loop** (mach traps are never traced by systrace=1, so a
mach-trap-per-iteration loop is also console-invisible).

## 2. Binary-ground-truth map of the hang window (D3 dyld, unstripped)

M02 fires at `start()+0x1588`; the window is exactly two compiled statements
(disassembly, all addresses from `/tmp/fleet/d4/disasm-full.txt`, D3 binary):

| addr | instruction | meaning |
|---|---|---|
| 0x15a4 | `bl _d3_mark` (M02) | allocator created |
| 0x15b4 | `bl _lsl__Allocator__aligned_allocEmm` | **w1=8 (alignof ProcessConfig), w2=0x1e8 (sizeof=488 B)** |
| 0x15cc | `bl _dyld4__ProcessConfigC1` | ProcessConfig ctor (Process→Security→Logging→DyldCache→PathOverrides, C2 @0x3244) |
| 0x15d8 | `bl _d3_mark` (M03) | never reached |

Sub-window A (`aligned_alloc(16, 512→goodSize 512 B)`, lsl/Allocator.cpp):
`aligned_alloc` @0x235f8 → **virtual** `allocate_buffer` via vtable slot 0x38 →
`PersistentAllocator::allocate_buffer` @0x24a3c: `Lock::lock` (no-op: null
lock, D3 §1b) → **while(1)** { `lower_bound` iterator ctor on `_freeSizeHash`
→ scan (`findSpace`) → on no-fit: `vm_allocate_bytes` → 3×BTree insert →
`reloadMagazine` → loop } → on fit: `reserveRange` + `reloadMagazine` +
`validate()` → `AllocationMetadata` placement.

Sub-window B (ctor): Process (getpid; `findApple` walk; `getMainPlatform`
in-memory LC walk; `executable_file`/`dyld_file` hex → `fsgetpath` 427;
`SyscallDelegate::realpath` → open 5; `internalInstall` commpage read
`_COMM_PAGE_DEV_FIRM`; `dyldCommPageFlags` commpage read; D1's in-memory
`amfi_check_dyld_policy_self`) → Security (in-memory) → Logging (in-memory
env) → **DyldCache/CacheFinder** (`SUPPORT_IGNITION` = 0 → compiled out; first
failing-BSD-syscall candidate = `open(IPHONE_DYLD_SHARED_CACHE_DIR)` = ENOENT
→ **would systrace-print — absent in v68b**) → PathOverrides.

## 3. Mission item 1 verdict — every trap and every loop in the window

Syscalls/traps actually reachable before the first systrace-visible failure:

| call | trap/stub | audit verdict (fresh, vs booted kernel's tables) |
|---|---|---|
| `vm_allocate` (vm_allocate_bytes) | `_kernelrpc_mach_vm_allocate` wrapper @0x55e14 → trap **−10** `mov x16,#-0xa; svc; ret` @0x55c34 | ✓ syscall_sw.c row 10: `_kernelrpc_mach_vm_allocate_trap, 4 args, munge_wwlw`; wrapper passes (task, &addr, size, flags) = 4 args; KERN_INVALID_ARGUMENT(0x10000004) MIG-fallback branch present and correct |
| `vm_protect` (guard page) | trap **−14** @0x55c4c | ✓ row 14: 5 args `munge_wllww`; wrapper passes 5 |
| `vm_deallocate` | trap **−12** @0x55c40 | ✓ row 12: 3 args `munge_wll` |
| `mach_task_self()` | `_task_self_trap` **−28** @0x55c28 | ✓ row 28; set by `mach_init` (called at start()+0x1174, long before M01) |
| getpid / open / close / fcntl / fsgetpath / mprotect | BSD 20/5/6/92/427/74 | ✓ syscalls.master: getpid=20, fsgetpath=427 (AUE_FSGETPATH), fcntl=92 NO_SYSCALL_STUB, munmap=73 (D2's fix intact) |

Loops enumerated in the window (assert-free = silent-spin capable):

1. `PersistentAllocator::allocate_buffer` **while(1)** @0x24ae4→0x24c10 — every
   escape hatch is LOUD: `vm_allocate_bytes` failure → `assert` →
   `__assert_rtn` → `abort_report_np` → `halt` → console+`abort_with_payload`
   (all verified in the linked binary; D1 shims, no TLS); successful-but-
   never-accepted refill drains the magazine into `assert(_magazine[0].size !=
 0)` / `refill` contracts — also loud. **A while(1) spin cannot stay silent
 past a few dozen iterations — hence NOT the v68b wall in its raw form.**
2. `BTree` iterator machinery (lower_bound ctor walk, `operator++` @0x24b6c,
   node walks) — **assert-free pure pointer/index chasing**. A subtly corrupt
   node (or a miscompile of the template under the new SDK libc++/clang — this
   source never shipped on this compiler) spins forever, touching no pages and
   making no syscalls: **fits the v68b signature exactly. Top candidate.**
3. Process ctor in-memory walks (`findApple` env walk @0x3320, LC walks,
   `_simple_getenv`) — bounded by NULL/fault; `findApple` ran fine pre-M02
   (MemoryManager ctor + dyld_hw_tpro probe), LC walk proven by v61's stock
   dyld reaching stat64. Lower probability.
4. commpage reads (`_COMM_PAGE_DEV_FIRM`, `_COMM_PAGE_DYLD_FLAGS`) — would
   fault LOUD if unmapped. Excluded by v68b's no-usertrap.

Also verified and NOT the defect (each was a named hypothesis, now struck with
evidence): `vm_page_size` = 0x1000 in glue.c's static init is OVERRIDDEN — the
linked `mach_init` @0x5382c is libc_primitives.c's version (won the duplicate
against glue.c's) and sets `vm_page_size=0x4000` (16 KiB, correct) +
`mach_task_self_=1` before any allocator use; `Lock::lock` on the bootstrap
MemoryManager is the proven no-op; vtable slot 0x38 executed successfully once
inside the PersistentAllocator ctor (M02 fired), so virtual dispatch is sound;
assert→halt path is PROVEN loud end-to-end.

**Conclusion**: the exact loop cannot be named from static evidence alone —
by design of the fix below, the NEXT hang either dies LOUD at the guard or
pins itself to a function via A1/A2/A3 (and repeats pin the iteration).

## 4. The fix (0006) — loop guard + instruments, one boot (D3 precedent)

Patch `patches-d4/0006-d4-allocator-markers-retry-guard.patch` (25 insertions,
`lsl/Allocator.cpp` only; `git apply --check` PASS on the committed D3 chain;
header overlay `shims-d4/d3_boottrace.h` adds the marker defines):

1. **Retry guard** in `PersistentAllocator::allocate_buffer`: the refill
   while(1) is bounded at **16 attempts** (a healthy boot needs ≤ 2); on the
   17th attempt `assert(0 && "PersistentAllocator: vm_allocate retry limit
   exceeded (16) — allocation loop not converging")` → the PROVEN loud path
   (`__assert_rtn` → `halt` → console line + `abort_with_payload` 521 → pid-1
   exit → MACH Reboot + park). A silent spin of this class is now impossible.
2. **Markers** (write(2) EBADF technique, D3-proven; lengths unique, unused
   range; `__has_include`-guarded so cache-builder builds are untouched):

| marker | len (hex) | site | decode |
|---|---|---|---|
| A1 | 23 (`0x17`) | `lsl::Allocator::aligned_alloc` ENTRY | every allocation attempt; count = allocation index |
| A2 | 24 (`0x18`) | `PersistentAllocator::allocate_buffer` while(1) top | one line per loop iteration; REPEATS = iterations |
| A3 | 25 (`0x19`) | scan-exhausted, before `vm_allocate_bytes` | refill path taken |

Updated decode table (full = D3's §6 table + these):

- **W-A1 (alloc spin)**: A1 then repeating A2 (+A3 pairs) → while(1) spin —
  now impossible (guard), so a repeat pattern means the guard build is NOT
  resident (check root sha `5f82a901…`).
- **W-A2 (single A2, no A3, silence)**: hang INSIDE the scan (BTree
  lower_bound/iterator walk) on the FIRST iteration — pins mission candidate
  2 to the BTree machinery; next step = symbolize from a PC sample (kernel
  lane's §7 sampler ask, D3 doc) or narrow with an A4 inside the iterator.
- **W-A3 (single A2+A3 then silence)**: hang in `vm_allocate_bytes`→insert→
  `reloadMagazine` (all assert-guarded → would be loud) — treat as
  reloadMagazine/insert BTree walk hang; same follow-up as W-A2.
- **W-A4 (A1 k=1 then silence, no A2)**: hang between aligned_alloc entry and
  the allocate_buffer call — excluded by construction (only targetSize math
  between them); A1 k=1 with A2 present = ProcessConfig STORAGE alloc
  succeeded → silence with k=1 means the CTOR hung before its first internal
  allocation (Process/Security/Logging) → mission candidate 3.
- **W-A5 (A1 k≥2 then silence)**: hang in a LATER allocation (ctor strdup /
  DyldCache / PathOverrides) — the k-th allocation index names the call site.
- **W-A6 (A3 absent entirely, M03 absent)**: A1/A2 both silent = embedded dyld
  is NOT D4 (check root sha `5f82a901…` / pairing).

## 5. Validation (all host-side)

| gate | result |
|---|---|
| patch discipline | `git apply --check` PASS on committed D3 chain; applied + committed stepwise (`40255b8 D4 0006`) in `/tmp/fleet/d4/dyld-src` |
| header parity | MH_MAGIC_64 ARM64 DYLINKER NOUNDEFS DYLDLINK, adhoc `codesign -v` PASS |
| size | 882,528 B (D3 882,336; +192 = A1/A2/A3 code + guard assert) |
| marker sweep | **16/16** strings (13 D3 + 3 D4); **16** `bl _d3_mark` call sites; guard assert string + vm_allocate-fail assert string both embedded |
| stub sweep | **67/67** unique stub symbols vs kernel tables (`svc #0x80`, numbers exact incl. −10/−12/−14/−15/−28, munmap `mov x16,#0x49`=73); aliases byte-identical via archive carry (`syscalls_impl.o` cmp PASS vs D2's validated object) |
| lock surface | `_os_unfair_lock_lock` = `bl _d3_lock_barrier` (dmb ishld); 0 `___ulock_wait2`/`___ulock_wake` refs; TPIDRRO = exactly the 3 dormant sites |
| F1c mapSegments | `mov w3,#0x0` @0x12c68 → `bl _vm_allocate`; stock `mov w3,#0x1` @0x12ca4 — D3-identical addresses ✓ |
| **root-v17** | fsck_hfs -fn "appears to be OK"; **198 identical + 1 differing** (`usr/lib/dyld`, 0755, byte-identical to registered dyld); F1c verify PASS: predicate 36/36, GOT 3,944, census 536(+1)=537, 16,606 pointers in range, symtab 21,048, disjointness 0/0, `__dyld4` pair 0x3003870e8/0x3003858d0; NULL gate PASS (704→0 NULL, 504 in-window, 71 residual, __got/__la_resolver 0) |
| pins | root-v16 sha unchanged before AND after (§ header); D3/D2/D1 lane files untouched (archive reused read-only, copied into d4/libs for lane isolation) |

## 6. Pairing rule + asks

- root-v17 ships the D4 dyld — it IS the image's `/usr/lib/dyld` (D1 pairing
  rule). Ask: `WITH_ROOT17` pin from sha `5f82a9016fc76f57…` **only together
  with dyld.blazer-d4-1066.8** (`7e092f33f86cf102…`).
- **F2**: QEMU A/B v16 vs v17 (tag copy from inputs; v17 embeds the D4 dyld
  automatically) BEFORE any device boot — ~15 min, decodes the wall with zero
  device risk. Success = boot past M02 with A1/A2/A3 lines marching (W-A2/A3
  decode) or a LOUD guard/assert halt (diagnostic landed); silent repeat
  without A1/A2 = W-A6 (wrong image) — sha-check first.
- **Kernel lane**: D3's §7 pid-1 PC-sampler ask stands — W-A2's BTree-walk
  class is only fully named by a sampled EL0 PC.
- **Coordinator**: keep `systrace=1` + `cpus=1` boot-args (markers ride
  systrace's failed-BSD prints; lock no-ops assume the single-threaded
  loader).

## 7. Files + reproduce

```
/tmp/fleet/d4/dyld.blazer-d4-1066.8       sha 7e092f33f86cf102c16abd3665ef1d6d4b5056bed22a49b83253c90fc8225fba (882,528 B)
/tmp/fleet/d4/root-v17-candidate.hfs      sha 5f82a9016fc76f579937aa036df47a7b3df01b6f4581aad8ba206573c54ac5d9 (29,360,128 B)
/tmp/fleet/d4/patches-d4/0006-d4-allocator-markers-retry-guard.patch  (lsl/Allocator.cpp; git-apply-able onto D3's committed chain)
/tmp/fleet/d4/patches-d4/d3_boottrace.h.with-d4-markers              (header overlay source of truth)
/tmp/fleet/d4/shims-d4/d3_boottrace.h                                (active overlay; shims-d3 fallback kept in search path)
/tmp/fleet/d4/dyld-src/                   (D3 committed chain + D4 0006, stepwise commit 40255b8)
/tmp/fleet/d4/d4-rebuild-dyld.sh          (apply 0006 + xcodebuild D1/D3 recipe + adhoc sign)
/tmp/fleet/d4/d4-build-v17.sh             (v16 pin → v17, all gates)
/tmp/fleet/d4/verify_d4_dyld.py           (linked-binary verification: stubs/locks/markers/guard/TPIDRRO)
/tmp/fleet/d4/v12-verify.log              (F1c verify PASS on v17 inputs)
/tmp/fleet/d4/build-final.log             (xcodebuild RC=0)
research/laguna-usb-fix/patches-d4/       (durable twin of the patch set)
research/laguna-usb-fix/ALLOCATOR-HANG.md (this file)
```

Boundary honored: D1/D2/D3/F1d/F1c lane files untouched (libdyld-support.a
reused byte-identical via read-only copy; the D3 dyld-src chain cloned, not
edited — 0006 is a layered patch with its own commit).

---

5-line summary:
1. v68b's wall is pinned to the M02→M03 window: `aligned_alloc(8,488)` +
   `ProcessConfig` ctor — mapped statement-by-statement from the linked D3
   binary; static evidence eliminates the while(1)'s raw form (every escape
   hatch is assert→__assert_rtn→halt→console+abort = LOUD: verified
   end-to-end), leaving the assert-free BTree iterator walk (top candidate) or
   ctor in-memory walks; the console's flat memory stats + zero failed-BSD
   lines fit exactly those in-memory silent-spin classes.
2. Trap audit (fresh, vs booted kernel tables): mach_vm_allocate −10
   (munge_wwlw, 4-arg wrapper branch-verified), vm_protect −14, vm_deallocate
   −12, task_self_trap −28, mach_vm_map −15 — ALL correct in the linked
   binary; BSD ctor calls getpid 20 / open 5 / fsgetpath 427 / fcntl 92 ✓;
   `vm_page_size` wrong-4096 hypothesis STRUCK (linked mach_init is
   libc_primitives' 16 KiB version, ran before use).
3. Fix shipped WITH instruments (0006, layered on D3's committed chain):
   PersistentAllocator while(1) bounded at 16 retries → LOUD assert halt (the
   silent-spin class is now impossible), + markers A1 (aligned_alloc entry,
   len 0x17) / A2 (while(1) iteration, 0x18) / A3 (no-fit vm_allocate path,
   0x19) — repeats pin the exact loop; W-A1..A6 decode table pre-registered.
4. dyld.blazer-d4-1066.8 (882,528 B, +192, adhoc): 16/16 markers + 16 call
   sites, 67/67 stubs re-swept live, munmap 73 ✓, lock barrier surface ✓, 0
   ulock refs, TPIDRRO 3 dormant, F1c mapSegments at D3-identical addresses;
   root-v17 = pinned root-v16 + this dyld: fsck OK, exactly-1-file delta, F1c
   verify PASS (36/36, GOT 3,944, census 537, disjointness 0/0), NULL gate
   PASS; candidate sha 5f82a9016fc76f57….
5. Asks: F2 QEMU A/B v16-vs-v17 FIRST (success = A-markers marching past M02
   or a loud guard halt; silent-no-A-markers = W-A6 wrong-image → sha check);
   coordinator WITH_ROOT17 pin from `5f82a9016fc76f57…` only with the D4 dyld;
   keep systrace=1 + cpus=1; kernel-lane PC-sampler ask stands for the BTree
   walk class.
