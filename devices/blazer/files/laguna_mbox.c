/*
 * laguna_mbox.c — CPM mailbox transport for the laguna port (Tensor G5 / Pixel 10 Pro XL).
 *
 * XNU-side adaptation of Google's goog-mba Linux mailbox driver (Douglas Anderson,
 * "[PATCH v2 0/4] mailbox: Improve the mbox core then introduce the goog-mba driver",
 * Message-ID 20261023235408.3063350-1-dianders@chromium.org, posted 2026-10-23; the author
 * reports testing on Pixel 10 Pro XL/Mustang with WIP Laguna CPM drivers). Upstream source
 * and its ARM64 build validation live next to this file in upstream/ (see README.md there).
 *
 * WHAT THIS DRIVES: the CPM (Central Power Manager) mailbox. On our device the *stock, saved*
 * FDT (hardware/bootchain-20261022/live-fdt.dtb, inventoried with SHA-256 in
 * upstream/stock-dt-mailboxes.json) exposes:
 *
 *   /cpm_mba_syscon@5250000                google,cpm-mba-global (syscon, 0x50)
 *   /cpm_ap_ns_req_mba_client_0@5260000    google,mba-ctrl  — our TX (queue mode)
 *   /cpm_ap_ns_resp_mba_client_1@5261000   google,mba-ctrl  — our RX (queue mode)
 *   /goog_mba_cpm_iface                    requests 8 TX slots + 4 RX slots through the old
 *                                          indexed API; the stock DT payload size is 4 words
 *
 * The ABL log lines "MBOX: gsa-mba: num of mailboxes 8" and "MBOX: cpm-mba: num of mailboxes 32"
 * (research/abl5.log:26,83) are the *stock Android kernel's* driver instance counts for the GSA
 * and CPM families — they are not addresses. The goog-mba family covers CPM, AOC, GSA, GDMC and
 * the Aurora cluster mailboxes under one binding (google,lga-mailbox-array); this file wires the
 * CPM pair only, because that is what our display path needs first.
 *
 * WHAT XNU NEEDS IT FOR: CPM is the power-domain manager for the DPU. Our dc9x00 display path
 * (research/laguna-iomfb/, research/laguna-141-bridge patches 0101-0103) can only probe real DPU
 * MMIO once DPU top/subdomain power is requested over this mailbox; the wire protocol above the
 * transport is already extracted transport-independent in upstream/cpm_power_protocol.{c,h}
 * (DPU logical DT ID 5 -> LPB wire ID 5 / LPCM wire ID 0; hcp_begin_dpu / hcp_ack / hcp_lpb_event).
 * This file is the queue transport those functions assume underneath.
 *
 * CONTRACTS taken from goog-mba.c (read-from-source, upstream/0004.patch):
 *   client-relative register offsets (XNU maps the client subnode only; the host-private
 *   register region is never mapped or written):
 *     0x00          doorbell: write 1 after payload writes
 *     0x04          IRQ config: startup enables bit8 automatic host ACK and bits16/24
 *                   message/ACK masks
 *     0x08          IRQ status: W1C message bit0 and ACK bit8
 *     0x10          outstanding TX count (queue mode)
 *     0x34          buffer words on newer IP (no global region needed)
 *     0x100 + 4*n   shared message word n
 *   On older IP the word capacity is read from the global region at 0x10 + 4*index, where
 *   index = (client physical address & 0x1ffff) / 0x1000.
 *   Queue mode: the buffer is a circular queue of tx/rx_payload_words entries; TX writes the
 *   payload at tx_idx, advances, wraps, rings the doorbell; RX copies words out BEFORE
 *   acknowledging (ACK is W1C of the message bit); TX clears ACK status before reading the
 *   outstanding count. Queue initialization: the client sends its 0x100 initialization
 *   message with init semantics — the driver writes the supplied words at the current TX
 *   index, then resets the NEXT TX index to zero, tracks the outstanding message and rings
 *   the doorbell. The transport itself does not know 0x100; the protocol layer does.
 *
 * BRING-UP SAFETY (our patch-series rules; cf. pixos AGENTS.md rule 7 and the mba-upstream
 * README's "not authorization to access MMIO"):
 *   - everything is gated behind boot-arg laguna_mbox=1 (default off); with the arg absent the
 *     driver maps nothing and costs one PE_parse_boot_argn call;
 *   - polled only at first (no SPI250/251 wiring, no interrupt setup — mirrors our laguna
 *     patch style where peripherals start polled);
 *   - the first hardware goal is a read-only register dump (buffer words, IRQ config), not a
 *     transmit; transmit and the 0x100 init are second-stage and separately gated.
 *
 * XNU integration sketch (when promoted from snippet to patch):
 *   - add to xnu/files/bsd/dev/ + files.arm64 under the LAGUNA board config;
 *   - call laguna_mbox_init() from the dc9x00 display probe path (or laguna_early), never from
 *     a context that panics on failure: every failure here must degrade to "no mailbox";
 *   - the cpm_power_protocol.c state machine (rename hcp_* -> laguna_cpm_*) sits on top:
 *     hcp_request_header() builds the header word, this file carries the 4 words.
 */

#include <pexpert/pexpert.h>            /* PE_parse_boot_argn */
#include <machine/machine_routines.h>   /* (only if IRQ path is added later) */

/* --- client-relative register contract (goog-mba.c) --------------------------------------- */

#define MBOX_DOORBELL          0x00    /* trigger host doorbell with value 1 */
#define MBOX_IRQ_CFG           0x04    /* bit8 auto host ACK; bits16/24 msg/ACK masks */
#define MBOX_IRQ_STATUS        0x08    /* W1C: bit0 message, bit8 ACK */
#define MBOX_OUTSTANDING       0x10    /* outstanding TX count in queue mode */
#define MBOX_BUFFER_WORDS      0x34    /* buffer words on newer IP */
#define MBOX_MSG(i)            (0x100 + (i) * 4)

#define IRQ_MSG_INT            0x001   /* a message waits in the RX queue */
#define IRQ_ACK_INT            0x100   /* the host acknowledged our TX */

/* The CPM pair, from the saved live FDT (stock-dt-mailboxes.json; device-verified source). */
#define CPM_GLOBAL_PA          0x05250000ULL   /* google,cpm-mba-global, 0x50 */
#define CPM_TX_PA              0x05260000ULL   /* cpm_ap_ns_req_mba_client_0, 0x1000 */
#define CPM_RX_PA              0x05261000ULL   /* cpm_ap_ns_resp_mba_client_1, 0x1000 */

/* Payload size of the stock interface (stock DT: 4 words each way; CPM supplies its own
 * queue-protocol header word as the first of the four). */
#define PAYLOAD_WORDS          4

typedef unsigned long vm_offset_t;

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

static volatile unsigned int *tx_base, *rx_base, *gbl_base;
static unsigned int tx_words, rx_words;        /* capacity of each ring, in words */
static unsigned int tx_idx, rx_idx;            /* ring cursors (word offsets) */
static unsigned int tx_outstanding;            /* messages written, not yet acked */
static int mbox_up;                            /* 0 until init succeeded */
static int mbox_tx_armed;                      /* second-stage gate: writes allowed */

static inline unsigned int
rd(volatile unsigned int *base, unsigned int off)
{
	return base[off / 4];
}

static inline void
wr(volatile unsigned int *base, unsigned int off, unsigned int val)
{
	base[off / 4] = val;
}

/*
 * Ring capacity. Newer IP answers at 0x34; older IP wants the global region:
 * words = global[0x10 + 4*index], index = (pa & 0x1ffff) / 0x1000. Read the direct register
 * first; only map the global syscon if the direct answer is not plausible (0 or > 256).
 */
static unsigned int
buffer_words(volatile unsigned int *base, vm_offset_t pa)
{
	unsigned int w = rd(base, MBOX_BUFFER_WORDS);

	if (w != 0 && w <= 256) {
		return w;
	}
	if (gbl_base == 0) {
		gbl_base = (volatile unsigned int *)ml_io_map(CPM_GLOBAL_PA, 0x50);
	}
	if (gbl_base == 0) {
		return 0;
	}
	{
		unsigned int index = ((unsigned int)pa & 0x1ffff) / 0x1000;
		w = rd(gbl_base, 0x10 + 4 * index);
	}
	return (w != 0 && w <= 256) ? w : 0;
}

/*
 * Stage 1 (read-only bring-up): map, size the rings, program nothing, print a dump.
 * Gated by laguna_mbox=1; never panics.
 */
void
laguna_mbox_init(void)
{
	unsigned int on = 0;

	if (!PE_parse_boot_argn("laguna_mbox", &on, sizeof(on)) || on == 0) {
		return;
	}
	tx_base = (volatile unsigned int *)ml_io_map(CPM_TX_PA, 0x1000);
	rx_base = (volatile unsigned int *)ml_io_map(CPM_RX_PA, 0x1000);
	if (tx_base == 0 || rx_base == 0) {
		printf("laguna_mbox: mapping failed\n");
		return;
	}
	tx_words = buffer_words(tx_base, CPM_TX_PA);
	rx_words = buffer_words(rx_base, CPM_RX_PA);
	mbox_up = tx_words >= PAYLOAD_WORDS && rx_words >= PAYLOAD_WORDS;
	printf("laguna_mbox: tx@%llx %u words, rx@%llx %u words%s (irq_cfg %08x/%08x)\n",
	    CPM_TX_PA, tx_words, CPM_RX_PA, rx_words, mbox_up ? "" : " — unusable",
	    rd(tx_base, MBOX_IRQ_CFG), rd(rx_base, MBOX_IRQ_CFG));
}

/*
 * Stage 2: program the queue (IRQ config with automatic host ACK), then send CPM's
 * 0x100 queue-initialization message with the driver's init semantics: words written at
 * the current TX index, then the NEXT TX index resets to zero, the message counts as
 * outstanding, doorbell rung. `words` is the full 4-word frame INCLUDING the protocol
 * header (hcp_request_header() builds it). Return 0 on success.
 *
 * Requires laguna_mbox_tx=1 (separate gate — no hardware writes until stage 1 was read
 * back sane and a human saw the dump).
 */
int
laguna_mbox_tx_init(const unsigned int *words)
{
	unsigned int on = 0;

	if (!mbox_up) {
		return -1;
	}
	if (!PE_parse_boot_argn("laguna_mbox_tx", &on, sizeof(on)) || on == 0) {
		return -1;
	}
	/* startup: automatic host ACK (bit8) + message/ACK masks (bits16/24) per goog-mba */
	wr(tx_base, MBOX_IRQ_CFG, (1u << 8) | (1u << 16) | (1u << 24));

	for (unsigned int i = 0; i < PAYLOAD_WORDS; i++) {
		wr(tx_base, MBOX_MSG(tx_idx + i), words[i]);
	}
	tx_idx = 0;                             /* init resets the next TX index to zero */
	tx_outstanding = 1;
	wr(tx_base, MBOX_DOORBELL, 1);
	mbox_tx_armed = 1;
	printf("laguna_mbox: queue init sent (%08x %08x %08x %08x)\n",
	    words[0], words[1], words[2], words[3]);
	return 0;
}

/* Normal TX (after a successful init). Words stay synchronously written: the caller's
 * buffer may be freed once this returns (unlike Linux's queued channels). */
int
laguna_mbox_tx(const unsigned int *words)
{
	if (!mbox_tx_armed) {
		return -1;
	}
	/* clear a stale ACK status before reading the outstanding count (goog-mba order) */
	wr(tx_base, MBOX_IRQ_STATUS, IRQ_ACK_INT);
	if (rd(tx_base, MBOX_OUTSTANDING) >= tx_words / PAYLOAD_WORDS) {
		return -1;                      /* ring full — caller retries later */
	}
	for (unsigned int i = 0; i < PAYLOAD_WORDS; i++) {
		wr(tx_base, MBOX_MSG(tx_idx + i), words[i]);
	}
	tx_idx += PAYLOAD_WORDS;
	if (tx_idx >= tx_words) {
		tx_idx = 0;
	}
	tx_outstanding++;
	wr(tx_base, MBOX_DOORBELL, 1);
	return 0;
}

/*
 * Polled RX drain: copies every complete message out of the ring BEFORE acknowledging.
 * Returns the number of messages drained; each `out` receives PAYLOAD_WORDS words.
 * The copy-before-ACK order is a source contract — the ACK releases the host to refill
 * the slot we would still be reading.
 */
unsigned int
laguna_mbox_rx_drain(unsigned int *out, unsigned int max_msgs)
{
	unsigned int n = 0;

	if (!mbox_tx_armed || rx_words < PAYLOAD_WORDS) {
		return 0;
	}
	while (n < max_msgs && (rd(rx_base, MBOX_IRQ_STATUS) & IRQ_MSG_INT)) {
		for (unsigned int i = 0; i < PAYLOAD_WORDS; i++) {
			out[n * PAYLOAD_WORDS + i] = rd(rx_base, MBOX_MSG(rx_idx + i));
		}
		rx_idx += PAYLOAD_WORDS;
		if (rx_idx >= rx_words) {
			rx_idx = 0;
		}
		wr(rx_base, MBOX_IRQ_STATUS, IRQ_MSG_INT);      /* W1C acknowledge */
		n++;
	}
	return n;
}

/*
 * What NOT to do here (all read-from-source contracts, kept as guardrails):
 *  - never map or write the host-private register region (CPM is the host; we are a client);
 *  - never treat the doorbell-only or shared-buffer shapes as this hardware's (queue mode only);
 *  - never send a power request before the 0x100 init handshake completed (sequence 1..13
 *    and token allocation live in the protocol layer, not here);
 *  - an RX LPB event carries no transaction ID — the protocol layer must reject stale
 *    same-ID events; a successful ACK alone does not prove DPU power state.
 */
