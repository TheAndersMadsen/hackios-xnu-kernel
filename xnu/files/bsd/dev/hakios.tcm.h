/*
 * bsd/dev/hakios.tcm.h — the Synaptics TouchComm (TCM) touch screen of the Pixel 10 Pro
 * (blazer / laguna): "touchscreen@0" under "spi@db31000", compatible "synaptics,tcm-spi".
 * A source of touches for /dev/hakios-touch (sys/hakios.input.h), the same producer
 * contract bsd/dev/hakios.goodix.c fills for the POCO X5 Pro.
 *
 * The driver is one file (hakios.tcm.c) in two halves. The protocol core never
 * touches hardware: it drives everything through struct hakios_tcm_bus, which the
 * kernel half wires to the DesignWare SSI controller and the AOC pad that carries
 * the attention line, and which the host test (research/laguna-touch/tcm-test)
 * wires to mocks. The register logic takes its MMIO through struct hakios_tcm_io,
 * so the host test can run it against a fake register file. Compiling with
 * HAKIOS_TCM_HOSTTEST leaves out the kernel-only half.
 *
 * Protocol facts (from the stock stack's own sources — Google's
 * kernel/google-modules/touch/synaptics_touch, the "syna_touch" module of the
 * phone's vendor kernel, files synaptics_touchcom_core_dev.h / _core_v1.c /
 * _core_v2.c / _func_touch.c): TouchComm comes in two generations. Both carry
 * every message in a 4-byte header; v2 adds a CRC-6 in the last header byte
 * and a big-endian CRC-16 (CCITT, poly 0x1021) after the payload. The device
 * announces that a response or a report waits by pulling its attention line
 * low (the FDT's synaptics,irq-on-state = 0); a message read clears it.
 */
#ifndef _BSD_DEV_HAKIOS_TCM_H_
#define _BSD_DEV_HAKIOS_TCM_H_

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/cdefs.h>

#define HAKIOS_TCM_MAX_OBJECTS  10      /* stock MAX_NUM_OBJECTS */
#define HAKIOS_TCM_CONFIG_MAX   192     /* the touch report configuration */
#define HAKIOS_TCM_PAYLOAD_MAX  320     /* app info + margin for the config */

/*
 * TouchComm message codes, shared by both generations (the stock enum
 * tcm_command / tcm_report_type / tcm_status_code).
 */
#define TCM_CMD_CONTINUE_WRITE  0x01
#define TCM_CMD_IDENTIFY        0x02
#define TCM_CMD_RESET           0x04
#define TCM_CMD_ENABLE_REPORT   0x05
#define TCM_CMD_DISABLE_REPORT  0x06
#define TCM_CMD_ACK             0x07    /* v2: a continued read's chunk request */
#define TCM_CMD_RETRY           0x08    /* v2: reread a corrupted chunk */
#define TCM_CMD_SET_MAX_READ    0x09    /* v2: configure the chunk size */
#define TCM_CMD_GET_REPORT      0x0a    /* v2: fetch the pending report */
#define TCM_CMD_GET_APPLICATION_INFO     0x20
#define TCM_CMD_GET_TOUCH_REPORT_CONFIG  0x25

#define TCM_STATUS_IDLE         0x00    /* v1: nothing waiting */
#define TCM_STATUS_OK           0x01    /* the previous command succeeded */
#define TCM_STATUS_CONTINUED    0x03    /* this chunk continues the payload */
#define TCM_STATUS_CORRUPTED    0xfe    /* our own verdict on a bad packet */

#define TCM_REPORT_IDENTIFY     0x10
#define TCM_REPORT_TOUCH        0x11

#define TCM_MODE_APPLICATION    0x01

/* What an object's classification byte says (stock object_classification). */
#define TCM_OBJ_LIFT            0
#define TCM_OBJ_FINGER          1
#define TCM_OBJ_GLOVED          2
#define TCM_OBJ_STYLUS          3
#define TCM_OBJ_PALM            6

/* Touch report configuration codes (stock touch_report_code), the few we use. */
#define TCM_CFG_END             0x00
#define TCM_CFG_FOREACH_ACTIVE  0x01
#define TCM_CFG_FOREACH         0x02
#define TCM_CFG_FOREACH_END     0x03
#define TCM_CFG_PAD             0x04
#define TCM_CFG_TIMESTAMP       0x05
#define TCM_CFG_OBJ_INDEX       0x06
#define TCM_CFG_OBJ_CLASS       0x07
#define TCM_CFG_OBJ_X           0x08
#define TCM_CFG_OBJ_Y           0x09
#define TCM_CFG_NUM_ACTIVE      0x18

/*
 * The bus the protocol core drives. Set once before hakios_tcm_bring_up();
 * every entry may be swapped out by the host test. The log line carries the
 * "tcm: " prefix the kernel's printf wants; the core does not print itself.
 */
struct hakios_tcm_bus {
	bool (*write)(const uint8_t *data, uint32_t len);       /* one CS frame out */
	bool (*read)(uint8_t *data, uint32_t len);              /* one CS frame in */
	int (*attn)(void);      /* 1 = a message waits (line low), 0 = idle, -1 = the line cannot be read */
	bool (*reset)(bool assert);     /* the PMIC reset line, active low; false = not drivable */
	void (*delay_us)(uint32_t us);
	void (*log)(const char *format, va_list args);
};

extern struct hakios_tcm_bus hakios_tcm_bus;

/* What the driver knows about the chip after a successful bring-up. */
struct hakios_tcm_state {
	uint8_t proto;                  /* 1 or 2: the TouchComm generation in use */
	uint8_t mode;                   /* the identify packet's firmware mode */
	char part_number[17];
	uint32_t build_id;              /* the "packrat" number */
	uint16_t max_wr_size, max_rd_size;
	uint16_t app_status;            /* 0 = ok, 1 = booting, 2 = updating */
	uint16_t max_x, max_y;          /* the panel as the application firmware sees it */
	uint16_t max_objects;

	uint8_t config[HAKIOS_TCM_CONFIG_MAX];
	uint32_t config_len;

	uint32_t scale_x, scale_y;      /* coordinates become 0..65535 against this */
	uint32_t flip;                  /* boot-arg touch_flip, as hakios.goodix reads it */

	uint8_t sequence;               /* the v2 header's sequence toggle */
	const uint8_t *last_payload;    /* the message just read, in the core's own buffer */

	struct {
		bool down, seen;
		uint8_t class;
		uint32_t x, y;          /* where the report put the object */
		uint32_t lx, ly;        /* where it was last posted, 0..65535 */
	} obj[HAKIOS_TCM_MAX_OBJECTS];

	uint32_t messages, reports, touches, bad, resets;
	bool touch_enabled, verbose;
};

/*
 * The protocol core (hakios.tcm.c). bring_up runs the whole handshake —
 * hard reset through bus.reset if the chip stays silent, soft reset through
 * TCM if it answers in the wrong mode, identify, application info, the touch
 * report configuration, and the enabling of touch reports. dispatch reads
 * and handles one message when the attention line says one waits; a command
 * waits for its response the same way, so bring_up and dispatch share the
 * same read path. The kernel's poll loop and the host test call exactly these.
 */
bool hakios_tcm_bring_up(struct hakios_tcm_state *st, bool force_reset);
bool hakios_tcm_dispatch(struct hakios_tcm_state *st);
void hakios_tcm_release_all(struct hakios_tcm_state *st);
bool hakios_tcm_touch_report(struct hakios_tcm_state *st, const uint8_t *payload, uint32_t len);

/*
 * Where the touches go: the consumer defines this. The kernel half hands
 * samples to hakios_touch_post() (the /dev/hakios-touch producer, the same
 * contract hakios.goodix.c fills); the host test records them.
 * type/finger/x/y follow sys/hakios.input.h: HAKIOS_TOUCH_DOWN/MOVE/UP,
 * coordinates scaled to 0..65535.
 */
void hakios_tcm_input(uint32_t type, uint32_t finger, uint32_t x, uint32_t y);

/* ---- the register logic's io table ----
 *
 * Everything the SSI and pad code touches goes through this io table: the
 * kernel wires it to range-checked ml_io_map'd windows, the host test to a
 * fake register file. Reads and writes outside the window the implementation
 * was given are dropped, so a bug in the logic cannot turn into a wild store. */
struct hakios_tcm_io {
	uint32_t (*read32)(void *ctx, uint64_t pa);
	void (*write32)(void *ctx, uint64_t pa, uint32_t value);
	void *ctx;
};

/* Program the SSI for the touch chip. Returns NULL when the engine takes
 * the configuration, otherwise why not; the baud divider is inherited from
 * the running configuration unless override_baudr says otherwise. */
const char *hakios_tcm_ssi_init(const struct hakios_tcm_io *io, uint64_t base,
    uint32_t override_baudr, uint32_t *baudr_out);
/* One full-duplex transaction with the chip selected throughout. */
bool hakios_tcm_ssi_transfer(const struct hakios_tcm_io *io, uint64_t base,
    const uint8_t *tx, uint8_t *rx, uint32_t len);
/* The attention pad's level: bit 0 of its data register, 1 = idle. */
int hakios_tcm_attn_pad(const struct hakios_tcm_io *io, uint64_t pad_base,
    uint32_t pad, uint32_t slot, uint32_t off);

#endif /* _BSD_DEV_HAKIOS_TCM_H_ */
