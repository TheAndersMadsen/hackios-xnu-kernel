# Next-boot runbook: USB console → payload admission → XNU (v51 flow)

**All commands below are operator instructions, not actions performed by this
lane.** No device write was performed while preparing them. The boot must be a
parent-approved, temporary `fastboot boot` with the existing unlocked blazer
and a verified Android-A recovery path. Do not flash either image or change
slots/partitions.

Status this flow was verified against: **2026-10-27, attempts v39–v51.** v51
(`out/next-boot-v51/laguna-usb-next-boot-avb.img`, sha256 `9aa84e828405f90a…`)
passed payload admission and **XNU booted on the phone** (VM bootstrap, IOKit,
security policies, PMRD) before panicking on an init assertion — the first
native-XNU boot on laguna silicon. Admission details and the refusal ledger:
[`../evidence-channels/PAYLOAD-ADMISSION.md`](../evidence-channels/PAYLOAD-ADMISSION.md).

## 1. Required inputs

- A **mirror-bearing kernel Mach-O** (`XNU-MSGBUF v1` or `v2` producer;
  `v2` = header at pmsg+0x3F800). The v51 boot used
  `research/laguna-usb-fix/mach.blazer-r4r5-tcm` (v2-only, 16,416,856 B).
  `tools/build-next-boot.sh` refuses a mirror-less Mach-O.
- Pinned 24 MiB recovery root:
  `research/laguna-deviceroot/work/bootstrap-root-proven-v6.hfs`
  (SHA-256 `aebc9a8a410369b19c60c3e845cb814896bd9709264d2c085283cf816439b4bf`,
  25,165,824 B — the root actually staged as kexec md0 in the proven
  on-device ladder; the builder enforces the exact hash).
- AVB test key `/tmp/test-rsa4096.pem`, salt/rollback profile (in the script).
- PyUSB venv at `/tmp/tegu-venv`.
- ABL already able to use the USB controller in peripheral fastboot mode —
  the `fastboot boot` handoff leaves DWC3 **clocked** (warm world), which is
  the state this flow assumes; the shim still probes and can take the cold
  LPCM path if not.
- Parent/device-owner approval and the central device lock. Nothing is
  flashed: the payload rides inside the boot image (see §2).

## 2. Build and verify the candidate (host only)

```sh
cd /path/to/your/workspace          # research/… layout expected by the script
export R4_MACHO=/absolute/path/to/the/mirror-bearing.macho
export OUT=$PWD/research/laguna-usb-console/out/next-boot
research/laguna-usb-console/tools/build-next-boot.sh
cat "$OUT/SHA256SUMS"
```

The helper (this kit's [`build-next-boot.sh`](build-next-boot.sh) is the same
recipe):

1. rejects a mirror-less Mach-O and validates the exact root hash;
2. emits the ADT + TEGUXNU payload with the **v51 window** —
   `mkadt.py --phys-base=0xd0000000 --mem-size=0x0A000000`
   (32 MiB-aligned base, 160 MiB = 92.1 MiB kernel + 64 MiB headroom + slack;
   why this exact window: PAYLOAD-ADMISSION.md §4);
3. builds the shim and **embeds the payload as a TEGUPLD1 envelope directly
   after `_end`, raising the arm64 Image `image_size` to cover it** — the
   build prints `envelope embedded: _end=… payload=… image_size=…`;
4. ships an **empty cpio as the ramdisk** (tegu `ramdisk_size = 0` semantics —
   the envelope IS the delivery; shipping the payload twice busts the 64 MiB
   AVB partition budget, the v46 lesson);
5. re-runs `shim/build.sh` and generates a fresh per-build TSHIMST2 nonce —
   the frozen `next-boot-*` image is the only nonce ledger (evidence-lab map
   §4.1);
6. signs a 64 MiB `laguna-usb-next-boot-avb.img` (SHA256_RSA4096
   /rollback/salt/key profile) and runs `verify_image`.

`out/shim-boot-laguna.img` built without a payload is only a diagnostic
artifact (no envelope: `image_size == _end`) and must not be substituted.

## 3. Start the log reader before the temporary boot

```sh
cd /path/to/your/workspace
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py \
  --timeout 120 | tee research/laguna-usb-console/out/next-boot-live.log
```

Use shell redirection, not `usb_log.py --output`, because the venv's path
allowlist points into the Tegu repository. Leave this single EP0 client
running. It waits for VID:PID `1209:0001` and product
`Tegu USB diagnostic`, then replays the log from offset zero in ≤512-byte
reads. The protocol's exact read is:

```
bmRequestType=0xc0 bRequest=0x5a
wValue=byte_offset & 0xffff
wIndex=byte_offset >> 16
wLength<=512
```

Each served transfer also leaves a per-request stamp in the shim's trail
(PAYLOAD-ADMISSION.md §5), so the dump names the last EP0 transfer even if
the host log is lost.

## 4. Force-fastboot: REQUIRED before every boot attempt

**Device-proven on blazer, 2026-10-27: arm force-fastboot before every
`fastboot boot`.** Any shim failure reset — and now also XNU's panic reset —
then lands in fastboot autonomously instead of USB-dark, which is what makes
the whole evidence loop work. This is a persistent ABL env var stored in the
**devinfo partition** (not misc, not a cookie register), checked at every
boot start:

```sh
fastboot oem force-fastboot on      # expect: OKAY
fastboot getvar force-fastboot      # expect: force-fastboot on
```

Never disarm it inside an attempt loop (disarming is a session-end decision).
Then, with the intended phone selected and no other fastboot process active:

```sh
fastboot getvar product                 # expect: blazer
fastboot boot "$OUT/laguna-usb-next-boot-avb.img"
```

This is the only device-boot command in the runbook; there is no `flash`,
`erase`, `set-active`, `format`, GPT, or userdata command.

### Expected stamp trails (TSHIMST2)

Every stage writes a **TSHIMST2 stamp** to `pmsg+0x30000` (PA `0x95430000`,
`kernel.log` file offset `0x230000` with dump-base `0x95200000`; 128×24-byte
slots since v3.8): a 64-byte header (magic, per-build nonce, count, seq,
last_stage, flags, cntfrq, t0, tlast, dropped, reason) plus
`{seq, stage, flags, timestamp, arg}` slots. Decode from the retrieved
aperture:

```sh
python3 research/laguna-usb-console/tools/decode-shim-stamps.py \
  research/laguna-usb-fix/attempts/<ts>-<name>/late-kernel.log
```

(`E`/`D` = ENTER/DONE. Verified shapes from the v39–v51 device runs:)

**Warm-world bring-up (deterministic since v39 — every `fastboot boot` run
looks like this):**
`entry E → cpu-state D 0x80d01998 → wdt-survey E/D 0x3 → fdt-parse E
0x8a000000 → console-init E → console-paint-done D (~4.4-4.9 s) → wdt-pat D
0x2 → usb-warm E → usb-warm D 0x33313130 (GSNPSID-only verdict — valid ID
proves the core is clocked; canaries are diagnostics, never gating) →
usb-preflight E → usb-pwr-status/gsnpsid/gctl/evt-hi/dctl D value stamps →
usb-preflight D → dma-admit E/D → dwc3-halt-wait E/D → dwc3-reset-wait E/D →
ep0-config E → ep0-ready D → usb-enum-wait E 0x8 (8 s wait) → per-request
E/D pairs (`0xc05a00 E → 0x8180 D` = the host's 384-byte GET_LOG reads) →
usb-enum-wait D 0x1`.

**WDT quiesce, then admission (v44+ order):** `wdt-quiesce-late E/D` runs
**before** the enumeration wait — the survey finds three firmware WDTs but
the pat path services two, and an unpatted instance bites ~9 s after the
last pat (v39–v43 all died `usb-enum-wait FAULT 0x0` at pat+9.0 s). Then:
`payload-admit E 0x8a000000 → usb-enum-wait E 0x1 / D 0x1 (1 s handoff
grace) → payload-admit D 0x1 (FDT parsed) → D 0x2 (handoff inputs)`.

**Embedded-envelope branch (any envelope build):** additionally
`payload-admit D 0x3` (envelope found + aligned) and `D 0x4` (validated).
A scan fallback never emits 3/4.

**Window checks:** `payload-admit D 0x5` (window admitted), `D 0x6`
(ramoops clear). Then `payload-admit D <entry_pa> → handoff ENTER <ba_pa> →
el-drop ENTER arg=8 (CurrentEL=EL2) → el-drop DONE arg=0x40000000` — the
last stamps before XNU runs; header reason goes to
`handed-off-to-xnu` (5).

**Admission refusal branch:** the failing check stamps
`payload-admit REFUSED` with the **first 8 chars of the refusal text**
(little-endian ASCII in the arg), repeated once by the terminal recorder,
then a third REFUSED carrying the shim load PA (`0x80000000`). Decode
dictionary (all device-earned):

| stamped text | refusal | earned by |
|---|---|---|
| `no TEGUX` | `no TEGUXNU payload within scan budget` (initrd scan: ABL retained archives push the magic past the 64 MiB budget) | v45 |
| `XNU wind` | `XNU window overlaps FDT reservation` (or outside-FDT-RAM / resident-input siblings) | v46b |
| `invalid ` | `invalid kernel window` (base not 32 MiB-aligned, or bad size) | v47 |
| `insuffic` | `insufficient kernel headroom` (< 92.1 MiB kernel + 64 MiB headroom) | v48 |
| `FDT dyna` | `FDT dynamic pool unresolved` — **obsolete**: unresolved shared-dma-pool nodes are non-fatal since v51 | v49/v50 |

**Cold path (non-fastboot chains power-gate USB — corrected frames, v3.8+):**
the warm verdict is GSNPSID-only; if no valid Synopsys ID, the shim takes the
LPCM mailbox with ABL's transport (mbox[2] `0x05250000`, qid `0x12`, page
`0x05272000`, doorbell → ctl → copy → GO → poll 200 µs × 1001 → **in-place
reply** → doorbell) and the **corrected** request frames from
[`../evidence-channels/ABL-LPCM-REQUEST.md`](../evidence-channels/ABL-LPCM-REQUEST.md)
§4 — cmd **0x02** (put/enable, byte3=0=on, aux=1, seq byte1=0x05) against the
DT clock numbers `{0x08000000, 0x00070502, 1, 0}` (07 usbc_non_sticky),
`0x00080502` (08 usbc_sticky), `0x000e0502` (0x0e u2phy_apb),
`0x00110502` (0x11 usb2_phy_reset), put reset-gate `0x00110502` then
**cmd 0x04** deassert `0x010d0504`. Never send cmd 0x04 with a DT clock
number: cmd 0x02 and cmd 0x04 address **different id spaces** — that mismatch
is exactly the original v37 refusal `cpm-clk REFUSED 0x7f8` (reply `-8`,
"assert the hsio_n_pixel_ck0_clk reset"). Success predicate per request:
**reply dword1 == 0**; expected trail `mba-live D → cpm-geo/geo2 D 0x4d4241 →
mba-send E → [REQ0/REQ1 echo] → [REPLY0/REPLY1] → cpm-clk D id<<8|reply`
(×4 clocks, ×6 resets) → `phy-resume D` → preflight.

**Success branch (v51 — XNU runs):** no park at admission. The EP0 log
freezes at the jump (nothing services EP0 once XNU owns the machine), XNU
boots and writes its own channels, and the panic/XNU reset parks the phone
~100 s later. **On a post-XNU dump the stamp page may decode as "no
TSHIMST magic" — that is the success signature** (the running XNU/park
session consumed the pmsg zone), not channel loss. The XNU-side story is in
the v2 mirror + persistent_ram console (§8); expect a reader verdict like
`PANICKED | XNU entered=yes | panicked=yes (…)`.

### Hang-proofing contract (what a park means)

Every poll wait is bounded twice (cntvct deadline AND an iteration budget,
so a dead arch timer cannot hang), the firmware watchdogs are armed and
patted during bring-up and quiesced only once the bounded waits are behind
the shim, a global 90 s bring-up deadline is checked at the stage
boundaries, and the channel arms `reason=IN-PROGRESS` at entry so even a
WDT wedge decodes with the last stage named. Any bring-up failure, timeout,
or admission refusal is TERMINAL WITH EVIDENCE: failing stage painted on
the panel, stamp trail recorded, 40 s photograph window, PSCI reset — and
with force-fastboot armed the phone lands back in fastboot. **A diagnostic
image that reaches fastboot again is the SUCCESS path for evidence
recovery, not a brick indicator.**

## 4b. Full evidence loop after the boot ends

1. **Photograph the panel first** if it shows the shim's red stage panel
   (`HANG <stage>` / `WHY` / `CODE` lines) — it disappears on any reset.
   After XNU entry ABL no longer owns the display, so a post-XNU park shows
   ABL's own output, not the shim's.
2. **The park may come late** (shim terminal reset, or XNU's panic reset
   ~100 s after entry). When fastboot is responsive again, pull the 4 MiB
   `kernel.log` **before any power-cycle**:
   `python3 research/laguna-ramdump/tools/capture-fastboot.py <dir>` or the
   raw proven path (`fastboot oem ramdump stage_file kernel.log` →
   `fastboot get_staged …`; also stage `abl.log`).
3. **Decode stamps FIRST** for a shim-death dump (timestamps show where
   time went; value stamps show what the hardware returned; the refusal
   dictionary above names the check). For a post-XNU dump go straight to
   the reader — the mirror and console carry the verdict (§8).
4. One command does arm → boot → bounded watch → park → collect → decode →
   one-line verdict: [`../evidence-lab/run-attempt.sh`](../evidence-lab/run-attempt.sh),
   ledger in [`../evidence-lab/ATTEMPTS.md`](../evidence-lab/ATTEMPTS.md)
   (v39–v51 prose section documents the EP0-determinism + admission
   campaign). For a late park, pull the dump into the attempt dir as
   `late-kernel.log` and decode manually.

## 5. Reboot (one EP0 client only)

Stop `usb_log.py` first (Ctrl+C), then, **while the shim is still polling**:

```sh
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_reboot.py
```

Exact request:

```
bmRequestType=0x40 bRequest=0x5c wValue=0x5447 wIndex=1 wLength=0
```

The gadget queues no data stage, completes the control status stage, and
only then calls the existing PSCI reset path with a barrier. **After XNU
entry there is no implied EP0 service** — a running XNU cannot accept this
request; use the parent's normal recovery and the mirror. The same
one-client rule applies to hotfix, pull, stamp, and framebuffer capture.

## 6. Optional indexed RAM stamp

With other EP0 clients stopped and the shim polling:

```sh
/tmp/tegu-venv/bin/python research/laguna-usb-console/host/usb_stamp.py \
  0 0x4c474c5354414d50
```

This sends `0x40/0x5e`, `wValue=slot`, `wIndex=0x4c47`, and exactly eight
little-endian data bytes. Slot must be 0..7. Only the configured
`tgl_failure_record` word is written. ACK means write+barrier completed, not
persistence.

## 7. Optional framebuffer capture

With other EP0 clients stopped:

```sh
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/framebuffer_capture.py \
  --scale 4 research/laguna-usb-console/out/panel-scale4.png
```

The request is `0xc0/0x5d` with the same offset32 encoding. TFB1 is a
32-byte little-endian header followed by top-down native BGRA rows. Scale 4
produces a 320×714 view from 1280×2856. Note: on the `fastboot boot` handoff
ABL powers off the MIPI CDPHY ~41 ms before the kernel jump, so shim paint
lands in DRAM but is not panel-visible — photograph the panel only for
shim-painted *failure* stages after a park, when ABL has re-owned the
display. See
[`../evidence-channels/DISPLAY-PRODUCER.md`](../evidence-channels/DISPLAY-PRODUCER.md).

## 8. Retrieve the early-XNU mirror after return (v2 layout)

Use a responsive ABL fastboot session and a new evidence directory. The
collector proves the channel with 1 KiB `RAMDUMP_MSG.txt` before requesting
the 4 MiB `kernel.log`:

```sh
python3 research/laguna-ramdump/tools/capture-fastboot.py \
  research/laguna-usb-console/out/evidence-next-boot
```

Then decode the raw aperture, not Android's filtered pstore text file:

```sh
python3 research/laguna-ramdump/reader/read-ramdump.py \
  research/laguna-usb-console/out/evidence-next-boot/kernel.log \
  --dump-base 0x95200000 --logs --tail 65536
```

Symbolication on the v51-era kernel: pass
`--mach research/laguna-usb-fix/mach.blazer-r4r5-tcm` (68,906 symbols).

**v2 layout (XNU-MSGBUF v2):** header at `pmsg+0x3F800` = raw file offset
`0x23F800`; ring data begins exactly at file offset `0x240000` (PA
`0x95440000`), 1 MiB. The reader is v2-first with a v1 fallback and also
decodes the compact breadcrumbs (pmsg+0x28000, stage codes) and the
boot/diag counters (+0x28040/+0x28080). This is the channel that proved the
v51 boot: mirror seq 6, 7,642 B reconstructed, breadcrumbs
`XNU_ENTRY(6) → XNU_INIT(7) → PANIC(12)`. A valid header alone is not
execution evidence; ring text or independent breadcrumb evidence is
required.

**Freshness hazard:** stale windows lie. On a shim-death dump the reader can
print `VERDICT: XNU-ENTERED` from stale kexec-probe residue. Interpret as:
trust **nonce-matched TSHIMST2** for "how far did the shim get" (remember:
on a *successful* run the stamp page is consumed — §4); trust
**`XNU-MSGBUF v2` magic + monotonic breadcrumbs** for "did XNU actually
run"; ignore `probe:`/`KEXEC-XNU:` raw lines and any verdict that disagrees
with those two. Full channel map:
[`../evidence-lab/EVIDENCE-MAP.md`](../evidence-lab/EVIDENCE-MAP.md).
