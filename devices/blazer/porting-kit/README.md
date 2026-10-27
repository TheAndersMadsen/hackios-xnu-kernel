# Porting kit — death instrumentation for the laguna bring-up

> **STATUS (2026-10-27, fold #7): BLOCKER = DYLD `indirect symbol has out of range
> address` — the root-v9 closure-repair residual.** MILESTONE first: **10/11 on
> device — HFS root mount + launchd exec + dyld shared-region reached** (v60/v61,
> mach r4r7-hfs): `hfs: mounted iOSRoot on device b(1, 0)` (the `laguna_hfs`
> bridge + mockfs gate beat the old mockfs steal), `/sbin/launchd` exec'd with no
> ENOEXEC, and dyld ran inside PID 1 (shared-region probes, `minisandbox: request
> 2 … allowed`). Two walls cleared on the way: the ENOEXEC mockfs steal (v55–v59)
> and the `_NDR_record` bind wall — **v61 (root-v9, export-trie + `__TEXT` closure
> repair) no longer names `_NDR_record` anywhere**; launchd bound past its full
> 537-symbol census. The NEW wall (v61): `systrace: launchd[1] map_with_linking_np(…,
> 0x1118) = 22` (EINVAL) → `pid 1 exited -- exit reason namespace 6 subcode 0x9,
> description: indirect symbol has out of range address` — dyld-1066 dying on the
> repaired dylibs' indirect symbol table after the per-segment export-trie remap
> (the residual N1r pre-documented in
> [`evidence-lab/NDR-RECORD-FIX.md`](evidence-lab/NDR-RECORD-FIX.md) §5); launchd
> never reached main. Fix lane in flight: root-v10 (INDIRECT-FIX.md when it lands).
> Wall history: v54 `IONVRAMCHRPHandler creation failed` → cleared by the v55 4-prop
> `/chosen` CHRP fix; v55–v59 errno-8 ENOEXEC → root-caused as the mockfs steal →
> cleared by the laguna_hfs port (v60); v60 `_NDR_record` bind → cleared by root-v9
> (v61). Read the evidence: [`evidence-lab/ENOEXEC-ROOT-MOUNT.md`](evidence-lab/ENOEXEC-ROOT-MOUNT.md)
> (the v55–v59 exec wall dissected to file:line + the execdiag instrument +
> the 5-point v60 success proof), [`evidence-lab/NDR-RECORD-FIX.md`](evidence-lab/NDR-RECORD-FIX.md)
> (the bind wall root-caused on the bytes + the root-v9 repair recipe + the
> DEVICE-CONFIRMED status line), [`evidence-lab/POST-LAUNCHD-MAP.md`](evidence-lab/POST-LAUNCHD-MAP.md)
> (the M1→M3 post-launchd console map: what dyld can print, what PID-1 death
> looks like, which sigtrap walls are N-A on root-v8/v9), and
> [`evidence-lab/ATTEMPTS.md`](evidence-lab/ATTEMPTS.md) (the full ledger, now
> through v61). Earlier folds: [`evidence-lab/BRIDGE-EXEC-DIFF.md`](evidence-lab/BRIDGE-EXEC-DIFF.md)
> (mockfs-steal root cause + the minimal 0001/0002 laguna_hfs port recipe),
> [`evidence-lab/ROOT-TRANSPORT-AUDIT.md`](evidence-lab/ROOT-TRANSPORT-AUDIT.md)
> (payload→md0 byte-perfect, false-mount mechanics), [`evidence-lab/INIT-NVRAM.md`](evidence-lab/INIT-NVRAM.md)
> (the cleared NVRAM wall, dissected), plus the readiness maps for the post-dyld
> stretch: [`evidence-lab/DISPLAY-READY.md`](evidence-lab/DISPLAY-READY.md) (mach symbol
> audit: DC9x00 series is source-only in this mach + level-1 paint recipe),
> [`evidence-lab/USERLAND-READY.md`](evidence-lab/USERLAND-READY.md) (root-v7
> M0–M3 audit; root-v8 restores `libdispatch.dylib`), [`evidence-lab/AMFI-PLAN.md`](evidence-lab/AMFI-PLAN.md)
> (in-kernel miniamfi stand-ins deny nothing — no AMFI panic is possible on
> this lineage). Root side: `WITH_ROOT8=1` on
> [`usb-console/build-next-boot.sh`](usb-console/build-next-boot.sh) carries
> the 28 MiB root-v8 (sha `77dc3e9f…`); v60/v61 booted root-v8/root-v9 builds of
> the same recipe. Recipe and honesty notes in
> [`usb-console/ROOT-INTEGRATION.md`](usb-console/ROOT-INTEGRATION.md).

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
| [`evidence-lab/`](evidence-lab/) | The reproducible experiment pattern: `EVIDENCE-MAP.md` (channel×offset×decoder table + per-outcome command blocks), `decode-shim-stamps.py` (TSHIMST1/2 trail decoder, selftest included), `run-attempt.sh` (ONE command per device attempt: arm → boot → watch → park → collect → decode → one-line verdict), `ADT-NODES.md` (what the ADT emits now: `/options` + G2 nodes), `INIT-ASSERTION.md` (the v51/1695 wall — retired), `INIT-NVRAM.md` (the v54 NVRAM wall dissected to file:line, plus the 4-prop fix that cleared it), **`BRIDGE-EXEC-DIFF.md` (the launchd ENOEXEC root cause — mockfs steals the HFS+ root because the foundation kernel has no HFS driver — plus the minimal `mockfs_check_macho` + `laguna_hfs` port recipe)**, **`ROOT-TRANSPORT-AUDIT.md` (payload→md0 transport byte-perfect; the false-mount mechanics behind the "successful" root mount)**, **`DISPLAY-READY.md` (mach symbol audit: the DC9x00 probe/splash series is source-only in the running mach + the level-1 paint recipe for v56)**, **`USERLAND-READY.md` (root-v7 M0–M3 audit: launchd/dyld/plists/identity all clean; the carved `libdispatch.dylib` is the single missing LC_REEXPORT_DYLIB — root-v8 candidate restores it)**, **`AMFI-PLAN.md` (miniamfi stand-ins deny nothing — no AMFI wall exists on this lineage, exec kill paths verified inert)**, **`ENOEXEC-ROOT-MOUNT.md` (the v55–v59 launchd ENOEXEC wall: progression table, the file:line mockfs-steal chain, the execdiag instrument, the 5-point v60 success proof)**, **`NDR-RECORD-FIX.md` (the `_NDR_record` bind wall root-caused on the bytes — empty closure export tries + hollow `__TEXT` — and the root-v9 repair recipe; status line: DEVICE-CONFIRMED, v61 clears it; the new `indirect symbol has out of range address` wall documented)**, **`POST-LAUNCHD-MAP.md` (the post-launchd console map: dyld stderr arrives only as the PID-1 exit reason, this tree always panics on PID-1 death, sigtrap walls N-A on the 28 MiB closure)**, `ATTEMPTS.md` (the attempt ledger: 2026-10-26/27 backfill, v36–v51 EP0-determinism + payload-admission campaign, the v53/v54 NVRAM-wall rows, v55–v59 through the ENOEXEC wall, and v60–v61 — HFS root mount, launchd exec'd, dyld shared-region, `_NDR_record` cleared, map_with_linking_np EINVAL) | B2 evidence-map + B5 attempt-runner + S1 stamp-decoder + P1/P2/P6 init-wall lanes + K1 (execdiag instrument), K2 (transport audit), K3 (bridge-exec diff), Q2 (AMFI), Q3 (display), Q5 (userland). The attempt pattern — preconditions abort, never wait; decode stamps FIRST; one-line verdicts — is the transferable art |
| [`evidence-channels/`](evidence-channels/) | Instruction-level ABL/SoC truth: `ABL-USB-STATE.md` (what the fastboot handoff does/doesn't tear down), `ABL-MBA-PROTOCOL.md` (the LPCM mailbox wire: mbox[2] 0x05250000 qid 0x12, doorbell/GO/in-place reply), `ABL-LPCM-REQUEST.md` (the 0xf8 refusal decoded + the corrected request frame: cmd 0x02 dual-id table, success = reply dword1 == 0), `ABL-RUNTIME-FACTS.md` (device abl.log facts), `DISPLAY-PRODUCER.md` (CDPHY dies 41 ms pre-jump; 128-byte handoff + revive ladder), **`PAYLOAD-ADMISSION.md` (the v39–v51 campaign: embedded envelope, payload validation, window selection vs the stock FDT reservation map, LB_STEP/LB_FAIL instrumentation, the v51 XNU-boot record)**, **`ROOT-INTEGRATION.md` (root-v7 opt-in on `build-next-boot.sh`: `WITH_ROOT7=1` + ADT window `0x0A400000`, the 64 MiB headroom gate it clears, boot-args contract, device-validation caveats)** | A-ABL/A-ABL2/A-ABL3/C3 lanes + the coordinator's admission fixes, 2026-10-27; ROOT-INTEGRATION from the P3 root lane. Host-only capstone teardown of the live stock ABL (`deepspace-17.4-16433848`) + DT decompile + saved captures |
| [`EVIDENCE-CHANNELS.md`](EVIDENCE-CHANNELS.md) | The measured pmsg (PA `0x95400000`) collision map and the operating rules it forces | R5 evidence-channel relocation lane, from today's on-device dumps |
| [`AGENTS.md`](AGENTS.md) | The project playbook (read FIRST): read order, one-paragraph project brief, current frontier (v61) with exact console text, what produced progress vs what wasted time (both with named evidence), collaboration protocol, host-only command crib, verbatim safety rails, artifact map | A1 retrospective lane, distilled from the 2026-10-26→27 session (v2 → v61); folded verbatim |

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
