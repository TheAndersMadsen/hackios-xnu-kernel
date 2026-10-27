# ABL LPCM REQUEST/REPLY — refusal decode + corrected request recipe (lane A-ABL)

2026-10-27. Same live ABL binary (`live-abl_a.payload.bin`, sha256 `0ab1fbac…`,
base `0xffff0000be000000`) + capstone index. Host-only. Decodes the v3.7 park
(`research/laguna-usb-fix/attempts/20261027-085704-v37/ramdump/kernel.log`):
`cpm-geo2 DONE arg=0x4d4241` (LPCM magic CONFIRMED — predicted
`[0x05250000]==0x004D4241`), `mba-ack DONE` (channel mechanics work),
`cpm-clk REFUSED arg=0x7f8` (firmware refused the clock request), `phy-resume
FAULT -5`.

## 0. TL;DR — why the firmware refused

The shim sent **cmd 0x04 with id 0x07 and byte3=0** ("enable", per
ABL-MBA-PROTOCOL.md). ABL's binary proves cmd 0x04 and cmd 0x02 address **two
different id spaces**, and 0x07 exists in both — meaning different resources:

| class | builder | id source (clock desc) | id 7 belongs to |
|---|---|---|---|
| **cmd 0x02** (gate/put, byte3: 0=on, 1=off) | `0xbe03159c` via desc+0x30 | **+0x30 column = the DT lpcm5 clock numbers** | row 0x14 `hsio_n_usbc_non_sticky` — the clock the shim wanted |
| **cmd 0x04** (reset ctrl, byte3: 0=assert, 1=deassert) | `0xbe031514` via desc+0x34 | **+0x34 column = a separate reset-id space** | row 0x3a `hsio_n_pixel_ck0_clk` — a **display pixel clock's reset** |

So the shim's frame was "ASSERT the `hsio_n_pixel_ck0_clk` reset" — an
unrelated display resource, with assert polarity (the opposite of its enable
intent), no `put` first, and byte1 (seq/client, ABL sends **5**) not from
ABL's table. The firmware's refusal (reply dword1 = -8/0xf8 family) is the
expected answer to that frame. The corrected frame for the shim's intent is
**`{0x08000000, 0x00070502, 0x00000001, 0}`** — cmd 0x02, seq 5, id 0x07,
byte3=0, aux=1 (§4).

## 1. Reply handling — how ABL validates the in-place reply (PROVEN)

`lpcm_send_mb_msg` (`0xbe03173c`) after the successful mba_send
(`0xbe0317f8–0xbe03186c`), where `x19` = caller's original 4-byte cmd struct
and `[sp+0xc]` = reply **dword1** (the in-place reply overwrote the internal
buffer, not the caller's struct):

1. `ldrb w8, [x19]` — original cmd byte0 (0x04 for clock gates).
2. cmd==2 branch checks original byte3==2 (unreachable for ABL's builders,
   which emit byte3 ∈ {0,1}); falls through.
3. `ldr w0, [sp, #0xc]` — **reply dword1**.
4. `cbz w0, 0xbe0317d4` — **reply dword1 == 0 IS the success predicate**
   (returns 0). CONFIRMS ABL-MBA-PROTOCOL.md §6.
5. Nonzero: log `'[E] ' + 'lpcm service failed. resp=%d'` (`0xbe17cc80`,
   format site `0xbe031834`) with the raw resp — **ABL does NOT decode or name
   LPCM error codes anywhere**; the value is passed through to the caller,
   which logs it again via the per-op format (`lcm_reset_clock`,
   `lcm_enable_clk`, …) strings (`0x17f490`, `0x183afa`, `0x176fbb`,
   `0x181714`).
6. cmd byte0==3 → return resp; byte0==0 → return resp; other → resp is run
   through the token validator `0xbe0316ec` (mask `0x01810801`, bit24) and
   that value returned.

**Verdict on 0xf8/0x7f8:** ABL contains no switch/table mapping LPCM resp
codes to names. The client-side errno convention is the only anchor: ABL
itself returns **-8** for invalid-argument cases in this stack (`0xbe0316c0`
seq-index>7, `0xbe03199c`/`0xbe031a70` null handle, `mba_send 0xbe04fbe4`
length mismatch) — and `mba_send`'s -8 is literally the same class of check
the firmware performs on an unknown/mismatched (cmd,id) pair. If the shim's
stamp packed `id<<8 | (reply_dword1 & 0xff)` then `reply dword1 =
0xFFFFFFF8 (-8)` reproduces `arg=0x7f8` exactly. Treat -8 as the LPCM
EINVAL-family "request invalid for this resource/state" — and fix the request
(§4) rather than hunting a name ABL does not have.

## 2. Request builder, byte-verified (PROVEN)

`lpcm_send_mb_msg 0xbe03173c` frame construction (`0xbe031760–0xbe03177c`):

- `mov w8, #0x8000000` → **msg[0] = 0x08000000 constant**. NO seq, id, or
  client bits in msg[0] — bit-by-bit verified; the GDMC `0x1A000000|id` form
  is a different class on a different mailbox. LPCM msg[0] never varies.
- `ldr x9, [x0]` (caller's {cmd_dword, aux_dword}) stored at buf+0xc →
  **msg[1] = cmd_dword, msg[2] = aux_dword**; `str wzr, [sp, #0x14]` →
  **msg[3] = 0**.

### 2.1 Clock-gate builder `0xbe031514` (cmd 0x04 — the RESET-control service)

`0xbe031514(seq_idx, id, en)`:
- `str x9(=4), [sp,#8]` → byte0 = **0x04**
- `bl 0xbe0316c0(seq_idx)` → seq = `seq_table[seq_idx & 7]`,
  table `0xbe1e11a0` = `[5, 6, 2, 3, 4, 0x23, 0x25, 9]`;
  **seq_idx = clock descriptor +0x08, which is 0 for every hsio_n row ⇒
  byte1 = 5 always for USB clocks** (static per-descriptor value, never a
  rolling counter — CONFIRMED: the descriptor rows carry 0 there).
- `strb id, [sp, #0xa]` → byte2 = **id = descriptor +0x34** (reset-service id)
- `bic w9(1), w19(en)` → byte3 = `1 & ~en` ⇒ **byte3 = 0 means en=1 = ASSERT,
  byte3 = 1 means en=0 = DEASSERT/RELEASE**
- frame: `{0x08000000, 0x04|seq<<8|id<<16|(1^en)<<24, 0, 0}`

### 2.2 Gate/put builder `0xbe03159c` (cmd 0x02 — the DT-numbered clock service)

`0xbe03159c(seq_idx, id, en, aux_sel)` — used by release `0xbe03199c` (via
descriptor **+0x30**) and the power-ON path:
- byte0 = **0x02**; byte1 = seq (same table; = 5 for hsio_n rows); byte2 =
  **id = descriptor +0x30 — this column IS the DT lpcm5 clock numbering**
  (row 0x14 usbc_non_sticky = 0x07, row 0x15 usb2_phy_reset = 0x11 —
  matches A-DT's DT derivation exactly); byte3 = `1 & ~en` ⇒ **byte3 = 0 =
  ON/put, byte3 = 1 = OFF/release**.
- **msg[2] (aux) = `1` or `2`** (`cinc w8, w9(1), ne(arg3 & 1)`,
  `0xbe0315e8–0xbe031608`); ABL's USB power-ON passes aux_sel=0 ⇒ aux=1.
- frame: `{0x08000000, 0x02|seq<<8|id<<16|(1^en)<<24, 1|2, 0}`

### 2.3 Set-rate `lcm_clock_set_rate 0xbe031a70`

Uses descriptor **+0x30** with builder `0xbe03163c`:
`{0x08000000, 0x50000002|id<<16, rate, 0}` — ABL sets `hsio_n_phy_fw_clk`
to 400 MHz (`0x17d78400` at `0xbe00e18c`) on its way into USB init.

### 2.4 The dual-id table (group 0, `0xbe1b7f60`, 71 rows × 0x38; PROVEN)

USB-relevant rows (name, **put id = +0x30 (cmd 0x02)**, **rst id = +0x34
(cmd 0x04)**):

| row | clock | put (cmd 02) | rst (cmd 04) |
|---|---|---|---|
| 0x13 | hsio_n_usbc_sticky | 0x08 | 0x0b |
| 0x14 | hsio_n_usbc_non_sticky | **0x07** | 0x0a |
| 0x15 | hsio_n_usb2_phy_reset | **0x11** | 0x0d |
| 0x16 | hsio_n_usb3dp_phy_reset | 0x12 | 0x0e |
| 0x17 | hsio_n_ebu | 0x13 | 0x0f |
| 0x23 | hsio_n_phy_fw_clk | 0x03 | — |
| 0x24 | hsio_n_u3phy_fw_clk | 0x14 | — |
| 0x25 | hsio_n_u3phy_ref_clk | 0x10 | — |
| 0x28 | hsio_n_u2phy_apb_clk | 0x0e | 0x09 |
| 0x2a | hsio_n_u2phy_ref_clk | 0x05 | — |
| 0x2b | hsio_n_usbdp_suspend_clk | 0x06 | — |
| 0x2c | hsio_n_usb_drd_bus_clk | 0x09 | 0x0c |
| 0x2e | hsio_n_usb_top_csr_clk | 0x0b | 0x06 |
| 0x2f | hsio_n_usb_dp_cfg_clk | 0x0d | 0x04 |
| 0x30 | hsio_n_usb_ebu_clk | 0x00 | — |
| 0x3a | **hsio_n_pixel_ck0_clk** | 0x01 | **0x07** |
| 0x3b | hsio_n_pixel_ck1_clk | 0x02 | 0x08 |

(The +0x30 column reproduces A-DT's DT `lpcm5` clock numbers 07/08/0e/11 for
the USB2 set — the two independent derivations agree.)

## 3. Preconditions ABL satisfies before its first USB clock gate (PROVEN order)

ABL's USB power-ON is the deferred state machine `0xbe00def4` (arg w1=1),
queued at dwc3 start; the exact order:

1. **PMIC rails over the SAME mailbox, class 0x0A** — regulator enable
   `0xbe16e4b8(rail, &status, 1000 ms)` → `0xbe16e028` → builder
   `0xbe16e030`: msg `{0x0A000000, (type<<24)|(id<<16)|param, w4, w5}`
   (type 0/1/2 from op; id = rail; timeout constant `0x3b9ac9ff` ns):
   **LDO11M (0x17) → BUCK12S (0x37) → LDO19M (0x1f)**, each verified via a
   status read (the `0xbe16e4b8` post-check `tbnz w8,#0` + re-enable
   `0xbe16e528`). The rails are LPCM traffic on mbox[2] — S1's bring-up must
   decide explicitly whether its "PMU D0" step covers them.
2. **put before reset** for `hsio_n_usb2_phy_reset`:
   `0xbe03199c(rec_0x15, 1, 0)` = cmd 0x02, put id 0x11, byte3=0 (ON),
   aux=1 — THEN `0xbe0318d8(rec_0x15, 0)` = cmd 0x04, rst id 0x0d, byte3=1
   (DEASSERT). Never a bare cmd-0x04.
3. `0xbe00ec50(1)`, `0xbe00ecac(1)`, `0xbe00e7d0`, `0xbe00ea04` module
   helpers (local; not LPCM sends) between the clock ops.
4. USB3/DP extension (arg-dependent): rows 0x16/0x23/0x24: set_rate(phy_fw,
   400 MHz) → `udelay(100)` → put(0x03) → put(0x14) → put(0x12) →
   rst-deassert(0x0e) → `udelay(1000)` → **Phy SRAM init wait**
   (`'Phy SRAM init timed out'`, `0xbe00ed40`).
- **Announce/session messages:** two additional mbox[2] classes exist —
  `{0x0C000000, 0x00030001, 0, 0}` (`0xbe00f024`) and
  `{0x02000000, 0x00000004, 0, 0}` (`0xbe02b170`). Both reach the sender only
  through runtime-registered pointers (no static xref), so **whether they run
  before the first clock gate is UNPROVEN from this binary** — they look like
  subsystem-online announces; S1 should treat them as candidates if puts keep
  failing after §4. No seq-reset or drain message was found. mbox-mode flag
  `0xbe2f5768` is statically 1 (ABL-MBA-PROTOCOL §5) — nothing to set up.
- **ctl/GO semantics stand:** ctl = `page+0x04` bit24 set (msg[0] bit21=0 for
  all LPCM classes seen: 0x08000000, 0x0A000000, 0x0C000000, 0x02000000 all
  have bit21=0); GO = `page+0x00 ← 1` after the copy; doorbell
  `page+0x08 ← 1` before + after. The v3.7 `mba-ack DONE` proves the shim's
  channel mechanics are correct — the failure is in the FRAME, not the
  transport.

## 4. Corrected request recipe (what the shim should send)

Intent "enable the USB2 clock set from cold", minimal USB2 form, in ABL's own
order (all frames 4 dwords, channel 0 / qid 0x12 on 0x05250000; success =
reply dword1 == 0):

```text
# 0. (rails — if the shim has no class-0x0A regulator support, document the gap)
#    {0x0A000000, (1<<24)|(0x17<<16)|..., ...}  LDO11M      [0xbe16e030]
#    {0x0A000000, ...}                          BUCK12S     [0xbe16e4b8]
#    {0x0A000000, ...}                          LDO19M

# 1. put usb2_phy_reset gate ON        (cmd 02, put id 0x11, on, aux=1)
{0x08000000, 0x00110502, 0x00000001, 0}

# 2. deassert usb2_phy_reset line      (cmd 04, rst id 0x0d, byte3=1)
{0x08000000, 0x010d0504, 0x00000000, 0}

# 3. USB2 clock set (the DT 07/08/0e/11 set = put ids, byte3=0 = on, aux=1)
{0x08000000, 0x00070502, 0x00000001, 0}   # usbc_non_sticky        (07)
{0x08000000, 0x00080502, 0x00000001, 0}   # usbc_sticky            (08)
{0x08000000, 0x000e0502, 0x00000001, 0}   # u2phy_apb (dt 0e)      (0x0e)
{0x08000000, 0x00110502, 0x00000001, 0}   # usb2_phy_reset (dt 11) (0x11)
```

byte1 = 0x05 in every frame (seq_table[0]; every hsio_n row carries desc+8=0).
If a clock must be turned OFF later: same frame with byte3=1. If a reset must
be asserted: cmd 0x04 with byte3=0. Never mix: byte2 is interpreted against
the cmd's own id space (cmd 02 → put ids = DT numbers; cmd 04 → reset ids).
Cross-check each id against the §2.4 table before sending.

## 5. S1 correction list

1. **cmd/id-space mismatch (ROOT CAUSE of the 0x7f8 refusal) — CORRECTED.**
   The shim sent cmd 0x04 with id 0x07. Per ABL's descriptor table
   (`0xbe1b7f60` row 0x3a vs row 0x14), cmd 0x04 + id 7 =
   `hsio_n_pixel_ck0_clk`'s RESET (display), not any USB clock; byte3=0 made
   it an ASSERT. The DT-numbered USB clocks (07/08/0e/11) are driven by
   **cmd 0x02** via the +0x30 column. Replace the constant with cmd 0x02.
2. **Polarity — CORRECTED.** byte3 = `1^en`: 0 = ON/assert, 1 = OFF/deassert.
   "Enable a clock" = byte3 **0** on cmd 0x02 (put); "deassert a reset" =
   byte3 **1** on cmd 0x04. ABL never sends cmd 0x04 without a preceding
   cmd 0x02 put for the same resource.
3. **seq byte — RESOLVED to a constant.** byte1 = seq_table[desc+8 & 7]
   (`0xbe1e11a0` = [5,6,2,3,4,0x23,0x25,9]); every hsio_n row has desc+8=0 ⇒
   **byte1 = 0x05**. It is a static per-descriptor value, not a counter; ABL
   never validates a seq echo. (If the firmware turns out to require it,
   0x05 is the value ABL has always used for these very clocks.)
4. **aux dword — CORRECTED.** Clock-gate frames carry msg[2] = **1** (ABL
   power-ON passes aux_sel=0 ⇒ `cinc` gives 1), msg[3] = 0. The earlier
   recipe sent aux=0.
5. **Reply decode — CONFIRMED with one clarification.** Success = reply
   dword1 == 0 (`0xbe031830`); ABL names nothing further (logs raw resp via
   `'lpcm service failed. resp=%d'` `0xbe17cc80`). The shim's 0x7f8 stamp is
   consistent with reply dword1 = 0xFFFFFFF8 (-8): an EINVAL-family refusal
   for a frame that targets an unrelated resource with assert polarity.
   Keep stamping raw dword1 — it is the only diagnostic ABL has.
6. **Preconditions — PARTIALLY NEW.** Before its first USB clock gate ABL
   sends three PMIC rail ops on the same mailbox in class **0x0A**
   (`{0x0A000000, (type<<24)|(rail<<16)|param, …}`, rails 0x17 LDO11M /
   0x37 BUCK12S / 0x1f LDO19M, `0xbe16e030`), and each USB clock bring-up is
   put(cmd 02) → deassert(cmd 04) as a PAIR. Two announce classes exist
   ({0x0C000000,0x00030001} `0xbe00f024`; {0x02000000,4} `0xbe02b170`) but
   their boot ordering is UNPROVEN (runtime-registered callers). If corrected
   cmd-0x02 frames are still refused, add the class-0x0A rail enables next —
   that is ABL's own precedent for "rails before gates".
7. **Channel mechanics — CONFIRMED CORRECT by v3.7.** `cpm-geo2 arg=0x4d4241`
   and `mba-ack DONE` validate the magic, page/window arithmetic, doorbell,
   and poll from ABL-MBA-PROTOCOL.md unchanged. `phy-resume FAULT -5` is
   downstream of the refused clock (clock never came on), not a new defect.

---

## Summary (5 lines)

1. The refusal is a FRAME bug, not a transport bug: shim sent cmd 0x04 (reset
   service, +0x34 id space) where the DT clock 07 lives in cmd 0x02 (put
   service, +0x30 id space) — so id 7 selected `hsio_n_pixel_ck0_clk`'s
   reset, a display clock, with assert polarity, no put first.
2. Reply handling verified: success = reply dword1 == 0; ABL logs raw resp via
   `'lpcm service failed. resp=%d'` and names no error codes anywhere —
   0xf8/0x7f8 = -8 (EINVAL family) is consistent and final; nothing more to
   decode from ABL.
3. Builder byte-verified: msg[0] = 0x08000000 constant (no seq bits);
   msg[1] = cmd|seq<<8|id<<16|(1^en)<<24 with seq = 5 (static table
   [5,6,2,3,4,0x23,0x25,9], desc+8=0 for all hsio_n rows) and id from the
   cmd's own column; msg[2] = 1 for gates.
4. Preconditions: ABL powers LDO11M/BUCK12S/LDO19M over class-0x0A frames on
   the same mailbox, then always put(cmd 02, byte3=0) → deassert(cmd 04,
   byte3=1) as a pair; two announce-class frames exist but their pre-gate
   ordering is unproven; ctl/GO/doorbell semantics stand (mba-ack proves
   them on device).
5. Corrected minimal frame for DT clock 07: `{0x08000000, 0x00070502,
   0x00000001, 0}` — cmd 02, seq 05, id 07, byte3=0 (on), aux 1; full USB2
   recipe in §4.
