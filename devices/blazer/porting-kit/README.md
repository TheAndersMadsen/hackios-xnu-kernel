# Porting kit — death instrumentation for the laguna bring-up

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
| [`usb-console/`](usb-console/) | The polled DWC3 EP0 diagnostic console: `PROTOCOL.md`, `BRINGUP.md`, `HARDWARE-ENABLE.md`, `RUNBOOK.md` (v3.7 flow: force-fastboot REQUIRED, warm-skip + LPCM cold path, expected stamp trails), `build-next-boot.sh` | A3/S1 USB console lane. Shim host-validated (v3.7: **427 checks** + decoder 11/11 + selftest + ARM64 freestanding gate, all PASS normal and under ASan/UBSan). On device 2026-10-27: EP0 came live once (v3.6 run); frontier = LPCM clock request REFUSED `0xf8`, reply codes being decoded |
| [`evidence-lab/`](evidence-lab/) | The reproducible experiment pattern: `EVIDENCE-MAP.md` (channel×offset×decoder table + per-outcome command blocks), `decode-shim-stamps.py` (TSHIMST1/2 trail decoder, selftest included), `run-attempt.sh` (ONE command per device attempt: arm → boot → watch → park → collect → decode → one-line verdict), `ATTEMPTS.md` (the attempt ledger, incl. the 2026-10-26/27 backfill) | B2 evidence-map + B5 attempt-runner + S1 stamp-decoder lanes. The attempt pattern — preconditions abort, never wait; decode stamps FIRST; one-line verdicts — is the transferable art |
| [`evidence-channels/`](evidence-channels/) | Instruction-level ABL/SoC truth: `ABL-USB-STATE.md` (what the fastboot handoff does/doesn't tear down), `ABL-MBA-PROTOCOL.md` (the LPCM mailbox wire: mbox[2] 0x05250000 qid 0x12, doorbell/GO/in-place reply), `ABL-RUNTIME-FACTS.md` (device abl.log facts), `DISPLAY-PRODUCER.md` (CDPHY dies 41 ms pre-jump; 128-byte handoff + revive ladder) | A-ABL/A-ABL2/C3 lanes, 2026-10-27. Host-only capstone teardown of the live stock ABL (`deepspace-17.4-16433848`) + DT decompile + saved captures |
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
  v3.7 runbook was frozen against the 2026-10-27 09:13 UTC state (key
  sources, SHA-256 prefixes: `tgl_usb_console.c 4b6acd9f`, `tgl_cpm.c
  36178e74`, `stamp.c f935f1fc`, `shim.c fddf27a8`, `start.S 6b7923d6`,
  `loader.c f04be0c4`). If a later shim changes the flow, the
  `evidence-channels/` wire truth is the stable layer.
- Decode order on any dump: stamps → mirror/breadcrumbs → `abl.log` →
  pstore (see `evidence-lab/EVIDENCE-MAP.md` §3; and its §4.3 freshness
  hazard before believing any "XNU-ENTERED" verdict).
