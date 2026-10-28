# Laguna USB-fix attempt log

## README — one command per attempt (`run-attempt.sh`)

Every device attempt is now reproducible with a single command. The script
implements the exact loop that was previously hand-orchestrated for the
2026-10-26/27 attempts, and makes each attempt's evidence complete and its
verdict a one-liner in the table at the bottom of this file.

### Usage

```sh
cd /Users/andersmadsen/Developer/personal/hackios
research/laguna-usb-fix/run-attempt.sh \
  --image research/laguna-usb-console/out/next-boot-vNN/laguna-usb-next-boot-avb.img \
  --name vNN
```

Useful flags: `--watch 300` (total watch deadline, s), `--poll 10`, `--settle 20`,
`--ep0-grace 15`, `--keep-alive` (skip the arm step; force-fastboot must already
be on — still verified), `--skip-avb`, `--dry-run` (print the whole plan; runs
only host-side checks — sha256 + AVB verify — touches NO device), `--selftest`
(host-only checks of the internal bounded-runner and parsers).
Environment overrides: `FASTBOOT`, `ADB`, `TEGU_PYTHON`, `PY3`.

### What it does, in order

1. **Preconditions (abort, never wait):** phone must already be in a
   *responsive blazer* fastboot (`getvar product` bounded 5 s). Host checks for
   all tools: usb_log.py (venv `/tmp/tegu-venv`), decode-shim-stamps.py,
   read-ramdump.py, capture-fastboot.py, felix avbtool, panic-capture mach.
2. **AVB gate (host-only):** candidate verified with the felix avbtool
   `verify_image` via a `boot.img` symlink in a temp dir (same pattern as the
   builders). Refuses to boot an unverifiable candidate.
3. **Arm force-fastboot:** `fastboot oem force-fastboot on` (expect OKAY) +
   `getvar force-fastboot` (expect `on`); aborts otherwise. force-fastboot is
   **never disarmed** by the script in any mode — disarming is the
   coordinator's session-end decision.
4. **EP0 logger first:** starts `usb_log.py --timeout <watch>` in the
   background, stdout+stderr into the attempt dir `live.log` (single EP0
   client; shell redirection because `--output` is allowlisted to the tegu
   nested repo).
5. **Temporary boot:** `fastboot boot <IMG>` (bounded 90 s). A boot
   send-stall is recorded and ends the attempt as `boot-send` — no blind
   retries (v34 lesson).
6. **Bounded watch:** settle 20 s, then poll every 10 s: live-log size +
   EP0 marker, `fastboot devices` + `getvar product` responsiveness, `adb
   devices`. Breaks early on EP0-live or a responsive fastboot park; hard
   deadline 300 s otherwise (usb-dead/dark class).
7. **On park:** immediately stops the logger and runs the proven bounded
   collector (`capture-fastboot.py <attempt>/ramdump --diagnostics`):
   `RAMDUMP_MSG.txt` channel proof → `stage_file`+`get_staged` `kernel.log`
   (4 MiB) → `abl.log` + `abl_last.log`. No reboot in between, no bare
   listing, no raw bank; zero-byte outputs are preserved as failure records.
   Then: `decode-shim-stamps.py` (TSHIMST1/2 trail at pmsg+0x30000) and
   `read-ramdump.py --mach research/laguna-panic-capture/mach.laguna141-paniccap
   --dump-base 0x95200000 --logs --tail 65536`; full reader output saved to
   `reader.txt`.
8. **On EP0-live / Android:** saves the live log; if adb is up, best-effort
   (non-fatal) `adb exec-out su -c 'cat /sys/fs/pstore/console-ramoops-0'`.
9. **Verdict:** one line appended to the table at the bottom of this file,
   assembled from the decoder output (last stage + flags + seq, header reason)
   or from the outcome class.

### Device-write contract

The script's ONLY device writes are `fastboot oem force-fastboot on`,
`fastboot boot <img>`, and the named stage_file/get_staged reads. It never
flashes, erases, formats, set-actives, changes slots, or reboots the device.
Every device command is printed verbatim (`[cmd] ...`) before it runs and all
children are hard-bounded (no interactive prompts, no waits).

### Attempt dir layout (`research/laguna-usb-fix/attempts/<ts>-<name>/`)

`meta.txt` (image path, sha256, parameters, contract) · `preflight-product.out`
· `avb-verify.out` · `ff-arm.out` / `ff-getvar.out` · `live.log` (EP0 logger)
· `boot.log` · `capture.log` · `ramdump/{RAMDUMP_MSG.txt,kernel.log,abl.log,
abl_last.log,bootreason.log,manifest.json,...}` · `stamps-decode.txt` ·
`reader.txt` · `pstore-console-ramoops-0.txt` (best-effort).

### Verdict table columns

`timestamp | candidate sha (first 16; full sha in meta.txt) | attempt name |
EP0? | last-stage | reason | dump path`. `last-stage` is the decoder's last
stage with flags, suffixed `@seqN` when the stamp ring gives a seq; when no
dump exists it is an outcome class (`boot-send`, `no-dump`, `ep0-live`,
`android-landed`, `usb-dead`). Keep prose notes ABOVE the attempt-log section —
the script appends rows at EOF.

## Backfill notes — 2026-10-26/27 hand-orchestrated attempts

The five rows below were backfilled from `research/laguna-usb-console/out/`
(live logs, build dirs), `research/session-20261027-usbboot/` (dumps), and
`/tmp/fleet/shim-early-evidence-report.md` + `/tmp/fleet/COORDINATION.md`.
Each was re-verified on 2026-10-27 by re-running decode-shim-stamps.py and
read-ramdump.py against the surviving dumps (outputs reproduced exactly).

- **v2 (2026-10-26 18:37)** — first USB-console next-boot
  (`out/next-boot/laguna-usb-next-boot-avb.img`, sha256 full
  `988561750fc9307e56860c39f1409e97a2f48d5466dda0307b7fab311318bf12`).
  ABL accepted the boot; the shim died before its EP0 gadget enumerated
  (0 bytes on 1209:0001 for 180 s) and the phone ended **USB-dark**: the v3.1
  shim quiesced both firmware watchdogs as its FIRST interaction, so the
  (unbounded cold-handoff wait) hang reset nothing. force-fastboot was not yet
  armed. Pre-dates TSHIMST1 → no stamps, no dump. This failure produced the
  v3.2 hang-proofing + stamp-channel delivery and the force-fastboot-on rule.
- **v2b (2026-10-27 06:55)** — same shim family rebuilt with TSHIMST1
  (`out/next-boot-v2/...`, full sha
  `91cf8b2143b4e8c2ac035bbac4dd0ec0e8b8ebae997ef272a2596739be007a03`, stamp
  nonce 0x6dbf3154abd454ef). force-fastboot armed → failure reset parked the
  phone → full evidence loop worked end to end. Decoder: count 8/8 seq 8
  **dropped 16** (v1 8-slot ring saturated by ABL ladder re-entries), last
  stage `usb-preflight [FAULT] arg=0xffffffff`, reason `usb-fatal` — cold-ABL
  handoff read DWC3 GSNPSID/GCTL with the IP un-clocked. Drove the v3.3
  CPM-before-DWC3 order fix + 64-slot TSHIMST2 ABI.
- **v33 (2026-10-27 07:20)** — v3.3 shim
  (`out/next-boot-v33/...`, full sha
  `ecfc985ea05e58ec57f148e1dbd3c86fd9d3324e72c7cbcc0465d03382d465ec`, nonce
  0x20e41996ba745307). Paint fix confirmed (entry→console-paint-done ~2.4 s,
  was 26 s). Terminal stamp DECODED as `usb-preflight [FAULT] @5027 ms` — but
  the death is actually **inside `tgl_cpm_init()`** ~43 ms after wdt-pat (a
  fast geometry/state refusal, not the 3 s ACK timeout); the `usb-preflight`
  stage id is a stale terminal-record mislabel (preflight never ran). Reason
  field still `usb-fatal`; count 9/64, dropped 0. Drove v3.4 CPM value stamps
  + `cpm-never-came-up` reason + preflight 0xc450000 fail-fast canary.
- **v34 (2026-10-27 07:29, aborted)** — v3.4 shim
  (`out/next-boot-v34/...`, full sha
  `200813d959f1189736c11912f96a7dbede5337a77056f9a048d4f05a99a90323`). Boot
  issued; the evidence pull then hit a fastboot **send stall** and the attempt
  was aborted. Zero-byte `v34-kernel.log`/`v34-abl.log` were kept on purpose:
  a zero-byte file is a failed capture (fastboot creates the local output
  before `upload`), never evidence of an empty region. run-attempt.sh now
  bounds and records exactly this case (`boot-send` / capture failure
  records) instead of abandoning the loop.
- **v34b (2026-10-27 07:47)** — same v3.4 image re-booted, capture complete.
  Decoder: count 13/64 seq 13 dropped 0; trail `cpm-first ENTER → cpm-geo
  ENTER → cpm-geo DONE 0x0 → cpm-geo **REFUSED 0x0** → cpm-first FAULT
  0xffffffff` at ~4975 ms; header reason **`cpm-never-came-up`**. The MBA
  mailbox geometry reads return 0x0/0x0 cold ⇒ per the v3.4 decision tree the
  missing layer is MBA-syscon (0x5250000) bring-up / ABL MBA quiesce — NOT the
  LPCM payload (wire audit already agrees with CPM-FIRST-SPEC §3.1 + DT).

### Backfill — 2026-10-27 11:31–11:52 v53-g2 → v54 (P6, host-only ledger backfill)

Verdicts assembled host-side from each attempt dir's `meta.txt` +
`reader-full.txt`; run-attempt.sh had auto-appended preliminary `ep0-live`
rows before the dumps were read, and those rows are upgraded in place below.

- **v53-g2 (2026-10-27 11:31:36)** — ADT with G2 nodes (sha
  `50e7becec35e1af9…`). EP0 console live; **XNU entered** and ran the PE/IOKit
  init ladder — `found interrupt-controller` + `found timer`, VM bootstrap,
  ATM/Firehose/OSLog, scheduler clutch, mig_table, serial KDP, `iBoot version:
  laguna-shim`, vnguard/AMFI/Sandbox policies — then panicked after PMRD's
  ON_STATE power event with the undecoded-format assertion record (`panic
  message: %s:%d Assertion failed: %s`, second record `panic`, frames=0) →
  `MACH Reboot`. Reader verdict: `PANICKED | stage=PANIC(12) | XNU
  entered=yes`.
- **v53 (11:31:57) + v53 (11:35:26) — aborted, no device.** Same v53-g2
  image; preflight `getvar product` never saw a responsive fastboot
  (`< waiting for any device >`) because the phone was still inside the
  v53-g2 boot/panic cycle. The precondition abort worked as designed: no
  blind boot, no device writes, no dump.
- **v54 (2026-10-27 11:44:00)** — ADT with `/options` + G2 nodes (sha
  `c6adcec3efe1ea8e…`). **PASSED `IOPlatformExpert.cpp:1695`** — no more
  `gIOOptionsEntry` assert; `pe_arm_map_interrupt_controller: found
  interrupt-controller` + `found timer` on device, then the full ladder to
  `iBoot version: laguna-shim`. **NEW WALL:** `panic message:
  IONVRAMCHRPHandler creation failed` — `IONVRAM.cpp:getNVRAMProperties:1151
  - Unable to find nvram-bank-size property` →
  `IONVRAMCHRPHandler.cpp:init:304 - Unable to get NVRAM properties` →
  `MACH Reboot` (auto-parked back in fastboot). Dump read 11:52; reader
  verdict `PANICKED | stage=PANIC(12) | XNU entered=yes`. The 1695 analysis
  in INIT-ASSERTION.md is retired as the live wall; the new NVRAM wall
  analysis lands in INIT-NVRAM.md (P1, folded when written).

### Backfill — 2026-10-27 12:19 v55 STAGED-NOT-BOOTED (host-only ledger note)

- **v55 (2026-10-27 12:19) — STAGED-NOT-BOOTED, no device contact, NO device
  result claimed.** v55 (NVRAM CHRP fix, sha `00c23ba634495784…`) is built +
  AVB-verified + QEMU-validated past the NVRAM wall, but the 12:19 device
  attempt never ran: the phone was absent at preflight and the runner aborted
  safely pre-boot — no blind boot, no device writes, no dump. v55 fires
  on-device the moment the phone reconnects (coordinator-owned); nothing in
  this row should be read as an on-device outcome.

### Backfill — 2026-10-27 13:51–15:09 v55 → v59 (K7, host-only ledger backfill)

Verdicts assembled host-side from each attempt dir's `meta.txt` +
`reader-full.txt`; run-attempt.sh had auto-appended preliminary `ep0-live`
rows before the dumps were read, and those rows are upgraded in place below.
This block extends (does not duplicate) the P6/Q6 v53/v54 backfill above.

- **v55 (2026-10-27 13:51:13, sha `00c23ba634495784…`, mach r4r5-tcm
  `b39179b5…`) — FIRST ROOT-MOUNT SUCCESS + the errno-8 wall appears.**
  The v55 CHRP NVRAM ADT passed on device: `NVRAM size is 8192 bytes`
  (IONVRAM.cpp:initImageFormat:1364), `_bankSize=0x2000, _bankCount=0x1,
  _currentBank=0` (getNVRAMProperties:1176), unserializeImage ok — the v54
  `IONVRAMCHRPHandler creation failed` panic is GONE. Reader MILESTONES:
  reached 8 rungs XNU-ENTRY → PE-INTCTRL → PE-TIMER → VM-BOOTSTRAP-DONE →
  **NVRAM-OK** → PE-1695-PASSED → **CPU-REG** → **ROOT-MOUNT**
  (`BSD root: md0`, `vfs_mountroot ok`), last=ROOT-MOUNT,
  next-expected=LAUNCHD-EXEC, wall=UNKNOWN-PANIC. Console then:
  `load_init_program: attempting to load /sbin/launchd` @42.601 s →
  `failed loading /sbin/launchd: errno 8` @42.620 s → `MACH Reboot`
  @44.118 s. PANIC(12)@seq1935762565; panic channel message=None (no panic
  string recovered — the launchd-exec panic prints only via the init_programs
  loop's final `panic("Process 1 exec of %s failed, errno %d")`, which this
  boot's capture did not recover as text). Compact stages record
  LAUNCHD_EXEC(9) = the attempt, not a success.
- **v56 (13:55:08, sha `255d40afb6c5fc59…`) — root-v7 + DRAM window
  0x0A400000: window EXONERATED, wall reproduced.** v55 ADT with dram-size
  bumped 0x0A000000→0x0A400000 + WITH_ROOT7=1 (window needed for the larger
  payload). Identical ladder to ROOT-MOUNT, identical death:
  errno 8 @108.315 s, PANIC(12)@seq1935762572, no panic string. The +4 MiB
  window is not the problem; the wall is downstream of root-mount.
- **v57 (13:59:21, sha `96db88f3a1757aa8…`) — root-v8 (libdispatch
  restored): same wall.** Q5's root-v8 (`77dc3e9f…`, split libdispatch
  dylib restored per USERLAND-READY M3). Ladder + death identical:
  console `failed loading /sbin/launchd: errno 8` @105.448 s,
  PANIC(12)@seq1935762579. Root-v7 vs root-v8 makes no difference → root
  CONTENT exonerated; every root generation dies at the same exec.
- **v58 (14:10:20, sha `8cdc6dd8e1243428…`) — systrace armed, SILENT
  decline.** Boot-args gained `systrace=1` (the kern_exec.c TUNABLE,
  kern_exec.c:1165-1166). Zero trace output: the TUNABLE declares the
  variable but nothing in the booted mach (still r4r5-tcm, pre-execdiag)
  reads it at the decline site → the `exec_mach_imgact` magic gate returns
  a silent `-1` and the activator loop collapses to ENOEXEC with no print.
  errno 8 @566.598 s, PANIC(12)@seq1935762586. Motivated K1's unconditional
  execdiag instrument (v59).
- **v59 (15:09:08, sha `51f3d78d71f403fc…`, mach r4r6-execdiag
  `34b300e0…`, WITH_ROOT8=1) — MOCKFS-STEAL FINGERPRINT CONFIRMED.**
  execdiag fired at the wall (3413.242 s):
  `execdiag: /sbin/launchd: declined: first page is not a Mach-O image:
  err=-1 hdr[magic=0x00000000 cputype=0x00000000 cpusub=0x00000000
  filetype=0x00000000] fsize=29360128 vdata[0..15]= 00000000 00000000
  00000000 00000000` then `execdiag: /sbin/launchd: ENOEXEC: no execsw
  image activator accepted this file: err=8 …`. Read: the exec read a
  28 MiB "file" (fsize 29360128 = 0x1C00000 = the WHOLE root-v8 HFS+
  volume, not launchd's 541,600 B) whose first page is zeros — mockfs
  claimed the HFS+-formatted md0 as root (its mountroot accepted ANY
  device; no `mockfs_check_macho` gate in this binary) and served the raw
  volume as one synthetic `/sbin/launchd`; first page zeros (HFS+ reserved
  boot block, volume header at offset 1024) → magic gate declines silent
  -1 → ENOEXEC(8) → panic. K2/K3's diagnosis is device-proven exactly as
  predicted (BRIDGE-EXEC-DIFF.md §verdict). Full analysis:
  ENOEXEC-ROOT-MOUNT.md. Fix = K3 patches 0001+0002 (applied to the
  foundation tree; build green is K4's lane, mach.blazer-r4r7-hfs).

### Backfill — 2026-10-27 16:10–17:34 v60 → v61 (F4, host-only ledger backfill)

Verdicts assembled host-side from each attempt dir's `meta.txt` +
`reader-full.txt` + `late-kernel.log`; run-attempt.sh had auto-appended
preliminary `ep0-live` rows before the dumps were read, and those rows are
upgraded in place below. This block extends (does not duplicate) the K7
v55→v59 backfill above. Both attempts boot the SAME mach
`mach.blazer-r4r7-hfs` (`27f05924…` = K3 mockfs-gate + laguna_hfs port +
K4 build fixes) — the delta between v60 and v61 is root content only
(v8 → v9), so the wall that moved is a pure root-image result.

- **v60 (2026-10-27 16:10:53, sha `f207a5e087f6c9ee…`, mach r4r7-hfs
  `27f05924…`, root-v8 `77dc3e9f…`, WITH_ROOT8=1) — HFS ROOT MOUNT +
  LAUNCHD EXEC'D + DYLD SHARED REGION: the ENOEXEC wall is GONE.**
  laguna_hfs won the root race against mockfs exactly as K4 verified in
  source: `BSD root: md0, major 1, minor 0` → `hfs: mounted iOSRoot on
  device b(1, 0)` @908.386 s → `load_init_program: attempting to load
  /sbin/launchd` with NO execdiag decline and NO errno 8 — launchd
  **exec'd** (first real exec past the v55–v59 wall) and dyld ran inside
  PID 1: `shared_region_check_np(…) = 12`,
  `minisandbox: request 2 (first seen from launchd[1]): allowed`, dyld
  probed its cache paths (`/System/Library/Caches/com.apple.dyld/` = 2
  ENOENT; `dyld_shared_cache_arm64.development` / `dyld_shared_cache_arm64`
  = 9). Reader MILESTONES: all 10 rungs reached — XNU-ENTRY, PE-INTCTRL,
  PE-TIMER, VM-BOOTSTRAP-DONE, NVRAM-OK, PE-1695-PASSED, CPU-REG,
  ROOT-MOUNT, LAUNCHD-EXEC, DYLD — last=DYLD, next-expected=none
  (dyld=shared-region). **NEW WALL (dyld bind, N1r's predicted one):**
  `pid 1 exited -- exit reason namespace 6 subcode 0x4, description
  Symbol not found: _NDR_record` + `Expected in:
  <616FA041-BFAA-389C-8C6A-3DC585DAD933> /usr/lib/libSystem.B.dylib`
  @908.504 s — launchd's bind #1 against the closure's EMPTY
  `LC_DYLD_EXPORTS_TRIE` placeholders (NDR-RECORD-FIX.md §1.1). coredump
  also failed: `/private/var/cores` missing (error 2) — a real path
  gap, not just noise. Panic `initproc failed to start -- exit reason
  namespace 6 subcode 0x4` → `MACH Reboot` @910.163 s;
  PANIC(12)@seq1935762603.
- **v61 (17:33:49, sha `22a0ae61355ea622…`, same mach + root-v9
  `a427241d…` = N1r's export-trie + __TEXT closure repair) —
  _NDR_RECORD WALL CLEARED; NEW WALL = map_with_linking_np EINVAL(22) →
  'indirect symbol has out of range address'.** `Symbol not found` /
  `_NDR_record` appear NOWHERE in the boot: the namespace-6 subcode-0x4
  bind wall is dead and N1r's repair is device-confirmed — launchd bound
  past the entire 537-symbol census. Ladder identical (all 10 rungs,
  dyld=shared-region; `hfs: mounted iOSRoot on device b(1, 0)` @44.137 s
  — fresh boot session, so timestamps are ~20× earlier than v60). dyld's
  console trail ran one step further than v60 — same shared-region/
  minisandbox/cache-probe + libobjc-env lines, then:
  `systrace: launchd[1] map_with_linking_np(0x16ed98780, 0x1, 0x16ed987b0,
  0x1118) = 22` (EINVAL) @44.291 s → **NEW WALL:** `pid 1 exited -- exit
  reason namespace 6 subcode 0x9, description indirect symbol has out of
  range address` @44.296 s (dyld-1066's own fatal error text; launchd
  never reached main — no `initproc exited`, no `kern.initproc_spawned`).
  coredump failed again (`/private/var/cores` missing, error 2);
  `MACH Reboot` @45.917 s; PANIC(12)@seq1935762613. Read (brief + N1r
  provenance note): almost certainly the repaired dylibs' indirect symbol
  table entries pointing out of segment range after the per-segment
  export-trie remap — the residual family pre-documented in
  NDR-RECORD-FIX.md §5 (disk-trie alias semantics / closure loading at
  preferred addresses). Fix lane: F1 → root-v10 (INDIRECT-FIX.md when it
  lands). **(PROVENANCE CORRECTION 2026-10-28, F1c audit — INDIRECT-FIX.md
  §5): the shipped root-v9 was later proven byte-identical in CONTENT to
  root-v8 (199/199 files, 0 differing; all 36 `LC_DYLD_EXPORTS_TRIE` empty,
  `__TEXT` hollow — N1r's repairs never landed in the pinned image). The
  v60→v61 wall change is therefore a layout/extent effect (the laguna HFS
  driver serving different bytes for the same logical content depending on
  image layout), not content — documented honestly rather than read as an
  N1r fix confirmation. The repairs only became real in root-v10.**

### Backfill — 2026-10-27 19:13 → 2026-10-28 v63 → v69 (P6, host-only ledger backfill)

Verdicts assembled host-side from the v63 attempt dir (`meta.txt` +
`reader-full.txt`), the QEMU bridge evidence
(`qemu-validation/evidence/runC-late-vNNb-nvram/` + pins
`inputs/tag-vNNb.env`), and the F/D-lane reports (F1b/F1c/F1d/D1/D2/D3/D4).
The v63 table row below was upgraded in place from the preliminary
`ep0-live` auto-append (P6/K7 pattern). **v66–v69 were never device-fired**
— no attempt dir exists, so they carry prose entries here and NO
attempt-table rows; no device result is claimed for any of them. Their
evidence is QEMU-only, via F2's bridge payload (exact device kernel bytes +
ADT swapped), which has reproduced every wall in this class marker-for-marker
(v60b/v61b/v63b precedents). Context candidates v62/v64/v65 (built, never
device-fired) are summarized in one bullet each. Every entry shares mach
`mach.blazer-r4r7-hfs` (`27f05924…`) — only root + dyld content moves.

- **v63 (2026-10-27 19:13:23 device, sha `abd8630281617ab2…`, mach r4r7-hfs
  `27f05924…`, root-v10 `3af78bcb…` = F1's N1r-pipeline re-run + 554
  section-field translations + 31 GOT flag flips) — INDIRECT WALL CLEARED
  ON DEVICE; NEW WALL = launchd SIGSEGV in dyld's NXArgcPtr wire-up.**
  `indirect symbol has out of range address` appears NOWHERE: the
  namespace-6 subcode-0x9 closure-validation death is gone. Ladder again
  10/10 (last=DYLD, dyld=shared-region): `hfs: mounted iOSRoot on device
  b(1, 0)` @4591.596 s → `/sbin/launchd` exec'd (no execdiag, no errno 8)
  → shared_region/minisandbox lines @4591.646–650 s → then
  `usertrap: launchd[1] exception 1 code 0x1 0x201e1d34628: pc 0x100e0e058
  lr 0x100e0e020 sp 0x16f2eb280, shared cache slide 0x0` @4591.806 s
  (`called from 0x100e0c2e0` → `0x0`) → `pid 1 exited -- exit reason
  namespace 2 subcode 0xb` @4591.807 s → `MACH Reboot` @4593.416 s;
  PANIC(12)@seq1935762623. Symbolication (F1b, byte-proven; F1c symtab
  refinement): the faulting VA 0x201e1d34628 is an UN-APPLIED dyld
  chained-fixup REBASE entry (target36 0x1E1D34628, next=2) sitting raw in
  libdyld `__DATA_DIRTY,__dyld4` +0x18 (`defaultVars.NXArgcPtr`); pc =
  `dyld4::prepare`+0x9c4 = the `*state.vars->NXArgcPtr = argc` store
  (dyldMain.cpp:742). QEMU v63b reproduced it marker-for-marker — SAME VA,
  namespace 2 subcode 0xb, different dyld base (0x10087e…) — one defect,
  layout-independent. Fix lane: F1b → root-v11 (POINTER-REBASE.md).
- **v62 (sha `2c27c35dfa06e925…`, mach r4r8-hfs-disp + root-v9) — BUILT +
  AVB-VERIFIED, NEVER FIRED.** F3's display-mach candidate (K6 DC9x00
  series 0001–0003 + /laguna-dpu ADT node, `io.display.probe=1
  io.display.splash=1`). Coordinator chose the root lane first; no device
  result claimed. Dark-panel-is-success note lives in
  /tmp/fleet/f3-display-mach-report.md §7.
- **v64 (sha `6ba42b935c700800…`, root-v11 = F1b's 11,757-pointer rebase +
  380 sectname repairs) — QEMU-ONLY (v64b, evidence 2026-10-27 19:57).
  NEGATIVE CONTROL as pre-registered:** same NXArgcPtr instruction faulted
  at the SAME class of address (dyld base 0x100AE4000, F1c's string-pointer
  proof) — root-v11's rebased pointers still dangle because the stock
  extracted dyld maps disk dylibs ANYWHERE and never slides rebase-less
  splits (wall-#4 root cause, SUBCACHE-POINTERS.md). Never device-fired.
- **v65 (sha `4193058b2ad521d5…`, root-v13 = v12 disjoint compacted layout
  + D1's SOURCE-BUILT dyld-1066.8 with the F1c mapSegments preferred-load
  patch) — QEMU-ONLY (v65b, evidence 2026-10-27 22:40). FIRST patched-dyld
  boot; NEW WALL = NULL+8 deref:** `usertrap … fault VA 0x8` symbolicated
  byte-exactly (NULL-DEREF.md) to `_cerror_nocancel`+0x10 — D1's dyld
  links xnu libsyscall's TLS errno (`_os_tsd_get_direct(__TSD_ERRNO)` at
  tpidrro+8) with TLS base 0; trigger = the EXPECTED cacheless-boot
  `shared_region_check_np` = 12 (ENOMEM) error return in start()'s cache
  probe. Died BEFORE prepare() — preferred-load + NXArgcPtr walls
  untested in v65b (F1d frontier correction). Never device-fired.
- **v66 (sha `8c28b4fef52b79b3…`, root-v14 = F1d's TLS-free errno/lock
  dyld `e0e00473d6b02183…`) — QEMU-ONLY (v66b, evidence 2026-10-27
  23:35), never device-fired. Wall-#6 cleared, then SYSCALL-STUB LIVELOCK:**
  `systrace: launchd[1] shared_region_check_np(…, 0x4000, 0x1, 0x0) = 12`
  @0.600862 s kernel-side with NO NULL+8 usertrap (the F1d errno fix
  works), then pid 1 goes silent. D2 decoded the parked guest state
  byte-exactly: dyld alive at EL0 inside the stub's error path — the D1
  BSDSYS stub macro's `bl _cerror` clobbers the caller's LR (x30 = the
  stub's OWN `ret`; X16=294; X0:X1=-1:-1 = the cerror return convention;
  X8=&errno) → `ret` branches to ITSELF = 1-instruction livelock; kernel
  healthy the whole time (memory-stat loop, no usertrap possible). Fix:
  D2's 48/48 BSD error-path LR save/restore + munmap 71→73
  (SYSCALL-STUBS.md) → root-v15.
- **v67 (sha `d5311d0208d21b02…`, root-v15 = D2 stub-fixed dyld
  `731bf809729b9dd3…`) — QEMU-ONLY (v67b, evidence 2026-10-28 00:17),
  never device-fired. STUB FIX CONFIRMED, then SILENT EL0 SPIN:**
  check_np = 12 @0.620319 s, NO livelock, NO usertrap, scheduler healthy
  (memory-stat spam to 890 s) — the D2 fix is resident and working; pid 1
  then hangs with zero console output (no exit, no panic). D3 proved the
  hang is NOT the F1d constant-owner lock (binary-level audit) and that
  kernel systrace prints only FAILED syscalls ON RETURN — so
  blocked-forever / success-loop / pure-EL0-loop are console-
  indistinguishable (BOOTSTRAP-LOCKS.md). Motivated the D3 boottrace
  instrument (13 unique-length write(2) markers, decoded by LENGTH, no
  dyld base needed) → root-v16.
- **v68 (sha `ec837ca87783532d…`, root-v16 = D3 barrier-lock + boottrace
  dyld `9057e30b4246de4b…`) — QEMU-ONLY (v68b, evidence 2026-10-28 01:12),
  never device-fired. FIRST INSTRUMENTED EL0 TRACE:** `write(0x2, …, 0xa)`
  = M01 @0.619536 s → `write(0x2, …, 0xb)` = M02 (persistent allocator
  created) @0.622365 s → silence; kernel healthy. D4 windowed the
  M02→M03 gap from the linked binary: hang inside
  `lsl::Allocator::aligned_alloc(8, 0x1e8=sizeof(ProcessConfig))` or the
  ProcessConfig ctor — the assert-free BTree-walk class (every escape
  hatch in the binary compiles LOUD; the silent spin has no assert).
  Fix: D4's bounded 16-retry guard + A1/A2/A3 allocator markers
  (ALLOCATOR-HANG.md) → root-v17.
- **v69 (sha `530ece467e1afa4d…`, root-v17 = D4 guarded + allocator-marker
  dyld `7e092f33f86cf102…`) — QEMU-ONLY (v69b, evidence 2026-10-28 01:54),
  never device-fired. CURRENT FRONTIER — B-TREE WALK SPIN:**
  M01 (0xa) @0.606356 → A1 (0x17, aligned_alloc entry k=1) → A2 (0x18,
  guard-loop iteration) ×2 → M02 (0xb, persistent allocator created)
  @0.609318 → A1 (k=2) → A2 @0.609727 → silence; **zero A3 (0x19) lines**
  = the no-fit→vm_allocate path never ran → per the pre-registered
  ALLOCATOR-HANG.md §4 decode table this is W-A2: hang INSIDE the BTree
  scan on allocation k=2. Kernel healthy throughout (memory-stat spam to
  890+ s). P1 owns the D5-equivalent fix → root-v18.
- **D-loop status (v61 → v69):** six walls named/cleared in ~30 h with ONE
  device boot (v63) — indirect-symbol validation (root-v10, device),
  NXArgcPtr rebase (root-v11/v12 content + F1c layout), TLS errno/lock
  (F1d), stub LR livelock (D2), silence (D3 boottrace instrument), and now
  the allocator BTree walk (D4 guard narrowed it to W-A2). Death moved from
  kernel exec walls through dyld's bind validation into dyld's OWN heap
  bootstrap; the boottrace ladder (M/A markers) is now the frontier
  readout. Every step validated QEMU-first against the F2 bridge
  (marker-for-marker record: v60b/v61b/v63b); the device is only burned
  when a wall class changes. Strategic exit from the carve-patch class
  remains the v3.9 UFS full-root transport (ROOT-TRANSPORT.md /
  ufs-staging/PLAN.md).

## Attempt log — one line per attempt (appended by run-attempt.sh)

timestamp | candidate sha | attempt name | EP0? | last-stage | reason | dump path
2026-10-26T18:37:00 | 988561750fc9307e | v2 | no | usb-dead(no-stamps) | usb-dark | -
2026-10-27T06:55:00 | 91cf8b2143b4e8c2 | v2b | no | usb-preflight[FAULT]@seq8 | usb-fatal | research/session-20261027-usbboot/v2b-kernel.log
2026-10-27T07:20:00 | ecfc985ea05e58ec | v33 | no | usb-preflight[FAULT]@seq9 | usb-fatal-cpm-init-mislabeled | research/session-20261027-usbboot/v33-kernel.log
2026-10-27T07:29:00 | 200813d959f11897 | v34 | no | boot-send | aborted-send-stall | -
2026-10-27T07:47:00 | 200813d959f11897 | v34b | no | cpm-first[FAULT]@seq13 | cpm-never-came-up | research/session-20261027-usbboot/v34b-kernel.log
2026-10-27T08:52:36 | 746902b9bb91ed89 | v36 | no | ep0-live | usb-console-live | -
2026-10-27T08:58:48 | 3f2d200c1fbeb2d7 | v37 | no | usb-warm [FAULT]@seq27 | cpm-never-came-up | attempts/20261027-085704-v37/ramdump/kernel.log
2026-10-27T09:18:18 | cb68de26ed49c2bc | v38 | no | wdt-quiesce-late [DONE]@seq34 | refused | attempts/20261027-091614-v38/ramdump/kernel.log
2026-10-27T09:45:46 | 6fee0c6f47f0dab5 | v39 | yes | ep0-live | usb-console-live | -
2026-10-27T09:51:49 | 2c6822462e7fc090 | v40 | yes | ep0-live | usb-console-live | -
2026-10-27T09:56:05 | 732838a6f184402a | v41 | yes | ep0-live | usb-console-live | -
2026-10-27T10:00:03 | 65d450fc7cca1d27 | v42 | yes | ep0-live | usb-console-live | -
2026-10-27T10:03:47 | c91aec537d612442 | v43 | yes | ep0-live | usb-console-live | -
2026-10-27T10:08:34 | ceb6ea419363be3d | v44 | yes | ep0-live | usb-console-live | -
2026-10-27T10:12:19 | bbbd734f39501ff2 | v45 | yes | ep0-live | usb-console-live | -
2026-10-27T10:21:30 | 7f8394e35098c28d | v46b | yes | ep0-live | usb-console-live | -
2026-10-27T10:27:26 | da10ae8fcfc85291 | v47 | yes | ep0-live | usb-console-live | -
2026-10-27T10:30:29 | 38934b4945b15c18 | v48 | yes | ep0-live | usb-console-live | -
2026-10-27T10:33:44 | 78d3f8d5a6d0da1e | v49 | yes | ep0-live | usb-console-live | -
2026-10-27T10:37:36 | a80478a83f984ffa | v50 | yes | ep0-live | usb-console-live | -
2026-10-27T10:40:03 | 9aa84e828405f90a | v51 | yes | ep0-live | usb-console-live | -
2026-10-27T11:07:46 | 16360033d2b9d2a3 | v52 | yes | ep0-live | usb-console-live | -
2026-10-27T11:22:46 | 8ced00652596ca94 | loop-20261027-112204 | yes | ep0-live | usb-console-live | -
2026-10-27T11:31:57 | 50e7becec35e1af9 | v53 | no | no-device | aborted-preflight-waiting | -
2026-10-27T11:32:15 | 50e7becec35e1af9 | v53-g2 | yes | PANIC(12)@seq1935762554 | xnu-panicked-xnu-entered | attempts/20261027-113136-v53-g2/reader-full.txt
2026-10-27T11:35:26 | 50e7becec35e1af9 | v53 | no | no-device | aborted-preflight-waiting | -
2026-10-27T11:44:38 | c6adcec3efe1ea8e | v54 | yes | PANIC(12)@seq1935762558 | nvram-chrp-handler-failed | attempts/20261027-114400-v54/reader-full.txt
2026-10-27T12:19:00 | 00c23ba634495784 | v55 | no | staged-not-booted | aborted-preflight-phone-absent | -

## 20261027-112204 (cron device-loop)
[run-attempt] outcome=ep0-live EP0=yes
[run-attempt] VERDICT: 2026-10-27T11:22:46 | 8ced00652596ca94 | loop-20261027-112204 | yes | ep0-live | usb-console-live | -
[run-attempt] appended to /Users/andersmadsen/Developer/personal/hackios/research/laguna-usb-fix/ATTEMPTS.md
[run-attempt] evidence: /Users/andersmadsen/Developer/personal/hackios/research/laguna-usb-fix/attempts/20261027-112208-loop-20261027-112204
[run-attempt] DONE — force-fastboot remains ARMED (disarm is a coordinator decision).

2026-10-27T13:51:51 | 00c23ba634495784 | v55 | yes | ROOT-MOUNT→PANIC(12)@seq1935762565 | root-mounted-first-launchd-errno8-panic-stringless | attempts/20261027-135113-v55/reader-full.txt
2026-10-27T13:55:46 | 255d40afb6c5fc59 | v56 | yes | ROOT-MOUNT→PANIC(12)@seq1935762572 | root-mounted-rootv7-window0x0A400000-errno8 | attempts/20261027-135508-v56/reader-full.txt
2026-10-27T13:59:59 | 96db88f3a1757aa8 | v57 | yes | ROOT-MOUNT→PANIC(12)@seq1935762579 | root-mounted-rootv8-libdispatch-errno8 | attempts/20261027-135921-v57/reader-full.txt
2026-10-27T14:10:58 | 8cdc6dd8e1243428 | v58 | yes | ROOT-MOUNT→PANIC(12)@seq1935762586 | systrace-armed-silent-decline-errno8 | attempts/20261027-141020-v58/reader-full.txt
2026-10-27T15:09:50 | 51f3d78d71f403fc | v59 | yes | ROOT-MOUNT→PANIC(12)@seq1935762593 | execdiag-mockfs-steal-fingerprint-confirmed | attempts/20261027-150908-v59/reader-full.txt
2026-10-27T16:11:43 | f207a5e087f6c9ee | v60 | yes | DYLD→PANIC(12)@seq1935762603 | hfs-root-mounted-launchd-exec-dyld-shared-region-ndr-record-bind-wall | attempts/20261027-161053-v60/reader-full.txt
2026-10-27T17:34:27 | 22a0ae61355ea622 | v61 | yes | DYLD→PANIC(12)@seq1935762613 | ndr-cleared-map-with-linking-einval-indirect-symbol-out-of-range | attempts/20261027-173349-v61/reader-full.txt
2026-10-27T19:14:01 | abd8630281617ab2 | v63 | yes | DYLD→PANIC(12)@seq1935762623 | indirect-cleared-launchd-sigsegv-nxargcptr-prepare-namespace2-subcode0xb | attempts/20261027-191323-v63/reader-full.txt
