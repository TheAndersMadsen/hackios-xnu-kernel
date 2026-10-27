# ABL USB/CPM STATE AT HANDOFF — static disassembly verdict

Lane A-ABL, 2026-10-27. Host-only static analysis of the **live stock ABL read off
the device** (`research/laguna-ramdump/evidence/live-abl_a.payload.bin`, sha256
`0ab1fbac995a98cf7168d39ae6b959ddd3ea532cded4a4c92192c3d9965dbba2`, 3,268,608 B,
image `deepspace-17.4-16433848`, from `abl_a` via read-only capture
`live-abl_a.img` sha256 `3cb89625…` per `research/laguna-ramdump/MECHANISM.md` §6).
Runtime base `0xffff0000be000000`; all addresses below are runtime VAs, which are
**payload offsets + 0xffff0000be000000** (ELF wrapper PH2 confirms). MMIO is
accessed at VA==PA (identity-mapped; the driver stores `0x0c400000` directly as a
pointer). Method: capstone full linear disassembly (486,920 insn slots), adrp+add
xref walking, string-anchored function identification. **No device, flash, adb,
fastboot or QEMU action was performed.**

## 0. TL;DR verdict

On the `fastboot boot` handoff path ABL does **NOT** gate USB clocks, does **NOT**
power-gate the USB TOP domain, and does **NOT** reset or re-program the DWC3. It
only **stops the USB worker threads**, whose exit path clears
`DCTL.RUN_STOP` (bit31) — an electrical disconnect from the host. The DWC3 stays
clocked, in **device mode** (`GCTL.PRTCAPDIR=2`), with event buffers still
configured, at identity-mapped `0x0c400000`. The CPM/ACPM mailbox transports are
left **live** (no quiesce anywhere in the handoff sequence). A shim dying on a
GSNPSID/GCTL read right after a `fastboot boot` handoff is therefore **not
explained by any ABL USB teardown** — the inherited state is a clocked,
readable controller; look at the shim's own mapping/entry contract first, or at
whether the payload actually reached the CPU via a *non-fastboot* boot chain
(those DO power USB off).

## 1. The handoff path, end to end (PROVEN)

Single convergence point: `boot_image` at `0xbe06b2ac` — the only `bl` caller of
the function that prints `Starting Linux kernel ...` (string `0xbe1953d4`, xref
`0xbe06cf30` inside it). It has exactly one caller: `android_boot` (`0xbe012518`,
call site `0xbe0128b8`). `android_boot` has exactly two callers:

| Caller | Site | Meaning |
|---|---|---|
| `0xbe0136a4` (fn `pixel_loader` init `0xbe012d30`) | normal-boot decision (no key pressed after the 3480 ms window) | cold boot |
| `0xbe0147c0` (fn fastboot `boot:` handler `0xbe014594`) | after `download:` + `boot:` | **the `fastboot boot` path** |

The `fastboot boot` handler tail (`0xbe0146e4`–`0xbe0147c0`), PROVEN:

1. `OKAY` sent to host (`bl 0xbe087600` w/ `'OKAY'`).
2. `bl 0xbe0881c4(4)` — fastboot state word `[0xbe5588a4]=4`, `[0xbe5588a0]=1`.
3. `bl 0xbe0a3c40` — **USB task stop + screen black**:
   - `[0xbe56d094] = 2` (usb task state),
   - `bl 0xbe0a339c` and `bl 0xbe0a36b4`: set stop bytes `[0xbe56d070]`/`[0xbe56d080]=1`,
     then spin `bl 0xbe01d1cc(10)` (10 ms sleeps) until running bytes
     `[0xbe56d074]`/`[0xbe56d078]` clear — **joins two USB worker threads**,
   - `bl 0xbe0863ec(0)` — vtable dispatch → `0xbe1566d4` = bounds-checked
     full-screen fill (x=0,y=0,w/h from object) — **paints the panel black**,
   - `b 0xbe086478` → `0xbe15673c` display flush.
4. Sets `[0xbe540474]=1` ("booting-from-fastboot" latch), assembles the
   boot-payload struct from globals `[0xbe540478..0xbe5404d8]` (staged
   boot/vendor_boot/init_boot/dtbo pointers+sizes from the `download:`), calls
   `android_boot`.

Inside `android_boot` → AVB (`avb_slot_verify.c` string ref at `0xbe0126ac`) →
`boot_image`. The only DWC3-side action anywhere in this chain is the worker
exit below; **no clock, power-domain, PHY or controller-reset call executes**.

### 1.1 The worker-exit DWC3 write (PROVEN)

The DWC3 driver's worker/shutdown tail `0xbe063fd4` (one of the callbacks stored
at `[dwc+0x98]` by `dwc3_probe` `0xbe064100`) does:

```text
0xbe06403c: ldr x8, [x19, #0xd8]     ; MMIO base == 0x0c400000 (stored at 0xbe2f3db8)
0xbe064040: mov w9, #0xc704          ; DCTL
0xbe064044: ldr w10, [x8, x9]
0xbe064048: and w10, w10, #0x7fffffff ; clear BIT31 = RUN_STOP
0xbe06404c: str w10, [x8, x9]        ; gadget stopped => host-visible disconnect
0xbe064054..0xbe064078: poll DSTS(0xc70c) bit22 with 1000 ms bound (0xbe058678 timer)
```

That is the **entire** controller-level handoff action: clear DCTL bit31 (the
RUN_STOP bit in the standard Synopsys/Linux mapping; ABL itself treats bit30
as CSFTRST at init, and polls DSTS here for the stop to take effect). No DCTL
soft reset, no PHY writes, no `GUSB2PHYCFG`, no event-buffer teardown, no CPM
calls. (Bit30 of DCTL = CSFTRST is only used at *init*, §2.)

### 1.2 The final trampoline (PROVEN)

`0xbe06b1b0` (jump wrapper called from `0xbe06cf48` with
x0=VA→PA([x27+0xdd8]), w1=`0x8A000000`):
switches `TCR_EL1` (`bfxil` 0x3520), installs the identity TTBR0, cleans
`0xffff0000be000000..+0x2000000` (`bl 0xbe00f3a8`), `dsb sy; isb`, then jumps to
the trampoline `0xbe06b160`, which:

```text
mrs x2, sctlr_el1 ; and x2, x2, #-6 ; msr sctlr_el1, x2  ; MMU off, D-cache off
tlbi vmalle1 ; dsb sy ; isb ; msr daifset, #0xf          ; all exceptions masked
mov x1, xzr ; mov x2, xzr ; mov x3, xzr
br x7                                                    ; x7 = kernel entry PA, x0 = 0x8A000000 (DTB PA)
```

Standard ARM64 entry. `0x8A000000` is the DTB physical address passed as x0.

### 1.3 What the pre-jump sequence does NOT do (PROVEN)

The final pre-jump block `0xbe06cee8`–`0xbe06cf48` executes exactly:
event-dispatch(3) → boottime log (`0xbe06e140`) → history dump (`0xbe15aa38`) →
timer read (`0xbe058678`) → `daifset #2` → event-dispatch(4) → timer →
event-0x6ffe → print `Starting Linux kernel ...` → VA→PA → jump. The dispatcher
`0xbe096658` walks a 30-entry static handler array at `0xbe20fea0..0xbe20ff90`;
all 30 were surveyed (§4): **none of them touches USB, DWC3, CPM or clocks**.
Numeric notify IDs 1–11 are boot-stage events; FourCC tags exist too
('ANDR', 'RDMP', 'FAST', 'STAH', 'STFA'). **No mailbox send, no clock enable/
disable, no power-domain call occurs between the last USB thread join and the
trampoline.** ABL quiesces nothing before handoff.

## 2. What ABL left the DWC3 in (PROVEN + INFERRED)

ABL's `dwc3_core_init` at `0xbe0636a0` (identified by `'DWC_USB3_DEVICE_NUM_INT
is 0'` @`0xbe063968` and `'failed to initialize dwc3 core'` @`0xbe063a2c`,
prefix `'[DWC3] '` `0xbe063ed0`) runs once when fastboot USB starts:

| Step | Address | Register (base 0x0c400000) | Action |
|---|---|---|---|
| soft reset | `0xbe0636bc–0xbe063704` | DCTL `0xc704` | set BIT30 (CSFTRST), poll-clear with 10 ms sleeps |
| USB2 phy cfg | `0xbe063714–0xbe06373c` | GUSB2PHYCFG(0) `0xc100`, `0xc200` | clear low byte, set BIT1; later `& ~0x141`-style tweaks |
| GCTL clock bit | `0xbe063740–0xbe06374c` | GCTL `0xc110` | `GCTL |= 0x800` |
| USB3 pipe | `0xbe063750–0xbe063758` | GUSB3PIPECTL(0) `0xc2c0` | `|= 0x90020000`, later clear BIT31 etc. |
| ID read | `0xbe0637e4–0xbe0637ec` | `0xc120` (GSNPSID), `0xc144` (GHWPARAMS) | read 0xc120 and **discard** (`ldr wzr`), read 0xc144 bits[22:17] for event-buffer count |
| event bufs | `0xbe063810–0xbe06389c` | GEVNT area `0xc40c+` | allocate 0x208-B records, program GEVNTADR/COUNT, `str wzr` to 0xc40c+0x10*n |
| mode | `0xbe0638d8` | DCFG `0xc700` | `= 0x00200804` or `0x00020800` |
| events en | `0xbe0638e0–0xbe0638fc` | DEVTEN `0xc708` | `|= 7` |
| **device mode** | `0xbe063904–0xbe063924` | GCTL `0xc110` | `GCTL = (GCTL & 0x0007cf37) \| 0x2000` → **PRTCAPDIR=2 (DEVICE)** (mask 0xcf37 incl. PRTCAPDIR bits, +0x07 high half) |
| tuning | `0xbe063928–0xbe063930` | GUCTL2-ish `0xc12c` | `= 0xd036fff` |

Register map proven in use: GCTL=0xc110, GSNPSID/GSTS=0xc120, GUCTL2=0xc12c,
GHWPARAMS=0xc144, GEVNTCOUNT0=0xc40c (+0x10 stride), DCFG=0xc700, DCTL=0xc704,
DSTS=0xc70c, DEVTEN=0xc708, DGCMD block 0xc800/0xc804/0xc808, DEPEVT-related
0xc818/0xc81c.

State at the trampoline, per subsystem:

| Item | State at jump | Status |
|---|---|---|
| DWC3 clocks / USB TOP domain | ON — power-off path never runs on `fastboot boot` | PROVEN (§3 callers) |
| GCTL | device mode (`PRTCAPDIR=2`), clock bit 11 set, no further writes after init | PROVEN init; INFERRED unchanged (no writer in handoff path) |
| DCTL | BIT31 (RUN_STOP) cleared by worker exit ⇒ gadget stopped, VBUS session ended electrically | PROVEN |
| PHY | still programmed from fastboot start; no phy writes in handoff | PROVEN (absence), state INFERRED "live" |
| Event buffers / DEPCMD state | configured at init; never torn down on this path | PROVEN (absence of teardown) |
| MMIO mapping | VA==PA identity; `0x0c400000` is the literal pointer stored at `0xbe2f3db8` | PROVEN |
| CPM/ACPM mailboxes | live, no quiesce | PROVEN (§5) |

## 3. The USB power-off path exists — but is NOT on the fastboot handoff

`platform_usb_power_off` `0xbe00e248` (prologue `0xbe00e248`, body analyzed to
`0xbe00e790`) — the function behind every `hsio_n_* failed to disable ret:%d`
string:

1. Gate: reads latch `[0xbe5403b2]`; if bit0 set → return (one-shot).
2. Read power state `bl 0xbe031b34(0xa, &out)`; if `out==1` log
   `'limiting to USB2'` (`0xbe17a80c`) and exit early.
3. Acquire handles via `bl 0xbe031874(id)` for ids `0x15, 0x28, 0x2c, 0x13,
   0x14, 0x16, 0x2e, 0x2f`, then release each via `bl 0xbe03199c(h,0,0)`;
   each release failure logs, in id order:
   `0x15→'usb2_phy_reset…'`, `0x28→'usb_drd_bus…'`, `0x13→'usbc_sticky…'`,
   `0x14→'usbc_non_sticky…'`, `0x16→'hsio_n_usb3dp_phy_reset…'`,
   `0x2e→'hsio_n_usb_top_csr_clk…'`, `0x2f→0xbe182e33`.
4. Power-domain ops `bl 0xbe031c10(9,0)`, `(5,0)`, `(5,1)`, `(9,1)` with error
   strings `'failed to put USB TOP in power gated state from on:%d'`
   (`0xbe1af89a`) and `…from off:%d` (`0xbe182e5f`) — **USB TOP power gating**.
5. Tail `0xbe00e664–0xbe00e788`: looks up the DWC3 object (`bl 0xbe064100` with
   the `0xbe2f3db8` record), releases more handles (`0x2b, 0x14, 0x13, 0x2c,
   0x28, 0x2e`), queues deferred work (`adr 0xbe00def4/0xbe00debc` stored at
   `[dwc+0xc0]`, submitted via `bl 0xbe0b730c`), sets latch `[0xbe5403b2]=1`.

**All three callers** of `0xbe00e248` are `bl` sites `0xbe013600` (fn
`pixel_loader` init `0xbe012d30`), `0xbe0137d8` (fn `0xbe0136dc`), `0xbe013c78`
(fn `0xbe013c5c`), and each is guarded by the same latch pattern
`ldrb [0xbe540472]; tbnz #0 → skip; call; strb →latch`. Caller semantics:

- `0xbe012d30` `pixel_loader` init: after the 3480 ms power-key wait
  (`bl 0xbe0136dc` with `w0=0xd98`), on the **no-key continue-boot** exit it
  calls `0xbe0356c4`, checks `0xbe09bc24`, cleans env
  `'recovery-user-request'`, then USB power-off, stops module
  `'pixel_charger'` (`bl 0xbe01159c('pixel_charger',1)`), latches, and
  proceeds to `android_boot` (`0xbe0136a4`). **This is the cold-boot path —
  a normal Android/kexec boot hands the kernel a USB domain that was power
  gated.**
- `0xbe01347c` calls the small wrapper `0xbe013c5c` then dispatches event
  `'RDMP'` — the **USB ramdump mode** path (also
  `'FAILCannot boot in USB ramdump mode'` in the boot handler).
- `0xbe0136dc` (the button-wait function) itself powers USB off mid-wait if the
  latch is clear (site `0xbe0137d8`).

**Consequence:** USB is power-gated by ABL only on the *normal-boot* branch
(and ramdump). On `fastboot boot`, `0xbe00e248` is unreachable.

## 4. The 30 handoff-notify callbacks (PROVEN survey, none USB)

Array `0xbe20fea0..0xbe20ff90` (30 fn ptrs) — every entry disassembled ≥300
insns, event comparisons and string refs extracted. Subjects: pixel_charger,
mbfs_preboot, spi trace/esim, MTE debugpolicy, tp_dram (cpm_tracepoint_buff),
i2c_enable, GSA ROM patch, debug-policy dpm/avf, slot antirollback, boottrace,
debug_kinfo DT reservation, metrics-test, ramdump cmdline props, bl31_dram log,
abl_persist.log, UFS/zufs cmdline, GSC consent (/gvdu), trusty + keymaster,
arm_gic_init_percpu, xtum/pinfo. **Zero references to the USB platform driver,
CPM mailbox, or clock manager.** The `pixel_loader` fn pointer itself sits in
the adjacent init-module table (`0xbe20ff90` `'pixel_charger'`,
`0xbe20ffc8`→`0xbe012d30` `'pixel_loader'`, then `'pl_fb_cmsd_init'`,
`'fastboot_flash_boot'`, `'stack_chk_guard_init'`, `'virtua_ab_init'`,
`'target_cdd_init'`, `'cdd_ramdump_init'`, `'fastboot_target_dp_init'`,
`'fuse_print'`, `'fastboot_target_sbdp_init'`, `'arm_gic_init_percpu'`, …).

## 5. CPM/ACPM (Google MBA queue transport) usage (PROVEN)

- Transport source path embedded: `abl/gsp/dev/mailbox/google_mba/queue-transport/mba_transport.c`
  (`0xbe17231d`) and `.../poll/google_mba_poll_client.c` (`0xbe199894`).
- Three static mailbox objects, table `0xbe2f5138` (`get_mailbox(i) = 0xbe04fea0`,
  bound-checked `cmp w0,#2`):
  - `mbox[0]` `0xbe2f4f50`, reg base `0x06250000` (stored as
    `0xffffffff'06250000'`), **6 channels** (records `0xbe2f4b00`, stride 0xb8,
    per-channel ids 0/1, 2/3, 4/5, 6/7, 8/9, 0xa/0xb, size `0xc8`),
  - `mbox[1]` `0xbe2f5030`, base `0x0e000000`, 1 channel (ids 0/1, size 0x100),
  - `mbox[2]` `0xbe2f5110`, base `0x05250000`, 1 channel (ids 0x12/0x13).
- Send `mba_send` `0xbe04fb90(mbox, chan, msg, ndwords, flags)`: per-channel
  record fields verified (+0x14 = max message dwords, compared against `w3`;
  +0x10/+0x11 type/dir; msg[0] bit21 selects ops doorbell variant via
  `csel 0x18/0x20`).
- Message shape: **4 dwords**. `gdmc_ipc_send_and_check` `0xbe0b0f88` builds
  `{0x1A000000|(id&0x1f), PA_lo, PA_hi, 0xb}` (service ID 5 = LPB power vote,
  matching the live-log `GDMC MBA SERVICE ID 5` in MECHANISM.md); response
  validated by `(msg[0] & 0x7f400000) == 0x1a400000` and bit23. One variant
  sends 0x10 dwords with a 20000 ms timeout (`0xbe02cb38`).
- ~20 call sites across GDMC IPC/thermal/power (`0xbe0b0f88`,
  `0xbe0b05a4`, `0xbe0b1560`, `0xbe0b1a5c…0xbe0b26e4`), plus `0xbe00f024`,
  `0xbe02b170`, `0xbe031744`, `0xbe04f738` (poll client), `0xbe16e030`.
  The CPM clock/power-domain API proper is `0xbe031874` (acquire by id),
  `0xbe0318d8`, `0xbe03199c` (release), `0xbe031c10` (domain on/off).
- **Quiesce before handoff: NONE** (§1.3). Channels are live state that the
  kernel/shim may continue to use, though ABL's GDMC votes (e.g. LPB) remain
  outstanding — re-arming them is our own problem.

## 6. force-fastboot (PROVEN — hardens the evidence loop)

Two mechanisms:

1. **Persistent ABL env variable** `force-fastboot` (values "on"/"off"):
   - Set: `fastboot oem force-fastboot on` → handler `0xbe072d04` compares arg
     with `'on'`/`'off'` and calls `env_set('force-fastboot', 1)` at
     `0xbe072dc4` (`bl 0xbe09fb10`); errors:
     `'FAILfailed to enable/disable force-fastboot %d'`, usage
     `'FAILforce-fastboot [on|off]'`.
   - Read at boot start: `0xbe012a64` — if power key not "vol down pressed"
     (`bl 0xbe0a1fac`), `env_get('force-fastboot', &out)` (`0xbe09fb60`, site
     `0xbe012aa8`); nonzero byte → logs `'force-fastboot is on'`
     (`0xbe1876a4`) and calls `0xbe0881e4(reason,1)` — the same boot-mode
     API pixel_loader uses for `'platform requested'`. So the flag short-
     circuits into fastboot before USB/fastboot init.
   - Persistence medium: the env/devinfo store — `env` APIs live in the
     devinfo module (`0xbe09fa3c` validates magic `'FRDI'`-family word
     `0x52464944` and reads the **`devinfo` partition, 0x2000 bytes**, via
     `bl 0xbe08dd8c('devinfo', 0, 0x2000, …)`). NOT misc, NOT a cookie
     register. (misc strings exist but are the separate BCB/control-message
     path.)
2. **Debug policy** (non-persistent, signed DP payload): boot-time check
   `0xbe047410–0xbe047454` reads DT overlay prop
   `'/fragment@%d/__overlay__/%s'` with `x0='abl'`, `"force_fastboot"` via
   `bl 0xbe085b30`; `==1` logs `"Forcing ABL to stay in fastboot mode by
   Debug Policy ('abl/force_fastboot' == 1)"` and requests DBL fastboot
   (`bl 0xbe02b898('debugpolicy', 1)`).

Our loop worked because the env flag is checked on every boot start, before the
power-key window, and survives reboots in the devinfo partition. Note the
interplay: ABL main `0xbe0471ec` also consumes/patches reboot-reason env vars
(`'reboot'`/`'shutdown'` at `0xbe047560–0xbe0475b8`) — the same env store.

## 7. Strings/code inventory for A-DT / A-CPM (PROVEN)

USB clock/reset/power-domain names ABL registers (all `hsio_n_*` / `usbc_*`
strings in-image, and the ids used with the `0xbe031874` API):

- clocks: `hsio_n_usb_top_csr_clk` (`0xbe18297d`), `hsio_n_u2phy_apb_aon_clk`
  (`0xbe17c4f8`), `hsio_n_phy_fw_clk` (`0xbe1806bd`),
  `hsio_n_usbdp_suspend_clk` (`0xbe171805`), `hsio_s_top_csr_clk`,
  `hsio_s_mphy_ref_clk`
- resets/enable rails: `usb_drd_bus`, `usbc_sticky`, `usbc_non_sticky`,
  `usb2_phy_reset`, `hsio_n_usb3dp_phy_reset` (ids 0x28, 0x13, 0x14, 0x15,
  0x16 in the CPM API; + `0x2e` top-csr, `0x2f`, `0x2b`)
- power domains: `HSIO_N_GEM_1_USB` (`0xbe1809e0`),
  `FABSTBY_GEM_HSIO_N_USB` (`0xbe173d13`), `FABSTBY_LPCM_RFW_GDMC`,
  `FABSTBY_SSWRP_RFW_GDMC`; messages `'failed to put USB TOP in power gated
  state from {on,off}:%d'`
- driver log prefixes: `'[LGA_PLAT_USB] '` (`0xbe182df1`), `'[DWC3] '`,
  `'platform_usb_init failed (%d). USB charging may be degraded'`
- DT: `dwc3_core_init` queries node name `'dwc3'` (`0xbe1b04ef`) via
  `bl 0xbe06948c` (site `0xbe063838` and again `0xbe0638e8`); the `sswrp`
  block, `usb_dload_disable` var (`0xbe180691`), `'USB RAMDUMP Mode'`
  (`0xbe181fa2`), `'bringup phy'` (`0xbe17e8fe`).
- No `simple-framebuffer` anywhere (consistent with A6's notes); display uses
  the same black-screen fill we saw in the boot handler.

## 8. What this means for usb-preflight (plain words)

Our shim dies reading GSNPSID/GCTL at `0x0c400000` right after a
`fastboot boot` handoff — but this binary proves ABL hands us a **live,
clocked, device-mode DWC3 with an identity-mapped (VA==PA) register space**.
The only thing ABL changed before jumping is clearing DCTL bit31 (gadget
stopped — the host saw a detach roughly when `OKAY` came back) and joining
the worker threads. Nothing gated clocks, nothing re-programmed GCTL, nothing
power-gated USB TOP, nothing quiesced the mailboxes. So if our preflight
faults on that read, the fault is ours, not ABL's state: the likeliest causes
are (1) our shim touching `0x0c400000` through a mapping that doesn't exist
yet or isn't Device-memory typed, or before its MMU/preflight setup is
actually active — note ABL itself reads those registers with the MMU **on**
and identity-mapped, so a shim that reads them with its own page tables must
map `0x0c400000` Device/nGnRnE explicitly (or read before enabling its MMU
like the trampoline's post-MMU-off world expects); and (2) reading after we
did something that gated the domain ourselves. One fleet-level fork matters:
if the payload ever reaches the CPU through a **non-fastboot chain**
(normal boot image, or the pixel_loader no-key window expiring), ABL has
already **power-gated USB TOP and released the USB clocks** — in that world
usb-preflight genuinely must bring the domain up cold (A3's clock order
`07,08,0e,11`, resets `0a,0b,0c,09,06,0d`, CPM-mediated), and a GSNPSID read
**will** fail there. Instrument which path we're actually in before blaming
the silicon: the pmsg shim stamps can carry a "came-from" word, and the
DCTL/DSTS values themselves are a fingerprint (clocked+stopped = RUN_STOP=0
but registers readable).

### Concrete reads the shim can use as preflight evidence (all PROVEN offsets)

```text
GSNPSID     0x0c40_c120   (expect Synopsys 0x5533xxxx family)
GCTL        0x0c40_c110   bits[13:12]==2 => device mode; bit11 set
DCTL        0x0c40_c704   bit31==0 => gadget stopped by ABL worker exit
DSTS        0x0c40_c70c
DEVTEN      0x0c40_c708   ==0x7 from init (if ABL path held)
DCFG        0x0c40_c700   ==0x00200804 (or 0x00020800)
GEVNTCOUNT0 0x0c40_c40c   (+0x10 per buffer)
```

## 9. Claim ledger

- PROVEN = direct disassembly fact from the live ABL with instruction addresses
  cited above. INFERRED = "unchanged since init"/"live" style conclusions from
  the *absence* of writers in the audited handoff path (absence audited across
  the full instruction index, all callers enumerated by exact `bl` matching and
  function-pointer table scans).
- Binary identity: `deepspace-17.4-16433848`, matches the device's DBL version
  recorded in A6/abl-handoff notes. The October factory `CP3A.261005.005` ABL
  (`19be0a3a…`) is a different build; same-generation structure, but
  per-build addresses may shift a few pages.
- Not covered: runtime register values (this is static analysis), Trusty/GSA
  side effects beyond strings, and the exact devinfo-partition layout.

---

## Summary (5 lines)

1. `fastboot boot` handoff = OKAY → stop 2 USB worker threads (+ black screen)
   → android_boot → trampoline (MMU/D-cache off, DAIF masked, x0=DTB PA
   0x8A000000). Nothing else touches USB or CPM.
2. Worker exit writes `DCTL &= ~BIT31` (RUN_STOP=0, host-visible disconnect) —
   the only DWC3 write in the handoff; clocks/domain stay ON; GCTL stays
   device-mode; GSNPSID-readable; VA==PA identity mapping.
3. ABL's full USB power-off (clock releases 0x15/0x28/0x2c/0x13/0x14/0x16/0x2e/
   0x2f + USB TOP power-gate) runs ONLY on the normal-boot/ramdump branches —
   a kexec-style cold handoff inherits a powered-down USB instead.
4. ABL never quiesces its 3 MBA mailboxes (0x06250000 ×6ch, 0x0e000000 ×1ch,
   0x05250000 ×1ch; 4-dword `{0x1A000000|id, PA_lo, PA_hi, 0xb}` shape) before
   jumping; GDMC votes stay outstanding.
5. force-fastboot = persistent env var in the 8 KiB `devinfo` partition
   (checked at every boot start) + signed debug-policy prop `abl/force_fastboot`
   — both mechanisms now understood; a post-handoff GSNPSID fault on the
   fastboot path points at our shim's mapping, not ABL state.
