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
4. builds the USB-enabled v3 shim with polling and the 1 MiB log;
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

If the controller does not enumerate, do not assume a root/XNU failure. The
possible earlier refusals are DMA reservation/ownership, CPM transport,
USB2 power/PHY, or MMIO access. Do not bypass them with a static DMA address or
Tegu PHY writes. A failure before EP0 starts cannot be read over EP0; retain
the build hash and use the independent ramdump/R4 path rather than forcing a
second unsafe initialization.

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
