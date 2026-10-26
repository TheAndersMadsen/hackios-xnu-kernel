# Mailbox reuse audit — goog-mba → laguna_mbox

Audited 2026-10-24. Read-only harvest; no device access, no tree builds.

## 1. What the code is and where it came from

**Source (primary):** Douglas Anderson (Google/Chromium), mailing-list series
`[PATCH v2 0/4] mailbox: Improve the mbox core then introduce the goog-mba driver`,
Message-ID `20261023235408.3063350-1-dianders@chromium.org`, posted **2026-10-23** (v2 —
newest revision found in public search). The author reports testing on
**Pixel 10 Pro XL (Mustang / Tensor G5)** together with work-in-progress Laguna CPM,
pinctrl, PCIe and RTC drivers — that is the author's hardware result.

**Local validation already done by the earlier fleet lane**
(`research/native-20261024/display/mba-upstream/`, verified 2026-10-24T15:58Z):

- all four normalized patches pass `git apply --check` against linux-next
  `bd1ecbb61afefc6a8b876ccbc31e9228b44e9eab` (driver NOT yet merged upstream —
  `torvalds/linux` commits query for `drivers/mailbox/goog-mba.c` returned empty);
- normalized added files match the published Git blob SHA-1 prefixes
  (`f265993fe71d`, `79ddf32ef5ba`, `02be79757fcb`, `b7ed27912503`, `5c3d47e3ded3`);
- driver + both core prerequisite patches compile **warning-free as ELF64 AArch64
  objects** (clang 18, kbuild:v2 image, `kernel-build/out-vm` config):
  `build/goog-mba.o` (195,304 B, sha256 `70bf161f…`), `build/mailbox-core.o`
  (196,968 B, sha256 `7e0b5e6c…`). Objects were not linked/booted.

**Copied into this directory** (`upstream/`): `goog-mba.c` (621 lines),
`goog-mba-priv.h` (130), `goog-mba-message.h` (38), normalized patches
0001 (zero-cell xlate), 0002 (fwnode matching), 0004 (driver), the DT binding
`google,lga-mailbox-array.yaml` (226 lines), `stock-dt-mailboxes.json`
(SHA-256-pinned inventory of OUR device's saved live FDT mailbox nodes),
`manifest.json` (full hashes/Message-IDs), and `cpm_power_protocol.{c,h}`
(the transport-independent CPM wire-protocol extraction — the layer above the mailbox).

## 2. Which mailbox is which (answers the ABL-log question)

Our ABL logs (`research/abl5.log:26,83`, `abl6.log`) print:

```
MBOX: gsa-mba: num of mailboxes 8
MBOX: cpm-mba: num of mailboxes 32        (printed twice — two probe passes)
```

Those lines are the **stock Android kernel's** `gsa-mba` / `cpm-mba` vendor driver
instance counts, not addresses. The device's actual saved FDT
(`hardware/bootchain-20261022/live-fdt.dtb`, inventoried in
`upstream/stock-dt-mailboxes.json`, dtb sha256 `d7c27fdc…`) shows the family:

| family | global syscon | AP clients (google,mba-ctrl) |
|---|---|---|
| **CPM** ← goog-mba series targets this | `cpm_mba_syscon@5250000` (0x50) | `cpm_ap_ns_req_mba_client_0@5260000` (TX), `cpm_ap_ns_resp_mba_client_1@5261000` (RX); `goog_mba_cpm_iface` requests **8 TX slots + 4 RX slots**, payload **4 words** |
| AOC | `aoc_mba_syscon@b490000` | `aoc_ap_0..19_mba_client_*@b4a0000..b4b3000` (20 clients) |
| GSA | `gsa_mba_syscon@e000000` | `gsa_ap_0_mba_client_0@e010000` (1 client) |
| GDMC | `gdmc_mba_syscon@6250000` | non-secure/critical/ap1-3 req+resp ×9 |
| Aurora/SWRP | per-cluster syscons 0x38430000… | sswrp + mcu clients |

DMA pool: `/reserved-memory/goog_mba_gdmc_iface_dma_region@8b420000` (16 KiB, no-map).

**The CPM pair at 0x05260000 / 0x05261000 is device-verified** — it is read out of
our own phone's merged FDT, not guessed. The register *semantics* are read-from-source
(goog-mba.c). Hardware I/O remains unvalidated (transport availability, firmware
ownership, power/protection).

## 3. What XNU needs it for

CPM is the DPU's power manager. The wire protocol above the transport is already
extracted in `upstream/cpm_power_protocol.{c,h}`: `hcp_begin_dpu()` builds the request
(DPU logical DT ID 5 → LPB wire ID 5 / LPCM wire ID 0), `hcp_ack()` / `hcp_lpb_event()`
correlate replies, `hcp_request_header()` builds the header word the transport carries.
What was missing is the transport — this deliverable (`laguna_mbox.c`) fills exactly that
slot so `hcp_*` can run. Without it, our dc9x00 display path
(`research/laguna-iomfb/0001`, bridge patches 0101–0103) stays QEMU/ramfb-only: any real
DPU MMIO attempt aborts while the DPU is unpowered ("Unclassified kernel abort" class).

GSA (the other ABL-log mailbox) is the security-agent channel — later concern
(encrypted storage / key requests), same driver family, same register contract.

## 4. Drop-in path for the laguna XNU patches

Target shape: `bsd/dev/laguna_mbox.c` + `files.arm64` entry under
`ARM64_BOARD_CONFIG_LAGUNA`, called from the dc9x00 probe path. See
`laguna_mbox.c` (this dir) for the adapted skeleton with the full register contract,
init handshake, polled RX drain and the guardrails. Key adaptation decisions:

1. **No Linux mailbox core.** XNU has no `mbox_controller` framework, so the two core
   patches (0001 zero-cell xlate, 0002 fwnode matching) do not apply; instead we hard-wire
   the CPM pair (addresses from the live FDT) and implement queue mode directly.
   The core patches remain the reference for semantics if we later want a generic layer.
2. **One RX channel.** V2 exposes each TX slot as a channel but queued RX is a single
   channel whose callback drains the ring — the XNU port just polls the ring (IRQ via
   SPI250/251 is a later step, mirroring our poll-first patch style).
3. **Payload = 4 words.** CPM supplies its own header word; token/sequence allocation
   stays in the protocol layer (do not push it into the transport).
4. **Init handshake required before any power request**: send the 0x100 message with
   init semantics (write at current TX idx, reset next TX idx to 0, track outstanding,
   doorbell) — never substitute a power request for the handshake.
5. **Boot-arg gated twice**: `laguna_mbox=1` (maps + read-only dump) and
   `laguna_mbox_tx=1` (programs IRQ config + first transmit). Default off everywhere.

## 5. Effort / risk

- Transport port (this skeleton → compiling patch): **~1 session**. ~200 lines of XNU C,
  no IRQ wiring, no allocator. Biggest unknowns: whether ABL/CPM firmware expects the
  AP to (re)initialize the queue at handoff, and whether the CPM client block is powered
  at ABL handoff (ABL's own log shows its drivers probed it — encouraging).
- Protocol layer adoption (`cpm_power_protocol.c` rename + wire-in): **~0.5 session**,
  host-testable as-is (it is transport-independent C with no I/O).
- First hardware proof (read-only dump over a kexec run): **1 device session**, no flash.
- Risk: writing MMIO while CPM's bus clock is gated hangs the bus (cf. the metal branch's
  `regs_alive()` lesson for the display block — same class of hazard). Keep stage-1
  read-only until a power/clock answer exists.
