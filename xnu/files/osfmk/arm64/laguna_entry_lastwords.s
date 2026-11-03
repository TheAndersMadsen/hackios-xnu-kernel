/* R4 gated XNU_ENTRY LGLW writer — position-independent AArch64.
 *
 * Called by start_first_cpu after patch-xnu-marker.py has performed the XNH0
 * handoff, but before MMU/caches are enabled. x0 is final boot_args PA (or the
 * original direct-shim value). The old marker supplies the true incoming x0.
 *
 * lagunalogdump defaults ON. A complete, token-bound lagunalogdump=0 argument
 * suppresses this record; C-side mirror/stages use the same gate. The command
 * line scan is bounded to boot_args.CommandLine[608] at offset 108.
 *
 * Record ABI is exactly research/laguna-ramdump/producer/STAMPS.md: alternating
 * 128-byte LGLW slots, CRC-32/IEEE over [0,120), DONE committed last. Physical
 * stores + DSB SY make the record visible to a warm-reset raw RAM dump.
 * Clobbers x9..x17 and flags; x0 is restored to final boot_args, x1..x8/lr
 * are preserved. No stack, MMU, caches, C runtime, or external data is used.
 */
#include <arm64/asm.h>

.equ LW_SLOT0,        0x95401100
.equ LW_SLOT1,        0x95401180
.equ MARKER_SLOT,     0x95400100
.equ MARKER_MAGIC,    0x5a5a5a5a
/* XNU-MSGBUF v2: the compact trail moved out of the empirically hostile low
 * pmsg region (TEE fastboot-re-entry log + handoff page + probe log cover
 * +0x000..~0x5C80 on every retrieved dump). pmsg+0x28000 is remnant-quiet.
 * The CRC/DONE LGLW slots stay at the frozen R3-shared ABI (0x95401100/80). */
.equ COMPACT_SLOT,    0x95428000
.equ COMPACT_WORDS,   12
.equ LGLW_MAGIC,      0x574c474c
.equ LW_COMMIT,       0x454e4f44
.equ LW_VERSION_SIZE, 0x00800001       /* version=1, bytes=128 */
.equ LW_STAGE,        6                /* XNU_ENTRY */
.equ LW_PRODUCER,     4                /* XNU_STUB */
.equ BA_COMMANDLINE,  108
.equ BOOT_LINE_LENGTH,608
.equ DISABLE_LEN,     15               /* "lagunalogdump=0" */

.text
.balign 8
.globl EXT(laguna_logdump_entry)
LEXT(laguna_logdump_entry)
	mov     x13, x0                   /* final boot_args PA */

	/* Recover the true incoming x0 from the already-executed old marker. */
	movz    x17, #(MARKER_SLOT & 0xffff)
	movk    x17, #((MARKER_SLOT >> 16) & 0xffff), lsl #16
	ldr     w16, [x17]
	movz    w15, #(MARKER_MAGIC & 0xffff)
	movk    w15, #((MARKER_MAGIC >> 16) & 0xffff), lsl #16
	cmp     w16, w15
	b.ne    1f
	ldr     x14, [x17, #8]
	b       2f
1:
	mov     x14, x13                  /* unpatched/direct entry: same x0 */
2:

	/* Clear only the 48-byte compact trail at its v2 location, preserving
	 * M1/boot/DIAG counters (and the relocated v2 counters, which persist
	 * across boots). Disabled boots therefore cannot leak a prior boot's
	 * compact stage. */
	movz    x15, #(COMPACT_SLOT & 0xffff)
	movk    x15, #((COMPACT_SLOT >> 16) & 0xffff), lsl #16
	mov     w16, #(COMPACT_WORDS / 2)
Llaguna_ld_clear_compact:
	str     xzr, [x15], #8
	subs    w16, w16, #1
	b.ne    Llaguna_ld_clear_compact
	dsb     sy

	/* Gate the earliest stamp. No boot_args means the default (ON). */
	cbz     x13, Llaguna_ld_gate_done
	add     x9, x13, #BA_COMMANDLINE
	mov     x17, x9                   /* command-line base */
	add     x10, x9, #(BOOT_LINE_LENGTH - DISABLE_LEN)
Llaguna_ld_find_disable:
	ldrb    w11, [x9]
	cmp     w11, #'l'
	b.ne    Llaguna_ld_next
	cmp     x9, x17
	b.eq    Llaguna_ld_candidate
	ldrb    w11, [x9, #-1]
	cmp     w11, #' '
	b.ne    Llaguna_ld_next
Llaguna_ld_candidate:
	mov     x12, x9
	adr     x15, Llaguna_ld_disable_token
Llaguna_ld_compare:
	ldrb    w16, [x15], #1
	cbz     w16, Llaguna_ld_token_tail
	ldrb    w11, [x12], #1
	cmp     w11, w16
	b.ne    Llaguna_ld_next
	b       Llaguna_ld_compare
Llaguna_ld_token_tail:
	/* Require a token boundary without reading past CommandLine[608]. */
	add     x16, x17, #BOOT_LINE_LENGTH
	cmp     x12, x16
	b.eq    Llaguna_ld_disabled
	ldrb    w16, [x12]
	cbz     w16, Llaguna_ld_disabled
	cmp     w16, #' '
	b.eq    Llaguna_ld_disabled
Llaguna_ld_next:
	add     x9, x9, #1
	cmp     x9, x10
	b.ls    Llaguna_ld_find_disable
Llaguna_ld_gate_done:

	movz    x9, #(LW_SLOT0 & 0xffff)
	movk    x9, #((LW_SLOT0 >> 16) & 0xffff), lsl #16

	/* Sanitize both seq words: [0,0xfffffffd] only. */
	ldr     w10, [x9, #8]
	ldr     w11, [x9, #0x88]
	movn    w15, #1                   /* 0xfffffffe */
	cmp     w10, w15
	csel    w10, wzr, w10, hs
	cmp     w11, w15
	csel    w11, wzr, w11, hs
	cmp     w10, w11
	csel    w12, w10, w11, hi

	movn    w15, #2                   /* 0xfffffffd = sequence limit */
	cmp     w12, w15
	b.eq    Llaguna_ld_generation_reset
	add     w12, w12, #1
	mov     x10, x9                   /* odd next -> slot 0 */
	tbz     w12, #0, Llaguna_ld_select_slot1
	b       Llaguna_ld_slot_selected
Llaguna_ld_select_slot1:
	add     x10, x9, #(LW_SLOT1 - LW_SLOT0)
	b       Llaguna_ld_slot_selected

Llaguna_ld_generation_reset:
	str     wzr, [x9, #0x7c]
	str     wzr, [x9, #0xfc]
	dsb     sy
	mov     w12, #1
	mov     x10, x9

Llaguna_ld_slot_selected:
	/* Invalidate first; the other slot survives any torn update. */
	str     wzr, [x10, #0x7c]
	dsb     sy
	mov     x15, x10
	mov     w16, #16
Llaguna_ld_zero_record:
	str     xzr, [x15], #8
	subs    w16, w16, #1
	b.ne    Llaguna_ld_zero_record

	movz    w15, #(LGLW_MAGIC & 0xffff)
	movk    w15, #((LGLW_MAGIC >> 16) & 0xffff), lsl #16
	str     w15, [x10, #0x00]
	movz    w15, #(LW_VERSION_SIZE & 0xffff)
	movk    w15, #((LW_VERSION_SIZE >> 16) & 0xffff), lsl #16
	str     w15, [x10, #0x04]
	str     w12, [x10, #0x08]
	mov     w15, #LW_STAGE
	str     w15, [x10, #0x0c]
	mov     w15, #LW_PRODUCER
	str     w15, [x10, #0x10]
	str     x14, [x10, #0x18]         /* incoming x0 */
	str     x13, [x10, #0x20]         /* final boot_args PA */

	/* CRC-32/IEEE bytes [0,120), two table-driven nibbles per byte. */
	movn    w12, #0
	adr     x15, Llaguna_ld_crc_nibble_table
	mov     x16, x10
	add     x17, x10, #120
Llaguna_ld_crc_loop:
	ldrb    w11, [x16], #1
	eor     w12, w12, w11
	and     w11, w12, #0xf
	ldr     w11, [x15, w11, uxtw #2]
	eor     w12, w11, w12, lsr #4
	and     w11, w12, #0xf
	ldr     w11, [x15, w11, uxtw #2]
	eor     w12, w11, w12, lsr #4
	cmp     x16, x17
	b.ne    Llaguna_ld_crc_loop
	mvn     w12, w12
	str     w12, [x10, #0x78]
	dsb     sy

	movz    w15, #(LW_COMMIT & 0xffff)
	movk    w15, #((LW_COMMIT >> 16) & 0xffff), lsl #16
	str     w15, [x10, #0x7c]
	dsb     sy

	/* Independent compact breadcrumb at pmsg+0x28000, R3 stage XNU_ENTRY. */
	movz    x15, #(COMPACT_SLOT & 0xffff)
	movk    x15, #((COMPACT_SLOT >> 16) & 0xffff), lsl #16
	mov     w16, #LW_STAGE
	str     w16, [x15]
	dsb     sy
Llaguna_ld_disabled:
	mov     x0, x13                   /* preserve final boot_args for start.s */
	ret

.balign 4
Llaguna_ld_disable_token:
	.asciz  "lagunalogdump=0"

.balign 8
Llaguna_ld_crc_nibble_table:
	.word   0x00000000, 0x1db71064, 0x3b6e20c8, 0x26d930ac
	.word   0x76dc4190, 0x6b6b51f4, 0x4db26158, 0x5005713c
	.word   0xedb88320, 0xf00f9344, 0xd6d6a3e8, 0xcb61b38c
	.word   0x9b64c2b0, 0x86d3d2d4, 0xa00ae278, 0xbdbdf21c
