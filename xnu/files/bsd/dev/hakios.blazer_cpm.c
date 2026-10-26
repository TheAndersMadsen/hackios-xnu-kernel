/*
 * The Central Power Manager's goog-mba v2 queue transport on Tensor G5.
 *
 * This is a transport, not a power-domain protocol. The CPM request/response
 * client addresses and the four-word payload come from blazer's live FDT:
 *
 *   cpm_ap_ns_req_mba_client_0   0x05260000 (AP -> CPM)
 *   cpm_ap_ns_resp_mba_client_1  0x05261000 (CPM -> AP)
 *   cpm_mba_syscon               0x05250000 (legacy capacity lookup only)
 *
 * Register semantics follow Google's goog-mba v2 driver. There is no Linux
 * mailbox core in XNU, so the small queue part lives here. DPU power messages,
 * sequence/token allocation and reply correlation belong to a later protocol
 * user of this file, not to this transport.
 *
 * Bring-up is deliberately split in two. blazer_cpm=1 maps the two client
 * windows and reads their capacity/IRQ-configuration registers; it writes
 * nothing. blazer_cpm_tx=1 is an additional, explicit authorization for queue
 * initialization and subsequent writes. Both default to zero. A first-stage
 * loader must leave these client blocks powered: as with every MMIO driver,
 * addresses in the DT are not authorization to touch an unpowered block.
 */
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/errno.h>
#include <sys/hakios.blazer_cpm.h>
#include <sys/systm.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

#define CPM_GLOBAL_DEFAULT  0x05250000ULL
#define CPM_REQ_DEFAULT     0x05260000ULL
#define CPM_RESP_DEFAULT    0x05261000ULL
#define CPM_GLOBAL_SIZE     0x50
#define CPM_CLIENT_SIZE     0x1000

#define MBA_DOORBELL        0x00
#define MBA_IRQ_CFG         0x04
#define MBA_IRQ_STATUS      0x08
#define MBA_OUTSTANDING     0x10
#define MBA_BUFFER_WORDS    0x34
#define MBA_MSG(i)          (0x100 + (i) * 4)

#define MBA_IRQ_MESSAGE     (1u << 0)
#define MBA_IRQ_ACK         (1u << 8)
#define MBA_IRQ_AUTO_ACK    (1u << 8)
#define MBA_IRQ_MESSAGE_MASK (1u << 16)
#define MBA_IRQ_ACK_MASK    (1u << 24)

struct cpm_channel {
	volatile uint32_t *regs;
	uint64_t pa;
	uint32_t words;
	uint32_t cursor;
};

static struct cpm_channel cpm_tx, cpm_rx;
static volatile uint32_t *cpm_global;
static bool cpm_mapped, cpm_tx_allowed, cpm_queue_ready;

static inline uint32_t
cpm_read(volatile uint32_t *base, uint32_t off)
{
	return base[off / 4];
}

static inline void
cpm_write(volatile uint32_t *base, uint32_t off, uint32_t value)
{
	__asm__ volatile ("dsb sy" ::: "memory");
	base[off / 4] = value;
	__asm__ volatile ("dsb sy" ::: "memory");
}

static uint32_t
cpm_capacity(struct cpm_channel *channel)
{
	uint32_t words = cpm_read(channel->regs, MBA_BUFFER_WORDS);

	if (words != 0 && words <= 256) {
		return words;
	}
	/* Older IP reports each client's capacity in the global syscon. Do not
	 * map that region unless the direct register did not answer. */
	if (cpm_global == NULL) {
		uint64_t pa = CPM_GLOBAL_DEFAULT;

		PE_parse_boot_argn("blazer_cpm_global", &pa, sizeof(pa));
		cpm_global = (volatile uint32_t *)ml_io_map((vm_offset_t)pa, CPM_GLOBAL_SIZE);
	}
	if (cpm_global == NULL) {
		return 0;
	}
	{
		uint32_t index = (uint32_t)((channel->pa & 0x1ffff) / 0x1000);

		words = cpm_read(cpm_global, 0x10 + 4 * index);
	}
	return words != 0 && words <= 256 ? words : 0;
}

static bool
cpm_shape_valid(const struct cpm_channel *channel)
{
	return channel->words >= HAKIOS_BLAZER_CPM_WORDS &&
	    channel->words % HAKIOS_BLAZER_CPM_WORDS == 0;
}

void
hakios_blazer_cpm_init(void)
{
	uint32_t enabled = 0, tx = 0;
	uint32_t tx_cfg, rx_cfg;

	if (cpm_mapped || !PE_parse_boot_argn("blazer_cpm", &enabled, sizeof(enabled)) || enabled == 0) {
		return;
	}
	PE_parse_boot_argn("blazer_cpm_tx", &tx, sizeof(tx));
	cpm_tx_allowed = tx != 0;
	cpm_tx.pa = CPM_REQ_DEFAULT;
	cpm_rx.pa = CPM_RESP_DEFAULT;
	PE_parse_boot_argn("blazer_cpm_req", &cpm_tx.pa, sizeof(cpm_tx.pa));
	PE_parse_boot_argn("blazer_cpm_resp", &cpm_rx.pa, sizeof(cpm_rx.pa));
	if ((cpm_tx.pa & (CPM_CLIENT_SIZE - 1)) != 0 ||
	    (cpm_rx.pa & (CPM_CLIENT_SIZE - 1)) != 0 || cpm_tx.pa == cpm_rx.pa) {
		printf("blazer-cpm: request/response addresses are not distinct 4 KiB clients\n");
		return;
	}
	cpm_tx.regs = (volatile uint32_t *)ml_io_map((vm_offset_t)cpm_tx.pa, CPM_CLIENT_SIZE);
	cpm_rx.regs = (volatile uint32_t *)ml_io_map((vm_offset_t)cpm_rx.pa, CPM_CLIENT_SIZE);
	if (cpm_tx.regs == NULL || cpm_rx.regs == NULL) {
		printf("blazer-cpm: could not map the request/response clients\n");
		return;
	}
	/* These are plain state registers. No IRQ status is acknowledged and no
	 * doorbell or configuration register is written in probe mode. */
	tx_cfg = cpm_read(cpm_tx.regs, MBA_IRQ_CFG);
	rx_cfg = cpm_read(cpm_rx.regs, MBA_IRQ_CFG);
	cpm_tx.words = cpm_capacity(&cpm_tx);
	cpm_rx.words = cpm_capacity(&cpm_rx);
	cpm_mapped = cpm_shape_valid(&cpm_tx) && cpm_shape_valid(&cpm_rx);
	printf("blazer-cpm: req@%llx %u words, resp@%llx %u words, irq cfg %08x/%08x: %s%s\n",
	    cpm_tx.pa, cpm_tx.words, cpm_rx.pa, cpm_rx.words, tx_cfg, rx_cfg,
	    cpm_mapped ? "queue shape valid" : "unusable",
	    cpm_tx_allowed ? "; TX authorized" : "; read only");
	if (!cpm_mapped) {
		cpm_tx.regs = cpm_rx.regs = NULL;
	}
}

int
hakios_blazer_cpm_queue_init(const uint32_t words[HAKIOS_BLAZER_CPM_WORDS])
{
	if (!cpm_mapped || cpm_tx.regs == NULL || cpm_rx.regs == NULL) {
		return ENXIO;
	}
	if (!cpm_tx_allowed) {
		return EPERM;
	}
	if (words == NULL) {
		return EINVAL;
	}
	/* Startup enables automatic host ACK and unmasks message/ACK status.
	 * Queue init is special: write at the current cursor, then reset the NEXT
	 * cursor to zero. A CPM power request is never substituted for this step. */
	cpm_write(cpm_tx.regs, MBA_IRQ_CFG,
	    MBA_IRQ_AUTO_ACK | MBA_IRQ_MESSAGE_MASK | MBA_IRQ_ACK_MASK);
	for (unsigned i = 0; i < HAKIOS_BLAZER_CPM_WORDS; i++) {
		cpm_write(cpm_tx.regs, MBA_MSG(cpm_tx.cursor + i), words[i]);
	}
	cpm_tx.cursor = 0;
	cpm_rx.cursor = 0;
	cpm_write(cpm_tx.regs, MBA_DOORBELL, 1);
	cpm_queue_ready = true;
	printf("blazer-cpm: queue init sent (%08x %08x %08x %08x)\n",
	    words[0], words[1], words[2], words[3]);
	return 0;
}

int
hakios_blazer_cpm_send(const uint32_t words[HAKIOS_BLAZER_CPM_WORDS])
{
	if (!cpm_mapped || !cpm_queue_ready) {
		return ENXIO;
	}
	if (words == NULL) {
		return EINVAL;
	}
	/* goog-mba clears stale ACK status before checking queue space. */
	cpm_write(cpm_tx.regs, MBA_IRQ_STATUS, MBA_IRQ_ACK);
	if (cpm_read(cpm_tx.regs, MBA_OUTSTANDING) >= cpm_tx.words / HAKIOS_BLAZER_CPM_WORDS) {
		return EBUSY;
	}
	for (unsigned i = 0; i < HAKIOS_BLAZER_CPM_WORDS; i++) {
		cpm_write(cpm_tx.regs, MBA_MSG(cpm_tx.cursor + i), words[i]);
	}
	cpm_tx.cursor += HAKIOS_BLAZER_CPM_WORDS;
	if (cpm_tx.cursor >= cpm_tx.words) {
		cpm_tx.cursor = 0;
	}
	cpm_write(cpm_tx.regs, MBA_DOORBELL, 1);
	return 0;
}

unsigned
hakios_blazer_cpm_receive(uint32_t *out, unsigned max_msgs)
{
	unsigned n = 0;

	if (!cpm_queue_ready || out == NULL || max_msgs == 0) {
		return 0;
	}
	while (n < max_msgs && (cpm_read(cpm_rx.regs, MBA_IRQ_STATUS) & MBA_IRQ_MESSAGE) != 0) {
		/* Copy the complete message before acknowledging it. The ACK tells CPM
		 * that the slot may be reused; acknowledging first could tear the frame. */
		for (unsigned i = 0; i < HAKIOS_BLAZER_CPM_WORDS; i++) {
			out[n * HAKIOS_BLAZER_CPM_WORDS + i] = cpm_read(cpm_rx.regs, MBA_MSG(cpm_rx.cursor + i));
		}
		cpm_rx.cursor += HAKIOS_BLAZER_CPM_WORDS;
		if (cpm_rx.cursor >= cpm_rx.words) {
			cpm_rx.cursor = 0;
		}
		cpm_write(cpm_rx.regs, MBA_IRQ_STATUS, MBA_IRQ_MESSAGE);
		n++;
	}
	return n;
}
