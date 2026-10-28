# NULL-DEREF.md — wall #6 "launchd[1] fault VA 0x8 in the F1d dyld" → root-v14

Lane: F1d (2026-10-27). Predecessors: F1 (`INDIRECT-FIX.md`, root-v10) → F1b
(`POINTER-REBASE.md`, root-v11) → F1c (`SUBCACHE-POINTERS.md`, root-v12 + dyld
patch) → D1 (`DYLD-BUILD.md`, patched dyld build + root-v13). Status:
**root-v14 candidate BUILT + VALIDATED, not fired** (coordinator owns the
device/QEMU).

Candidates:
- `/tmp/fleet/f1d/dyld.blazer-f1d-1066.8` — 882,800 B,
  sha256 `e0e00473d6b021836bed1b513a53241b87784b1caa6d30054d93e7d072776d5d`
- `/tmp/fleet/f1d/root-v14-candidate.hfs` — 29,360,128 B,
  sha256 `12811844debbd44e49bb39dfe40860c6c59f69fbfd066c2db30f1f9fa4b3f82d`
  (= pinned root-v13 + the F1d dyld, exactly ONE file differs)

Pinned artifacts re-hashed unchanged before AND after every step:
root-v13 `8559c64349e83596…`, root-v12 `45f3367196e0cbae…`, root-v11
`7106626ecf6bb16c…`.

## 1. The v65b crash, symbolicated (mission item 1)

```
v65b (QEMU, runC-late-v65b-nvram, mach r4r7-hfs + root-v13):
  [0.621505] shared_region: … [1(launchd)] check_np(0x16f677bd0) vm_shared_region_start_address() failed
  [0.621900] systrace: launchd[1] shared_region_check_np(0x16f677bd0, 0x4000, 0x1, 0x0) = 12
  [0.622426] usertrap: launchd[1] exception 1 code 0x1 0x8: pc 0x100ba5650 lr 0x100ba5684 sp 0x16f677b00, shared cache slide 0x0
  [0.622629] usertrap:   called from 0x100ba7f58
  [0.622942] usertrap:   called from 0x100b55278
  [0.623045] usertrap:   called from 0x0
  [0.628568] pid 1 exited -- exit reason namespace 2 subcode 0xb (signal 11)
```

**Dyld load base derivation.** The boot dyld in v65 is D1's build
(`dyld.blazer-d1-1066.8`, `__TEXT` vmaddr 0x0, so base B = the kernel-chosen
load address). The outermost walked frame `0x100b55278` must be a return
address inside `start` (the frame whose own return address is 0x0 is the
kernel-entry frame — `__dyld_start` at file 0x100c does `mov x30, #0x0; b
start`, matching the `called from 0x0` line exactly). `nm` puts `start` at
0x101c..0x16e8, so B = 0x100b55278 − δ with δ ∈ (0x101c, 0x16e8); the only
16 KiB-aligned candidate is **B = 0x100B54000** (δ = 0x1278 = start+0x25c).
Cross-checks: pc−B = 0x51650 and cf1−B = 0x53f58 are both inside `__TEXT`
(vmaddr 0..0x64000) ✓.

Symbolication against D1's symtab (`/tmp/fleet/d1/dyld.blazer-d1-1066.8`,
2,955 symbols):

| runtime | file offset | symbol | note |
|---|---|---|---|
| pc  0x100ba5650 | 0x51650 | **`_cerror_nocancel`+0x10** | the faulting `ldr` |
| lr  0x100ba5684 | 0x51684 | `_cerror`+0x1c | return after `bl _cerror_nocancel` |
| cf1 0x100ba7f58 | 0x53f58 | `___shared_region_check_np`+0x10 | return after `bl _cerror` |
| cf2 0x100b55278 | 0x1278  | `start`+0x25c | return after `bl SyscallDelegate::hasExistingDyldCache` |
| cf3 0x0         | —       | `__dyld_start` (entry, x30=0) | |

Full source chain (dyld-1066.8 tree /tmp/r2-dyld-1066, ce1cc20):

1. `start()` (dyldMain.cpp:1049) → `handleDyldInCache` → :996
   `_simple_getenv(envp,"DYLD_SHARED_REGION")` (unset) → :1000
   `dyldMF->getUuid()` (D1 dyld carries LC_UUID) → **:1008
   `sSyscallDelegate.hasExistingDyldCache(...)`**
2. `SyscallDelegate::hasExistingDyldCache` (DyldDelegates.cpp:268) → **:270
   `__shared_region_check_np(&cacheBaseAddress)`**
3. Kernel: no shared cache mapped → `vm_shared_region_start_address()` fails
   → returns **12** (console lines above). Expected, designed-for path on a
   cacheless boot — dyld upstream treats `!= 0` as "no cache" and continues.
4. D1's raw stub (`syscalls_impl.S` BSDSYS macro, @0x53f48):
   `mov x16,#294; svc #0x80; b.lo ret; bl _cerror` — carry set → `bl _cerror(12)`.
5. `_cerror` (@0x51668) → `_pthread_exit_if_canceled` (ICF-folded no-op at
   0x51780 with `__subsystem_init`) → `bl _cerror_nocancel(12)`.
6. `_cerror_nocancel` (@0x51640), xnu libsyscall `custom/errno.c:61-71`:

```
0x51640  adrp x8, 0xac000        ; x8 = &errno (static, __DATA_DIRTY 0xac1f0)
0x51644  add  x8, x8, #0x1f0
0x51648  str  w0, [x8]           ; errno = err            ← SUCCEEDS (mapped)
0x5164c  mrs  x8, TPIDRRO_EL0    ; user TLS base = 0 on the boot thread
0x51650  ldr  x8, [x8, #0x8]     ; ← FAULT VA 0x8 = NULL + 8*__TSD_ERRNO(slot 1)
0x51654  cbz  x8, 0x5165c        ; null-check comes too late — deref already faulted
0x51658  str  w0, [x8]
0x5165c  mov x0,#-1 ; mov x1,#-1 ; ret
```

**Root cause**: xnu libsyscall `custom/errno.c:66`
`_os_tsd_get_direct(__TSD_ERRNO)` → `libsyscall/os/tsd.h:158`
`_os_tsd_get_base()[slot]` → `tpidrro_el0`-relative slot array with
`__TSD_ERRNO == 1` (os/tsd.h:37) → load from 0+8 = **0x8**. The dyld boot
thread never sets a TLS base (libpthread does not exist before libSystem
initializers, which never run in this boot); `tpidrro_el0` = 0. `errno = err`
(static) already succeeded — only the TSD mirror read kills. exception
code 0x1 = KERN_INVALID_ADDRESS (nothing mapped page 0) — consistent.

**A/B proof the defect is in D1's errno path, not the kernel or the probe**:
the OLD extracted-cache dyld hit the exact same failing call in v61/v61b/v63/
v63b/v64b (same kernel `shared_region … check_np … failed` + `systrace … = 12`
lines) and CONTINUED every time (map_with_linking_np, indirect-symbol wall,
prepare()). Apple's dyld links a dyld-variant libsystem_kernel whose errno path
is TLS-free; D1's build links xnu libsyscall's `custom/errno.c`, which is not.

**Frontier correction (honest scope)**: v65b did NOT actually reach
`prepare()`. The crash is in `start()`'s shared-cache probe, BEFORE
`prepare()`/any dylib load — so the F1c preferred-load path (36 dylibs at
0x300000000+) and the NXArgcPtr wire-up were never exercised in v65b. The
absence of the v64b NXArgcPtr crash line only means the boot died earlier.
"v65 cleared prepare()+0x9c4" is therefore NOT established; root-v14's first
successful boot re-opens that question with the preferred-load patch live.

**Why the dyld's own preferred-address log lines are absent**: D1's
`state.log` flows through `_simple_dprintf` → `write(2)`; PID 1 has no fds
(K5 §2 fact 1, write = EBADF), so all dyld diagnostics are dropped on this
boot. The console can neither prove nor disprove preferred loading; the
discriminator remains the absence of a dyld-internal fault at an
out-of-window VA (F1c §6).

## 2. The TLS-deref class (full audit of the D1 binary)

Sweep of every `mrs …, TPIDRRO_EL0` in the linked v13 dyld:

| symbol | TLS use | pre-handoff reachability on cacheless boot | disposition |
|---|---|---|---|
| `___error` | slot 1 (`[tp+8]`) | consumers kdebug_trace.o/libproc.o/os_lock.o — reachable | **FIXED** (errno member) |
| `_cerror_nocancel` | slot 1 | every BSD-stub error path — THE WALL | **FIXED** (errno member) |
| `_os_unfair_lock_lock`/`_unlock`/`_assert_owner`/`_assert_not_owner` | slot 3 (`[tp+0x18]`, `__TSD_MACH_THREAD_SELF`) in the FAST path | `RuntimeLocks` + loader locks lock during ProcessConfig/prepare/loading — **guaranteed next wall** (fault VA 0x18) | **FIXED** (lock member) |
| `_mig_get_reply_port`/`_mig_dealloc_reply_port` | slot 2 (`[tp+0x10]`, `__TSD_MIG_REPLY`) | consumers = `_task_info` (only caller: `SyscallDelegate::kernelDyldImageInfoAddress`, called only from start()'s IN-CACHE branch, skipped when not in cache) + `_kernelrpc_mach_port_*`/`_kernelrpc_mach_vm_*` MIG fallbacks (D1's `_vm_allocate` uses the raw trap first; MIG only on MACH_SEND_INVALID_DEST fallback) | dormant — pre-registered, unfixed |
| `_mach_msg_overwrite` | slot 123 (`[tp+0x3d8]`, `__TSD_MACH_MSG_AUX`) | gated by `options & 0x802` aux path — MIG-only | dormant — pre-registered, unfixed |

## 3. The fix — two support-library members, ZERO dyld source edits

All artifacts in `research/laguna-usb-fix/patches-dyld-errno/` (README there
has the full reproduce):

1. **`errno_dyld.c` → replaces archive member `errno.o`** (xnu libsyscall
   custom/errno.c). Static errno (tentative definition, exactly like Apple's
   file), `__error()` returns `&errno`, `cerror`/`cerror_nocancel` keep the
   exact Apple ABI (`cerror_return_t` = unsigned __int128 → x0:x1 = −1:−1),
   `cerror` still calls the D1 no-op `_pthread_exit_if_canceled`. Semantically
   exact: dyld's boot is single-threaded until libSystem initializers run, and
   in this fleet dyld hands off before any second thread exists.
2. **libplatform-349.140.6 `src/os/lock.c` rebuilt with
   `-DF1D_BOOT_NO_TLS`** (`0001-lockc-f1d-boot-no-tls.patch`, 13 inserted
   lines, flag-gated) → replaces archive member `os_lock.o`:
   `_os_lock_owner_get_self()` returns constant owner 1 instead of the TSD
   read. Lock/unlock stay symmetric (single thread); post-handoff no other
   thread ever locks dyld's own locks in this boot. Disassembly check: new
   `_os_unfair_lock_lock` = `mov w8, #1; ldaxr/stxr` fast path (no TLS), and
   the contention path passes the constant owner through an outlined thunk
   (`mov x2,#0; mov w3,#1; b __os_unfair_lock_lock_slow`) — ABI preserved.
   Without this, the FIRST unfair-lock lock during prepare() would have
   faulted at VA 0x18 one wall later.
3. **`shims-f1d/pthread/tsd_private.h`** — minimal additive shim so
   `os/lock_private.h`'s `OS_UNFAIR_LOCK_INLINE` section parses (the real
   Apple header is closed; D1's ephemeral copy no longer exists on disk).
   Nothing in the dyld link uses the inline helpers.
4. Rebuild = D1's exact xcodebuild recipe (`DYLD-BUILD.md` §4), SYMROOT and
   support archive redirected to lane copies; dyld source tree untouched
   (READ-ONLY use of D1's clone + shims). `codesign --force --sign -` (adhoc,
   parity). Build log: `/tmp/fleet/f1d/build-final.log` (RC=0,
   ** BUILD SUCCEEDED **).

D1's lane files untouched; `mapSegments` (F1c patch) verified present in the
new binary at IDENTICAL addresses (symbol @0x12950, FIXED-first
`mov w3,#0x0` vm_allocate @0x12bd8, ANYWHERE fallback @0x12c14) — the rebuild
changed nothing in dyld's own codegen for that function.

## 4. Validation (all host-side, nothing fired)

| gate | result |
|---|---|
| header parity (`otool -hv`) | MH_MAGIC_64 ARM64 ALL **DYLINKER** NOUNDEFS DYLDLINK TWOLEVEL, LC_BUILD_VERSION platform 2 minos 16.5 sdk 27.1, LC_UNIXTHREAD — identical classes to D1's dyld |
| size | 882,800 B (D1: 882,688; +112) |
| `PROJECT:dyld-1066.8` string + both preferred-address patch strings | present |
| `nm` symbol count | 2,957 (D1: 2,955; +2 local cold-section splits from the rebuilt lock.o) |
| `_cerror_nocancel` disasm | `errno = err` static store + `mov x0/x1,#-1; ret` — **no TPIDRRO deref** |
| `___error` disasm | `adrp/add/ret` = return `&errno` (static at 0xac1f0) |
| `_os_unfair_lock_lock` disasm | constant owner `mov w8,#1` CAS fast path — **no TPIDRRO deref** |
| TPIDRRO sweep of the whole binary | exactly the 3 dormant documented sites remain (`_mig_get_reply_port`, `_mig_dealloc_reply_port`, `_mach_msg_overwrite`) |
| F1c mapSegments patch | present at identical offsets (see §3) |
| `__TEXT` vmaddr | 0x0 (base-derivation math for the next boot unchanged) |
| codesign | adhoc, `codesign -v` PASS |
| v13 pin before AND after | `8559c64349e83596…` unchanged |
| v12 pin before AND after | `45f3367196e0cbae…` unchanged |
| fsck_hfs -fn on v14 | `The volume iOSRoot appears to be OK` |
| volume diff v14 vs v13 | 199 files, same set, **exactly 1 differs** = `usr/lib/dyld` (mode 0755, BSDInfo carried by cp) |
| mounted v14 `/usr/lib/dyld` | byte-identical to the registered F1d dyld |
| F1c `build_v12.py verify` (dylib content unchanged) | PASS: predicate 36/36, GOT 3,944/3,944, census 537, tries 10,901, 16,606/16,606 pointers, symtab 21,048, disjointness 0/0, `__dyld4` pair |
| **NEW gate** (`f1d_null_gate.py`, mission item 4) | **PASS — zero NULL-bound slots among the previously-subcache-bound set**: F1b census `cache-other` ≥0x217040000 clean slots = 704; 575 in data sections → 504 re-targeted in-window non-NULL, 71 non-NULL out-of-window = documented F1c §5 non-pointer data residual class (bignum limbs/tagged literals, referents don't exist cacheless), **0 NULL**; 129 `__TEXT` slots byte-faithful vs v11; `__got`/`__la_resolver` old-cache-range residuals = **0**; libdyld `__dyld4` +0x38/+0x40 = 0x3003870e8/0x3003858d0 exact |

Gate-tooling notes (for the next lane): plain `__got` FILE values of 0 are
LEGAL in dsc splits — dyld binds every extern-entry slot at runtime via the
indirect symbol table (F1c gate [2] proves all 3,944 resolve), and F1c's
residual scan skips 0x6/0x7-flagged sections by design. The meaningful
invariant is: no slot in the bound sections carries an OLD-cache-range value
([0x217040000, 0x22ADE4000)) — measured **0** — and no previously-subcache
census slot is NULL — measured **0**.

## 5. Wall-#7 prediction (what root-v14 should show, and what comes next)

Prerequisite for any progress: root-v14 (or any image with the TLS-free
errno/lock dyld). v13's dyld cannot get past the shared-cache probe — every
subsequent wall below was unreachable in v65b.

Expected v65c (same mach + root-v14) console, in order:
1. The SAME `shared_region … check_np … failed` + `systrace … = 12` lines
   (kernel side unchanged) — but NO usertrap; dyld takes the no-cache path.
2. NO dyld log lines visible (fd 2 still EBADF — expected silence, not failure).
3. `prepare()` runs: first real exercise of (a) F1c's preferred-load patch
   (36 dylibs map at 0x300000000+ slide 0 — invisible in console, but the v12
   pointer space becomes live), (b) the NXArgcPtr wire-up that killed v64b,
   (c) the first `os_unfair_lock_lock` calls (now constant-owner).
4. Next walls, in likely order:
   - **W7a — bind/fixup-time signal in dyld's runtime window** (~0x100B54000
     + ≤0x64000): any new dyld-internal pc symbolicates against the F1d dyld
     (same `nm` + base-derivation recipe; the dyld's symtab is in the
     registered artifact). Discriminator vs F1c's wall-#5 prediction: the
     pc window is the same, but now the fault VA should sit inside the v12
     dylib windows (0x300000000+) if preferred loading worked, vs a
     kernel-chosen address if it fell back (the "mapping … unslided /
     preferred address unavailable" fallback lines are logged but invisible —
     EBADF; only the fault-VA window discriminates).
   - **W7b — F1c wall-#5 (no-initializer gap)**: launchd main on
     unconstructed libSystem/dispatch/objc — first fault inside launchd
     (0x100000000..0x10008C000) or a v12 dylib window. Unchanged by this fix.
   - **W7c — dormant TLS sites** (pre-registered, same class): fault VA 0x10
     with pc in `_mig_get_reply_port`+0x18 (trigger: any MIG fallback — e.g.
     a future `_kernelrpc_mach_port_*` path) or fault VA 0x0/aux-related in
     `_mach_msg_overwrite`. If either fires, fix = same support-lib pattern
     (static reply-port/aux handling), NOT a data fix.
   - **W7d — "boot dyld did not take the patch" discriminator**: a
     dyld-internal fault at an out-of-window VA again = the errno/lock fix
     somehow not resident (check the embedded root sha; v14 = 12811844debbd44e…).

## 6. Files + reproduce

```
/tmp/fleet/f1d/dyld.blazer-f1d-1066.8      sha e0e00473d6b021836bed1b513a53241b87784b1caa6d30054d93e7d072776d5d
/tmp/fleet/f1d/root-v14-candidate.hfs      sha 12811844debbd44e49bb39dfe40860c6c59f69fbfd066c2db30f1f9fa4b3f82d
/tmp/fleet/f1d/src/errno_dyld.c            (durable twin: patches-dyld-errno/errno_dyld.c)
/tmp/fleet/f1d/src/lock_f1d.c              (patched lock.c; twin: patches-dyld-errno/0001-*.patch)
/tmp/fleet/f1d/shims-f1d/pthread/tsd_private.h
/tmp/fleet/f1d/f1d-rebuild-dyld.sh         (archive swap + D1 xcodebuild recipe + sign)
/tmp/fleet/f1d/f1d-build-v14.sh            (v13 pin → v14, all gates)
/tmp/fleet/f1d/f1d_null_gate.py            (mission-item-4 gate)
/tmp/fleet/f1d/build-final.log             (xcodebuild RC=0)
research/laguna-usb-fix/patches-dyld-errno/{README.md,0001-lockc-f1d-boot-no-tls.patch,errno_dyld.c,shims-f1d/,…}
```

Evidence: `research/laguna-usb-fix/qemu-validation/evidence/runC-late-v65b-nvram/`
(mirror-ring-strings.txt, reader.txt, uart.log). Disassembly + symbolication:
`/tmp/fleet/f1d/{dyld-syms.txt,dyld-disasm.txt,dyld-syms-new.txt,dyld-disasm-new.txt}`.

Builder pin ask (payload lane): `WITH_ROOT14` from sha
`12811844debbd44e…` — fires only together with the F1d dyld (it IS the image's
`/usr/lib/dyld`; v14 ships both in one image, same as D1's v13 pairing rule).
Firing = coordinator only (device or QEMU F2 lane).

---

5-line summary:
1. v65b's NULL+8 wall symbolicated byte-exactly: dyld base 0x100B54000 (unique
   16K candidate consistent with start's extent + the x30=0 entry frame), pc =
   D1-dyld `_cerror_nocancel`+0x10 — xnu libsyscall errno.c's TSD read
   (`_os_tsd_get_direct(__TSD_ERRNO)`, slot 1) on a boot thread whose
   tpidrro_el0 is 0; trigger = the expected cacheless-boot
   `shared_region_check_np` = 12 in start() (dyldMain.cpp:1008 →
   DyldDelegates.cpp:270) via D1's stub error path; same call survived on the
   old dyld in v61-v64b (A/B) — Apple's dyld-variant errno is TLS-free, D1's
   build is not.
2. Frontier correction: v65b died BEFORE prepare() — the F1c preferred-load
   path and NXArgcPtr wire-up were never exercised in v65b; "cleared
   prepare()+0x9c4" is not established. Also: dyld's preferred-address log
   lines are invisible on this boot (state.log → write(2) → EBADF), so
   preferred-load success remains console-unprovable; only fault-VA windows
   discriminate.
3. Fix (support-library only, zero dyld source edits): errno.o member replaced
   with a TLS-free static-errno errno_dyld.c (exact Apple cerror ABI) +
   libplatform lock.c rebuilt with -DF1D_BOOT_NO_TLS (constant owner 1 — kills
   the guaranteed next wall: os_unfair_lock_lock's [tp+0x18] fast-path deref
   during prepare()) + one minimal additive pthread/tsd_private.h shim; D1
   recipe rerun verbatim; mapSegments patch verified present at identical
   offsets.
4. root-v14 = pinned v13 + dyld.blazer-f1d-1066.8 (sha e0e00473d6b02183…,
   882,800 B, adhoc, header parity): fsck OK, exactly-1-file delta (usr/lib/
   dyld), F1c verify PASS unchanged, NEW gate PASS — 0 NULL among the 704
   previously-subcache-bound census slots (504 re-targeted in-window, 71
   documented non-pointer data), 0 old-cache-range values in
   __got/__la_resolver, __dyld4 pair exact; v13/v12/v11 pins re-hashed
   unchanged; candidate sha 12811844debbd44e….
5. Wall-#7 pre-registered: v65c should show the same kernel shared_region
   lines with NO usertrap, then reach prepare() (first live exercise of
   preferred loading); next walls = dyld-internal fault in the runtime window
   (symbolicate against the registered F1d dyld, base recipe in §1), F1c's
   no-initializer wall-#5, or the dormant TLS sites (fault VA 0x10 in
   _mig_get_reply_port = same class, same fix pattern). Asks: payload lane
   WITH_ROOT14 pin from 12811844debbd44e…; F2 QEMU A/B v13 vs v14; coordinator
   fires.
