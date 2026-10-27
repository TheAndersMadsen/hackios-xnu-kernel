# ROOT-INTEGRATION — root-v7 embedded in the next-boot payload (laguna / Pixel 10 Pro)

Lane: **P3 (root/userland readiness)** · 2026-10-27 · **host-only** (no device, no boots; host builds + validators only).
Mission: make root-mount + launchd ready BEFORE the kernel passes the NVRAM wall, so no device cycle is wasted.
Reads first: `USERLAND-PATH.md` (M0–M8 ladder), `ROOT-TRANSPORT.md` (v3.7/v3.8/v3.9 phasing), `root-upgrade/BUILD-ROOT-V7.md` (R2's image lane), `ufs-staging/PLAN.md` (v3.9 freeze).

---

## 1. State of root-v7 (verified today)

| Item | Value |
|---|---|
| Image | `research/laguna-usb-fix/root-upgrade/root-v7.hfs` |
| Size | **29,360,128 B = 28 MiB** (R2 cap 32 MiB; +4,194,304 B = +4 MiB exact vs the v6 root) |
| SHA-256 | **`72bf50b931758a2f2de4b4520a02e4b5177093a820171e82bfcd3878383259ab`** |
| Format | raw case-sensitive journaled HFS+, `HX` at offset 1024, volname `iOSRoot` |
| Content | 198 files / 156 dirs / 6 symlinks; real `/sbin/launchd` (541,600 B) + **`/usr/lib/dyld` 922,912 B (foundation `a453aa23…`)** + 35 retained carved closure dylibs + 143 dslocal identity files (passwd/group/dslocal nodes) + `/System/Cryptexes/{OS,App}` layout with the native cache directory **present but empty** |
| Delta vs v6 | 144 added files + 13 added dirs + 1 deletion (carved `libdispatch.dylib`, so it cannot shadow the future native cache) |

Re-validation performed this rotation (all host-only):

- R2's full gate `root-upgrade/tools/verify-v7.sh` **re-run → "V7 VERIFY COMPLETE"**: `fsck_hfs -fn` clean, 955 content checks + 162 identity checks PASS, image hash stable across every read-only mount (`72bf50b9…` before = after), `closure-manifest.txt` byte-identical (`1288b782…`).
- `ufs-staging/` contains only docs (`PLAN.md`, `probe-ladder.md`) — no scripts; nothing to run there.

**Honest scope (unchanged from R2):** v7 is the M0–M2 bootstrap/recovery root with M3-*ready* identity and native-cache layout. It does **not** complete dyld — the native cryptex cache is 2.536 GiB and must arrive via the v3.9 UFS transport. Empty-cache directory is by design.

## 2. Where boot-args come from (the C-string chain)

1. `research/laguna-usb-console/tools/build-next-boot.sh` composes `CMDLINE='…'` (line ~60) and passes it to `mkpayload.py --cmdline "$CMDLINE"`.
2. `hackios-xnu-kernel/blazer/teguload/mkpayload.py` writes the string at **payload offset `0x48`**, NUL-padded into the 608-byte `BOOT_LINE_LENGTH` field (`cl = cmdline.encode()[:607]`; the buffer is zero-filled, so termination is implicit), and the length is NOT re-checked against the shim's copy loop — keep cmdlines < 608 chars.
3. The shim (`teguload/shim/loader.c`) copies `p->cmdline` into `boot_args.CommandLine` (offset 108 in the 736-byte `boot_args`), and the entry stub jumps to XNU with **x0 = boot_args PA**. `payload.h:tgl_payload_validate` refuses an unterminated 608-byte line.
4. (The kexec lane composes its own cmdline in `kexec-xnu.c kx_build_cmdline` — not the shim path; the shim payload is the live delivery.)

`rd=md0` + the ADT `/chosen/memory-map` `RAMDisk = <pa,size>` (written by `mkpayload.add_ramdisk_to_adt`) is the whole root-mount contract; `rootrw=1` mounts RW. The kernel also recognizes `rd=ufsroot0` (`IOKitBSDInit.cpp:833` → `hakios_blazer_ufs_rootdev`) — that is the v3.9 profile, frozen in `ufs-staging/PLAN.md`, **not** implemented in the builder (the shim lacks the U0–U9 UFS bring-up ladder).

## 3. THE BLOCKER FOUND (this is why P3 existed today)

The shim refuses any payload whose metadata top leaves **< 64 MiB headroom** in the kernel window — `payload.h:tgl_payload_validate`: `window_end - top < 0x4000000 → "insufficient kernel headroom"`. The v6 payload passes with only ~3.7 MiB to spare; **v7 (+4 MiB ramdisk) tips it over**. Host-proof, using the REAL shim admission code (`teguload/tests/host-admission.sh` compiles `payload.h` unmodified):

| Payload | ADT window (`mem_size`) | Verdict (real `tgl_payload_validate`) |
|---|---|---|
| v54 as built (v6 root) | 0x0A000000 | **PASS** (control; matches its on-device run) |
| root-v7 payload, today's window | 0x0A000000 | **`insufficient kernel headroom`** — the shim would park on device |
| root-v7 payload, window +4 MiB | **0x0A400000** | **PASS** (0 failures) |

Exact geometry of the fixed build: `phys_base=0xd0000000`, `mem_size=0x0A400000`, ramdisk PA `0xd4424000` size `0x1C00000`, ADT 18,836 B (P2's current mkadt output), `top=0xd6030000`, `window_end=0xda400000`, headroom **71,106,560 B (67.81 MiB) ≥ 67,108,864 B**. Under the old window the same ADT misses the rule by **135,168 B** (with v54's 10,540 B ADT: by 131,072 B).

Note: R2's `root-upgrade/validation/mkpayload.json` witness recorded a *build-side* pass only — `mkpayload.py` does not run the shim's headroom rule. Without this catch, a v7 boot would have burned a device cycle parked at the shim admission gate.

**Fix:** `WITH_ROOT7=1` in the builder also raises the ADT window one notch (`mkadt.py --mem-size=0x0A000000 → 0x0A400000`). This grows the kernel-visible DRAM window 160→164 MiB at `0xd0000000` — `mkadt.py` itself is untouched (P2 owns it), and the value stays far from mkadt's ramoops-overlap guard. **DEVICE-VALIDATE:** the extra 4 MiB of claimed kernel RAM has not been exercised on the phone; if the next boot shows memory-corruption signatures, the first suspect is this window bump.

## 4. Integration recipe (exact)

```bash
# default build (unchanged behavior — v6, window 0x0A000000):
R4_MACHO=$PWD/research/laguna-usb-fix/mach.blazer-r4r5-tcm \
OUT=$PWD/research/laguna-usb-console/out/next-boot-vNN \
  research/laguna-usb-console/tools/build-next-boot.sh

# root-v7 build (opt-in):
WITH_ROOT7=1 R4_MACHO=$PWD/research/laguna-usb-fix/mach.blazer-r4r5-tcm \
OUT=$PWD/research/laguna-usb-console/out/next-boot-vNN-root7 \
  research/laguna-usb-console/tools/build-next-boot.sh
```

Run the script **from inside OUT** (or pre-create `boot.img -> laguna-usb-next-boot-avb.img` there): `avbtool verify_image` opens the hash descriptor as CWD-relative `boot.img` — a pre-existing fleet workflow quirk, not introduced here (v54's dir carries the same symlink).

`WITH_ROOT7=1` does exactly three things, all inside the builder:

1. `ROOTFS=$ROOT/research/laguna-usb-fix/root-upgrade/root-v7.hfs`, pinned `29360128:72bf50b9…` (the v6 pin `25165824:aebc9a8a…` stays the default gate).
2. `mkadt.py --mem-size=0x0A400000` (§3).
3. Boot-args: **unchanged** (§5 — same string is correct for both roots).

**Default-path neutrality proof:** the edited builder run with no flag was compared against a hand-run of the original command sequence: `laguna.adt` differs in exactly 63 bytes inside the `random-seed` property — `mkadt.py:201` does `os.urandom(64)` by design; `payload-usb.bin` differs only in those same embedded seed bytes. Everything else byte-identical. (Consequence worth knowing for triage: two default builds of the SAME tree differ in 64 seed bytes; compare against v54 only modulo the seed.)

**Post-build host validation** (all run today on the WITH_ROOT7 build):

- `research/laguna-usb-fix/root-integration/verify-payload.py <payload> --image <shim Image>` — new P3 tool: TEGUXNU v1 header, cmdline byte-for-byte + NUL/length, embedded root byte-identical to `root-v7.hfs`, ADT `RAMDisk` prop = `<pa,size>`, the payload.h headroom mirror, TEGUPLD1 envelope math. → **ALL CHECKS PASS**.
- `teguload/tests/host-admission.sh <live-fdt.dtb> <payload>` — real shim admission → **OK (0 failures)**.
- `avbtool verify_image` → footer + SHA256_RSA4096 + boot hash verified.

## 5. Boot-args contract

Composed C-string (181 chars) — **identical for v6 and v7**, validated byte-for-byte at payload `0x48` in the built v7 artifact:

```
debug=0x8 serial=3 cpus=1 io.display.iomfb=1 rd=md0 rootrw=1 ignition_level=1 launchd_unsecure_cache=1 kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1 lagunalogdump=1
```

- `rd=md0` — correct for BOTH roots: both are ADT-RAMDisk memory devices. root-v7 does **not** want `rd=ufsroot0`.
- `rootrw=1` — mount md0 RW (HFS+ journaled, volname iOSRoot).
- `ignition_level=1 launchd_unsecure_cache=1` — REQUIRED HFS-contract tokens (M2: launchd reads the on-disk D2 launchd.plist; ignition selects the cryptex-cache path once a real cache exists — inert-but-correct on v7's empty cache dir).
- NOT needed, and correctly absent: `rootfiledev=`, `pageable=`, `private=` — these are physical-device/sleep-token args, meaningless for an ADT RAMDisk md0 root. No token in the payload references them.
- `kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1 lagunalogdump=1` — hygiene + the M0–M6 evidence mirror; keep.

**v3.9 frozen profile** (NOT in the builder until the shim UFS ladder U0–U9 lands): swap `rd=md0` → `rd=ufsroot0` and append
`blazer_ufs=2 blazer_ufs_live=1 blazer_ufs_base=0x3c400000 blazer_ufs_root=<STAGING> blazer_ufs_lun=0`
→ ~286 chars, still < 607. v7 stays embedded as md0 (recovery: boot-arg flip back to `rd=md0`); `rootrw=1` then applies to RAMDisk volumes only; the UFS system volume is RO by driver design. mkpayload today supports one `--ramdisk` (RAMDisk); `RAMDisk1` (root.apfs `/redwood` md1) needs a small mkpayload extension when v3.9 starts.

## 6. Envelope + budget math (exact numbers)

TEGUPLD1 envelope (`shim/build.sh`): `Image = [_end][ "TEGUPLD1" | u64 payload_len | TEGUXNU blob ]`, and the arm64 Image header's `image_size` (u64 at `_head+16`) is raised to `_end + 16 + payload_len`. The shim's guard is `image_size ≤ 128 MiB`.

| Item | v6 build (today's default) | **WITH_ROOT7=1** | Budget |
|---|---|---|---|
| root image | 25,165,824 B (`aebc9a8a…`) | **29,360,128 B (`72bf50b9…`)** | — |
| payload-usb.bin (TEGUXNU) | 41,602,624 B | **45,796,928 B (+4,194,304 exact)** | ≈52 MB raw shim cap: **fits** (−6,203,072 B vs 52,000,000; −8,729,024 B vs 52 MiB) |
| shim `_end` | — | **1,294,336 B** | — |
| `image_size` @ `_head+16` | — | **47,091,280 B** (= `_end+16+payload`, = Image file size) | ≤ 128 MiB guard: fits |
| shim-boot-laguna.img (mkbootimg v4) | 42,909,696 B | **47,104,000 B** | ABL `fastboot boot` budget 67,108,864 B: **fits**, headroom 20,004,864 B (19.08 MiB) |
| laguna-usb-next-boot-avb.img | 67,108,864 B | 67,108,864 B | = PARTITION_SIZE (padded; verify PASS) — same as every prior version |
| kernel window headroom (payload.h rule) | PASS | **PASS (71,106,560 B ≥ 64 MiB)** | the rule that WOULD have failed without §3 |

v54 reference for comparison: payload 41,594,272 B, shim-boot 42,901,504 B. Today's default builds ~8 KiB larger only because P2's current mkadt emits a bigger ADT (18,836 vs 10,540 B) plus the 64-byte random-seed.

**Answer to the mission's fit question: 41.6 MB + 28 MiB fits** — more precisely, the v6 root is *replaced* (not added to): payload grows by exactly +4 MiB to 45,796,928 B (43.68 MiB), and every envelope (`TEGUPLD1`/`image_size=47,091,280`), shim-validate, and ABL budget number above is stated from the built artifact.

## 7. What happens at M0–M3 after root mount (with v7 as md0)

- **M0 → M1:** unchanged mechanics — ADT `RAMDisk` prop + `rd=md0` → IOFindBSDRoot memory-device branch → `hfs: mounted iOSRoot` (RW via `rootrw=1`). v7 is the same volume class; expect the same ramoops/mirror lines. The only new risk is §3's window bump.
- **M1 → M2 (launchd exec):** v7 fixes v6's actual defect here: the vendor-bytes v6 root had **no `/usr/lib/dyld`**, so launchd's `LC_LOAD_DYLINKER` target was missing. v7 ships the foundation dyld (922,912 B, `a453aa23…`) plus 143 identity files (22 UserName / 14 GroupName coverage: root `0:0`, mobile `501:501`, `_securityd`, `_logd`, …) with the proven static-dslocal contract. `ignition_level=1 launchd_unsecure_cache=1` stay REQUIRED (on-disk D2 plist path).
- **M2 → M3 (dyld):** v7 does **not** complete dyld — by construction. dyld will map the 35 retained carved closure dylibs (the v6 recovery closure) and is expected to hit the same namespace-6 class of failure (the `_NDR_record` dead end) unless the native cache is present; v7's `/System/Cryptexes/OS/.../com.apple.dyld` directory is deliberately **empty**. v7's M3 value is *readiness*: real foundation dyld binary, no shadowing carved libdispatch, cryptex layout, and identity records — so the v3.9 `rd=ufsroot0` swap (full APFS from UFS) needs zero payload-layout work, only the shim UFS ladder + the frozen cmdline of §5.
- **Failure posture stays proven:** if anything in the v7 path misbehaves on device, the recovery boot is today's exact build (`rd=md0` + v6), unchanged and admission-proven.

## 8. Files touched / created (all P3-owned)

| File | Change |
|---|---|
| `research/laguna-usb-console/tools/build-next-boot.sh` | opt-in `WITH_ROOT7=1` block (root swap + pin, mem-size bump, profile echo); root-pin python accepts either profile; **default path byte-neutral** (§4) |
| `research/laguna-usb-fix/root-integration/verify-payload.py` | new P3 host validator (payload/cmdline/root/ADT/headroom/envelope/budget) |
| `research/laguna-usb-fix/ROOT-INTEGRATION.md` | this doc |
| `/tmp/fleet/p3-root-report.md`, `/tmp/fleet/COORDINATION.md` | fleet reporting |

Deleted after validation: 382 MB scratch build tree (payloads, Image, boot images) and `root-upgrade/work/` (v6 extraction created by the verify re-run; recreatable per `BUILD-ROOT-V7.md` §5). `root-v7.hfs` and `closure-manifest.txt` hashes unchanged. Not touched: `mkadt.py` (P2), `read-ramdump.py` (P5), `root-upgrade/tools/*`, shim sources.

## Summary (5 lines)

1. root-v7 (28 MiB, `72bf50b9…`, HFS+ iOSRoot, launchd + real foundation dyld + 143 identity files, cryptex layout with empty native-cache dir) is payload-ready and re-passed R2's full host gate today (fsck + 955 content + 162 identity checks, image hash stable).
2. Found and host-proved the blocker a v7 boot would have hit at the shim: `payload.h` requires ≥64 MiB kernel-window headroom after ADT+boot_args; v7 (+4 MiB) misses it by ~128–135 KiB under the 0x0A000000 window — real-code proof via `tests/host-admission.sh` ("insufficient kernel headroom"), v54 control passes.
3. Fix shipped as opt-in `WITH_ROOT7=1` in `build-next-boot.sh`: pinned root swap + `mkadt --mem-size=0x0A400000` (+4 MiB window, mkadt.py untouched; DEVICE-VALIDATE: the extra claimed RAM is unexercised on device); default path proven byte-neutral modulo mkadt's by-design 64-byte `random-seed`.
4. Built and validated the full WITH_ROOT7 artifact chain host-side: payload 45,796,928 B (+4 MiB exact; ≈52 MB raw cap OK), envelope `image_size=47,091,280 B` (`_end` 1,294,336 + 16 + payload; ≤128 MiB guard OK), boot img 47,104,000 B vs ABL 64 MiB budget (19.08 MiB headroom), AVB verify PASS, real shim admission OK, cmdline + embedded root byte-for-byte vs contract.
5. Boot-args contract unchanged (`rd=md0 rootrw=1 ignition_level=1 launchd_unsecure_cache=1 …`; no rootfiledev/pageable/private — RAMDisk roots don't take them; v3.9 `rd=ufsroot0` + `blazer_ufs=*` profile stays frozen until the shim U0–U9 ladder lands); expected reach with v7 = clean M0–M2 with M3-ready layout, M3 itself still requires the UFS full root; recovery posture = today's v6 build unchanged.
