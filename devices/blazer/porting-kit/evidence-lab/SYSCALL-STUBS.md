# SYSCALL-STUBS.md — D1/F1d dyld syscall-stub audit + v66b hang root cause (lane D2, 2026-10-27)

Status: **AUDIT COMPLETE + DEFECTS FIXED + root-v15 candidate BUILT, all gates
green, not fired** (coordinator owns device/QEMU). Predecessors: D1
(`DYLD-BUILD.md`, syscalls_impl.S + root-v13) → F1d (`NULL-DEREF.md`, errno/lock
fix + root-v14) → **this lane (D2)**.

Candidates (registered):
- `/tmp/fleet/d2/dyld.blazer-d2-1066.8` — 882,768 B,
  sha256 `731bf809729b9dd368545e29d68c214f94e3f149f378474182899f71b01e8a07`
- `/tmp/fleet/d2/root-v15-candidate.hfs` — 29,360,128 B,
  sha256 `89d871b94f0072ab1834990bea12b366986afa8bd3f3aeb063783d696d0dba46`
  (= pinned root-v14 + the D2 dyld, exactly ONE file differs: `usr/lib/dyld`)

Pinned artifacts re-hashed unchanged before AND after: root-v14
`12811844debbd44e…`, root-v13 `8559c64349e83596…`.

---

## 1. The v66b wall, root-caused to the byte

QEMU fact (F2 evidence `runC-late-v66b-nvram/`, mach r4r7-hfs + root-v14):
guest hangs in kernel exception vectors, PC=`_ExceptionVectorsBase+0x480`,
X16=0x126 (294 = shared_region_check_np), X0=X1=0xffffffffffffffff,
X30/LR=0x100ef7bbc, paused at 900 s — no MACH Reboot. The old extracted dyld
called the identical syscall successfully (=12) in v61–v65b.

**Register-state decode (every value now explained):**

| evidence value | meaning |
|---|---|
| `systrace: launchd[1] shared_region_check_np(0x16f10bbd0, 0x4000, 0x1, 0x0) = 12` | the syscall REACHED the kernel with the CORRECT X0 (=&cacheBaseAddress stack buffer) and returned 12 (ENOMEM, cacheless boot, expected). Args were NEVER the problem — the mission's "constant -1" hypothesis is struck: X0=X1=−1 is the *cerror RETURN convention* (`cerror_return_t` = unsigned __int128 → x0:x1 = −1:−1), i.e. the state AFTER the error path ran |
| dyld base **B = 0x100EA4000** | unique 16 K-aligned base consistent with the captured registers (v65b's D1 dyld was 0x100B54000; F1d's dyld is a fresh kernel-chosen allocation) |
| X30 = 0x100ef7bbc = **B + 0x53BBC** | `nm` of the F1d dyld: `___shared_region_check_np` @ **0x53bac**; 0x53BBC = stub+0x10 = the `1:` label — **the `ret` instruction itself** |
| X16 = 294 | set by the stub's `mov x16,#294`; the loop body never touches it |
| X0 = X1 = −1 | F1d `_cerror`'s return values (errno fix working as designed) |
| X8 = 0x100f501f0 = **B + 0xAC1F0** | &errno (F1d's static errno store) — left there by `_cerror` |
| PC = EL0-IRQ vector entry, kernel alive (memory-stat spam continues) | the CPU oscillates: userland 1-instruction spin ↔ timer IRQ ↔ scheduler. No usertrap (the loop address is mapped and valid), no pid-1 exit, no MACH Reboot — a silent EL0 livelock |

**The defect (source level):** D1's `BSDSYS` macro:

```asm
_\name\():
	mov	x16, #\num
	svc	#0x80
	b.cc	1f
	bl	_cerror      ; ← x30 (caller's return address) := &1:
1:
	ret              ; ← after _cerror returns, LR = &1: → ret branches TO ITSELF
```

`bl _cerror` destroys the caller's x30 and nothing restores it. On the
SUCCESS path (`b.cc` taken) the stub is fine; on ANY BSD-syscall ERROR the
stub enters a one-instruction infinite loop at EL0.

**Apple's real convention** (ground truth: macOS `/usr/lib/system/
libsystem_kernel.dylib` `_close`, arm64, disassembled on this host):

```
_close:  mov x16,#6 ; svc #0x80 ; b.lo skip
         pacibsp ; stp x29,x30,[sp,#-0x10]! ; mov x29,sp
         bl _cerror
         mov sp,x29 ; ldp x29,x30,[sp],#0x10 ; retab   ← LR restored, ret to CALLER
```

**Why v65b masked it:** D1's original `_cerror` crashed INSIDE (TLS deref,
NULL-DEREF.md §1) before ever returning — the LR bug was unreachable. F1d
fixed `_cerror` (errno_dyld.c), the error path returned for the first time,
and the very first failing syscall (`shared_region_check_np` = 12) hit the
livelock. A/B clean: old dyld (Apple stubs) survived v61–v65b; D1/F1d dyld
never got past its first BSD errno.

**Kernel-side conventions verified in-source** (so the fix matches the booted
kernel, not just Apple's userland): error signaled by CARRY —
`bsd/dev/arm/systemcalls.c:603` `ss64->cpsr |= PSR64_CF` ("setting carry to
trigger cerror call"), cleared on success (:659); mach traps = `-x16` indexed
into `mach_trap_table` (`osfmk/arm64/bsd_arm64.c:198,215`); `-3`/`-4` are
sleh.c special cases (`MACH_ARM_TRAP_ABSTIME/CONTTIME`, sleh.c:1661-1664).

## 2. The audit (all 68 svc sites; DYLD-BUILD.md's "56 stubs" was an undercount)

Method: mechanical cross-check of every stub in `syscalls_impl.S` against the
BOOTED kernel's own tables — `bsd/kern/syscalls.master` (BSD numbers,
NO_SYSCALL_STUB flags, `sys_`-prefixed kernel names), `osfmk/kern/syscall_sw.c`
`mach_trap_table` (arm64 dispatch = −index), sleh.c ABSTIME/CONTTIME —
plus per-stub disassembly of the compiled object and the LINKED binary
(`otool -tv`): first insn `mov x16,#N`, second `svc #0x80`, x0–x8 untouched,
success return in x0, BSD error path = stp/bl _cerror/ldp before the ret.
Tooling: `patches-dyld-stubs/audit.json` (source+object audit),
`verify-linked.json` (linked-binary verification).

**Verdict: 68/68 svc sites now correct in the linked binary.**
48 BSD-class: numbers 48/48 ✓ (one FIXED), args 48/48 ✓, LR save/restore
48/48 ✓ (all FIXED — systemic). 20 mach traps: numbers 20/20 ✓, shape
`svc;ret` 20/20 ✓ (no cerror path — kern_return_t in x0, by design). Plus 3
tail-branch aliases (`_kernelrpc_mach_vm_{allocate,deallocate,protect}` → `b
__kernelrpc_*`) — LR-safe by construction ✓.

Full table (68 rows):

| # | stub | num | class | kernel entry (this tree) | num | args pass | error-path LR | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `close` | 6 | BSD | sys_close | Y | x0-x7 untouched | stp/ldp OK | OK |
| 2 | `write` | 4 | BSD | write | Y | x0-x7 untouched | stp/ldp OK | OK |
| 3 | `open` | 5 | BSD | open | Y | x0-x7 untouched | stp/ldp OK | OK |
| 4 | `openat` | 463 | BSD | openat | Y | x0-x7 untouched | stp/ldp OK | OK |
| 5 | `open_dprotected_np` | 216 | BSD | open_dprotected_np | Y | x0-x7 untouched | stp/ldp OK | OK |
| 6 | `unlink` | 10 | BSD | unlink | Y | x0-x7 untouched | stp/ldp OK | OK |
| 7 | `chmod` | 15 | BSD | chmod | Y | x0-x7 untouched | stp/ldp OK | OK |
| 8 | `fchmod` | 124 | BSD | fchmod | Y | x0-x7 untouched | stp/ldp OK | OK |
| 9 | `ftruncate` | 201 | BSD | ftruncate | Y | x0-x7 untouched | stp/ldp OK | OK |
| 10 | `mkdir` | 136 | BSD | mkdir | Y | x0-x7 untouched | stp/ldp OK | OK |
| 11 | `rename` | 128 | BSD | rename | Y | x0-x7 untouched | stp/ldp OK | OK |
| 12 | `getpid` | 20 | BSD | getpid | Y | x0-x7 untouched | stp/ldp OK | OK |
| 13 | `ioctl` | 54 | BSD | ioctl | Y | x0-x7 untouched | stp/ldp OK | OK |
| 14 | `stat` | 188 | BSD | stat | Y | x0-x7 untouched | stp/ldp OK | OK |
| 15 | `fstat` | 189 | BSD | sys_fstat | Y | x0-x7 untouched | stp/ldp OK | OK |
| 16 | `fstatat` | 469 | BSD | fstatat | Y | x0-x7 untouched | stp/ldp OK | OK |
| 17 | `statfs` | 157 | BSD | statfs | Y | x0-x7 untouched | stp/ldp OK | OK |
| 18 | `getfsstat` | 18 | BSD | getfsstat | Y | x0-x7 untouched | stp/ldp OK | OK |
| 19 | `getattrlist` | 220 | BSD | getattrlist | Y | x0-x7 untouched | stp/ldp OK | OK |
| 20 | `getattrlistbulk` | 461 | BSD | getattrlistbulk | Y | x0-x7 untouched | stp/ldp OK | OK |
| 21 | `getxattr` | 234 | BSD | getxattr | Y | x0-x7 untouched | stp/ldp OK | OK |
| 22 | `setxattr` | 236 | BSD | setxattr | Y | x0-x7 untouched | stp/ldp OK | OK |
| 23 | `fsetxattr` | 237 | BSD | fsetxattr | Y | x0-x7 untouched | stp/ldp OK | OK |
| 24 | `mmap` | 197 | BSD | mmap | Y | x0-x7 untouched | stp/ldp OK | OK |
| 25 | `mprotect` | 74 | BSD | mprotect | Y | x0-x7 untouched | stp/ldp OK | OK |
| 26 | `munmap` | 73 | BSD | munmap | Y | x0-x7 untouched | stp/ldp OK | FIXED: 71→73 |
| 27 | `pread` | 153 | BSD | pread | Y | x0-x7 untouched | stp/ldp OK | OK |
| 28 | `socket` | 97 | BSD | socket | Y | x0-x7 untouched | stp/ldp OK | OK |
| 29 | `connect` | 98 | BSD | connect | Y | x0-x7 untouched | stp/ldp OK | OK |
| 30 | `sysctlbyname` | 274 | BSD | sys_sysctlbyname | Y | x0-x7 untouched | stp/ldp OK | OK |
| 31 | `fsgetpath` | 427 | BSD | fsgetpath | Y | x0-x7 untouched | stp/ldp OK | OK |
| 32 | `__sendto` | 133 | BSD | sendto | Y | x0-x7 untouched | stp/ldp OK | OK |
| 33 | `__proc_info` | 336 | BSD | proc_info | Y | x0-x7 untouched | stp/ldp OK | OK |
| 34 | `__shared_region_check_np` | 294 | BSD | shared_region_check_np | Y | x0-x7 untouched | stp/ldp OK | OK |
| 35 | `__shared_region_map_and_slide_2_np` | 536 | BSD | shared_region_map_and_slide_2_np | Y | x0-x7 untouched | stp/ldp OK | OK |
| 36 | `__map_with_linking_np` | 550 | BSD | map_with_linking_np | Y | x0-x7 untouched | stp/ldp OK | OK |
| 37 | `getentropy` | 500 | BSD | getentropy | Y | x0-x7 untouched | stp/ldp OK | OK |
| 38 | `abort_with_payload` | 521 | BSD | abort_with_payload | Y | x0-x7 untouched | stp/ldp OK | OK |
| 39 | `__proc_info_extended_id` | 545 | BSD | proc_info_extended_id | Y | x0-x7 untouched | stp/ldp OK | OK |
| 40 | `__kdebug_typefilter` | 177 | BSD | kdebug_typefilter | Y | x0-x7 untouched | stp/ldp OK | OK |
| 41 | `__kdebug_trace_string` | 178 | BSD | kdebug_trace_string | Y | x0-x7 untouched | stp/ldp OK | OK |
| 42 | `__kdebug_trace64` | 179 | BSD | kdebug_trace64 | Y | x0-x7 untouched | stp/ldp OK | OK |
| 43 | `__ulock_wait` | 515 | BSD | sys_ulock_wait | Y | x0-x7 untouched | stp/ldp OK | OK |
| 44 | `__ulock_wake` | 516 | BSD | sys_ulock_wake | Y | x0-x7 untouched | stp/ldp OK | OK |
| 45 | `__ulock_wait2` | 544 | BSD | sys_ulock_wait2 | Y | x0-x7 untouched | stp/ldp OK | OK |
| 46 | `fcntl` | 92 | BSD | sys_fcntl | Y | x0-x7 untouched | stp/ldp OK | OK |
| 47 | `mremap_encrypted` | 489 | BSD | mremap_encrypted | Y | x0-x7 untouched | stp/ldp OK | OK |
| 48 | `task_self_trap` | -28 | MACH | task_self_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 49 | `_kernelrpc_mach_vm_allocate_trap` | -10 | MACH | _kernelrpc_mach_vm_allocate_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 50 | `_kernelrpc_mach_vm_deallocate_trap` | -12 | MACH | _kernelrpc_mach_vm_deallocate_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 51 | `_kernelrpc_mach_vm_protect_trap` | -14 | MACH | _kernelrpc_mach_vm_protect_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 52 | `_kernelrpc_mach_vm_map_trap` | -15 | MACH | _kernelrpc_mach_vm_map_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 53 | `_kernelrpc_mach_port_allocate_trap` | -16 | MACH | _kernelrpc_mach_port_allocate_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 54 | `_kernelrpc_mach_port_deallocate_trap` | -18 | MACH | _kernelrpc_mach_port_deallocate_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 55 | `_kernelrpc_mach_port_mod_refs_trap` | -19 | MACH | _kernelrpc_mach_port_mod_refs_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 56 | `_kernelrpc_mach_port_insert_right_trap` | -21 | MACH | _kernelrpc_mach_port_insert_right_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 57 | `_kernelrpc_mach_port_construct_trap` | -24 | MACH | _kernelrpc_mach_port_construct_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 58 | `_kernelrpc_mach_port_destruct_trap` | -25 | MACH | _kernelrpc_mach_port_destruct_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 59 | `task_dyld_process_info_notify_get` | -13 | MACH | task_dyld_process_info_notify_get_trap | N | N/A | n/a (svc;ret) | FIXED (was defective) |
| 60 | `mach_msg_trap` | -31 | MACH | mach_msg_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 61 | `mach_msg_overwrite_trap` | -32 | MACH | mach_msg_overwrite_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 62 | `mach_generate_activity_id` | -43 | MACH | mach_generate_activity_id | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 63 | `mach_msg2_trap` | -47 | MACH | kern_invalid | N | N/A | n/a (svc;ret) | FIXED (was defective) |
| 64 | `_kernelrpc_mach_port_guard_trap` | -41 | MACH | _kernelrpc_mach_port_guard_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 65 | `thread_get_special_reply_port` | -50 | MACH | thread_get_special_reply_port | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 66 | `_kernelrpc_mach_port_unguard_trap` | -42 | MACH | _kernelrpc_mach_port_unguard_trap | Y | x0-x7 untouched | n/a (svc;ret) | OK |
| 67 | `mach_absolute_time_kernel_trap` | -3 | MACH | kern_invalid (sleh ABSTIME) | N | N/A | n/a (svc;ret) | FIXED (was defective) |
| 68 | `__Z21__map_with_linking_npPK10mwl_regionjPK12mwl_info_hdrj` | 550 | ALIAS | map_with_linking_np | Y | x0-x7 untouched | stp/ldp OK | OK |

Table notes:
- `task_dyld_process_info_notify_get` (−13): kernel table row is spelled
  `..._notify_get_trap`; same trap, mig user symbol differs — number exact.
- `mach_msg2_trap` (−47): table row 47 lives under
  `#if defined(__LP64__) || defined(__arm64__)` (syscall_sw.c:153-155) —
  correct for arm64; the #else row is kern_invalid.
- `mach_absolute_time_kernel_trap` (−3): NOT a trap-table row — sleh.c
  special-cases −3 (`MACH_ARM_TRAP_ABSTIME`) before table dispatch and fills
  x0 with `mach_absolute_time()`. D1's number and no-error shape are correct.
- NO_SYSCALL_STUB-class entries (294, 536, 550, 521, 545, 177-179, 515/516/
  544, 463/469, …): get REAL argument passing — every stub passes x0–x7
  through untouched (proven on the bytes AND by the v66b systrace line itself,
  which shows the correct X0 at call time).

## 3. The two defects found + fixed (patch `patches-dyld-stubs/0001`)

1. **Systemic: BSD error path clobbered the caller's LR → ret-to-self
   livelock** (48/48 BSD-class stubs + the hand-written C++-mangled
   `__map_with_linking_np` body). Fix — Apple's save/restore shape, PAC-free
   (this build is not PAC-signed):

   ```asm
   b.cc	2f                    ; carry clear = success: x0 = kernel return
   stp	x29, x30, [sp, #-16]! ; preserve the CALLER's return address
   bl	_cerror               ; errno = err; returns x0:x1 = -1:-1
   ldp	x29, x30, [sp], #16   ; restore caller LR
2:	ret
   ```

   Blast radius on the boot: EVERY failed BSD syscall (ENOENT from the 15-dir
   cache probe, sysctl name2oid misses, fstatat EBADF, getattrlist EINVAL —
   F5's absorbed-set) would have livelocked the boot one wall after v66b's.
   This fix is what makes the F1d errno path actually usable end-to-end.

2. **`munmap` numbered 71; this kernel's table has munmap = 73** — 71 is
   `{ int nosys(void); }   { old mmap }` (syscalls.master:129). First dyld
   munmap would have returned ENOSYS (78) through the (now-fixed) cerror path
   — a wrong-return landmine in the mapping-fallback paths, silent until it
   mattered. Fixed to 73 and re-verified in the linked binary
   (`mov x16,#0x49`).

Nothing else changed: mach traps, tail aliases, numbers, arg passing, and all
F1d/F1c content verified untouched (§4).

## 4. Rebuild + validation (host-only; F1d fixes kept, incremental on F1d's tree)

Rebuild recipe (D1's xcodebuild line, verbatim; archive swap is the only
delta): `/tmp/fleet/d2/d2-rebuild-dyld.sh` — copies F1D's `libdyld-support.a`
(already carrying F1d's TLS-free `errno.o` + constant-owner `os_lock.o`),
swaps in the D2-fixed `syscalls_impl.o` (compiled with
`xcrun -sdk iphoneos clang -arch arm64 -c` — the macOS-default SDK build is
REJECTED at link, "built for 'macOS'"), xcodebuild, adhoc codesign.
One gotcha recorded: `xcrun clang` without `-sdk iphoneos` produces an
object the dyld link refuses.

| gate | result |
|---|---|
| header parity | MH_MAGIC_64 ARM64 **DYLINKER** NOUNDEFS DYLDLINK TWOLEVEL, LC_BUILD_VERSION platform 2 **minos 16.5**, LC_UNIXTHREAD — identical classes to D1/F1d |
| size | 882,768 B (F1d 882,800; net −32 after reflow) |
| `PROJECT:dyld-1066.8` string | present |
| stub sweep of the LINKED binary | 68/68: numbers ✓ (vs kernel tables), `svc #0x80` ✓, x0–x8 untouched ✓, 48/48 BSD error paths `stp/bl _cerror/ldp` ✓, 20/20 mach `svc;ret` ✓ |
| `___shared_region_check_np` in binary | @0x55df0: `mov x16,#0x126; svc; b.lo; stp x29,x30; bl _cerror; ldp x29,x30; ret` |
| `_munmap` in binary | @0x55d10: `mov x16,#0x49` (= 73) with the same LR shape |
| F1d errno fix intact | `_cerror` = static errno store, **0 TPIDRRO derefs** |
| F1d lock fix intact | `_os_unfair_lock_lock` fast path = `mov w8,#1` constant-owner CAS |
| TPIDRRO sweep, whole binary | exactly the 3 documented dormant sites (mig reply ×2, mach_msg aux) |
| F1c mapSegments patch intact | `mov w3,#0x0` FIXED-first vm_allocate present in mapSegments |
| codesign | adhoc, `codesign -v` PASS |

## 5. root-v15 (mission item 3)

`/tmp/fleet/d2/d2-build-v15.sh` (twin of f1d-build-v14.sh): pinned root-v14 →
scratch copy → install D2 dyld at `/usr/lib/dyld` (0755) → fsck → gates → sha.

| gate | result |
|---|---|
| pinned root-v14 sha before AND after | `12811844debbd44e…` unchanged |
| pinned root-v13 sha before AND after | `8559c64349e83596…` unchanged |
| dyld input sha | `731bf809729b9dd3…` (registered artifact, byte-identical into image) |
| fsck_hfs -fn | `The volume iOSRoot appears to be OK` |
| volume diff vs v14 | 199 files, same list, **exactly 1 differs** = `usr/lib/dyld` (198 identical) |
| mounted v15 `/usr/lib/dyld` | byte-identical to the registered D2 dyld, mode 0755 |
| F1c `build_v12.py verify` | PASS: 16,606/16,606 pointers in range, symtab 21,048, disjointness 0/0, `__dyld4` pair 0x3003870e8/0x3003858d0, VERIFY RESULT: PASS |
| F1d NULL gate | PASS: 704 census slots → 0 NULL, 504 re-targeted in-window, 71 documented data residual; `__got`/`__la_resolver` old-cache residuals 0 |

**root-v15 sha256 `89d871b94f0072ab1834990bea12b366986afa8bd3f3aeb063783d696d0dba46`** (29,360,128 B).

## 6. Expected v66c signature (F2 QEMU A/B ask)

With root-v15, v66b's livelock must be gone. Expected console, in order:
1. Same kernel lines: `load_init_program … /sbin/launchd`, the dyld signature
   note, `shared_region: … check_np(0x16f10bbd0) … failed` +
   `systrace: … shared_region_check_np(…) = 12` (kernel side unchanged).
2. **NEW: boot PROGRESSES past the cache probe** — more systrace lines
   (openat/stat64 on the 15 cache-probe dirs = 2/9 ENOENT class per F5,
   getattrlist /dev EINVAL, sysctl misses) instead of the silent stat-spam
   hang. Each of those syscalls FAILS — the fixed stub must return −1/errno
   and dyld must CONTINUE; that is exactly the path that hung v66b.
3. Next walls, in F1d §5's order (W7a dyld-runtime-window, W7b no-initializer
   wall-#5, W7c dormant TLS, W7d patch-not-resident — now discriminable from
   the stub fix: a ret-to-self livelock again at ANY stub = this fix not
   resident; check the embedded root sha `89d871b94f0072ab…`).
4. mach_msg2 (−47) is now first-live-exercised only if dyld MIG-falls back;
   the −47 arm64 table row is verified correct (§2 note).

## 7. Files + reproduce

```
/tmp/fleet/d2/dyld.blazer-d2-1066.8      sha 731bf809729b9dd368545e29d68c214f94e3f149f378474182899f71b01e8a07 (882,768 B)
/tmp/fleet/d2/root-v15-candidate.hfs     sha 89d871b94f0072ab1834990bea12b366986afa8bd3f3aeb063783d696d0dba46 (29,360,128 B)
/tmp/fleet/d2/syscalls_impl.S            FIXED source (orig preserved as syscalls_impl.S.orig; D1's lane file untouched)
/tmp/fleet/d2/syscalls_impl-fixed.o      compiled stub object (iphoneos SDK)
/tmp/fleet/d2/audit_stubs.py             source+object audit vs kernel tables
/tmp/fleet/d2/verify_d2_dyld.py          linked-binary verification (68 stubs + F1d/F1c regression)
/tmp/fleet/d2/audit.json                 per-stub audit evidence
/tmp/fleet/d2/verify-linked.json         per-stub linked-binary evidence
/tmp/fleet/d2/d2-rebuild-dyld.sh         archive swap + D1 xcodebuild recipe + sign
/tmp/fleet/d2/d2-build-v15.sh            v14 pin → v15, all gates
/tmp/fleet/d2/build-final.log            xcodebuild RC=0
/tmp/fleet/d2/v12-verify.log             F1c verify PASS
research/laguna-usb-fix/patches-dyld-stubs/
  0001-syscalls-impl-lr-restore-munmap73.patch   (durable twin of the fix; git-apply-able onto D1's syscalls_impl.S 3c14b372c36dbe21…)
  syscalls_impl.S                                 (full fixed reference copy, sha ee63d406dd11f285…)
  audit.json / verify-linked.json / d2-syms.txt   (evidence; disasm regenerable: otool -tv <dyld>)
research/laguna-usb-fix/SYSCALL-STUBS.md          (this file)
```

Boundary honored: D1's lane file `/tmp/fleet/d1/libs/syscalls_impl.S`
(sha `3c14b372c36dbe21…`) and F1d's lane files are UNTOUCHED — the fix ships
as a patch (the fleet's cross-lane fold-in path) plus my own working copies.
BASE derivation note for future dumps: F1d/D2 dyld `__TEXT` vmaddr 0x0, so
runtime dyld addresses resolve as `va − B` with B 16 K-aligned; v66b proved
B = 0x100EA4000 for the F1d binary via X30 = B+0x53BBC (`1:` of the
check_np stub) — same nm-based recipe as NULL-DEREF.md §1.

Asks:
- **F2**: QEMU A/B root-v14 vs root-v15 (tag copy from inputs/tag-v66b.env,
  5 shas; v15 embeds the new dyld automatically — it IS `usr/lib/dyld`).
  Success = boot progress past the cache probe (§6.2); livelock again =
  §6.4 discriminator.
- **Coordinator**: WITH_ROOT15 pin from sha `89d871b94f0072ab…` only with
  the D2 dyld in the same image (v15 ships both, D1's v13 pairing rule).
- **F1/F-successor lanes**: any future `syscalls_impl.S` edits must keep the
  LR save/restore on every new BSD stub — the macro enforces it; don't
  hand-roll error paths.

---
5-line summary:
1. v66b hang ROOT-CAUSED byte-exactly: D1's `BSDSYS` stub macro clobbered the
   caller's LR on the BSD error path (`bl _cerror`, shared `ret` → ret-to-self
   EL0 livelock); v66b registers fully decoded (base 0x100EA4000, LR=B+0x53BBC
   = the check_np stub's own `ret`, X16=294, X0:X1=−1:−1 = cerror return,
   X8=&errno 0xAC1F0, kernel alive in stat-spam); the kernel's carry-on-error
   convention and Apple's real stub shape (stp/bl/ldp, macOS libsystem_kernel
   `_close`) verified from primary sources; v65b masked the bug because D1's
   cerror crashed before returning.
2. Full audit of ALL 68 svc sites (56 was an undercount) against the booted
   kernel's own tables (syscalls.master / syscall_sw.c mach_trap_table /
   sleh.c ABSTIME): 47 BSD numbers correct, 20 mach traps correct (incl. −3
   ABSTIME and −47 mach_msg2), args x0–x7 untouched 68/68 — plus TWO defects:
   the systemic LR clobber (48/48 BSD-class) and munmap 71→73 (71 = nosys
   "old mmap"); "constant −1 arg" hypothesis STRUCK (−1 is the cerror RETURN).
3. Fix = patch `patches-dyld-stubs/0001` (LR save/restore ×49 sites + munmap
   73), rebuild on F1d's tree (archive swap only; errno/lock fixes kept;
   mapSegments patch intact; one gotcha: compile the .o with -sdk iphoneos);
   linked-binary sweep 68/68 clean, TPIDRRO sweep = 3 dormant documented sites.
4. root-v15 = pinned root-v14 + D2 dyld (sha 731bf809729b9dd3…, 882,768 B):
   fsck OK, exactly-1-file delta, F1c verify PASS (16,606 pointers, census,
   disjointness), NULL gate PASS; candidate sha 89d871b94f0072ab…; pins
   re-hashed unchanged before/after.
5. Asks: F2 QEMU A/B v14-vs-v15 (expected = boot progress past the cache
   probe, systrace lines resume); coordinator WITH_ROOT15 pin; D1 fold-in via
   the patch; D1's lane files untouched per ownership rules.
