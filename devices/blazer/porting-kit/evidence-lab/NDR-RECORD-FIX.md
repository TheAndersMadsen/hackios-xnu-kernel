# NDR-RECORD-FIX — the launchd bind wall, root-caused and repaired in root-v9

Lane: **N1r (fix the `_NDR_record` bind wall)** · 2026-10-27 · **host-only** (no device actions; pinned v8 never mutated).
Mission: get launchd past its first dyld bind with the smallest honest change, weighing the fleet's "2.536 GiB cache via v3.9" plan against a targeted fix — and enumerate every other missing symbol now, so v61 cannot die on symbol #2.

Candidate: **`/tmp/fleet/n1r/root-v9-candidate.hfs` · SHA-256 `a427241dfa71e5088ff1f42695d43018798b64294f53254c001cfb519bba0c17`** (28 MiB; builder pin untouched — `77dc3e9f…` re-verified byte-identical before and after this lane's work).

> **STATUS — FIX CONFIRMED ON DEVICE (F4 ledger, 2026-10-27 17:34, v61).** v61 (image
> `22a0ae61…` = mach r4r7-hfs + THIS root-v9) does NOT name `_NDR_record` anywhere in the
> boot: the namespace-6 subcode-0x4 `Symbol not found` wall (§1/§2) is GONE — exactly §4
> outcome (1). Launchd bound past the full 537-symbol census; dyld reached shared-region.
> **NEW WALL (the next frontier, documented here):** `systrace: launchd[1]
> map_with_linking_np(0x16ed98780, 0x1, 0x16ed987b0, 0x1118) = 22` (EINVAL) →
> `pid 1 exited -- exit reason namespace 6 subcode 0x9, description: indirect symbol has
> out of range address` — the §5 residual family (indirect symbol table entries of the
> repaired dylibs pointing out of segment range after the per-segment trie remap / disk-trie
> alias semantics). Launchd never reached main (no `initproc exited` / `kern.initproc_spawned`).
> Fix lane: **F1 → root-v10** (`research/laguna-usb-fix/INDIRECT-FIX.md` when it lands).
> Evidence: `attempts/20261027-161053-v60/` + `attempts/20261027-173349-v61/` under this
> directory; ledger rows + prose in `ATTEMPTS.md` (v60/v61 backfill).

---

## 1. Root cause — two stacked layers, both on the bytes

### 1.1 The bind layer: the closure dylibs have EMPTY export tries (byte-confirmed)

`dyld_info -fixups` on root-v8's `/sbin/launchd` (541,600 B, `bcb112ed…`) shows 780 chained-fixup entries (264 binds / 537 unique symbols; 527 target `libSystem`, 10 target `libobjc`), and **bind #1 — at `__DATA_CONST __got 0x100068000` — is literally `libSystem/_NDR_record`**. v60 died on the first bind it attempted.

The bind fails because every one of the 36 closure Mach-Os carries the dsc_extractor's **placeholder trie: `LC_DYLD_EXPORTS_TRIE` (0x80000033) with `dataoff=0, datasize=0`** (verified on all 36 files of the volume). The shared cache was supposed to publish these exports from its own linkedit; with no cache, dyld-1066 reads exports ONLY from tries → **zero exports anywhere in the closure → every two-level bind fails**, and `_NDR_record` is simply the alphabetically-first edge of libSystem's re-export walk. K5's W5 ("closure dead-end") and the v6-era wall are this placeholder.

Provenance (answers the mission's "did the QEMU base's libsystem export it?"): the raw dsc splits in `research/laguna-deviceroot/work/dylibs` DO export `_NDR_record` — the 20H392 cache trie has it (cache-absolute VA `0x1de4fb270`, exactly matching the raw split's symtab entry, `n_type=0x0f` ordinary external). It vanished in the *normalized* generation that root-v8 actually ships (`888c63c4…`, byte-matched to `dylibs-normalized-stripped`): the normalizer's symtab pass dropped cache-published symbols. The laguna-141 QEMU base never solved this either — its QEMU boots rode the full native cache (N1 §3/§4), which is exactly why the bridge lineage never hit the wall past exec.

### 1.2 The execution layer (NEW discovery this lane): the closure `__TEXT` is HOLLOW

`root-v8`'s `libsystem_kernel.dylib` has **607 nonzero bytes in its entire `__TEXT`** (the header only) vs **191,378** in the raw split; the same holds across the closure (`__DATA`/`__DATA_CONST` are fully populated — the rebase-swept absolute pointers survive — only code was hollowed by the v6-era carve to fit the vendor_boot ramdisk). Consequence: even a perfect bind wall fix would hand launchd zeroed code — the first call into any closure function is SIGILL. The "bind wall" was therefore the *designed* terminal wall of a load/bind-only closure, and an honest "past the first bind" fix must restore real code too. This is why the fleet's "2.536 GiB cache or nothing" stance was correct as a *completion* plan — and why a 28 MiB candidate can now honestly aim past M3's bind instead of at a dead-end demonstration.

## 2. The census — all missing symbols, enumerated NOW (the symbol-#2 trap, found and closed)

Launchd's 537 unique binds vs the union of every closure dylib's symtab exports (v8 files and raw splits alike):

* **520 binds resolve from symtab exports** (they only need tries to *exist*).
* **17 binds resolve from NO symtab** — they exist only inside the cache tries:

| # | symbol | owner (cache trie) | note |
|---|---|---|---|
| 1 | `_NDR_record` | libsystem_kernel | the documented wall; MIG support constant, `NDR_record_t` in the kernel's mig sources, plain `n_type=0x0f` data symbol |
| 2–17 | `_memmove` `_memcpy` `_memset` `_bzero` `_memchr` `_memcmp` `_strchr` `_strcmp` `_strcpy` `_strlcat` `_strlcpy` `_strlen` `_strncmp` `_strncpy` `_strnlen` `_strstr` | libsystem_c | flags-0x08 alias nodes → `_platform_memmove` … (iOS 16.7 keeps the optimized mem/str exports aliased inside libsystem_c) |
| 17 | `___chkstk_darwin` | libcompiler_rt | flags-0x08 alias node, self-named |

Without the census, fixing only `_NDR_record` would have died on bind #2 (`_memmove` — also in `__got`, processed in address order). **All 17 are grafts of the same root cause**, which is why the fix transplants tries wholesale rather than grafting 17 points.

## 3. The fix — root-v9 = v8 + two repairs on exactly the same 36 files

Single-variable delta discipline: no files added or removed; `launchd`, `dyld`, the D2 plist + signature, identity records, and all 163 other volume files are byte-identical to v8.

Per closure dylib (all 36: 32 × `/usr/lib/system/*` + `libSystem.B` + `libobjc.A` + `libc++.1` + `libc++abi`):

1. **Restore real `__TEXT` code** from the raw dsc splits (`laguna-deviceroot/work/dylibs`; objc from the `__OBJC_RO`-stripped `dylibs-stripped-closure` tree, which is the same 20F66-generation file v8 hollowed). Only the byte range `[max(hdr_end, first __TEXT section offset), TEXT end)` is copied — v8's own load commands are never touched. Layout-invariant: sections sit at identical offsets (`__TEXT` never moved by the normalizer except its base vmaddr, which normalize patched per-segment).
2. **Transplant the dylib's own export trie** from the 20H392 cache (objc from the 20F66 cache — v8's objc is the 20F66-generation carve; verified by full-`__TEXT` byte comparison: 27+4 files match 20H392 at ≥99%, matching the dsc stub-adjustment rate; objc matches 20F66):
   * trie values are `__TEXT`-relative: remapped per containing segment as `v8val = value − delta_text + delta_seg`;
   * every `__TEXT` export is byte-verified (16 B) against the restored source bytes — **0 mismatches across 10,585 plain exports**;
   * resolver/alias nodes (`flags=0x08`, payload `[flags][uleb][canonical-name]` — identical shape in 20F66 and 20H392 caches) are transplanted **verbatim** when `__TEXT`-dwelling (layout-invariant): 338 shipped, incl. all 17 census symbols; 0 skipped (libc++'s 315 resolvers included — they are `__TEXT`-dwelling);
   * 1 drop: an objc export living in the stripped `__OBJC_RO` (no backing bytes — and not launchd-bound).
3. **Fill the existing empty `LC_DYLD_EXPORTS_TRIE`** (no load-command surgery — the placeholder LC is already there): trie bytes appended at a 16-byte-aligned offset after the old file end, `dataoff/datasize` patched in place, `__LINKEDIT` vmsize/filesize extended to cover. (History note: `deviceroot`'s `build-export-tries.py` wrote tries under LC `0x80000034` — that is `LC_DYLD_CHAINED_FIXUPS`, a fatal constant bug; `align-dylibs.py` carried the correct `0x80000033`. Neither tool's output ever shipped: no closure file has a non-empty trie.)

**Verification (all host-side, all green):**
* staged files: prefix byte-diff vs v8 limited to the trie LC (8 B) + LINKEDIT extent fields (16 B) + the restored `__TEXT` range — nothing else;
* independent trie re-walk of every staged file: 10,901 exports walked = exported;
* **launchd bind census: 537/537 resolved** against the final volume bytes (`launchd-fixups.txt` × `trie-symbols.json`); `_NDR_record` exports at trie value `0x1bee81c0` = its VA `0x1de4fb270` mapped into v8's moved `__DATA` (`0x1de4f8000`) — bytes confirmed present at the mapped offset;
* no closure dylib carries any bind/fixup LC (prelinked data — dyld processes no binds for them), so launchd's 780 fixups are the entire bind surface;
* `fsck_hfs -fn`: clean; BSDInfo identity bytes of all 36 replaced paths byte-identical to v8 (kit `hfs-fixmeta` pass); volume free 13.7 MB (tries cost ~1.1 MB; code restore is in-place, zero growth);
* pinned v8 `77dc3e9f…` re-hashed unchanged after all work.

## 4. Expected v61 console signature (vs v60's death)

v60 (v8): `hfs: mounted iOSRoot` → two `load_init_program: attempting` lines → `[908.504186] pid 1 exited -- exit reason namespace 6 subcode 0x4, description Symbol not found: _NDR_record` → `initproc failed to start` panic.

v61 (v9): the M1/M2 lines identical, then:
1. **NO `namespace 6` / `Symbol not found`** — the bind wall must be gone. Any namespace-6 death on v9 = a bind my census missed (its description names the symbol; send it back to this lane).
2. `minisandbox: request N (first seen from launchd[1])` lines (M2, non-fatal, K5 W7).
3. Past main(): **`initproc exited`** instead of `initproc failed to start` becomes reachable; `kern.initproc_spawned=1` (the reached-main probe, K5 §2).
4. Then the K5 §3.2/W9–W10 territory: job-spawn phase against 530 declared jobs with all program binaries absent → launchd-logged ENOENT spawn-fail storm, **launchd itself survives** (only PID-1 death panics); first-daemon/identity walls (lsd, logd, dslocal) ride the existing v3.9/full-root lanes.
5. If a residual alias-semantics or unslid-pointer issue trips instead, the signature is a **crash inside a closure dylib address range** (namespace 11/SIGSEGV/SIGILL with a closure-region PC) — NOT namespace 6. One boot cleanly discriminates all three outcomes.

## 5. Weighing vs the v3.9 cache transport (honest)

v9 does **not** obsolete v3.9. The 2.536 GiB native cache remains the real completion path (unique preferred addresses, full closure semantics, no hollow/restored-code caveats). What v9 changes: the 28 MiB md0 closure becomes genuinely *loadable, bindable, and executable*, so (a) v61 no longer terminates at a designed dead-end, (b) the next wall (launchd main → job-spawn) becomes observable on device, and (c) the v3.9 lane gets a live launchd to develop against instead of a fixed wall. Residual risks, stated: dyld-1066's flags-0x08 alias handling on a *disk* trie is inferred from the identical node shape Apple ships inside caches (disk and cache tries share the parser); the closure still requires unslided loading at its preferred cache-global addresses (v6-era closure design assumption, unchanged by v9); objc is the 20F66 generation (pre-existing v8 trait, unchanged).

## 6. Paths

* Candidate: `/tmp/fleet/n1r/root-v9-candidate.hfs` (sha above) — coordinator decision to boot; builder pin NOT changed.
* Tooling + evidence (regenerable): `/tmp/n1r-work/` — `census.py` (bind census), `harvest-closure.py` + `cache_exports.py` (20H392/20F66 cache trie harvester), `n1r_patch.py` (restorer + trie filler + staged verifier), `n1r-build-v9.sh` (scratch build: pin check → rw install → BSDInfo restore → fsck), `n1r-final-verify.py` (volume gate), `launchd-fixups.txt`, `trie-symbols.json`, `cache-closure-exports.json`.
* Compact report: `/tmp/fleet/n1r-ndr-report.md`; coordination line in `/tmp/fleet/COORDINATION.md`.

## Summary (5 lines)

1. **Fix chosen**: fill the closure's empty `LC_DYLD_EXPORTS_TRIE` placeholders with each dylib's own 20H392 cache export trie (objc: 20F66) remapped per-segment, AND restore the hollow `__TEXT` code from the raw dsc splits — a targeted image repair, not the v3.9 cache transport; root-v9 built in a scratch copy, pin untouched.
2. **Other missing symbols found: 16** — `_memmove/_memcpy/_memset/_bzero/_memchr/_memcmp/_strchr/_strcmp/_strcpy/_strlcat/_strlcpy/_strlen/_strncmp/_strncpy/_strnlen/_strstr` (libsystem_c alias nodes) + `___chkstk_darwin` (libcompiler_rt) — without them the next boot dies on bind #2 `_memmove`; census = 537/537 launchd binds resolved on the final volume.
3. **root-v9 sha**: `a427241dfa71e5088ff1f42695d43018798b64294f53254c001cfb519bba0c17` at `/tmp/fleet/n1r/root-v9-candidate.hfs` (28 MiB, fsck clean, 163 files byte-identical to v8, launchd/dyld/plists untouched, v8 pin `77dc3e9f…` intact).
4. **Confidence**: high that the namespace-6 bind wall is dead (binds fully enumerated and resolved host-side); medium on the first post-bind wall — the two known unknowns (dyld-1066 disk-trie alias handling, unslided-at-preferred-address loading) are pre-documented and each has a distinct one-boot signature (closure-region crash vs namespace-6 with a named symbol).
5. **Paths**: `/tmp/fleet/n1r/root-v9-candidate.hfs` · `research/laguna-usb-fix/NDR-RECORD-FIX.md` · `/tmp/fleet/n1r-ndr-report.md` · tooling/evidence `/tmp/n1r-work/`.
