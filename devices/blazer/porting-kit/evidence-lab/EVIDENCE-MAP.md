# EVIDENCE-MAP — the five evidence channels as ONE system

B2 lane, 2026-10-27. Host-only. Every offset, command and cross-check in this
file was **verified against today's real dumps**
(`research/session-20261027-usbboot/{v2b,v33,v34b}-{kernel,abl}.log`,
`console-ramoops.txt`) and the frozen build ledger
(`research/laguna-usb-console/out/next-boot*/`, `/tmp/fleet/*report*.md`).

## 0. Address math (memorize these three)

```
dump aperture  = 4 MiB kernel.log, dump-base 0x95200000  (file off = PA - 0x95200000)
ramoops console = PA 0x95200000 .. 0x953FFFFF            (file 0x000000..0x1FFFFF)
pmsg base       = PA 0x95400000                           (file 0x200000)
                  -> "pmsg+0xNNNN" = file offset 0x200000 + NNNN
```

## 1. Channel table

| # | Channel | PA / file offset (4 MiB kernel.log) | Format | Writer | Alive when | Survives | Decoder + exact command |
|---|---------|--------------------------------------|--------|--------|------------|----------|--------------------------|
| 1 | **EP0 USB console** | host-side only (VID:PID `1209:0001`); ctrl `0xc0 bRequest=0x5a`, offset32 (`wValue=off&0xffff`, `wIndex=off>>16`, ≤512 B/chunk); 1 MiB ring, header `TEGU USB: log32 1048576` | ASCII log, append-only ring | shim `tgl_usb_console.c` (paint, stage lines, payload progress) | from shim console-init until XNU entry (after entry nobody services EP0) | nothing on device — it must be captured live | the reader IS the decoder: `/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py --timeout 120 \| tee <attempt>/live.log` (start BEFORE boot; single EP0 client) |
| 2 | **TSHIMST2 stamps** | pmsg+0x30000..+0x30644 = PA 0x95430000, **file 0x230000** | 64 B header {magic "TSHIMST2", per-build nonce u64, count, seq, last_stage, flags, cntfrq, t0, tlast, dropped, reason} + ≤64×24 B slots {seq, stage u16, flags u16, t u64, arg u64}; dsb after every record | shim `stamp.c` from `start.S` (physical stores, MMU off) | shim entry → terminal stamp (reason 1..6; 5=handed-off-to-xnu) | PSCI reset bounce, TEE clobber (+0x000..0x900), park + ABL re-entry, XNU handoff, the retrieval itself — **proven in all 3 dumps today** | `python3 research/laguna-usb-console/tools/decode-shim-stamps.py <kernel.log>` (handles TSHIMST1 v1 8-slot too; `--json`) |
| 3a | **XNU-MSGBUF v2 mirror** | header pmsg+0x3F800 (file 0x23F800), ring pmsg+0x40000 (file 0x240000, 1 MiB) | 64 B header {magic "XNU-MSGBUF v2" 16 B, seq, flags, msgbufp, msg_bufc, size, write, total, tail_off, tail_len} + ring text | XNU `laguna_panicdump_init` (post-MMU, from arm_vm_init time) | XNU init → panic/reboot; **post-arm_vm_init ONLY** | warm reset (raw DRAM) | `python3 research/laguna-ramdump/reader/read-ramdump.py <kernel.log> --dump-base 0x95200000 --logs --tail 65536` (v2-first, v1 fallback; **see §4 hazard**) |
| 3b | **XNU compact breadcrumbs** | pmsg+0x28000 (file 0x228000), 12×u32; boot ctr +0x28040, diag ctr +0x28080 (persist, sanitized) | stage codes 1..12 = ABL_SEEN, KERNEL_UP, PROBE_START, ROOT_STAGED, KEXEC_DONE, **XNU_ENTRY(6)**, XNU_INIT(7), ROOT_MOUNTED, LAUNCHD_EXEC, DYLD_STAGE, DYLD_SHARED_REGION, PANIC. No timestamps. Entry **clears** the 48 B then appends | `start.s:516 bl EXT(laguna_logdump_entry)` — **pre-MMU** (MMU on is ~start.s:847); C-side `laguna_panicdump_stage()` appends | from XNU's first instructions (pre-MMU) onward | warm reset | read-ramdump.py (same command; prints stage trail; gates on monotonicity) |
| 3c | **LGLW CRC/DONE slots** (frozen R3 ABI) | PA 0x95401100 / 0x95401180 (file 0x201100 / 0x201180) | 128 B records {magic "LGLW", version/size, seq, stage, producer, CRC-32 over [0,120), "DONE" committed last} | `laguna_entry_lastwords.s` (XNU_ENTRY, stage 6, producer 4) + panic path | same as 3b | warm reset, **but empirically clobbered on the Linux-kexec path** (patch 0130) | read-ramdump.py |
| 4 | **abl.log** (staged) | standalone 512 KiB file from fastboot (`DATA00080000`); 16 B header `7a78 0000 0000 0000 f0ff0700 00000000` ('zx' + ring length 0x7fff0) then **plain ABL text** (single session, zero-padded) | `[   sec][I/D/A/W/E]` lines, `<lvl>` ABL-main lines, `[FB]` fastboot trace | ABL itself | current cold boot only — ring re-initializes every DBL boot; **on the park path it holds the PARK session, not the shim session** | nothing (but re-derivable via channel 5) | `tail -c +17 <abl.log> > /tmp/abl-park.txt && python3 research/abl-log-decode.py /tmp/abl-park.txt` (verified: prints RTC, slot state, FB cmds, TRIAGE: PARKED-FASTBOOT) |
| 5 | **console-ramoops-0** (pstore, rooted Android) | `/sys/fs/pstore/console-ramoops-0` | ABL console text, **multiple sessions of the previous power cycle** (today: shim-boot session AND the following boot) | ABL console → zone PA 0x95200000; Linux overwrites it with its own console once it boots | read it right after landing on Android; holds the ABL view of the shim boot (download/auth/`Accept cmd:boot`/kernel jump) | until the next Linux boot overwrites the zone | `adb shell su -c 'cat /sys/fs/pstore/console-ramoops-0' > console-ramoops.txt` then grep (`Accept cmd:boot`, `Starting Linux kernel`, RTC). Today's pull: 54 KB, 2 sessions ✓ |
| 6 | **ramoops console zone inside kernel.log** | file 0x000000..0x1FFFFF | same zone as 5, as captured by the park-path dump | last Linux boot before the cycle (in today's dumps: torn single-bit-corrupted **Android** console, e.g. `Bo/ting Lilux`) | stale by definition on shim-death dumps | stale | read-ramdump.py console decode — **freshness-gate: if stamps say shim-death, this text is residue** |
| — | host: **pmsg low region (hostile)** | pmsg+0x000..~0x5C80 (file 0x200000..0x205C80): TEE/ABL fastboot re-entry log (+0x000..0x900), kexec handoff page (XNH0 @+0x120), probe ring (+0x1080..) | mixed residue | ABL/TEE/Linux-kexec | always stale on shim-path dumps | — | none; treat as clobber (patch 0130 relocated everything XNU-owned out of here) |
| — | **panel paint** | framebuffer (memory-backed, EP0 `0xc0/0x5d` TFB1) | red HANG panel: HANG/WHY/CODE/STAGE/ESRFAR/STAMPS/NEXT | shim terminal path | until power-cycle | nothing — photograph it | eyes, or `framebuffer_capture.py --scale 4` |

## 2. Cross-channel consistency on TODAY's real dumps (v2b / v33 / v34b)

| Check | v2b (06:55) | v33 (07:20) | v34b (07:47) |
|---|---|---|---|
| Stamps ABI | TSHIMST1 (v1, 8-slot) | TSHIMST2 (v2) | TSHIMST2 (v2) |
| Dump nonce | `0x6dbf3154abd454ef` | `0x20e41996ba745307` | `0x54b13be4f9b98219` |
| **Nonce vs frozen flashed image** (compiled movz/movk constant in `out/next-boot-v2|v33|v34/shim-boot-laguna.img`) | **MATCH** | **MATCH** | **MATCH** |
| Nonce vs fleet-report ledger | report v3.2 says `0x75989d9f...` ≠ dump — see note (†) | report v3.3 says `0xd058016c...` ≠ dump — see note (†) | report v3.4 says `0x7efbc033...` ≠ dump — see note (†) |
| cntfrq in header | 38400000 ✓ | 38400000 ✓ | 38400000 ✓ |
| Trail | 8/8, dropped 16, reason usb-fatal, death = usb-preflight ENTER, entry→death 26455 ms (t0 612369183) | 9/64, dropped 0, reason usb-fatal, terminal stamp = stale stage_id 7 FAULT −1 @5027 ms; real death inside `tgl_cpm_init` | 13/64, dropped 0, reason **cpm-never-came-up**, cpm-geo REFUSED 0x0 @4975 ms |
| XNU mirror (+0x3F800) | absent (stale garbage, no magic) ✓ expected | absent ✓ | absent ✓ |
| XNU breadcrumbs (+0x28000) | stale residue, no valid trail ✓ | same ✓ | same ✓ (reader correctly warns "nonmonotonic") |
| EP0 console | never enumerated ("No tegu diagnostic enumerated before timeout.") | same | same |
| abl.log session | PARK only (RTC 05:54:40 UTC, force-fastboot @23.13 s, ramdump cmds @31.8 s, `DATA00400000`+`DATA00080000`) | PARK only (RTC 06:19:35, @30.06 s) | PARK only (RTC 06:46:53, @30.25 s) |
| ABL reboot reason | `0xbab9 - Early Kernel PANIC` ✓ = shim's terminal PSCI reset | same ✓ | same ✓ |
| ABL CPM facts (park session) | cpm loaded 528384 B / auth 18137 µs, cpm-mba mailboxes=32 | same | same |
| Verdict | shim v3.2 died in usb-preflight; full evidence loop worked end-to-end | shim v3.3 died in CPM init (mislabeled preflight) | shim v3.4: cpm-geo REFUSED 0x0 @4969.5 ms (terminal cpm-first FAULT @4975.4 ms) — MBA mailbox dead cold → A-ABL syscon bring-up is the flagged missing layer |

(†) **Nonce ledger note:** `build-next-boot.sh` re-runs `shim/build.sh` on every
candidate build, which generates a FRESH nonce and overwrites
`shim/out/stamp-nonce.txt`. The nonces recorded in the fleet report addenda
belong to intermediate diagnostic images (`shim-v3x-diagnostic-*`) that were
**never flashed** — none of them appear as compiled constants in any frozen
`next-boot-*` image. The authoritative provenance for a candidate is the frozen
`next-boot-*/shim-boot-laguna.img` itself; verified scanner in §4.3.

Correlation bonus (v2 cycle): console-ramoops session 1 shows
`download:04000000` @2.94 s → `Accept cmd:boot` @4.46 s → kernel `0x139000`
copied → `Starting Linux kernel` @15.74 s; the shim's stamp `t0`=15.95 s on the
cntvct (38.4 MHz, monotonic from SoC reset) — **Δ ≈ 0.2 s** between ABL's jump
and the shim's first stamp. v34b `t0`=45.2 s shows a slower operator cycle on
the same free-running clock.

## 3. What each outcome means (decision tree)

```
boot candidate (`fastboot boot`)
├── EP0 1209:0001 enumerates          → OUTCOME A (console live; keep capturing)
├── shim fails → PSCI reset → fastboot park (force-fastboot armed)
│                                     → OUTCOME B (the proven loop; 3 of 3 today)
├── shim hands off, XNU runs          → OUTCOME B retrieval still works;
│                                       mirror/breadcrumbs become the decoders
├── phone reboots to ANDROID          → OUTCOME C (pstore post-mortem)
└── USB-dark, no park, no Android     → OUTCOME D (v34 lesson: send-stall etc.)
```

### 3.0 Pre-boot — every attempt, in this order (device-proven)

```sh
# arm the safety net (device-proven 2026-10-27: OKAY + autonomous park)
fastboot oem force-fastboot on
fastboot getvar force-fastboot            # expect: force-fastboot on

cd /Users/andersmadsen/Developer/personal/hackios
# start the ONE EP0 client BEFORE boot (verified invocation, matches today's live logs)
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py --timeout 120 \
  | tee research/laguna-usb-console/out/next-boot-<NAME>-live.log
```

### OUTCOME A — EP0 enumerated (`1209:0001` appears)

The running `usb_log.py` above IS the capture: it waits for the device, reads
the ring header (`TEGU USB: log32 1048576`), then replays from offset 0 in
≤512-byte `0xc0/0x5a` reads. Nothing else may open EP0 while it runs.

```sh
# if you need input into the shim/XNU (64-byte packets, single client rules apply):
/tmp/tegu-venv/bin/python research/tegu-ios/tools/tegu/usb_log.py \
  --send-after 'READY' --send 'ping'          # or --interactive
```

Even when EP0 is live, still do OUTCOME B afterwards — stamps and abl.log are
independent channels.

### OUTCOME B — phone parks in fastboot (proven 3/3 today)

Do NOT power-cycle. Photograph the panel first if it shows the red stage panel.

```sh
D=research/session-$(date +%Y%m%d-%H%M%S)-usbboot && mkdir -p $D

# 1) the 4 MiB aperture (device trace verified: "Accept cmd:oem ramdump
#    stage_file kernel.log" -> OKAY -> "Accept cmd:upload" -> DATA00400000)
fastboot oem ramdump stage_file kernel.log
fastboot get_staged $D/kernel.log        # host-side name for "upload" (4194304 B)

# 2) ABL's own ring (verified: DATA00080000 = 524288 B)
fastboot oem ramdump stage_file abl.log
fastboot get_staged $D/abl.log

# 3) decode FIRST the stamps, then the rest
python3 research/laguna-usb-console/tools/decode-shim-stamps.py $D/*-kernel.log
python3 research/laguna-usb-console/tools/decode-shim-stamps.py $D/*-kernel.log --json > $D/stamps.json
python3 research/laguna-ramdump/reader/read-ramdump.py $D/*-kernel.log --dump-base 0x95200000 --logs --tail 65536
tail -c +17 $D/abl.log > /tmp/abl-park.txt && python3 research/abl-log-decode.py /tmp/abl-park.txt
```

(Prefer the bounded collector when available: `python3
research/laguna-ramdump/tools/capture-fastboot.py $D` — it proves the channel
with a 1 KiB `RAMDUMP_MSG.txt` before the 4 MiB pull. B5's
`research/laguna-usb-fix/run-attempt.sh --image <candidate> --name <NN>`
automates this whole branch incl. decodes.)

### OUTCOME C — phone lands on Android (post-mortem)

```sh
D=research/session-$(date +%Y%m%d)-android && mkdir -p $D
adb wait-for-device
adb shell su -c 'ls /sys/fs/pstore'                                   # inventory
adb shell su -c 'cat /sys/fs/pstore/console-ramoops-0' > $D/console-ramoops.txt
# today's caveat: a generic pstore pull came back 0 bytes; console-ramoops-0
# is the proven file. It holds ALL ABL sessions of the previous power cycle,
# including the shim-boot session (download/auth/jump) that the parked
# abl.log does NOT contain.
grep -nE "RTC:|Accept cmd:(boot|download)|Starting [Ll]inux|AVB|Jumping" $D/console-ramoops.txt
```

### OUTCOME D — USB-dark, no park, no Android

1. Photograph the panel (it paints HANG/WHY/CODE/STAGE until power-cycle).
2. Do not blind-retry the boot (v34 lesson: send-stall must abort, per B5).
3. After any recovery to fastboot/Android, run the matching branch above —
   stamps survive the reset bounce and the park.
4. Record the abort in `research/laguna-usb-fix/ATTEMPTS.md` (one line).

## 4. Verified utilities / hazards

### 4.1 Decoder provenance — TSHIMST2 nonce vs the flashed image
The dump nonce alone does not prove WHICH image ran (nonces are per-build).
Match it against the frozen candidate. Verified method (movz/movk 64-bit
constant construction, interleaving-tolerant; today it matched 3/3 dumps):

```sh
python3 - <<'EOF'
import struct, sys
path, want = sys.argv[1], int(sys.argv[2], 16)     # <frozen shim-boot-laguna.img> <nonce from dump>
words = struct.unpack("<%dI" % (len(open(path,'rb').read())//4), open(path,'rb').read())
h=[want>>s&0xffff for s in (0,16,32,48)]
def enc(i,hw,imm):  # movz/movk 64-bit: base | hw<<21 | imm16<<5 | rd
    return (0xD2800000 if i==0 else 0xF2800000)|(hw<<21)|((imm&0xffff)<<5)
n=len(words); hit=None
for i in range(n):
    rd=words[i]&0x1F
    if words[i]!=enc(0,0,h[0])|rd: continue
    k=1; seq=[i]
    for j in range(i+1,min(i+200,n)):
        if words[j]&0x1F!=rd: continue
        if words[j]==enc(k,k,h[k])|rd: seq.append(j); k+=1
        if k==4: hit=(rd,seq); break
    if hit: break
print("NONCE 0x%016x %s in %s"%(want,
      "FOUND @ "+",".join(hex(x*4) for x in hit[1]) if hit else "NOT FOUND", path))
EOF
# verified today: next-boot-v2+0x6dbf3154abd454ef, v33+0x20e41996ba745307,
# v34+0x54b13be4f9b98219 -> all FOUND (non-contiguous movz..movk runs in the
# shim code region, ~0xb3b4..0xb864 with kernel@0x1000). Guard: a hit whose
# four offsets are CONSECUTIVE words is a data coincidence (e.g. 0xdeadbeef
# filler in the payload/avb region), not compiled code.
```

### 4.2 Payload → embedded XNU macho provenance
```python
# seg table @0x2a8: nsegs @0x0c; kernel macho = min(file_off)..max(file_off+file_size)
# over the segments that are not the 0x1800000 (24 MiB md0) ramdisk.
# today (next-boot-v34): kernel span 0x408..0xe67508, sha256 f249c68d214bd666...ed8e01
```
That embedded mach carries `XNU-MSGBUF v2` (producer) **and** the
`_laguna_logdump_entry` pre-MMU breadcrumb writer — i.e. the shim-path mach
DOES contain the R5 channels. It is a post-fixup-rewrite rebuild of the R5
evidence-record mach `mach.laguna141-paniccap` (3629fa3de2e8172075afed6dd1812
79d011227b2eaf4ea71947048639a6f864e); source-of-record for that hash:
`research/laguna-panic-capture/evidence/r5-hashes.txt`.

### 4.3 Hazard — read-ramdump.py VERDICT false positive (observed today)
On v34b-kernel.log (shim died at cpm-geo; XNU never ran) the reader printed
`VERDICT: XNU-ENTERED | probe ran=yes` — derived from **stale** kexec-probe
text (torn `KEXEC-XNU:` lines at pmsg+0x1084.., XNH0 handoff page, single-bit
corruption) left over from the 2026-10-26 Linux-kexec ladder. Until R5 adds
freshness gating, interpret as:
- trust `TSHIMST2` (nonce-matched) for "how far did the SHIM get";
- trust `XNU-MSGBUF v2` magic at file 0x23F800 / monotonic breadcrumbs at
  0x228000 for "did XNU actually run";
- ignore `probe:`/`KEXEC-XNU:` raw lines and any VERDICT that disagrees with
  the above two.

### 4.4 Doc drift found today
- `research/laguna-usb-console/RUNBOOK.md` §8 still cites the v1 mirror layout
  (header `0x95440000`/ring-base). v2 (patch 0130) header is pmsg+0x3F800 =
  file 0x23F800; ring data begins exactly at file 0x240000. The reader is
  v2-first so it works, but the doc should be corrected by its owner.

## 5. Instrumentation GAPS for the iOS goal

Window under audit: **shim's last stamp (ep0/handoff) → XNU mirror init
(arm_vm_init)**.

1. **Covered:** shim terminal stamps (`pre-xnu-handoff` ENTER, terminal reason
   `handed-off-to-xnu`=5) → XNU `start.s:516` breadcrumbs fire **pre-MMU**
   (verified: `laguna_logdump_entry` uses physical movz/movk addressing of
   0x95428000/0x95401100/80; MMU enable is ~start.s:847; and the flashed
   payload's mach contains the symbol). So the handoff boundary itself is
   double-instrumented.
2. **GAP — XNU entry (stage 6) → `laguna_panicdump_init` (stage 7):** the
   compact trail jumps straight from 6 to 7; MMU-on, arm_vm_init, and the
   panicdump console-zone seed have **no intermediate breadcrumb**, and the
   compact words carry **no timestamps**. A death in this window is visible
   only as "6 present, 7 absent". Minimal fix (R5, ~20 instructions + 1 u32):
   (a) from `laguna_logdump_entry`, pre-MMU, also store a dated 24-byte
   "XNU-ENTRY" prologue at pmsg+0x3F800 (v2 magic + seq + cntvct) so the
   mirror channel shows *when* XNU entered even if init never arms; (b) add
   one compact stage (e.g. MMU_ON=13 — requires widening the 12-word
   static_assert) at the MMU-enable point in start.s.
3. **GAP — ABL's view of the shim boot is lost on the park path:** `abl.log`
   staging returns only the PARK session (verified: 1× `DBL Cold Boot` per
   file, zero-padded after ~0x78xx). The shim session's ABL log survives ONLY
   in console-ramoops-0 after an Android landing (today's console-ramoops.txt
   proves it: download/boot/kernel-0x139000/jump). Cheap operational fix: end
   every park retrieval by rebooting to Android once and pulling pstore
   (`OUTCOME C`), archiving both. Code fix is not warranted.
4. **GAP — freshness keys are per-channel and asymmetric:** TSHIMST2 has the
   nonce (strong); the XNU mirror/breadcrumbs have none — stale windows are
   detectable today only by magic-absence/monotonicity, and the reader already
   mis-verdicted once (§4.3). Minimal fix: R5 mirrors the build nonce (or a
   boot-serial) into the v2 mirror header seq field at init.
5. **Not a gap:** EP0 never enumerating in 5 attempts is the debugging
   frontier (CPM/MBA mailbox cold path), not an instrumentation hole —
   channel 2 says exactly where it died, with the hardware's values attached.

## 6. Five-line summary

1. Nonce chain verified end-to-end: all three dumps (v2b/v33/v34b) carry the
   exact compiled nonce of their frozen flashed `next-boot-*` image; fleet-report
   nonces were superseded intermediate builds (build.sh regenerates per build).
2. Timestamps line up: freq=38400000 in every stamp header; ABL-jump↔shim-t0
   Δ≈0.2 s via console-ramoops; park RTC/`0xbab9` reboot reason consistent 3/3.
3. XNU channels (mirror/breadcrumbs) are STALE residue in all three dumps —
   expected (shim died pre-handoff); and read-ramdump.py's VERDICT said
   "XNU-ENTERED" from stale probe text — treat §4.3 as the standing hazard.
4. Gap audit: handoff→XNU-entry is instrumented (pre-MMU breadcrumbs, verified
   in the flashed mach); the real holes are XNU-entry→mirror-init (no dated
   prologue), ABL shim-session loss on the park path, and no freshness key in
   the XNU mirror header.
5. EVIDENCE-MAP.md now carries the channel table, per-outcome verified command
   blocks (park/Android/EP0/dark), the nonce-provenance scanner, and the
   decode order: stamps → mirror/breadcrumbs → abl.log → console-ramoops.
