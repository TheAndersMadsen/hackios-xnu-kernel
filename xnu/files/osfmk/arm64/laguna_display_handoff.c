/* Data-only display handoff validation.  No hardware access. */
#include "laguna_render_port.h"

#include <string.h>

_Static_assert(sizeof(struct laguna_display_handoff) == LAGUNA_DISPLAY_HANDOFF_SIZE,
	"laguna_display_handoff wire size changed");
_Static_assert(offsetof(struct laguna_display_handoff, crc32) ==
	LAGUNA_DISPLAY_HANDOFF_CRC_OFFSET, "laguna_display_handoff CRC offset changed");

static uint32_t
crc32_update(uint32_t crc, const uint8_t *data, size_t size)
{
	crc = ~crc;
	for (size_t i = 0; i < size; i++) {
		crc ^= data[i];
		for (unsigned bit = 0; bit < 8; bit++) {
			uint32_t mask = (uint32_t)-(int32_t)(crc & 1u);
			crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & mask);
		}
	}
	return ~crc;
}

static bool
producer_has_nul(const uint8_t *producer, size_t size)
{
	for (size_t i = 0; i < size; i++) {
		if (producer[i] == 0) {
			return true;
		}
	}
	return false;
}

static uint32_t
handoff_crc(const struct laguna_display_handoff *handoff)
{
	struct laguna_display_handoff copy = *handoff;
	copy.crc32 = 0;
	return crc32_update(0, (const uint8_t *)&copy, sizeof(copy));
}

void
laguna_display_handoff_seal(struct laguna_display_handoff *handoff)
{
	if (handoff == NULL) {
		return;
	}
	handoff->magic = LAGUNA_DISPLAY_HANDOFF_MAGIC;
	handoff->version = LAGUNA_DISPLAY_HANDOFF_VERSION;
	handoff->struct_size = LAGUNA_DISPLAY_HANDOFF_SIZE;
	handoff->reserved[0] = 0;
	handoff->reserved[1] = 0;
	handoff->reserved[2] = 0;
	handoff->crc32 = 0;
	handoff->crc32 = handoff_crc(handoff);
}

static bool
range_end(const struct laguna_memory_range *range, uint64_t *end)
{
	if (range->size == 0 || range->base + range->size < range->base) {
		return false;
	}
	*end = range->base + range->size;
	return true;
}

static bool
ranges_overlap(uint64_t a_base, uint64_t a_end, uint64_t b_base, uint64_t b_end)
{
	return a_base < b_end && b_base < a_end;
}

static enum laguna_display_error
validate_forbidden(const struct laguna_display_handoff *handoff,
	const struct laguna_memory_range *forbidden, size_t forbidden_count)
{
	uint64_t fb_end;
	struct laguna_memory_range fb = {
		.base = handoff->cpu_pa,
		.size = handoff->cpu_size
	};

	if (!range_end(&fb, &fb_end)) {
		return LAGUNA_DISPLAY_RANGE_OVERFLOW;
	}
	for (size_t i = 0; i < forbidden_count; i++) {
		uint64_t other_end;
		if (!range_end(&forbidden[i], &other_end)) {
			return LAGUNA_DISPLAY_RANGE_OVERFLOW;
		}
		if (ranges_overlap(fb.base, fb_end, forbidden[i].base, other_end)) {
			return LAGUNA_DISPLAY_RANGE_OVERLAP;
		}
	}
	return LAGUNA_DISPLAY_OK;
}

enum laguna_display_error
laguna_display_validate(const struct laguna_display_handoff *handoff,
	const struct laguna_memory_range *forbidden, size_t forbidden_count)
{
	uint64_t needed, end;

	if (handoff == NULL || (forbidden_count != 0 && forbidden == NULL)) {
		return LAGUNA_DISPLAY_BAD_ARGUMENT;
	}
	if (handoff->magic != LAGUNA_DISPLAY_HANDOFF_MAGIC) {
		return LAGUNA_DISPLAY_BAD_MAGIC;
	}
	if (handoff->version != LAGUNA_DISPLAY_HANDOFF_VERSION) {
		return LAGUNA_DISPLAY_BAD_VERSION;
	}
	if (handoff->struct_size != LAGUNA_DISPLAY_HANDOFF_SIZE) {
		return LAGUNA_DISPLAY_BAD_STRUCT_SIZE;
	}
	if (handoff->reserved[0] != 0 || handoff->reserved[1] != 0 || handoff->reserved[2] != 0) {
		return LAGUNA_DISPLAY_BAD_RESERVED;
	}
	if (handoff->crc32 != handoff_crc(handoff)) {
		return LAGUNA_DISPLAY_BAD_CRC;
	}
	if ((handoff->flags & LAGUNA_DISPLAY_REQUIRED_FLAGS) != LAGUNA_DISPLAY_REQUIRED_FLAGS) {
		return LAGUNA_DISPLAY_MISSING_FLAGS;
	}
	if ((handoff->flags & ~LAGUNA_DISPLAY_KNOWN_FLAGS) != 0) {
		return LAGUNA_DISPLAY_UNKNOWN_FLAGS;
	}
	if (handoff->pixel_format != LAGUNA_PIXEL_ARGB8888 &&
	    handoff->pixel_format != LAGUNA_PIXEL_XRGB8888) {
		return LAGUNA_DISPLAY_BAD_FORMAT;
	}
	if (handoff->width == 0 || handoff->height == 0 ||
	    handoff->width > 8192 || handoff->height > 8192 ||
	    handoff->refresh_millihz < 1000 || handoff->refresh_millihz > 240000) {
		return LAGUNA_DISPLAY_BAD_GEOMETRY;
	}
	if ((uint64_t)handoff->stride < (uint64_t)handoff->width * 4 ||
	    (handoff->stride & 0xffu) != 0) {
		return LAGUNA_DISPLAY_BAD_STRIDE;
	}
	needed = (uint64_t)handoff->stride * handoff->height;
	if (handoff->cpu_size < needed || (handoff->cpu_size & 0xfffu) != 0) {
		return LAGUNA_DISPLAY_BAD_CPU_SIZE;
	}
	if (handoff->cpu_pa < UINT64_C(0x80000000) ||
	    handoff->cpu_pa >= (UINT64_C(1) << 40) || (handoff->cpu_pa & 0xffu) != 0 ||
	    handoff->cpu_pa + handoff->cpu_size < handoff->cpu_pa) {
		return LAGUNA_DISPLAY_BAD_CPU_PA;
	}
	end = handoff->cpu_pa + handoff->cpu_size;
	if (end > (UINT64_C(1) << 40)) {
		return LAGUNA_DISPLAY_BAD_CPU_PA;
	}
	if ((handoff->dpu_iova & 0xffu) != 0 ||
	    handoff->dpu_iova + handoff->cpu_size < handoff->dpu_iova) {
		return LAGUNA_DISPLAY_BAD_IOVA;
	}
	if (handoff->reserve_pa != handoff->cpu_pa || handoff->reserve_size != handoff->cpu_size) {
		return LAGUNA_DISPLAY_BAD_RESERVATION;
	}
	if (handoff->mapping_kind == LAGUNA_DISPLAY_MAPPING_IDENTITY) {
		if (handoff->dpu_iova != handoff->cpu_pa) {
			return LAGUNA_DISPLAY_BAD_MAPPING;
		}
	} else if (handoff->mapping_kind != LAGUNA_DISPLAY_MAPPING_FIRST_STAGE_SMMU) {
		return LAGUNA_DISPLAY_BAD_MAPPING;
	}
	if (handoff->cache_kind != LAGUNA_DISPLAY_CACHE_WRITE_COMBINE &&
	    handoff->cache_kind != LAGUNA_DISPLAY_CACHE_CLEAN_ON_PRESENT) {
		return LAGUNA_DISPLAY_BAD_CACHE;
	}
	if (handoff->producer[0] < 33 || handoff->producer[0] > 126 ||
	    !producer_has_nul(handoff->producer, sizeof(handoff->producer))) {
		return LAGUNA_DISPLAY_BAD_PRODUCER;
	}
	if (handoff->commit_generation == 0) {
		return LAGUNA_DISPLAY_BAD_GENERATION;
	}
	return validate_forbidden(handoff, forbidden, forbidden_count);
}

enum laguna_display_error
laguna_display_boot_video(const struct laguna_display_handoff *handoff,
	const struct laguna_memory_range *forbidden, size_t forbidden_count,
	struct laguna_boot_video *video)
{
	enum laguna_display_error error;

	if (video == NULL) {
		return LAGUNA_DISPLAY_BAD_ARGUMENT;
	}
	error = laguna_display_validate(handoff, forbidden, forbidden_count);
	if (error != LAGUNA_DISPLAY_OK) {
		return error;
	}
	video->base = handoff->cpu_pa;
	video->width = handoff->width;
	video->height = handoff->height;
	video->row_bytes = handoff->stride;
	video->depth = 32;
	video->graphics_mode = 1;
	return LAGUNA_DISPLAY_OK;
}

const char *
laguna_display_error_string(enum laguna_display_error error)
{
	static const char *const names[] = {
		"ok", "bad argument", "bad magic", "bad version", "bad struct size",
		"bad reserved field", "bad CRC", "missing flags", "unknown flags",
		"bad pixel format", "bad geometry", "bad stride", "bad CPU physical address",
		"bad CPU size", "bad DPU IOVA", "bad mapping", "bad reservation",
		"bad cache policy", "bad producer", "bad commit generation",
		"range overflow", "forbidden range overlap"
	};
	if ((unsigned)error >= sizeof(names) / sizeof(names[0])) {
		return "unknown error";
	}
	return names[error];
}
