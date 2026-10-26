# Hardware enable: Laguna EP0 console + R4 msgbuf mirror

Status: integrated and host-validated, **not device-verified**. No phone, ADB,
Fastboot, QEMU, flash, slot, firmware, partition, userdata, or MMIO action was
performed in this lane.

## 1. EP0 is DMA on DWC3 — the premise correction

EP0 vendor control does not eliminate DMA admission. The controller uses DMA
for all of the following:

- the 8-byte SETUP packet and its `CONTROL_SETUP` TRB;
- control IN/OUT data stages and their TRBs;
- the global event buffer and its completion events.

The generic driver evidence is explicit: `dwc3_ep0_out_start()` prepares an
8-byte setup TRB and starts it; `gadget.c` fills TRB buffer-pointer fields from
DMA addresses. Our `ud_start()` does the same for EP0 OUT/IN. The log, stamp,
and framebuffer contents are copied by the CPU, but the event, setup, TRB and
data buffers still cross the DWC3 DMA engine.

Therefore **`memory-swap-enabled` and SMMU must not be waived as “EP0 is PIO.”**
What is unnecessary is a *new* SMMU mapping or a full-system DMA allocator. The
minimal console reuses the exact ABL-proven event allocation and leaves the
existing translation/memory-swap behavior unchanged. No bulk endpoint or DMA
into iOS memory exists.

## 2. Minimal safe DMA admission

The integration accepts only the inherited `GEVNTADR` allocation, not an
arbitrary static pool. It requires all of:

1. a recognized DWC3 ID and `GCTL.PRTCAPDIR = device` from the just-used
   fastboot peripheral path;
2. nonzero, 4 KiB-aligned `GEVNTADR`, with `GEVNTSIZ >= 0xc0` and a multiple of
   four bytes;
3. the entire original allocation contained in one of these exact live-FDT
   reservations, also present in the current parsed tree:
   - `abl@be000000` (16 MiB),
   - `xhci_dma@9010000` (256 KiB),
   - `xhci_dma_buffer@9050000` (48 KiB),
   - `xhci_dma_dram@97000000` (4 MiB);
4. no overlap with the shim, FDT, initrd, console framebuffer, XNU window, or
   ramoops aperture;
5. successful fault-gated CPU reads at the first and last words of the original
   allocation;
6. `RUN_STOP` stopped and `DSTS.DEVCTRLHLT` observed **before any byte of the
   inherited event allocation is cleared**.

Usage is capped at the original size and at 16 KiB. Layout is a 64-byte event
ring, two 16-byte TRBs, an 8-byte SETUP buffer, then a data buffer at `0x80`.
The data capacity is `min(512, dma_bytes - 0x80)`. Thus even a 512-byte ABL
allocation is usable with 384-byte reads; the host tools accept these short
reads and advance by the returned count. The smallest accepted arena is
`0xc0` (192 bytes), providing a 64-byte data stage.

This is an ABL-page admission, not proof that all RAM is DMA-capable. It also
avoids changing `memory-swap-enabled`: the existing fastboot transfer behavior
and standard little-endian TRBs are retained. A different SMMU state or a new
allocation requires a separate review and is refused.

## 3. Fault-gated hardware sequence

All new USB/CPM reads **and writes** go through `probe_read32` /
`probe_write32`. A recovered synchronous fault aborts before further writes.
As the DER Event 20 lesson says, an async SError is not recoverable by this
mechanism, so fastboot-proven power/topology is a prerequisite, not an optional
check. No DC9x00 readback is introduced.

Order:

1. Preserve WDT quiesce, VBAR-first entry and runtime MPIDR.
2. Read DWC3 ID/GCTL, inherited event address/size, USB2 CFG19/CFG21 and the
   wrapper's USB2 PMU state. Any fault, non-device role, inaccessible PHY, or
   non-D0 USB2 state refuses before a CPM write. USB3/DP state is irrelevant to
   this high-speed EP0 profile and is left alone.
3. Perform the ABL/FDT DMA admission above and read both ends of the buffer.
4. Validate the two CPM clients at `0x05260000` and `0x05261000`: direct
   capacity registers report 4..256 words, multiples of four, no outstanding
   TX and no stale RX message. No global-region fallback is used.
5. Clear a stale ACK, then perform the queue-mode `0x100` init frame and wait
   for its ACK with a 3 s bound. Reset cursors and sequence to 1.
6. Request the four clocks in order `07,08,0e,11` using LPCM destination `0x8`,
   command `0x2`, `lpcm_id=5`, `PREPARE=0`, `CLK_GATE=1`. These clock nodes do
   **not** have `use-smc`, so the SMC clock shortcut is not used.
7. Request resets in order `0a,0b,0c,09,06,0d` using command `0x4`,
   `DEASSERT=1`. Each request is a four-byte packed service payload:
   `req_id | lpcm_id<<8 | reset_id<<16 | 1<<24`.
8. Preserve the existing Type-C/TUSB2E11 peripheral configuration. No I2C,
   repeater, IDDQ, PLL, CFG19, NVMEM, SRAM or USB3/DP writes occur. If needed,
   set only `USB2PHY_CFG21.PHY_ENABLE` (bit12 at `0x0c45001c`).
9. Halt the controller, clear the admitted buffer, clear only USB2 SUSPHY,
   issue the bounded DWC3 soft reset, install the small event ring and EP0,
   then set RUN_STOP.

Each CPM request has one sequence and token zero. RX frames are copied before
ACK, and accepted only when valid response type, destination/source swap,
sequence and token match. Wrong responses, timeouts, full queues and firmware
errors stop the operation; there is no ambiguous-write retry.

## 4. Integrated v3 changes

- `laguna_usb.c/.h`: dynamic ABL arena, fault callbacks and post-halt clearing.
- `laguna_usb_dma.c/.h`: pure reservation/overlap admission.
- `tgl_cpm.c/.h`: serial CPM queue client and USB2 board operations.
- `tgl_usb_console.c/.h`: preflight, DMA/CPM ordering, handlers and polling.
- `console.c`: exact on-panel `con_str`/`con_hex`/`con_hex2` text mirrored into
  the 1 MiB EP0 log; panel output is unchanged.
- `shim.c`: versioned USB start after `DTCOMPAT`, an 8-second bounded initial
  poll, polling in the 40-second reset window, and a refusal to hand off during
  an active control transfer. `TGL_USB_DISABLE` is a build escape hatch.
- `loader.c`: polling between payload segments and a final active-transfer
  check before XNU entry.
- `start.S`: only the affected BSS/stack references now use `adrp+add`, so the
  1 MiB log no longer violates `adr`'s ±1 MiB range. No absolute pointers were
  introduced.
- `build.sh`: all new objects are linked and checked for ABS64 relocations.

Current integrated artifacts (empty diagnostic ramdisk, **not** the next-boot
R4 payload):

- `Image`: 1,277,952 bytes, SHA-256
  `7102e186457fd0082fa074959a269700a12e62e16bc8cf2a4dcac5ead77e8ec4`.
- `shim-boot-laguna.img`: 1,290,240 bytes, current hash in
  `out/integrated-image-sha256.txt`. Empty-ramdisk gzip metadata can change
  this hash between builds; it is not an R4 candidate.

## 5. Validation

`run-host-tests.sh` passes all of:

- 198 protocol/descriptor/request/fault checks;
- 25 DMA admission checks;
- 63 CPM queue/encoding/response/failure checks;
- 20 full-stack v3 startup/log/stamp/reboot checks;
- those same 306 checks under ASan/UBSan;
- 6 unmodified Tegu-tool integration tests against the C mock;
- 16 applicable original Tegu baseline tests;
- 3 live-FDT tests and 3 next-boot gate tests;
- actual v3 build and host render, plus a freestanding ARM64 audit with zero
  ABS64/JUMP_SLOT relocations and zero undefined symbols.

The full-stack test exercises ABL admission, CPM init and all ten clock/reset
requests, EP0 log read, stamp commit, and post-ACK reboot. The mock is not
hardware evidence.

## 6. Telemetry boundary with R4

The USB log is live during teguload and payload copying. There is no claim of
an uninterrupted shim→XNU USB session: XNU owns its mappings and would need a
separate polling adapter before it could service EP0.

For XNU proper, use R4's **independent** `XNU-MSGBUF v1` mirror. It starts in
early XNU, writes every accepted byte to fixed PA `0x95440040` with a 64-byte
header at `0x95440000`, and reconstructs up to 1 MiB even when the original
msgbuf is in the kexec window at `0x88000000` and absent from the 4 MiB ramoops
retrieval. `lagunalogdump=1` is explicitly included in the next-boot cmdline.
R2's reader can decode that raw `kernel.log` region after return.

`tools/build-next-boot.sh` refuses an R4 Mach-O that lacks the mirror marker or
the exact 24 MiB root. This prevents accidentally shipping an older 0128
payload without R4. A final R4 artifact and its hash must still be supplied by
that lane; this lane does not fabricate it.

See `RUNBOOK.md` for exact build/host/reboot commands and one-client rules.
