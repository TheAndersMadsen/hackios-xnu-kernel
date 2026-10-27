# ABL MBA/CPM WIRE PROTOCOL — send path, doorbell, completion, response (lane A-ABL)

2026-10-27. Same binary and method as `ABL-USB-STATE.md`: live stock ABL
`research/laguna-ramdump/evidence/live-abl_a.payload.bin` (sha256 `0ab1fbac…`,
`deepspace-17.4-16433848`), runtime base `0xffff0000be000000`, capstone full
linear index + adrp/add xref walking. **Host-only; no device action.**
Device facts from the v3.5 park (`research/session-20261027-usbboot/v35-kernel.log`):
`mba-live DONE arg=0x7004d4241` and `cpm-geo DONE arg=0x60007` are consistent
with the static analysis below; `arg=0x…4d4241` is exactly the MBA magic word
this report predicts (§3.1), and `6 channels` is mbox[0]'s static channel count.

## 0. What this document resolves

S1's three DEVICE-VALIDATE placeholders, all three now resolved from ABL code:

| # | Placeholder | Resolution |
|---|---|---|
| 1 | per-channel message window (stride) | page = `base + 0x10000 + QID*0x1000`; **message window at page+0x100** (§4 step 7) |
| 2 | doorbell write (offset + value + sync) | **page+0x08 ← 1, then read-back, then `dsb sy`** — done BEFORE the message copy and again after the reply; plus a "GO" word **page+0x00 ← 1 AFTER the copy** (§4 steps 4/9/13) |
| 3 | response location + shape | **in-place**: mba_send copies 4 dwords from page+0x100 back over the caller's buffer; LPCM checks reply dword1 (0=OK); the `0x1a400000`+bit23 check is the GDMC (mbox[0]) convention, NOT LPCM (§6) |

## 1. The mailbox objects (static .data, PROVEN)

Table `0xbe2f5138`, accessor `get_mailbox(i)` = `0xbe04fea0` (bounds `cmp w0,#2`;
`ldr x0, [0xbe2f5138 + i*8]`):

| idx | object | base VA (=PA, identity/tagged) | channels | base+0x20 | role (proven by callers, §7) |
|---|---|---|---|---|---|
| 0 | `0xbe2f4f50` | `0x06250000` (stored `0xffffffff06250000`) | 6 | 0 | GDMC IPC (thermal/power votes, LPB) |
| 1 | `0xbe2f5030` | `0x0e000000` | 1 | 1 | 16-dword big-message channel (20000 ms timeout user `0xbe02ca58`) |
| 2 | `0xbe2f5110` | `0x05250000` | 1 | 2 | **LPCM = CPM clock/reset/power requests** |

- The stored base is a tagged VA (high half `0xffffffff` is ignored by
  translation — top-byte-ignore); effective MMIO base = low 32 bits. ABL
  identity-maps MMIO (same convention as DWC3 `0x0c400000`).
- mbox[2] @ `0x05250000` matches A-DT's DT `syscon @0x5250000`; the LPCM
  message window derived in §5 lands at `0x05272100`, i.e. inside the
  `mba-ctrl` neighborhood A-DT flagged — reconcile region sizes there.
- **Device cross-check:** the shim's `mba-live arg=0x7004d4241` contains
  `0x4D4241` = the MBA magic/version word this binary checks at `[base]`
  (§3.1); `cpm-geo arg=0x60007` = 6 channels = mbox[0]'s static count.

## 2. Channel records (0xb8-byte stride, PROVEN)

`ch = [mbox+0x08] + idx*0xb8`. Static contents read from .data:

| field | meaning (from use) | mbox[2] ch0 (LPCM) | mbox[0] ch0..5 (GDMC) | mbox[1] ch0 |
|---|---|---|---|---|
| +0x08 | **TX queue id (QID)** — drives ALL register/page arithmetic | **0x12** | 1, 2, 4, 6, 8, 0xa | 0 |
| +0x0c | peer/rx id (used by init loop `0xbe05028c`) | 0x13 | 0, 3, 5, 7, 9, 0xb | 0 |
| +0x10 (byte) | type: 1 ⇒ semaphore at ch+0x50 | 0 | 1, 1, 0, 0, 0, 0 | 0 |
| +0x11 (byte) | 1 ⇒ skip post-reply doorbell ack | 0 | 0, 0, 0, 0, 0, 0 | 1 |
| +0x14 | **max message dwords** (send rejects mismatches, err -8) | **4** | 1, 4, 4, 4, 4, 4 | 0x10 |
| +0x18 | poll sleep in µs | **0xc8 = 200** | 0xc8 | 0 |

Channel geometry sanity: 6 GDMC channels with qids {1,2,4,6,8,0xa} and peer ids
{0,3,5,7,9,0xb} — the `0x60007` device arg is consistent with the 6-channel
mailbox and the 0x07-adjacent id pairing.

## 3. Transport ops table (PROVEN)

`ops = [mbox+0x18]` — all three mailboxes share `0xbe2f7bc0`. Eleven fns,
`0xbe05022c..0xbe050450`, all take (mbox, qid) and compute registers as
`base + 0x10000 + QID*0x1000 + off` via the idiom
`mov w8,#off ; movk w8,#1,lsl #16 ; add w8,w8,w1,lsl #12`:

| ops off | fn | action |
|---|---|---|
| +0x00 | `0xbe05022c` | init: per channel, `page+0x04 ← 0x10000`, store mbox+0x20/idx into ch+0xac/b0, `bl 0xbe04ec28(ch+0xa8)`, `bl 0xbe04f518(ch+0xa8)`, conditional `page+0x08 ← 0x10000` / `|= 0x100` |
| +0x08 | `0xbe050330` | read `page+0x08 & 1` — **tx-complete status** |
| +0x10 | `0xbe05034c` | read `page+0x08 & 0x100` — alt status (bit21 messages) |
| +0x18 | `0xbe050368` | `page+0x04 &= ~0x01000000` (clear ctl bit24) |
| +0x20 | `0xbe05038c` | `page+0x04 \|= 0x01000000` (set ctl bit24) |
| +0x28 | `0xbe0503b0` | **DOORBELL: `page+0x08 ← 1`; `ldr wzr` read-back; `dsb sy`** |
| +0x30 | `0xbe0503d8` | **DOORBELL variant: `page+0x08 ← 0x100`; read-back; `dsb sy`** |
| +0x38 | `0xbe050400` | **GO: `page+0x00 ← 1`** |
| +0x40 | `0xbe05041c` | read `page+0x0c & 1` (rx status; poll client uses it) |
| +0x48 | `0xbe050438` | **magic: `([base] - 0x4d4000) == 0x241`** ⇒ `[base] == 0x004D4241` ('MBA', version 0x241) — matches device `arg=0x…4d4241` |
| +0x50 | `0xbe050450` | **window offset: `0x10000 + QID*0x1000`** — note `mov w8,#0x100; movk #1,lsl#16` = 0x**1**0100, i.e. the message sits at page+0x100 (§4 step 7) |

## 4. `mba_send` (`0xbe04fb90`) — exact op order (PROVEN)

Signature `mba_send(mbox, chan_idx, buf, ndwords, flags)`; errors: no
mbox/ops → -42 (-0x2a), bad chan → -2, `ndwords != ch[+0x14]` → -8.

1. `ch = [mbox+0x08] + chan_idx*0xb8` (`0xbe04fbd0`).
2. If `ch[+0x10]==1`: down(`ch+0x50` semaphore, forever) via `bl 0xbe01bc8c`
   (`0xbe04fc08`).
3. Save DAIF; if IRQs enabled → `msr daifset, #2`; lock `ch+0x88`
   (`bl 0xbe00f33c`, `0xbe04fc4c`).
4. `QID = ch[+0x08]`; **doorbell: `ops[+0x28](mbox, QID)` → `page+0x08 ← 1`,
   read-back, `dsb sy`** (`0xbe04fc6c–0xbe04fc74`).
5. If `ch[+0x10]==1`: also `ops[+0x30]` → `page+0x08 ← 0x100`, read-back, dsb
   (`0xbe04fc8c–0xbe04fc9c`).
6. Direction ctl from `msg[0]` **bit21** (`tst w8, #0x200000`, `0xbe04fcd8`):
   bit set → `ops[+0x18]` (clear page+0x04 bit24); bit clear → `ops[+0x20]`
   (set bit24). **LPCM header `0x08000000` has bit21=0 ⇒ ctl bit24 is SET.**
   GDMC `0x1A000000` also bit21=0 ⇒ set.
7. `w0 = ops[+0x50](mbox, QID)` = `0x10100 + QID*0x1000` (`0xbe04fcf4–0xfcf8`).
   **Copy the ndwords from `buf` to `[base + w0]`**, i.e. **message window =
   base + 0x10000 + QID*0x1000 + 0x100**, ascending, 32-bit stores
   (`0xbe04fd00–0xbe04fd1c`).
8. `ops[+0x38](mbox, QID)` → **GO: `page+0x00 ← 1`** (`0xbe04fd20–0xbe04fd34`).
9. If `ch[+0x11]==0`: sleep-free wait loop — **poll `ops[+0x08]`**
   (`page+0x08 & 1`) up to **0x3e9 = 1001 iterations**, sleeping
   `ch[+0x18]` µs between (`bl 0xbe156668`), with one refinement: if
   `ch[+0x10]==1` **and** byte2 of `msg[0]` has bit5 set (dword bit21 — the
   same bit as step 6) the poll switches to `ops[+0x10]` (`page+0x08 & 0x100`)
   (`0xbe04fdf0–0xbe04fe44`). Timeout → **-13 (-0xd)**.
   ⇒ **LPCM/GDMC completion = `page+0x08 & 1`; ABL BLOCKS here** — clock
   requests are fire-and-CHECK, never fire-and-forget (worst case ≈ 200 ms at
   200 µs/iter).
10. On completion: `ops[+0x50]` again, **copy ndwords from the window back
    over `buf`** — the reply is IN-PLACE (`0xbe04fd5c–0xbe04fd88`).
11. If `ch[+0x11]==0`: `ops[+0x28]` doorbell again (`page+0x08 ← 1`,
    `0xbe04fda0–0xbe04fdbc`); if `ch[+0x10]==1`: `ops[+0x30]`
    (`page+0x08 ← 0x100`) (`0xbe04fdcc–0xbe04fde4`).
12. Unlock `ch+0x88` (sem path: up at ch+0x50 via `bl 0xbe01bd9c`), restore
    DAIF if we masked it (`0xbe04fe4c–0xbe04fe7c`); return w20 (0, or -13).

No cache maintenance anywhere in the path — the only barriers are the doorbell
ops' `dsb sy` after a read-back. Map the mailbox MMIO Device (nGnRnE/nGnRE) or
run MMU-off like ABL does.

## 5. The LPCM clock request, end to end (PROVEN)

**Which mailbox/channel:** `lpcm_send_mb_msg` `0xbe03173c` does
`mov w0, #2 ; bl 0xbe04fea0` → **mbox[2]** (base `0x05250000`), channel **0**
(`mov w1, wzr` at `0xbe031784`), ndwords **4**, flags 0. QID = ch[+0x08] =
**0x12**. Therefore:

```text
magic        : 0x05250000 + 0x00000 = 0x05250000  (== 0x004D4241)
channel page : 0x05250000 + 0x10000 + 0x12*0x1000 = 0x05272000
  GO word    : 0x05272000  (write 1 after copying the message)
  ctl        : 0x05272004  (bit24 SET for LPCM — msg[0] bit21 = 0)
  doorbell   : 0x05272008  (write 1; read-back; dsb sy)  / completion = bit0
  rx status  : 0x0527200c  (bit0; used by the poll client, not mba_send)
message window: 0x05250000 + 0x10000 + 0x12000 + 0x100 = 0x05272100  (16 B)
timeout      : 1001 polls x 200 µs ≈ 200 ms → -13 on timeout
```

**Mode gate:** `lpcm_send_mb_msg` is reached only when the static byte
`[0xbe2f5768] == 1` — it **is 1** in this image (`0xbe2f5768 = 0x01`,
read at `0xbe0318fc/0xbe0319c0/0xbe031a94/0xbe031ce0`; no code writer ⇒
mailbox mode is unconditionally on). ABL's own USB clock gates during fastboot
init therefore use this exact path — consistent with `mba-live` at handoff.

**Message shapes** (all sent as the 4-dword MBA frame
`{0x08000000, cmd_dword, aux, 0}`; the `0x08000000` header word is written by
`lpcm_send_mb_msg` at `0xbe031760–0xbe031774`):

- **Clock gate enable/disable** — builder `0xbe031514` (called from
  `0xbe0318d8` enable / `0xbe03199c`-family with ch[+0x34] = LPCM gate id):
  `cmd_dword = 0x04 | (seq<<8) | (gate_id<<16) | ((1^enable)<<24)` — i.e.
  byte0=0x04 (CLOCK_GATE), byte1=seq, byte2=gate id, byte3=0 for ENABLE / 1
  for DISABLE. `aux = 0`.
  - seq comes from an 8-entry static table `0xbe1e11a0` = `[5, 6, 2, 3, 4,
    0x23, 0x25, 9]`, indexed by the clock descriptor's field (+0x08, ≤7).
    ABL never checks a seq echo in the reply ⇒ S1 can send any byte1.
- **Release/put** — builder `0xbe03159c` (release `0xbe03199c`, ch[+0x30] =
  LPCM release id): `cmd_dword = 0x02 | (seq<<8) | (id<<16) | ((1^state)<<24)`,
  `aux = 1 or 2` (`cinc` of arg bit0, `0xbe0315f0–0xbe031608`).
- **Value set (dvfs class)** — builder `0xbe03163c`:
  `cmd_dword = 0x50000002 | (id<<16)`, `aux = value`.
- **Two-phase commit** — `0xbe04f738` (poll client): phase 1
  `{0x05000002, id, state, 0}` expecting reply dword1 ∈ {0x11, 0x12}
  (0x10 = error), phase 2 `{0x05000003, id, 0, 0}` with deadline = now+100
  (0xbe04f7e8).
- **GDMC IPC** (mbox[0], channel from byte `[0xbe2fd980]`): 4 dwords
  `{0x1A000000|(id&0x1f), PA_lo, PA_hi, 0xb}` (builder `0xbe0b0f88`), 20000 ms
  variant sends 0x10 dwords (`0xbe02cb38`).

**Callers that matter for USB:** the USB power-off `0xbe00e248` releases clocks
via `0xbe03199c` (ids 0x15/0x28/0x2c/0x13/0x14/0x16/0x2e/0x2f) and gates the
USB TOP domain via `0xbe031c10` (ids 9/5) — every one of those becomes a
blocking mbox[2] send under flag=1.

## 6. Response/completion conventions (PROVEN — corrects ABL-USB-STATE §5)

- **Where the reply lands:** in the send buffer, in place — mba_send copies 4
  dwords from the window back over `buf` (§4 step 10). There is no separate
  response register.
- **Completion signalling:** `page+0x08 & 1` (bit21-clear messages — LPCM and
  GDMC both), polled with bounded sleep; -13 on 200 ms timeout. `page+0x0c
  bit0` exists as an rx-status used by the poll client, not by mba_send.
- **LPCM validation** (`0xbe0317f8–0xbe03186c`, after send): reads reply dword0
  byte0 (reply type):
  - type 0 ⇒ dword1 is a status, 0 = success (nonzero → logged, returned);
  - type 3 ⇒ dword1 is the result value, returned as-is;
  - type 2 ⇒ returns dword1 too (`0xbe031804–0xbe031814`);
  - any other type ⇒ dword1 is a "token" validated by `0xbe0316ec`
    (`(token+0x18) <= 0x18` i.e. class 0 only, and
    `(1<<24) & 0x01810801 != 0`), then returned.
  For **clock gates (cmd 0x04)** the caller chain (`0xbe0318d8`) treats any
  nonzero `lpcm_send_mb_msg` return as failure and logs `'lcm_reset_clock'`
  (error fmt `0xbe1970bf`). ⇒ **S1 success check = reply dword1 == 0** (with
  reply byte0 ∈ {0,2,3} sanity).
- **GDMC (mbox[0]) convention** — `gdmc_ipc_send_and_check` `0xbe0b0f88`
  (`0xbe0b100c–0xbe0b1024`): reply dword0 `(w & 0x7f400000) == 0x1a400000` AND
  `bit23` set = success. **This is where the `0x1a400000`+bit23 pattern in
  ABL-USB-STATE §5 lives — it is the GDMC service-tag convention, NOT a
  general MBA response magic and NOT used on the LPCM mailbox.** Corrected
  here as instructed.

## 7. Mailbox usage census (PROVEN)

| mbox | users |
|---|---|
| 0 (GDMC) | `0xbe0b05a4`, `0xbe0b0f88`, `0xbe0b1560`, `0xbe0b1a5c…0xbe0b26e4` (~15 send sites, chan byte `[0xbe2fd980]`) |
| 1 (16-dw) | `0xbe02ca58` (0x10 dwords, 20000 ms timeout), `0xbe02cc44` |
| 2 (LPCM) | `lpcm_send_mb_msg 0xbe031744`; cmd pair `0xbe00f024`/`0xbe02b170` (0x05000002/3); two-phase poll client `0xbe04f738`; `0xbe0b61d0`; `0xbe16e030` |

## 8. S1 correction list (each placeholder resolved)

1. **Message window stride — RESOLVED.**
   `page(mbox, qid) = base + 0x10000 + qid*0x1000`;
   `message = page + 0x100`. The +0x100 is easy to lose: the primitive is
   `mov w8,#0x100 ; movk w8,#1,lsl #16 ; add w0,w8,w1,lsl #12` = **0x10100 +
   qid*0x1000** (`0xbe050450`) — movk keeps the 0x100 low half. qid is the
   channel record's **+0x08** field, NOT the channel index: for the LPCM
   mailbox (idx 2, base `0x05250000`, one channel) qid = **0x12**, so the shim
   must use doorbell `0x05272008`, GO `0x05272000`, ctl `0x05272004`, message
   `0x05272100`..+16, rx-status `0x0527200c`. (GDMC mbox[0] qids are
   1,2,4,6,8,0xa ⇒ pages 0x…11000/12000/14000/16000/18000/1a000.)
2. **Doorbell — RESOLVED.** `page+0x08 ← 1` with read-back + `dsb sy`
   (`0xbe0503b0`), executed BEFORE the message copy AND again after the reply
   fetch; for type-1 channels also `page+0x08 ← 0x100` (`0xbe0503d8`) — the
   LPCM channel is type 0, so the 0x100 variant is never used on it.
   Additionally `page+0x04` bit24 is SET before the copy when `msg[0]` bit21
   is clear (`0xbe05038c`, selected at `0xbe04fcd8`) — LPCM header
   `0x08000000` ⇒ bit24 set — and `page+0x00 ← 1` ("GO") is written AFTER the
   message copy (`0xbe04fd30`, fn `0xbe050400`). Exact ABL order: doorbell(1)
   → ctl bit24 set → copy msg @ page+0x100 → GO(1) → poll page+8 bit0 → copy
   reply → doorbell(1). Only barrier in the whole path: `dsb sy` inside the
   doorbell ops (after a read-back) — map the window Device.
3. **Response — RESOLVED.** In-place: the 4-dword reply overwrites the send
   buffer. Completion = `page+0x08 & 1` (poll, `ch[+0x18]`=200 µs steps, max
   1001 iters ≈ 200 ms, timeout -13). LPCM semantics: reply dword1 == 0 = OK
   for clock gates; reply dword0 byte0 is a type tag (0/2/3 observed);
   nonzero dword1 for cmd 0x04 = failure (ABL logs `lcm_reset_clock`). The
   `(w&0x7f400000)==0x1a400000 && bit23` success check from
   ABL-USB-STATE.md §5 **applies only to the GDMC mailbox (mbox[0],
   `gdmc_ipc_send_and_check 0xbe0b100c`)** — do not apply it to LPCM replies.
   Post-send ack: doorbell(1) again (LPCM); the 0x100 ack is type-1-only.
   Extra facts for the port: send header word = `0x08000000` always
   (`0xbe031760`); length gate is exact (`ndwords == ch[+0x14]`, LPCM=4,
   err -8); mbx mode is statically ON (`0xbe2f5768==1`), so ABL's own boot
   clock gates traverse this path — the shim is re-implementing ABL's own
   transport, not inventing one.

### Caveats

- Register addresses above are VAs in ABL's identity map (= PAs); the tagged
  high half `0xffffffff` in the stored base is translation-ignored.
- The `page+0x08` doorbell semantics (set-vs-clear, W1C vs set) are INFERRED
  from op order; the VALUES and offsets are PROVEN instruction facts.
- ABL never touches mbox[1] or the GDMC channels on the USB power paths, and
  never quiesces any of this at handoff (ABL-USB-STATE §1.3 stands, now
  device-proven by `mba-live`).

---

## Summary (5 lines)

1. `lpcm_send_mb_msg 0xbe03173c` = mailbox idx 2 @ `0x05250000`, channel 0,
   qid **0x12**, always 4 dwords `{0x08000000, cmd, aux, 0}`; mbx mode static
   ON (`0xbe2f5768=1`) — ABL's own USB clock gates use this path at boot.
2. Channel page = `base+0x10000+qid*0x1000`; message window = **page+0x100**
   (the movk idiom makes it 0x10100+qid*0x1000, `0xbe050450`); doorbell =
   **page+0x08 ← 1** with read-back + `dsb sy`; GO = page+0x00 ← 1 after the
   copy; ctl bit24 at page+0x04 set for LPCM.
3. Completion = poll `page+0x08 & 1`, 200 µs × 1001 (~200 ms, then -13);
   ABL BLOCKS on every clock request — fire-and-check, never fire-and-forget.
4. Reply is in-place (4 dwords copied back over the buffer); LPCM success =
   reply dword1 == 0; the `0x1a400000`+bit23 magic is GDMC-mbox[0]-only
   (corrects the earlier doc); MBA magic `[base]==0x004D4241` matches the
   device's `arg=…4d4241`.
5. S1's three placeholders are fully resolved with instruction addresses —
   the shim hang was expected: with placeholder strides/doorbells the send
   could never complete; the numbers above are the wire truth from ABL.
