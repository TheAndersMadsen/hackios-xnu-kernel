# Porting kit — death instrumentation for the laguna bring-up

> **STATUS (2026-10-28, fold #9): BLOCKER = the dyld-bootstrap D-loop — a silent
> EL0 spin inside dyld-1066.8's `PersistentAllocator::aligned_alloc` BTree walk,
> windowed by the boottrace instrument after marker M02 (persistent allocator
> created); decode W-A2 of
> [`evidence-lab/ALLOCATOR-HANG.md`](evidence-lab/ALLOCATOR-HANG.md) §4. Fix lane:
> P1 → root-v18.** MILESTONE (device, unchanged since v63): **10/11 — HFS root
> mount + launchd exec + dyld shared-region** (mach r4r7-hfs; `hfs: mounted
> iOSRoot on device b(1, 0)`, `/sbin/launchd` exec'd, `minisandbox: request 2 …
> allowed`). The kernel-side walls are ALL dead; every death since v61 is inside
> the userland closure/dyld bootstrap, and **every wall in this class is now
> reproduced marker-for-marker in QEMU** before any device boot (F2 bridge:
> exact device kernel bytes, ADT swapped). Wall chain since fold #7, one doc
> each: **W-indirect** `indirect symbol has out of range address` → cleared on
> DEVICE by root-v10's 554 section-field translations + 31 GOT flag flips
> ([`evidence-lab/INDIRECT-FIX.md`](evidence-lab/INDIRECT-FIX.md); §5 honesty
> note: the shipped root-v9 was later proven byte-identical in CONTENT to
> root-v8 — the v60→v61 wall change was layout/extent, not the N1r repairs);
> **W-NXArgcPtr** SIGSEGV at `dyld4::prepare`+0x9c4 (v63, namespace 2 subcode
> 0xb) → un-applied cache chained-fixup rebase in libdyld `__dyld4`
> ([`evidence-lab/POINTER-REBASE.md`](evidence-lab/POINTER-REBASE.md),
> [`evidence-lab/SUBCACHE-POINTERS.md`](evidence-lab/SUBCACHE-POINTERS.md);
> fixed by the disjoint compacted 0x300000000+ layout, root-v12, + the
> mapSegments preferred-load dyld patch) → **W-TLS** NULL+8 deref in
> `_cerror_nocancel` (v65b) → TLS-free errno/lock support libs
> ([`evidence-lab/NULL-DEREF.md`](evidence-lab/NULL-DEREF.md), v65b frontier
> correction included) → **W-STUB** 1-instruction ret-to-self livelock (the
> BSD stub's `bl _cerror` clobbered LR; v66b) → 48/48 error-path LR
> save/restore ([`evidence-lab/SYSCALL-STUBS.md`](evidence-lab/SYSCALL-STUBS.md))
> → **W-SILENT** healthy-scheduler spin with zero console (v67b; kernel
> systrace prints only failed syscalls ON RETURN, so D3 built the **boottrace
> instrument**: 13 unique-length `write(2)` markers + per-image MP events,
> decoded by LENGTH alone —
> [`evidence-lab/BOOTSTRAP-LOCKS.md`](evidence-lab/BOOTSTRAP-LOCKS.md) §6) →
> **W-ALLOC** M01→M02 then spin (v68b) → D4's 16-retry guard + A1/A2/A3
> allocator markers → v69b: M01→A1→A2×2→M02→A1→A2, zero A3 = hang INSIDE the
> BTree scan ([`evidence-lab/ALLOCATOR-HANG.md`](evidence-lab/ALLOCATOR-HANG.md)).
> The dyld running now is SOURCE-BUILT dyld-1066.8
> ([`evidence-lab/DYLD-BUILD.md`](evidence-lab/DYLD-BUILD.md) — the shims +
> patches recipe, built adhoc with header parity), not the extracted cache dyld.
> The syscall-absorption map that keeps pid 1 alive this deep:
> [`evidence-lab/SYSCALL-DELTA.md`](evidence-lab/SYSCALL-DELTA.md) (blocking
> items = 0; every pre-wall failure is absorbed); the post-fix launchd map:
> [`evidence-lab/LAUNCHD-NEXT.md`](evidence-lab/LAUNCHD-NEXT.md) (W1–W5
> discriminators, log-visibility verdict). Older wall history (v54 NVRAM →
> v55–v59 ENOEXEC mockfs steal → v60 `_NDR_record` → v61 indirect): see
> [`evidence-lab/ENOEXEC-ROOT-MOUNT.md`](evidence-lab/ENOEXEC-ROOT-MOUNT.md),
> [`evidence-lab/NDR-RECORD-FIX.md`](evidence-lab/NDR-RECORD-FIX.md),
> [`evidence-lab/POST-LAUNCHD-MAP.md`](evidence-lab/POST-LAUNCHD-MAP.md), and
> [`evidence-lab/ATTEMPTS.md`](evidence-lab/ATTEMPTS.md) (the full ledger, now
> through v69 with the QEMU-only candidates documented in prose). Earlier
> folds: [`evidence-lab/BRIDGE-EXEC-DIFF.md`](evidence-lab/BRIDGE-EXEC-DIFF.md)
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
> the 28 MiB root-v8 (sha `77dc3e9f…`); the same recipe carried root-v9
> (device, v61) through root-v10 (device, v63) to root-v17 (QEMU-only,
> v69b) — root content is pinned per profile with a size:sha `ROOT_PIN`
> gate that refuses a wrong rootfs. Recipe and honesty notes in
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
| [`evidence-lab/`](evidence-lab/) | The reproducible experiment pattern: `EVIDENCE-MAP.md` (channel×offset×decoder table + per-outcome command blocks), `decode-shim-stamps.py` (TSHIMST1/2 trail decoder, selftest included), `run-attempt.sh` (ONE command per device attempt: arm → boot → watch → park → collect → decode → one-line verdict), `ADT-NODES.md` (what the ADT emits now: `/options` + G2 nodes), `INIT-ASSERTION.md` (the v51/1695 wall — retired), `INIT-NVRAM.md` (the v54 NVRAM wall dissected to file:line, plus the 4-prop fix that cleared it), **`BRIDGE-EXEC-DIFF.md` (the launchd ENOEXEC root cause — mockfs steals the HFS+ root because the foundation kernel has no HFS driver — plus the minimal `mockfs_check_macho` + `laguna_hfs` port recipe)**, **`ROOT-TRANSPORT-AUDIT.md` (payload→md0 transport byte-perfect; the false-mount mechanics behind the "successful" root mount)**, **`DISPLAY-READY.md` (mach symbol audit: the DC9x00 probe/splash series is source-only in the running mach + the level-1 paint recipe for v56)**, **`USERLAND-READY.md` (root-v7 M0–M3 audit: launchd/dyld/plists/identity all clean; the carved `libdispatch.dylib` is the single missing LC_REEXPORT_DYLIB — root-v8 candidate restores it)**, **`AMFI-PLAN.md` (miniamfi stand-ins deny nothing — no AMFI wall exists on this lineage, exec kill paths verified inert)**, **`ENOEXEC-ROOT-MOUNT.md` (the v55–v59 launchd ENOEXEC wall: progression table, the file:line mockfs-steal chain, the execdiag instrument, the 5-point v60 success proof)**, **`NDR-RECORD-FIX.md` (the `_NDR_record` bind wall root-caused on the bytes — empty closure export tries + hollow `__TEXT` — and the root-v9 repair recipe; status line: DEVICE-CONFIRMED, v61 clears it; the new `indirect symbol has out of range address` wall documented)**, **`POST-LAUNCHD-MAP.md` (the post-launchd console map: dyld stderr arrives only as the PID-1 exit reason, this tree always panics on PID-1 death, sigtrap walls N-A on the 28 MiB closure)**, **`INDIRECT-FIX.md` (the v61 indirect-symbol wall: 554 section-field translations + 31 GOT flag flips → root-v10; §5 fleet-critical provenance correction: the shipped root-v9 ≡ root-v8 content, so the v60→v61 wall change was layout/extent, not the N1r repairs)**, **`POINTER-REBASE.md` (wall-#3: un-applied cache chained-fixup rebase in libdyld `__dyld4` = `NXArgcPtr`, the v63 SIGSEGV decoded byte-exactly; 11,757-pointer rebase → root-v11)**, **`SUBCACHE-POINTERS.md` (wall-#4 root cause: stock dyld never honors preferred vmaddrs so rebase-less splits dangle; disjoint compacted 0x300000000+ layout + 16,606 pointer fixes → root-v12 + the mapSegments preferred-load dyld patch)**, **`NULL-DEREF.md` (wall-#6: TLS errno NULL+8 deref on the cacheless boot's first error return; TLS-free errno + constant-owner lock support libs → the v14 dyld)**, **`DYLD-BUILD.md` (the SOURCE-BUILT dyld-1066.8: 24-shim SDK-wall recipe, 68 raw-syscall stubs verified against the booted kernel's tables, adhoc parity)**, **`SYSCALL-STUBS.md` (the stub LR-livelock wall: `bl _cerror` clobbers x30 → ret-to-self; 68/68 svc audit + 48 error-path LR save/restores → the v15 dyld)**, **`BOOTSTRAP-LOCKS.md` (the boottrace instrument: 13 unique-length write(2) markers decoded by LENGTH alone + W-L1–W-L5 decode table; D3 barrier-only locks)**, **`ALLOCATOR-HANG.md` (W-ALLOC, the current frontier: `aligned_alloc` BTree-walk spin after M02; 16-retry guard + A1/A2/A3 markers + W-A1–A6 decode table; v69b = W-A2)**, **`SYSCALL-DELTA.md` (the absorbed-set map: blocking items = 0, every pre-wall failure absorbed — why pid 1 survives this deep)**, **`LAUNCHD-NEXT.md` (post-bind launchd map: consolidated plist, console log visibility, W1–W5 wall discriminators)**, `ATTEMPTS.md` (the attempt ledger: 2026-10-26/27 backfill, v36–v51 EP0-determinism + payload-admission campaign, the v53/v54 NVRAM-wall rows, v55–v59 through the ENOEXEC wall, v60–v63 on device — HFS root mount, launchd exec'd, dyld shared-region, `_NDR_record` → indirect-symbol → NXArgcPtr walls — and v64–v69 documented as QEMU-only prose with no device results claimed) | B2 evidence-map + B5 attempt-runner + S1 stamp-decoder + P1/P2/P6 init-wall lanes + K1 (execdiag instrument), K2 (transport audit), K3 (bridge-exec diff), Q2 (AMFI), Q3 (display), Q5 (userland), F1/F1b/F1c/F1d (indirect→pointer→layout→dyld walls), D1/D2/D3/D4 (dyld build → stubs → locks/boottrace → allocator), F5 (syscall delta), K3 (launchd-next). The attempt pattern — preconditions abort, never wait; decode stamps FIRST; one-line verdicts; build in-kernel/in-dyld diagnostic instruments BEFORE burning cycles — is the transferable art |
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
