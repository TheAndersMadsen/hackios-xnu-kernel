/* LAGUNA LAST WORDS v1 — fixed-PA, crash-consistent boot-stage records.
 *
 * Canonical producer/reader contract: research/laguna-ramdump/producer/STAMPS.md.
 * Freestanding-safe: stdint.h only, no allocation, no locks, no libc calls.
 * All stored fields are little-endian (laguna and the host tests are LE).
 *
 * Physical layout in the reserved ramoops pmsg aperture:
 *   slot 0  PA 0x95401100, slot 1  PA 0x95401180, 128 bytes each.
 * This pmsg+0x1100..0x11ff window is after MARKER-SCRATCH (+0x1000),
 * its boot counter (+0x1040), and DIAG's wrap counter (+0x1080), and before
 * DIAG's records (+0x2000).  It never touches the persistent_ram header.
 *
 * The writer alternates slots.  It invalidates the target's commit word,
 * writes data + CRC with commit=0, cleans to the PoC, then commits the final
 * word last.  The other slot remains the last complete record after a torn
 * update.  Sequence numbers do not wrap: implausible/erased values are
 * ignored, and exhaustion invalidates both slots before restarting at 1.
 */
#ifndef LAGUNA_LASTWORDS_H
#define LAGUNA_LASTWORDS_H

#include <stdint.h>

#define LW_PMSG_BASE       0x95400000ULL
#define LW_RECORDS_PA      0x95401100ULL
#define LW_RECORD_SIZE     128u
#define LW_SLOT_COUNT      2u
#define LW_WINDOW_SIZE     (LW_RECORD_SIZE * LW_SLOT_COUNT)
#define LW_SLOT_STRIDE     LW_RECORD_SIZE
#define LW_SLOT_PA(slot)   (LW_RECORDS_PA + (uint64_t)(slot) * LW_SLOT_STRIDE)

#define LW_MAGIC           0x574c474cu /* bytes "LGLW" */
#define LW_COMMIT          0x454e4f44u /* bytes "DONE"; written last */
#define LW_VERSION         1u
#define LW_SEQ_MAX         0xfffffffdu /* 0 and 0xfffffffe/ffffffff are erased */
#define LW_CRC_LEN         120u        /* bytes [0,120): excludes crc+commit */
#define LW_NOTE_SIZE       64u

/* Stage values are durable ABI.  ABL_SEEN is reserved for an earlier-stage
 * producer; this Linux/XNU ladder does not emit it retroactively. */
#define LW_STAGE_ABL_SEEN          1u
#define LW_STAGE_KERNEL_UP         2u
#define LW_STAGE_PROBE_START       3u
#define LW_STAGE_ROOT_STAGED       4u
#define LW_STAGE_KEXEC_DONE        5u
#define LW_STAGE_XNU_ENTRY         6u
#define LW_STAGE_XNU_INIT          7u
#define LW_STAGE_ROOT_MOUNTED      8u
#define LW_STAGE_LAUNCHD_EXEC      9u
#define LW_STAGE_DYLD_STAGE       10u
#define LW_STAGE_DYLD_SHARED_REGION 11u
#define LW_STAGE_PANIC            12u

#define LW_PRODUCER_ABL            1u /* reserved; not emitted by this lane */
#define LW_PRODUCER_LINUX_PROBE    2u
#define LW_PRODUCER_KEXEC_LOADER   3u
#define LW_PRODUCER_XNU_STUB       4u
#define LW_PRODUCER_XNU_KERNEL     5u
#define LW_PRODUCER_XNU_PANIC      6u

#define LW_FLAG_ATTEMPT            (1u << 0) /* entered, not completed */
#define LW_FLAG_FAILURE            (1u << 1) /* failure/panic context */

struct lw_record {
	uint32_t magic;          /* +0x00 LW_MAGIC */
	uint16_t version;        /* +0x04 LW_VERSION */
	uint16_t bytes;          /* +0x06 LW_RECORD_SIZE */
	uint32_t seq;            /* +0x08 1-based, non-wrapping */
	uint32_t stage;          /* +0x0c LW_STAGE_* */
	uint32_t producer;       /* +0x10 LW_PRODUCER_* */
	uint32_t flags;          /* +0x14 LW_FLAG_* */
	uint64_t detail[4];      /* +0x18 stage-specific, zero when unused */
	char note[LW_NOTE_SIZE]; /* +0x38 NUL-padded ASCII; may be empty */
	uint32_t crc;            /* +0x78 CRC-32/IEEE of bytes [0,120) */
	uint32_t commit;         /* +0x7c LW_COMMIT, committed last */
};

_Static_assert(sizeof(struct lw_record) == LW_RECORD_SIZE,
	       "LGLW record must be exactly 128 bytes");
_Static_assert(__builtin_offsetof(struct lw_record, detail) == 0x18,
	       "LGLW detail ABI");
_Static_assert(__builtin_offsetof(struct lw_record, note) == 0x38,
	       "LGLW note ABI");
_Static_assert(__builtin_offsetof(struct lw_record, crc) == LW_CRC_LEN,
	       "LGLW CRC ABI");
_Static_assert(LW_RECORDS_PA >= LW_PMSG_BASE + 0x1084 &&
	       LW_RECORDS_PA + LW_WINDOW_SIZE <= LW_PMSG_BASE + 0x2000,
	       "LGLW window must be clear of scratch/counters and DIAG records");
_Static_assert(LW_RECORDS_PA + LW_WINDOW_SIZE <= LW_PMSG_BASE + 0x200000,
	       "LGLW window must remain inside pmsg");

/* Memory backend.  read/write return 0 on success, negative on failure.
 * clean(pa,len) must make the written range visible at the Point of
 * Coherency and execute the required completion barrier before returning.
 * Linux uses DC CVAC + DSB SY on its /dev/mem mapping; XNU's Device-memory
 * mapping needs only DSB SY.  The host mock records this ordering. */
struct lw_mem {
	void *ctx;
	long (*read)(void *ctx, uint64_t pa, void *buf, uint32_t len);
	long (*write)(void *ctx, uint64_t pa, const void *buf, uint32_t len);
	long (*clean)(void *ctx, uint64_t pa, uint32_t len);
};

struct lw_event {
	uint32_t stage;
	uint32_t producer;
	uint32_t flags;
	uint64_t detail[4];
	const char *note; /* optional; bounded to 63 chars + NUL */
};

static inline uint32_t
lw_crc32(const uint8_t *p, uint32_t n)
{
	uint32_t c = 0xffffffffu;
	uint32_t i;
	int b;

	for (i = 0; i < n; i++) {
		c ^= p[i];
		for (b = 0; b < 8; b++)
			c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
	}
	return ~c;
}

static inline int
lw_valid(const struct lw_record *r)
{
	if (!r || r->magic != LW_MAGIC || r->version != LW_VERSION ||
	    r->bytes != LW_RECORD_SIZE || r->seq == 0 ||
	    r->seq > LW_SEQ_MAX || r->stage == 0 || r->producer == 0 ||
	    r->commit != LW_COMMIT)
		return 0;
	return r->crc == lw_crc32((const uint8_t *)r, LW_CRC_LEN);
}

static inline uint32_t
lw_sanitize_seq(uint32_t seq)
{
	return seq == 0 || seq > LW_SEQ_MAX ? 0 : seq;
}

static inline void
lw_encode(struct lw_record *r, uint32_t seq, const struct lw_event *event)
{
	uint32_t i, j;

	for (i = 0; i < LW_RECORD_SIZE; i++)
		((uint8_t *)r)[i] = 0;
	r->magic = LW_MAGIC;
	r->version = LW_VERSION;
	r->bytes = LW_RECORD_SIZE;
	r->seq = seq;
	r->stage = event->stage;
	r->producer = event->producer;
	r->flags = event->flags;
	for (i = 0; i < 4; i++)
		r->detail[i] = event->detail[i];
	if (event->note) {
		/* Keep the final byte NUL.  Notes are static producer text or a
		 * trusted kernel path, never an unchecked panic pointer. */
		for (i = 0; i + 1 < LW_NOTE_SIZE && event->note[i]; i++)
			r->note[i] = event->note[i];
	}
	/* Make padding deterministic even if a future compiler/store path
	 * changes note handling. */
	for (j = i; j < LW_NOTE_SIZE; j++)
		r->note[j] = 0;
	r->crc = lw_crc32((const uint8_t *)r, LW_CRC_LEN);
	r->commit = 0;
}

/* Commit one event.  Error returns are negative and no success is logged by
 * this layer.  A failed update can invalidate the selected older slot, but
 * never the other slot (except the practically unreachable seq-exhaustion
 * reset, which deliberately invalidates both). */
static inline long
lw_write(const struct lw_mem *mem, const struct lw_event *event)
{
	struct lw_record rec;
	uint32_t seq[LW_SLOT_COUNT] = {0, 0};
	uint32_t newest = 0, next, slot, zero = 0, commit = LW_COMMIT;
	uint64_t pa;
	long rc;

	if (!mem || !mem->read || !mem->write || !mem->clean || !event)
		return -1;
	if (!event->stage || !event->producer)
		return -2;
	for (slot = 0; slot < LW_SLOT_COUNT; slot++) {
		rc = mem->read(mem->ctx, LW_SLOT_PA(slot) +
		    __builtin_offsetof(struct lw_record, seq), &seq[slot], 4);
		if (rc < 0)
			return -3;
		seq[slot] = lw_sanitize_seq(seq[slot]);
		if (seq[slot] > newest)
			newest = seq[slot];
	}
	next = newest == LW_SEQ_MAX ? 1 : newest + 1;
	slot = (next - 1) % LW_SLOT_COUNT;

	/* Exhaustion is a deliberate generation reset, not a wrapped sequence. */
	if (newest == LW_SEQ_MAX) {
		uint32_t i;
		for (i = 0; i < LW_SLOT_COUNT; i++) {
			pa = LW_SLOT_PA(i) + __builtin_offsetof(struct lw_record, commit);
			if (mem->write(mem->ctx, pa, &zero, 4) < 0)
				return -4;
			if (mem->clean(mem->ctx, pa, 4) < 0)
				return -5;
		}
	}

	pa = LW_SLOT_PA(slot) + __builtin_offsetof(struct lw_record, commit);
	if (mem->write(mem->ctx, pa, &zero, 4) < 0)
		return -6;
	if (mem->clean(mem->ctx, pa, 4) < 0)
		return -7;

	lw_encode(&rec, next, event);
	pa = LW_SLOT_PA(slot);
	if (mem->write(mem->ctx, pa, &rec, LW_RECORD_SIZE) < 0)
		return -8;
	if (mem->clean(mem->ctx, pa, LW_RECORD_SIZE) < 0)
		return -9;

	pa += __builtin_offsetof(struct lw_record, commit);
	if (mem->write(mem->ctx, pa, &commit, 4) < 0)
		return -10;
	if (mem->clean(mem->ctx, pa, 4) < 0)
		return -11;
	return (long)next;
}

/* Reference read-side primitive for host tests and small producers.  Returns
 * 1 with the highest valid sequence, 0 when neither slot is valid, or a
 * negative error only when neither slot could be read.  Invalid/CRC-bad
 * records are never selected.  R2 owns the full RAM-dump scanner/decoder. */
static inline long
lw_read_latest(const struct lw_mem *mem, struct lw_record *out)
{
	struct lw_record candidate;
	uint32_t slot;
	int have = 0, read_errors = 0;

	if (!mem || !mem->read || !out)
		return -1;
	for (slot = 0; slot < LW_SLOT_COUNT; slot++) {
		if (mem->read(mem->ctx, LW_SLOT_PA(slot), &candidate,
		    LW_RECORD_SIZE) < 0) {
			read_errors++;
			continue;
		}
		if (lw_valid(&candidate) && (!have || candidate.seq > out->seq)) {
			*out = candidate;
			have = 1;
		}
	}
	if (have)
		return 1;
	return read_errors == LW_SLOT_COUNT ? -2 : 0;
}

#endif /* LAGUNA_LASTWORDS_H */
