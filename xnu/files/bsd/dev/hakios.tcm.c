/*
 * The touch screen of the Pixel 10 Pro (blazer / laguna): a Synaptics
 * TouchComm (TCM) controller ("touchscreen@0", compatible "synaptics,tcm-spi")
 * behind the SoC's DesignWare SSI at spi@db31000, as a source of touches for
 * /dev/hakios-touch (sys/hakios.input.h). Only with boot-arg blazer_touch=1:
 * every address below belongs to this phone, and nothing here runs, or is even
 * mapped, without it.
 *
 * The file is two halves. The protocol core is hardware-free: message framing
 * (TouchComm v1 and v2), the identify / application-info / report-config
 * handshake, the configuration-driven touch report parser, and the finger
 * state that turns reports into hakios.input.h samples, all through struct
 * hakios_tcm_bus (bsd/dev/hakios.tcm.h), which the host test
 * (research/laguna-touch/tcm-test) replaces with a fake chip. The rest is the
 * bus: the SSI register logic through a range-checked io table, the AOC
 * attention pad, and the kernel glue (mapping, boot args, the one polling
 * thread).
 *
 * SoC-specific pieces, all from the live device tree (devices/blazer/fdt/
 * laguna.dtb, the same FDT hardware/bootchain-20261022/live-fdt.dtb carried)
 * and /proc/iomem of the same build, with the probes' findings:
 *   SPI     "lsion_cli20_int3_spi" spi@db31000, reg window 0x0db33000+0x100,
 *           compatible "google,spi": a Synopsys SSI (the DT properties are the
 *           spi-dw ones — "ssi_clk"/"pclk" clocks, rx-sample-delay-ns), chip
 *           on CS 0, mode 0 (synaptics,spi-mode = 0), spi-max-frequency
 *           20 MHz (0x1312d00), rx-sample-delay-ns 25. The probe lane's
 *           verified table agrees (touch SSI 0x0db33000). The FDT's clocks
 *           point into the LSIO clock controller; there is no clock framework
 *           here, so the driver inherits the running configuration the boot
 *           loader handed over (the touch screen works in fastboot, so the
 *           rails, the pads, the ssi_clk and the pclk are on) and only
 *           reprograms framing and the baud divider — keeping the divider the
 *           stock kernel left unless boot-arg touch_baudr= names one.
 *   ATTN    synaptics,irq-gpio = <aoc_pinctrl 0x41 1>: pad 0x41 of
 *           pinctrl@B400000 (gpio-line-names[0x41] = "TS0_2_AOC_INT_L_1V2"),
 *           active low, in the pad warehouse window 0x0b400000+0x5b000 (with
 *           the interrupt window 0x052b9000+0x12000 beside it; iomem names
 *           both for b400000.pinctrl; the probe lane saw attention-line
 *           activity at 0x052c9010, inside that second window). The pad
 *           warehouse layout is the one part of this driver no public source
 *           pins down: the window size is exactly 0x5b * 0x1000, so the
 *           default guess is one 0x1000 slot per pad with the data register
 *           at +4, overridable with touch_attn_slot=/touch_attn_off= and
 *           switchable off with touch_attn_mode=1 (the core then reads blind,
 *           the tegu driver's fallback). The first four words of the pad's
 *           slot are logged at bring-up for the first boot on hardware.
 *           NEEDS-DEVICE-PROBE.
 *   reset   synaptics,reset-gpio = <da9188_gpio 0x11 1>: GPIO 17 of the
 *           da9188 PMIC GPIO controller ("PMICS2TS_RESET_L_1V2" in its
 *           gpio-line-names), which is driven through the goog_mba_cpm_iface
 *           AOC mailbox (mba-dest-channel 0x0a — bsd/dev/hakios.blazer_cpm.c's
 *           transport, once a CPM client of it is learned) — the same
 *           firmware-mediated path as the TS rails L26S_TS_AVDD (3.3 V) and
 *           L27S_TS_DVDD (1.8 V). Without that request format the line cannot
 *           be pulled, so bus.reset() says so and the driver stands on what
 *           the boot loader left (the FDT's own timings are in the core's
 *           hard reset for the day the mailbox is learned).
 *           NEEDS-DEVICE-PROBE.
 *   TBN     the stock stack arbitrates this SPI bus through the AOC touch
 *           bus negotiator (goog,tbn-enabled; touch_bus_negotiator +
 *           aoc_tbn_service_dev in /proc/modules). If ABL hands the bus
 *           parked, every transfer below times out and the log says so;
 *           whether it does is the first thing to check on hardware.
 *
 * The register map is drivers/spi/spi-dw.h of the mainline kernel
 * (linux-next, in this tree at ../linux-next): CTRLR0 differs between the
 * PSSI (0x400a) and HSSI (1.02a/1.03a/2.00a) IP generations, told apart by
 * the IDR register the way spi-dw-core.c does.
 *
 * Everything protocol-shaped follows the stock stack's own sources —
 * Google's kernel/google-modules/touch/synaptics_touch (Synaptics' libtcm):
 * synaptics_touchcom_core_dev.h for the shared enums and the CRC routines,
 * _core_v1.c and _core_v2.c for the two message framings, and _func_touch.c
 * for the configuration-driven report parser. TouchComm v1 frames a message
 * as a 4-byte header {0xa5, code, length little-endian} with no checksums;
 * v2 uses {code, length, byte 3 = host | sequence | CRC-6} and appends a
 * big-endian CRC-16 (CCITT, poly 0x1021, init 0xffff) whenever there is a
 * payload, with both checksums checked as residues: a good packet's CRC-6
 * over the whole header and CRC-16 over the whole packet are zero. Which
 * generation the chip speaks is decided exactly the way stock
 * syna_tcm_detect_device() does: write one bare 0x07 byte, read back four
 * bare bytes; a valid CRC-6 residue means v2, a leading 0xa5 means v1.
 *
 * The attention line is asserted by the chip while a response or report waits
 * and released by reading the message out. No interrupt is taken: the poll
 * loop looks at the pad the way hakios.goodix.c looks at its interrupt line,
 * and the core treats "the line cannot be read" (attn() == -1) as "read
 * anyway". All of this runs on the driver's one thread, so the message
 * buffers are static, the way hakios.goodix.c keeps its transfer buffers.
 */
#if defined(HAKIOS_TCM_HOSTTEST)
#include <stddef.h>
#include <string.h>
#include <sys/hakios.input.h>
#else
/* Port note (mach-choice lane): the fork overlay's aggregated hakios.patch
 * pre-seeds the kernel types before this include; this tree does not, so the
 * kernel include block comes FIRST (the same order bsd/dev/hakios.goodix.c
 * uses) and sys/hakios.input.h — whose prototypes use bool/dev_t — comes
 * after it. */
#include <libkern/libkern.h>            /* memcpy/memset and friends in the kernel */
#include <sys/systm.h>
#include <sys/hakios.input.h>
#endif

#include "hakios.tcm.h"

/* ---- the register logic, host-testable against any hakios_tcm_io ---- */

#define DW_SPI_CTRLR0           0x00
#define DW_SPI_SSIENR           0x08
#define DW_SPI_SER              0x10
#define DW_SPI_BAUDR            0x14
#define DW_SPI_TXFTLR           0x18
#define DW_SPI_RXFTLR           0x1c
#define DW_SPI_TXFLR            0x20
#define DW_SPI_RXFLR            0x24
#define DW_SPI_SR               0x28
#define DW_SPI_IMR              0x2c
#define DW_SPI_ICR              0x48
#define DW_SPI_IDR              0x58
#define DW_SPI_VERSION          0x5c
#define DW_SPI_DR               0x60
#define DW_SPI_RX_SAMPLE_DLY    0xf0

/* stock spi-dw.h's IP ids: the SSI generation decides CTRLR0's layout */
#define DW_SPI_ID_PSSI_400A     0x3430302au
#define DW_SPI_ID_HSSI_102A     0x3130322au
#define DW_SPI_ID_HSSI_103A     0x3130332au
#define DW_SPI_ID_HSSI_200A     0x3230302au

#define DW_SPI_SR_BUSY          (1u << 0)
#define DW_SPI_SR_TFNF          (1u << 1)
#define DW_SPI_SR_RFNE          (1u << 3)

/* PSSI CTRLR0: 8-bit data [3:0], SPI frame [5:4], mode 0 [7:6], TR [9:8] */
#define DW_SPI_CTRLR0_PSSI      (7u | 0u << 4 | 0u << 6 | 0u << 8)
/* HSSI CTRLR0: 8-bit data [4:0], SPI frame [7:6], mode 0 [9:8], TR [11:10],
 * master [31] — the PSSI has no master bit */
#define DW_SPI_CTRLR0_HSSI      (7u | 0u << 6 | 0u << 8 | 0u << 10 | 1u << 31)

static void
tcm_delay_us(uint32_t us)
{
	if (hakios_tcm_bus.delay_us != NULL) {
		hakios_tcm_bus.delay_us(us);
	}
}

static bool
ssi_wait_not_busy(const struct hakios_tcm_io *io, uint64_t base, unsigned loops)
{
	for (unsigned i = 0; i < loops; i++) {
		if (!(io->read32(io->ctx, base + DW_SPI_SR) & DW_SPI_SR_BUSY)) {
			return true;
		}
		tcm_delay_us(10);
	}
	return false;
}

/*
 * Program the SSI for the touch chip: mode 0, 8-bit frames, CS 0, transmit
 * and receive, the baud divider inherited (or overridden). Returns NULL when
 * the engine takes the configuration, otherwise why not. Nothing here starts
 * clocks or rails: the boot loader left them running, and if it did not, the
 * ID register reads as zero and we say exactly that.
 */
const char *
hakios_tcm_ssi_init(const struct hakios_tcm_io *io, uint64_t base, uint32_t override_baudr, uint32_t *baudr_out)
{
	uint32_t idr = io->read32(io->ctx, base + DW_SPI_IDR);
	uint32_t version = io->read32(io->ctx, base + DW_SPI_VERSION);
	bool hssi = idr == DW_SPI_ID_HSSI_102A || idr == DW_SPI_ID_HSSI_103A || idr == DW_SPI_ID_HSSI_200A;
	uint32_t baudr, check;

	if (idr == 0 && version == 0) {
		return "the SSI reads as zero: its bus clock is off or the window is wrong";
	}
	if (!hssi && idr != DW_SPI_ID_PSSI_400A) {
		return "an SSI generation this driver does not know";
	}
	baudr = override_baudr ? override_baudr : io->read32(io->ctx, base + DW_SPI_BAUDR);
	if (baudr < 2 || baudr > 0xfffe) {
		return "no sensible baud divider found (boot-arg touch_baudr= names one)";
	}
	io->write32(io->ctx, base + DW_SPI_IMR, 0);            /* no interrupts; everything is polled */
	io->read32(io->ctx, base + DW_SPI_ICR);                /* clear whatever hangs over from before */
	io->write32(io->ctx, base + DW_SPI_SSIENR, 0);
	io->write32(io->ctx, base + DW_SPI_CTRLR0, hssi ? DW_SPI_CTRLR0_HSSI : DW_SPI_CTRLR0_PSSI);
	io->write32(io->ctx, base + DW_SPI_TXFTLR, 0);
	io->write32(io->ctx, base + DW_SPI_RXFTLR, 0);
	io->write32(io->ctx, base + DW_SPI_SER, 1);            /* the chip on CS 0 */
	io->write32(io->ctx, base + DW_SPI_BAUDR, baudr);
	io->write32(io->ctx, base + DW_SPI_SSIENR, 1);
	check = io->read32(io->ctx, base + DW_SPI_CTRLR0);
	if (check != (hssi ? DW_SPI_CTRLR0_HSSI : DW_SPI_CTRLR0_PSSI)) {
		return "the SSI does not take its CTRLR0";
	}
	if (baudr_out) {
		*baudr_out = baudr;
	}
	return NULL;
}

/*
 * One transaction with the chip selected throughout: `len` bytes out (zeros
 * without tx) and as many in — the SSI runs in full-duplex transmit-and-
 * receive mode, the way spi-dw-core.c pushes its FIFOs. The chip only drives
 * MISO during the reads, which is all the TouchComm transport asks for.
 */
bool
hakios_tcm_ssi_transfer(const struct hakios_tcm_io *io, uint64_t base, const uint8_t *tx, uint8_t *rx, uint32_t len)
{
	uint32_t sent = 0, received = 0;

	if (!ssi_wait_not_busy(io, base, 2000)) {
		return false;
	}
	io->read32(io->ctx, base + DW_SPI_ICR);
	while (sent < len) {
		if (io->read32(io->ctx, base + DW_SPI_SR) & DW_SPI_SR_TFNF) {
			io->write32(io->ctx, base + DW_SPI_DR, tx ? tx[sent] : 0);
			sent++;
		}
		if (io->read32(io->ctx, base + DW_SPI_SR) & DW_SPI_SR_RFNE) {
			uint32_t word = io->read32(io->ctx, base + DW_SPI_DR);

			if (received < len) {
				if (rx) {
					rx[received] = (uint8_t)word;
				}
				received++;
			}
		}
	}
	if (!ssi_wait_not_busy(io, base, 20000)) {
		return false;
	}
	/* drain the last frames, bounded the way hakios.goodix drains its FIFO */
	for (unsigned i = 0; i < 64 && (io->read32(io->ctx, base + DW_SPI_SR) & DW_SPI_SR_RFNE); i++) {
		uint32_t word = io->read32(io->ctx, base + DW_SPI_DR);

		if (received < len) {
			if (rx) {
				rx[received] = (uint8_t)word;
			}
			received++;
		}
	}
	return received == len;
}

/*
 * The attention pad's level: bit 0 of the pad's data register, 1 = high =
 * idle (the line is active low). The slot layout is the NEEDS-DEVICE-PROBE
 * guess described at the top of the file.
 */
int
hakios_tcm_attn_pad(const struct hakios_tcm_io *io, uint64_t pad_base, uint32_t pad, uint32_t slot, uint32_t off)
{
	return (int)(io->read32(io->ctx, pad_base + (uint64_t)pad * slot + off) & 1);
}

/* ---- the protocol core, host-testable behind struct hakios_tcm_bus ---- */

/* How long a command may take to answer, and how the wait is spaced. */
#define TCM_RESPONSE_TIMEOUT_MS 300
#define TCM_RESPONSE_STEP_MS    5
/* stock TAT_DELAY_US: the bus turnaround pause between a write and its read */
#define TCM_TURNAROUND_US       150

/* the FDT's synaptics,reset-active-ms/-delay-ms/-power-delay-ms */
#define TCM_RESET_ACTIVE_MS     1
#define TCM_RESET_DELAY_MS      70
#define TCM_POWER_DELAY_MS      200
/* stock RESET_DELAY_MS / FW_MODE_SWITCH_DELAY_MS: a reset takes this long */
#define TCM_RESET_SETTLE_MS     200
/* stock CMD_RESPONSE_POLLING_DELAY_MS, for reads without an attention line */
#define TCM_BLIND_READ_MS       20

#define TCM_V1_MARKER           0xa5

struct hakios_tcm_bus hakios_tcm_bus = {
	.write = NULL,
	.read = NULL,
	.attn = NULL,
	.reset = NULL,
	.delay_us = NULL,
	.log = NULL,
};

static void
tcm_vlog(const char *format, va_list args)
{
	if (hakios_tcm_bus.log == NULL) {
		return;
	}
	hakios_tcm_bus.log(format, args);
}

static void
tcm_log(const char *format, ...)
{
	va_list args;

	va_start(args, format);
	tcm_vlog(format, args);
	va_end(args);
}

/* ---- the checksums (stock syna_tcm_crc6 / syna_tcm_crc16, verbatim algorithms) ---- */

static uint8_t
tcm_crc6(const uint8_t *p, uint32_t bits)
{
	static const uint16_t table[16] = {
		0, 268, 536, 788, 1072, 1340, 1576, 1828,
		2144, 2412, 2680, 2932, 3152, 3420, 3656, 3908
	};
	uint16_t r = 0x3f << 2, x;

	while (bits > 8) {
		r ^= *p++;
		r = (uint16_t)((r << 4) ^ table[r >> 4]);
		r = (uint16_t)((r << 4) ^ table[r >> 4]);
		bits -= 8;
	}
	if (bits > 0) {
		x = *p;
		while (bits--) {
			if (x & 0x80) {
				r ^= 0x80;
			}
			x = (uint8_t)(x << 1);
			r = (uint16_t)(r << 1);
			if (r & 0x100) {
				r ^= 0x03 << 2;
			}
		}
	}
	return (uint8_t)((r >> 2) & 0x3f);
}

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xffff), the stock table routine
 * written out bit by bit; appending the result big-endian makes the checksum
 * over the whole packet zero, which is how received packets are checked. */
static uint16_t
tcm_crc16(const uint8_t *p, uint32_t len)
{
	uint16_t r = 0xffff;

	while (len--) {
		r ^= (uint16_t)(*p++ << 8);
		for (int bit = 0; bit < 8; bit++) {
			r = (r & 0x8000) ? (uint16_t)((r << 1) ^ 0x1021) : (uint16_t)(r << 1);
		}
	}
	return r;
}

static uint16_t
le16(const uint8_t *p)
{
	return (uint16_t)(p[0] | p[1] << 8);
}

static uint32_t
le32(const uint8_t *p)
{
	return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

/* ---- the two message framings ---- */

static bool
tcm_bus_write(const uint8_t *data, uint32_t len)
{
	return hakios_tcm_bus.write != NULL && hakios_tcm_bus.write(data, len);
}

static bool
tcm_bus_read(uint8_t *data, uint32_t len)
{
	return hakios_tcm_bus.read != NULL && hakios_tcm_bus.read(data, len);
}

static int
tcm_bus_attn(void)
{
	return hakios_tcm_bus.attn != NULL ? hakios_tcm_bus.attn() : -1;
}

static void
tcm_delay_ms(uint32_t ms)
{
	tcm_delay_us(ms * 1000);
}

/* A v2 packet: header {code, length, host 0 | sequence toggle | CRC-6 of the
 * first 26 bits}, the payload, and (with a payload only) the CRC-16 of
 * everything before it, big endian — stock syna_tcm_v2_write. */
static bool
tcm_write(struct hakios_tcm_state *st, uint8_t code, const uint8_t *payload, uint32_t len)
{
	static uint8_t packet[4 + HAKIOS_TCM_PAYLOAD_MAX + 2];

	if (len > HAKIOS_TCM_PAYLOAD_MAX) {
		return false;
	}
	if (st->proto == 2) {
		packet[0] = code;
		packet[1] = (uint8_t)len;
		packet[2] = (uint8_t)(len >> 8);
		/* stock's order: the host and sequence bits go in first, then the
		 * CRC-6 of the first 26 bits (which reads those two bits back) */
		packet[3] = (uint8_t)((st->sequence++ & 1) << 6);
		packet[3] |= tcm_crc6(packet, 4 * 8 - 6);
		if (len > 0) {
			uint16_t crc;

			memcpy(packet + 4, payload, len);
			crc = tcm_crc16(packet, 4 + len);
			packet[4 + len] = (uint8_t)(crc >> 8);
			packet[5 + len] = (uint8_t)crc;
			return tcm_bus_write(packet, 6 + len);
		}
		return tcm_bus_write(packet, 4);
	}
	/* v1 writes carry no marker and no checksum: {code, length, payload}. */
	packet[0] = code;
	packet[1] = (uint8_t)len;
	packet[2] = (uint8_t)(len >> 8);
	if (len > 0) {
		memcpy(packet + 3, payload, len);
	}
	return tcm_bus_write(packet, 3 + len);
}

/*
 * Read one message in. The header is always its own read; a v2 payload then
 * arrives in chunks that each begin with a header of their own and end in a
 * CRC-16, one CMD_ACK written before each chunk (stock
 * syna_tcm_v2_continued_read, one CMD_RETRY when a chunk is corrupt). A v1
 * payload is one read a single pad byte longer than the payload (stock
 * syna_tcm_v1_read). The chunk size is the chip's own max_read_size, narrowed
 * to what our buffers take.
 */
static bool
tcm_read(struct hakios_tcm_state *st, uint8_t *code_out, uint32_t *len_out)
{
	static uint8_t chunk[4 + HAKIOS_TCM_PAYLOAD_MAX + 2];
	static uint8_t payload[HAKIOS_TCM_PAYLOAD_MAX + 1];
	uint8_t header[4];
	uint8_t code;
	uint32_t len, got = 0;

	if (!tcm_bus_read(header, 4)) {
		return false;
	}
	if (st->proto == 2) {
		if (tcm_crc6(header, 4 * 8) != 0) {
			st->bad++;
			tcm_log("tcm: bad v2 header %02x %02x %02x %02x\n", header[0], header[1], header[2], header[3]);
			return false;
		}
		code = header[0];
	} else {
		if (header[0] != TCM_V1_MARKER) {
			st->bad++;
			return false;
		}
		code = header[1];
	}
	/* v1's length sits at bytes 2-3 (behind the marker and the code), v2's
	 * at bytes 1-2 (the code comes first and byte 3 is the CRC-6) */
	len = st->proto == 2 ? le16(header + 1) : le16(header + 2);
	if (len > HAKIOS_TCM_PAYLOAD_MAX) {
		st->bad++;
		tcm_log("tcm: v%u message of %u bytes is more than we take\n", st->proto, len);
		return false;
	}
	if (st->proto == 2) {
		/*
		 * The payload arrives in chunks, each behind a CMD_ACK we write,
		 * and each framed in its own header (whose length field is the
		 * chunk's size — the chip chooses the chunking, we follow it) and
		 * its own CRC-16; stock syna_tcm_v2_continued_read, with one
		 * CMD_RETRY when a chunk is corrupt.
		 */
		while (got < len) {
			uint32_t take;

			for (uint8_t retry = 0;; retry++) {
				if (!tcm_write(st, retry == 0 ? TCM_CMD_ACK : TCM_CMD_RETRY, NULL, 0)) {
					return false;
				}
				tcm_delay_us(TCM_TURNAROUND_US);
				memset(chunk, 0, sizeof(chunk));
				if (!tcm_bus_read(chunk, 4)) {
					return false;
				}
				if (tcm_crc6(chunk, 4 * 8) != 0) {
					goto corrupt;
				}
				take = le16(chunk + 1);
				if (take == 0 || take > len - got || take > HAKIOS_TCM_PAYLOAD_MAX - got) {
					goto corrupt;
				}
				if (!tcm_bus_read(chunk + 4, take + 2)) {
					return false;
				}
				if (tcm_crc16(chunk, 4 + take + 2) == 0) {
					break;
				}
corrupt:
				st->bad++;
				if (retry >= 1) {
					tcm_log("tcm: a chunk of the %u-byte message stays corrupt\n", len);
					return false;
				}
			}
			memcpy(payload + got, chunk + 4, take);
			got += take;
		}
	} else if (len > 0) {
		if (!tcm_bus_read(payload, len + 1)) {  /* + the stock MESSAGE_PADDING byte */
			return false;
		}
		got = len;
	}
	st->messages++;
	if (code_out) {
		*code_out = code;
	}
	/* the payload stays in the static buffer; tcm_read_and_handle copies it out */
	if (len_out) {
		*len_out = got;
	}
	st->last_payload = got > 0 ? payload : NULL;
	return true;
}

static void tcm_parse_ident(struct hakios_tcm_state *st, const uint8_t *payload, uint32_t len);

/*
 * One message read and handled. command_pending says one of our commands is
 * waiting for its answer, so the device has already pushed the message;
 * an asynchronous read in v2 has to ask for the report with
 * CMD_TCM2_GET_REPORT first (stock syna_tcm_v2_read_message).
 */
static bool
tcm_read_and_handle(struct hakios_tcm_state *st, bool command_pending, uint8_t *code_out, uint8_t *out,
    uint32_t out_max, uint32_t *out_len)
{
	uint8_t code = 0;
	uint32_t len = 0;

	if (st->proto == 2 && !command_pending) {
		if (!tcm_write(st, TCM_CMD_GET_REPORT, NULL, 0)) {
			return false;
		}
		tcm_delay_us(TCM_TURNAROUND_US);
	}
	if (!tcm_read(st, &code, &len)) {
		return false;
	}
	if (code == TCM_REPORT_TOUCH) {
		hakios_tcm_touch_report(st, st->last_payload, len);
	} else if (code == TCM_REPORT_IDENTIFY) {
		tcm_parse_ident(st, st->last_payload, len);
		tcm_log("tcm: identify report: mode %02x, \"%s\", build %u\n", st->mode, st->part_number, st->build_id);
	}
	if (code_out) {
		*code_out = code;
	}
	if (out != NULL && st->last_payload != NULL) {
		if (len > out_max) {
			len = out_max;
		}
		memcpy(out, st->last_payload, len);
	}
	if (out_len) {
		*out_len = len;
	}
	return true;
}

/* Send a command and collect its response, waiting for the attention line.
 * A v1 device says STATUS_IDLE (0) when the line woke early; asynchronous
 * touch reports may arrive while we wait and are handled as they come; and
 * the answer to CMD_IDENTIFY is an identify report rather than a status, so
 * that report ends the wait (stock accepts either shape). */
static bool
tcm_command(struct hakios_tcm_state *st, uint8_t code, const uint8_t *payload, uint32_t len, uint8_t *response,
    uint32_t response_max, uint32_t *response_len)
{
	uint8_t answer = 0;
	uint32_t answer_len = 0;

	if (!tcm_write(st, code, payload, len)) {
		return false;
	}
	tcm_delay_us(TCM_TURNAROUND_US);
	for (uint32_t waited = 0; waited < TCM_RESPONSE_TIMEOUT_MS; waited += TCM_RESPONSE_STEP_MS) {
		int attention = tcm_bus_attn();

		if (attention == 0) {
			tcm_delay_ms(TCM_RESPONSE_STEP_MS);
			continue;
		}
		if (attention < 0) {
			tcm_delay_ms(TCM_BLIND_READ_MS);        /* no line: read blind */
		}
		if (!tcm_read_and_handle(st, true, &answer, response, response_max, &answer_len)) {
			tcm_delay_ms(TCM_RESPONSE_STEP_MS);
			continue;
		}
		if (answer == TCM_STATUS_OK || answer == TCM_REPORT_IDENTIFY) {
			/* the identify answer may be a status with the packet as its
			 * payload (v1's shape) rather than the identify report */
			if (code == TCM_CMD_IDENTIFY && answer == TCM_STATUS_OK) {
				tcm_parse_ident(st, response, answer_len);
			}
			if (response_len) {
				*response_len = answer_len;
			}
			return true;
		}
		if (answer == TCM_STATUS_IDLE || answer >= TCM_REPORT_IDENTIFY) {
			continue;       /* a report, already handled; keep waiting */
		}
		tcm_log("tcm: command %02x answered status %02x\n", code, answer);
		return false;
	}
	tcm_log("tcm: command %02x timed out\n", code);
	return false;
}

/* ---- the identification and application packets ---- */

/* The identify payload is 24 bytes in v1 and 32 in v2: version, mode, part
 * number, build id, write size, and (v2) read size. */
static void
tcm_parse_ident(struct hakios_tcm_state *st, const uint8_t *payload, uint32_t len)
{
	if (payload == NULL || len < 24) {
		return;
	}
	st->mode = payload[1];
	memcpy(st->part_number, payload + 2, 16);
	st->part_number[16] = 0;
	st->build_id = le32(payload + 18);
	st->max_wr_size = le16(payload + 22);
	if (len >= 26) {
		st->max_rd_size = le16(payload + 24);
	}
}

/* The application info packet (48 bytes, 16-bit fields little-endian). */
static void
tcm_parse_app_info(struct hakios_tcm_state *st, const uint8_t *payload, uint32_t len)
{
	if (payload == NULL || len < 48) {
		tcm_log("tcm: short application info (%u bytes)\n", len);
		return;
	}
	st->app_status = le16(payload + 2);
	st->max_x = le16(payload + 32);
	st->max_y = le16(payload + 34);
	st->max_objects = le16(payload + 36);
	if (st->max_objects == 0 || st->max_objects > HAKIOS_TCM_MAX_OBJECTS) {
		st->max_objects = HAKIOS_TCM_MAX_OBJECTS;
	}
}

/* Which TouchComm generation the chip speaks (stock syna_tcm_detect_device:
 * a bare 0x07 written, four bare bytes read back). */
static bool
tcm_detect(struct hakios_tcm_state *st)
{
	uint8_t magic = 0x07, raw[4];

	if (!tcm_bus_write(&magic, 1) || !tcm_bus_read(raw, 4)) {
		return false;
	}
	if (tcm_crc6(raw, 4 * 8) == 0) {
		st->proto = 2;
		return true;
	}
	if (raw[0] == TCM_V1_MARKER) {
		st->proto = 1;
		return true;
	}
	return false;
}

/*
 * The PMIC reset line, if the bus layer can drive it at all: the FDT wiring
 * is synaptics,reset-gpio = da9188 (CPM-mediated) GPIO 0x11, active low,
 * with synaptics,reset-active-ms 1 and reset-delay-ms 70, and the rails'
 * power-delay-ms 200 behind it. The kernel's bus layer cannot drive that
 * line without the CPM mailbox request format, so on the phone this logs and
 * stands down; the host test proves the sequencing.
 */
static void
tcm_hard_reset(struct hakios_tcm_state *st)
{
	if (hakios_tcm_bus.reset == NULL || !hakios_tcm_bus.reset(true)) {
		tcm_log("tcm: the reset line cannot be driven; relying on what the boot loader left running\n");
		return;
	}
	tcm_delay_ms(TCM_RESET_ACTIVE_MS);
	hakios_tcm_bus.reset(false);
	tcm_delay_ms(TCM_RESET_DELAY_MS);
	tcm_delay_ms(TCM_POWER_DELAY_MS);
	st->resets++;
}

/* The TCM reset: the chip reboots its firmware and announces itself with an
 * identify report. Like stock, a corrupt or missing answer to CMD_RESET is
 * expected; only the identify afterwards counts. */
static bool
tcm_soft_reset(struct hakios_tcm_state *st)
{
	(void)tcm_command(st, TCM_CMD_RESET, NULL, 0, NULL, 0, NULL);
	tcm_delay_ms(TCM_RESET_SETTLE_MS);
	st->resets++;
	for (uint32_t waited = 0; waited < 500; waited += TCM_BLIND_READ_MS) {
		int attention = tcm_bus_attn();

		if (attention != 0) {
			uint8_t code = 0;

			if (attention < 0) {
				tcm_delay_ms(TCM_BLIND_READ_MS);
			}
			if (tcm_read_and_handle(st, st->proto == 1, &code, NULL, 0, NULL) &&
			    code == TCM_REPORT_IDENTIFY) {
				return st->mode == TCM_MODE_APPLICATION;
			}
		}
		tcm_delay_ms(TCM_BLIND_READ_MS);
	}
	return false;
}

/*
 * The touch report's fields are bit-packed as its configuration says; stock
 * syna_tcm_get_touch_data: a field starts at the given bit, least significant
 * bit first within each byte, and runs over however many bits it declared.
 */
static uint32_t
tcm_touch_data(const uint8_t *report, uint32_t report_size, uint32_t offset, uint32_t bits)
{
	uint32_t data = 0, remaining = bits, bit_offset = offset % 8, byte_offset = offset / 8;

	if (bits == 0 || bits > 32) {
		return 0;
	}
	while (remaining > 0) {
		uint32_t available = 8 - bit_offset, take = available < remaining ? available : remaining;

		if (byte_offset >= report_size) {
			break;
		}
		data |= (uint32_t)(report[byte_offset] >> bit_offset & (0xff >> (8 - take))) << (bits - remaining);
		bit_offset = 0;
		byte_offset++;
		remaining -= take;
	}
	return data;
}

/* One touch report, decoded and turned into input samples. The report layout
 * is read from the chip's own configuration (stock syna_tcm_parse_touch_report):
 * the parser walks the configuration, repeats the per-object records for every
 * active object, and picks each field out of the report bit by bit. */
bool
hakios_tcm_touch_report(struct hakios_tcm_state *st, const uint8_t *report, uint32_t len)
{
	const uint8_t *config = st->config;
	uint32_t config_size = st->config_len;
	uint32_t idx = 0, offset = 0, obj = 0, next = 0, objects = 0, active_objects = 0;
	bool active_only = false, count_known = false;
	static uint32_t shown;

	if (config_size == 0 || len == 0) {
		return false;
	}
	for (uint32_t i = 0; i < HAKIOS_TCM_MAX_OBJECTS; i++) {
		st->obj[i].seen = false;
	}
	st->reports++;
	while (idx < config_size) {
		uint8_t code = config[idx++];
		uint32_t bits, data;

		switch (code) {
		case TCM_CFG_END:
			goto decode;
		case TCM_CFG_FOREACH_ACTIVE:
			obj = 0;
			next = idx;
			active_only = true;
			continue;
		case TCM_CFG_FOREACH:
			obj = 0;
			next = idx;
			active_only = false;
			continue;
		case TCM_CFG_FOREACH_END:
			/* stock's replication: again from the FOREACH, while there is more */
			if (active_only) {
				if (count_known) {
					objects++;
					obj++;
					if (objects < active_objects) {
						idx = next;
					}
				} else if (offset < len * 8) {
					obj++;
					idx = next;
				}
			} else {
				obj++;
				if (obj < st->max_objects) {
					idx = next;
				}
			}
			continue;
		case TCM_CFG_PAD:
			offset = (offset + 7) / 8 * 8;
			continue;
		default:
			/* every other record is a bits-sized field: timestamp, widths,
			 * gestures, ...; stock skips them with a warning, so do we */
			break;
		}
		if (idx >= config_size) {
			st->bad++;
			return false;
		}
		bits = config[idx++];
		data = tcm_touch_data(report, len, offset, bits);
		offset += bits;
		switch (code) {
		case TCM_CFG_OBJ_INDEX:
			obj = data;
			if (obj >= HAKIOS_TCM_MAX_OBJECTS) {
				st->bad++;
				return false;
			}
			break;
		case TCM_CFG_OBJ_CLASS:
			st->obj[obj].seen = true;
			st->obj[obj].class = (uint8_t)data;
			break;
		case TCM_CFG_OBJ_X:
			st->obj[obj].x = data;
			break;
		case TCM_CFG_OBJ_Y:
			st->obj[obj].y = data;
			break;
		case TCM_CFG_NUM_ACTIVE:
			active_objects = data;
			count_known = true;
			break;
		default:
			break;
		}
	}
decode:
	for (uint32_t i = 0; i < HAKIOS_TCM_MAX_OBJECTS; i++) {
		uint8_t class = st->obj[i].seen ? st->obj[i].class : TCM_OBJ_LIFT;
		bool down = class == TCM_OBJ_FINGER || class == TCM_OBJ_GLOVED || class == TCM_OBJ_PALM;
		uint32_t x = st->obj[i].x, y = st->obj[i].y;

		if (!down) {
			if (st->obj[i].down) {
				st->obj[i].down = false;
				hakios_tcm_input(HAKIOS_TOUCH_UP, i, st->obj[i].lx, st->obj[i].ly);
			}
			continue;
		}
		/* to 65535 against the chip's own panel size, hakios.goodix's formula */
		x = x >= st->scale_x ? 65535 : x * 65535 / st->scale_x;
		y = y >= st->scale_y ? 65535 : y * 65535 / st->scale_y;
		if (st->flip & 1) {
			x = 65535 - x;
		}
		if (st->flip & 2) {
			y = 65535 - y;
		}
		if (!st->obj[i].down) {
			st->obj[i].down = true;
			st->touches++;
			if (shown++ < 40) {
				tcm_log("tcm: finger %u down at %u, %u\n", i, x, y);
			}
			hakios_tcm_input(HAKIOS_TOUCH_DOWN, i, x, y);
		} else if (st->obj[i].lx != x || st->obj[i].ly != y) {
			hakios_tcm_input(HAKIOS_TOUCH_MOVE, i, x, y);
		}
		st->obj[i].lx = x;
		st->obj[i].ly = y;
	}
	return true;
}

/* Release every finger: the chip stopped talking, or the reports stopped. */
void
hakios_tcm_release_all(struct hakios_tcm_state *st)
{
	for (uint32_t i = 0; i < HAKIOS_TCM_MAX_OBJECTS; i++) {
		if (st->obj[i].down) {
			st->obj[i].down = false;
			hakios_tcm_input(HAKIOS_TOUCH_UP, i, st->obj[i].lx, st->obj[i].ly);
		}
		st->obj[i].seen = false;
	}
}

/*
 * Bring the chip up from nothing: detect, identify, application info, the
 * touch report configuration, and touch reports enabled. force_reset asks
 * for the hard reset first (boot-arg touch_reset=2); a chip that will not
 * answer gets one hard reset and one soft reset before we give up.
 */
bool
hakios_tcm_bring_up(struct hakios_tcm_state *st, bool force_reset)
{
	static uint8_t info[HAKIOS_TCM_PAYLOAD_MAX];
	static uint8_t config[HAKIOS_TCM_PAYLOAD_MAX];
	uint8_t payload[2];
	uint32_t len = 0;

	memset(st, 0, sizeof(*st));
	st->scale_x = 1;
	st->scale_y = 1;
	if (hakios_tcm_bus.write == NULL || hakios_tcm_bus.read == NULL) {
		return false;
	}
	tcm_log("tcm: bringing up the touch screen\n");
	if (force_reset) {
		tcm_hard_reset(st);
	}
	if (!tcm_detect(st)) {
		tcm_log("tcm: no TouchComm answer on the bus\n");
		tcm_hard_reset(st);
		if (!tcm_detect(st)) {
			tcm_log("tcm: still no TouchComm answer\n");
			return false;
		}
	}
	tcm_log("tcm: TouchComm v%u\n", st->proto);
	if (!tcm_command(st, TCM_CMD_IDENTIFY, NULL, 0, info, sizeof(info), &len)) {
		if (!tcm_soft_reset(st)) {
			tcm_log("tcm: no identify after the resets\n");
			return false;
		}
	}
	if (st->mode != TCM_MODE_APPLICATION) {
		tcm_log("tcm: the chip runs mode %02x, not the application firmware\n", st->mode);
		if (!tcm_soft_reset(st) || st->mode != TCM_MODE_APPLICATION) {
			tcm_log("tcm: still not in application mode; there is no firmware upload here\n");
			return false;
		}
	}
	tcm_log("tcm: application firmware \"%s\", build %u, writes %u, reads %u\n", st->part_number, st->build_id,
	    st->max_wr_size, st->max_rd_size);

	/* v2: keep the chip's own read size when it fits, else narrow it (stock
	 * syna_tcm_v2_set_max_read_size, so that no chunk overruns our buffers). */
	if (st->proto == 2 && st->max_rd_size != 0 && st->max_rd_size > HAKIOS_TCM_PAYLOAD_MAX) {
		uint16_t ours = HAKIOS_TCM_PAYLOAD_MAX;

		payload[0] = (uint8_t)ours;
		payload[1] = (uint8_t)(ours >> 8);
		if (!tcm_command(st, TCM_CMD_SET_MAX_READ, payload, 2, NULL, 0, NULL)) {
			return false;
		}
		st->max_rd_size = ours;
	}
	if (!tcm_command(st, TCM_CMD_GET_APPLICATION_INFO, NULL, 0, info, sizeof(info), &len)) {
		return false;
	}
	tcm_parse_app_info(st, info, len);
	if (st->app_status != 0) {
		tcm_log("tcm: the application says its status is %u (0 = ok)\n", st->app_status);
	}
	st->scale_x = st->max_x;
	st->scale_y = st->max_y;
	tcm_log("tcm: panel %u x %u, up to %u objects\n", st->max_x, st->max_y, st->max_objects);

	if (!tcm_command(st, TCM_CMD_GET_TOUCH_REPORT_CONFIG, NULL, 0, config, sizeof(config), &len) ||
	    len == 0 || len > HAKIOS_TCM_CONFIG_MAX) {
		tcm_log("tcm: no usable touch report configuration (%u bytes)\n", len);
		return false;
	}
	memcpy(st->config, config, len);
	st->config_len = len;

	payload[0] = TCM_REPORT_TOUCH;
	payload[1] = 1;
	if (!tcm_command(st, TCM_CMD_ENABLE_REPORT, payload, 2, NULL, 0, NULL)) {
		return false;
	}
	st->touch_enabled = true;
	tcm_log("tcm: touch reports on, configuration %u bytes; ready\n", st->config_len);
	return true;
}

/*
 * Read and handle one message when the attention line says one waits; false
 * when the line is idle. The poll loop calls this; a command's response
 * arrives through the same read path from tcm_command.
 */
bool
hakios_tcm_dispatch(struct hakios_tcm_state *st)
{
	if (tcm_bus_attn() == 0) {
		return false;
	}
	if (!tcm_read_and_handle(st, false, NULL, NULL, 0, NULL)) {
		st->bad++;
		return false;
	}
	return true;
}

#if !defined(HAKIOS_TCM_HOSTTEST)

/* ---- the kernel side: mapping, boot args, the thread ---- */

#include <kern/clock.h>
/* (mach-choice port: the fork overlay could see <kern/misc_protos.h> for
 * vprintf; this tree's bsd pass compiles against the export headers, where it
 * is not present — sys/systm.h (already included above) declares vprintf. */
#include <kern/sched_prim.h>
#include <kern/thread.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);

#define HAKIOS_TCM_SPI_PA       0x0db33000ULL
#define HAKIOS_TCM_SPI_SIZE     0x100ULL
#define HAKIOS_TCM_PAD_PA       0x0b400000ULL
#define HAKIOS_TCM_PAD_SIZE     0x5b000ULL
#define HAKIOS_TCM_INTR_PA      0x052b9000ULL
#define HAKIOS_TCM_INTR_SIZE    0x12000ULL
#define HAKIOS_TCM_ATTN_PAD     0x41
#define HAKIOS_TCM_ATTN_SLOT    0x1000
#define HAKIOS_TCM_ATTN_OFF     4

struct tcm_window {
	vm_offset_t va;
	uint64_t size;
};

static struct tcm_window tcm_spi_win, tcm_pad_win, tcm_intr_win;

/* the boot-arg set, hakios.goodix's naming continued; blazer_touch gates the
 * device's drivers the way blazer_ufs and blazer_cpm gate theirs, so the
 * POCO's touchscreen=1 never moves Tensor addresses */
static uint32_t boot_poll_us = 4000, boot_flip, boot_baudr;
static uint32_t boot_attn_slot = HAKIOS_TCM_ATTN_SLOT, boot_attn_off = HAKIOS_TCM_ATTN_OFF;
static uint32_t boot_attn_mode, boot_delay_s = 20, boot_force_reset;

static uint32_t
tcm_mmio_read(void *ctx, uint64_t pa)
{
	struct tcm_window *window = ctx;

	if (pa < window->va || pa - window->va >= window->size) {
		return 0;
	}
	return *(volatile uint32_t *)pa;
}

static void
tcm_mmio_write(void *ctx, uint64_t pa, uint32_t value)
{
	struct tcm_window *window = ctx;

	if (pa < window->va || pa - window->va >= window->size) {
		return;
	}
	*(volatile uint32_t *)pa = value;
}

/* one io per mapped window; accesses outside the window are dropped, so a
 * bug in the logic cannot turn into a wild store */
static const struct hakios_tcm_io tcm_spi_io = { tcm_mmio_read, tcm_mmio_write, &tcm_spi_win };
static const struct hakios_tcm_io tcm_pad_io = { tcm_mmio_read, tcm_mmio_write, &tcm_pad_win };

static void
tcm_vlog_kernel(const char *format, va_list args)
{
	printf("tcm: ");
	vprintf(format, args);
}

/* Port note (mach-choice lane): these were named tcm_bus_write/read/attn in
 * the fleet patch, which collides with the generic wrappers above (the patch
 * was only ever compiled host-side, where the kernel section is compiled out).
 * In the kernel build the core keeps calling the generic wrappers, which
 * route through hakios_tcm_bus — assigned to tcm_kernel_bus below. */
static bool
tcm_kernel_write(const uint8_t *data, uint32_t len)
{
	return hakios_tcm_ssi_transfer(&tcm_spi_io, tcm_spi_win.va, data, NULL, len);
}

static bool
tcm_kernel_read(uint8_t *data, uint32_t len)
{
	return hakios_tcm_ssi_transfer(&tcm_spi_io, tcm_spi_win.va, NULL, data, len);
}

static int
tcm_kernel_attn(void)
{
	if (boot_attn_mode == 1) {
		return -1;      /* the line is not trusted; the core reads blind */
	}
	return hakios_tcm_attn_pad(&tcm_pad_io, tcm_pad_win.va, HAKIOS_TCM_ATTN_PAD, boot_attn_slot, boot_attn_off);
}

static bool
tcm_bus_reset(bool assert __unused)
{
	static unsigned said;

	if (said++ < 4) {
		printf("tcm: the reset line rides the CPM mailbox (da9188 gpio 0x11), which this driver does not know\n");
	}
	return false;
}

static void
tcm_bus_delay_us(uint32_t us)
{
	delay_for_interval(us, NSEC_PER_USEC);
}

/* the samples go to /dev/hakios-touch, the same contract hakios.goodix fills */
void
hakios_tcm_input(uint32_t type, uint32_t finger, uint32_t x, uint32_t y)
{
	hakios_touch_post(type, finger, x, y);
}

static struct hakios_tcm_bus tcm_kernel_bus = {
	.write = tcm_kernel_write,
	.read = tcm_kernel_read,
	.attn = tcm_kernel_attn,
	.reset = tcm_bus_reset,
	.delay_us = tcm_bus_delay_us,
	.log = tcm_vlog_kernel,
};

static uint64_t
micros_now(void)
{
	uint64_t ns;

	absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
	return ns / 1000;
}

static void
tcm_thread(__unused void *parameter, __unused wait_result_t result)
{
	static struct hakios_tcm_state state;
	uint64_t started;

	/*
	 * Start late (boot-arg touch_delay, seconds): if the first bus access
	 * hangs the SoC — the AOC touch bus negotiator is the suspect — the
	 * console and the display got there first, like tegu's 45 s.
	 */
	printf("tcm: the touch screen starts in %u s\n", boot_delay_s);
	delay_for_interval(boot_delay_s * 1000, NSEC_PER_MSEC);
	tcm_spi_win.va = ml_io_map(HAKIOS_TCM_SPI_PA, HAKIOS_TCM_SPI_SIZE);
	tcm_spi_win.size = HAKIOS_TCM_SPI_SIZE;
	tcm_pad_win.va = ml_io_map(HAKIOS_TCM_PAD_PA, HAKIOS_TCM_PAD_SIZE);
	tcm_pad_win.size = HAKIOS_TCM_PAD_SIZE;
	tcm_intr_win.va = ml_io_map(HAKIOS_TCM_INTR_PA, HAKIOS_TCM_INTR_SIZE);
	tcm_intr_win.size = HAKIOS_TCM_INTR_SIZE;
	if (!tcm_spi_win.va || !tcm_pad_win.va || !tcm_intr_win.va) {
		printf("tcm: the MMIO mappings failed\n");
		goto out;
	}
	hakios_tcm_bus = tcm_kernel_bus;
	{
		uint32_t baudr = 0;
		const char *problem = hakios_tcm_ssi_init(&tcm_spi_io, tcm_spi_win.va, boot_baudr, &baudr);
		uint64_t slot = tcm_pad_win.va + (uint64_t)HAKIOS_TCM_ATTN_PAD * boot_attn_slot;

		printf("tcm: ssi idr %08x version %08x, baud divider %u, attn pad 0x%x slot %u/%u words %08x %08x %08x %08x\n",
		    tcm_spi_io.read32(tcm_spi_io.ctx, tcm_spi_win.va + DW_SPI_IDR),
		    tcm_spi_io.read32(tcm_spi_io.ctx, tcm_spi_win.va + DW_SPI_VERSION),
		    baudr, HAKIOS_TCM_ATTN_PAD, boot_attn_slot, boot_attn_off,
		    tcm_pad_io.read32(tcm_pad_io.ctx, slot),
		    tcm_pad_io.read32(tcm_pad_io.ctx, slot + 4),
		    tcm_pad_io.read32(tcm_pad_io.ctx, slot + 8),
		    tcm_pad_io.read32(tcm_pad_io.ctx, slot + 12));
		if (problem != NULL) {
			printf("tcm: %s\n", problem);
			goto out;
		}
	}
	started = micros_now();
	if (!hakios_tcm_bring_up(&state, boot_force_reset == 2)) {
		printf("tcm: no TouchComm touch screen (after %llu us)\n", micros_now() - started);
		hakios_tcm_release_all(&state);
		goto out;
	}
	printf("tcm: TouchComm touch screen ready; the attention pad is looked at every %u us\n", boot_poll_us);
	for (unsigned tick = 1;; tick++) {
		static unsigned bad, polls, reads;

		polls++;
		if (tcm_bus_attn() != 0) {
			if (hakios_tcm_dispatch(&state)) {
				reads++;
				bad = 0;
			} else if (++bad > 100) {
				/* the chip stopped answering: let go of the fingers and start over */
				printf("tcm: %u bad polls in a row; starting over\n", bad);
				hakios_tcm_release_all(&state);
				if (!hakios_tcm_bring_up(&state, false)) {
					printf("tcm: the re-bring-up failed; giving up\n");
					goto out;
				}
				bad = 0;
			}
		}
		if (tick % (60 * (1000000 / boot_poll_us)) == 0) {
			printf("tcm: %u polls, %u messages, %u reports, %u finger positions, %u bad; "
			    "attention %s; firmware \"%s\" build %u\n", polls, state.messages, state.reports,
			    state.touches, state.bad, tcm_bus_attn() ? "idle" : "asserted", state.part_number,
			    state.build_id);
		}
		tcm_bus_delay_us(boot_poll_us);
	}
out:
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

void
hakios_tcm_init(void)
{
	uint32_t enabled = 0, force_reset = 0;
	thread_t thread;

	if (!PE_parse_boot_argn("blazer_touch", &enabled, sizeof(enabled)) || !enabled) {
		return;
	}
	(void)PE_parse_boot_argn("touch_poll_us", &boot_poll_us, sizeof(boot_poll_us));
	(void)PE_parse_boot_argn("touch_flip", &boot_flip, sizeof(boot_flip));
	(void)PE_parse_boot_argn("touch_baudr", &boot_baudr, sizeof(boot_baudr));
	(void)PE_parse_boot_argn("touch_attn_mode", &boot_attn_mode, sizeof(boot_attn_mode));
	(void)PE_parse_boot_argn("touch_attn_slot", &boot_attn_slot, sizeof(boot_attn_slot));
	(void)PE_parse_boot_argn("touch_attn_off", &boot_attn_off, sizeof(boot_attn_off));
	(void)PE_parse_boot_argn("touch_delay", &boot_delay_s, sizeof(boot_delay_s));
	(void)PE_parse_boot_argn("touch_reset", &force_reset, sizeof(force_reset));
	boot_force_reset = force_reset;         /* 2 forces the hard reset at bring-up */
	if (boot_poll_us < 250 || boot_poll_us > 100000) {
		boot_poll_us = 4000;
	}
	if (boot_delay_s > 120) {
		boot_delay_s = 20;
	}
	if (kernel_thread_start(tcm_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	}
}

#endif /* !HAKIOS_TCM_HOSTTEST */
