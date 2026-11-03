/*
 * Consume a display-owning first stage's framebuffer handoff, never the DPU.
 *
 * /chosen/laguna-display-handoff = { record_pa:u64, 128:u64 } points to the
 * versioned CRC'd wire record in laguna_render_port.h. The optional
 * /chosen/laguna-display-forbidden property is an array of {base,size} u64
 * pairs and must carry every FDT no-map/dynamic exclusion the producer knows.
 * The kernel window and record page are always included; ramoops is included
 * whenever its geometry is supplied.
 *
 * Tiered, fail-closed decision (DISPLAY-PRODUCER §4):
 *   1. record with the full ownership flag set (…SCANOUT_LIVE) validates
 *      against laguna_display_validate()  -> adopt into boot_args.video and
 *      PE_state.video; MiniIOMFB picks it up and honors cache_kind
 *      (WC: DSB on present; CLEAN_ON_PRESENT: flush to PoC).
 *   2. record with only the cold flag set (CPU_PA_VALID, LINEAR_32BPP,
 *      PHYS_CONTIGUOUS, RESERVED — and no ownership/liveness bit) validates
 *      against laguna_display_validate_cold() -> RAM console exactly as the
 *      loader left it, re-populated from the CRC'd record; verdict stamp
 *      "display-unowned"; zero DPU/DSI/CDPHY MMIO.
 *   3. anything else (unknown bits, partial ownership claims, validator
 *      refusals, malformed properties/ranges) -> headless; video cleared.
 *   4. no record -> the loader's video stays authoritative (GOP/ramfb on
 *      QEMU, the shim RAM-console PA on the phone) but unowned.
 * A record carrying any ownership/liveness bit must satisfy the FULL flag
 * contract; partial claims are refused, never half-adopted.
 *
 * io.display.native=1 (Path B native producer) is deliberately NOT honored
 * by this tree: the flag only earns an explicit refusal verdict.  The shim
 * record path always runs first; a native producer needs the DPU-INIT §6
 * providers (CPM executor, DPU SMMU owner, clock/panel init) that do not
 * exist here, so the gate permits nothing and touches no display MMIO.
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

/* Field-for-identical writes to the linked M2 adopt path: boot_args.Video
 * stays physical (initialize_screen maps it WCOMB while kernel_map is still
 * NULL), PE_state.video gets the unpacked geometry. */
static void
display_adopt(boot_args *args, const struct laguna_boot_video *video)
{
	if (args != NULL) {
		args->Video.v_baseAddr = video->base;
		args->Video.v_display = video->graphics_mode;
		args->Video.v_rowBytes = video->row_bytes;
		args->Video.v_width = video->width;
		args->Video.v_height = video->height;
		args->Video.v_depth = video->depth;
	}
	PE_state.video.v_baseAddr = video->base;
	PE_state.video.v_rowBytes = video->row_bytes;
	PE_state.video.v_width = video->width;
	PE_state.video.v_height = video->height;
	PE_state.video.v_depth = video->depth;
	PE_state.video.v_display = video->graphics_mode;
	PE_state.video.v_scale = 1;
	PE_state.video.v_rotate = 0;
	PE_state.video.v_length = (unsigned long)video->row_bytes * video->height;
}

/* Same field mapping laguna_display_boot_video() produces for a live record,
 * applied to a cold (RAM-console) record. */
static void
display_cold_video(const struct laguna_display_handoff *handoff,
    struct laguna_boot_video *video)
{
	video->base = handoff->cpu_pa;
	video->width = handoff->width;
	video->height = handoff->height;
	video->row_bytes = handoff->stride;
	video->depth = 32;
	video->graphics_mode = 1;
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
	uint64_t flags;
	uint32_t native = 0;

	/* Native Path-B producer gate: deliberately after the shim record path
	 * and not implemented in this tree (no kernel CPM executor, no DPU SMMU
	 * owner, no clock/panel provider).  The boot-arg earns an explicit,
	 * stamped refusal — never a silent modeset and no display MMIO. */
	(void)PE_parse_boot_argn("io.display.native", &native, sizeof(native));
	if (native == 1) {
		kprintf("blazer-display: verdict=NATIVE-REFUSED io.display.native=1: Path B producer "
		    "not implemented; zero DC9x00/DSI/CDPHY MMIO issued\n");
	} else if (native != 0) {
		kprintf("blazer-display: verdict=NATIVE-REFUSED io.display.native=%u: unsupported value\n",
		    native);
	}

	if (SecureDTLookupEntry(NULL, "/chosen/laguna-display-handoff", &node) != kSuccess) {
		/* No record: the loader's video stays authoritative but unowned
		 * (QEMU GOP/ramfb; the phone's shim RAM-console PA until S1
		 * lands the record builder). */
		if (args != NULL && args->Video.v_baseAddr != 0) {
			kprintf("blazer-display: verdict=UNOWNED no handoff record; loader video "
			    "%ux%u stride %u at %llx kept display-unowned\n",
			    args->Video.v_width, args->Video.v_height, args->Video.v_rowBytes,
			    (unsigned long long)args->Video.v_baseAddr);
		} else {
			kprintf("blazer-display: verdict=HEADLESS no handoff record, no loader video\n");
		}
		return;
	}
	if (args == NULL ||
	    SecureDTGetProperty(node, "reg", (void const **)&reg, &size) != kSuccess ||
	    size != 2 * sizeof(uint64_t) || reg[0] == 0 || reg[1] != LAGUNA_DISPLAY_HANDOFF_SIZE) {
		kprintf("blazer-display: verdict=HEADLESS malformed handoff property; video disabled\n");
		display_headless(args);
		return;
	}
	mapping = ml_io_map((vm_offset_t)reg[0], (vm_size_t)reg[1]);
	if (mapping == 0) {
		kprintf("blazer-display: verdict=HEADLESS cannot map handoff at %llx; video disabled\n",
		    (unsigned long long)reg[0]);
		display_headless(args);
		return;
	}
	handoff = (const struct laguna_display_handoff *)mapping;
	if (!display_forbidden_add(forbidden, &count, reg[0], reg[1]) ||
	    (args->memSize != 0 && !display_forbidden_add(forbidden, &count,
	    args->physBase, args->memSize))) {
		kprintf("blazer-display: verdict=HEADLESS invalid forbidden range; video disabled\n");
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
			kprintf("blazer-display: verdict=HEADLESS invalid ramoops exclusion; video disabled\n");
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
			kprintf("blazer-display: verdict=HEADLESS malformed forbidden-range property; video disabled\n");
			ml_io_unmap(mapping, (vm_size_t)reg[1]);
			display_headless(args);
			return;
		}
		for (unsigned i = 0; i < bytes / (2 * sizeof(uint64_t)); i++) {
			if (!display_forbidden_add(forbidden, &count, ranges[2 * i], ranges[2 * i + 1])) {
				kprintf("blazer-display: verdict=HEADLESS too many/invalid forbidden ranges; video disabled\n");
				ml_io_unmap(mapping, (vm_size_t)reg[1]);
				display_headless(args);
				return;
			}
		}
	}

	/*
	 * Tier selection by flag policy (fail-closed): unknown bits refuse, a
	 * complete ownership claim goes through the full validator, a clean
	 * cold record goes through the cold validator, and everything in
	 * between — especially partial ownership claims — refuses headless.
	 */
	flags = handoff->flags;
	if ((flags & ~LAGUNA_DISPLAY_KNOWN_FLAGS) != 0) {
		kprintf("blazer-display: verdict=HEADLESS unknown flags 0x%llx; video disabled\n",
		    (unsigned long long)flags);
		ml_io_unmap(mapping, (vm_size_t)reg[1]);
		display_headless(args);
		return;
	}
	if ((flags & LAGUNA_DISPLAY_REQUIRED_FLAGS) == LAGUNA_DISPLAY_REQUIRED_FLAGS) {
		/* Tier 1: producer proved power + config + mapping + live scanout. */
		error = laguna_display_boot_video(handoff, forbidden, count, &video);
		if (error != LAGUNA_DISPLAY_OK) {
			kprintf("blazer-display: verdict=HEADLESS live record rejected (%s); video disabled\n",
			    laguna_display_error_string(error));
			ml_io_unmap(mapping, (vm_size_t)reg[1]);
			display_headless(args);
			return;
		}
		display_cache_kind = handoff->cache_kind;
		kprintf("blazer-display: verdict=ADOPTED scanout-live %s: %ux%u stride %u at %llx, "
		    "%u forbidden ranges, cache %u, gen %u\n",
		    handoff->producer, video.width, video.height, video.row_bytes,
		    (unsigned long long)video.base, (unsigned)count, display_cache_kind,
		    handoff->commit_generation);
		ml_io_unmap(mapping, (vm_size_t)reg[1]);
		display_adopt(args, &video);
		return;
	}
	if ((flags & LAGUNA_DISPLAY_COLD_REQUIRED) == LAGUNA_DISPLAY_COLD_REQUIRED &&
	    (flags & LAGUNA_DISPLAY_COLD_FORBIDDEN) == 0) {
		/* Tier 2 (the expected cold path): reserved linear RAM console,
		 * no scanout claim.  The CRC'd record wins over the unsealed
		 * boot_args page if they ever disagree. */
		error = laguna_display_validate_cold(handoff, forbidden, count);
		if (error != LAGUNA_DISPLAY_OK) {
			kprintf("blazer-display: verdict=HEADLESS cold record rejected (%s); video disabled\n",
			    laguna_display_error_string(error));
			ml_io_unmap(mapping, (vm_size_t)reg[1]);
			display_headless(args);
			return;
		}
		display_cold_video(handoff, &video);
		if (args->Video.v_baseAddr != 0 &&
		    (args->Video.v_baseAddr != video.base ||
		    args->Video.v_width != video.width || args->Video.v_height != video.height ||
		    args->Video.v_rowBytes != video.row_bytes)) {
			kprintf("blazer-display: video mismatch: loader b=%llx %ux%u r=%u d=%u vs record "
			    "b=%llx %ux%u r=%u; CRC'd record wins\n",
			    (unsigned long long)args->Video.v_baseAddr,
			    args->Video.v_width, args->Video.v_height, args->Video.v_rowBytes,
			    args->Video.v_depth,
			    (unsigned long long)video.base, video.width, video.height,
			    video.row_bytes);
		}
		display_cache_kind = handoff->cache_kind;
		kprintf("blazer-display: verdict=RAMCONSOLE display-unowned %s: %ux%u stride %u at %llx, "
		    "%u forbidden ranges, cache %u, gen %u\n",
		    handoff->producer, video.width, video.height, video.row_bytes,
		    (unsigned long long)video.base, (unsigned)count, display_cache_kind,
		    handoff->commit_generation);
		ml_io_unmap(mapping, (vm_size_t)reg[1]);
		display_adopt(args, &video);
		return;
	}
	kprintf("blazer-display: verdict=HEADLESS flags 0x%llx are neither the full nor the cold "
	    "contract (partial ownership claim); video disabled\n", (unsigned long long)flags);
	ml_io_unmap(mapping, (vm_size_t)reg[1]);
	display_headless(args);
}

bool
hakios_blazer_display_clean_on_present(void)
{
	return display_cache_kind == LAGUNA_DISPLAY_CACHE_CLEAN_ON_PRESENT;
}
