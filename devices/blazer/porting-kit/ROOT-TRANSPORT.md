# ROOT-TRANSPORT — how the FULL root (out/system.apfs, 2.32 GiB) reaches the phone (laguna / Pixel 10 Pro)

Lane: C2 (root transport) · 2026-10-27 · **host-only** (no device access; host file/DT/git reads only).
Mission: the shim payload embeds the 24 MiB bootstrap root (`aebc9a8a…`, HFS) as md0 — good for M0–M2,
a proven dead end at M3. iOS userland needs the FULL foundation root (`out/system.apfs 637103e1…`,
2.32 GiB, cryptex-native dyld cache, dslocal 940/940, lsd/mapdb, backboardd/SpringBoard, all_pass=true).
The `fastboot boot` payload budget cannot carry it. This doc evaluates every transport and recommends one,
with a shim-version phased plan and the concrete spec for the recommended option.

Read first (all verified this rotation):

- `USERLAND-PATH.md` (B4) — the M0→M8 ladder; M3's fix is *inside* the foundation image; Gap 2 names
  this exact transport problem.
- `size-fit.md` (`../laguna-deviceroot/docs/`) — the honest budget: shim payload raw-caps ≈ 52 MB
  (≈ 37 MB root max); dyld shared cache 2.5 GB/43 files; full closure 92 MB carved (gz ≈ 30 MB);
  **no launchd-capable closure can also pass dyld inside ≤64 MiB**.
- `ATTEMPTS.md` + `attempts/20261027-08515*-v36/` — **v36 = `ep0-live` (2026-10-27 08:52:36)**: the
  CPM-first shim has a live EP0 USB console. Everything below builds on this.
- `DT-USB-FACTS.md` (A-DT) — DWC3/CPM mailbox facts; this doc adds the **UFS DT facts** (§7, same
  host `fdtget`/`dtc` discipline, same authoritative DTB).
- `../laguna-felix-fold/EVALUATION.md` RANK 3b — felix `ufs_scsi_read/_write` shapes (verified directly
  in source this rotation, `git show origin/main-working-kernel:baremetal_loader/ufsinit.c`).
- felix `loader_main.c` RDWBLOCK storage-core (verified: `secondary_core_main`, PSCI_CPU_ON parking a
  UFS-serving core; `rd=ufsroot0 ufsrw=1` cmdline).
- **Our own kernel tree**: `research/laguna-foundation/kernel-blazer/xnu/files/bsd/dev/hakios.blazer_ufs.c`
  + `iokit/bsddev/IOKitBSDInit.cpp:833` — the blazer 8796 kernel **already contains** a G5 UFS
  read-only takeover driver publishing `/dev/ufsroot0`, and `rd=ufsroot0` is already a recognized root
  token. This is the single biggest fact in the doc: option B's kernel side is code-complete.

---

## 0. The budget (why the full root cannot ride the current path)

| Item | Bytes | Source |
|---|---|---|
| `fastboot boot` image cap | 64 MiB = 67,108,864 B | ABL boot-image download buffer (`fastboot boot` payload) |
| v36 candidate `shim-boot-laguna.img` | **41,570,304 B** | `out/next-boot-v36/` (the "41.5 MB") |
| of which payload-usb.bin (raw) | 40,278,928 B | kernel+shim ≈ 15,113,104 B + v6 root 25,165,824 B |
| Shim-path payload hard cap | ≈ 52 MB raw (TEGUXNU rides mkbootimg ramdisk **uncompressed**) | size-fit.md §1 |
| Full foundation root | **2,492,203,008 B** (2.32 GiB) | `out/system.apfs 637103e1…` |
| its dyld shared cache alone | ~2.5 GB (43 files, 20H392 multi-cache) | size-fit.md §4 |

2.32 GiB vs 64 MiB: a factor of **36×**. No compression or trimming closes that through this channel —
and M3 specifically needs the **native cryptex cache** (ignition_level=1), which is the biggest object in
the image. The full root needs a transport that is *not* the boot image.

RAM is not the constraint: 16 GB device, kexec manifest rules (16-seg cap, ≤½-RAM) would admit a resident
2.32 GiB copy. DRAM *delivery* is the constraint.

---

## 1. Option table

| # | Transport | Bytes moved | One-time cost | Per-boot latency added | DRAM cost | New code | Risk | Dependency | Unlocks |
|---|---|---|---|---|---|---|---|---|---|
| A0 | EP0 control-transfer push after EP0-live | 2.32 GiB **every session**, in ≤64 B data stages | 0 | **~2.7 h best case** (≈256 KB/s), hours realistic | 2.32 GiB contiguous | host pusher only | low (channel proven) but pointless | CPM-first (done) | nothing — M2 at best |
| A1 | Shim **bulk-endpoint** addition (custom push) | 2.32 GiB every session | host tool 1–2 days | **1.5–8 min** (10–40 MB/s; ABL's own fastboot measured 4 MiB/102 ms ≈ 41 MB/s on this link) | 2.32 GiB contiguous | shim EP1 OUT/IN + DMA rings + host pusher | medium (new enumeration states; unproven shim surface) | shim lane bandwidth; 2.3 GiB contig RAM + ADT RAMDisk | M3+ (resident full root) |
| B | **UFS read**: full root pre-staged on a GPT partition, read **by XNU itself** (`hakios.blazer_ufs.c` mode 2), shim cold-brings the host and leaves it live | ~0.2–0.5 GiB **lazily**, per touch | one-time **adb push ≈ 2 min** (persists on userdata) | **≈ 0** staging cost per boot; + ≲1 s shim UFS bring-up; reads spread across boot (3–15 s total) | **≈ 0** (block device, no resident copy) | shim UFS probe ≈ S-half of CPM-first scope; kernel driver **already in tree** | medium-low (felix-proven SCSI shape; G5 constants DEVICE-VALIDATE) | staging partition decision + one coordinator-approved write; A-DT lpcm-0x06 ids | **M3→M8** (native cache read from UFS) |
| B′ | kexec-segment resident staging from Android | 2.32 GiB every session | 0 | Android boot + kexec + 2.32 GiB copy each session | 2.32 GiB | existing ladder (knob already added: `xnu.ramdisk_max=`) | low-med | **Android alive every session** (force-fastboot disarm policy conflict) | M3+ (resident) |
| C | Bigger RAM closure (C5 root-v7 ≤ 32 MiB) | 32 MiB in-image | 0 | 0 | 32 MiB | image work (C5's lane) | low | C5 mount/QEMU proof | **M2 only** — dyld cache can't fit; M3 stays closed |
| D | Split: RAM bootstrap + pivot to UFS "when XNU storage is up" | = B | = B | = B | 32 MiB + block device | **none beyond B** — no pivot machinery needed (see §2d) | = B | = B | M3→M8, plus a recovery boot volume |

## 2. Evaluation

### a) A0 — EP0 control transfer: impractical for payload, perfect for what it is

The shim is an HS gadget (minimum HS profile; USB3/DP resources explicitly out of scope — DT-USB-FACTS
§5). EP0 data stages are ≤64 B on HS. Each control transfer costs SETUP+DATA+STATUS transactions ≈ 7–9
transactions on the wire; a polled bare-metal gadget sustains ~10³–10⁴ transfers/s ⇒ **128–320 KB/s
best case**. 2.32 GiB ⇒ **~2.7–5.5 hours**, every session, into a pre-reserved 2.32 GiB contiguous RAM
window. Even the theoretical SS 512 B control chunk (not reachable — HS PHY only) would be minutes-to-
hours because per-transfer overhead, not link rate, dominates. **Verdict: never a payload channel.** EP0
stays exactly what v36 proved it to be: the console, stamp retrieval, and (proposed below) the UFS probe
evidence channel. A bulk-endpoint addition (A1) is technically worth building *someday* — HS bulk on this
very link measured ≈41 MB/s (ABL fastboot `DATA00400000` 4 MiB in ~102 ms, abl.log 30.089→30.191) — but
it duplicates what B gets for free, is per-session instead of one-time, and inflates the shim surface.
**Keep A1 as a documented fallback, not the plan.** Tegu-style embedded payloads (what we do today) stay
correct for kernel + bootstrap root and forever-wrong for the full root.

### b) B — UFS read: the transport is already half-built, and the half that is built is the hard half

**Kernel side: done, in our tree.** `hakios.blazer_ufs.c` (xnu-8796.121.2, blazer branch):
read-only takeover of a **live** UFS host — single-slot legacy request list, polled TEST UNIT READY and
READ(10) only (no WRITE/UNMAP/query/NOP ever constructed), MCQ refused, outstanding-doorbell refused,
36-bit DMA reach enforced, 1 MiB bounce + 4×256 KB PRDT chunks, GPT walk by `blazer_ufs_root=` partition
name (no default — "guessing userdata is not a safe bring-up default"), publishes `/dev/ufsroot0`;
`IOKitBSDInit.cpp:833` maps `rd=ufsroot0` onto the BSD root. Boot contract: `blazer_ufs=2
blazer_ufs_live=1 blazer_ufs_base=0x3c400000 blazer_ufs_root=<NAME> blazer_ufs_lun=0`. Mode 1
(`blazer_ufs=1`) is a zero-side-effect state-register whitelist probe.

**The gap the driver names itself**: it is *not* a cold driver — "ABL shuts this controller down before
starting Linux. The first stage must run Google's UFS, UniPro, M-PHY, CPM and pin/power setup and then
attest a live handoff with `blazer_ufs_live=1`." That first stage is us: the shim. And the pattern for
cold re-init after an ABL shutdown is **exactly** felix's `ufsinit.c` (gs101, HCI `0x14700000`): UIC
engine (op 0x02 DME_SET/0x10 DME_GET via HCI+0x90/94/98/9C, UICCMDR bit 0x400 of IS+0x20), UniPro/PMA
attribute tables, dme-pa-locked quirks, then `ufs_scsi_read_once` (UPIU cmd 0x01/flags 0x40, READ(16)
0x88 >4 GB else READ(10) 0x28, UTRD dw0 CT=1|DD=2|I=1, UCD at +0x1000, 4 KB PRDT chain, doorbell
UTRLDBR +0x58, timeout-clear UTRLCLR +0x5c, IS ack +0x20, success = OCS 0 + resp 0x21 + status 0, 3×
retry + REQUEST_SENSE on 0x02). That isfelix-proven *shape*; every constant is G2 and must be
re-derived from the laguna DT (§7) — same discipline as CPM-FIRST-SPEC ("DERIVE-FROM-LAGUNA-DT").

**Why not felix's storage-core (RDWBLOCK)?** felix parks a secondary core (PSCI_CPU_ON) polling a
shared-memory block descriptor because *their* kernel's `redwood_ufs` memory device only speaks that
protocol. Ours talks to the HCI directly from XNU. The paravirt core would be strictly added complexity
(an always-spinning core, cross-core cache discipline for every block) for zero capability. **Rejected.**

**Staging.** The driver reads a **GPT partition** (LUN 0). Staging = get 2,492,203,008 B of
`out/system.apfs` onto a ≥2.5 GB partition once:
1. *Preferred:* find an existing unused ≥2.5 GB partition (device probe: `adb ls -l /dev/block/by-name/`
   + `cat /proc/partitions`, or `fastboot getvar partition-size:<name>`). If one exists, write it once
   from Android-alive (`adb push` → `su -c dd`) — a **device write**, so it is a coordinator-approved,
   one-time, logged operation with before/after sha256, in the same contract class as force-fastboot
   arming (run-attempt.sh §Device-write contract).
2. *Fallback:* wipe-and-reuse `userdata` itself (felix's exact move — their loader computes
   `userdata_start_lba` and flashes the rootfs there). Destructive to Android data; only if (1) finds
   nothing.
3. *Discouraged:* file-on-userdata + FIEMAP extents + `blazer_ufs_lba=` override. f2fs GC can relocate
   extents between staging and boot; silent wrong-bytes risk. Not worth it while (1) is plausible.

adb push throughput over this link (HS, ADB) ≈ 20–35 MB/s ⇒ **≈1.5–2.5 min, one time ever**. Per boot:
**zero transport cost** — XNU reads lazily; a boot touches maybe 0.2–0.5 GiB of the volume, spread
through mount/dyld/cascade at single-slot polled UFS speeds (~10⁻²–10⁻¹ s per MB class). No 2.32 GiB
contiguous DRAM reservation, no ADT RAMDisk juggling for the big image.

**Risks** (all device-validate, none fatal): (i) cold bring-up depth unknown — ABL *did* run UFS PHY
calibration in its own boot (abl.log 22.77s), and the park→`fastboot boot` handoff may leave PHY/link
alive or not; the shim probe ladder below is built to *discover* the needed depth layer by layer.
(ii) Writability: the driver is deliberately RO. The foundation image is state-preseeded (SetupDone,
lsd/mapdb, dslocal baked — felix items m/g/i all in-image), and the writable data volume (`root.apfs
de340c95…`, 4 MiB, `/redwood` as md1) keeps riding the shim payload as a RAMDisk — so boot-to-cascade
should not need UFS writes. SpringBoard runtime writes are the first thing to watch; the write path
(felix `ufs_scsi_write` shape: UPIU flags 0x20, WRITE(10)/(16), same UTRLDBR/UTRLCLR discipline) is the
later project, *after* M3 unblocks. (iii) SMMU: UFS node is `iommu-idmapped` + `iommu-force-cacheable` +
`dma-coherent` (§7) — PA DMA, and the shim's existing SMMU treatment for DWC3's stream 0x8100 (same
stream id on UFS) carries over; verify UFS DMA works in bypass/identity before trusting descriptors.

**Dependencies:** (1) staging decision + one write approval (coordinator); (2) A-DT decode/confirm of
lpcm-0x06 clock/reset ids + the two sswrp-0x18 power domains (already extracted, §7); (3) for mode 2,
the payload kernel must be the **blazer branch** (B4 Gap 1) — mode-1 probing works with today's 141
kernel because the evidence comes out the shim's own EP0 console.

### c) C — C5's root-v7 (≤32 MiB): valuable, but its ceiling is M2 — by construction

Per B4's ladder, every rung past M2 needs the **native dyld cache path** (`ignition_level=1`,
cryptex cache hard-linked into `/System/Library/Caches/com.apple.dyld`, **no split dylibs**). The cache
alone is ~2.5 GB. The only smaller alternative ever built — the 36-dylib carved closure (92 MB raw,
with the 83.5 MB `__OBJC_RO` problem) — is a **precedent-named dead end** (DECODE.md §7: dyld namespace
6 `Symbol not found: _NDR_record`). There is no ≤32 MiB content that completes dyld. **Answer to "what
minimum closure boots launchd→SpringBoard": only the full foundation image does**, and it is 2.32 GiB —
which is precisely why C exists (bootstrap only) and B exists (the real content).

Position relative to C5: **v7 rides the payload in v3.7 as a drop-in** (v6 stays until v7 passes host
`hdiutil` mount + one QEMU boot — C5's bisect harness is the right gate), with the payload-fit budget:
**≤ 32 MiB = 33,554,432 B** ⇒ payload ≈ 48,667,536 B (46.4 MiB), ~5.9 MB under the ≈52 MB cap. Absolute
root ceiling for the shim path is ≈37 MB (size-fit §1) — do not exceed 32 MiB so the token budget and
any future md1 addition (root.apfs 4 MiB) still fit. v7 keeps the v6 contract byte-class intact: one
case-sensitive journaled HFS+ volume, `H+`/`HX` @1024, volname iOSRoot, launchd+dyld+closure+launchd.plist,
the injection set, var skeleton. v7's role after B lands: **recovery boot volume** (`rd=md0` boot-arg
flip) and the kernel-alive debugging platform — it keeps earning its 32 MiB forever.

### d) D — split: yes, but as "two roots, one boot-arg", not as a pivot

XNU has no pivot_root; root selection happens once, in IOFindBSDRoot, from `rd=`. Since XNU's own UFS
stack (mode 2) is up *before* launchd, there is no "later pivot" to design — the split degenerates into:
**md0 = v6/v7 HFS bootstrap RAMDisk (always embedded, recovery/debug) + ufsroot0 = full UFS APFS (real
root), chosen by `rd=` at boot**. Default boot: `rd=ufsroot0`. Recovery boot: `rd=md0` (today's proven
path, unchanged, for when UFS or the blazer kernel misbehaves). The writable `/redwood` data volume
rides as md1 RAMDisk either way. **This is D's end-state with zero new kernel machinery**, delivered
incrementally by B's phases. The felix storage-core interpretation of D was considered and rejected
(§2b). 

---

## 3. Recommendation

**B, landing as D's end-state, in three shim versions + one later project:**

- **v3.7 — root-v7 rides the payload (no transport code).** Land C5's ≤32 MiB v7 (host-mount + QEMU
  gates), keep CPM-first/EP0 exactly as v36 proved them, add payload-identity stamps (root sha
  prefix + size in the TSHIMST2 stream so every future dump self-identifies its root). Rung: M0–M2 on a
  cleaner bootstrap; the attempt loop keeps running whatever B4/M2 need.
- **v3.8 — shim UFS probe, read-only, fail-closed (§4 spec).** After CPM-first S1–S7 (CPM is shared:
  UFS power rides the same `mboxes = <0x29>`), probe the UFS host with **zero-init first**: safe
  register reads (CAP/VER/HCS/IS — the mode-1 whitelist), then single-slot TUR, READ CAPACITY(10), GPT
  header + partition-name walk. Every step: stamp + EP0 print + bounded wait + bail→park (the
  CPM-first posture, one attempt per boot). Evidence decides the bring-up depth: if the host answers
  after the ABL handoff with no init, v3.8 is nearly free; where it doesn't, layer felix's cold
  sequence (clocks → PD verify → phy_rst deassert → UniPro/PMA → DME → link start) one
  DEVICE-VALIDATE step at a time, each behind its own stamp. Cmdline gains **`blazer_ufs=1`** (probe
  mode) once the blazer kernel rides the payload; with the 141 kernel the shim-side prints are the
  evidence. Rung: "UFS live at handoff" is *learned*, not assumed.
- **v3.9 — full root from UFS (the M3 unlock).** Payload kernel = blazer branch (B4 Gap 1, M2/kernel
  lane delivery through this shim), cmdline: `blazer_ufs=2 blazer_ufs_live=1 blazer_ufs_base=0x3c400000
  blazer_ufs_root=<STAGING> blazer_ufs_lun=0 rd=ufsroot0`, v6/v7 still embedded as md0 recovery volume,
  `root.apfs` as md1 (`/redwood`) RAMDisk, `rootrw=1` applying to RAMDisk volumes only. First device
  goals in order: `/dev/ufsroot0` appears → APFS mounts → **dyld completes (M3)** → cascade. The
  renderer/cascade boot-args (Gap 4) land with this kernel swap per USERLAND-PATH §4.
- **Later — UFS ro→rw** (felix `ufs_scsi_write` shape, single-slot, after M6 doesn't need it), and
  fallbacks that are **not** the plan: A1 bulk-EP push (only if UFS bring-up proves dead on G5),
  B′ kexec-segment residency (only if APFS-over-ufsroot0 hits a wall; requires Android alive every
  session, which fights the force-fastboot discipline).

**Why B over A1 (the question the mission asks explicitly):** a shim bulk endpoint would *also* work
(~41 MB/s proven on this link by ABL's own stack) — but it moves 2.32 GiB **every session** vs B's
one-time 2-minute adb push; it needs a custom host tool and a new unproven shim endpoint/ DMA surface
while the shim is finally stable; and it still ends in a 2.32 GiB contiguous RAM reservation + ADT
RAMDisk for the big image. B reuses a **felix-proven SCSI shape**, a **kernel driver that already
exists in our tree**, and the **CPM mailbox the shim just proved**. A1 is the fallback, not the plan.

---

## 4. Concrete spec — shim UFS probe (v3.8) and handoff (v3.9)

### 4.1 G5 UFS register map (from the authoritative blazer MP DTB — §7 for full provenance)

| Region | PA + size | Use |
|---|---|---|
| `ufs_hc` | `0x3c400000` + 0x1000 | the UFSHCI (CAP 0x00, VER 0x08, IS 0x20, HCS 0x30, HCE 0x34, UTRLBA 0x50/BAU 0x54, UTRLDBR 0x58, UTRLCLR 0x5c, UTRLRSR 0x60, MEM_CFG 0x300) |
| `ufs_top` | `0x3c410000` + 0x504 | Google UFS top CSR |
| `ufs_phy_sram` | `0x3c700000` + 0x14000 | PHY SRAM (patch/cal target; DT carries 72-cell `phy-cal-data`) |
| psm_status_hsios / ufs_hc / ufs_phy | `0x3c00b108` / `0x3c00d210` / `0x3c00e108` (4 B each) | PSM status reads (safe whitelist material) |
| iommu | stream 0x8100, `iommu-idmapped`, `iommu-force-cacheable`, `dma-coherent` | PA DMA; verify identity/bypass at handoff |
| clocks | lpcm **0x06**: clock-id **0x03** (`gpio_ref_clk`) + clock-id **0x10** (`aux_clk`) | CPM LPCM gate calls, same frame shape as USB's lpcm 0x05 |
| resets | lpcm **0x06**, reset controller `reset-num 0x05`, **id 0x03** (`phy_rst`) | CPM SET_RST/DEASSERT |
| power domains | `ufs_prep_pd` + `ufs_pd`, sswrp-id **0x18** family | verify-on (DT) then CPM on/off if needed |
| misc | `mboxes = <0x29>` (CPM MBA iface), `mba-pmic-dest-channel 0x0a` (vcc rails via PMIC), reset/power GPIOs, pinctrl `refclk_on/off` states, `gsa-device` ref | rails/clock-gating only when evidence demands |

### 4.2 Shim probe ladder (insert after CPM-first S7, before payload jump; same discipline)

| Step | Action | Success | On failure |
|---|---|---|---|
| U0 | Preconditions: CPM ready (S7 done), stamps on, EP0 console up | — | unreachable |
| U1 | Safe reads: `CAP 0x00` (UFS version nibble, MCQ bit), `VER 0x08`, `HCS 0x30`, PSM status trio | CAP non-zero / version ≥ 3.1 | stamp `UFS_HCI` + raw values → park (host unclocked ⇒ bring-up depth question for next boot) |
| U2 | Clocks (if U1 dead or as DEVICE-VALIDATE ladder): LPCM 0x06 ids 0x03, 0x10 via `tgl_cpm.c` frame shape | seq-correlated reply == 0 | stamp clock id + response; continue only if a defined step list says so |
| U3 | PD verify sswrp 0x18 pair; `phy_rst` deassert (lpcm 0x06 id 0x03) only if evidence demands | replies == 0 | stamp + park |
| U4 | Link state: `HCS.DEVDP`/UTRLRDY reads; **no HCE toggle, no doorbell writes yet** | host present | decide: felix cold init (next rotation's spec) vs park |
| U5 | Single-slot TUR: felix `ufs_scsi_read_once`-shape slot 1, LUN 0, 1 s timeout | OCS 0 + resp 0x21 + status 0 | stamp uic/ocs/resp bytes → park |
| U6 | READ CAPACITY(10) 0x25 (8 B) | sane `last_lba`/`block_size` (4 KB class, tens of GB) | stamp → park |
| U7 | GPT: read LBA1 (+entries) | `blazer_ufs_root=` target found; log name/LBA-range | stamp "GPT no match" + names seen → park |
| U8 | **Leave live**: no HCE=0, no UTRLCLR unless clearing *our* timeout, descriptors left in a defined DMA slab (reuse the shim's existing DMA arena; document PA in stamps) | — | — |
| U9 | EP0 console prints the whole trail (`UFS: CAP=… HCS=… TUR ok CAP: lba=… GPT 'staging' @…`), stamps `TGL_STAGE_UFS_*` ids continue the v3.6 table, new header reason `ufs-never-came-up` | — | — |

One attempt per boot; bounded (deadline + iteration) waits; `dsb sy` before doorbell/list publish
(felix :481-486 discipline, CPM-FIRST-SPEC §4 verbatim). The probe is **side-effect-light by design**:
U1/U4/U5/U6/U7 write nothing outside our own DMA slab except the single doorbell ring per SCSI command
— that is the minimum the host accepts commands at all, and it is exactly what XNU mode 1/2 will do
again after us, so a failed probe leaves the host no dirtier than a successful one.

### 4.3 Handoff contract (v3.9)

- Cmdline (extending today's template): keep every existing token; add
  `blazer_ufs=2 blazer_ufs_live=1 blazer_ufs_base=0x3c400000 blazer_ufs_root=<STAGING-NAME>
  blazer_ufs_lun=0 rd=ufsroot0`. Drop `rd=md0` from the default boot (md0 stays embedded; `rd=md0`
  remains the recovery boot profile). `rootrw=1` keeps applying to the RAMDisk data volume (md1
  `/redwood`); the UFS system volume is RO by driver design — do not pass tokens promising otherwise.
- ADT: RAMDisk = v6/v7 HFS (as today), RAMDisk1 = root.apfs 4 MiB (`/redwood`). The 2.32 GiB image
  carries **no ADT entry at all** — it is a block device now.
- Staging (one-time, coordinator-approved device write): target partition ≥ 2.5 GB; content =
  `out/system.apfs` (sha256 `637103e1…` prefix recorded in meta + stamps); write from Android-alive
  (`adb push` + `su -c dd` + `blockdev --flush`), verify with a readback sha256; log in ATTEMPTS.md.
- Failure posture: any mode-2 refusal in XNU prints via ramoops/mirror (EP0 is long gone by then) —
  the recovery boot is `rd=md0` (v6/v7), unchanged from today's proven path.

### 4.4 Open items (owners)

| Item | Owner | Note |
|---|---|---|
| Staging partition pick (probe by-name/partitions) | device lane, coordinator sign-off on the one write | §2b(1) |
| A-DT: confirm lpcm-0x06 clock/reset/PD ids + whether UFS needs *any* CPM call after ABL handoff | A-DT | values in §7 are extracted, not yet device-proven |
| felix cold-init constant table port (UniPro/PMA/DME for G5) — only if U4/U5 say the host is dead at handoff | C2 next rotation | felix ufsinit.c is the shape; every constant DERIVE-FROM-LAGUNA-DT |
| Blazer kernel delivery through shim (cmdline ext + AMFI/personalities intact) | M2/kernel lane (B4 Gap 1) | v3.9 dependency |
| `blazer_ufs_root=` name freeze + RO-adequacy watch (SpringBoard writes) | B4/userland | md1 RAMDisk is the write surface until ro→rw lands |

---

## 5. G5 UFS DT facts (new section, same discipline as DT-USB-FACTS)

Provenance: host `fdtget`/`dtc -I dtb -O dts` over `research/laguna-foundation/kernel-blazer/devices/
blazer/fdt/laguna.dtb` (blazer MP, 896,033 B) — no downloads, no device. Aliases: `ufs0 = "/ufs@3c400000"`.

- Node `ufs@3c400000`, `compatible = "google,ufshc"`; regs named `ufs_hc` / `ufs_top` / `ufs_phy_sram` /
  `psm_status_hsios` / `psm_status_ufs_hc` / `psm_status_ufs_phy` (PAs in §4.1).
- `iommus = <… 0x8100>` — **same stream id as DWC3** — plus `iommu-idmapped`, `iommu-force-cacheable`,
  `dma-coherent`: plain-PA DMA is the DT-sanctioned mode (matches felix's MMU-off posture).
- Clocks `gpio_ref_clk` = lpcm **0x06** clock-id **0x03**; `aux_clk` = lpcm **0x06** clock-id **0x10**
  (decoded from phandles 0x49f/0x4a0). USB lives on lpcm 0x05 (HSION); UFS lives on lpcm 0x06 (LSIO) —
  same `google,mba-ctrl` CPM transport (phandle 0x29) as the USB clocks.
- Resets: controller phandle 0x4a1, lpcm **0x06**, `reset-num 0x05` (ids 0x00–0x04); node consumes
  **id 0x03** named `phy_rst`.
- Power domains: `ufs_prep_pd` (phandle 0x4a6) + `ufs_pd` (phandle 0x43d), sswrp-id **0x18** family.
- `phy-cal-data` = 72 cells of M-PHY calibration in-DT, with `google,enable-phy-calibration` +
  `google,enable-phy-patching` (mode 2) — but ABL already ran calibration in its boot (abl.log
  `[UFS] performing phy calibration` @ 22.77 s); the shim probe ladder is built to learn whether any of
  it must be redone after the handoff.
- Supplies/rails: `vdd0p75/vdd1p2/vcc/vccq` regulators, `mba-pmic-dest-channel 0x0a`, reset/power GPIOs,
  pinctrl `refclk_on`/`refclk_off` states, `gsa-device` reference. Treat all as **verify-not-toggle**
  unless the probe ladder proves a gap.
- The kernel driver's defaults already agree: `blazer_ufs_base=` default **0x3c400000** "from the live
  FDT"; `UPIU_IID_ONE` matches "Google Laguna sets the header IID to one".

## Summary (5 lines)

1. The full root (2.32 GiB, native cryptex dyld cache) cannot ride any ≤64 MiB `fastboot boot` payload
   (36× over; even the raw payload cap is ≈52 MB) — and EP0 control transfers (~128–320 KB/s, ≤64 B
   stages, hours per session) are a console channel, never a payload channel.
2. Recommendation: **UFS** — stage `out/system.apfs` once on a ≥2.5 GB GPT partition (adb push ≈ 2 min,
   one coordinator-approved write), shim cold-brings/probes the host after CPM-first and leaves it live
   (felix-proven SCSI shape, one-attempt-per-boot, fail-closed), and XNU mounts it with the **already
   in-tree** `hakios.blazer_ufs.c` mode 2 via `rd=ufsroot0` — no resident copy, no pivot machinery, md0
   (v6/v7) stays embedded as the recovery volume (option D's end-state via option B).
3. A shim bulk endpoint (A1, ~41 MB/s proven by ABL's own fastboot on this link) is a *fallback only*:
   it moves 2.32 GiB every session into a 2.32 GiB RAM reservation vs B's one-time push and lazy reads;
   kexec-segment residency (B′) needs Android alive every session and fights the force-fastboot
   discipline; felix's storage-core paravirt is unneeded because our kernel talks to the HCI directly.
4. Phasing: **v3.7** = C5's root-v7 (≤32 MiB hard; ceiling is M2 by construction — only the full image
   passes dyld) rides the payload with root-identity stamps; **v3.8** = read-only shim UFS probe ladder
   U0–U9 (safe HCI reads → TUR → READ CAPACITY → GPT walk, stamps + EP0 prints, `blazer_ufs=1` once the
   blazer kernel rides); **v3.9** = blazer kernel + `blazer_ufs=2 blazer_ufs_live=1 blazer_ufs_root=<STAGING>
   rd=ufsroot0`, md0 recovery + md1 `/redwood` RAMDisk — that is the M3 unlock.
5. New DT facts recorded (§5): laguna UFS = `google,ufshc` @0x3c400000 (+top 0x3c410000, phy_sram
   0x3c700000, PSM trio), stream 0x8100 idmapped/coherent, clocks lpcm-0x06 ids 0x03/0x10, phy_rst =
   lpcm-0x06 id 0x03, PDs sswrp-0x18 pair, CPM mailbox 0x29, ABL pre-calibrated PHY — all
   DEVICE-VALIDATE through the U0–U9 ladder; staging-partition pick + blazer-kernel delivery are the
   two external dependencies.
