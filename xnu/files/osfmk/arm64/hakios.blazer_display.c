/*
 * Consume a display-owning first stage's framebuffer handoff, never the DPU.
 *
 * /chosen/laguna-display-handoff = { record_pa:u64, 128:u64 } points to the
 * versioned CRC'd wire record in laguna_render_port.h. The optional
 * /chosen/laguna-display-forbidden property is an array of {base,size} u64
 * pairs and must carry every FDT no-map/dynamic exclusion the producer knows.
 * The kernel window and record page are always included; ramoops is included
 * whenever its geometry is supplied. A present-but-invalid record clears the
 * video handoff and leaves the system headless. An absent record preserves a
 * valid loader GOP/ramfb video (the normal QEMU path).
 */
#include <arm64/hakios.blazer_display.h>
#include <arm64/laguna_render_port.h>
#include <mach/mach_types.h>
#include <pexpert/boot.h>
#include <pexpert/device_tree.h>
#include <pexpert/pexpert.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern void ml_io_unmap(vm_offset_t addr, vm_size_t size);

#define DISPLAY_FORBIDDEN_MAX 40
#define DISPLAY_FORBIDDEN_DT_MAX 32

static uint32_t display_cache_kind;

static bool
display_forbidden_add(struct laguna_memory_range *ranges, size_t *count,
    uint64_t base, uint64_t size)
{
	if (size == 0 || base + size < base || *count >= DISPLAY_FORBIDDEN_MAX) {
		return false;
	}
	ranges[*count].base = base;
	ranges[*count].size = size;
	(*count)++;
	return true;
}

static void
display_headless(boot_args *args)
{
	if (args != NULL) {
		bzero(&args->Video, sizeof(args->Video));
	}
	bzero(&PE_state.video, sizeof(PE_state.video));
	display_cache_kind = 0;
}

void
hakios_blazer_display_init(void)
{
	DTEntry node;
	uintptr_t const *reg = NULL;
	unsigned int size = 0;
	boot_args *args = (boot_args *)PE_state.bootArgs;
	struct laguna_memory_range forbidden[DISPLAY_FORBIDDEN_MAX];
	size_t count = 0;
	struct laguna_boot_video video;
	const struct laguna_display_handoff *handoff;
	enum laguna_display_error error;
	vm_offset_t mapping;

	if (SecureDTLookupEntry(NULL, "/chosen/laguna-display-handoff", &node) != kSuccess) {
		return;                         /* loader GOP/ramfb remains authoritative */
	}
	if (args == NULL ||
	    SecureDTGetProperty(node, "reg", (void const **)&reg, &size) != kSuccess ||
	    size != 2 * sizeof(uint64_t) || reg[0] == 0 || reg[1] != LAGUNA_DISPLAY_HANDOFF_SIZE) {
		kprintf("blazer-display: malformed handoff property; video disabled\n");
		display_headless(args);
		return;
	}
	mapping = ml_io_map((vm_offset_t)reg[0], (vm_size_t)reg[1]);
	if (mapping == 0) {
		kprintf("blazer-display: cannot map handoff at %llx; video disabled\n",
		    (unsigned long long)reg[0]);
		display_headless(args);
		return;
	}
	handoff = (const struct laguna_display_handoff *)mapping;
	if (!display_forbidden_add(forbidden, &count, reg[0], reg[1]) ||
	    (args->memSize != 0 && !display_forbidden_add(forbidden, &count,
	    args->physBase, args->memSize))) {
		kprintf("blazer-display: invalid forbidden range; video disabled\n");
		ml_io_unmap(mapping, (vm_size_t)reg[1]);
		display_headless(args);
		return;
	}
	{
		uint64_t ramoops = 0, ramoops_size = 0;
		bool have_base = PE_parse_boot_argn("blazer_ramoops_base", &ramoops, sizeof(ramoops));
		bool have_size = PE_parse_boot_argn("blazer_ramoops_size", &ramoops_size, sizeof(ramoops_size));

		if (have_base && have_size &&
		    !display_forbidden_add(forbidden, &count, ramoops, ramoops_size)) {
			kprintf("blazer-display: invalid ramoops exclusion; video disabled\n");
			ml_io_unmap(mapping, (vm_size_t)reg[1]);
			display_headless(args);
			return;
		}
	}
	if (SecureDTLookupEntry(NULL, "/chosen/laguna-display-forbidden", &node) == kSuccess) {
		uintptr_t const *ranges = NULL;
		unsigned int bytes = 0;

		if (SecureDTGetProperty(node, "reg", (void const **)&ranges, &bytes) != kSuccess ||
		    bytes == 0 || bytes > DISPLAY_FORBIDDEN_DT_MAX * 2 * sizeof(uint64_t) ||
		    bytes % (2 * sizeof(uint64_t)) != 0) {
			kprintf("blazer-display: malformed forbidden-range property; video disabled\n");
			ml_io_unmap(mapping, (vm_size_t)reg[1]);
			display_headless(args);
			return;
		}
		for (unsigned i = 0; i < bytes / (2 * sizeof(uint64_t)); i++) {
			if (!display_forbidden_add(forbidden, &count, ranges[2 * i], ranges[2 * i + 1])) {
				kprintf("blazer-display: too many/invalid forbidden ranges; video disabled\n");
				ml_io_unmap(mapping, (vm_size_t)reg[1]);
				display_headless(args);
				return;
			}
		}
	}

	error = laguna_display_boot_video(handoff, forbidden, count, &video);
	if (error != LAGUNA_DISPLAY_OK) {
		kprintf("blazer-display: handoff rejected (%s); video disabled\n",
		    laguna_display_error_string(error));
		ml_io_unmap(mapping, (vm_size_t)reg[1]);
		display_headless(args);
		return;
	}
	display_cache_kind = handoff->cache_kind;
	kprintf("blazer-display: %s: %ux%u stride %u at %llx, %u forbidden ranges, cache %u\n",
	    handoff->producer, video.width, video.height, video.row_bytes,
	    (unsigned long long)video.base, (unsigned)count, display_cache_kind);
	ml_io_unmap(mapping, (vm_size_t)reg[1]);

	args->Video.v_baseAddr = video.base;
	args->Video.v_display = video.graphics_mode;
	args->Video.v_rowBytes = video.row_bytes;
	args->Video.v_width = video.width;
	args->Video.v_height = video.height;
	args->Video.v_depth = video.depth;
	PE_state.video.v_baseAddr = video.base;
	PE_state.video.v_rowBytes = video.row_bytes;
	PE_state.video.v_width = video.width;
	PE_state.video.v_height = video.height;
	PE_state.video.v_depth = video.depth;
	PE_state.video.v_display = video.graphics_mode;
	PE_state.video.v_scale = 1;
	PE_state.video.v_rotate = 0;
	PE_state.video.v_length = (unsigned long)video.row_bytes * video.height;
}

bool
hakios_blazer_display_clean_on_present(void)
{
	return display_cache_kind == LAGUNA_DISPLAY_CACHE_CLEAN_ON_PRESENT;
}
