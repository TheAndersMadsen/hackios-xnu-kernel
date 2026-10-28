# INDIRECT-FIX.md — the v61 dyld wall "indirect symbol has out of range address" → root-v10

Lane: F1 (2026-10-27, resumed session). Status: **root-v10 candidate BUILT + VALIDATED, not fired**
(coordinator owns the device). Candidate: `/tmp/fleet/f1/root-v10-candidate.hfs`
sha256 `3af78bcb87800967e5e5e21db6a3a84c97bf18da4e68933f0ffbdda4d3b481e6`.

Every claim below cites dyld-1066.8 source (git `ce1cc20 dyld-1066.8`, `/tmp/r2-dyld-1066`)
or a byte-level audit reproducible with `/tmp/fleet/f1/audit/audit_indirect.py`.

## 1. The exact predicate (source, file:line)

Device fatal (v61): `pid 1 exited -- exit reason namespace 6 subcode 0x9, description:
indirect symbol has out of range address`, preceded by the non-fatal
`map_with_linking_np(...,0x1,...,0x1118) = 22 (EINVAL)` (kernel lacks the MWL syscall;
dyld tolerates it — Loader.cpp:1676-1694 "falling back to linking in-process").

Which images take the dying path — dispatch in `MachOAnalyzer::forEachBind`
(common/MachOAnalyzer.cpp:1408; decisions at :1432/:1697/:1700):

1. `leInfo.dyldInfo != nullptr` → LC_DYLD_INFO bind opcodes.
2. `else if (leInfo.chainedFixups != nullptr)` → **"binary uses chained fixups, so do
   nothing"** (:1697-1699) — launchd (real 20H392 binary) takes this and is innocent.
3. `else if (leInfo.dynSymTab != nullptr)` → **classic walk** — every cache-split dylib
   in root-v9 (no LC_DYLD_INFO, no LC_DYLD_CHAINED_FIXUPS) lands here.

Runtime chain for a cache-less boot (everything is a JustInTimeLoader):

- `Loader.cpp:1786 if (ma->hasChainedFixups()) … else if (:1808 hasOpcodeFixups()) …
  else` (:1836-1887) → `forEachRebaseLocation_Relocations` (Loader.cpp:1857) THEN
  `forEachBindLocation_Relocations` (Loader.cpp:1869).
- Bind-target construction also runs `mach_o::Fixups` (`JustInTimeLoader.cpp:946`,
  `fixups.forEachBindTarget` :955) → `Fixups::forEachBind_Relocations`
  (common/MachOLayout.cpp:1484, fatal emit **:1589**).

The fatal string is emitted at three sites, all funneling into the same predicate:

| Emit site | Function | Reached from |
|---|---|---|
| common/MachOAnalyzer.cpp:**4813** | `forEachBind_Relocations` (:4708) | Loader.cpp:1869 (bind fixups) |
| common/MachOAnalyzer.cpp:**1788** | `forEachBind` classic branch (:1700-) | validation walks |
| common/MachOLayout.cpp:**1589** | `Fixups::forEachBind_Relocations` (:1484) | JustInTimeLoader.cpp:955 |

The predicate itself:

- `MachOAnalyzer::segIndexAndOffsetForAddress` (**MachOAnalyzer.cpp:1106-1115**): linear
  scan over `segmentsInfo[]` built from the file's own LC_SEGMENT_64 commands
  (`getAllSegmentsInfos` :775 → `forEachSegment`, `__PAGEZERO` included at index 0);
  returns false when the address is not inside `[seg.vmAddr, seg.vmAddr+seg.vmSize)`
  for ANY segment.
- `forEachIndirectPointer` (**MachOAnalyzer.cpp:1167**): walks every section with type
  `S_NON_LAZY_SYMBOL_POINTERS` (0x6) or `S_LAZY_SYMBOL_POINTERS` (0x7)
  (SDK loader.h:517/519; arm64 `S_SYMBOL_STUBS`=0x8 sections are NOT walked — only
  i386 self-modifying stubs are), and for each 8-byte slot passes
  **`sectAddr + i*elementSize`** — the SLOT address, not a symbol value.
- So the error means: **a pointer-section slot address lies outside every segment of the
  file** — a load-command inconsistency, not a symbol-table problem.

Intermediate checks in the same walk (all emulated in my audit):
`section %s overflows indirect symbol table` (:1207, `reserved1+size/8 > nindirectsyms`),
`indirect symbol[%d] = %d which is invalid symbol index` (:1221, `symNum > nsyms`),
`symbol[%d] string offset out of range` (:1238), and on the rebase side
`local relocation has out of range r_address` for LOCAL (0x80000000) slots
(:5364 ≈, rebase walk runs first per Loader.cpp:1857).

## 2. Defect table (byte-level audit of root-v9's 36 closure dylibs)

Tool: `/tmp/fleet/f1/audit/audit_indirect.py` (exact emulation of the dyld-1066.8 walk,
evaluation order = local relocs → indirect LOCAL slots → external relocs → indirect BIND
slots; emits dyld's exact strings). Ground truth = Apple dsc_extractor output at
`research/laguna-deviceroot/work/dylibs/` (raw splits), never-hollowed references
(libdispatch, libSystem.B, libsystem_platform, libsystem_pthread carry the correct
standalone form).

Root cause: **the v6-era carve rebuilt every LC_SEGMENT_64 (vmaddr page-aligned,
fileoff re-padded) but left each section's `addr` and `offset` at the stale raw-split
values.** The raw splits satisfy `sect.addr == sect.offset + (seg.vmaddr - seg.fileoff)`
for all 36 (0 violations); v9's moved segments broke that identity.

### Class 1 — stale section addr/offset (THE v61 fatal)

5 dylibs fail the slot-range predicate outright (BIND slots past segment end):

| dylib | offending slot (v9) | geometry |
|---|---|---|
| libSystem.B.dylib | `__DATA_CONST,__got[58]` slot=0x1dab664a0 (`___error`) | seg [0x1dab64000,+0x1e0) — slot 0x24a0 past start, beyond vmsize |
| libc++abi.dylib | `__DATA,__got_we[93]` slot=0x1dee72d90 (`__ZdaPv`) | seg [0x1dee70000,+0x300) |
| libdispatch.dylib | `__DATA_DIRTY,__la_res[955]` slot=0x1e1d36c90 (`_dispatch_mach_request_no_senders`) | seg end 0x1e1d36c89 — **7 bytes past end** |
| libsystem_platform.dylib | `__DATA_CONST,__got[109]` slot=0x1e1ac3240 (`___error`) | seg [0x1e1ac0000,+0x218) |
| libsystem_pthread.dylib | `__DATA_CONST,__got[93]` slot=0x1e1ac3458 (`___bsdthread_create`) | seg [0x1e1ac0000,+0x248) |

All 36 carry the same latent staleness (per-segment deltas `Δvmaddr`/`Δfileoff`
computed v9 vs raw split; e.g. libdispatch `__DATA_CONST(Δvm=-0x2d00,Δfo=0)`,
libSystem.B `__DATA_CONST(Δvm=-0x24a0,Δfo=+0x2000)`); the 31 non-fatal ones merely have
overhangs that still land inside a segment, i.e. binds would write into the WRONG
content location. Full delta table in `/tmp/fleet/f1/audit/` (fixer output log).

### Class 2 — cache-form `__got` flags (latent wall: silent bind skip)

31/36 dylibs carry `__got` in `__DATA_CONST` with **flags=0x0 (S_REGULAR)** — cache
form, where binds are chained fixups. `forEachIndirectPointer` only walks types 0x6/0x7
(:1193-1196), so those GOTs would never bind → first GOT-based call after init jumps
through a stale swept pointer. The never-hollowed references all carry **flags=0x6**
(S_NON_LAZY_SYMBOL_POINTERS) — the correct standalone form. Affected include libc++,
libc++abi, libobjc.A, libxpc, libsystem_c, libsystem_kernel's sibling set — i.e. launchd
would crash inside objc/libSystem init even after the indirect wall passed.

## 3. Fix per dylib class (root-v10 = stage + class 1 + class 2)

Base: N1r's intended root-v9 dylibs, REBUILT via their own pipeline
(`/tmp/n1r-work/n1r_patch.py`, re-run clean: 36/36 files, 10,901 exports, per-file
verification OK — restored `__TEXT` from raw dsc splits, cache export tries remapped
per-segment into the v8 layout, `LC_DYLD_EXPORTS_TRIE` filled, `__LINKEDIT` extended).
**Note: the pinned root-v9.hfs does NOT contain these repairs** — see §5.

On top of that (tool: `/tmp/fleet/f1/audit/fix_sections.py`):

1. **Class 1** — for every dylib, for every section, translate by its OWN segment's
   rebuild deltas: `addr += Δvmaddr`; `offset += Δfileoff` (non-zero-fill only). Restores
   the raw-verified identity `addr == offset + (vmaddr - fileoff)` in the v8/v9 layout.
   Segment vmaddrs/`__TEXT` stay exactly as N1r verified them (trie values and
   rebase-swept `__DATA` pointers remain coherent). 554 section fields patched.
2. **Class 2** — flip `__DATA_CONST __got` flags `0x0 → 0x6` where `reserved1 != 0`
   (31 flips), matching the never-hollowed reference form.

Everything else byte-identical to the staged files (verified: all diffs confined to
section-header bytes; `__TEXT` byte-verified against the correct raw sources incl.
libobjc via dylibs-stripped-closure).

## 4. Validation results (all host-side, before any boot)

| Gate | Result |
|---|---|
| dyld-1066.8 predicate audit (`audit_indirect.py`, exact strings/order) | **36/36 classic walk clean, 0 fatal** (3,944 walked GOT bind slots: libdispatch 555, libsystem_trace 287, libxpc 287, libSystem.B 60, …) |
| launchd bind census (launchd-fixups.txt vs union of fixed dylib tries) | **537/537 resolve, missing = []** |
| GOT bind preflight (every flipped-GOT entry resolves via tries) | **3,944/3,944 resolve, 0 unresolvable** |
| N1r per-file gates (re-run): trie walk count == export count, no unexpected bytes, `__TEXT` == raw source | 36/36 OK |
| section-fields-only diff (fixed vs staged) | PASS (all diffs inside section headers) |
| fsck_hfs -fn on root-v10-candidate.hfs | `The volume iOSRoot appears to be OK` |
| mount-level diff v10 vs pinned v9 | 199 files, same set, **exactly 36 differ** (the dylibs), 0 unexpected |
| pinned root-v9.hfs sha before AND after | `a427241dfa71e5088ff1f42695d43018798b64294f53254c001cfb519bba0c17` (unchanged) |

Candidate: `/tmp/fleet/f1/root-v10-candidate.hfs`, 29,360,128 B,
sha256 `3af78bcb87800967e5e5e21db6a3a84c97bf18da4e68933f0ffbdda4d3b481e6`.
Builder pin NOT changed; nothing fired. To boot it, the builder needs a `WITH_ROOT10`
branch (payload lane) pinning this sha.

## 5. Fleet-critical findings (coordinator attention)

1. **Pinned root-v9.hfs ≡ root-v8.hfs content.** File-level diff of the two mounted
   images: 199 files, byte-identical (0 differing). N1r's repaired candidate
   (`/tmp/fleet/n1r/root-v9-candidate.hfs`, sha a427241d… = the pinned v9) has all 36
   `LC_DYLD_EXPORTS_TRIE` empty (doff=0/dsize=0) and hollow `__TEXT` — the repairs
   never landed in the shipped image; only HFS layout churn distinguishes the images.
   Their final gate ("trie parsed, 537/537") must have run against an earlier mount.
2. **v60→v61 wall change is therefore NOT explained by root content** (payloads
   verified: v60 embeds root-v8 bytes, v61 embeds root-v9 bytes, contents identical;
   payload-region diff shows only the embedded-root image bytes differ, mach identical).
   Most plausible remaining mechanism: the laguna HFS driver serves different bytes for
   the same logical content depending on image layout (extent sensitivity) — K-lane
   audit target. Alternatively the binds in v61 resolved via the **symtab fallback**
   (`hasExportTrie()==false` with datasize==0 → global-symbol scan; n_values are
   cache-layout absolutes, correct at slide 0 for unmoved `__TEXT`) — and v60's run hit
   a layout where the driver returned degraded bytes. The v61 "wall moved" observation
   is real; its cause attribution is not settled.
3. Consequence absorbed into root-v10: with tries now FILLED (N1r design), dyld switches
   from the symtab-fallback to trie resolution (offset-based, layout-independent) —
   which ALSO removes the symtab-fallback hazard (23/36 dylibs have moved `__TEXT`
   (Δvm −0x1000…−0x3000) → their symtab n_values point past the load address; the
   fallback path would have returned wrong bind targets for them).

## 6. Wall-#3 prediction (what dyld does after indirect symbols pass)

Boot order after fixups-complete: `Loader::applyFixups` (all 36 classic walkers now
clean) → libSystem primitives → `findAndRunAllInitializers` (Loader.cpp:1884+) →
launchd main. Predicted walls, in order:

- **W-#3a (most likely): stale swept `__DATA`/`__DATA_CONST` pointers in moved-segment
  dylibs.** v8's carved `__DATA` carries rebase-swept absolute pointers for the
  CACHE layout; wherever v8 moved a segment vs the cache layout (23/36 `__TEXT`
  moved; e.g. libdispatch `__DATA_CONST` Δvm=−0x2d00), pointers targeting those
  regions dangle by Δvm. Bind-side GOT entries are REWRITTEN by the walk (fixed);
  `INDIRECT_SYMBOL_LOCAL` slots (rebase walk, slide=0 → value+0) and non-GOT internal
  pointer sections (`__objc_classrefs` etc., type S_REGULAR — NOT walked) keep stale
  values. Signature: no dyld diagnostic — pid 1 dies with SIGSEGV/signal-class exit
  (namespace 6 subcode 0x1/0x4-style or plain signal) during
  `findAndRunAllInitializers`/objc init, AFTER binds complete (no "Symbol not found").
- **W-#3b: missing-weak/nil-call crash path** — weak imports bound to nil then called;
  same stage, same signature class.
- **W-#4: launchd-main + F5's benign ENOENT storm** (`/private/var/cores`,
  `/System/Library/Caches/com.apple.dyld`, libobjc-env.dylib — all proven harmless).

If the console shows a bind-stage "Symbol not found" again, the discriminator is my
preflight: 0/3,944 unresolved + 537/537 — so any such failure would be a NEW resolution
surface (e.g. dyld querying a dylib outside the 36), not a regression of this fix.

## 7. Reproduce

```sh
cd /tmp/fleet/f1/audit
python3 audit_indirect.py /tmp/fleet/f1/fixed/*.dylib     # 36/36 clean
python3 fix_sections.py                                   # rebuilds fixed/ from work/ (stage)
# census + assembly scripts inline in /tmp/fleet/f1-indirect-report.md
```

---
5-line summary:
1. Predicate pinned: segIndexAndOffsetForAddress (MachOAnalyzer.cpp:1106) fails on SLOT
   addresses (sectAddr+i*8); fatal emits :4813/:1788/:1589; only no-dyldInfo,
   no-chainedFixups, has-dynSymTab images (the 36 split dylibs) take the dying path.
2. Defect: v6 carve rebuilt segments but left stale section addr/offset (5 dylibs fatal,
   36 latent) + 31 cache-form `__got` flags=0x0 that dyld would silently never bind.
3. root-v10 = N1r's pipeline re-run (tries filled, __TEXT restored — the pinned v9 never
   contained them: v9≡v8 content, fleet finding) + section-field translation (554 fields)
   + 31 GOT flag flips; nothing else touched.
4. Validated: 36/36 predicate-clean, 3,944/3,944 GOT binds resolve, census 537/537,
   fsck OK, exactly-36-file diff vs v9, v9 pin unchanged.
5. Candidate /tmp/fleet/f1/root-v10-candidate.hfs sha 3af78bcb87800967… — builder pin
   untouched, nothing fired; wall-#3 prediction = stale swept pointers in moved-segment
   dylibs at initializer time (no dyld diagnostic).
