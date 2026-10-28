# POINTER-REBASE.md — wall #3 "stale cache-addressed pointers" → root-v11

Lane: F1b (2026-10-27). Predecessor: F1 (`INDIRECT-FIX.md`, root-v10).
Status: **root-v11 candidate BUILT + VALIDATED, not fired** (coordinator owns the device).

Candidate: `/tmp/fleet/f1b/root-v11-candidate.hfs` — 29,360,128 B,
sha256 `7106626ecf6bb16c3691686188a9340f9d33e5a87c7eda7c10151bb8d9a7061d`.
Pinned root-v10.hfs (`3af78bcb87800967e5e5e21db6a3a84c97bf18da4e68933f0ffbdda4d3b481e6`)
verified unchanged before AND after every build.

## 1. The crash, byte-identified (v63, 20261027-191323, root-v10)

```
usertrap: launchd[1] exception 1 code 0x1 0x201e1d34628: pc 0x100e0e058 lr 0x100e0e020
          sp 0x16f2eb280, shared cache slide 0x0
usertrap:   called from 0x100e0c2e0
usertrap:   called from 0x0
pid 1 exited -- exit reason namespace 2 subcode 0xb, description none
```

Same failure on QEMU (pc 0x10087e058 — same pc−lr = 0x38, frame = pc−0x1D78; different
absolute base = different boot-dyld runtime layout, same defect).

**Faulting VA `0x201e1d34628` is bit-identical to an un-applied dyld chained-fixup
REBASE entry** (`dyld_cache_format.h dyld_chained_ptr_64_rebase`):

| field | bits | value |
|---|---|---|
| target:36 | 0..35 | `0x1E1D34628` (old-cache VA) |
| high8:4 | 36..39 | 0 |
| **next:12** | 40..51 | **2** ← the `+0x20000000000` observed as bit 41 |
| reserved+bind | 52..63 | 0 |

Location: `libdyld.dylib` `__DATA_DIRTY,__dyld4` slot +0x18 (file off 0x2c020 in
root-v10's copy; section VA `0x1e1d34020`). The value is 8 bytes of raw cache chain
encoding that the dsc-split pipeline left UN-APPLIED in the data sections. Our boot
has no slide info in split dylibs, so nothing ever decodes it; the first consumer
dereferences the encoded word and faults.

**Consumer pinned (dyld-1042.1 ref tree, `dyld/dyldMain.cpp:724-757`, "wire up
libdyld.dylib to dyld"):** dyld locates libdyld's `__dyld4` section
(`findSectionContent("__DATA", "__dyld4")`), sets `state.vars =
&libdyld4Section->defaultVars`, then writes through the section's pre-existing
pointers: `*state.vars->NXArgcPtr = argc` (+ NXArgv/environ/progname). Section slot
map (LibdyldDyld4Section, 0x48 B = exactly the section size):

| slot | field | root-v10 value | root-v11 value (patched) |
|---|---|---|---|
| +0x00 | apis | 0 | 0 (dyld fills at boot) |
| +0x08 | allImageInfos | 0 | 0 (dyld fills) |
| +0x10 | defaultVars.mh | 0 | 0 (dyld fills) |
| **+0x18** | **defaultVars.NXArgcPtr** | **0x201e1d34628 (chain)** | **0x1e1d34068** |
| +0x20 | NXArgvPtr | 0x201e1d34610 (chain) | 0x1e1d34050 |
| +0x28 | environPtr | 0x201e1d34620 (chain) | 0x1e1d34060 |
| +0x30 | __prognamePtr | 0x201e1d34618 (chain) | 0x1e1d34058 |
| +0x38 | dyldLookupFuncAddr | 0x226c830e8 (stale, dyld-data subcache — RESIDUAL, see §6) | unchanged |
| +0x40 | (tail) | 0x226c818d0 (stale — RESIDUAL) | unchanged |

Targets 0x1e1d34628/610/620/618 are libdyld's own OLD-layout `__DATA_DIRTY,__common`
slots (`_NXArgc`, `_NXArgv`, `_environ`, `_NXProgname`); libdyld `__DATA_DIRTY`
moved Δvm = −0x5c0 in the carve, so the patched values point at the true new
addresses. pc/lr (0x100e0e058/0x100e0e020) are inside the boot dyld's runtime
mapping (root-v10 ships a 922 KB custom dyld, lsl/EphemeralAllocator symbols; its
exact runtime layout is not pinned — pc ownership is inferred, the faulting VA is
byte-proven).

## 2. Census (all 36 closure dylibs, root-v10 layout)

Tool: `/tmp/fleet/f1b/rebase_ptrs.py` (survey/census/patch/verify). OLD layout =
raw dsc splits (`research/laguna-deviceroot/work/dylibs[/dylibs-stripped-closure]`),
byte-verified == 20H392 cache segment layout for all real segments (only the
deduped `__LINKEDIT` vmsize differs per-image — cache links one shared region
0x217040000..0x22ADE4000; excluded from classification, Δvm=0 anyway). NEW layout =
root-v10 files (byte-identical to `/tmp/fleet/f1/fixed`, verified against the
mounted v10 image).

Every 8-aligned value in file-backed non-`__TEXT` sections classified (V==0 skipped):

| class | count | action |
|---|---|---|
| self (target in own OLD segment range) | **10,850** | patched `V + Δvm(target seg)`; chain-form written clean |
| cross (target in another closure dylib's OLD range) | **907** | patched by target dylib's Δvm |
| — of which chain-form (encoding present) | 11,687 | decoded, written as clean absolute |
| cache-other (value in cache maps, outside the 36) | 13,599 | left; overwhelmingly non-pointer (bignum limbs, flags, tagged data) |
| unmapped (no range) | 13,517 | left (non-pointer data / literal pools) |
| bind-chain (bit63 set, res==0) | 286 | left — all false positives (−0.0, objc tagged pointers, bit patterns); real cache binds would be GOT/runtime territory |
| self/cross inside `__TEXT` sections | **0** | `__TEXT` untouched (N1r byte-identity gate preserved) |

Rebase targets by section: `__DATA_CONST,__const` 9,066 · `__objc_c` 800 ·
`__DATA_DIRTY,__objc_d` 449 · `__got` 298 · `__DATA,__const_` 279 · `__la_resolver`
104 · `__dyld4` 4 · `__objc_selrefs`-class 39+12 · rest ≤ 90 each. Big movers:
libdispatch `__DATA` Δvm **+0x2403900**, libsystem_platform `__DATA` +0x2c50620,
libsystem_pthread `__DATA` +0x2c486d8, libobjc `__OBJC_RW` +0x26ad00, everything
else −0x450..−0x3b38 or 0.

## 3. Second defect class fixed in the same files: corrupt sectname tails

The v8-carve/N1r pipeline writes the NEW-layout section addr into the null tail of
the 16-byte `sectname` field (+8..+15) of every section record. For names < 8 chars
this sits in padding (benign); **for names ≥ 8 chars there is no NUL and the addr
bytes ARE the name tail** — `__cstring`, `__unwind_info`, `__mod_init_func`,
`__objc_classlist`, `__objc_selrefs`, `__objc_imageinfo` … read back as garbage
(e.g. `__cstrinP\xc5f\xf9\x01`).

dyld-1066.8/1042.1 match objc data sections by **full strcmp/strncmp-16**:
`MachOFile::hasSection` (strcmp), `findObjCDataSection` (:3565 strcmp),
`__objc_imageinfo` matches (MachOAnalyzer.cpp:2554/:2584/:4404, MachOLayout.cpp:65,
PrebuiltObjC.cpp:1200), `__objc_selrefs` roObjC option (JustInTimeLoader.cpp:891).
Initializer discovery is immune (`forEachInitializerPointerSection` matches section
TYPE 0x9, not name — MachOFile.cpp:2909). With corrupted names, dyld's objc setup
would silently treat every closure dylib as objc-less (no class registration, no
selref fixups) — a second wall behind wall #3. root-v11 restores all 380 corrupted
sectname records from the raw splits' clean bytes (load-command header area only;
`__TEXT` content beyond `32+sizeofcmds` byte-identical to root-v10).

## 4. Rebase arithmetic (correctness argument)

dyld's own rebase for these images is `locValue + slide` with slide = 0
(Loader.cpp:1836-1887 → forEachRebaseLocation_Relocations; there are no local/external
relocs in the splits) — i.e. dyld can never fix them. The cache built these pointers
by applying chains at cache-build time to absolute cache VAs; the split moved the
segments by Δvm = new_vmaddr − old_vmaddr per segment. A value `V` that lands in the
OLD range of segment S must become `V + Δvm(S)` — exactly what a rebase with
per-image slide Δvm does; chain-form values are first decoded (target36 | high8<<36)
and written as CLEAN absolute addresses (the encoding is a cache-load artifact; no
runtime walker exists in our boot). GOT slots (type 0x6/0x7) are also patched
(harmless: dyld's bind walk overwrites them with trie-resolved targets, which equal
the patched values); `__DATA_CONST` is patched in-file — equivalent to dyld rebasing
before protection (dyld maps it RW, fixes, then re-protects).

## 5. Validation (all host-side, before any boot)

| gate | result |
|---|---|
| post-rebase scan: chain-form values whose target is inside a closure OLD range | **0** (129 chain-form values remain, all pointing OUTSIDE the closure — see §6) |
| patched values inside target's NEW segment range | **11,757 / 11,757** |
| F1 classic-walk predicate audit (`audit_indirect.py` on rebased files) | **36/36 clean, 0 fatal** |
| GOT bind preflight (indirect-symbol walk vs union of rebased tries) | **3,944/3,944 resolve, 0 unresolvable** |
| launchd bind census (`launchd-fixups.txt` vs rebased tries) | **537/537** (536 unique symbols + weak-import `_mig_strncpy_zerofill`, which resolves) |
| export tries | 10,901 exports across 36, LC bytes byte-identical to root-v10 |
| `__TEXT` content beyond load commands | byte-identical to root-v10 (36/36) |
| sectname repairs | 380 records restored to raw-split clean form |
| volume-level diff v11 vs v10 | 199 files, same set, **exactly 36 differ** (the dylibs), 0 unexpected |
| mounted-v11 bytes == rebased/ files | 36/36 byte-identical (no HFS extent sensitivity) |
| fsck_hfs -fn | `The volume iOSRoot appears to be OK` |
| pinned root-v10.hfs sha before AND after | `3af78bcb…` unchanged |

## 6. Residuals and wall-#4 prediction

Nothing in the closure's stale-pointer space is left dangling **within** the
closure. What remains, in likely first-fault order:

**(a) NO-INITIALIZER GAP (expected next wall — "wall #4a").** None of the 36 split
dylibs has any initializer: 0 × LC_ROUTINES_64, 0 × S_MOD_INIT_FUNC_POINTERS,
0 × S_INIT_FUNC_OFFSETS (checked in root-v11 files AND the 20H392 cache image
headers — the cache stores per-image initializer addresses only in dyld4 runtime
metadata, the PrebuiltLoaderSet in the .dyldlinkedit subcache; the split pipeline
never synthesized `__mod_init_func`). launchd itself also has none (normal for a
self-sufficient main). Consequence: dyld finishes binds, runs zero initializers,
jumps to launchd main with libSystem/libdispatch/objc runtime state never
initialized (no `_objc_init`, no `dispatch_init`, no `pthread_init`). Signature:
launchd dies AFTER dyld completes (no dyld diagnostic), at the first runtime call
that needs constructed state — likely objc_msgSend/dispatch inside launchd's early
xpc/bootstrap path, or a fault reading a structure only init would have built.
Fix path (next lane): harvest the closure's initializer addresses from the cache
PrebuiltLoaderSet / accelerator metadata and synthesize `__mod_init_func` sections
(+ LC updates) in a root-v12, or teach the boot dyld to call them post-bind.

**(b) OBJC DROPPED-REGION REFERENCES (hits after any objc registration — "wall
#4b").** 129 chain-form values remain in `__DATA_DIRTY,__objc_data` (libxpc 65,
libdispatch 55, libsystem_trace 5, libsystem_c 1, libobjc 1) + 2 `__objc_selrefs`
(dispatch 1, libxpc 1) whose targets are cache-GLOBAL regions the extractor dropped:
libobjc's cache-only `__OBJC_RO` segment (uniqued selector strings — e.g.
selref → `stringWithUTF8String:` @ 0x1832a6cfe) and cache-global objc optimization
regions (0x1e585xxxx — absolutized method lists / preopt data owned by no image).
The raw split of libobjc simply has no `__OBJC_RO` segment. Any objc class/method
walk touching those 131 slots faults reading 0x1832xxxxx/0x1e58xxxxx. Fix path:
relocate those selector strings into mapped closure memory (selref redirect to an
equivalent mapped copy) and map the 131 `__objc_data` entries onto per-dylib
`__TEXT,__objc_methlist` content (content matching required), or teach libobjc/dyld
to skip preopt-form classes.

**(c) DYLD-DATA SUBCACHE POINTERS ("wall #4c").** 150 aligned clean values ≥
0x217040000 point into the cache's shared-`__LINKEDIT`/dyld-data region (libSystem
trace 47, libdyld 35, corecrypto 16, libc++ 14, …) — including libdyld `__dyld4`
+0x38 dyldLookupFuncAddr = 0x226c830e8. These pointed at dyld-owned runtime state
in the cache; nothing maps that region at boot. Fault only if the owning code path
dereferences them (dyld wires libdyld's `apis`/`allImageInfos` itself, so the
first-touch risk is `dyldLookupFuncAddr` consumers — the `__DATA,__dyld` DATAdyld
path in JustInTimeLoader.cpp:786 only runs for macOS-platform minOS≤10.9 images,
none here).

**(d) systrace errno storm** (`/private/var/cores` ENOENT, dyld_shared_cache
ENOENT/EROFS, map_with_linking_np EINVAL fallback) — proven benign in the M-wave;
unchanged by root-v11.

## 7. Source provenance + reproduce

dyld source trees: `/tmp/r2-dyld-1066` = **the real dyld-1066.8** (git `ce1cc20`,
F1's predicate citations — verified this session); `/tmp/r2-dyld-src` = **dyld-1378**
(unrelated newer tree — do not cite for this closure). The BOOT dyld shipped in
root-v10 (`/usr/lib/dyld`, 922,912 B, lsl/EphemeralAllocator symbols) is the fleet's
own build of the dyld-1042.1 family — the `__dyld4` wire-up mechanism in §1 cites
the 1042.1 ref tree at `research/laguna-minicache/ref/dyld-1042.1/dyld/dyldMain.cpp:724-757`.

```sh
cd /tmp/fleet/f1b
python3 rebase_ptrs.py survey      # raw-vs-cache segment identity (0 mismatches, LINKEDIT dedup aside)
python3 rebase_ptrs.py census      # classification: self 10,850 / cross 907 / residuals
python3 rebase_ptrs.py patch       # writes rebased/ + post-scan + name repair (380)
python3 rebase_ptrs.py verify      # all gates above; PASS
./f1b-build-v11.sh                 # pinned-v10 copy → install 36 → BSDInfo → fsck → diff → sha
```

Builder pin (payload lane): add WITH_ROOT11 branch pinning
`root-v11-candidate.hfs` sha `7106626ecf6bb16c3691686188a9340f9d33e5a87c7eda7c10151bb8d9a7061d`.
Firing = coordinator only.

---
5-line summary:
1. Wall #3 root cause byte-proven: dsc splits carry UN-APPLIED cache chained-fixup
   entries in data sections (e.g. libdyld `__dyld4` NXArgcPtr = 0x201e1d34628 =
   chain-encoded target 0x1e1d34628 + next=2<<40); dyld's `*state.vars->NXArgcPtr
   = argc` (dyldMain.cpp:748) faults through it — slide=0 classic rebase can never fix these.
2. root-v11 = root-v10 + 11,757 pointer rebases (10,850 self + 907 cross, per-segment
   Δvm; chain-form decoded to clean absolutes) + 380 corrupt sectname repairs
   (addr-in-name-tail quirk broke every ≥8-char section name → dyld objc name matches).
3. Validated: 0 residual in-closure stale pointers; 11,757/11,757 in-range;
   36/36 F1 predicate clean; 3,944/3,944 GOT binds; 537/537 launchd census; tries +
   __TEXT byte-identical; fsck OK; exactly-36-file diff; v10 pin intact.
4. Candidate /tmp/fleet/f1b/root-v11-candidate.hfs sha 7106626ecf6bb16c… — builder
   pin (WITH_ROOT11) untouched, nothing fired.
5. Wall #4 pre-registered: no-initializer gap (0 init sections/LCs in all 36 — cache
   stores init addrs only in dyld4 PrebuiltLoaderSet) → launchd main on uninitialized
   runtime; then 131 objc dropped-region refs (libobjc __OBJC_RO uniqued selstrings +
   cache-global method lists) and 150 dyld-data-subcache pointers (incl. libdyld
   dyldLookupFuncAddr). Full detail: research/laguna-usb-fix/POINTER-REBASE.md.
