# Porting kit — death instrumentation for the laguna bring-up

> **STATUS (2026-10-27, attempt v51): XNU BOOTS ON THE PHONE.** `fastboot
> boot` of the embedded-envelope candidate (window `0xd0000000+0x0A000000`)
> passed payload admission, dropped EL2→EL1, and ran native XNU through VM
> bootstrap, IOKit, the vnguard/AMFI/Sandbox security policies, and PMRD's
> power-state ladder — then panicked on an init assertion right after PMRD
> (`"%s:%d Assertion failed: %s"`, `MACH Reboot`). That assertion is the
> current blocker. The panic is fully captured (XNU-MSGBUF **v2** mirror
> seq 6, breadcrumbs `XNU_ENTRY(6)→XNU_INIT(7)→PANIC(12)`, 90 KB console —
> reader verdict `PANICKED | XNU entered=yes`). Enablers proven on device:
> deterministic EP0 console (v39+), the stamped attempt loop
> (force-fastboot → boot → stamp → park → collect → decode), mirror v2,
> and the tegu-proven entry contract (embedded TEGUPLD1 envelope +
> `image_size` raise + EL2→EL1 drop). See
> [`evidence-channels/PAYLOAD-ADMISSION.md`](evidence-channels/PAYLOAD-ADMISSION.md)
> and the v39–v51 section of
> [`evidence-lab/ATTEMPTS.md`](evidence-lab/ATTEMPTS.md).

Everything in this directory is folded from today's validated research lanes so
other porters can use it without the research workspace. Host-only unless a
doc explicitly says otherwise: the reader and builder never touch a device,
and the USB runbook is an operator procedure requiring approval.

Start with [`EVIDENCE-CHANNELS.md`](EVIDENCE-CHANNELS.md) — the measured map of
which memory channels survive XNU death and which get clobbered. Then
[`evidence-lab/EVIDENCE-MAP.md`](evidence-lab/EVIDENCE-MAP.md) — the five live
evidence channels as one verified system, with per-outcome command blocks.

| Directory | What | Provenance |
| --- | --- | --- |
| [`mirror-panic-capture/`](mirror-panic-capture/) | `0001-laguna-0129-*.patch` — always-on XNU msgbuf mirroring + RAM-dump stages (apply to an XNU 8796 tree); `XNU-MSGBUF-v1.md` — the header/ring wire contract | R4 panic-capture lane. Validated Mach-O hash `cae95ab12…` (15,102,200 B); QEMU default-on 12/12 mirror checks, opt-out 5/5, host selftests 11/11. Superseded on device by the v2 relocation (header pmsg+0x3F800) — the reader and runbook handle both |
| [`ramdump-reader/`](ramdump-reader/) | `read-ramdump.py` + `./selftest` — standalone DRAM/pstore-aperture reader: persistent_ram linearization, PAN1 channels, XNU-MSGBUF v1 mirror, LGLW stage breadcrumbs, symbolication, one mechanical verdict line | R2 ramdump lane, v2-era copy. **57/57 selftest** from this directory. `decode-device-panic.py` and `make-r2-fixtures.py` ship alongside so the kit is self-contained. Note: this copy predates the v2 relocation — it decodes the v1 layout only; for `XNU-MSGBUF v2` (header pmsg+0x3F800) use the updated research reader or the layout notes in [`usb-console/RUNBOOK.md`](usb-console/RUNBOOK.md) §8 |
| [`usb-console/`](usb-console/) | The polled DWC3 EP0 diagnostic console: `PROTOCOL.md`, `BRINGUP.md`, `HARDWARE-ENABLE.md`, `RUNBOOK.md` (v51 flow: force-fastboot REQUIRED, **embedded TEGUPLD1 envelope + `image_size` raise**, window `0xd0000000+0x0A000000`, warm-skip (GSNPSID-only verdict) + corrected LPCM cold path, expected stamp trails incl. the admission refusal dictionary and the XNU-boots success branch), `build-next-boot.sh` | A3/S1 USB console lane. Shim host-validated; on device 2026-10-27 EP0 enumeration is deterministic (v39–v51 every run) and v51 carried XNU through admission to a running kernel. `build-next-boot.sh` accepts v1/v2 machs and embeds the payload (`ramdisk_size = 0` semantics — the v46 64 MiB-budget lesson) |
| [`evidence-lab/`](evidence-lab/) | The reproducible experiment pattern: `EVIDENCE-MAP.md` (channel×offset×decoder table + per-outcome command blocks), `decode-shim-stamps.py` (TSHIMST1/2 trail decoder, selftest included), `run-attempt.sh` (ONE command per device attempt: arm → boot → watch → park → collect → decode → one-line verdict), `ATTEMPTS.md` (the attempt ledger: 2026-10-26/27 backfill, v36–v38, and the **v39–v51 EP0-determinism + payload-admission campaign** ending in XNU boots + PMRD panic) | B2 evidence-map + B5 attempt-runner + S1 stamp-decoder lanes. The attempt pattern — preconditions abort, never wait; decode stamps FIRST; one-line verdicts — is the transferable art |
| [`evidence-channels/`](evidence-channels/) | Instruction-level ABL/SoC truth: `ABL-USB-STATE.md` (what the fastboot handoff does/doesn't tear down), `ABL-MBA-PROTOCOL.md` (the LPCM mailbox wire: mbox[2] 0x05250000 qid 0x12, doorbell/GO/in-place reply), `ABL-LPCM-REQUEST.md` (the 0xf8 refusal decoded + the corrected request frame: cmd 0x02 dual-id table, success = reply dword1 == 0), `ABL-RUNTIME-FACTS.md` (device abl.log facts), `DISPLAY-PRODUCER.md` (CDPHY dies 41 ms pre-jump; 128-byte handoff + revive ladder), **`PAYLOAD-ADMISSION.md` (the v39–v51 campaign: embedded envelope, payload validation, window selection vs the stock FDT reservation map, LB_STEP/LB_FAIL instrumentation, the v51 XNU-boot record)** | A-ABL/A-ABL2/A-ABL3/C3 lanes + the coordinator's admission fixes, 2026-10-27. Host-only capstone teardown of the live stock ABL (`deepspace-17.4-16433848`) + DT decompile + saved captures |
| [`EVIDENCE-CHANNELS.md`](EVIDENCE-CHANNELS.md) | The measured pmsg (PA `0x95400000`) collision map and the operating rules it forces | R5 evidence-channel relocation lane, from today's on-device dumps |

## Verify a fresh clone in one minute

```sh
cd devices/blazer/porting-kit/ramdump-reader
./selftest          # expect: selftest PASS (57/57)
```

The selftest is fully synthetic and host-only. Full symbolication fidelity
needs the exact 0128 payload (`xnu-laguna141-paniccap-0128.macho`,
SHA-256 `cd6ca158…`) — the reader finds it in a workspace `out/` directory or
take `LAGUNA_READER_ROOT=<workspace>` / `--mach <path>`; without it, analysis
still runs and symbolication degrades honestly.

## Notes on the fold

- The kit copies are verbatim lane deliverables except `read-ramdump.py`,
  which gained a small path-resolution fallback (`_companion`,
  `_find_mach0128`) so it works from this directory on any clone; the
  canonical research-tree copies take precedence when present.
- `usb-console/build-next-boot.sh`, `evidence-lab/run-attempt.sh`, and the
  `evidence-lab/` + `evidence-channels/` docs reference the fleet workspace
  layout (`research/…` paths, the proven v6 root, the AVB test key, the
  4 MiB dump aperture). Treat the scripts as the reference recipes and the
  docs as the ground truth they were verified against; until those inputs
  exist in your tree, the docs still tell you exactly what to measure and
  why.
- The shim sources themselves are not duplicated here and are not yet on
  this branch; the fleet workspace integrates them at
  `hackios-xnu-kernel/blazer/teguload/shim/`. That tree moves daily, so the
  v51 runbook was frozen against the 2026-10-27 ~11:15 workspace state —
  the tree that built the v51 candidate (frozen image sha256
  `9aa84e828405f90a…`) — key sources, SHA-256 prefixes: `loader.c
  88d43e01`, `payload.h 183b642e`, `fdt.c 218de075`, `laguna_usb.c
  da80e312`, `tgl_usb_console.c 23308a6d`, `shim.c ee16f7ad`, `wdt.c
  385be3c8`, `start.S 6b7923d6`, `shim/build.sh 70d6e0d2`,
  `teguload/mkpayload.py 568fd8bb`, `teguload/mkadt.py c8d5b2c4`,
  `tools/build-next-boot.sh 64333c98`. If a later shim changes the flow,
  the `evidence-channels/` wire truth (incl. PAYLOAD-ADMISSION.md) is the
  stable layer.
- Decode order on any dump: stamps → mirror/breadcrumbs → `abl.log` →
  pstore (see `evidence-lab/EVIDENCE-MAP.md` §3; and its §4.3 freshness
  hazard before believing any "XNU-ENTERED" verdict).
