# ABL runtime facts (from device abl.log, park boot 2026-10-27 v33)

Header magic: `7a78000000000000f0ff070000000000` — 'zx' + length field. Log = ABL's own boot log ring.

## CPM / mailbox facts
[   0.410941][I] APC breadcrumb
[   0.411264][D] MBOX: gsa-mba: num of mailboxes 8
[   0.411596][D] Get APC WDT sub reason 0x00 from CPM breadcrumb2.
[   0.495465][A] start - cpm::wait_for_boot_sequencer_completion
[   0.495982][A] start - CPM bootargs
[   0.510052][I] Loading cpm
[   0.513162][I] cpm successfully loaded (528384 bytes) (3045 us)!
[   0.531350][I] Authenticated cpm (18137 us)!
[   0.538748][D] MBOX: cpm-mba: num of mailboxes 32
[   0.545744][I] Requesting GMC_VDD Vmin CRC32 to CPM
[   0.545783][D] MBOX: cpm-mba: num of mailboxes 32
[  22.132708][I] Started writing boot arguments to CPM
[  22.135924][I] Sending init GMC DVFS command to CPM with PfState7
[  22.138142][I] Sending init GMC complete command to CPM
[  22.240719][I] gsa_fw secure mbox now available (73257 us)!
[  22.942204] <0> [I] TCPM: C implementation is used
[  22.994992] <0> [I] [BOOTREASON] CPM thermal trip breadcrumb: 0x0
[   9.394434] <0> [CDD] CPM INFO tracepoints
[   9.396950] <0> [CDD] CPM INFO END
[   9.396974] <0> [CDD] CPM ERROR tracepoints
[   9.398399] <0> [CDD] CPM ERROR END
[   9.405496] <0> [E] [CDD] Failed to retrieve CPM register dump: -2
[  23.142153] <0> [I] TCPM: port connected
[  23.194745] <1> [I] TCPM: current limit: 1500mA
[  23.271366] <0> [I] TCPM: current limit: 1500mA
[  24.164705] <1> [I] TCPM: current limit: 1500mA
[  24.173089] <1> [I] TCPM: current limit: 1500mA

## USB / PHY / fastboot / handoff lines
[   0.411978][I] Slot A: active, successful, fastboot ok, 2 retries left
[   0.439761][I] Loading dphy
[   0.441505][I] dphy successfully loaded (282624 bytes) (1694 us)!
[   0.449737][I] Authenticated dphy (8180 us)!
[   0.549094][D] Writing ImemTraining DDR PHY firmware on Broadcast
[   3.477077][A] Ch2: DRAM PHY training successful
[   3.477122][A] Ch3: DRAM PHY training successful
[   3.477168][A] Ch0: DRAM PHY training successful
[   3.477213][A] Ch1: DRAM PHY training successful
[   6.802686][A] Ch3: DRAM PHY training successful
[   6.802733][A] Ch0: DRAM PHY training successful
[   6.802779][A] Ch1: DRAM PHY training successful
[   6.802908][A] Ch2: DRAM PHY training successful
[   7.231789][A] Ch1: DRAM PHY training successful
[   7.231835][A] Ch2: DRAM PHY training successful
[   7.232035][A] Ch3: DRAM PHY training successful
[   7.232081][A] Ch0: DRAM PHY training successful
[   7.691057][A] Ch1: DRAM PHY training successful
[   7.691116][A] Ch2: DRAM PHY training successful
[   7.691246][A] Ch3: DRAM PHY training successful
[   7.691292][A] Ch0: DRAM PHY training successful
[   8.239876][A] Ch3: DRAM PHY training successful
[   8.239948][A] Ch0: DRAM PHY training successful
[   8.240066][A] Ch1: DRAM PHY training successful
[   8.240112][A] Ch2: DRAM PHY training successful
[   8.845263][A] Ch0: DRAM PHY training successful
[   8.845309][A] Ch1: DRAM PHY training successful
[   8.845355][A] Ch2: DRAM PHY training successful
[   8.845401][A] Ch3: DRAM PHY training successful
[   9.558131][A] Ch3: DRAM PHY training successful
[   9.558178][A] Ch0: DRAM PHY training successful
[   9.558224][A] Ch1: DRAM PHY training successful
[   9.558403][A] Ch2: DRAM PHY training successful
[  10.328168][A] Ch2: DRAM PHY training successful
[  10.328212][A] Ch3: DRAM PHY training successful
[  10.328256][A] Ch0: DRAM PHY training successful
[  10.328300][A] Ch1: DRAM PHY training successful
[  11.200975][A] Ch1: DRAM PHY training successful
[  11.201127][A] Ch2: DRAM PHY training successful
[  11.201171][A] Ch3: DRAM PHY training successful
[  11.201215][A] Ch0: DRAM PHY training successful
[  12.360599][A] Ch3: DRAM PHY training successful
[  12.360643][A] Ch0: DRAM PHY training successful
[  12.360687][A] Ch1: DRAM PHY training successful
[  12.360781][A] Ch2: DRAM PHY training successful
[  13.726785][A] Ch2: DRAM PHY training successful
[  13.727548][A] Ch3: DRAM PHY training successful
[  13.727593][A] Ch0: DRAM PHY training successful
[  13.727638][A] Ch1: DRAM PHY training successful
[  15.436471][A] Ch2: DRAM PHY training successful
[  15.437509][A] Ch3: DRAM PHY training successful
[  15.437554][A] Ch0: DRAM PHY training successful
[  15.437599][A] Ch1: DRAM PHY training successful
[  17.567584][A] Ch0: DRAM PHY training successful
[  17.567629][A] Ch1: DRAM PHY training successful
[  17.567675][A] Ch2: DRAM PHY training successful
[  17.568735][A] Ch3: DRAM PHY training successful
[  19.223444][A] Ch0: DRAM PHY training successful
[  19.226784][A] Ch3: DRAM PHY training successful
[  19.226829][A] Ch1: DRAM PHY training successful
[  19.226875][A] Ch2: DRAM PHY training successful
[  22.000512][A] Ch2: DRAM PHY training successful
[  22.001791][A] Ch0: DRAM PHY training successful
[  22.001845][A] Ch1: DRAM PHY training successful
[  22.002766][A] Ch3: DRAM PHY training successful
[  22.075539][D] Writing ImemMissionMode DDR PHY firmware on Broadcast
[  22.541233][I] USB platform teardown attempt concluded.
[  22.616083][I] Jumping to BL31
[  22.769939] <0> [I] [UFS] performing phy calibration
[  23.181823] <1> [I] [PXL] fastboot enter reason: force-fastboot is on
[  23.187346] <1> [I] [LGA_PLAT_USB] Hsio_n usb2 phy enabled=0 before teardown.
[  23.247847] <0> [I] [LGA_PLAT_USB] bringup phy
[  23.248584] <0> [I] [LGA_USB_PHY] usb2_set_vbus_valid 1 20000
[  23.248698] <0> [I] [LGA_USB_PHY] usb3_set_vbus_valid 1 10000
[  23.256041] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_CONFIG_MASTER_VERSION: 0x1a617f8
[  23.256162] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_AON_FW_VERSION_0: 0x2190
[  23.256271] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_AON_FW_VERSION_1: 0xc9e
[  23.270065] <1> [I] [LGA_USB_PHY] DP TCA Orientation 0

## LAST 30 lines (tail = what led to the fastboot park)
[  23.246350] <0> [I] DVFS: Set dvfs id 0 to opp 19
[  23.246381] <0> [I] DVFS: Set dvfs id 1 to opp 21
[  23.246424] <0> [I] DVFS: Set dvfs id 2 to opp 13
[  23.247096] <0> [I] DVFS: Set dvfs id 3 to opp 7
[  23.247847] <0> [I] [LGA_PLAT_USB] bringup phy
[  23.248584] <0> [I] [LGA_USB_PHY] usb2_set_vbus_valid 1 20000
[  23.248698] <0> [I] [LGA_USB_PHY] usb3_set_vbus_valid 1 10000
[  23.250363] <1> [I] [DISP] display[0]:width:1280
[  23.250468] <1> [I] [DISP] display[0]:height:2856
[  23.251151] <1> [W] [LGA] Unknown event 0x54534146
[  23.256041] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_CONFIG_MASTER_VERSION: 0x1a617f8
[  23.256162] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_AON_FW_VERSION_0: 0x2190
[  23.256271] <0> [I] [LGA_USB_PHY] RAWCMN_DIG_AON_FW_VERSION_1: 0xc9e
[  23.270065] <1> [I] [LGA_USB_PHY] DP TCA Orientation 0
[  23.271186] <0> [I] [MAX-TCPC] TCPC BC1.2, type: 1 val:1
[  23.271366] <0> [I] TCPM: current limit: 1500mA
[  24.164527] <1> [I] [MAX-TCPC] TCPC BC1.2, type: 1 val:1
[  24.164705] <1> [I] TCPM: current limit: 1500mA
[  24.172784] <0> [I] [UDC] new high-speed device found
[  24.172913] <1> [I] [MAX-TCPC] TCPC BC1.2, type: 1 val:1
[  24.173089] <1> [I] TCPM: current limit: 1500mA
[  30.061626] <0> [I] [FB] Accept cmd:oem ramdump stage_file kernel.log
[  30.067659] <1> [I] [FB] OKAY
[  30.089423] <0> [I] [FB] Accept cmd:upload
[  30.089519] <0> [I] [FB] DATA00400000
[  30.191058] <0> [I] [FB] OKAY
[  30.217232] <0> [I] [FB] Accept cmd:oem ramdump stage_file abl.log
[  30.221938] <1> [I] [FB] OKAY
[  30.242624] <0> [I] [FB] Accept cmd:upload
[  30.242706] <0> [I] [FB] DATA00080000
