/*
 * laguna_panicdump — panic-path capture into the ramoops console zone.
 * See laguna_panicdump.h for the contract (device truth, format, boot args).
 *
 * Linux-side semantics this file must stay byte-compatible with
 * (linux-next fs/pstore/ram_core.c, no ECC — the DT ramoops@95200000 node
 * carries no ecc-size):
 *   struct persistent_ram_buffer { u32 sig; u32 start; u32 size; u8 data[]; }
 *   PERSISTENT_RAM_SIG = 0x43474244; both the console and the pmsg zone are
 *   created with per-zone sig 0, so the stored sig is 0x43474244 for both.
 *   buffer_size = zone_size - sizeof(buffer) = zone_size - 12.
 *   Zap (old content discarded) happens on Linux's next init when the stored
 *   sig mismatches or start/size violate start <= size <= buffer_size.
 *   console-ramoops-0 / pmsg-ramoops-0 file content = data[start..start+size)
 *   wrap-linearized, oldest first — appended bytes show up at the file tail.
 *
 * The append mirrors persistent_ram_write() ring arithmetic exactly; the
 * pmsg mirror is a raw fixed-offset record the same way the kexec marker
 * (pmsg+0x100) and the MARKER-SCRATCH/diag-ring pages (pmsg+0x1000..0x12000)
 * are raw placements — none of those touch the zone header, and neither does
 * the mirror. XNU-MSGBUF v2 keeps all XNU-owned evidence out of the hostile
 * low region (<+0x6000, see the v2 addendum in docs/XNU-MSGBUF-v1.md);
 * research/check-pmsg-contract.py guards all of the offsets.
 */
#include <arm64/laguna_panicdump.h>
#include <arm64/laguna_lastwords.h>

#if defined(__arm64__) && !defined(LAGUNA_PANICDUMP_STUB)
/* Port note (mach-choice lane): the 141 tree guarded this body with
 * #if defined(LAGUNA) (its MASTER.arm64.laguna machine config). The blazer
 * tree builds MACHINE_CONFIGS=BCM2837, where LAGUNA is not defined, so the
 * guard would have compiled the stub path and silenced the whole evidence
 * chain. The chain is always-on by design (lagunapanicdump=0 disables), so
 * the guard is now __arm64__ with an explicit stub opt-out for host builds. */

#include <stdint.h>

#include <pexpert/pexpert.h>           /* kprintf, PE_parse_boot_argn */
#include <machine/machine_routines.h>  /* ml_io_map, ml_validate_nofault,
                                        * ml_nofault_copy (0124) */
#include <kern/thread.h>               /* kernel_thread_start_priority */
#include <kern/clock.h>                /* delay_for_interval (test needle) */
#include <kern/debug.h>                /* panic_validate_ptr (0124) */
#include <kern/startup.h>              /* STARTUP(OSLOG) msgbuf-resize hook */
#include <sys/msgbuf.h>

extern struct msgbuf *msgbufp;

/* ---- placement constants (guarded by research/check-pmsg-contract.py) ---- */

/* DT /reserved-memory/ramoops@95200000: reg 0x95200000+0x400000,
 * console-size 0x200000 then pmsg-size 0x200000 (check-pmsg-contract.py
 * computes console@0x95200000 pmsg@0x95400000 from the same node). */
#define LAGUNA_RAMOOPS_BASE       0x95200000ull
#define LAGUNA_RAMOOPS_SIZE       0x400000ull
#define LAGUNA_CONSOLE_OFF        0x0ull
#define LAGUNA_CONSOLE_SIZE       0x200000ull
#define LAGUNA_PMSG_OFF           0x200000ull
#define LAGUNA_PMSG_SIZE          0x200000ull

/* Mirror record: pmsg zone +0x30000, window 0x2040. Clear of the handoff
 * marker slots (+0x100..0x130), the boot_args smuggle page (+0x1000..0x2000),
 * MARKER-SCRATCH (+0x1000..0x1043) and the diag ring (+0x1080 counter,
 * +0x2000..0x12000 records); far enough up that Android's own /dev/pmsg0
 * ring (appended at the zone tail, 2 MiB wrap) does not reach it per boot. */
#define LAGUNA_MIRROR_OFF         0x30000ull
#define LAGUNA_MIRROR_SIZE        0x2040ull
#define LAGUNA_MIRROR_PAYLOAD_MAX 0x2000u   /* 8 KiB mirror payload cap */
/* Linux fs/pstore/ram_core.c persistent_ram_buffer, no ECC. sizeof = 12. */
#define LAGUNA_PRZ_HDR            12u
#define LAGUNA_PRZ_SIG            0x43474244u

/* Mirror record layout (little-endian, fixed 40-byte header). */
#define LAGUNA_MIRROR_MAGIC2      0x50414e31u /* 'PAN1' quick-scan tag */
#define LAGUNA_MMR_MAGIC2_OFF     16u
#define LAGUNA_MMR_CRC_OFF        20u
#define LAGUNA_MMR_LEN_OFF        24u
#define LAGUNA_MMR_SEQ_OFF        28u
#define LAGUNA_MMR_DATA_OFF       40u

/* Capture budget (panic path must stay bounded). */
#define LAGUNA_CAP_MSGBUF_MAX     0x4000u   /* 16 KiB msgbuf tail */
#define LAGUNA_CAP_PANICLOG_MAX   0x8000u   /* 32 KiB paniclog */

/* R4 always-on msgbuf mirror, XNU-MSGBUF v2 (0130): fixed 64-byte header +
 * MAX_MSG_BSIZE ring.  V2 relocates every XNU-owned always-on channel out of
 * the empirically hostile low pmsg region (2026-10-26 mirror-run dump
 * r4z-kernel.log: ABL/TEE fastboot-re-entry log at +0x000..~0x900, the
 * Linux-kexec handoff page +0x000..0xFFF = kexec seg 13, and the probe log
 * ring +0x1080..~0x5C80; identical map in every retrieved dump).  The header
 * now sits at +0x3F800 immediately below the unchanged 1 MiB ring at +0x40000
 * (the ring window itself is bit-compatible with v1; only the header moved,
 * so v1's embedded header at +0x40000 no longer exists in v2 output).
 *
 * The header's stream fields advance with EVERY accepted byte, so an early
 * death never waits for a timer. seq versions the live-source metadata
 * refreshes; it is deliberately not a validity/commit gate for the ring. */
#define LAGUNA_LOGDUMP_HDR_OFF      0x3F800ull
#define LAGUNA_LOGDUMP_OFF          0x40000ull /* ring data base (unchanged) */
#define LAGUNA_LOGDUMP_HEADER_SIZE  0x40u
#define LAGUNA_LOGDUMP_RING_SIZE    0x100000u /* MAX_MSG_BSIZE */
#define LAGUNA_LOGDUMP_WINDOW_SIZE  (LAGUNA_LOGDUMP_HEADER_SIZE + \
                                     LAGUNA_LOGDUMP_RING_SIZE)
#define LAGUNA_LD_SEQ_OFF           0x10u
#define LAGUNA_LD_FLAGS_OFF         0x14u
#define LAGUNA_LD_MSGBUFP_OFF       0x18u
#define LAGUNA_LD_BUFC_OFF          0x20u
#define LAGUNA_LD_SIZE_OFF          0x28u
#define LAGUNA_LD_BUFSIZE_OFF       0x28u /* source-contract alias */
#define LAGUNA_LD_BUFX_OFF          0x2cu
#define LAGUNA_LD_TOTAL_OFF         0x30u
#define LAGUNA_LD_TAIL_OFF_OFF      0x38u
#define LAGUNA_LD_TAIL_LEN_OFF      0x3cu

#define LAGUNA_LD_FLAG_WRAPPED        (1u << 0)
#define LAGUNA_LD_FLAG_PANIC          (1u << 1)
#define LAGUNA_LD_FLAG_HALT           (1u << 2)
#define LAGUNA_LD_FLAG_EARLY_REPLAY   (1u << 3)
#define LAGUNA_LD_FLAG_SOURCE_INVALID (1u << 4)

/* V2 relocated compact breadcrumb trail + XNU-owned counters, one 128-byte
 * page at pmsg+0x28000..0x280ff.  Twelve LE stage-code words are chrono-
 * logical/nondecreasing; LGLW remains the CRC/DONE authoritative channel.
 * Entry clears exactly the 48 breadcrumb bytes every boot.  The boot counter
 * increments once per enabled boot (persists across boots); the diag counter
 * increments on every mirror flush (proves the periodic thread was alive).
 * Both are clear of the probe log (ends ~+0x5C80), the diag-ring/boot-args
 * placements (<+0x12000), the PAN1 mirror (+0x30000..0x32040), and the
 * logdump header/ring (>= +0x3F800). */
#define LAGUNA_STAGE_COMPACT_OFF   0x28000ull
#define LAGUNA_STAGE_COMPACT_WORDS 12u
#define LAGUNA_STAGE_COMPACT_SIZE  (LAGUNA_STAGE_COMPACT_WORDS * 4u)
#define LAGUNA_BOOTCTR_OFF         0x28040ull
#define LAGUNA_DIAGCTR_OFF         0x28080ull
#define LAGUNA_V2_CTR_MAX          0xfffffffeu /* sanitized like LGLW seq */

_Static_assert(LAGUNA_STAGE_COMPACT_OFF >= 0x12000ull,
    "v2 compact trail overlaps the legacy diag-ring/boot-args placements");
_Static_assert(LAGUNA_STAGE_COMPACT_OFF + LAGUNA_STAGE_COMPACT_SIZE <=
    LAGUNA_BOOTCTR_OFF, "v2 compact trail overlaps the boot counter");
_Static_assert(LAGUNA_BOOTCTR_OFF + 4 <= LAGUNA_DIAGCTR_OFF,
    "v2 boot counter overlaps the diag counter");
_Static_assert(LAGUNA_DIAGCTR_OFF + 4 <= 0x28100ull,
    "v2 counters escape the relocated evidence page");
_Static_assert(LAGUNA_LOGDUMP_HDR_OFF >= LAGUNA_DIAGCTR_OFF + 4 &&
    LAGUNA_LOGDUMP_HDR_OFF >= LAGUNA_MIRROR_OFF + LAGUNA_MIRROR_SIZE,
    "v2 header overlaps the counters or the PAN1 mirror");
_Static_assert(LAGUNA_LOGDUMP_HDR_OFF + LAGUNA_LOGDUMP_HEADER_SIZE <=
    LAGUNA_LOGDUMP_OFF, "v2 header overlaps the ring data");
_Static_assert(LAGUNA_LOGDUMP_OFF + LAGUNA_LOGDUMP_RING_SIZE <=
    LAGUNA_PMSG_SIZE, "logdump ring escapes the pmsg zone");
_Static_assert(LAGUNA_LOGDUMP_RING_SIZE == MAX_MSG_BSIZE,
    "logdump ring must cover XNU's largest msgbuf");

_Static_assert(LAGUNA_MIRROR_SIZE >= LAGUNA_MMR_DATA_OFF + LAGUNA_MIRROR_PAYLOAD_MAX,
    "mirror window too small for the payload cap");
_Static_assert(LAGUNA_MIRROR_OFF + LAGUNA_MIRROR_SIZE <= LAGUNA_PMSG_SIZE,
    "mirror window escapes the pmsg zone");
_Static_assert(LAGUNA_MIRROR_OFF >= 0x12000ull,
    "mirror window overlaps the diag-ring/boot-args placements");

/* ---- zone state (init-time, read-only afterwards) ---- */

static volatile uint8_t *laguna_pd_va;      /* ml_io_map of the 4 MiB zone */
static volatile uint32_t laguna_pd_enabled; /* boot-arg gate passed + mapped */
static volatile uint32_t laguna_pd_captured;/* a capture already ran */
static volatile uint32_t laguna_pd_captured_len; /* its paniclog size */
static volatile uint32_t laguna_pd_console_live; /* console header appendable */
static uint32_t laguna_pd_test_secs;        /* lagunapanictest value (bootdone) */

/* R4 mirror state. Appends are serialized by the existing BSD log lock;
 * aligned volatile loads/stores are sufficient for lock-free dump readers and
 * the periodic metadata thread. No new lock is ever taken on the log path. */
static volatile uint32_t laguna_ld_enabled;
static volatile uint32_t laguna_ld_disabled;
static volatile uint32_t laguna_ld_thread_started;
static volatile uint32_t laguna_ld_stage_compact_count;
static volatile uint32_t laguna_ld_flags;
static volatile uint32_t laguna_ld_seq;
static volatile uint32_t laguna_ld_active_size;
static volatile uint32_t laguna_ld_tail_len;
static volatile uint64_t laguna_ld_total;

/*
 * 0124: panic-time staging for the msgbuf tail. The rung-2 capture used to
 * read the BSD ring straight out of msgbufp->msg_bufc — and the QEMU
 * evidence (evidence/ramoops-zone-qemu-pass.bin, decoded by
 * tools/decode-device-panic.py) shows a nested panic with the CPU attributed
 * to this capture: the ring buffer can be (re)moved under the panic path, so
 * a direct dereference is exactly the kind of fault that tears the record.
 * The tail is now staged with ml_nofault_copy() (physical-mode copy that
 * stops cleanly on an unmapped page and reports how far it got), after
 * panic_validate_ptr() checked both msgbufp and the ring span. A short copy
 * is APPENDED AS-IS with an explicit note; no fault can tear the record.
 */
static uint8_t laguna_pd_mb_stage[LAGUNA_CAP_MSGBUF_MAX];

static inline volatile uint8_t *
laguna_pd_at(uint64_t zone_off)
{
	return laguna_pd_va + zone_off;
}

static inline const volatile uint8_t *
laguna_pd_cat(uint64_t zone_off)
{
	return laguna_pd_va + zone_off;
}

/* ---- 0125-class LGLW stage records (RAM-dump producer) ---- */

static int
laguna_pd_lw_bounds(uint64_t pa, uint32_t len, uint64_t *zone_off)
{
	uint64_t off;

	if (laguna_pd_va == NULL || pa < LAGUNA_RAMOOPS_BASE)
		return 0;
	off = pa - LAGUNA_RAMOOPS_BASE;
	if (off > LAGUNA_RAMOOPS_SIZE ||
	    (uint64_t)len > LAGUNA_RAMOOPS_SIZE - off)
		return 0;
	*zone_off = off;
	return 1;
}

static long
laguna_pd_lw_read(void *ctx, uint64_t pa, void *buf, uint32_t len)
{
	uint64_t off;
	uint8_t *out = buf;
	(void)ctx;
	if (!laguna_pd_lw_bounds(pa, len, &off))
		return -1;
	for (uint32_t i = 0; i < len; i++)
		out[i] = *laguna_pd_cat(off + i);
	return 0;
}

static long
laguna_pd_lw_write(void *ctx, uint64_t pa, const void *buf, uint32_t len)
{
	uint64_t off;
	const uint8_t *in = buf;
	(void)ctx;
	if (!laguna_pd_lw_bounds(pa, len, &off))
		return -1;
	for (uint32_t i = 0; i < len; i++)
		*laguna_pd_at(off + i) = in[i];
	return 0;
}

static long
laguna_pd_lw_clean(void *ctx, uint64_t pa, uint32_t len)
{
	uint64_t off;
	(void)ctx;
	if (!laguna_pd_lw_bounds(pa, len, &off))
		return -1;
	/* ml_io_map Device-memory stores are already uncacheable. */
	__asm__ volatile("dsb sy" ::: "memory");
	return 0;
}

static struct lw_mem laguna_pd_lw_mem = {
	.ctx = NULL,
	.read = laguna_pd_lw_read,
	.write = laguna_pd_lw_write,
	.clean = laguna_pd_lw_clean,
};

static void
laguna_logdump_compact_stage(uint32_t stage)
{
	const uint64_t cbase = LAGUNA_PMSG_OFF + LAGUNA_STAGE_COMPACT_OFF;
	uint32_t count = laguna_ld_stage_compact_count;

	if (!laguna_ld_enabled || stage == 0 || stage > 12 ||
	    count >= LAGUNA_STAGE_COMPACT_WORDS) {
		return;
	}
	*(volatile uint32_t *)(void *)laguna_pd_at(cbase + count * 4u) = stage;
	__asm__ volatile("dsb sy" ::: "memory");
	laguna_ld_stage_compact_count = count + 1;
}

/* Safe before init and from the panic rungs: disabled = 0, otherwise the
 * lw_write result (positive sequence or negative error).  No allocation,
 * locks, printf, or unchecked panic-string dereference. */
long
laguna_panicdump_stage(uint32_t stage, uint32_t producer, uint32_t flags,
    uint64_t detail0, uint64_t detail1, uint64_t detail2, uint64_t detail3,
    const char *note)
{
	struct lw_event event;

	/* R4: both independent gates must opt in. lagunalogdump=0 suppresses
	 * additive breadcrumbs without disabling the 0123/0124 panic capture. */
	if (!laguna_pd_enabled || !laguna_ld_enabled)
		return 0;
	event.stage = stage;
	event.producer = producer;
	event.flags = flags;
	event.detail[0] = detail0;
	event.detail[1] = detail1;
	event.detail[2] = detail2;
	event.detail[3] = detail3;
	event.note = note;
	{
		long rc = lw_write(&laguna_pd_lw_mem, &event);
		if (rc > 0) {
			laguna_logdump_compact_stage(stage);
		}
		return rc;
	}
}

/* ---- CRC-32/IEEE, table-free (same polynomial as the diag ring) ---- */

static uint32_t
laguna_pd_crc32(const volatile uint8_t *p, uint32_t n)
{
	uint32_t c = 0xffffffffu;
	uint32_t i;
	int b;

	for (i = 0; i < n; i++) {
		c ^= p[i];
		for (b = 0; b < 8; b++) {
			c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
		}
	}
	return ~c;
}

static void laguna_panicdump_test_thread(void *arg, int wait_result);

/* ---- raw zone access (Device memory: aligned volatile word ops) ---- */

static inline uint32_t
laguna_pd_rd32(uint64_t zone_off)
{
	return *(const volatile uint32_t *)(const void *)laguna_pd_cat(zone_off);
}

static inline void
laguna_pd_wr32(uint64_t zone_off, uint32_t v)
{
	*(volatile uint32_t *)(void *)laguna_pd_at(zone_off) = v;
}

static inline void
laguna_pd_wr64(uint64_t zone_off, uint64_t v)
{
	*(volatile uint64_t *)(void *)laguna_pd_at(zone_off) = v;
}

static inline uint64_t
laguna_pd_rd64(uint64_t zone_off)
{
	return *(const volatile uint64_t *)(const void *)laguna_pd_cat(zone_off);
}

/* ---- R4 always-on XNU-MSGBUF v2 mirror ---- */

static int
laguna_pd_map(void)
{
	if (laguna_pd_va == NULL) {
		/* One shared mapping for panic capture, LGLW and the log mirror.
		 * The physmap does not cover this low bank, and a second early
		 * ml_io_map would exhaust the bootstrap I/O page-table window. */
		laguna_pd_va = (volatile uint8_t *)(uintptr_t)ml_io_map(
		    (vm_offset_t)LAGUNA_RAMOOPS_BASE,
		    (vm_size_t)LAGUNA_RAMOOPS_SIZE);
	}
	return laguna_pd_va != NULL;
}

static void
laguna_logdump_append(char c, uint32_t bufx)
{
	const uint64_t hbase = LAGUNA_PMSG_OFF + LAGUNA_LOGDUMP_HDR_OFF;
	const uint64_t rbase = LAGUNA_PMSG_OFF + LAGUNA_LOGDUMP_OFF;
	uint64_t total, next;
	uint32_t active, tail_len, tail_off;

	if (!laguna_ld_enabled || laguna_pd_va == NULL) {
		return;
	}
	active = laguna_ld_active_size;
	if (active == 0 || active > LAGUNA_LOGDUMP_RING_SIZE) {
		return;
	}
	total = laguna_ld_total;
	*laguna_pd_at(rbase + (total % active)) = (uint8_t)c;
	next = total + 1;
	laguna_ld_total = next;

	/* Data first, then the live stream metadata. tail_len never leads the
	 * corresponding byte, so even a reset between these stores cannot make
	 * the reader consume a not-yet-written byte as current log data. R2's
	 * geometry is exact: bufx == total % live_size and the retained tail
	 * starts at (total - tail_len) % live_size. */
	if (laguna_ld_tail_len < active) {
		laguna_ld_tail_len++;
	}
	tail_len = laguna_ld_tail_len;
	tail_off = (uint32_t)((next - tail_len) % active);
	laguna_pd_wr64(hbase + LAGUNA_LD_TOTAL_OFF, next);
	laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_LEN_OFF, tail_len);
	laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_OFF_OFF, tail_off);
	laguna_pd_wr32(hbase + LAGUNA_LD_BUFX_OFF, bufx);
	if (next > active && total <= active) {
		laguna_ld_flags |= LAGUNA_LD_FLAG_WRAPPED;
		laguna_pd_wr32(hbase + LAGUNA_LD_FLAGS_OFF, laguna_ld_flags);
	}
}

void
laguna_logdump_putc(char c, uint32_t bufx)
{
	laguna_logdump_append(c, bufx);
}

static int
laguna_logdump_source_snapshot(uint32_t reason, uint64_t *mbva,
    uint64_t *bcva, uint32_t *size, uint32_t *bufx)
{
	struct msgbuf *mb = msgbufp;
	char *bufc;
	int magic, ssize, sbufx, sbufr;

	if (mb == NULL) {
		return 0;
	}
	if ((reason & (LAGUNA_LOGDUMP_FLUSH_PANIC |
	    LAGUNA_LOGDUMP_FLUSH_HALT)) &&
	    !panic_validate_ptr(mb, (vm_size_t)sizeof(*mb), "msgbufp")) {
		return 0;
	}

	/* Do not dereference msg_bufc here. The fixed mirror is authoritative;
	 * these fields only let R2 cross-check the live source when a full RAM
	 * dump is available. Panic/halt still validates that source span using
	 * the exact 0124 nofault discipline before publishing its metadata. */
	magic = mb->msg_magic;
	ssize = mb->msg_size;
	sbufx = mb->msg_bufx;
	sbufr = mb->msg_bufr;
	bufc = mb->msg_bufc;
	if (magic != MSG_MAGIC || ssize <= 0 || ssize > MAX_MSG_BSIZE ||
	    sbufx < 0 || sbufx > ssize || sbufr < 0 || sbufr > ssize) {
		return 0;
	}
	if ((reason & (LAGUNA_LOGDUMP_FLUSH_PANIC |
	    LAGUNA_LOGDUMP_FLUSH_HALT)) &&
	    !ml_validate_nofault((vm_offset_t)bufc, (vm_size_t)ssize)) {
		return 0;
	}

	*mbva = (uint64_t)(uintptr_t)mb;
	*bcva = (uint64_t)(uintptr_t)bufc;
	*size = (uint32_t)ssize;
	*bufx = (uint32_t)sbufx;
	return 1;
}

void
laguna_logdump_flush(uint32_t reason)
{
	const uint64_t hbase = LAGUNA_PMSG_OFF + LAGUNA_LOGDUMP_HDR_OFF;
	uint64_t mbva, bcva;
	uint32_t size, bufx, flags, seq, diag;

	if (!laguna_ld_enabled || laguna_pd_va == NULL) {
		return;
	}
	/* A failed source inspection must not make the authoritative fixed ring
	 * unreadable. Retain the last valid source metadata and mark the fault. */
	mbva = laguna_pd_rd64(hbase + LAGUNA_LD_MSGBUFP_OFF);
	bcva = laguna_pd_rd64(hbase + LAGUNA_LD_BUFC_OFF);
	size = laguna_pd_rd32(hbase + LAGUNA_LD_SIZE_OFF);
	bufx = laguna_pd_rd32(hbase + LAGUNA_LD_BUFX_OFF);
	flags = laguna_ld_flags;
	if (laguna_logdump_source_snapshot(reason, &mbva, &bcva,
	    &size, &bufx)) {
		flags &= ~LAGUNA_LD_FLAG_SOURCE_INVALID;
	} else {
		flags |= LAGUNA_LD_FLAG_SOURCE_INVALID;
	}
	flags |= reason & (LAGUNA_LOGDUMP_FLUSH_PANIC |
	    LAGUNA_LOGDUMP_FLUSH_HALT);
	laguna_ld_flags = flags;

	laguna_pd_wr64(hbase + LAGUNA_LD_MSGBUFP_OFF, mbva);
	laguna_pd_wr64(hbase + LAGUNA_LD_BUFC_OFF, bcva);
	laguna_pd_wr32(hbase + LAGUNA_LD_SIZE_OFF, size);

	/* total/tail/bufx belong exclusively to append/replay. A periodic flush
	 * must never combine a pre-append source index with a post-append total. */
	laguna_pd_wr32(hbase + LAGUNA_LD_FLAGS_OFF, flags);
	__asm__ volatile("dsb sy" ::: "memory");

	/* V2 diag counter: one increment per completed flush event. A nonzero,
	 * advancing counter proves the mirror machinery (periodic thread,
	 * startup hook, panic/halt rungs) executed after init, independent of
	 * the per-byte stream. Sanitized so zone garbage cannot truncate it. */
	diag = laguna_pd_rd32(LAGUNA_PMSG_OFF + LAGUNA_DIAGCTR_OFF);
	diag = (diag == 0 || diag >= LAGUNA_V2_CTR_MAX) ? 1u : diag + 1u;
	laguna_pd_wr32(LAGUNA_PMSG_OFF + LAGUNA_DIAGCTR_OFF, diag);

	seq = laguna_ld_seq + 1;
	if (seq == 0 || seq > 0xfffffffdu) {
		seq = 1;
	}
	laguna_ld_seq = seq;
	laguna_pd_wr32(hbase + LAGUNA_LD_SEQ_OFF, seq);
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
laguna_logdump_replay(void)
{
	const uint64_t hbase = LAGUNA_PMSG_OFF + LAGUNA_LOGDUMP_HDR_OFF;
	struct msgbuf *mb = msgbufp;

	laguna_ld_total = 0;
	laguna_ld_tail_len = 0;
	laguna_ld_flags &= ~LAGUNA_LD_FLAG_WRAPPED;
	if (mb == NULL || mb->msg_magic != MSG_MAGIC ||
	    mb->msg_size <= 0 || mb->msg_size > MAX_MSG_BSIZE ||
	    mb->msg_bufc == NULL) {
		laguna_ld_active_size = 0;
		laguna_pd_wr32(hbase + LAGUNA_LD_SIZE_OFF, 0);
		laguna_pd_wr64(hbase + LAGUNA_LD_TOTAL_OFF, 0);
		laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_LEN_OFF, 0);
		laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_OFF_OFF, 0);
		laguna_pd_wr32(hbase + LAGUNA_LD_BUFX_OFF, 0);
		return;
	}

	{
		uint32_t size = (uint32_t)mb->msg_size;
		uint32_t bx = (uint32_t)mb->msg_bufx;
		uint32_t br = (uint32_t)mb->msg_bufr;
		uint32_t used, start;

		laguna_ld_active_size = size;
		if (bx > size || br > size) {
			laguna_ld_active_size = 0;
			laguna_pd_wr32(hbase + LAGUNA_LD_SIZE_OFF, 0);
			laguna_pd_wr64(hbase + LAGUNA_LD_TOTAL_OFF, 0);
			laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_LEN_OFF, 0);
			laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_OFF_OFF, 0);
			laguna_pd_wr32(hbase + LAGUNA_LD_BUFX_OFF, 0);
			return;
		}
		used = bx >= br ? bx - br : size - br + bx;
		if (used == 0) {
			for (uint32_t i = 0; i < size; i++) {
				if (mb->msg_bufc[i] != '\0') {
					used = size;
					break;
				}
			}
		}
		if (used > bx) {
			used = size; /* wrapped/read-pointer ambiguity: replay full ring */
		}

		/* Seed stream geometry so the replay ends with total % size == bx.
		 * A full ring has one absolute wrap plus bx; a partial ring retains
		 * exactly used bytes beginning at (bx-used) == br. */
		laguna_ld_total = used == size ? bx : bx - used;
		laguna_pd_wr32(hbase + LAGUNA_LD_SIZE_OFF, size);
		laguna_pd_wr64(hbase + LAGUNA_LD_TOTAL_OFF, laguna_ld_total);
		laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_LEN_OFF, 0);
		laguna_pd_wr32(hbase + LAGUNA_LD_TAIL_OFF_OFF, 0);
		laguna_pd_wr32(hbase + LAGUNA_LD_BUFX_OFF, bx);
		laguna_pd_wr32(hbase + LAGUNA_LD_FLAGS_OFF, laguna_ld_flags);
		start = (bx + size - used) % size;
		for (uint32_t i = 0; i < used; i++) {
			laguna_logdump_append(mb->msg_bufc[(start + i) % size], bx);
		}
	}
}

void
laguna_logdump_early_init(void)
{
	static const char magic[LAGUNA_LOGDUMP_HEADER_SIZE] = "XNU-MSGBUF v2";
	const uint64_t hbase = LAGUNA_PMSG_OFF + LAGUNA_LOGDUMP_HDR_OFF;
	uint32_t arg = 1;

	if (laguna_ld_enabled || laguna_ld_disabled) {
		return;
	}
	(void)PE_parse_boot_argn("lagunalogdump", &arg, (int)sizeof(arg));
	if (!arg) {
		laguna_ld_disabled = 1;
		return;
	}
	if (!laguna_pd_map()) {
		return; /* late_init may retry after the existing panic init */
	}

	for (uint32_t i = 0; i < LAGUNA_LOGDUMP_HEADER_SIZE; i++) {
		*laguna_pd_at(hbase + i) = 0;
	}
	for (uint32_t i = 0; i < sizeof(magic); i++) {
		*laguna_pd_at(hbase + i) = (uint8_t)magic[i];
	}
	laguna_ld_total = 0;
	laguna_ld_tail_len = 0;
	laguna_ld_seq = 0;
	laguna_ld_flags = 0;

	/* V2 boot counter: persists across boots (never cleared). Sanitized so
	 * stale-zone garbage or an exhausted word restarts at 1. */
	{
		uint32_t boots = laguna_pd_rd32(LAGUNA_PMSG_OFF + LAGUNA_BOOTCTR_OFF);
		boots = (boots == 0 || boots >= LAGUNA_V2_CTR_MAX) ? 1u : boots + 1u;
		laguna_pd_wr32(LAGUNA_PMSG_OFF + LAGUNA_BOOTCTR_OFF, boots);
	}

	laguna_ld_enabled = 1;
	__asm__ volatile("dsb sy" ::: "memory");

	/* start.s already cleared the compact trail and, when enabled, wrote
	 * XNU_ENTRY. Recover its append position without erasing that evidence. */
	laguna_ld_stage_compact_count = 0;
	while (laguna_ld_stage_compact_count < LAGUNA_STAGE_COMPACT_WORDS &&
	    laguna_pd_rd32(LAGUNA_PMSG_OFF + LAGUNA_STAGE_COMPACT_OFF +
	    laguna_ld_stage_compact_count * 4u) != 0) {
		laguna_ld_stage_compact_count++;
	}

	/* msgbuf is statically initialized before this point. Replay its current
	 * stream in source-ring geometry before accepting new bytes directly. */
	laguna_logdump_replay();
	laguna_ld_flags |= LAGUNA_LD_FLAG_EARLY_REPLAY;
	laguna_logdump_flush(0);
}

void
laguna_logdump_late_init(void)
{
	if (!laguna_ld_enabled && !laguna_ld_disabled) {
		laguna_logdump_early_init();
	}
	if (laguna_ld_disabled) {
		kprintf("LAGUNA LOGDUMP: disabled by lagunalogdump=0\n");
		return;
	}
	if (!laguna_ld_enabled) {
		kprintf("LAGUNA LOGDUMP: ramoops map unavailable\n");
		return;
	}
	kprintf("LAGUNA LOGDUMP: armed (XNU-MSGBUF v2 hdr @pmsg+0x%x, "
	    "ring @pmsg+0x%x, %u KiB, bc/dr+0x%x/+0x%x)\n",
	    (uint32_t)LAGUNA_LOGDUMP_HDR_OFF, (uint32_t)LAGUNA_LOGDUMP_OFF,
	    LAGUNA_LOGDUMP_RING_SIZE / 1024, (uint32_t)LAGUNA_BOOTCTR_OFF,
	    (uint32_t)LAGUNA_DIAGCTR_OFF);
	laguna_logdump_flush(0);
}

__startup_func
static void
laguna_logdump_oslog_ready(void)
{
	/* oslog_init_msgbuf is RANK_SECOND. Its optional msgbuf=n resize has
	 * completed. Rebase the active ring into the new source geometry instead
	 * of appending with a new modulus over incompatible old positions. */
	laguna_logdump_replay();
	laguna_logdump_flush(0);
}
STARTUP(OSLOG, STARTUP_RANK_THIRD, laguna_logdump_oslog_ready);

static void
laguna_logdump_thread(void *arg __unused, int wait_result __unused)
{
	thread_set_thread_name(current_thread(), "laguna-logdump");
	for (;;) {
		delay_for_interval(1, NSEC_PER_SEC);
		laguna_logdump_flush(0);
	}
}

static void
laguna_logdump_start(void)
{
	thread_t thrd = THREAD_NULL;

	if (!laguna_ld_enabled || laguna_ld_thread_started) {
		return;
	}
	laguna_ld_thread_started = 1;
	if (kernel_thread_start_priority(laguna_logdump_thread, NULL, -1,
	    &thrd) == KERN_SUCCESS) {
		thread_deallocate(thrd);
	} else {
		laguna_ld_thread_started = 0;
		kprintf("LAGUNA LOGDUMP: periodic thread start failed; direct mirror remains live\n");
	}
}

/* ---- persistent_ram ring append (mirror of persistent_ram_write) ---- */
static void
laguna_pd_console_append(const char *s, uint32_t n)
{
	const uint64_t cbase = LAGUNA_CONSOLE_OFF;
	const uint32_t bufsz = (uint32_t)(LAGUNA_CONSOLE_SIZE - LAGUNA_PRZ_HDR);
	uint32_t start, size, c, pos, rem;

	if (!laguna_pd_console_live || laguna_pd_va == NULL || n == 0) {
		return;
	}

	/* Linux clamps to the buffer size and keeps the TAIL of the input. */
	c = n;
	if (c > bufsz) {
		s += c - bufsz;
		c = bufsz;
	}

	start = laguna_pd_rd32(cbase + 4u);
	size = laguna_pd_rd32(cbase + 8u);
	if (start > bufsz || size > bufsz) {
		laguna_pd_console_live = 0;   /* Linux would zap anyway */
		return;
	}

	/* data first: a fault here leaves the header in the old state. */
	pos = start;
	rem = bufsz - pos;
	if (rem > c) {
		rem = c;
	}
	for (uint32_t i = 0; i < rem; i++) {
		*laguna_pd_at(cbase + LAGUNA_PRZ_HDR + pos + i) = (uint8_t)s[i];
	}
	if (rem < c) {
		const uint32_t rest = c - rem;
		for (uint32_t i = 0; i < rest; i++) {
			*laguna_pd_at(cbase + LAGUNA_PRZ_HDR + i) = (uint8_t)s[rem + i];
		}
	}
	__asm__ volatile("dsb sy" ::: "memory");

	/* header second: size grows first, then start wraps (the exact
	 * persistent_ram_write update order; final state identical). */
	if (size != bufsz) {
		uint32_t new_size = size + c;
		if (new_size > bufsz) {
			new_size = bufsz;
		}
		laguna_pd_wr32(cbase + 8u, new_size);
	}
	uint32_t new_start = start + c;
	while (new_start >= bufsz) {
		new_start -= bufsz;
	}
	laguna_pd_wr32(cbase + 4u, new_start);
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
laguna_pd_console_str(const char *s)
{
	uint32_t n = 0;

	if (s == NULL) {
		return;
	}
	while (s[n] != '\0' && n < 128u) {
		n++;
	}
	laguna_pd_console_append(s, n);
}

/* ---- pmsg mirror record (fixed offset, own CRC; newest wins) ---- */

static void
laguna_pd_mirror_write(const char *msg, const char *paniclog, uint32_t plen)
{
	static const char magic[16] = "XNU-PANIC v1";
	const uint64_t rbase = LAGUNA_PMSG_OFF + LAGUNA_MIRROR_OFF;
	uint32_t seq = 1;
	uint32_t mlen = 0;
	uint32_t pl = plen;
	uint32_t crc;
	uint32_t i;

	if (laguna_pd_va == NULL) {
		return;
	}

	/* Seq = previous valid record's seq + 1, sanitized on garbage. */
	if (laguna_pd_rd32(rbase + LAGUNA_MMR_MAGIC2_OFF) == LAGUNA_MIRROR_MAGIC2) {
		uint32_t prev = laguna_pd_rd32(rbase + LAGUNA_MMR_SEQ_OFF);
		if (prev != 0xffffffffu && prev < 0xfffffffeu) {
			seq = prev + 1u;
		}
	}

	/* Assemble the payload directly in the zone, then CRC it from there.
	 * Every store stays inside [rbase, rbase + LAGUNA_MIRROR_SIZE). */
	if (msg != NULL) {
		for (i = 0; msg[i] != '\0' && mlen < LAGUNA_MIRROR_PAYLOAD_MAX - 2u; i++) {
			*laguna_pd_at(rbase + LAGUNA_MMR_DATA_OFF + mlen++) = (uint8_t)msg[i];
		}
	}
	if (plen > 0 && paniclog != NULL && mlen < LAGUNA_MIRROR_PAYLOAD_MAX - 2u) {
		*laguna_pd_at(rbase + LAGUNA_MMR_DATA_OFF + mlen++) = '\n';
		if (pl > LAGUNA_MIRROR_PAYLOAD_MAX - mlen - 1u) {
			pl = LAGUNA_MIRROR_PAYLOAD_MAX - mlen - 1u;
		}
		for (i = 0; i < pl; i++) {
			*laguna_pd_at(rbase + LAGUNA_MMR_DATA_OFF + mlen++) = (uint8_t)paniclog[i];
		}
	}
	/* Zero the stale tail of the payload window (previous records may have
	 * been longer) so old bytes never read as data. */
	for (i = mlen; i < LAGUNA_MIRROR_PAYLOAD_MAX; i++) {
		*laguna_pd_at(rbase + LAGUNA_MMR_DATA_OFF + i) = 0;
	}
	__asm__ volatile("dsb sy" ::: "memory");

	crc = laguna_pd_crc32(laguna_pd_cat(rbase + LAGUNA_MMR_DATA_OFF), mlen);

	/* magic, then metadata — the quick-scan tag goes last for
	 * crash-consistency (a torn write leaves the old record invalid or
	 * the new one absent, never a half-valid new one). */
	for (i = 0; i < sizeof(magic); i++) {
		*laguna_pd_at(rbase + i) = (uint8_t)magic[i];
	}
	laguna_pd_wr32(rbase + LAGUNA_MMR_CRC_OFF, crc);
	laguna_pd_wr32(rbase + LAGUNA_MMR_LEN_OFF, mlen);
	laguna_pd_wr32(rbase + LAGUNA_MMR_SEQ_OFF, seq);
	laguna_pd_wr32(rbase + LAGUNA_MMR_MAGIC2_OFF, LAGUNA_MIRROR_MAGIC2);
	__asm__ volatile("dsb sy" ::: "memory");
}

/* ---- init ---- */

void
laguna_panicdump_init(void)
{
	uint32_t dump_arg = 1;   /* default ON */
	uint32_t test_arg = 0;   /* default off */

	(void)PE_parse_boot_argn("lagunapanicdump", &dump_arg,
	    (int)sizeof(dump_arg));
	if (!dump_arg) {
		kprintf("LAGUNA PANICDUMP: disabled by lagunapanicdump=0\n");
		return;
	}

	/* The physmap does not cover the low bank (device: declared-clean
	 * RAM is 0x8_80000000+; QEMU contract: 0x80000000+256 MiB ends at
	 * 0x90000000). Reuse the one early Device mapping if R4 already made it. */
	if (!laguna_pd_map()) {
		kprintf("LAGUNA PANICDUMP: ml_io_map of ramoops failed — capture unavailable\n");
		return;
	}

	/* Validate the console header the way Linux's persistent_ram_post_init
	 * does; repair an EMPTY (zapped) zone so our record can lead the stream
	 * until ABL appends after it. A zapped-but-unwritten zone has no ABL
	 * data to clobber. */
	{
		uint32_t sig = laguna_pd_rd32(LAGUNA_CONSOLE_OFF);
		uint32_t start = laguna_pd_rd32(LAGUNA_CONSOLE_OFF + 4u);
		uint32_t size = laguna_pd_rd32(LAGUNA_CONSOLE_OFF + 8u);
		uint32_t bufsz = (uint32_t)(LAGUNA_CONSOLE_SIZE - LAGUNA_PRZ_HDR);

		laguna_pd_console_live = 0;
		if (sig == LAGUNA_PRZ_SIG && start <= bufsz && size <= bufsz) {
			laguna_pd_console_live = 1;
			kprintf("LAGUNA PANICDUMP: console zone sig ok, start=%u size=%u\n",
			    start, size);
		} else if ((sig == 0 || sig == LAGUNA_PRZ_SIG) && start == 0 && size == 0) {
			/* ABL erased the zone (fresh/zeroed header): seed a valid
			 * empty header; Linux accepts an empty existing buffer. */
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF, LAGUNA_PRZ_SIG);
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF + 4u, 0);
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF + 8u, 0);
			__asm__ volatile("dsb sy" ::: "memory");
			laguna_pd_console_live = 1;
			kprintf("LAGUNA PANICDUMP: console zone seeded (was sig=%08x)\n", sig);
		} else {
			kprintf("LAGUNA PANICDUMP: console zone invalid (sig=%08x start=%u size=%u) — pmsg mirror only\n",
			    sig, start, size);
		}
	}

	laguna_pd_enabled = 1;
	{
		long lwrc = laguna_panicdump_stage(LW_STAGE_XNU_INIT,
		    LW_PRODUCER_XNU_KERNEL, 0, LAGUNA_RAMOOPS_BASE,
		    LAGUNA_RAMOOPS_SIZE, 0, 0, "laguna_panicdump_init armed");
		if (lwrc < 0) {
			kprintf("LAGUNA LASTWORDS: XNU_INIT write failed rc=%ld\n", lwrc);
		}
	}
	kprintf("LAGUNA PANICDUMP: armed (console=%s mirror@pmsg+0x%x)\n",
	    laguna_pd_console_live ? "live" : "off",
	    (uint32_t)LAGUNA_MIRROR_OFF);

	(void)PE_parse_boot_argn("lagunapanictest", &test_arg,
	    (int)sizeof(test_arg));
	laguna_pd_test_secs = test_arg;
	if (test_arg > 0) {
		kprintf("LAGUNA PANICTEST: %u s (armed at bsdinit_task, before load_init_program)\n",
		    test_arg);
	}
}

/*
 * Called from the first laguna IOKit driver start (AppleKeyStore::start,
 * iokit/Kernel/arm/LagunaKeyStore.cpp): the earliest fully-threaded hook on
 * every delivery path. Spawns the lagunapanictest panic needle; idempotent
 * (first caller wins).
 */
void
laguna_panicdump_bootdone(void)
{
	thread_t thrd = THREAD_NULL;
	uint32_t secs = laguna_pd_test_secs;

	/* The periodic source-metadata thread is independent of the panic test
	 * and of lagunapanicdump; direct per-byte mirroring is already live. */
	laguna_logdump_start();
	if (!laguna_pd_enabled || secs == 0) {
		return;
	}
	laguna_pd_test_secs = 0;
	if (kernel_thread_start_priority(laguna_panicdump_test_thread,
	    (void *)(uintptr_t)secs, -1, &thrd) == KERN_SUCCESS) {
		thread_deallocate(thrd);
		kprintf("LAGUNA PANICTEST: armed, panicking in %u s\n", secs);
	} else {
		kprintf("LAGUNA PANICTEST: kernel_thread_start_priority failed — no forced panic\n");
	}
}

/* ---- panic capture (called from SavePanicInfo, panic context) ---- */

void
laguna_panicdump_capture(const char *msg, const char *paniclog,
    vm_size_t paniclog_len)
{
	uint32_t plen;

	/* R4: BOTH rungs refresh the independent msgbuf mirror before the 0123
	 * enable check and before the capture-size dedup. This also covers the
	 * lagunapanicdump=0 + lagunalogdump=1 configuration. */
	laguna_logdump_flush(LAGUNA_LOGDUMP_FLUSH_PANIC);
	if (!laguna_pd_enabled) {
		return;
	}
	plen = (uint32_t)paniclog_len;
	if (paniclog == NULL) {
		plen = 0;
	} else if (plen > LAGUNA_CAP_PANICLOG_MAX) {
		plen = LAGUNA_CAP_PANICLOG_MAX;
	}

	/*
	 * Capture ladder: rung 1 (debugger_collect_diagnostics) fires with
	 * just the panic string, rung 2 (SavePanicInfo, after
	 * print_all_backtraces) fires with the full paniclog. A later rung
	 * re-captures only when it has MORE text — the panic output can only
	 * grow between the two sites, so this is exactly "replace the stub
	 * with the full record". If the fuller rung aborts before its
	 * capture, the earlier rung's record stands.
	 */
	if (laguna_pd_captured && plen <= laguna_pd_captured_len) {
		return;
	}
	/* Commit PANIC before source validation, msgbuf staging, or either long
	 * text writer.  A nested abort therefore still leaves the exact stage;
	 * PAN1/console remain the authoritative panic-text channels. */
	(void)laguna_panicdump_stage(LW_STAGE_PANIC, LW_PRODUCER_XNU_PANIC,
	    LW_FLAG_FAILURE, plen, 0, 0, 0, "panic capture rung");
	laguna_pd_captured = 1;
	laguna_pd_captured_len = plen;

	/*
	 * 0124: validate the copy SOURCES before any consumer runs. The msg
	 * pointer and the paniclog span are kernel-managed (and the paniclog
	 * was written through before we got here), but the capture must never
	 * be the first dereference of a bad translation: a fault here tears
	 * the record mid-write. panic_validate_ptr/ml_validate_nofault are the
	 * sanctioned panic-path guards (panic_validate_ptr uses exactly this).
	 */
	if (msg != NULL &&
	    !ml_validate_nofault((vm_offset_t)msg, 1)) {
		msg = NULL;
	}
	if (paniclog != NULL &&
	    !ml_validate_nofault((vm_offset_t)paniclog, plen)) {
		plen = 0;   /* record continues; mirror/console get message only */
	}

	/* pmsg mirror first: the guaranteed-survival channel (console zone
	 * content is subject to ABL's own pstore erase policy). */
	laguna_pd_mirror_write(msg, paniclog, plen);

	/* console stream: native persistent_ram append — shows up in
	 * console-ramoops-0 right after ABL's log. */
	laguna_pd_console_str("\n\n----- XNU-PANIC-BEGIN (laguna141 xnu-8796.141.3) -----\n");
	laguna_pd_console_str("panic message: ");
	laguna_pd_console_str(msg != NULL ? msg : "(null)");
	laguna_pd_console_str("\n");
	if (plen > 0) {
		laguna_pd_console_append(paniclog, plen);
		laguna_pd_console_str("\n");
	}

	/*
	 * kernel msgbuf tail (the pre-panic console log): the newest bytes of
	 * the BSD ring. 0124 hardening — every pointer is
	 * panic_validate_ptr'd BEFORE dereference and the ring itself is
	 * staged with ml_nofault_copy (stops cleanly at an unmapped page,
	 * returns the byte count), so a bad/relocated msg_bufc can no longer
	 * abort the capture mid-record (the QEMU evidence showed exactly that
	 * nested abort, frames attributed to this function). A short copy is
	 * appended with an explicit note; the record always reaches END.
	 */
	{
		if (msgbufp != NULL &&
		    panic_validate_ptr(msgbufp,
		    (vm_size_t)sizeof(struct msgbuf), "msgbufp") &&
		    msgbufp->msg_magic == 0x063061 &&
		    msgbufp->msg_size > 0 && msgbufp->msg_size <= (int)0x100000 &&
		    panic_validate_ptr(msgbufp->msg_bufc,
		    (vm_size_t)msgbufp->msg_size, "msgbuf ring")) {
			uint32_t bufsz = (uint32_t)msgbufp->msg_size;
			uint32_t bufx = (uint32_t)msgbufp->msg_bufx;
			uint32_t n = LAGUNA_CAP_MSGBUF_MAX;

			if (bufx > bufsz) {
				bufx = bufsz;   /* torn index: clamp, don't fault */
			}
			if (n > bufsz) {
				n = bufsz;
			}
			{
				char hdr[96];
				uint32_t hn = 0;
				static const char tag[] =
				    "\n----- msgbuf tail (last ";
				static const char tail[] = " bytes) -----\n";
				char num[12];
				int v = (int)n, k = 0;

				while (tag[hn] != '\0') {
					hdr[hn] = tag[hn];
					hn++;
				}
				while (v > 0) {
					num[k++] = (char)('0' + (v % 10));
					v /= 10;
				}
				while (k > 0) {
					hdr[hn++] = num[--k];
				}
				for (uint32_t j = 0; tail[j] != '\0'; j++) {
					hdr[hn++] = tail[j];
				}
				laguna_pd_console_append(hdr, hn);
			}

			/* 0124: stage the ring tail with ml_nofault_copy —
			 * the copy stops at an unmapped page and returns how
			 * far it got; nothing here can take an abort. The two
			 * spans (tail-of-ring then wrap-to-front) are staged
			 * in capture order so the append stays ring-aware. */
			uint32_t from = (bufx + bufsz - n) % bufsz;
			uint32_t lead = bufsz - from;
			vm_size_t got = 0;

			if (lead > n) {
				lead = n;
			}
			got = ml_nofault_copy(
			    (vm_offset_t)(msgbufp->msg_bufc + from),
			    (vm_offset_t)laguna_pd_mb_stage, lead);
			if (got < lead) {
				lead = (uint32_t)got;
				n = lead;   /* first span already short: stop */
			} else if (lead < n) {
				vm_size_t got2 = ml_nofault_copy(
				    (vm_offset_t)msgbufp->msg_bufc,
				    (vm_offset_t)(laguna_pd_mb_stage + lead),
				    n - lead);
				n = lead + (uint32_t)got2;
			}
			if (n > 0) {
				laguna_pd_console_append(
				    (const char *)laguna_pd_mb_stage, n);
			}
			if (n < LAGUNA_CAP_MSGBUF_MAX && n != bufsz) {
				/* staged less than requested: say so, in-band,
				 * instead of leaving the tail silently short */
				static const char shortmsg[] =
				    "\n----- msgbuf staged short (source "
				    "validated out; record continues) -----\n";
				laguna_pd_console_str(shortmsg);
			}
		} else {
			laguna_pd_console_str(
			    "----- msgbuf unavailable (validated out) -----\n");
		}
	}

	laguna_pd_console_str("----- XNU-PANIC-END -----\n");
}

/* ---- test needle ---- */

static void
laguna_panicdump_test_thread(void *arg, int wait_result __unused)
{
	uint32_t secs = (uint32_t)(uintptr_t)arg;

	thread_set_thread_name(current_thread(), "laguna-panicdump-test");
	kprintf("LAGUNA PANICTEST: panicking in %u s\n", secs);
	delay_for_interval(secs, NSEC_PER_SEC);
	panic("lagunapanictest: forced panic for panic-capture validation");
	/* not reached */
}

#else /* non-LAGUNA arm64 build stubs (the ramoops zone is laguna-only) */

void
laguna_panicdump_init(void)
{
}

void
laguna_panicdump_capture(
    const char *msg __unused,
    const char *paniclog __unused,
    vm_size_t paniclog_len __unused)
{
}

long
laguna_panicdump_stage(uint32_t stage __unused, uint32_t producer __unused,
    uint32_t flags __unused, uint64_t detail0 __unused,
    uint64_t detail1 __unused, uint64_t detail2 __unused,
    uint64_t detail3 __unused, const char *note __unused)
{
	return 0;
}

void
laguna_panicdump_bootdone(void)
{
}

void
laguna_logdump_early_init(void)
{
}

void
laguna_logdump_late_init(void)
{
}

void
laguna_logdump_putc(char c __unused, uint32_t bufx __unused)
{
}

void
laguna_logdump_flush(uint32_t reason __unused)
{
}

#endif /* LAGUNA */
