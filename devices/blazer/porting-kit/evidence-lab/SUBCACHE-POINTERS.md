# SUBCACHE-POINTERS.md — wall #4 "dyld-data subcache pointers / v64b SIGSEGV" → root-v12 + dyld patch

Lane: F1c (2026-10-27). Predecessors: F1 (`INDIRECT-FIX.md`, root-v10), F1b
(`POINTER-REBASE.md`, root-v11). Status: **root-v12 candidate BUILT + VALIDATED,
not fired** (coordinator owns the device); **dyld-side patch required and shipped**
(`patches-dyld-preferred/0001-mapSegments-preferred-load.patch`, apply-check PASS on
the exact dyld-1066.8 tree).

Candidate: `/tmp/fleet/f1c/root-v12-candidate.hfs` — 29,360,128 B,
sha256 `45f3367196e0cbae5b27437024761204715c9fa9657683c8e1592f4adb50d95f`.
Pinned root-v11 (`7106626ecf6bb16c…`) re-hashed unchanged before AND after every build.

## 1. The v64b crash, symbolicated (mission item 1)

```
v64b (QEMU, root-v11): usertrap: launchd[1] exception 1 code 0x1 0x1e1d34068:
  pc 0x100afa058 lr 0x100afa020 sp 0x16f79b280, shared cache slide 0x0
  called from 0x100af82e0 / called from 0x0
```

1. **None of the 36 dylibs owns these PCs** — scanning all 38 root-v11 Mach-Os
   (36 dylibs + /usr/lib/dyld + /sbin/launchd) against pc/lr/called-from hits
   nothing: `/usr/lib/dyld` has `__TEXT` at vmaddr **0x0** (the extracted-cache-dyld
   signature) and launchd at 0x100000000..0x10008C000.
2. **dyld's runtime base = 0x100AE4000**, proven by two independent systrace string
   pointers in the same boot (`"/System/Library/Caches/com.apple.dyld/"` @0x100b55868
   is at dyld file offset 0x71868; `"/"` @0x100b54763 is at offset 0x70763 — both
   subtract to exactly 0x100AE4000). Therefore:
   - pc  0x100afa058 = **dyld+0x16058** = `dyld4::prepare(APIs&, dyld3::MachOAnalyzer
     const*)` +0x9c4 (boot dyld carries a 3,255-entry symtab; local-symbol resolution)
   - lr  0x100afa020 = prepare+0x98c
   - cf1 0x100af82e0 = `start`+0x6e0
3. Disassembly of dyld+0x16040..0x1605c (otool -tv on root-v11's /usr/lib/dyld):
   `findSectionContent("__DATA","__dyld4")` → `str x20,[x0]` (apis) →
   `str x8,[x0,#8]` (allImageInfos) → `str x9,[x10,#0x10]!` + `str x10,[x20,#0x70]`
   (state->vars=&defaultVars) → `ldr w9,[x8,#0x60]` (argc) → `ldr x10,[x0,#0x18]`
   (**NXArgcPtr**) → **`str w9,[x10]`** ← the faulting instruction. This is
   dyldMain.cpp:742 `*state.vars->NXArgcPtr = argc` (dyld-1066.8 tree, wire-up at
   :732-753), byte-for-byte.
4. **code[0] = 0x1 = KERN_INVALID_ADDRESS (unmapped), not a protection fault** —
   nothing had ever mapped the target window.
5. Same instruction in all three runs (v63 device, v63b QEMU root-v10, v64b QEMU
   root-v11): fault VA = the VALUE stored in NXArgcPtr (chain word 0x201e1d34628
   pre-F1b; preferred-window address 0x1e1d34068 post-F1b).

## 2. Root cause — dyld never honors preferred vmaddrs (mission item 2 reframed)

Boot dyld = **Apple dyld-1066.8** (its own strings: `@(#)PROGRAM:dyld PROJECT:dyld-1066.8`;
`__TEXT` at vmaddr 0x0 = dsc_extractor output), source-exact tree at
`/tmp/r2-dyld-1066` (git ce1cc20). `Loader::mapSegments` (dyld/Loader.cpp:1250; the
dyld-1042.1 ref tree has the same code at :1245):

```
vm_address_t loadAddress = 0;
kern_return_t r = ::vm_allocate(mach_task_self(), &loadAddress, vmSpace, VM_FLAGS_ANYWHERE);
```

- `vmSpace = ma->mappedSize()` (JustInTimeLoader.cpp:1180; MachOFile.cpp:2975 =
  segment span). Segments are then mapped at `loadAddress + (seg.vmaddr −
  preferredLoadAddress)` (Loader.cpp:1274, MAP_FIXED). **The file's preferred
  vmaddrs are never honored**; loadAddress is kernel-chosen per image.
- The 36 splits carry no rebase info (LC_DYLD_INFO/chained fixups zeroed by
  extractor design — R2 finding), so dyld's fixup phase cannot apply the slide:
  **every swept absolute pointer dangles by `loadAddress − preferredText`. That is
  all 11,757 F1b-rebased pointers, not just the ~150 subcache ones.** The mission's
  "~150 pointers into the dyld-data subcache" framing was a mis-attribution: those
  are the cache-builder's dyld-alias substitutions (below), harmless relative to
  the general slide-dangling problem.
- F1b's "loading UNSLIDED at preferred vmaddrs" was inferred from the usertrap line
  `shared cache slide 0x0` — which only reports the shared-REGION slide (no cache
  = 0). Under stock dyld, slide-0 loading of a disk dylib never happens.
- The 94 preferred-range overlaps found across the 38 root-v11 images (nine dylibs
  claim `__DATA_DIRTY`/`__OBJC_RW` at 0x1e1d34000; 36× `__LINKEDIT` at 0x217040000)
  are real file-geometry defects but moot under stock dyld — preferred windows are
  simply never mapped. They still had to be fixed for the v12 design (§3).

Subcache-pointer semantics (mission item 2 answer):
- `LibdyldDyld4Section` (DyldProcessConfig.h:90-98): +0x00 apis, +0x08
  allImageInfos, +0x10 ProgramVars defaultVars (mh/NXArgcPtr/NXArgvPtr/environPtr/
  __prognamePtr), **+0x38 dyldLookupFuncAddr**, **+0x40 tlv_get_addrAddr** (the
  F1b residual-4c pair).
- In a standalone libdyld these are **self-pointers into libdyld** (libdyldGlue.cpp:106:
  `…{ nullptr, nullptr, {…}, &legacyDyldLookup4OldBinaries, tlv_get_addr }`). The raw
  20H392 split stores exactly that: `+0x38 = chain(0x1a6c830e8)` = libdyld
  `__TEXT`+0x30e8, `+0x40 = chain(0x1a6c818d0)` = `__TEXT`+0x18d0. The CACHE BUILDER
  substituted dyld-subcache aliases (SharedCacheBuilder.cpp:6673-6677) — the
  extractor kept the aliases (0x226c830e8/0x226c818d0) and F1b left them as
  `cache-other`. The raw splits carry the correct standalone targets, so a
  raw-ground-truth rebase restores them (§3, verified in v12).
- Their only dyld-side readers are macOS-gated and never fire for launchd:
  dyldMain.cpp:797-800 (`hasProgramVars` — old crt1.o-style macOS binaries) and
  JustInTimeLoader.cpp:774-794 (`DATAdyld` path, `platform == macOS` +
  `minOS <= 10.9`). libdyld's own glue reads `gDyld.apis` (dyld-filled) only.

## 3. The fix — root-v12 files + dyld-1066.8 patch (mission items 2-3)

No per-target pointer value can be correct under the stock loader (kernel-chosen
load addresses). The complete fix is two artifacts:

### 3a. root-v12 — make slide-0 loading POSSIBLE and exact

Builder: `/tmp/fleet/f1c/audit/build_v12.py` (survey/patch/verify); image recipe
`/tmp/fleet/f1c/f1c-build-v12.sh` (f1b's BSDInfo/fsck/diff/sha pipeline re-targeted).

1. **Layout plan**: compact each dylib's segments into one tight 16K-aligned span
   (LC order, `__TEXT` first; cursor advances with the OUTPUT file's vmsizes — the
   v6-carve expanded `__LINKEDIT` vmsizes beyond the raw split's piece) and lay the
   36 spans out pairwise-disjoint starting at **0x300000000** (total 9.8 MiB of VA,
   ends 0x300A9F000; above launchd/dyld/kernel-chosen heaps/stack, above every old
   cache window ≤0x22ADE4000 so stale residuals cannot alias into mapped memory).
   This de-conflicts all 94 overlaps **and** the 36× shared-window `__LINKEDIT`
   (each image's `mappedSize` reservation becomes its real span, so fixed
   reservations cannot collide).
2. **LC/section translation**: every `LC_SEGMENT_64.vmaddr` and section `addr`
   := raw + Δ12(seg) (fileoff/offset untouched → the F1 identity
   `addr == offset + (vmaddr − fileoff)` is preserved).
3. **Symtab**: 21,048 defined-symbol `n_value` := raw + Δ12(seg) (absolute-vmaddr
   form, classic Mach-O convention).
4. **Export tries**: dyld-1066.8 binds via `BindTarget{loader, runtimeOffset}`
   (Loader.h:140) — address = loadAddress + trie value (the
   `forEachExportedSymbol` callback param is literally `imageOffset`). v11's tries
   already hold loadAddress-relative values (N1r's remap — slide-correct, which is
   why v63/v64b's runtime GOT binds resolved sanely). v12 values are re-expressed
   as `value11 + (Δ12−Δ11)(seg) − (Δ12−Δ11)(__TEXT)`: **10,901/10,901** remapped
   in place with byte-length-stable ulebs; name sets byte-proven unchanged.
5. **Pointers re-derived from RAW ground truth** (not census deltas): every
   8-aligned value in file-backed non-`__TEXT` sections is classified against the
   RAW cache layout (F1b's self/cross model) and written as raw + Δ12(target seg).
   - **11,757 slots are f1b-consistent** (written value == what F1b would produce)
     — exact cross-validation of F1b's rebase from independent ground truth.
   - **4,849 slots are NEWLY FIXED**: the v6-carve had already mis-rebased them
     before F1b's census ran; the census classified the v10-era mangled values as
     `cache-other` and left them. Examples: libSystem `__got` entries whose raw
     targets are libdyld `__TEXT` (the cache's dyld-alias entries pointing at
     0x226c8xxxx/0x21c9cxxxx) and dispatch/trace/objc slots scattered into the
     dyld subcache. All now point at the true standalone (v12) target.
   - `__dyld4 +0x38/+0x40`: restored by the same pass and verified ==
     v12 libdyld `__TEXT`+0x30e8/+0x18d0 (standalone
     `legacyDyldLookup4OldBinaries`/`tlv_get_addr`) — 0x3003870e8/0x3003858d0.
6. Untouched by design: `__TEXT` content beyond load commands (36/36 byte-identical
   to v11), LC sizes/`sizeofcmds`, `LC_FUNCTION_STARTS` (0x26 — file-offset based;
   NOT LC_ROUTINES_64: **LC_ROUTINES_64 = 0x8000001A, absent in all 36** — F1b's
   no-initializer finding re-verified with the right constants after two false
   constant readings), export-trie name sets, GOT/LAZY slot counts.

### 3b. dyld-1066.8 patch (REQUIRED — v12 alone cannot change the wall)

`research/laguna-usb-fix/patches-dyld-preferred/0001-mapSegments-preferred-load.patch`
(`git apply --check` PASS on /tmp/r2-dyld-1066 = ce1cc20; nothing modified there):

- `Loader::mapSegments` gains a `preferredLoadAddr` parameter; when non-zero it
  first tries `vm_allocate(VM_FLAGS_FIXED)` at the preferred address (loud
  `state.log` lines on success and on fallback), then falls back to the stock
  ANYWHERE behavior. Diagnostics, never silent.
- `JustInTimeLoader.cpp:1197` passes `ma->preferredLoadAddress()`; the
  PrebuiltLoader call site passes 0 (unchanged semantics; our boot is JIT-only).
- With root-v12's disjoint windows every closure dylib loads at its preferred
  base with slide 0; a fallback (window taken) degrades to today's behavior with
  a named log line instead of a mystery SIGSEGV.

Build note for the dyld lane: no fleet dyld build exists (root's /usr/lib/dyld is
the plain extracted 20H392 cache dyld); options: xcodebuild the dyld-1066.8
project for arm64-iOS, a community ios-dyld recipe, or (fragile, last resort)
binary-patching the extracted dyld. None executed in this lane.

## 4. Validation (all host-side, root-v11 pin untouched)

| gate | result |
|---|---|
| F1 classic-walk predicate (`audit_indirect.py` on v12) | **36/36 clean, 0 fatal** |
| GOT bind preflight (indirect-symbol walk vs v12 tries) | **3,944/3,944 resolve** |
| launchd bind census (names vs v12 tries) | **536/536 unique (+1 weak, resolves) = 537** |
| export tries | 10,901 exports, name sets identical to v11, all values in v12 ranges (loadAddress-relative form) |
| rebased pointer values | **16,606/16,606 inside target's v12 range** (11,757 f1b-consistent + 4,849 carve-mangled fixes) |
| symtab | 21,048 defined n_values, 0 out of range |
| `__TEXT` content beyond load commands | byte-identical to v11, 36/36 |
| NEW disjointness | **0 segment overlaps, 0 reservation (mappedSize) overlaps** across 36 images + dyld/launchd file windows |
| NEW residual scan | 11,785 values outside v12 windows in non-GOT/non-__TEXT data sections = data/false-positive population (5,583+1,302+… dropped-cache literals, 4,456 not-in-cache); **0 in `__got`/`__la_resolver`**, **0 in `__dyld4`**, 0 chain-form with closure targets |
| `__dyld4` pair | verified standalone self-pointers (0x3003870e8/0x3003858d0) |
| fsck_hfs -fn | `The volume iOSRoot appears to be OK` |
| volume-level diff v12 vs pinned v11 | 199 files, same set, **exactly 36 differ** |
| mounted-v12 bytes == builder output | 36/36 byte-identical |
| pinned root-v11 sha before AND after | `7106626ecf6bb16c…` unchanged |
| dyld patch | `git apply --check` PASS on ce1cc20; zero writes to shared trees |

## 5. Residual registry (what still points outside mapped memory, and why)

- **~7,200 values targeting dropped cache text/data** and **4,456 not-in-cache**:
  overwhelmingly non-pointer data (bignum limbs, tagged words, literals) — same
  population F1b documented (13,599 `cache-other` / 13,517 `unmapped`), now
  enumerated per section by the builder's gate [7]. Cannot be "fixed": their
  referents do not exist in a cacheless boot.
- **~92 values in the shared-LINKEDIT/dyld-data windows** (subcache 41/42):
  non-pointer data rows in `__const` tables; none in deref-walked sections.
- **131+2 objc dropped-region refs (F1b residual 4b)** — still open, unchanged:
  libobjc's cache-only `__OBJC_RO` uniqued selstrings + cache-global preopt
  method lists. Reachable only once objc registration runs.
- **`__got`/`__la_resolver`: zero residuals** — dyld rebinds every slot at runtime
  anyway (slide-correct via tries), and after the rebase the file values agree.

## 6. Wall-#5 prediction (mechanism + signature; mission item 4)

**Prerequisite**: v12 + patched dyld, otherwise the wall stays #4 verbatim (under
stock dyld, v12's first preferred-window deref faults exactly like v64b — at
libdyld's new `__dyld4` value 0x30032cxxx instead of 0x1e1d34068; that would be a
successful A/B negative-control, not a regression).

Mechanism (F1b residual 4a, re-verified on v12 with the correct constants —
0×LC_ROUTINES_64 (0x8000001A), 0×S_MOD_INIT_FUNC_POINTERS, 0×S_INIT_FUNC_OFFSETS
in all 36; the cache keeps initializer addresses only in dyld4's PrebuiltLoaderSet
metadata): dyld finishes binds + wire-up, `findAndRunAllInitializers` finds
NOTHING, launchd main starts with libSystem/libdispatch/libobjc never constructed.
launchd's first runtime call that needs constructed state (malloc fast-path book-
keeping, `dispatch_*`, `objc_msgSend`, `xpc_*` — per K3's LAUNCHD-NEXT map the
early path is xpc/bootstrap-heavy) dereferences uninitialized/stale runtime state.

Signature: **no dyld diagnostic, no `map_with_linking_np` death, and — the
discriminator — no usertrap inside dyld's runtime window (~0x1008xxxxx-0x100b6xxxx)**.
The first fault's pc should sit inside launchd (`0x100000000..0x10008C000`) or a v12
dylib window (`0x300000000..0x300A9F000`), faulting on garbage/uninit data
(K3's W1-W5 discriminators apply unchanged). If instead the pc is dyld-internal
again with an out-of-window fault VA, the dyld patch did not take (build/bind
failure) — check for the patch's `mapping … unslided at preferred address` log line.
Next-next wall candidates in order: objc dropped-region refs (§5, 4b) once objc
init actually runs (it will only run if an initializer path exists — it does not),
then K3's W3/W4 plist-fatal/boot-task walls.

A/B asks: **F2** — bridge-payload A/B of v12 (expect: byte-identical wall-#4
signature to v64b except fault VA = libdyld's new `__dyld4` value, i.e. negative
control) and of v12+patched-dyld once a build lands (expect: wall #5 signature).
**Coordinator**: dyld build lane decision; WITH_ROOT12 pin from sha
`45f3367196e0cbae…` only AFTER the dyld patch can ship in the same payload
(v12-alone boots = negative control, spend a cycle only if cheap).

## 7. Files + reproduce

```
/tmp/fleet/f1c/root-v12-candidate.hfs   # sha 45f3367196e0cbae5b27437024761204715c9fa9657683c8e1592f4adb50d95f
/tmp/fleet/f1c/v12/                     # the 36 transformed dylibs (image source of truth)
/tmp/fleet/f1c/audit/build_v12.py       # survey | patch | verify
/tmp/fleet/f1c/audit/symmap.py          # phase-1 owner/overlap map (evidence)
/tmp/fleet/f1c/f1c-build-v12.sh         # pin-checked image build (BSDInfo restore + fsck + 36-file diff)
/tmp/fleet/f1c/dyldpatch/               # a/ = pristine ce1cc20 files, b/ = patched
research/laguna-usb-fix/patches-dyld-preferred/0001-mapSegments-preferred-load.patch

cd /tmp/fleet/f1c/audit
python3 build_v12.py survey             # layout plan + deltas
python3 build_v12.py patch              # writes v12/ (16,606 pointer fixes, 10,901 tries, 21,048 syms)
python3 build_v12.py verify             # all gates above -> PASS
bash /tmp/fleet/f1c/f1c-build-v12.sh    # candidate image (never fires)
```

---
5-line summary:
1. Wall #4 root cause REFRAMED with proof: boot dyld = extracted dyld-1066.8 whose mapSegments (Loader.cpp:1250) vm_allocate(ANYWHERE)s every disk dylib — preferred vmaddrs never honored, no rebase info → ALL 11,757 slide-dangling pointers; v64b fault = prepare()'s `*NXArgcPtr = argc` (dyld+0x16058, dyld base 0x100AE4000 proven by two string-pointer subtractions), same instruction as v63/v63b.
2. The "150 subcache pointers" are cache-builder dyld-alias substitutions; raw splits carry the standalone self-pointer targets — the raw-ground-truth rebase restores them automatically (libdyld __dyld4 +0x38/+0x40 = 0x3003870e8/0x3003858d0 = legacyDyldLookup4OldBinaries/tlv_get_addr).
3. root-v12 (sha 45f3367196e0cbae…): 36 dylibs compacted into disjoint 16K-aligned spans at 0x300000000+ (0 overlaps incl. __LINKEDIT), LC/section/symtab/trie/pointer transforms all re-derived from raw ground truth — 11,757 f1b-consistent + 4,849 NEWLY-fixed carve-mangled slots the census could not see.
4. All prior validators green (36/36 predicate, 3,944 GOT, 537 census, __TEXT identity, fsck, exactly-36 diff, v11 pin 7106626e… unchanged) + NEW gates (disjointness 0/0, residuals 0 in GOT/__dyld4, 16,606 in-range, tries 10,901 v12-relative).
5. REQUIRED companion: patches-dyld-preferred/0001 (mapSegments FIXED-at-preferred, loud fallback) — git apply --check PASS on ce1cc20; v12+stock-dyld = negative control (same wall at 0x30032cxxx); wall-#5 pre-registered = no-initializer gap re-verified with correct constants (0×LC_ROUTINES_64/0×mod_init in all 36) → launchd main on unconstructed libSystem/dispatch/objc; asks: F2 QEMU A/B, coordinator dyld-build decision.
