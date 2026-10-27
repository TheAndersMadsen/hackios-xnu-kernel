# Next-boot runbook: USB console + R4 msgbuf mirror

**All commands below are operator instructions, not actions performed by this
lane.** No device write was performed while preparing them. The boot must be a
parent-approved, temporary `fastboot boot` with the existing unlocked blazer
and a verified Android-A recovery path. Do not flash either image or change
slots/partitions.

## 1. Required inputs

- A final **R4** Mach-O containing `XNU-MSGBUF v1`. An older 0128 binary without
  that producer is not accepted. `tools/build-next-boot.sh` enforces this.
- Pinned 24 MiB root: `research/laguna-deviceroot/work/bootstrap-root-stripped.hfs`
  (SHA-256 `b17e7fd96d56ecf64e66408b36a838d1d1b43287da2f79c4ddddcd50910be946`).
- T2's AVB test key at `/tmp/test-rsa4096.pem` and its salt/rollback profile.
- PyUSB at `/tmp/tegu-venv` (verified version 1.3.1).
- ABL already able to use the USB controller in peripheral fastboot mode. This
  supplies the inherited DMA/PHY evidence required by the shim.
- Parent/device-owner approval and the central device lock. Preserve the current
  T2 vendor_boot/vendor_kernel_boot state; this candidate carries its own
  payload in boot's ramdisk and needs no partition write.

The R4 artifact and final hash are an explicit outstanding input from that lane.
This lane does not claim it exists merely because the producer source is present.

## 2. Build and verify the candidate (host only)

```sh
cd /Users/andersmadsen/Developer/personal/hackios
export R4_MACHO=/absolute/path/to/the/final-r4.macho
export OUT=$PWD/research/laguna-usb-console/out/next-boot
research/laguna-usb-console/tools/build-next-boot.sh
cat "$OUT/SHA256SUMS"
```

The helper:

1. rejects a missing/mirror-less R4 Mach-O and validates the exact root hash;
2. emits the 256 MiB-at-`0x80000000` ADT and TEGUXNU payload;
3. uses the T2 cmdline plus explicit `lagunalogdump=1`;
4. builds the USB-enabled v3.3 shim with polling, the 1 MiB log, TSHIMST2
   early stamps (`pmsg+0x30000`, 64-slot timestamped ring), bounded waits,
   CPM-before-DWC3 order, and the armed-WDT posture;
5. signs a 64 MiB `laguna-usb-next-boot-avb.img` with T2's
   SHA256_RSA4096/rollback/salt/key profile and runs `verify_image`.

`out/shim-boot-laguna.img` built without an R4 payload is only a diagnostic
artifact and must not be substituted for this candidate.

## 3. Start the log reader before the temporary boot

```sh
cd /Users/andersmadsen/Developer/personal/hackios
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py \
  --timeout 120 | tee research/laguna-usb-console/out/next-boot-live.log
```

Use shell redirection, not `usb_log.py --output`, because this nested checkout's
path allowlist points into the Tegu repository. Leave this single EP0 client
running. It first waits for VID:PID `1209:0001` and product
`Tegu USB diagnostic`, then replays the log from offset zero in ≤512-byte reads
(the implementation may return shorter chunks).

The protocol's exact read is:

```
bmRequestType=0xc0 bRequest=0x5a
wValue=byte_offset & 0xffff
wIndex=byte_offset >> 16
wLength<=512
```

Offsets beyond 64 KiB therefore continue through wIndex. The log is append-only
so the stock reader never has to resynchronize after a ring overwrite.

## 4. Temporary boot and expected evidence

**REQUIRED before every `fastboot boot` attempt (device-proven on blazer,
2026-10-27): arm force-fastboot so any shim failure reset lands in fastboot
autonomously instead of dark.** With the intended phone selected:

```sh
fastboot oem force-fastboot on      # expect: OKAY
fastboot getvar force-fastboot      # expect: force-fastboot on
```

This made the full evidence loop work: boot → shim failure → auto-reset →
parked in fastboot → `oem ramdump stage_file kernel.log`. Do not boot without
it.

With the intended phone selected and no other fastboot process active:

```sh
fastboot getvar product                 # expect: blazer
fastboot boot "$OUT/laguna-usb-next-boot-avb.img"
```

This is the only device-boot command in the runbook; there is no `flash`,
`erase`, `set-active`, `format`, GPT, or userdata command.

Expected stages are ABL handoff → CPM queue init ACK → USB2 warm resume →
`1209:0001` enumeration → mirrored findings/payload lines → XNU entry. The
shim polls for the first 8 seconds, between payload segments, during the final
handoff check, and in its 40-second reset/photograph window.

Every stage above also writes a **TSHIMST2 stamp** to `pmsg+0x30000`
(PA `0x95430000`): a 64-byte header (magic, per-build nonce, count, seq,
reason, cntfrq, t0) plus up to 64 x 24-byte
`{seq, stage_id, flags, timestamp, arg}` slots. Each risky wait stamps its
stage id ON ENTRY (so a hang names the wait), and the v3.3 preflight stamps
the VALUE of every MMIO read plus a distinct stage id per refusal branch
(bad-id / wrong-prtcapdir / stale-event-addr / not-D0). Decode the trail from
the retrieved aperture with:

```sh
python3 research/laguna-usb-console/tools/decode-shim-stamps.py \
  research/laguna-usb-console/out/evidence-next-boot/kernel.log
```

If the controller does not enumerate, do not assume a root/XNU failure. The
possible earlier refusals are CPM transport, USB2 power/clock (now resumed
BEFORE any DWC3/PHY read — the v3.2 order that read DWC3 un-clocked was
device-proven wrong), DMA reservation/ownership, or MMIO access. Do not
bypass them with a static DMA address or Tegu PHY writes.

**v3.2/v3.3 hang-proofing contract:** every poll wait is bounded twice
(cntvct deadline AND an iteration budget, so a dead arch timer cannot hang),
the firmware watchdogs stay armed and patted during bring-up — including
while the console paints (quiesced only just before the XNU handoff) — and
any USB-console bring-up failure or wait timeout is TERMINAL WITH EVIDENCE:
the failing stage is painted on the panel, the stamp trail records it, and
after a 40-second photograph window the shim issues a PSCI reset. With
force-fastboot armed the phone lands back in fastboot. A diagnostic image
that reaches fastboot again is the SUCCESS path for evidence recovery, not a
brick indicator.

## 4b. Full evidence loop when the shim fails

1. **The screen may hold the shim's last painted stage** (red panel,
   `HANG <stage>` / `WHY` / `CODE` lines). Photograph it before any
   power-cycle — it disappears on reset.
2. **After the failure reset the phone parks in fastboot** (force-fastboot
   armed). Retrieve the ramdump immediately — the TSHIMST trail lives at
   `pmsg+0x30000` (`kernel.log` file offset `0x230000` with dump-base
   `0x95200000`), far above the TEE/ABL clobber window (`+0x000..0x900`), and
   survives the reset bounce, ABL slot-ladder re-entries, XNU handoff and the
   retrieval itself. Do not power-cycle before
   `research/laguna-ramdump/tools/capture-fastboot.py` (or
   `fastboot oem ramdump stage_file kernel.log` on the proven path) has
   pulled `kernel.log`.
3. Decode stamps FIRST (`tools/decode-shim-stamps.py`): the per-slot
   timestamps show exactly where time went (the 2026-10-27 run spent
   26.4 s entry→usb-preflight, almost all of it Device-memory console
   painting — now region-cleared, 64-bit stored, and WDT-ticked), the value
   stamps show what the hardware returned, and `dropped` warns if any trail
   was truncated (a v1-image artifact; the v2 ring holds 64).
4. Then the R4 mirror (`+0x40000`) as in section 8. The stamp trail + painted
   stage + build nonce identify exactly which image and wait died.



## 5. Reboot (one EP0 client only)

Stop `usb_log.py` first (Ctrl+C), then, **while the shim is still polling**:

```sh
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_reboot.py
```

Exact request:

```
bmRequestType=0x40 bRequest=0x5c wValue=0x5447 wIndex=1 wLength=0
```

The gadget queues no data stage, completes the control status stage, and only
then calls the existing PSCI reset path with a barrier. The stock tool's text
about syncing disks and forced fastboot is Tegu-XNU-specific: this shim performs
no storage write and sets no persistent fastboot flag. A timeout is not retried
automatically.

**After XNU entry there is no implied EP0 service.** Without a separate XNU
adapter, a hanging XNU cannot accept this reboot request. Use the parent's
normal recovery and the R4 mirror instead of repeatedly sending requests.

The same one-client rule applies to hotfix, pull, stamp, and framebuffer
capture: stop the logger first. The minimal shim intentionally stalls hotfix
and pull because it has no filesystem/worker support.

## 6. Optional indexed RAM stamp

With other EP0 clients stopped and the shim polling:

```sh
/tmp/tegu-venv/bin/python research/laguna-usb-console/host/usb_stamp.py \
  0 0x4c474c5354414d50
```

This sends `0x40/0x5e`, `wValue=slot`, `wIndex=0x4c47`, and exactly eight
little-endian data bytes. Slot must be 0..7. Only the configured
`tgl_failure_record` word is written; no arbitrary address or persistent
storage is touched. ACK means write+barrier completed, not persistence.

## 7. Optional framebuffer capture

With other EP0 clients stopped:

```sh
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/framebuffer_capture.py \
  --scale 4 research/laguna-usb-console/out/panel-scale4.png
```

The request is `0xc0/0x5d` with the same offset32 encoding. TFB1 is a 32-byte
little-endian header followed by top-down native BGRA rows. Scale 4 produces a
320×714 view from 1280×2856 and greatly reduces EP0 traffic. This reads the
console's memory-backed framebuffer; it does not re-enable DC9x00 readback or
prove that pixels reached the physical panel.

## 8. Retrieve R4's early-XNU mirror after return

Use a responsive ABL fastboot session and a new evidence directory. The
collector proves the channel with 1 KiB `RAMDUMP_MSG.txt` before requesting the
4 MiB `kernel.log`; it does not start with a bare listing or raw bank:

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

R4's header is at `0x95440000` (raw file offset `0x240000`) and its ring begins
at `0x95440040` (`0x240040`), each 64/1,048,576 bytes. The reader validates
`XNU-MSGBUF v1`, bounds and total/tail geometry, then reconstructs the oldest
bytes first. A valid header alone is not execution evidence; ring text or
independent LGLW/PAN1 evidence is required.

This mirror is how early XNU errors are recovered even though the original
msgbuf is at `0x88000000`, outside the retrieved 4 MiB aperture. It is separate
from the live teguload USB log, and both belong in the next-boot evidence set.
