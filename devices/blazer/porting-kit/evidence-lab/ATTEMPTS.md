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

## 20261027-112204 (cron device-loop)
[run-attempt] outcome=ep0-live EP0=yes
[run-attempt] VERDICT: 2026-10-27T11:22:46 | 8ced00652596ca94 | loop-20261027-112204 | yes | ep0-live | usb-console-live | -
[run-attempt] appended to /Users/andersmadsen/Developer/personal/hackios/research/laguna-usb-fix/ATTEMPTS.md
[run-attempt] evidence: /Users/andersmadsen/Developer/personal/hackios/research/laguna-usb-fix/attempts/20261027-112208-loop-20261027-112204
[run-attempt] DONE — force-fastboot remains ARMED (disarm is a coordinator decision).

