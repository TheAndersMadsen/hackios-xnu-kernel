/*
 * hakios.blazer_panicdump — panic-path capture into the ramoops console zone.
 * See hakios.blazer_panicdump.h for the contract (device truth, format, boot args).
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
 * the mirror (research/check-pmsg-contract.py guards all of the offsets).
 */
#include <arm64/hakios.blazer_panicdump.h>

#include <stdint.h>

#include <pexpert/pexpert.h>           /* kprintf, PE_parse_boot_argn */
#include <machine/machine_routines.h>  /* ml_io_map, ml_validate_nofault,
                                        * ml_nofault_copy (0124) */
#include <kern/thread.h>               /* kernel_thread_start_priority */
#include <kern/clock.h>                /* delay_for_interval (test needle) */
#include <kern/debug.h>                /* panic_validate_ptr (0124) */

/* ---- placement constants (guarded by research/check-pmsg-contract.py) ---- */

/* Defaults from /reserved-memory/ramoops@95200000. tools/tensor-args.py
 * regenerates the corresponding blazer_* boot-args from the live FDT. The
 * mapping and all writes remain disabled unless blazer_panicdump=1. */
static uint64_t blazer_pd_ramoops_base = 0x95200000ull;
static uint64_t blazer_pd_ramoops_size = 0x400000ull;
static uint64_t blazer_pd_console_size = 0x200000ull;
static uint64_t blazer_pd_pmsg_base = 0x95400000ull;
static uint64_t blazer_pd_pmsg_size = 0x200000ull;

#define LAGUNA_RAMOOPS_BASE       blazer_pd_ramoops_base
#define LAGUNA_RAMOOPS_SIZE       blazer_pd_ramoops_size
#define LAGUNA_CONSOLE_OFF        0x0ull
#define LAGUNA_CONSOLE_SIZE       blazer_pd_console_size
#define LAGUNA_PMSG_OFF           (blazer_pd_pmsg_base - blazer_pd_ramoops_base)
#define LAGUNA_PMSG_SIZE          blazer_pd_pmsg_size

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

_Static_assert(LAGUNA_MIRROR_SIZE >= LAGUNA_MMR_DATA_OFF + LAGUNA_MIRROR_PAYLOAD_MAX,
    "mirror window too small for the payload cap");
_Static_assert(LAGUNA_MIRROR_OFF >= 0x12000ull,
    "mirror window overlaps the diag-ring/boot-args placements");
/* pmsg/window containment is checked against the boot-arg values at init. */

/* ---- zone state (init-time, read-only afterwards) ---- */

static volatile uint8_t *laguna_pd_va;      /* ml_io_map of the 4 MiB zone */
static volatile uint32_t laguna_pd_enabled; /* boot-arg gate passed + mapped */
static volatile uint32_t laguna_pd_captured;/* a capture already ran */
static volatile uint32_t laguna_pd_captured_len; /* its paniclog size */
static volatile uint32_t laguna_pd_console_live; /* console header appendable */
static uint32_t laguna_pd_test_secs;        /* blazer_panictest value (bootdone) */

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

static void hakios_blazer_panicdump_test_thread(void *arg, int wait_result);

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
hakios_blazer_panicdump_init(void)
{
	uint32_t dump_arg = 0;   /* explicit opt-in: other boards never map this PA */
	uint32_t test_arg = 0;   /* default off */

	if (!PE_parse_boot_argn("blazer_panicdump", &dump_arg,
	    (int)sizeof(dump_arg)) || dump_arg == 0) {
		return;
	}
	PE_parse_boot_argn("blazer_ramoops_base", &blazer_pd_ramoops_base,
	    sizeof(blazer_pd_ramoops_base));
	PE_parse_boot_argn("blazer_ramoops_size", &blazer_pd_ramoops_size,
	    sizeof(blazer_pd_ramoops_size));
	PE_parse_boot_argn("blazer_console_size", &blazer_pd_console_size,
	    sizeof(blazer_pd_console_size));
	PE_parse_boot_argn("blazer_pmsg_base", &blazer_pd_pmsg_base,
	    sizeof(blazer_pd_pmsg_base));
	PE_parse_boot_argn("blazer_pmsg_size", &blazer_pd_pmsg_size,
	    sizeof(blazer_pd_pmsg_size));

	if (blazer_pd_ramoops_base == 0 || blazer_pd_ramoops_size < LAGUNA_PRZ_HDR ||
	    blazer_pd_console_size < LAGUNA_PRZ_HDR ||
	    blazer_pd_console_size > blazer_pd_ramoops_size ||
	    blazer_pd_pmsg_base < blazer_pd_ramoops_base ||
	    LAGUNA_PMSG_OFF > blazer_pd_ramoops_size ||
	    blazer_pd_console_size > LAGUNA_PMSG_OFF ||
	    blazer_pd_pmsg_size > blazer_pd_ramoops_size - LAGUNA_PMSG_OFF ||
	    blazer_pd_pmsg_size < LAGUNA_MIRROR_OFF + LAGUNA_MIRROR_SIZE) {
		kprintf("blazer-panicdump: invalid ramoops/pmsg geometry; capture disabled\n");
		return;
	}

	/* The physmap does not cover the low bank (device: declared-clean
	 * RAM is 0x8_80000000+; QEMU contract: 0x80000000+256 MiB ends at
	 * 0x90000000), so the reserved zone needs its own Device mapping. */
	laguna_pd_va = (volatile uint8_t *)(uintptr_t)ml_io_map(
	    (vm_offset_t)LAGUNA_RAMOOPS_BASE, (vm_size_t)LAGUNA_RAMOOPS_SIZE);
	if (laguna_pd_va == NULL) {
		kprintf("blazer-panicdump: ml_io_map of ramoops failed — capture unavailable\n");
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
			kprintf("blazer-panicdump: console zone sig ok, start=%u size=%u\n",
			    start, size);
		} else if ((sig == 0 || sig == LAGUNA_PRZ_SIG) && start == 0 && size == 0) {
			/* ABL erased the zone (fresh/zeroed header): seed a valid
			 * empty header; Linux accepts an empty existing buffer. */
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF, LAGUNA_PRZ_SIG);
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF + 4u, 0);
			laguna_pd_wr32(LAGUNA_CONSOLE_OFF + 8u, 0);
			__asm__ volatile("dsb sy" ::: "memory");
			laguna_pd_console_live = 1;
			kprintf("blazer-panicdump: console zone seeded (was sig=%08x)\n", sig);
		} else {
			kprintf("blazer-panicdump: console zone invalid (sig=%08x start=%u size=%u) — pmsg mirror only\n",
			    sig, start, size);
		}
	}

	laguna_pd_enabled = 1;
	kprintf("blazer-panicdump: armed (console=%s mirror@pmsg+0x%x)\n",
	    laguna_pd_console_live ? "live" : "off",
	    (uint32_t)LAGUNA_MIRROR_OFF);

	(void)PE_parse_boot_argn("blazer_panictest", &test_arg,
	    (int)sizeof(test_arg));
	laguna_pd_test_secs = test_arg;
	if (test_arg > 0) {
		kprintf("blazer-panictest: %u s (armed at bsdinit_task, before load_init_program)\n",
		    test_arg);
	}
}

/*
 * Called from the first laguna IOKit driver start (AppleKeyStore::start,
 * iokit/Kernel/arm/LagunaKeyStore.cpp): the earliest fully-threaded hook on
 * every delivery path. Spawns the blazer_panictest panic needle; idempotent
 * (first caller wins).
 */
void
hakios_blazer_panicdump_bootdone(void)
{
	thread_t thrd = THREAD_NULL;
	uint32_t secs = laguna_pd_test_secs;

	if (!laguna_pd_enabled || secs == 0) {
		return;
	}
	laguna_pd_test_secs = 0;
	if (kernel_thread_start_priority(hakios_blazer_panicdump_test_thread,
	    (void *)(uintptr_t)secs, -1, &thrd) == KERN_SUCCESS) {
		thread_deallocate(thrd);
		kprintf("blazer-panictest: armed, panicking in %u s\n", secs);
	} else {
		kprintf("blazer-panictest: kernel_thread_start_priority failed — no forced panic\n");
	}
}

/* ---- panic capture (called from SavePanicInfo, panic context) ---- */

void
hakios_blazer_panicdump_capture(const char *msg, const char *paniclog,
    vm_size_t paniclog_len)
{
	uint32_t plen;

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
	laguna_pd_console_str("\n\n----- XNU-PANIC-BEGIN (blazer xnu-8796.121.2) -----\n");
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
		struct laguna_msgbuf_mirror {
			int magic, size, bufx, bufr;
			char *bufc;
		};
		extern struct laguna_msgbuf_mirror *msgbufp;

		if (msgbufp != NULL &&
		    panic_validate_ptr(msgbufp,
		    (vm_size_t)sizeof(struct laguna_msgbuf_mirror), "msgbufp") &&
		    msgbufp->magic == 0x063061 &&
		    msgbufp->size > 0 && msgbufp->size <= (int)0x100000 &&
		    panic_validate_ptr(msgbufp->bufc,
		    (vm_size_t)msgbufp->size, "msgbuf ring")) {
			uint32_t bufsz = (uint32_t)msgbufp->size;
			uint32_t bufx = (uint32_t)msgbufp->bufx;
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
			    (vm_offset_t)(msgbufp->bufc + from),
			    (vm_offset_t)laguna_pd_mb_stage, lead);
			if (got < lead) {
				lead = (uint32_t)got;
				n = lead;   /* first span already short: stop */
			} else if (lead < n) {
				vm_size_t got2 = ml_nofault_copy(
				    (vm_offset_t)msgbufp->bufc,
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
hakios_blazer_panicdump_test_thread(void *arg, int wait_result __unused)
{
	uint32_t secs = (uint32_t)(uintptr_t)arg;

	thread_set_thread_name(current_thread(), "laguna-panicdump-test");
	kprintf("blazer-panictest: panicking in %u s\n", secs);
	delay_for_interval(secs, NSEC_PER_SEC);
	panic("blazer_panictest: forced panic for panic-capture validation");
	/* not reached */
}

