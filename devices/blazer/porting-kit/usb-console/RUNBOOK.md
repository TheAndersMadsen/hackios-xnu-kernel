# Next-boot runbook: USB console shim, v3.7 flow (2026-10-27)

**All commands below are operator instructions, not actions performed by this
lane.** No device write was performed while preparing them. The boot must be a
parent-approved, temporary `fastboot boot` with the existing unlocked blazer
and a verified Android-A recovery path. Do not flash either image or change
slots/partitions.

This revision folds the shim v3.7 generation: the **force-fastboot REQUIRED
step**, the **warm-skip + LPCM cold path** bring-up, the **TSHIMST2 stamp
trail** with expected shapes, and the **EL2→EL1 drop** at the final jump. The
protocol truth behind it (ABL teardown, MBA/LPCM wire, runtime facts, display
handoff) lives in [`../evidence-channels/`](../evidence-channels/); the
attempt ledger and decode tooling live in [`../evidence-lab/`](../evidence-lab/).

## 1. Required inputs

- A **mirror-bearing Mach-O**: `XNU-MSGBUF v2` (header at pmsg+0x3F800) or
  `v1`. An older binary without a mirror producer is not accepted;
  `build-next-boot.sh` enforces this.
- Pinned 24 MiB root of record: the xnu.ramdisk bytes **embedded in the
  proven v6 vendor_boot** — 25,165,824 B, SHA-256
  `aebc9a8a410369b19c60c3e845cb814896bd9709264d2c085283cf816439b4bf`
  (the earlier `b17e7fd9…` pin referenced a regenerated work-dir file and no
  longer matches anything that ran on device).
- T2's AVB test key at `/tmp/test-rsa4096.pem` and its salt/rollback profile.
- PyUSB at `/tmp/tegu-venv` (verified version 1.3.1).
- ABL already able to use the USB controller in peripheral fastboot mode. On
  the `fastboot boot` handoff ABL leaves DWC3 **clocked and in device mode**
  (its only handoff USB action is clearing `DCTL.RUN_STOP`) — see
  [`../evidence-channels/ABL-USB-STATE.md`](../evidence-channels/ABL-USB-STATE.md).
- Parent/device-owner approval and the central device lock. Preserve the
  current vendor_boot/vendor_kernel_boot state; this candidate carries its
  own payload in boot's ramdisk and needs no partition write.

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

1. rejects a mirror-less Mach-O (accepts `XNU-MSGBUF v1` **or** `v2`) and
   validates the exact root hash;
2. emits the 256 MiB-at-`0x80000000` ADT and TEGUXNU payload with the T2
   cmdline plus explicit `lagunalogdump=1`;
3. builds the shim, which now **re-runs `shim/build.sh` and generates a fresh
   per-build TSHIMST2 nonce** — the frozen `next-boot-*` image is the only
   nonce ledger (see the evidence-lab map §4.1);
4. signs a 64 MiB `laguna-usb-next-boot-avb.img` with T2's
   SHA256_RSA4096/rollback/salt/key profile and runs `verify_image`.

What the v3.7 shim does at boot (flow, in order):

```
warm-probe → [warm: preflight] / [cold: LPCM mailbox → board resume → preflight]
         → DMA admit → EP0 gadget → payload → EL2→EL1 drop → jump
```

- **Warm-skip:** canary `0xc450000` + `GSNPSID` are probed **before any CPM
  work**. A plausible Synopsys ID stamps `usb-warm YES arg=gsnpsid` and skips
  the entire CPM phase straight to preflight→DMA→EP0 — this is correct after
  a `fastboot boot` handoff, which inherits a clocked controller.
- **Cold path (non-fastboot chains power-gate USB):** the shim talks to the
  **LPCM mailbox** with ABL's exact instruction-verified transport — mbox[2]
  base `0x05250000`, QID `0x12`, channel page `0x05272000` (`GO` +0x00, ctl
  +0x04 bit24 SET, doorbell/completion +0x08 write-1-to-clear, message window
  +0x100), ABL's send order (doorbell → ctl → copy → GO → poll 200 µs × 1001
  hard bound → **in-place reply** → doorbell), clock-gate message
  `{0x08000000, 0x04|seq<<8|gate_id<<16|(1^en)<<24, 0, 0}`, reset deassert in
  the release/put shape. Success = **reply dword1 == 0** (the GDMC
  `0x1a400000`+bit23 check is mbox[0]-only and was removed from this path).
  Full wire truth:
  [`../evidence-channels/ABL-MBA-PROTOCOL.md`](../evidence-channels/ABL-MBA-PROTOCOL.md).
- **EL2→EL1 drop:** ABL hands off at EL2 (cpu-state stamp `0x80d01998` proved
  it). The shim drops register-for-register at the FINAL jump
  (`tegu_el_drop_and_jump`, gated on CurrentEL==EL2); `el-drop ENTER(arg=8)`
  → `el-drop DONE(arg=0x40000000)` are the last stamps before the jump, and
  the entry CurrentEL is recorded in `boot_regs[4]`.

`out/shim-boot-laguna.img` built without a payload is only a diagnostic
artifact and must not be substituted for this candidate.

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

## 4. Force-fastboot: REQUIRED before every boot attempt

**Device-proven on blazer, 2026-10-27: arm force-fastboot before every
`fastboot boot`.** Any shim failure reset then lands in fastboot autonomously
instead of USB-dark, which is what makes the whole evidence loop work
(boot → shim failure → auto-reset → parked → ramdump pull). This is a
persistent ABL env var stored in the **devinfo partition** (not misc, not a
cookie register), checked at every boot start before the power-key window;
ABL also accepts a signed debug-policy DT prop `abl/force_fastboot`, but the
env var is the operator lever:

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

Every stage above writes a **TSHIMST2 stamp** to `pmsg+0x30000`
(PA `0x95430000`, `kernel.log` file offset `0x230000` with dump-base
`0x95200000`): a 64-byte header (magic, per-build nonce, count, seq,
last_stage, flags, cntfrq, t0, tlast, dropped, reason) plus up to 64×24-byte
`{seq, stage, flags, timestamp, arg}` slots. Each risky wait stamps ON ENTRY
(so a hang names the wait), value stamps carry what the hardware returned,
and the terminal record carries a reason. Decode from the retrieved aperture:

```sh
python3 research/laguna-usb-console/tools/decode-shim-stamps.py \
  research/laguna-usb-console/out/evidence-next-boot/kernel.log
```

(Last known trail shapes, from the 2026-10-27 device runs and the v3.7
host gates; `E`/`D` = ENTER/DONE flags:)

**Cold-path ACK outcome (the green trail):**
`entry → cpu-state → wdt-survey E/D → fdt-parse → console-init →
console-paint-done → wdt-pat → usb-warm E (+ verdict) → cpm-first →
cpm-geo E/D → cpm-geo2 D 0x4d4241 → mba-live D → phy-resume E →
usb-pwr E/D → per clock: mba-send E → [marker+RESP0] → cpm-clk D id<<8|reply`
(×4 clocks, ×6 resets) `→ phy-resume D → preflight value stamps →
preflight DONE → dma-admit D → ep0-ready D → usb-enum-wait → payload E/D →
handoff ENTER → el-drop ENTER(arg=8) → el-drop DONE(arg=0x40000000) →
handoff DONE(reason=5)`. Then XNU either consoles or panics — both are
answers.

**Cold-path failure outcome:** per request
`mba-ack TIMEOUT arg=<last completion word>` (or `mba-resp0 FAULT` if the
completion register itself faults) → `cpm-clk`/`cpm-rst FAULT` → header
reason `cpm-never-came-up` → stage painted on the panel → 40 s photograph
window → PSCI reset → force-fastboot park. A **NACK** (reply dword1 ≠ 0)
stamps `cpm-clk/rst REFUSED id<<8|reply` with the firmware's status byte.

**Warm outcome:** `usb-warm YES arg=gsnpsid` (no CPM stamps at all — the
whole CPM phase is skipped) → preflight → DMA → EP0. The 2026-10-27 08:52
run reached **EP0-live** through this path.

**Current frontier (v3.7 device run, 08:58):** the LPCM transport itself
works — `cpm-geo2 DONE 0x4d4241` (the MBA magic ABL's check predicts) and a
completed `mba-ack DONE arg=0x8400000fffffff8` — but the first clock-gate
request came back `cpm-clk REFUSED 0x7f8` (gate id 0x07, **firmware reply
`0xf8`**). The reply codes are being decoded; the very next stamps after the
refusal are the diagnostic (a second `usb2_cfg_csr` canary sample decides
"domain differs" vs "payload wrong"). Do not treat a REFUSED trail as a
protocol failure — the mailbox answered.

### Hang-proofing contract (what a park means)

Every poll wait is bounded twice (cntvct deadline AND an iteration budget,
so a dead arch timer cannot hang), the firmware watchdogs stay armed and
patted during bring-up (quiesced only just before the XNU handoff), a global
90 s bring-up deadline is checked at five stage boundaries, and the channel
arms `reason=IN-PROGRESS` at entry so even a WDT wedge decodes with the last
stage named. Any bring-up failure or timeout is TERMINAL WITH EVIDENCE:
failing stage painted on the panel, stamp trail recorded, 40 s photograph
window, PSCI reset — and with force-fastboot armed the phone lands back in
fastboot. **A diagnostic image that reaches fastboot again is the SUCCESS
path for evidence recovery, not a brick indicator.**

## 4b. Full evidence loop when the boot fails

1. **Photograph the panel first** if it shows the red stage panel
   (`HANG <stage>` / `WHY` / `CODE` lines) — it disappears on any reset.
2. **After the failure reset the phone parks in fastboot** (force-fastboot
   armed). Retrieve the ramdump immediately — the TSHIMST2 trail lives at
   `pmsg+0x30000` (`kernel.log` file offset `0x230000`), far above the
   TEE/ABL clobber window (`+0x000..0x900`), and survives the reset bounce,
   ABL re-entries, XNU handoff and the retrieval itself. Do not power-cycle
   before the 4 MiB `kernel.log` is pulled:
   `python3 research/laguna-ramdump/tools/capture-fastboot.py <dir>` or the
   raw proven path (`fastboot oem ramdump stage_file kernel.log` →
   `fastboot get_staged …`; also stage `abl.log`).
3. **Decode stamps FIRST** (timestamps show where time went; value stamps
   show what the hardware returned; `dropped` warns of truncation). Then the
   mirror and `abl.log` per §8. The stamp trail + painted stage + build
   nonce identify exactly which image and wait died.
4. One command does all of this (arm → boot → bounded watch → park →
   collect → decode → verdict): [`../evidence-lab/run-attempt.sh`](../evidence-lab/run-attempt.sh),
   with the ledger in [`../evidence-lab/ATTEMPTS.md`](../evidence-lab/ATTEMPTS.md).

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
entry there is no implied EP0 service.** The same one-client rule applies to
hotfix, pull, stamp, and framebuffer capture.

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

**v2 layout (XNU-MSGBUF v2, patch 0130):** header at `pmsg+0x3F800` =
raw file offset `0x23F800`; ring data begins exactly at file offset
`0x240000` (PA `0x95440000`), 1 MiB. The reader is v2-first with a v1
fallback and also decodes the compact breadcrumbs (pmsg+0x28000) and the
boot/diag counters. A valid header alone is not execution evidence; ring
text or independent breadcrumb/stamp evidence is required.

**Freshness hazard:** stale windows lie. On a shim-death dump the reader can
print `VERDICT: XNU-ENTERED` from stale kexec-probe residue. Interpret as:
trust **nonce-matched TSHIMST2** for "how far did the shim get"; trust
**`XNU-MSGBUF v2` magic + monotonic breadcrumbs** for "did XNU actually
run"; ignore `probe:`/`KEXEC-XNU:` raw lines and any verdict that disagrees
with those two. Full channel map:
[`../evidence-lab/EVIDENCE-MAP.md`](../evidence-lab/EVIDENCE-MAP.md).
