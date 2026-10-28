# DYLD-BUILD.md — patched dyld-1066.8 arm64 build + root-v13 (lane D1, 2026-10-27)

Status: **BUILT + SIGNED + VALIDATED; root-v13 candidate assembled, not fired**
(coordinator owns the device). Deliverables registered under `/tmp/fleet/d1/`,
durable twins under `research/laguna-usb-fix/{patches-dyld-build,d1-build}/`.

## 1. Artifacts

| artifact | sha256 (first 16) | size |
|---|---|---|
| `/tmp/fleet/d1/dyld.blazer-d1-1066.8` | `8e198afc13c73d43` | 882,688 B |
| `/tmp/full sha` | `8e198afc13c73d43352e6ae25588e315c22b6a92d8128906c55538be361ff988` | — |
| `/tmp/fleet/d1/root-v13-candidate.hfs` | `8559c64349e83596` | 29,360,128 B |
| full | `8559c64349e835966907fdb9ad75bf7b88525a25f62a7cd078282ca468303bd9` | — |

Compatibility reference (v12's `/usr/lib/dyld`, the extracted 20H392 cache dyld):
ARM64 / MH_DYLINKER / flags NOUNDEFS DYLDLINK TWOLEVEL / LC_BUILD_VERSION
platform 2 (iOS) minos 16.5 / LC_UNIXTHREAD / 16 K segments / adhoc
CodeDirectory (`com.apple.darwin.ignition`, 922,912 B).

## 2. Built binary parity check (all PASS)

- `otool -hv`: MH_MAGIC_64 ARM64 ALL **DYLINKER** NOUNDEFS DYLDLINK TWOLEVEL — identical flags to the old dyld.
- LC_BUILD_VERSION: platform 2, **minos 16.5** (`IPHONEOS_DEPLOYMENT_TARGET=16.5`), sdk 27.1.
- LC_UNIXTHREAD entry (dylinker convention, not LC_MAIN) ✓.
- Segments 16 K aligned (`__TEXT` 0x0, `__DATA_CONST` 0x64000, `__DATA` 0x68000, `__DATA_DIRTY` 0xac000, `__LINKEDIT` 0xb0000).
- `codesign --force --sign -` applied: CodeDirectory v=20400 flags=0x2 **adhoc** — same adhoc state as the extracted dyld (`codesign -v` PASS). Unsigned builds were also produced first; the signature was added for kernel-acceptance parity.
- `strings`: `@(#)PROGRAM:dyld  PROJECT:dyld-1066.8` (RC_ProjectSourceVersion=1066.8) ✓.
- nm symbol table present, 2,936+ symbols (UNSTRIPPED_PRODUCT=NO + debug map → sane).
- **F1c patch present in the linked binary** (mission 1d): `mapSegments`
  (`__ZN5dyld46Loader11mapSegmentsER11Diagnostics...` @0x12950) disassembles as
  `mov w3, #0x0` (= VM_FLAGS_FIXED) before `bl _vm_allocate` @0x12bd8, the loud
  fallback log `"preferred address 0x%0llX unavailable … falling back to
  anywhere"` via RuntimeState::log, then the stock `mov w3, #0x1` (ANYWHERE)
  call @0x12c14. The patch's two log strings are embedded (and referenced).

## 3. Source + patch chain

1. Source: `/tmp/r2-dyld-1066` verified genuine **dyld-1066.8** (git `ce1cc208…`,
   `VERSION` = `dyld-1066.8`); `/tmp/r2-dyld-src` is dyld-1378 (NOT used). Fresh
   working clone at `/tmp/fleet/d1/dyld-src` (shared tree untouched).
2. `research/laguna-usb-fix/patches-dyld-preferred/0001-mapSegments-preferred-load.patch`
   (F1c's; git apply PASS on ce1cc20) — mapSegments tries VM_FLAGS_FIXED at the
   image's preferred load address first (loud `state.log` lines), falls back to
   stock ANYWHERE; JustInTimeLoader passes `ma->preferredLoadAddress()`,
   PrebuiltLoader passes 0.
3. **`patches-dyld-build/0002-loaderh-preferred-load-decl.patch` (D1, REQUIRED)** —
   F1c's 0001 updates the three `.cpp` call sites but NOT the `Loader.h`
   declaration: the dyld target does not compile as shipped (`PrebuiltLoader.cpp:
   315: too many arguments … expected 10, have 11`). 0002 adds the
   `uint64_t preferredLoadAddr` parameter to the `Loader.h` mapSegments
   declaration. **This is a dyld source edit beyond 0001 — flagged explicitly
   per lane rules; it completes 0001 rather than changing its semantics.**
4. `patches-dyld-build/0003-prebuilt-loader-hash-shim-include.patch` (D1,
   build-system) — `build-scripts/prebuilt-loader-hash.sh` compiles its probe
   with a hard-coded `-I` list that lacks the public-SDK shim headers;
   the script now honors `$HEADER_SEARCH_PATHS` (exported by xcodebuild to
   script phases).

## 4. Build command (worked end-to-end)

```sh
cd /tmp/fleet/d1/dyld-src   # fresh clone of /tmp/r2-dyld-1066 @ ce1cc20 + patches above
xcodebuild -project dyld.xcodeproj -target dyld -configuration Release \
  -sdk iphoneos ARCHS=arm64 ONLY_ACTIVE_ARCH=NO \
  RC_ProjectSourceVersion=1066.8 IPHONEOS_DEPLOYMENT_TARGET=16.5 \
  CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY="" \
  HEADER_SEARCH_PATHS="/tmp/fleet/d1/shims /tmp/fleet/d1/dyld-src/include" \
  OTHER_LDFLAGS='$(inherited) /tmp/fleet/d1/libs/libdyld-support.a <libclang_rt.ios.a>' \
  SYMROOT=/tmp/fleet/d1/build
codesign --force --sign - /tmp/fleet/d1/build/Release-iphoneos/dyld
```

## 5. The public-SDK wall (why this is hard) and the D1 shim set

The `dyld` target links `-nostdlib` against AppleInternal-only static archives
(`${SDKROOT}/usr/local/lib/dyld/*.a` — libsystem_kernel/c/platform statics built
for dyld) and includes internal-SDK headers. iPhoneOS 27.1 (public) ships NO
corecrypto headers, no libproc.h/dyld.h/lock_private.h/`usr/local/lib/dyld/*.a`,
a stub `mach/mach_vm.h` that `#error`s, and has REMOVED the `bridgeos` and
`SPI_AVAILABLE` availability categories — so the OSS dyld-1066.8 project does
not build against it as published (several OSS headers also reference
`bridgeos(...)`).

D1 provides `research/laguna-usb-fix/dyld-build/shims/` (24 headers + 4 C files,
all additive — no dyld source file touched besides the two patches above):

- Header shims sourced from the **booted kernel tree** (xnu-8796.121.2, exact
  version provenance): `mach/shared_region.h`, `sys/kdebug_private.h`,
  `sys/reason.h`→`System/sys/reason.h`, `sys/csr.h`, `sys/codesign.h`,
  `sys/proc_info.h`, `sys/event.h` (kevent_qos_s), `sys/kdebug.h`,
  `sys/ulock.h` (ULF_DEADLINE added, value = OS_UNFAIR_LOCK_DEADLINE; the
  deadline ulock path is never exercised by dyld), `sys/dtrace.h`/`sys/
  dtrace_glue.h` (mac SDK), `sys/vnode.h`/`libproc.h` (mac SDK),
  `kern/kcdata.h`, `kern/cs_blobs.h`, `net/route.h`,
  `System/machine/cpu_capabilities.h` (arm commpage offsets; `#ifdef PRIVATE`
  wrapper stripped, exactly like Apple's internal-SDK userspace copy),
  `System/sys/content_protection.h` (same treatment), `corecrypto/*` (39
  headers).
- `os/lock_private.h` + `os/atomic_private.h` (+ `os/atomic.h`,
  `atomic_private_{impl,arch}.h`, `base_private.h`) from
  **libplatform-349.140.6** (OSS tag era-matched to dyld-1066.8; the 359 tag
  uses SPI_AVAILABLE that the new SDK dropped).
- Hand-written minimal headers: `CrashReporterClient.h` (no-op
  CRSetCrashLogMessage macros — the in-tree `#else` branch of
  `common/Defines.h` already uses identical no-ops for other flavors),
  `_simple.h` (libsimple subset), `libamfi.h` (enums transcribed from dyld's
  own `#else` fallback definitions, byte-for-byte values),
  `libc_private.h` (`__error` + `abort_report_np` decls + `<fcntl.h>` chain-in),
  `libproc_internal.h`, `vproc_priv.h` (guard-only; no identifiers consumed),
  `sandbox/private.h` (public-ABI filter values), `subsystem.h`,
  `openbyid_np.h`, `System/atexit.h`, `System/sys/commpage.h`,
  `dyld/VersionMap.h` (**empty sVersionMap table** — Apple-internal data; the
  only consumers are the external `dyld_program_*_at_least` API surface and
  `findVersionSetEquivalent`, no internal dyld-1066.8 code path calls them),
  `os/tp.h` (TPRO stubs), shadow `os/lock.h` (SDK copy + tp.h),
  `libkern/OSAtomic.h` (SDK copy + `__cxa_range_t`, guarded), `mach/message.h`
  (SDK copy + `mach_msg_option64_t` MACH64 enum, `mach_msg_vector_t`,
  `mach_msg_aux_header_t`, `mach_msg2_trap` decl — all verbatim from xnu
  osfmk/mach/message.h PRIVATE block), `mach/mach_vm.h` (mac SDK copy — the
  iOS SDK one `#error`s), `mach/{vm_map_internal,mach_port_internal,
  task_internal,mach_vm_internal}.h` (empty-or-alias stubs; the mig fallback
  decls mach_vm.c/mach_port.c need), `System/sys/fsgetpath.h` (mac SDK copy +
  `openbyid_np` decl), `dyld/VersionMap.h` as above.
- **`syscalls_impl.S`** — 56 raw stubs (arm64 `x16 = num; svc #0x80`; BSD
  errors branch to `_cerror` from xnu libsyscall `custom/errno.c` — the
  identical ABI to Apple's generated stubs). Every number verified against the
  booted kernel's own tables: BSD numbers from `xnu-8796.121.2
  bsd/kern/syscalls.master` (incl. NO_SYSCALL_STUB entries: shared_region 294/
  536, abort_with_payload 521, map_with_linking_np 550, proc_info_extended_id
  545), mach traps from `osfmk/mach/syscall_sw.h` kernel_trap rows
  (task_self_trap -28 … mach_msg2_trap -47), SWI 0x80 from
  `osfmk/mach/arm/vm_param.h`. Plus the C++-mangled
  `__Z21__map_with_linking_npPK10mwl_regionjPK12mwl_info_hdrj` alias Loader.cpp
  needs (dyld declares it with C++ linkage).
- **`simple_impl.c`** — malloc-free libsimple subset (`_simple_getenv`,
  `_simple_vdprintf/_simple_dprintf`, `_simple_salloc/_simple_vsprintf/
  _simple_sprintf/_simple_sresize/_simple_sfree/_simple_string`): full printf
  engine (dyld format-string audit: %s %d %u %p %c %o %x %X, l/ll/z, widths,
  precision, #/space flags — no floats/wide/%m), mach-vm-backed string builder
  with stable handles. This is dyld's `state.log` diagnostic channel — the
  patch's "mapping … unslided" lines flow through it.
- **`support_impl.c`** — `open_with_subsystem/stat_with_subsystem/subsystem_root_path`
  (raw `fstatat`/`open_dprotected_np` — deliberately NOT through glue.c's
  wrappers: dyld3::stat → stat_with_subsystem → stat → gSyscallHelpers would
  recurse), `sandbox_check` family (0 = unsandboxed: the true answer on the
  boot), `amfi_check_dyld_policy_self` (fully permissive flag set — deliberate
  D1 decision so DYLD_PRINT_*/DYLD_* env handling works on the research boot),
  `amfi_load_trust_cache` (-1), TPRO no-ops (boot never sets `dyld_hw_tpro`),
  `gCRAnnotations` (crash-info section data), `proc_set_dyld_all_image_info`
  over `__proc_info` (PROC_INFO_CALL_SET_DYLD_IMAGES = 0xf, xnu-8796
  proc_info.h:1068).
- **`libc_primitives.c`** — mem/str primitives (plain C; Apple's arm64 asm
  versions are not public — semantically identical, slower; dyld's boot is
  syscall/mapping-dominated), strlcpy/strlcat/strnlen (OpenBSD reference),
  `arc4random` (getentropy-seeded xorshift; consumer = stack-guard cookie),
  `wmemchr`, `mach_task_self_`/`vm_page_size`/`mach_init`, `_subsystem_init`
  (no-op: boot volume IS the subsystem root), `mach_absolute_time` (C port of
  the commpage timebase wrapper incl. wake-retry + kernel-trap fallback),
  `mach_msg2` (→ `mach_msg2_internal` in libsyscall mach_msg.c), `NDR_record`
  (xnu osfmk/mach/arm/ndr_def.h values), `malloc/free/calloc/realloc`
  (256 KiB vm-backed bump pool — dyld's malloc paths (DebuggerSupport
  addImagesToAllImages, dyldMain) never free), `_pthread_exit_if_canceled`
  (no cancellation state on the boot thread), `__libcpp_verbose_abort` +
  `_ZNSt3__122__libcpp_verbose_abortEPKcz` (SDK libc++ 27 mangled name, routes
  to dyld halt).
- **`corecrypto_sha1_384.c`** — `ccsha1_di`/`ccsha384_di` (+ the
  `ccsha256_vng_arm_di` struct the userspace build's `ccsha256_di.c` wants):
  standard FIPS 180-4 algorithms behind the exact corecrypto `ccdigest_info`
  API; bit-identical outputs to Apple's (algorithms are standardized); scalar
  C compress instead of vng asm. SHA-256 machinery compiles verbatim from
  xnu `osfmk/corecrypto`.
- `libplatform-349.140.6/src/os/lock.c` (os_unfair_lock family) and
  `_libc_weak_funcptr.c` (voucher weak funcptrs) compiled from OSS sources.
- mig-generated user stubs from the booted kernel's own defs
  (`task.defs` → task_info, `mach_port.defs` (minus the special-reply-port
  reset-link routine whose reply types the public SDK lacks) → the
  `_kernelrpc_mach_port_*` mig fallbacks, `mach_vm.defs`, `vm_map.defs`).

## 6. Link-time resolution (final undefined → provider map)

`libdyld-support.a` (31 members, ar rcs — **must use an indexed archive**;
`ar qc` silently produces an unusable archive). Last-link undefined set and
providers: libsystem_kernel stubs → syscalls_impl.S; _simple_* → simple_impl.c;
subsystem/sandbox/amfi/TPRO → support_impl.c; NDR_record/mach_absolute_time/
mach_msg2/malloc/NDR → libc_primitives.c; cc* → osfmk corecrypto + D1
sha1/384; os_unfair_lock* → libplatform lock.c; voucher funcptrs →
_libc_weak_funcptr.c; mig fallbacks → task/mach_port/mach_vm/vm_map user
stubs; compiler-rt (`libclang_rt.ios.a`) for `___chkstk_darwin`.

## 7. root-v13 (mission 2)

`research/laguna-usb-fix/dyld-build/d1-build-v13.sh` (twin of f1c's pipeline):
pinned root-v12 candidate → scratch copy → install D1 dyld at `/usr/lib/dyld`
(0755) → fsck → gates → sha. Gates (all PASS):

| gate | result |
|---|---|
| pinned root-v12 sha before AND after | `45f3367196e0cbae…` unchanged |
| dyld input sha | `8e198afc13c73d43…` (registered artifact, byte-identical into image) |
| fsck_hfs -fn | `The volume iOSRoot appears to be OK` |
| volume diff vs root-v11 | 162 identical, **exactly 37 differ** = 36 v12 dylibs + `/usr/lib/dyld` (census updated per mission) |
| 36 v12 dylibs byte-identical in v13 | 36/36 |
| `/usr/lib/dyld` == registered D1 dyld | byte-identical, mode `0o100755`, BSDInfo bytes == v12's |
| F1c `build_v12.py verify` on v13-mounted dylibs | PASS: predicate 36/36 clean, GOT 3,944/3,944, launchd census 536(+1 weak)=537, tries name-identity, 16,606/16,606 pointers in range, symtab 21,048, disjointness 0/0, `__dyld4` standalone pair 0x3003870e8/0x3003858d0 |

## 8. Open risks

1. **Unsigned vs adhoc**: shipped adhoc-signed (parity with the extracted
   dyld). If the kernel rejects even adhoc dylinkers, the AVB shim chain
   (which accepts our rootfs wholesale today) is unchanged, so risk is low.
2. **minos 16.5 / sdk 27.1**: dyld does not gate on its own build version;
   availability checks inside dyld use the MAIN executable's versions.
3. **libc primitive substitutions** (plain-C mem/str, bump-pool malloc,
   getentropy arc4random, no-op TPRO/subsystem): behavior-identical for dyld's
   call sites; performance slightly lower; documented above. If wall-#5
   surfaces a fault inside one of these primitives, symbolize against the D1
   binary (unstripped, symtab present).
4. **mach_msg2/voucher simplifications**: mach_msg2 passes straight through to
   mach_msg2_internal (the scalar path dyld uses); voucher set/adopt/revert
   return KERN_NOT_SUPPORTED (no vouchers on the boot) — if wall-#5 shows a
   MACH_SEND_INVALID_DEST/Voucher fault, revisit.
5. **build_v12.py verify NOTE**: "pointer count 16606 != F1b's 11757
   (geometry same; investigate)" — pre-existing F1c note (11,757 F1b-consistent
   + 4,849 carve-mangled fixes), not a D1 regression.

## 9. Reproduce

```sh
# dyld: fresh clone /tmp/r2-dyld-1066 @ ce1cc20 → git apply
#   patches-dyld-preferred/0001 → patches-dyld-build/0002 → patches-dyld-build/0003
#   then the xcodebuild in §4 with shims from dyld-build/shims/ (compile the
#   4 shim .c files + libplatform lock.c + _libc_weak_funcptr.c + mig products
#   + libsyscall C files into libdyld-support.a; full file list in §5-6)
# root-v13: research/laguna-usb-fix/dyld-build/d1-build-v13.sh
```

---
5-line summary:
1. Patched dyld-1066.8 (F1c 0001 mapSegments FIXED-at-preferred) BUILT for
   arm64-ios from a verified ce1cc20 clone: DYLINKER header parity with the old
   dyld, minos 16.5, 16K segments, adhoc-signed, 882,688 B, patch strings and
   the `mov w3,#0x0` FIXED-first vm_allocate call verified in mapSegments
   disassembly; sha 8e198afc13c73d43….
2. Wall crossed: public iPhoneOS 27.1 SDK lacks the AppleInternal dyld archives,
   headers, mach_vm.h (stubs #error), and the bridgeos/SPI availability
   categories — D1 shipped 24 shim headers (xnu-8796-versioned sources) +
   4 support C files + syscalls_impl.S (56 raw stubs, numbers verified against
   the booted kernel's syscalls.master/syscall_sw.h) + libplatform 349.140.6
   lock.c + corecrypto from osfmk (SHA-1/384 di implemented FIPS-exact).
3. F1c's 0001 patch is INCOMPLETE as shipped — the Loader.h mapSegments
   declaration lacks preferredLoadAddr (compile fails); D1 ships
   patches-dyld-build/0002 (source edit, flagged) + 0003 (hash-script
   HEADER_SEARCH_PATHS fix).
4. root-v13 = pinned root-v12 + D1 dyld: fsck OK, exactly-37-file delta vs v11
   (36 dylibs + usr/lib/dyld), 36/36 dylibs byte-identical, dyld byte-identical
   to the registered artifact, BSDInfo preserved, F1c build_v12.py verify PASS
   (predicate 36/36, GOT 3,944, census 537, 16,606 pointers, disjointness 0/0);
   sha 8559c64349e83596…, v12 pin untouched.
5. Nothing fired (coordinator owns the device); ask: F2 QEMU A/B v12+stock vs
   v13+patched (wall-#5 discriminator = no dyld-internal fault + the
   "mapping … unslided at preferred address" log line), coordinator WITH_ROOT13
   pin decision from sha 8559c64349e835966907fdb9ad75bf7b88525a25f62a7cd078282ca468303bd9.
