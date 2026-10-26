/*
 * Laguna display/render handoff and CPM power-sequence contracts.
 *
 * Host-testable data-only code: no MMIO, no device access, no XNU headers.
 * The binary handoff is produced by the display-owning first stage, not by
 * reading DC9x00 registers in XNU.  All multi-byte fields are little-endian
 * (both the first stage and XNU are AArch64 little-endian).
 */
#ifndef LAGUNA_RENDER_PORT_H
#define LAGUNA_RENDER_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LAGUNA_DISPLAY_HANDOFF_MAGIC UINT64_C(0x313030505344474c) /* "LGDSP001" as little-endian bytes */
#define LAGUNA_DISPLAY_HANDOFF_VERSION 1u
#define LAGUNA_DISPLAY_HANDOFF_SIZE 128u
#define LAGUNA_DISPLAY_HANDOFF_CRC_OFFSET 112u

/* DRM fourcc values whose little-endian memory order is B,G,R,(A/X). */
#define LAGUNA_PIXEL_ARGB8888 UINT32_C(0x34325241) /* AR24 */
#define LAGUNA_PIXEL_XRGB8888 UINT32_C(0x34325258) /* XR24 */

enum laguna_display_flags {
	LAGUNA_DISPLAY_CPU_PA_VALID       = UINT64_C(1) << 0,
	LAGUNA_DISPLAY_DPU_IOVA_VALID     = UINT64_C(1) << 1,
	LAGUNA_DISPLAY_LINEAR_32BPP       = UINT64_C(1) << 2,
	LAGUNA_DISPLAY_PHYS_CONTIGUOUS    = UINT64_C(1) << 3,
	LAGUNA_DISPLAY_RESERVED           = UINT64_C(1) << 4,
	LAGUNA_DISPLAY_DPU_CONFIGURED     = UINT64_C(1) << 5,
	LAGUNA_DISPLAY_POWER_RETAINED     = UINT64_C(1) << 6,
	LAGUNA_DISPLAY_IOMMU_RETAINED     = UINT64_C(1) << 7,
	LAGUNA_DISPLAY_SCANOUT_LIVE       = UINT64_C(1) << 8
};

#define LAGUNA_DISPLAY_KNOWN_FLAGS ( \
	LAGUNA_DISPLAY_CPU_PA_VALID | LAGUNA_DISPLAY_DPU_IOVA_VALID | \
	LAGUNA_DISPLAY_LINEAR_32BPP | LAGUNA_DISPLAY_PHYS_CONTIGUOUS | \
	LAGUNA_DISPLAY_RESERVED | LAGUNA_DISPLAY_DPU_CONFIGURED | \
	LAGUNA_DISPLAY_POWER_RETAINED | LAGUNA_DISPLAY_IOMMU_RETAINED | \
	LAGUNA_DISPLAY_SCANOUT_LIVE)

#define LAGUNA_DISPLAY_REQUIRED_FLAGS LAGUNA_DISPLAY_KNOWN_FLAGS

enum laguna_display_mapping {
	LAGUNA_DISPLAY_MAPPING_IDENTITY = 1,
	LAGUNA_DISPLAY_MAPPING_FIRST_STAGE_SMMU = 2
};

enum laguna_display_cache {
	LAGUNA_DISPLAY_CACHE_WRITE_COMBINE = 1,
	LAGUNA_DISPLAY_CACHE_CLEAN_ON_PRESENT = 2
};

/*
 * Version-1 wire record, exactly 128 bytes.  Keep fields in this order and do
 * not insert native pointers.  reserved[] must be zero for forward rejection.
 */
struct laguna_display_handoff {
	uint64_t magic;
	uint32_t version;
	uint32_t struct_size;
	uint64_t flags;
	uint64_t cpu_pa;
	uint64_t cpu_size;
	uint64_t dpu_iova;
	uint64_t reserve_pa;
	uint64_t reserve_size;
	uint32_t width;
	uint32_t height;
	uint32_t stride;
	uint32_t pixel_format;
	uint32_t refresh_millihz;
	uint32_t mapping_kind;
	uint32_t cache_kind;
	uint32_t commit_generation;
	uint8_t producer[16];
	uint32_t crc32;
	uint32_t reserved[3];
};

struct laguna_memory_range {
	uint64_t base;
	uint64_t size;
};

struct laguna_boot_video {
	uint64_t base;
	uint32_t width;
	uint32_t height;
	uint32_t row_bytes;
	uint32_t depth;
	uint32_t graphics_mode;
};

enum laguna_display_error {
	LAGUNA_DISPLAY_OK = 0,
	LAGUNA_DISPLAY_BAD_ARGUMENT,
	LAGUNA_DISPLAY_BAD_MAGIC,
	LAGUNA_DISPLAY_BAD_VERSION,
	LAGUNA_DISPLAY_BAD_STRUCT_SIZE,
	LAGUNA_DISPLAY_BAD_RESERVED,
	LAGUNA_DISPLAY_BAD_CRC,
	LAGUNA_DISPLAY_MISSING_FLAGS,
	LAGUNA_DISPLAY_UNKNOWN_FLAGS,
	LAGUNA_DISPLAY_BAD_FORMAT,
	LAGUNA_DISPLAY_BAD_GEOMETRY,
	LAGUNA_DISPLAY_BAD_STRIDE,
	LAGUNA_DISPLAY_BAD_CPU_PA,
	LAGUNA_DISPLAY_BAD_CPU_SIZE,
	LAGUNA_DISPLAY_BAD_IOVA,
	LAGUNA_DISPLAY_BAD_MAPPING,
	LAGUNA_DISPLAY_BAD_RESERVATION,
	LAGUNA_DISPLAY_BAD_CACHE,
	LAGUNA_DISPLAY_BAD_PRODUCER,
	LAGUNA_DISPLAY_BAD_GENERATION,
	LAGUNA_DISPLAY_RANGE_OVERFLOW,
	LAGUNA_DISPLAY_RANGE_OVERLAP
};

/* Set struct_size/version-independent CRC after the producer fills the record. */
void laguna_display_handoff_seal(struct laguna_display_handoff *handoff);

/* Validate all fields and ensure the reserved framebuffer avoids forbidden RAM. */
enum laguna_display_error laguna_display_validate(
	const struct laguna_display_handoff *handoff,
	const struct laguna_memory_range *forbidden,
	size_t forbidden_count);

/* Convert a valid record into the boot_args.video equivalent; never touches MMIO. */
enum laguna_display_error laguna_display_boot_video(
	const struct laguna_display_handoff *handoff,
	const struct laguna_memory_range *forbidden,
	size_t forbidden_count,
	struct laguna_boot_video *video);

const char *laguna_display_error_string(enum laguna_display_error error);

/* CPM power-on sequence: data only.  The executor owns polled MBA transport. */
enum laguna_cpm_step {
	LAGUNA_CPM_QUEUE_RESET = 0,
	LAGUNA_CPM_DPU_TOP_ON,
	LAGUNA_CPM_DSI0_ON,
	LAGUNA_CPM_FE0_ON,
	LAGUNA_CPM_FE1_ON
};

enum laguna_cpm_completion {
	LAGUNA_CPM_COMPLETION_TX_DONE = 0,
	LAGUNA_CPM_COMPLETION_LPB_ACK_AND_OPTIONAL_EVENT,
	LAGUNA_CPM_COMPLETION_LPCM_ACK_ZERO
};

struct laguna_cpm_frame {
	enum laguna_cpm_step step;
	uint32_t sequence;
	uint32_t token;
	uint32_t words[4]; /* queue header + three service payload words */
	enum laguna_cpm_completion completion;
	uint32_t destination_service;
};

/* Returns 0 on invalid arguments/insufficient capacity, otherwise frame count. */
size_t laguna_dpu_power_on_sequence(struct laguna_cpm_frame *frames,
	size_t capacity, bool include_fe1);

/* Sequence values are 1..13.  0 (initial) and 13 map to 1; other invalid values map to 0. */
uint32_t laguna_cpm_next_sequence(uint32_t current);

#ifdef __cplusplus
}
#endif

#endif /* LAGUNA_RENDER_PORT_H */
