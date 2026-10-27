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

## v39–v51 — the EP0-determinism + payload-admission campaign (2026-10-27 09:45–10:40)

These thirteen attempts took the lane from "EP0 enumerated once" to "XNU
boots on the phone". The script's outcome class for all of them is
`ep0-live` (the console stayed up, so no park happened during the watch
window), which is why the table rows look uniform — the real verdicts below
come from the dumps, which were pulled **late** (after the shim's terminal
reset or XNU's own panic reset parked the phone) and decoded afterwards
(`late-kernel.log` + regenerated `stamps-decode.txt`; the v51 dir carries
`reader-full.txt`). Full analysis: [`../evidence-channels/PAYLOAD-ADMISSION.md`](../evidence-channels/PAYLOAD-ADMISSION.md).

- **v39 (09:45, sha `6fee0c6f47f0dab5`)** — **EP0 first full enumeration +
  first transfer-active refusal.** Warm world: `usb-warm DONE 0x33313130`
  (GSNPSID), preflight PASS, `dma-admit DONE`, `ep0-ready DONE 0x820009`,
  8 s enumeration wait DONE (console live), then `payload-admit ENTER
  0x8a000000` and the handoff-time check refused — the old
  `state != EP0_SETUP` predicate treats the host-connected endpoint (parked
  in a wait-status) as busy. The refusal path re-entered the USB wait and
  died at `usb-enum-wait FAULT 0x0` (seq 33).
- **v40–v44 (09:51–10:08, shas `2c6822462e7fc090`, `732838a6f184402a`,
  `65d450fc7cca1d27`, `c91aec537d612442`, `ceb6ea419363be3d`)** — **EP0
  stamps + wait/WDT forensics.** Per-request EP0 breadcrumbs land
  (`usb-enum-wait ENTER type<<16|req<<8|len` / `DONE 0x8000|n`): the dumps
  show the host polling loop as `0xc05a00 ENTER → 0x8180 DONE` pairs (384 B
  GET_LOG reads). Every attempt still died `FAULT 0x0` **exactly ~9.0 s
  after the last `wdt-pat`** (e.g. pat@4.4 s → FAULT@13.4 s): the survey
  finds THREE firmware WDTs but the pat path services two — the unpatted
  instance bites mid-wait. v44's dump additionally proves the endpoint
  parks in `WAIT_STATUS3` after the host's last read — the transfer-active
  predicate, not the USB link, was refusing every handoff.
- **v45 (10:12, sha `bbbd734f39501ff2`)** — predicate fixed (only a data
  stage in flight blocks) and the first payload admission attempt: the
  initrd scan refused `no TEGUXNU payload within scan budget` (stamped
  `no TEGUX`, seq 75 @29.1 s) — ABL's retained vendor archives push the
  TEGUXNU magic past the 64 MiB scan budget. Drove the embedded-envelope
  delivery.
- **v46/v46b (10:18/10:19, aborted builds — no table rows)** — first
  envelope build embedded the payload AND still shipped it as the ramdisk:
  84,480,000 B > the 67,108,864 B AVB partition, so AVB refused pre-boot.
  v46b switched to the empty cpio (tegu `ramdisk_size = 0` semantics) →
  42,889,216 B, boots.
- **v46b boot (10:21, sha `7f8394e35098c28d`)** — envelope found and
  validated (`payload-admit DONE 0x3/0x4`), window `0x88000000+160M`
  refused `XNU window overlaps FDT reservation` (stamped `XNU wind`).
- **v47 (10:27, sha `da10ae8fcfc85291`)** — window experiment
  `0x97400000+128M` refused `invalid kernel window` (stamped `invalid `):
  base not 32 MiB-aligned — the alignment gate working as designed.
- **v48 (10:30, sha `38934b4945b15c18`)** — `0x98000000+112M` refused
  `insufficient kernel headroom` (stamped `insuffic`): the payload needs
  92.1 MiB of kernel + 64 MiB headroom = 156.1 MiB minimum.
- **v49 (10:33, sha `78d3f8d5a6d0da1e`) and v50 (10:37, sha
  `a80478a83f984ffa`)** — `FDT dynamic pool unresolved` refusals (stamped
  `FDT dyna`) on `0xA8000000+256M` and then the correct window
  `0xD0000000+160M`: the stock FDT carries `shared-dma-pool` nodes with no
  `alloc` property (pkvm_iommu_cma, vframe 532 MiB reusable, vstream) that
  the old check treated as fixed reservations — refusing EVERY window.
- **v51 (10:40, sha `9aa84e828405f90a`)** — unresolved pools non-fatal →
  **ADMISSION PASS on `0xD0000000+0x0A000000`** → segments copied →
  EL2→EL1 drop → **XNU booted**: VM bootstrap, IOKit, vnguard/AMFI/Sandbox
  policies, PMRD power ladder, then `%s:%d Assertion failed: %s` panic and
  `MACH Reboot` (~103 s console time). Mirror v2 (seq 6, 7,642 B) +
  breadcrumbs `XNU_ENTRY(6) → XNU_INIT(7) → PANIC(12)` + 90 KB console
  captured via the late pull. **Current blocker = the init assertion after
  PMRD**; note the stamp page itself is consumed on a successful run (XNU
  owns the pmsg zone after the jump) — "no TSHIMST magic" on a post-XNU
  dump is the success signature, not channel loss.

## Attempt log — one line per attempt (appended by run-attempt.sh)

(For v39+ the script recorded the `ep0-live` outcome class — the console
stayed up through the watch window, so no park happened while it was
watching. The dumps were pulled late and decoded afterwards; the decoded
per-attempt verdicts live in the v39–v51 prose section above, and the
attempt dirs' `stamps-decode.txt` files were regenerated 2026-10-27 from
the surviving `late-kernel.log` apertures.)

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
