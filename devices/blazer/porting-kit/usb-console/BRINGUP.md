# Laguna USB2/DWC3 warm bring-up for teguload v3

> Revision note: the integrated implementation and its ABL-event-allocation DMA
> contract are now in `hackios-xnu-kernel/blazer/teguload/shim/`. Use
> `HARDWARE-ENABLE.md` and `RUNBOOK.md` for the current next-boot path. Earlier
> statements about DMA being default-off were design-stage only, not a claim
> that EP0 avoids DMA.

Scope: standalone shim and a future polled XNU adapter. Everything in this lane
was designed and tested on the host. No phone, MMIO, QEMU, ADB, Fastboot, flash,
partition, or firmware operation was performed, and no hardware enumeration is
claimed.

## 1. What the live FDT actually says

Input: `hardware/bootchain-20261022/live-fdt.dtb`, SHA-256
`d7c27fdc8657be6e6a378aa0a622dee6508ef24e88c313084ff9de61222bd306`.
`tools/laguna_usb_config.py` validates the following and emits
`out/laguna_usb_config.h` plus `out/live-usb-inventory.json`.

- `/simple_usb_bus/usb3@c450000`: `google,dwc3-lga`, identity `ranges`, three
  wrapper regions at `0x0c450000+0x14`, `0x0c450020+0x08`, and
  `0x0c450028+0x04`.
- Child `dwc3@c400000`: `snps,dwc3`, **`0x0c400000..0x0c40d05f`**, peripheral
  by default, maximum-speed super-speed, GIC SPI `0x244`, and
  `iommus = <&amb_smmu 0x8100>`.
- `/usb_phy@c410000`: `google,usb_phy-lga` with **nine** regions:

| Name | CPU base | Size | Minimal USB2 use |
|---|---:|---:|---|
| `dptx_phy_regs` | `0x0c410000` | `0x10000` | USB3/DP only; untouched |
| `dptx_tca_regs` | `0x0c430000` | `0x1000` | USB3/Type-C assist; not programmed by the minimal path |
| `usb2_cfg_csr` | `0x0c450014` | `0x0c` | CFG19/CFG21 snapshot; set only CFG21 PHY_ENABLE if needed |
| `dp_top_csr` | `0x0c637000` | `0xa0` | snapshot only; inherited VBUS/orientation retained |
| `gp_ss_csr` | `0x0c260000` | `0x38` | not used |
| `phy_sram` | `0x0c420000` | `0x10000` | firmware not loaded or modified |
| `dp_pipe_lane0` | `0x0c480000` | `0x3e8` | not used |
| `dp_pipe_lane1` | `0x0c490000` | `0x3e8` | not used |
| `dwc3_lcsr_deemph` | `0x0c40d060` | `0x10` | tuning retained, not written |

The stock compatible strings differ from the local mainline bindings
(`google,dwc3-lga` versus `google,lga-dwc3`, and `google,usb_phy-lga` versus
`google,lga-usb-phy`). The mainline four-region PHY layout must not be applied
to this nine-region stock node merely by renaming a compatible string.

### Firmware-owned clocks, resets and power

All CPM clock/reset providers have `lpcm-id = 5` (HSION). They are **not MMIO
addresses**. `struct laguna_usb_board_ops` requires source-reviewed callbacks;
a missing callback fails closed.

| Order | Resource | DT node / ID |
|---:|---|---|
| clock 1 | USBC non-sticky | `hsion_usbc_non_sticky_clk`, clock-id `0x07` |
| clock 2 | USBC sticky | `hsion_usbc_sticky_clk`, clock-id `0x08` |
| clock 3 | USB2 PHY APB | `hsion_u2phy_apb_clk`, clock-id `0x0e` |
| clock 4 | USB2 PHY reset clock | `hsion_usb2_phy_reset_clk`, clock-id `0x11` |
| reset 1 | USBC non-sticky | `hsion_resets`, reset-id `0x0a` |
| reset 2 | USBC sticky | reset-id `0x0b` |
| reset 3 | USB DRD bus | reset-id `0x0c` |
| reset 4 | USB2 PHY APB | reset-id `0x09` |
| reset 5 | USB top CSR | reset-id `0x06` |
| reset 6 | USB2 PHY | reset-id `0x0d` |

USB3/DP additionally has reset clock `0x12`, firmware clock `0x03`, and PHY
reset `0x0e`; the minimal high-speed gadget does not call or modify them.
The DWC3 child also names fixed clocks `ref` (19.2 MHz), `bus_early`
(250 MHz), and `suspend` (200 kHz), not additional shim-managed gates.

The power hierarchy is `sswrp_hsio_n_pd` (`sswrp-id 0x17`) →
`hsio_n_usb_pd` (subdomain 0) → `hsio_n_usb_psw_pd` (subdomain 5). DT marks
these on-at-init, which is topology, not proof of their state after a particular
ABL handoff. The board `power_on` callback must establish this hierarchy and
the fixed USB rails using the actual CPM protocol, with response errors and
timeouts checked.

USB2 also traverses `/i2c@3a331000/tiusb2e11@3E`, a `goog-eusb2-repeater`
(TUSB2E11) with three supplies and reset pinctrl. Its power, role and Type-C
orientation must be preserved/confirmed by `repeater_peripheral`; there is no
bare-metal I2C write sequence invented in this lane.

## 2. Tegu eUSB701 is not the Laguna register contract

Tegu's documented successful experiment found its eUSB701 PHY disabled, in
reset and IDDQ, then used the Exynos `phy_exynos_eusb_initiate` sequence while
retaining firmware PLL/tuning. **Those offsets and the IDDQ write must not be
copied to Laguna.**

Laguna has Synopsys eUSB2 plus a USB3.2/DP combo PHY behind the different
`google,usb_phy-lga` binding. Google's G5 driver instead does the following for
USB2: configure CFG21/CFG19, establish VBUS state, enable clocks, deassert
resets, and set `USB2PHY_CFG21_PHY_ENABLE` (bit 12 at `0x0c45001c`). The stock
module's `google_u2phy_init` confirms these operations. No G5 IDDQ register or
Tegu-style reset sequence is documented by those sources.

The full driver rewrites CFG19 PLL feedback to 368 and CFG21 reference/bypass
fields. This shim deliberately implements only a **warm resume**: it logs those
values but does not rewrite PLL feedback, NVMEM calibration, repeater tuning,
Type-C orientation, SRAM firmware, or USB3/DP state. If a cold/reprogrammed PHY
is required, this minimal path is not sufficient.

## 3. Exact shim sequence

`shim/laguna_usb.c` implements this order:

1. **Static admission before an image is built.** Run the FDT validator. Do not
   substitute addresses from Tegu or another Tensor generation.
2. **DMA admission before any controller access.** Require a 4 KiB-aligned,
   physically contiguous 16 KiB allocation and a true `dma_path_admitted`.
   This covers CPU ownership and the controller's DMA path, including SMMU.
   Failure returns `LAGUNA_USB_ERR_DMA` before a DWC3 read or write.
3. **Read-only identity/handoff snapshot.** Require a recognized DWC3 ID and
   `GCTL.PRTCAPDIR = device`; log inherited GCTL, DCTL/DSTS as useful, USB2
   CFG19/CFG21, USBDP PHY_CFG1 and POWER_CFG1. Unknown ID or host-only mode
   refuses the operation.
4. **Warm-path decision.** If CFG21 PHY_ENABLE is set and all available reset
   queries report deasserted, retain the firmware setup and make no CPM calls.
   If not, require every board callback. In order: power hierarchy/rails; the
   four clocks; the six resets in the table; repeater/peripheral confirmation;
   then set only CFG21 PHY_ENABLE if still clear. Any error aborts.
5. **Controller only, not PHY cold init.** Clear RUN_STOP and wait for
   DEVCTRLHLT (bounded 100 ms). Clear generic `GUSB2PHYCFG.SUSPHY` (bit 6) only;
   do not touch USB3/DP SUSPHY. Issue DCTL.CSFTRST with a bounded 500 ms wait.
6. **EP0-only gadget.** Keep device mode; install a polled 4 KiB event buffer
   (interrupt masked); acknowledge old event count; select high speed/address
   zero; enable disconnect/reset/connect-done events; issue DEPSTARTCFG,
   SETEPCONFIG and SETTRANSFRESOURCE for EP0 OUT/IN; enable only physical EP0
   endpoints; arm an 8-byte SETUP TRB; then set RUN_STOP.
7. **Poll in the shim idle/photo windows.** Handle SETUP → optional data →
   XferNotReady(status) → status completion → rearm SETUP. OUT data is accepted
   only after the residual count proves its exact size. Reboot/stamp side
   effects follow the ACK rules in `PROTOCOL.md`.

This is the same generic DWC3 TRB/EP0 shape that worked on Tegu, with Laguna's
provider, USB2-only and DMA gates made explicit.

## 4. DMA and memory-swap are separate admission gates

The DWC3 references three no-map `xhci_dma*` pools. The smallest,
`/reserved-memory/xhci_dma_buffer@9050000`, is `0x09050000..0x0905bfff`
(48 KiB) and is a reasonable **candidate** for a 16 KiB EP0 arena after the
previous owner is quiescent. It is not automatically allocated by this code.
A DT reservation does not establish that ABL stopped using it, that the CPU may
write it, or that `amb_smmu` stream `0x8100` translates it.

`laguna_usb_builtin_dma()` supplies an in-image arena for the MMU-off shim, but
`dma == dma_pa` is accepted only when the integrator has independently admitted
identity DMA. The default integration snippet keeps `dma_path_admitted = false`.
An XNU adapter can instead pass distinct non-cacheable CPU and device addresses
and add the required cache maintenance if a cacheable mapping is used.

The live node also has `memory-swap-enabled`. The local mainline DWC3 source
has no matching property, and the lane did not establish its downstream DMA
semantics. Resolve this before hardware admission; do not silently assume that
it changes (or does not change) TRB byte order. Host tests validate standard
little-endian DWC3 TRBs only.

## 5. v3 and XNU integration boundary

- Copy `shim/laguna_usb.{c,h}` beside the v3 shim and add the object to its
  freestanding build. `integration/teguload_v3_usb.inc` shows the handlers,
  framebuffer/stamp wiring and deliberately off-by-default DMA switch.
- Mirror existing console text with `laguna_usb_write`/`laguna_usb_log`; the
  capability marker is inserted before the first byte automatically. Set the
  framebuffer only after `con_init`, using `con_fb_pa`, `FB_W`, `FB_H`, and
  `FB_STRIDE` (native little-endian BGRA).
- Set `tgl_failure_record` as the eight-word stamp sink and route the reboot
  callback to a wrapper around `reset_now`. Keep the original on-panel console.
- Poll while waiting for a host and during the existing 40-second photograph
  window. Do not call `loader_boot` in the middle of a control transfer.
- **A jump into XNU is an ownership transition, not a free live connection.**
  Either quiesce/re-enumerate in an XNU adapter or explicitly preserve code,
  log, event/TRB memory, non-cacheable CPU mappings, DMA translations and poll
  ownership. Otherwise the host must restart its reader at the phase change.
  The shim-only delivery does not claim an uninterrupted shim→XNU session.
- No XNU filesystem sync, forced-fastboot flag, hotfix, or pull is implied by
  the reused reboot request or product string.

## 6. What remains hardware-dependent

Host tests cannot prove CPM response formats, actual reset/clock state,
Type-C/repeater handoff, MMIO accessibility (including asynchronous SError/RAS
behavior), SMMU translation, `memory-swap-enabled`, enumeration, or sustained
DMA. Those are the next bounded device validations for the parent-owned phone,
not facts asserted by this host-only delivery.
