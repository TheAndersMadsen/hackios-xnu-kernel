# Porting kit — death instrumentation for the laguna bring-up

Everything in this directory is folded from today's validated research lanes so
other porters can use it without the research workspace. Host-only unless a
doc explicitly says otherwise: the reader and builder never touch a device,
and the USB runbook is an operator procedure requiring approval.

Start with [`EVIDENCE-CHANNELS.md`](EVIDENCE-CHANNELS.md) — the measured map of
which memory channels survive XNU death and which get clobbered.

| Directory | What | Provenance |
| --- | --- | --- |
| [`mirror-panic-capture/`](mirror-panic-capture/) | `0001-laguna-0129-*.patch` — always-on XNU msgbuf mirroring + RAM-dump stages (apply to an XNU 8796 tree); `XNU-MSGBUF-v1.md` — the header/ring wire contract | R4 panic-capture lane. Validated Mach-O hash `cae95ab12…` (15,102,200 B); QEMU default-on 12/12 mirror checks, opt-out 5/5, host selftests 11/11 |
| [`ramdump-reader/`](ramdump-reader/) | `read-ramdump.py` + `./selftest` — standalone DRAM/pstore-aperture reader: persistent_ram linearization, PAN1 channels, XNU-MSGBUF v1 mirror, LGLW stage breadcrumbs, symbolication, one mechanical verdict line | R2 ramdump lane, v2. **57/57 selftest** from this directory. `decode-device-panic.py` and `make-r2-fixtures.py` ship alongside so the kit is self-contained |
| [`usb-console/`](usb-console/) | The polled DWC3 EP0 diagnostic console: `PROTOCOL.md`, `BRINGUP.md`, `HARDWARE-ENABLE.md`, `RUNBOOK.md`, `build-next-boot.sh` | A3 USB console lane. Shim host-validated **306/306 checks** (plus the same suite under ASan/UBSan); device bring-up still pending |
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
- `usb-console/build-next-boot.sh` references the fleet workspace layout
  (`research/…` paths, the proven v6 root, the AVB test key). Until those
  inputs exist in your tree, treat it as the reference recipe for the
  candidate build: R4-mirror gate, cmdline, and the AVB signing profile.
- The shim sources themselves are not duplicated here and are not yet on this
  branch; the fleet workspace integrates them at
  `hackios-xnu-kernel/blazer/teguload/shim/` (306-check host validation above
  refers to that tree). The docs in [`usb-console/`](usb-console/) specify the
  complete wire ABI, so a fresh implementation can be validated against them.
