# USERLAND-READY — root-v7 M0–M3 audit (the moment the kernel reaches root mount)

Lane: **Q5 (userland readiness)** · 2026-10-27 · **host-only** (no device, no boots; mounts read-only, scratch-copy edits only).
Mission: audit root-v7 (`root-upgrade/root-v7.hfs`, 28 MiB, pin **`72bf50b9…`**) so that when the v55+ kernel passes NVRAM and the later walls, root mount → launchd exec → dyld actually starts userland; produce the exact gap list; build a root-v8 **candidate** (builder pin untouched — coordinator decides).

Reads: `ROOT-INTEGRATION.md` (P3, WITH_ROOT7=1), `USERLAND-PATH.md` (B4 ladder), `root-upgrade/BUILD-ROOT-V7.md` (R2 scope), `../laguna-sigtrap/REPORT.md` §1–§24 (QEMU userland walls).

---

## 0. Verdict (30 seconds)

- **M0, M1, M2: PRESENT and self-consistent.** launchd (541,600 B) + foundation dyld (`a453aa23…`, 922,912 B) + D2 `launchd.plist` (530 jobs) + 143 identity files + var/keybag/LS-seed skeleton all check out byte-level; v6-proven mount/exec mechanics apply unchanged.
- **M3: BROKEN — and one break is *not* the documented one.** Besides the by-design absent native cache, v7 deleted the **only** missing re-export target of `libSystem.B.dylib` (carved `libdispatch.dylib`, 786,363 B). With the cryptex cache empty, dyld-1066 cannot resolve `/usr/lib/system/libdispatch.dylib` during **launchd's own load** → expected death is a *load* error (`Library not loaded`), **earlier** than the v6-proven `Symbol not found: _NDR_record` *bind* failure every runbook describes. Same milestone (M3 fails), different signature — a triage trap unless fixed or documented.
- **root-v8 candidate built** (scratch copy, single-variable delta = restore carved libdispatch): `77dc3e9f3e2034812112d45cdac2f882c1f7ae3137c432881992d3b4e3d1c0bc`, 29,360,128 B (28 MiB exact — shim window math unchanged). fsck clean, v7's 195-target identity manifest + 21-target rootown re-checked PASS, delta census = exactly one file. **Builder pin NOT changed** — that is the coordinator's call after validation (device A/B v7 vs v8, or a QEMU late-chain run).
- No other cheap, safe, M0–M3-relevant gap exists in-image; everything else is (a) the 2.536 GiB native cache transport (v3.9) or (b) post-dyld spawn files that a recovery root deliberately lacks.

---

## 1. Method and pinned-image discipline

- Pinned v7 mounted **read-only** (`hdiutil attach -readonly -owners off`); `com.apple.FinderInfo` xattr added for the raw-image probe (same pattern as `verify-v7.sh`), **sha-256 re-verified `72bf50b9…` unchanged after every phase**. All mounts detached at the end.
- Raw-catalog reads (uid/gid/mode) via the kit's `iosroot/hfs/tools/hfs-fixmeta.py` parser (the same engine behind `hfs-identity-meta.py` / `rootown-v7.py`) — independent of mount-owner mapping.
- v6 reference bytes: `root-upgrade/tools/extract-v6.py` re-run (vendor_boot `blazer-devicerun-primary-vendor_boot-avb-v6.img` → `b17e7fd9…`, exact expected hash); extracted `work/` removed again afterwards (recreatable per BUILD-ROOT-V7.md §5).
- Binaries inspected with `otool`/`strings`/export-trie parse on the read-only mount; no file executed.
- Scratch candidate: byte copy of the pinned image → one file added on a writable attach → kit identity tool (`--apply`, 1 target) → fsck → verify → hash. Candidate lives at `/tmp/fleet/q5/root-v8-candidate.hfs` with `v8-onefile-manifest.json`, `v8-delta.txt`, `root-v7-filelist.txt` beside it.

## 2. Inventory of root-v7 (verified on the bytes this rotation)

| Class | Content | State |
|---|---|---|
| Exec targets | `/sbin/launchd` 541,600 B (sha `bcb112ed…`), PerformAlways-patched D2 lineage | PRESENT; only exec target by design |
| Dyld | `/usr/lib/dyld` 922,912 B, foundation `a453aa23…` (dyld-1066.8, cryptex-aware: strings for `/System/Cryptexes/OS`, `/private/preboot/Cryptexes/OS/`, `boot-arg forced cryptex fallback`, `ignition_halt_after`, `enable-dylibs-to-override-cache`) | PRESENT; matches C1 foundation cache pairing |
| launchd config | `/System/Library/xpc/launchd.plist` 795,217 B (sha `7b41fa30…`) + `launchd.plist.sig` 19,006 B (proven v6 pair — treat as immutable) + `extensions.plist` 104,731 B; launchd strings confirm it reads the on-disk `launchd.plist` path | PRESENT |
| Job graph | **530 declared jobs** (LaunchDaemons/LaunchAngels/Nano*), **1,656 MachServices**, **123 eager** (RunAtLoad/KeepAlive — incl. `com.apple.SpringBoard`, `amfid`, `DumpPanic`, `misd`, `PerfPowerServicesExtended`, `OTACrashCopier`, `ClarityBoard`, 10 LaunchAngels incl. `PosterBoard`); 68 jobs carry `EnvironmentVariables` (none DYLD_*); **all 530 program paths absent** from the image | EXPECTED (recovery root); only reachable post-dyld |
| Closure dylibs | 35 carved dylibs in `/usr/lib/system/` + `libSystem.B.dylib`, `libc++.1.dylib`, `libc++abi.dylib`, `libobjc.A.dylib` — all raw `0:0 0644` | PRESENT **minus libdispatch** (see §4) |
| Native cache | `/System/Cryptexes/{OS,App}/System/Library/Caches/com.apple.dyld` — present, **empty by design**; no `/System/Library/Caches` at all (R2 removes the legacy ignition-only dirs) | BY DESIGN; cache = 2.536 GiB via v3.9 |
| Identity | 143 dslocal files (`nodes/Default/{users(41),groups(99),config}`, Default 0700, records 0600) + `private/etc/{passwd 0644, master.passwd 0600, group 0644}` — 22 UserName / 14 GroupName coverage (root 0:0, mobile 501:501, `_securityd` 64:64, `_logd` 272:272, `_backboardd`, …) | PRESENT |
| Pre-seeds | `purplebuddy.plist` = **SetupDone=True, SetupFinishedAllSteps=True, ForceNoBuddy=True**; `springboard.plist` = **BKNoWatchdogs=True** (+FBLaunchWatchdogExceptions); `migration.plist` (LastSystemVersion 20H392); `data_ark.plist` (container shell); `systembag.kb` keybag; LS seeds `com.apple.MobileCoreServices/{com.apple.LaunchServices.plist, com.apple.launchservices.secure.plist}` = empty dicts; `/private/var/db/lsd/{com.apple.lsdidentifiers,com.apple.lsdschemes}.plist` = empty dicts | PRESENT |
| Var/log skeleton | `var/db/diagnostics{,/timesync,/.migrated}` + `var/db/uuidtext` (all 272:272), `var/preferences/Logging/{Processes,Subsystems}`, `var/log/com.apple.xpc.launchd`, `/private/var/db/com.apple.xpc.launchd/config` (empty override dir), `var/{root,empty,mobile,vm,run,tmp,folders…}` | PRESENT |
| Console/tty | `/dev` empty (devfs supplied by kernel at boot — v6-proven); no `/etc/ttys` (iOS launchd has no getty — correct) | PRESENT (N/A) |
| Symlinks (6) | `etc→private/etc`, `tmp→private/var/tmp`, `var→private/var`, `Developer→private/var/db/xcode`, `…timezone/localtime→…/US/Pacific`, `…timezone/zoneinfo→/usr/share/zoneinfo` | PRESENT; last three **dangle** (`/usr/share` empty, no zoneinfo content, no `var/db/xcode`) — harmless at M2, note for M3+ daemons that read localtime |
| Trust cache / AMFI files | none anywhere in the image | NONE by design — code-signing posture is kernel-side (141-bridge laguna_amfi permissive, v6-proven through dyld death); coordinate with Q2 for `amfi-only-platform-code` planning |
| Census | 198 files / 156 dirs / 6 symlinks (find census matches R2's raw-catalog numbers exactly) | CONFIRMED |

## 3. M0–M3 audit (USERLAND-PATH ladder)

| Milestone | Exit criterion | Verdict in root-v7 | Detail / exact file for any fix |
|---|---|---|---|
| **M0** kernel alive, ramdisk present | ADT `RAMDisk` + `rd=md0` | **PRESENT** — no root content needed; image is fsck-clean journaled HFS+, `HX`@1024, volname `iOSRoot`; payload embedding re-verified by P3 (`verify-payload.py` ALL PASS) | Non-image risk stays P3's window bump (`mem_size 0x0A400000`) — device-validate |
| **M1** root mounted | `BSD root: md0`, `hfs: mounted iOSRoot` (RW via `rootrw=1`) | **PRESENT** — same volume class as the device-proven v6 mount; journal replay on RW mount is standard | none |
| **M2** launchd exec | PID 1 exec'd, first launchd/Sandbox lines | **PRESENT** — v7 fixes v6's exec defect (`LC_LOAD_DYLINKER` → `/usr/lib/dyld` now exists); D2 plist+sig pair on-disk (launchd_unsecure_cache path); identity records cover every cached launchd identity; cmdline tokens correct in builder | none in-image |
| **M3** dyld completes, jobs spawn | launchd's dyld finishes; first processes | **BROKEN (2 breaks, 1 not-by-the-book)** — see §4 | ① `/usr/lib/system/libdispatch.dylib` (v8 candidate restores it); ② native cache `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld/*` (2.536 GiB — v3.9 transport, not an image fix) |

Post-dyld dormancy (only matters after M3 ever completes; all by-design for a recovery root, listed so nobody is surprised): `/usr/libexec/xpcproxy` absent (launchd's spawn helper — every job exec goes through it), all 530 job binaries absent (123 eager → immediate spawn-fail storm incl. SpringBoard/amfid), no configd/logd/lsd/securityd binaries, `/usr/share/zoneinfo` empty (localtime dangles).

## 4. THE FINDING — v7's M3 death would come earlier and different than documented

Byte-level facts (read from the image this rotation):

1. `sbin/launchd` imports exactly `libSystem.B.dylib` + `libobjc.A.dylib`; `LC_LOAD_DYLINKER` = `/usr/lib/dyld`. ✓
2. `usr/lib/libSystem.B.dylib` carries **32 `LC_REEXPORT_DYLIB`** commands → 31 of the targets exist in `usr/lib/system/`; **exactly one is missing: `/usr/lib/system/libdispatch.dylib`** — the sole deletion R2 made (BUILD-ROOT-V7.md §4).
3. The cryptex cache directory is empty, so dyld-1066 falls back to on-disk dylibs ("boot-arg forced cryptex/rootfs fallback" strings) — and a missing re-export target is fatal during **closure build / launchd's own load**: expected signature `dyld: Library not loaded: /usr/lib/system/libdispatch.dylib … no such image` → PID 1 never runs an instruction → kernel-side PID-1 death.
4. R2's stated expectation (ROOT-INTEGRATION.md §7) and every runbook describe the M3 wall as the v6-proven **namespace-6 `Symbol not found: _NDR_record`** — that signature requires libdispatch to LOAD first (v6 shipped the carved one; its export trie contains exactly **1** symbol and **not** `_NDR_record`, which is precisely why v6 died at bind). **v7-as-pinned cannot reach that bind** — the fleet would see a different, earlier death and could burn triage time re-deriving M3 from scratch.
5. Restoring the carved libdispatch is **cache-safe for v3.9**: dyld-1066 is cache-first; on-disk dylibs override cache entries **only** when `/System/Library/Caches/com.apple.dyld/enable-dylibs-to-override-cache` exists (dyld strings prove the gate; v7 intentionally has no `/System/Library/Caches` and the v3.9 full root ships its own cache). The carved dylib does not complete M3 either — the closure stays the precedent-named dead end — but it restores the **known** wall, the known evidence trail, and one more diagnostic stage (load OK → bind fail) per boot.

## 5. root-v8 candidate (registered; pin unchanged)

| Item | Value |
|---|---|
| File | `/tmp/fleet/q5/root-v8-candidate.hfs` (28 MiB exact — shim admission/window math identical) |
| SHA-256 | **`77dc3e9f3e2034812112d45cdac2f882c1f7ae3137c432881992d3b4e3d1c0bc`** |
| Delta vs v7 | **exactly one path**: `usr/lib/system/libdispatch.dylib` added, byte-identical to the v6-proven carved dylib (786,363 B, sha `1c0ddd6c…`), raw catalog `0:0 0644` matching its 35 siblings |
| Gates (all re-run on the candidate) | `fsck_hfs -fn` → "iOSRoot appears to be OK"; kit identity manifest (195 targets) → 0 non-conforming; `rootown-v7.py` (21 targets) → 0 nonconforming; owners-off census diff vs pinned v7 → exactly 1 line; launchd/dyld/plist shas unchanged (`bcb112ed…` / `a453aa23…` / `7b41fa30…`); size = 29,360,128 B |
| Pinned v7 | **`72bf50b9…` verified unchanged** — builder pin untouched |

Why this and nothing else: single-variable discipline (sigtrap lane method) — one delta per candidate so a device A/B (v7 vs v8) attributes cleanly. Deliberately NOT done: editing `launchd.plist` (D2-signed proven pair; the sig's scheme is unknown — never edit without regenerating), adding Collections.plist/wallpaper files (no PosterBoard in this root), per-job override plists in `/private/var/db/com.apple.xpc.launchd/config` (launchd has the machinery per its strings — a cheap *post-M3* lever if a specific missing-binary job storm needs silencing; not needed before dyld works).

Reproduction (host-only, from repo root):
```bash
python3 research/laguna-usb-fix/root-upgrade/tools/extract-v6.py        # v6 bytes (b17e7fd9…)
# mount v6 read-only, cp usr/lib/system/libdispatch.dylib out; then:
cp research/laguna-usb-fix/root-upgrade/root-v7.hfs /tmp/fleet/q5/root-v8-candidate.hfs
# writable-attach the copy, cp libdispatch into usr/lib/system/, detach,
python3 research/laguna-dslocal/tools/hfs-identity-meta.py /tmp/fleet/q5/root-v8-candidate.hfs \
  --manifest /tmp/fleet/q5/v8-onefile-manifest.json --apply            # 0:0 + mode 0644
python3 research/laguna-usb-fix/root-upgrade/tools/rootown-v7.py /tmp/fleet/q5/root-v8-candidate.hfs
# fsck + census + hash (full gate list in §5 above)
```

**Coordinator decision asked:** adopt `77dc3e9f…` as the WITH_ROOT7 pin (re-pin + Q4 rebuild) or keep `72bf50b9…` and accept the `Library not loaded` M3 signature on device. If kept, correct ROOT-INTEGRATION.md §7's prediction accordingly (P3-owned doc — flagged, not edited by Q5).

## 6. sigtrap transfer checklist (REPORT.md §1–§24 → this RAMDisk root)

| # | Lesson (source) | Verdict for root-v7/v8 | Why (one line) |
|---|---|---|---|
| 1 | Generic `Collections.plist` (§22.1–22.4: PosterBoard 17 deterministic crashes → fix green) | **N-A** | No PosterBoard/SpringBoard binary in v7; the one-file fix belongs to the **v3.9 full root** build — hand to that lane |
| 2 | mactfix md0 rebuild (delete `com.apple.mobileactivationd.plist`) (§22.1) | **APPLIES (dormant, do-not-edit)** | mobileactivationd is declared on-demand in v7's signed graph — it only spawn-fails when looked up (post-M3); never edit the D2 plist+sig pair; per-job launchd override dir is the cheap lever if ever needed |
| 3 | lsdb seeds = `MobileCoreServices/{secure,LS}.plist` engage LS bootstrap (§2, iter35) | **APPLIES — already present** | v7 carries both as empty dicts (bridge-proven shape); keep byte-identical |
| 4 | mapdb stubs / lsd rebuild gates SpringBoard; 25–47 min first LS rebuild (§22.3) | **N-A now / APPLIES later** | No lsd binary in v7 (mapdb stays a declared MachService — proven v6 contract); the rebuild-budget lesson rides the full root + USERLAND-PATH item o (≥30 min first boot) |
| 5 | logd posture: logd ON works when kernel serves it; backpressure "Flushed 222 logs [limit: 20]" (§20; felix j) | **N-A for v7 / APPLIES for full root** | v7 has logd stores (272:272) but no logd binary; for the full root keep logd ON and budget the backpressure signature on slow boots |
| 6 | launchd strips `DYLD_*` from non-root jobs → wrapper-exec or `kern.exec_inject` is the only carrier (§7d, cycles 9–11) | **APPLIES (kernel/policy knowledge)** | Any future injection into v7 jobs must not use plist env; needs the kernel-side AMFI 0x5a answer — Q2 coordination |
| 7 | Missing Mini* kernel stand-ins → `brk`-trap class (MobileKeyBag asserts on missing AppleKeyStore) (§4–§6) | **APPLIES as triage knowledge** | Root-side nothing to add; when first daemons run against the 141 mach, missing-service traps are kernel-lane (Q1/Q2) work, not image work |
| 8 | FBS display-config assert / SpringBoard display walls (§6, §7a) | **N-A** | No SpringBoard binary in this root |
| 9 | WRM/commcenter suspended-release XPC misuse — server stubs can't fix client bugs (rotation 9) | **N-A** | No WRM/ATCommand binaries here; churn-budgeting lesson for the full root |
| 10 | analyticsd SIGSEGV / cryptexd churn as next mactfix targets (§22.5) | **N-A** | Binaries absent; carry the list to the full-image lane |
| 11 | N1 dyld-align lesson (unaligned cache-global vmaddrs → mmap EINVAL residue) | **N-A now, checklist for v3.9** | Cache dir is empty by design; when the 2.536 GiB foundation cache arrives, validate cache alignment + `ignition_level=1` cryptex selection (dyld a453aa23… is the proven cache pair) before first boot |
| 12 | Load-command fix / "VERDICT: PASS" host validation before staging any dylib (rotation 5/7) | **APPLIES (method)** | Honored: v8's only added dylib is byte-exact v6 device-proven, not newly built |
| 13 | `apfsck -cw` mutates images; hash-before/after discipline (§15) | **APPLIES (method)** | Honored: pinned v7 re-hashed stable through every phase; candidate hashed post-build |
| 14 | Single-variable boot attribution (sigtrap cycles) | **APPLIES** | v8 = exactly one delta; recommend device A/B v7-then-v8 |

## 7. Gap list (host-side, actionable now → later)

**Now (this wave):**
1. ~~Restore carved libdispatch~~ → **DONE as root-v8 candidate** `77dc3e9f…` (§5); coordinator re-pin decision pending validation.
2. If v7 is kept: annotate ROOT-INTEGRATION.md §7 + runbooks — M3 signature on v7 = `Library not loaded: /usr/lib/system/libdispatch.dylib`, NOT `_NDR_record` (P3/B4-owned docs; flagged here).
3. QEMU cross-check (Q1/Q4 runner): a WITH_ROOT7 late-chain run should show `md0 bytes=29360128` + launchd exec (P5 ladder ROOT-MOUNT→LAUNCHD-EXEC→DYLD); if the runner reaches dyld, expect the load-error line above — with v8, expect the bind error class instead. (QEMU's known exact-Image limit applies; profile run is the late path.)

**Later / hand-offs:**
4. Q2: kernel AMFI/trust-cache posture for the v7 root (image ships no trust-cache files; permissive posture is v6-proven through dyld death; `amfi-only-platform-code` /chosen planning must not accidentally tighten code-signing before the full root lands).
5. v3.9 full-root lane: inherit sigtrap items 1/5/10 (Collections.plist generic, logd posture + churn list, LS-rebuild ≥30 min budget), the N1 cache-alignment acceptance check (item 11), and mactfix-class plist pruning at image build (never device-side).
6. Cosmetic, zero-risk, non-blocking: `/usr/share/zoneinfo` content + `var/db/xcode` (dangling symlink targets) — only daemons that read localtime care; skip unless a daemon's log names it.

---

## Summary (5 lines)

1. **launchd: PRESENT** (541,600 B `bcb112ed…`) and **dyld: PRESENT** (foundation `a453aa23…`, cryptex-aware dyld-1066.8) — M0/M1/M2 audit clean: D2 plist+sig, 143 identity files, pre-seeds (SetupDone=True, BKNoWatchdogs=True), var/keybag/LS skeleton all verified on the bytes; pinned `72bf50b9…` untouched all rotation.
2. **Biggest M1 gap: none** — root mount needs nothing from the image that v7 lacks (fsck-clean journaled HFS+ `iOSRoot`; only device-side unknown remains P3's +4 MiB window bump).
3. **Biggest M2/M3 gap: v7 deleted the only missing `LC_REEXPORT_DYLIB` target of libSystem.B — carved `libdispatch.dylib` (786,363 B, v6 bytes `1c0ddd6c…`)** — with the cryptex cache empty, dyld dies during launchd's own LOAD (`Library not loaded`) instead of the documented v6 `_NDR_record` BIND wall; M3 also (by design) lacks the 2.536 GiB native cache (v3.9 transport) and all 530 job binaries + xpcproxy (post-dyld dormancy).
4. **root-v8 candidate BUILT: `77dc3e9f3e2034812112d45cdac2f882c1f7ae3137c432881992d3b4e3d1c0bc`** — single-delta restore of libdispatch, 28 MiB exact, fsck + 195-target identity + 21-target rootown + one-line census all PASS; builder pin NOT changed (coordinator call).
5. **Paths:** audit + checklists → `research/laguna-usb-fix/USERLAND-READY.md`; candidate + manifest + census → `/tmp/fleet/q5/root-v8-candidate.hfs` (+ `v8-onefile-manifest.json`, `v8-delta.txt`); compact report → `/tmp/fleet/q5-userland-report.md`; coordination line in `/tmp/fleet/COORDINATION.md`; all hdiutil mounts detached.
