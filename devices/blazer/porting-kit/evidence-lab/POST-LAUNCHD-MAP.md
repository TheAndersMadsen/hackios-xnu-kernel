# POST-LAUNCHD-MAP — what the console shows after the HFS root lands (M1 → M2 → M3), and what each wall looks like on root-v8

Lane: **K5 (post-launchd wall map)** · 2026-10-27 · **host-only** (no device, no boots; image inspected read-only, mount detached, sha re-verified).
Mission: so that when K4's HFS driver lands and root-v8 mounts, no device cycle is wasted re-deriving what the kernel prints on the way up — exact grep-able signatures for M1 (launchd exec) → M2 (dyld loads launchd + deps) → M3 (bind + first daemon), plus the wall table (signature → cause → fix → applies?), the sigtrap transfer verdicts, and the root-v9 decision.

Reads: `USERLAND-READY.md` (Q5's root-v7/v8 audit) + `USERLAND-PATH.md` (M0–M8 ladder) + `../laguna-sigtrap/REPORT.md` §1–§24 + `../laguna-dyld-align/REPORT-N1.md` (N1 dyld-align decode) + the kernel source itself (`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2`, file:line below).

Image inspected: `/tmp/fleet/q5/root-v8-candidate.hfs` **sha-256 `77dc3e9f3e2034812112d45cdac2f882c1f7ae3137c432881992d3b4e3d1c0bc`** — identical before and after inspection. Kernel facts are from the tree K4 builds (`DEVELOPMENT ARM64 BCM2837`), the exact binary the device will run.

---

## 0. Verdict (30 seconds)

- **M1 → M2: root-v8 is COMPLETE — no image work remains before dyld runs.** Every exec-load input (launchd `bcb112ed…`, dyld `a453aa23…`, libSystem.B's 32 re-export targets incl. the v8-restored libdispatch `1c0ddd6c…`, libobjc.A) is on the bytes; fsck/identity/rootown gates were Q5's. **One CONTEXT CORRECTION below (§6): the mission brief's "530 job binaries + xpcproxy present" is FALSE on the bytes** — census is 199 files, `/usr/libexec` is EMPTY, `/sbin/launchd` is the only Mach-O executable. It is dormant (unreachable before the bind wall), so it does not change the v8 verdict.
- **M2 wall in OUR configuration: NONE expected.** The empty cryptex cache is NOT a wall — dyld-1066.8's 15-path cache probe falls through harmlessly on both empty-present and absent dirs (verified against the dyld-1042 source we hold and re-verified as format strings inside the actual 1066.8 binary). N1's mmap-EINVAL wall does NOT transfer (§3.2) — honest applicability: mechanism transfers, fix direction inverts, alignment wall does not.
- **M3 wall: the KNOWN one, by design.** v8's whole point was to restore the v6-proven bind wall: expected death = `Symbol not found: _NDR_record` → namespace 6 → **kernel panic** (this tree panics when PID 1 exits, `kern_exit.c:1667-1671`). The fix is the 2.536 GiB native cache (v3.9 transport), NOT another image edit.
- **root-v9 candidate: NONE.** Nothing M1→M3-relevant is missing from v8; anything else (xpcproxy, Collections.plist, zoneinfo) is either unreachable pre-bind or belongs to the full-root build. Builder pin untouched; `77dc3e9f…` stays the registered candidate.
- **Kernel-side M1→M3: CLEAN.** No kernel service needs userland between `load_init_program` and first-daemon; jetsam arg already set; the only kernel wall is the PID-1-death panic itself — which is also our best diagnostic.

---

## 1. M1 — launchd exec: exact console signatures

Kernel path: `bsdinit_task` → `vm_init_before_launchd()` → `load_init_program()` (`bsd_init.c:1113`) → `init_programs[]` loop (`kern_exec.c:7204-7215`). The build is **DEVELOPMENT**, so the loop tries `/usr/appleinternal/sbin/launchd.development` FIRST, then `/sbin/launchd`.

**SUCCESS (grep these, in this order):**

```
load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development   ← EXPECTED NOISE (ENOENT; kern_exec.c:7206) — NOT a failure
load_init_program: attempting to load /sbin/launchd                                  ← kern_exec.c:7206, then NO failure line, NO panic
```
followed by the ABSENCE of:
- `execdiag: /sbin/launchd: ...` (any execdiag line naming launchd = exec declined),
- `load_init_program: failed loading ... errno N` (`kern_exec.c:7198/7211`),
- `panic: Process 1 exec of /sbin/launchd failed, errno N` (`kern_exec.c:7215`).

**FAILURE signatures and their exact meanings:**

| Console | Meaning | Fix owner |
|---|---|---|
| `execdiag: /sbin/launchd: declined: first page is not a Mach-O image … fsize=29360128 vdata[0..15]= 00000000…` | **mockfs stole the root mount again** — the whole 28 MiB volume served as one synthetic file (the v59 fingerprint) | K4's laguna_hfs must have claimed md0 before mockfs (`hfs: mounted iOSRoot` must appear BEFORE this point); reappearance = HFS driver lost the mount race or `options LAGUNA_HFS` dropped from the build |
| `execdiag: /sbin/launchd: … fsize=541600` + a magic/gate error, or `failed loading … errno 8` | right bytes served but the exec gate declined | re-diagnose exec path (v6-proven mechanics say this should not happen) |
| `failed loading … errno 2` (ENOENT) | HFS lookup of `/sbin/launchd` failed | HFS driver namei issue (K4) |
| panic `Process 1 exec of /sbin/launchd failed, errno N` | both `init_programs[]` entries failed | read N; errno table at `kern_exec.c:7238-7260` |

There is NO explicit success print at M1 — success is "the two `attempting` lines, then silence or the next milestone". The immediate discriminator of what happened next is M2/M3's output below.

## 2. M2 — dyld loads launchd + deps: what the kernel can and cannot show

dyld runs **inside PID 1 before launchd's main()**. Two evidence facts govern everything here:

1. **dyld's own stderr is INVISIBLE.** At exec PID 1's fd table is empty; dyld writes its load/bind error to fd 2 → EBADF → lost. The deviceroot "stderr→/dev/console" lever does NOT cover this case (it wires spawned children; nothing wires PID 1 before dyld).
2. **The exit-reason description is the ONLY carrier of dyld's message.** dyld aborts with `OS_REASON_DYLD` (namespace **6**, `reason.h:93`) and a payload string; on death this tree prints it TWICE (`kern_exit.c:1798` and the panic at `:1828`, up to **800 chars**, `reason.h:58`):

```
pid 1 exited -- exit reason namespace 6 subcode 0x…, description <dyld's message>
panic(cpu 0 caller …): initproc failed to start -- exit reason namespace 6 subcode 0x… description: <dyld's message>
```

(The `initproc failed to start` variant is what you get while `kern.initproc_spawned` is still 0 — that sysctl is set BY launchd once it reaches main, `kern_exit.c:1306-1320`; it is itself a grep-able M3 discriminator: `initproc exited` = launchd reached main, `initproc failed to start` = died in dyld.)

**Kernel lines that may legitimately appear during M2 (all non-fatal, do not misread):**

| Line | Source | Meaning |
|---|---|---|
| `minisandbox: request N (first seen from launchd[1]): …` | `kern_minimac.c:234-236` | the Sandbox stand-in answering launchd's sandbox ops — a GOOD sign (userland is executing syscalls), once per op number |
| `CODE SIGNING: cs_invalid_page(0x…): p=1[launchd] … allowing (remove VALID)` | `kern_cs.c:258/300` (verbose path) | a page invalidated but allowed under the permissive stand-in posture (`miniamfi_vnode_check_signature` grants CS_SIGNED/CS_PLATFORM_BINARY, `kern_minimac.c:46-62`) — informational |
| `execdiag:` lines for paths OTHER than /sbin/launchd | K1 | would mean something is already exec'ing — unexpected before M3; investigate |
| nothing from shared_region | `vm_shared_region.c` prints only on race/failure | with no cache, launchd's `shared_region_check_np` returns un-mapped silently — correct |

### 2.1 The empty-cryptex-cache question — answered: NOT a wall

- **On the bytes (verified this rotation):** `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld` and the `App` twin exist and are EMPTY; `/private/preboot` does NOT exist at all; `/System/Library/Caches` does not exist (R2 removed the legacy dirs).
- **In dyld:** the cache discovery is `forEachInstalledCacheWithSystemPath` — 15 candidate dirs, each `realpath()` → skip if missing, `opendir()` → scan for DT_REG entries → **an empty dir yields no cache and dyld proceeds cache-less** (source-verified in the dyld-1042 tree we hold, `ref/dyld-1042.1/common/ProcessAtlas.cpp:865-885`). The v8 dyld-1066.8 binary carries the identical mechanism and path set as format strings (`/System/Library/Caches/com.apple.dyld/`, `/private/preboot/Cryptexes/OS/`, `/System/Volumes/Preboot/Cryptexes/OS/`, `/System/Cryptexes/OS`, `grafting boot cryptexes`, `boot-arg forced cryptex fallback` — Q5 verified the version strings; the cache-path strings re-verified here).
- Verdict: **dyld does not need the cache dir to contain anything, nor `/private/preboot` to exist.** Missing/empty → on-disk closure. The only cache-related wall remains the ABSENCE of the cache (the closure dead-end, M3).

### 2.2 N1 (dyld-align) applicability — honest verdict: mechanism transfers, fix direction INVERTED, alignment wall does NOT apply

| N1 finding | Transfers to root-v8? | Why (one line) |
|---|---|---|
| `isAlwaysOverridablePath` = exactly `/usr/lib/system/libdispatch.dylib` (DyldProcessConfig) | **YES (mechanism)** | dyld-1066.8 binary carries the same machinery as strings: `overridesDylibInCache`, `found: dylib-from-disk-to-override-cache`, and the literal path `/usr/lib/system/libdispatch.dylib` (verified on the bytes this rotation) |
| mmap EINVAL wall = split dylib with unaligned cache-global vmaddrs **while a full cache is present** | **NO** | v8 has NO cache (the precondition is absent), and v8's libdispatch is the v6-carved bytes that were **device-proven past mmap** (v6 died at BIND, i.e. the mmap succeeded) — the dsc-extractor unaligned-file family is not in this image |
| Fix = DELETE the disk libdispatch (cache serves it) | **INVERTED** | N1's world had a full customer cache to serve libdispatch; v8 has none, so the file MUST exist on disk (that is exactly the v7→v8 delta: without it, death is `Library not loaded` at LOAD — §3) |
| Empty/absent cache dir is harmless | **YES** | same fall-through verified above (§2.1) |
| Native multi-file subcache open/mmap path works as-is | **N-A for v8** | no cache to map; rides the v3.9 acceptance check when the 2.536 GiB cache lands (Q5 checklist item 11 already hands it there) |

Caveat kept honest: N1's source-level decode was dyld-**1042**; root-v8 runs dyld-**1066.8**. Every transfer above is re-anchored to strings inside the actual 1066.8 binary — but the empty-cache fall-through itself is source-verified only on 1042. This is the map's **biggest unknown** (§7): if 1066.8 changed the no-cache boot path, the first device boot after M2 tells us immediately via the §3 signatures (both candidate deaths are distinct, one-line, and named).

## 3. M3 — bind + first daemon

### 3.1 The expected v8 death: the KNOWN bind wall (by design)

Root-v8's closure = launchd + libSystem.B (32 re-exports, all present) + 36 carved dylibs incl. the v6 libdispatch (exports exactly 1 symbol, **not** `_NDR_record`). With no cache, dyld binds `_NDR_record` (referenced by the closure's MIG-generated code) → abort:

```
pid 1 exited -- exit reason namespace 6 subcode 0x…, description Symbol not found: _NDR_record
panic(cpu 0 caller …): initproc failed to start -- exit reason namespace 6 subcode 0x… description: Symbol not found: _NDR_record
```

This is the documented, precedent-named dead end — **v8 restored it deliberately** (known signature + known evidence trail + one extra diagnostic stage per boot: load-OK → bind-fail). Do NOT spend triage time on it; the fix is the 2.536 GiB native cache via the v3.9 transport, not an image edit.

**A/B discriminator vs v7:** if the device shows `Library not loaded: /usr/lib/system/libdispatch.dylib` in that panic line instead, the boot used **v7 bytes, not v8** (sha check the payload — Q5 §4). That single string difference is the cleanest possible confirmation that the v8 re-pin took effect.

### 3.2 First daemon (only reachable past the bind wall — future full-root world, kept for completeness)

- launchd reads the on-disk D2 `launchd.plist` (requires `launchd_unsecure_cache=1`, in the payload cmdline): **530 jobs / 1,656 MachServices / 123 eager** (RunAtLoad/KeepAlive incl. SpringBoard, amfid, DumpPanic, PosterBoard angels).
- Every job spawn goes through **xpcproxy** (launchd strings verified on-image: `service will always use xpcproxy`, `posix_spawn(%s) failed`, `abandoned xpcproxy before exec`). **xpcproxy is ABSENT from v8** and all 530 program paths are absent → the first-daemon phase would be a launchd-logged ENOENT spawn-fail storm (launchd throttles restarts; **launchd itself survives** — only PID 1 death panics). Dormant on v8 (unreachable); the full root ships all of it in-image.
- `kern.initproc_spawned` flipping to 1 is the earliest post-main signal; job-spawn lines from launchd itself need the console/oslog path — do not count on reading them until logd or a serial tee exists.

## 4. Kernel-side M1→M3 audit: no kernel walls expected

- `bsdinit_task` does `vm_init_before_launchd()` → `load_init_program()` → done (`bsd_init.c:1101-1115`). Nothing between root mount and exec needs userland; the v59 evidence already reaches the exec attempt.
- `memorystatus_init_at_boot_snapshot()` runs inside `load_init_program` (`kern_exec.c:7170`) — `kern.jetsam_critical_threshold=1` is already in the payload cmdline; no wall.
- After exec, kernel threads continue independently (IKit matching, pageout, devfs). **No kernel service waits on userland before M3**; no Mini* personality is consulted by dyld/launchd at M2/M3 (those are M4+ walls, already mapped in USERLAND-PATH.md).
- The ONE kernel-side behavior to expect: **PID 1 exit = panic** (`proc_should_trigger_panic`, `kern_exit.c:1667-1671`: "Always panic for launchd or equivalents"), preceded by the exit-reason print (§2). The DEVELOPMENT coredump attempt for initproc fails harmlessly. Consequence: every userland death before M3 completion shows up as a named panic with dyld's own message string — the map above is readable from the panic line alone.
- Console routing: kernel printf → serial (`serial=3`, proven); userland console writes only exist after launchd opens `/dev/console` (devfs, v6-proven). Do not expect launchd's own log lines before then.

## 5. Sigtrap-lane walls vs root-v8 — APPLIES / N-A verdicts with byte-level evidence

All five sigtrap walls are **userland-binary walls** and root-v8 has **no userland binaries** — they are N-A for v8 and ride the full-root (v3.9) lane. Evidence from this rotation's mount:

| Sigtrap wall (source) | Verdict for root-v8 | Evidence (one line) |
|---|---|---|
| **lsd self-deadlock** (bridge 0121 freeze; LS-rebuild 25–47 min budget, §22.3) | **N-A for v8 / APPLIES to v3.9** | `/usr/libexec` EMPTY (no lsd binary; `com.apple.lsd` + `com.apple.lsd.system` are declared in the D2 plist — dormant); the ≥30-min first-boot budget already handed to the full-root lane (USERLAND-PATH M1 item o) |
| **logd backpressure** (`Flushed 222 logs [limit: 20]`, §20) | **N-A for v8 / APPLIES to v3.9** | logd STORES present (`private/var/db/diagnostics{,/timesync,/.migrated}` + `uuidtext`, 272:272) but no `/usr/libexec/logd` binary — nothing to backpressure until the full root |
| **Collections.plist** (PosterBoard 17 deterministic crashes; generic-plist fix green, §22.1–22.4) | **N-A for v8 / APPLIES to v3.9 build** | no `/Library/Wallpaper` at all in v8 (path absent), no PosterBoard binary; the one-file generic `Collections.plist` fix is a full-root BUILD step (never edit pinned images) |
| **mobileactivationd 112-crash loop** (mactfix plist delete, §22.1) | **N-A for v8 (dormant declaration) / lesson rides v3.9** | `com.apple.mobileactivationd` IS declared in the D2 plist (program `/usr/libexec/mobileactivationd`) but the binary is absent → ENOENT spawn-fail, not a crash loop; never edit the D2 plist+sig pair — prune at image build (proven mactfix) |
| **mapdb stubs** (lsd rebuild gate, §22.3) | **N-A** | 0 hits for mapd/mapdb among the 530 jobs (checked this rotation); mapdb lives as a declared MachService — the proven v6 contract; with no lsd binary there is nothing to rebuild |
| launchd strips `DYLD_*` from non-root jobs (§7d cycles 9–11) | **APPLIES (policy knowledge, kernel carrier ready)** | verified in launchd strings on-image; THIS tree's answer already exists kernel-side: `miniamfi` 0x5a → ALLOW_ALL 0xff (`kern_minimac.c:70-71,90-111`) — injection into jobs needs `kern.exec_inject`-class wiring, not plist env |
| Missing Mini* service `brk`-traps (MKB/AppleKeyStore §4–§6) | **APPLIES later / kernel-lane (M4+)** | userland half absent in v8; when full-root daemons run, missing-service traps are IOKit-personality work (USERLAND-PATH M4) |
| WRM/commcenter suspended-release, analyticsd SIGSEGV, cryptexd churn (§22.5) | **N-A / churn list rides v3.9** | declared (WirelessRadioManagerd, analyticsd, `com.apple.security.cryptexd`), binaries absent |

## 6. The context correction (flagged, not blocking)

The rotation brief states root-v8 contains "530 job binaries + xpcproxy present". **On the bytes this is false**: census is **199 files** (198 v7 + the one-file libdispatch delta), `/usr/libexec` is EMPTY (no xpcproxy), `/sbin` holds only `launchd`, `/Applications` and `/System/Library/CoreServices` are empty, and a path scan of every declared program prefix finds zero Mach-O executables outside `/usr/lib`. Q5's audit already recorded this ("all 530 program paths absent … xpcproxy absent") — the brief compressed it wrong. Practical impact: **none before the bind wall** (§3.1), but do not plan any M3 first-daemon expectations against root-v8, and do not "fix" this by adding binaries to v8 — that would be a many-variable change violating the single-delta discipline for zero pre-M3 effect.

## 7. Wall table — signature → cause → fix → applies?

| # | Console signature | Cause | Fix | Applies to root-v8? |
|---|---|---|---|---|
| W1 | `execdiag: /sbin/launchd: declined … fsize=29360128 … 00000000…` | mockfs steals md0 (HFS driver missing/lost race) | K4: laguna_hfs build-green (`hfs: mounted iOSRoot` must precede exec) | **YES — M1; must be GONE**; reappearance = mount race or dropped `options LAGUNA_HFS` |
| W2 | `load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development` | DEVELOPMENT `init_programs[0]` noise | NONE — expected line, ENOENT | **YES — do not misread as failure** |
| W3 | `panic: Process 1 exec of /sbin/launchd failed, errno N` | exec failed | read N (errno table `kern_exec.c:7238`); errno 2 → HFS namei, 8 → Mach-O gate | NO (v6 mechanics proven; if seen, K4 re-verify) |
| W4 | quiet M1 → `pid 1 exited -- namespace 6 … Library not loaded: /usr/lib/system/libdispatch.dylib` + panic | **v7** bytes booted (re-export target absent, cache empty) | boot v8; verify payload sha `77dc3e9f…` | NO — its appearance proves the WRONG image |
| W5 | quiet M1 → `pid 1 exited -- namespace 6 … Symbol not found: _NDR_record` + `initproc failed to start` panic | closure bind wall (carved libdispatch 1-export; closure dead-end) | 2.536 GiB native cache via **v3.9 transport** — NOT an image edit | **YES — v8's EXPECTED M3 terminal wall (known, do not re-derive)** |
| W6 | mmap-EINVAL / `dylib-from-disk-to-override-cache-error` | split dylib unaligned vmaddrs (N1 class, cache-present world) | N/A | NO — no cache; libdispatch = v6 bytes proven past mmap |
| W7 | `minisandbox: request N (first seen from launchd[1])` | stand-in Sandbox policy answering | NONE — GOOD sign | YES (M2, non-fatal) |
| W8 | `CODE SIGNING: cs_invalid_page … allowing` | permissive posture, page invalidated | NONE — non-fatal | YES (M2, possible) |
| W9 | M3 silent, no daemon lines, no panic (launchd alive) | bind passed(!), jobs can't spawn: xpcproxy + 530 binaries absent | full root (v3.9) ships them; `kern.initproc_spawned` sysctl = reached-main probe | DORMANT on v8 (unreachable pre-bind); the ONLY v8 outcome that would surprise this map |
| W10 | `initproc exited -- namespace 7 …` panic (not "failed to start") | launchd reached main, died later (lsd/logd/plist-class) | sigtrap transfer table (§5) | only on full root |

## 8. root-v9 decision: NONE (registered decision, builder pin untouched)

1. Nothing M1→M3-relevant is missing from v8: the exec-load closure is complete (Q5 gates + this rotation's re-verification), the cache question is a non-wall, the bind wall's only real fix is the v3.9 cache transport.
2. Candidates considered and REJECTED as v9 deltas: xpcproxy / job binaries (unreachable pre-bind, many-variable), generic `Collections.plist` (full-root build item), zoneinfo content (cosmetic, post-M3; the three dangling symlinks stay harmless), per-job launchd overrides in `/private/var/db/com.apple.xpc.launchd/config` (post-M3 lever only; Q5 §5 already reserved it).
3. Verdicts ride existing lanes: v3.9 full-root lane inherits sigtrap items 1/5/10 + the N1 cache-alignment acceptance check (Q5 checklist item 11) + mactfix-at-build; Q2 owns the AMFI posture (this tree's `miniamfi` is already allow-all, nothing to re-verify at M2/M3).

---

## Summary (5 lines)

1. **Top M2 wall: NONE expected** — the empty cryptex cache is verified harmless (dyld-1066.8's 15-dir probe falls through on empty-present/absent alike; source-verified on dyld-1042 + re-anchored as strings in the actual 1066.8 binary), and N1's mmap-EINVAL wall does NOT transfer (no cache in v8; v8's libdispatch is the v6 bytes device-proven past mmap) — N1's override mechanism transfers but its delete-fix direction INVERTS for a cache-less root (file must exist; its absence = v7's `Library not loaded` load-class death, the W4 A/B discriminator against v8).
2. **Top M3 wall: the KNOWN bind wall by design** — `Symbol not found: _NDR_record` → namespace 6 → this tree PANICS on PID-1 death (`kern_exit.c:1667`), panic line carries dyld's message (800-char description; dyld stderr itself is invisible — PID 1 has no fds) → fix is the 2.536 GiB native cache via v3.9 transport, NOT an image edit; root-v9 = **NONE** (no in-image delta can help; builder pin untouched, `77dc3e9f…` unchanged after inspection).
3. **Biggest unknown**: dyld-1066.8's no-cache boot path is source-verified only on dyld-1042 (1066.8 verified by strings) — if 1066.8 changed the no-cache fallback, W4/W5's distinct one-line signatures identify it on the first boot with zero triage cost.
4. All five sigtrap walls (lsd/logd/Collections/mapdb/mobileactivationd) are **N-A for root-v8** (verified: `/usr/libexec` EMPTY, no `/Library/Wallpaper`, 0 mapdb jobs, 199-file census) and ride the v3.9 full-root lane; the brief's "530 job binaries + xpcproxy present" is FALSE on the bytes (dormant, flagged §6); kernel-side M1→M3 is clean (jetsam arg already set; no service waits on userland).
5. **Paths**: map → `research/laguna-usb-fix/POST-LAUNCHD-MAP.md`; compact report → `/tmp/fleet/k5-postlaunchd-report.md`; coordination line → `/tmp/fleet/COORDINATION.md`; image inspected read-only at `/tmp/fleet/q5/root-v8-candidate.hfs` (sha `77dc3e9f3e2034812112d45cdac2f882c1f7ae3137c432881992d3b4e3d1c0bc` re-verified stable, mount detached).
