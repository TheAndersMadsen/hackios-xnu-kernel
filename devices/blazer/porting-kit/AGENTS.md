# AGENTS.md — HakiOS field playbook (write this first, read this first)

Session distilled: **2026-10-26 → 2026-10-27, first USB-shim boots (v2) through v61**.
Maintained by lane A1 (retrospective). Every claim below is tied to a named
artifact under `research/` or `/tmp/fleet/` — go read it before you disagree with it.

## 0. Read order (never improvise this)

1. **This file** — protocol, commands, safety rails, lessons.
2. `research/SESSION-STATE.md` — authoritative frontier doc.
3. `research/laguna-usb-fix/ATTEMPTS.md` — attempt ledger (one row per device boot,
   auto-appended by `run-attempt.sh`; prose notes go ABOVE the table, never in it).
4. Latest wave brief `/tmp/fleet/FLEET-BRIEF-<wave>.md` (v55, v56, kwave, mwave …) —
   frontier facts + your lane + which files you own.
5. `/tmp/fleet/COORDINATION.md` — append-only bus; `/tmp/fleet/INDEX.md` — lane roster.
6. The wall doc for whatever you're attacking: INIT-ASSERTION.md, INIT-NVRAM.md,
   ENOEXEC-ROOT-MOUNT.md, NDR-RECORD-FIX.md, POST-LAUNCHD-MAP.md, BRIDGE-EXEC-DIFF.md,
   ROOT-TRANSPORT-AUDIT.md, EVIDENCE-MAP.md (evidence channels), USERLAND-PATH.md (M0–M8).

**/tmp/fleet gets wiped** (T3 restart 2026-10-26 lost the whole bus; later rotations ate
N12 iter57 evidence and the combined enoexec report). Anything you must keep gets a
durable twin under `research/`. Reports are compact in /tmp, full in the workspace.

## 1. Project in one paragraph

Boot Apple XNU (Darwin 22.5.0, `xnu-8796.121.2` "blazer foundation" tree) plus launchd
and userland on a **Pixel 10 Pro (laguna/blazer, Tensor G5)** via **RAM-only
`fastboot boot`** of an AVB-signed shim image. Delivery chain: ABL → teguload shim
(EP0 USB console + TSHIMST2 stamps) → TEGUXNU payload (kernel segments + ADT + HFS+
root ramdisk) → EL2→EL1 drop → XNU → md0 root → launchd → dyld → daemons.
**Nothing is ever flashed. Slot A stays stock.** One host (this Mac), one phone,
many agents, one coordinator who owns the device.

## 2. Current frontier (v61, 2026-10-27 17:34, sha `22a0ae61355ea622…`)

Reader ladder (`attempts/20261027-173349-v61/reader-full.txt`):
**10/10 named rungs reached** — XNU-ENTRY, PE-INTCTRL, PE-TIMER, VM-BOOTSTRAP-DONE,
NVRAM-OK, PE-1695-PASSED, CPU-REG, ROOT-MOUNT, LAUNCHD-EXEC, DYLD (`last=DYLD`,
`next-expected=none`; colloquially "10/11" counting launchd-main as the 11th).
Mach `mach.blazer-r4r7-hfs` (HFS driver + K1 execdiag) + root-v9 `a427241dfa71e5088…`.
The v60 `_NDR_record` bind wall is GONE; death is now inside dyld:

```
[44.190684] shared_region: [1(launchd)] check_np … vm_shared_region_start_address() failed
[44.249177] systrace: launchd[1] stat64 "/usr/local/lib/libobjc-env.dylib" = 2   (ENOENT)
[44.291486] systrace: launchd[1] map_with_linking_np(0x16ed98780, 0x1, 0x16ed987b0, 0x1118) = 22   (EINVAL)
[44.295945] pid 1 exited -- exit reason namespace 6 subcode 0x9, description: indirect symbol has out of range address
[44.296933] coredump … failed: error 2   (/private/var/cores missing on md0)
[45.917493] MACH Reboot → auto-park in fastboot
```

Working diagnosis (M-wave brief, N1r residuals pre-registered in NDR-RECORD-FIX.md §4):
the transplanted export tries remapped trie values per segment, but the dylibs'
**indirect symbol table entries point out of segment range** after the remap —
N1r risk (a) dyld-1066 disk-trie alias semantics / (b) unslided-at-preferred-address
closure loading. Fix lane: F1 → root-v10 + INDIRECT-FIX.md. This tree also panics on
any PID-1 exit (`kern_exit.c:1667`), so pid-1 death = MACH Reboot + park; that panic
is our best diagnostic, not a setback. Every boot auto-parks in fastboot — evidence
is pulled from the park, never by rebooting.

## 3. What ACTUALLY produced progress (do more of this)

| Practice | Concrete proof |
|---|---|
| **Minimal-delta device cycles — one variable per boot** | v53-g2 (G2 ADT nodes only) → v54 (+`/options`) → v55 (NVRAM CHRP props) → v56 (DRAM window 0x0A400000) → v57 (root-v7→v8) → v58 (systrace arm) → v59 (execdiag mach): each boot killed exactly one hypothesis (ATTEMPTS.md backfills; ENOEXEC-ROOT-MOUNT.md §a table). |
| **Build in-kernel diagnostic instruments BEFORE burning cycles** | v58's `systrace=1` printed nothing (TUNABLE with no reader at the decline site); K1 then built **execdiag** — unconditional (no boot-arg to forget), 7 hunks/9 prints covering every `kern_exec.c` decline, 16-line cap — and v59 cracked ENOEXEC in ONE boot (`execdiag: … fsize=29360128 vdata=00000000…` = mockfs-steal fingerprint, exactly as K3 predicted). |
| **Decode evidence BEFORE any reboot; park-path captures are complete** | Every attempt dir keeps `meta.txt`/`reader-full.txt`; the P6 and K7 backfills upgraded run-attempt.sh's preliminary `ep0-live` rows to real verdicts host-side (ATTEMPTS.md 11:31 and 13:51 backfill blocks). |
| **QEMU pre-validation of walls before the device** | P4 reproduced the v54 NVRAM panic **marker-for-marker** (12 markers, `p4-qemu-report.md` table); P2's QEMU run validated v55's NVRAM fix through MiniIOMFB attach before any boot → v55 passed NVRAM **first try on device, 5/5 boots** (INIT-NVRAM.md update). One-liner rerun in §6. |
| **Fail-closed guards at every layer** | `mkadt.py` SystemExit-refuses any device-mode window overlapping ramoops 0x95200000..0x95600000 (`adt-nodes-report.md`); builder `ROOT_PIN` size:sha gate refuses a wrong rootfs (`build-next-boot.sh:38-81`); `run-attempt.sh` AVB-gates the candidate and aborts (never waits) on preconditions; P3's `host-admission.sh` (real shim `payload.h` code) caught root-v7 missing the 64 MiB headroom rule by ~135 KiB **before** it burned a boot (`ROOT-INTEGRATION.md` §3). |
| **Pinned artifact discipline** | `ROOT_PIN='<size>:<sha256>'` per root profile; mach sha (first-16) in every brief/report/verdict row, full sha in `meta.txt`; `verify-payload.py` checks the embedded root byte-identical; N1r re-hashed v8 `77dc3e9f…` before AND after working on it. |
| **File-ownership lanes** | 20+ agents across P/Q/K/M/F waves with zero write conflicts: every lane owns named files (e.g. P2 owns mkadt.py, K4 owns mach.blazer-r4r7-hfs, F1 owns root-v10); cross-lane changes ship as patches ("patches are the fold-in path"). |
| **Durable workspace docs beat /tmp** | The fleet survived multiple /tmp wipes because each deliverable has a research/ twin (INIT-NVRAM.md, EVIDENCE-MAP.md…); lost reports were backfilled from attempt dirs and re-verified by re-running the decoders (ATTEMPTS.md backfill note). |
| **The MILESTONES ladder in the reader** | `read-ramdump.py` prints a 10-rung ladder + freshness — an instant frontier readout; every wave brief opens with "CONFIRMED frontier (device, exact console text)". Ladder: `read-ramdump.py:229 MILESTONE_LADDER`. |
| **Diagnose on the bytes, port with precedents, confirm on device** | The mockfs-steal chain: K2 transport audit (host, byte-perfect → "it's a filesystem contract break") → K3 bridge diff (source-level, found the 141 lineage's `mockfs_check_macho` + `laguna_hfs`) → patches 0001+0002 → K4 build-green → v60 `hfs: mounted iOSRoot on device md0`. Same pattern: N1r root-v9 (host bind census 537/537 on the final volume bytes) → v61 `_NDR_record` wall gone exactly as predicted. |
| **Pre-registered falsifiers** | K1 published ground truth BEFORE v59 (launchd = 541,600 B, extent block 3966, first 16 B `cf fa ed fe…`, and a decoder table for every possible execdiag outcome — ENOEXEC-ROOT-MOUNT.md §c). K5 published wall table W1–W10 with one-line A/B discriminators (POST-LAUNCHD-MAP.md). N1r: "one boot cleanly discriminates all three outcomes". |
| **Honest-scope reporting** | R2's root-v7 is "M0–M2 recovery root, NOT dyld completion" (2.536 GiB cache can't fit 28 MiB); the v55 12:19 row is "STAGED-NOT-BOOTED, NO device result claimed"; zero-byte captures are kept as failure records, never read as empty regions (v34 lesson, ATTEMPTS.md). |

## 4. What wasted time or failed (do less of this)

| Anti-pattern | Named evidence |
|---|---|
| **Multi-variable / premature boots** | v53-day chaos: 3 attempts ~30 min (v53 aborted ×2 waiting on a phone mid-boot-cycle + v53-g2) — ambiguity about what was tested. |
| **Silent decline paths with no tracing** | v58 `systrace=1`: TUNABLE declares the var, nothing reads it → zero output, one boot wasted (motivated execdiag). Bigger: mockfs/apfs mountroot **success prints nothing** — that silence masqueraded as "hfs mounted ok" for four boots (v55–v58); ROOT-TRANSPORT-AUDIT.md §5.2: "one printf would have surfaced this wall four boots ago". |
| **Unverified assumed facts** | "530 job binaries + xpcproxy present" — FALSIFIED by K5's census (199 files, `/usr/libexec` EMPTY, launchd is the only Mach-O executable; POST-LAUNCHD-MAP.md §6). "RAMDisk prop missing from ADT" — false alarm (mkpayload adds it only to the payload-embedded ADT copy; ROOT-TRANSPORT-AUDIT §3.2). "root-v8 bytes not findable raw in the image" — false alarm (HFS+ starts with 1024 zero bytes; fingerprint sits at +1024). The decmpfs hypothesis — dead end (decmpfs diff is 2 cosmetic lines; BRIDGE-EXEC-DIFF §3b). |
| **Builds with config drift** | K4's first laguna_hfs build: the overlay's conf/files `standard` conversion + MASTER composition membership made **doconf silently drop `options LAGUNA_HFS`** → no `-DLAGUNA_HFS` → `kext-config.h` re-defined CONFIG_PROTECT=1 → fatal AppleKeyStore include; plus hidden `_laguna_hfs_register`/dq* link errors. Fix = 0003 buildfixes patch + restore 0002 conf/files state + `laguna_hfs quota` in FILESYS_BASE (`k4-buildgreen-report.md`). |
| **Build-system footguns** | doconf bakes absolute paths → **`rm -rf BUILD/obj` after applying any patch**; M6's `-j15` truncated generated syscall family (0-byte `bsd.syscalls.master` → 1355 errors) → clean + `-j8`; link ends RC=2 on the export-list tail = documented baseline, NOT an error. |
| **Rate-limit waves killing lanes mid-flight** | The fleet is re-spawned in waves (P, Q, K, M, F briefs) because agents die on provider rate limits; the mitigation is that every lane is recoverable from files — briefs restate the frontier with exact shas so a fresh agent starts in minutes. Don't hold critical state only in conversation. |
| **/tmp/fleet wipes losing reports** | T3 restart (Oct 26) lost the bus + `fastbootboot-report.md`; later rotations ate N12 iter57 evidence. Workspace-first fixed it; COORDINATION.md line 1 still says "restarted 2026-10-26 after T3 /tmp cleanup". |
| **Budget busts** | root-v7 (+4 MiB) missed the shim's 64 MiB kernel-window headroom by ~128–135 KiB — a guaranteed admission-gate park (caught host-side, P3). R2 deleted a resumed lane's multi-GiB APFS carve/cpio/bisect intermediates; C1 used a 624 MB scratch; disk sits at ~16 GiB free; N11 lost post-1380 PNGs to host ENOSPC. Prune your own intermediates; keep <100 MiB/lane. |
| **boot.img symlink quirk** | `avbtool verify_image` opens the hash descriptor as CWD-relative `boot.img` — run builds/verifies from inside OUT or pre-create `boot.img -> laguna-usb-next-boot-avb.img` (ROOT-INTEGRATION.md §4; every vNN dir carries it). |
| **Stale mach pins in meta.txt** | v54's `meta.txt` names `mach.laguna141-paniccap` (panic-capture staging mach) while the RUNNING kernel was r4r5-tcm — INIT-ASSERTION.md had to disambiguate before trusting symbolization. Rule: read the dump with the mach that BOOTED (`--mach mach.blazer-r4r7-hfs` today). |
| **Preliminary ledger rows** | run-attempt.sh auto-appends `ep0-live` before dumps are read; if you don't upgrade them in place (P6/K7 backfill pattern), the ledger lies. |
| **Boot-args with no reader** | `systrace=1` (v58) and `rootrw=1` in the r4r7 tree (K4 note: mounts land read-only; not an exec blocker) — arm nothing you haven't verified has a consumer in the booted mach. |

## 5. Collaboration protocol

- **Wave brief** (`/tmp/fleet/FLEET-BRIEF-<wave>.md`): the coordinator writes one per
  wave — situation, confirmed frontier (exact console text + shas), shared facts
  (paths/commands), SAFETY RAILS, lane list with file ownership. It is the only
  onboarding a fresh agent needs.
- **Bus**: append ONE line per deliverable to `/tmp/fleet/COORDINATION.md`
  (see N1r/K5/K4 entries for the style: verdict → evidence → shas → paths → asks →
  boundaries honored). Keep `/tmp/fleet/INDEX.md` roster current at folds.
- **Report**: `/tmp/fleet/<lane-id>-report.md`, compact; **end with a 5-line summary**
  (numbered; every landed doc ends with one — it's the fleet's skimming convention).
- **File ownership**: write ONLY your lane's files. Shared trees are read-only for
  analysts; changes ship as patches (`patches*/`, `git apply --check` PASS, byte-exact
  reproduction proven) that the owning lane folds in.
- **Coordinator owns the device**: agents never adb/fastboot/boot/reboot. The
  coordinator fires `run-attempt.sh`, decides which candidate boots, and owns
  force-fastboot arming/disarming. QEMU lanes take `/tmp/fleet/qemu-guest-slot.lock`
  (flock, nonblocking) and cap boots (max 3 per lane is the norm).
- **Public folds push DOCS ONLY** to fork/blazer (fold #5 = `f9f6c47`) — never
  binaries, rootfs images, or ramdumps.
- **Asks culture**: every report ends with explicit asks to named lanes; asks name the
  exact file/decision needed (see COORDINATION.md G4/C1/C2 entries).

## 6. Command crib (all host-only unless stated)

```sh
cd /Users/andersmadsen/Developer/personal/hackios   # everything runs from repo root

# Kernel build (incremental; RC=2 export-list tail is baseline, not an error;
# rm -rf BUILD/obj after ANY patch — doconf bakes absolute paths):
cd research/laguna-foundation/kernel-blazer && ./xnu-build.sh -j8 -k
# built mach: xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837

# Payload/boot-image build (opt-in root profile; pins verified inside):
R4_MACHO=$PWD/research/laguna-usb-fix/mach.blazer-r4r7-hfs \
OUT=$PWD/research/laguna-usb-console/out/next-boot-vNN \
WITH_ROOT9=1  research/laguna-usb-console/tools/build-next-boot.sh   # also: WITH_ROOT7/8; default=v6
# ⚠ run from inside OUT or pre-create: ln -s laguna-usb-next-boot-avb.img boot.img
# builder: mkpayload.py embeds segments+ADT+root; avbtool signs (PARTITION_SIZE=67108864,
# KEY=/tmp/test-rsa4096.pem); ROOT_PIN size:sha gate refuses wrong roots.

# AVB verify (host-only preflight; run-attempt.sh --dry-run does sha256+AVB, no device):
(cd research/laguna-usb-console/out/next-boot-vNN && python3 \
  $PWD/research/native-20261024/reuse/felix-working-source/baremetal_loader/avbtool.py \
  verify_image --image laguna-usb-next-boot-avb.img)   # needs boot.img symlink in CWD

# Fire an attempt (COORDINATOR ONLY — this touches the phone):
research/laguna-usb-fix/run-attempt.sh \
  --image research/laguna-usb-console/out/next-boot-vNN/laguna-usb-next-boot-avb.img \
  --name vNN [--watch 300] [--keep-alive] [--skip-avb] [--dry-run] [--selftest]
# Device writes: ONLY `oem force-fastboot on`, `fastboot boot <img>`, stage_file/get_staged reads.
# Evidence lands in research/laguna-usb-fix/attempts/<ts>-vNN/ (meta.txt, reader.txt, ramdump/…).

# Read a dump (parked phone, no reboot):
python3 research/laguna-usb-console/tools/decode-shim-stamps.py <attempt>/ramdump/kernel.log
python3 research/laguna-ramdump/reader/read-ramdump.py <attempt>/ramdump/kernel.log \
  --mach research/laguna-usb-fix/mach.blazer-r4r7-hfs \
  --dump-base 0x95200000 --logs --tail 65536        # prints MILESTONES ladder + VERDICT
# Address math: file_off = PA - 0x95200000; ramoops console file 0x0..0x1FFFFF;
# pmsg base file 0x200000; TSHIMST2 @file 0x230000; XNU-MSGBUF v2 @0x23F800/0x240000. (EVIDENCE-MAP.md)

# EP0 console (start BEFORE boot; single EP0 client; shell-redirect into live.log):
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py --timeout 300 | tee <attempt>/live.log

# QEMU chain (host-only; ~30 s; P4 reproduced v54 marker-for-marker with this):
RUN_TAG=v54 RUN_CURRENT=1 RUN_PROFILE=1 RUN_LATE=1 PRUNE_BUILD=1 WATCH_LATE=600 \
  research/laguna-usb-fix/qemu-validation/runner/run-qemu-chain.sh
# verdicts: qemu-validation/evidence/verdict-<tag>.json; guest slot lock: /tmp/fleet/qemu-guest-slot.lock

# Rootfs inspection (read-only; hash before AND after):
hdiutil attach <root>.hfs -readonly -nomount            # then mount_ro if needed; fsck first:
fsck_hfs -fn /dev/rdisk?                                # "NO WRITE" line is normal on host
python3 research/laguna-usb-fix/root-integration/verify-payload.py <payload-usb.bin> \
  --image <raw arm64 Image>                             # NOT the mkbootimg wrapper (see §4, K2 gotcha)
bash research/laguna-usb-fix/root-upgrade/tools/verify-v7.sh   # full 955+162-check image gate
```

Boot-args contract (608-byte field at payload offset 0x48; keep <607 chars):
`debug=0x8 serial=3 cpus=1 io.display.iomfb=1 rd=md0 rootrw=1 ignition_level=1
launchd_unsecure_cache=1 kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1
lagunalogdump=1`. `rd=md0` + ADT `/chosen/memory-map RAMDisk=<pa,size>` is the whole
root-mount contract; the v3.9 UFS profile (`rd=ufsroot0 blazer_ufs=2 …`) is frozen in
`ufs-staging/PLAN.md` until the shim U0–U9 ladder lands.

## 7. Safety rails — VERBATIM (FLEET-BRIEF-mwave.md; every brief repeats them)

> - HOST-ONLY. The coordinator (main session) fires attempts. NO adb/fastboot/boots by agents.
> - NEVER flash/erase/set_active/slot/userdata; slot A stock; force-fastboot never disarmed.
> - Public folds: DOCS ONLY (no userland/kernel binaries, no rootfs images, no ramdumps).
> - Own only your lane files; write /tmp/fleet/<lane>-report.md + 1 line in /tmp/fleet/COORDINATION.md.
> - Disk tight: prune own intermediates. End with a 5-line summary.

Plus, from the v55 brief and run-attempt.sh contract: **never reboot the phone**;
recovery posture is the slot-A stock boot; the runner's ONLY device writes are
`oem force-fastboot on`, `fastboot boot <img>`, and named `stage_file`/`get_staged`
reads; a zero-byte capture is a failed capture, not evidence of an empty region.

## 8. Where things live

| Artifact | Path |
|---|---|
| Kernel source (branch `hakios`) | `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` |
| Machs (never overwrite a proven one) | `research/laguna-usb-fix/mach.blazer-r4r5-tcm` `b39179b5…` (baseline) · `r4r6-execdiag` `34b300e0…` · `r4r7-hfs` (current; K4-built) |
| Roots | `root-upgrade/root-v{6,7,8,9}.hfs` — v6 `aebc9a8a…` 24 MiB · v7 `72bf50b9…` · v8 `77dc3e9f…` · v9 `a427241d…` (28 MiB each) |
| Builder / ADT / payload tools | `research/laguna-usb-console/tools/build-next-boot.sh` · `hackios-xnu-kernel/blazer/teguload/{mkadt.py,mkpayload.py}` |
| Attempt runner + ledger | `research/laguna-usb-fix/run-attempt.sh` · `ATTEMPTS.md` · `attempts/<ts>-<name>/` |
| Reader / stamps decoder / EP0 logger | `research/laguna-ramdump/reader/read-ramdump.py` · `research/laguna-usb-console/tools/decode-shim-stamps.py` · `/tmp/tegu-venv/bin/python …/usb_log.py` |
| Wall docs | `research/laguna-usb-fix/{INIT-ASSERTION,INIT-NVRAM,ENOEXEC-ROOT-MOUNT,NDR-RECORD-FIX,BRIDGE-EXEC-DIFF,ROOT-TRANSPORT-AUDIT,POST-LAUNCHD-MAP,USERLAND-PATH,USERLAND-READY,AMFI-PLAN,DISPLAY-READY,EVIDENCE-MAP}.md` |
| Kernel patch sets | `research/laguna-usb-fix/patches-bridge-exec/` (0001 mockfs gate, 0002 laguna_hfs port, 0003 buildfixes) · `patches-display-foundation/` (K6, apply-ready) |
| QEMU lab | `research/laguna-usb-fix/qemu-validation/` |

## 9. Next steps (F-wave, 2026-10-27 17:40)

1. **F1 — root-v10**: fix `indirect symbol has out of range address` (indirect-symbol-table
   range vs per-segment trie remap; check flags-0x08 alias semantics and unslided preferred
   addresses). Owns `root-v10` + `INDIRECT-FIX.md`.
2. **F2 — QEMU A/B** root-v8 vs v9/v10 late-chain before any device boot.
3. **F3 — display**: apply K6 patches → `mach.blazer-r4r8-hfs-disp`.
4. **F4 — ledger**: backfill v60/v61 verdict rows in ATTEMPTS.md (still preliminary
   `ep0-live`; true verdicts in the attempt dirs' `reader-full.txt` + M-wave brief) + docs fold #7.
5. **F5 — syscall delta**: systrace errno-2/9/22 path list for root-v10.
6. **Strategic**: the 28 MiB closure is diagnosis-grade, not completion — the real M3+ path is
   **v3.9 UFS transport** of the full `system.apfs` `637103e1…` (2.31 GiB) per
   ROOT-TRANSPORT.md / ufs-staging/PLAN.md, which needs the shim U0–U9 ladder and one
   coordinator-approved staging write (`userdata_exp.ai`, 8 GiB, non-slotted).

---
*End with a 5-line summary in every report. Question a claim? Open the cited artifact —
this file only compresses them.*
