/*
 * hakios.blazer_panicdump — capture the kernel's panic output into the ramoops
 * console zone (PA 0x95200000) on the panic path, plus a magic-tagged mirror
 * record in the pmsg zone (PA 0x95400000).
 *
 * Device truth driving this: the teguload v3 ladder ran XNU on the Pixel 10
 * Pro and the kernel panicked ~119 s in ("Early Kernel PANIC", CDD reason
 * 0xbab9) with NO backtrace or console captured anywhere — no UART, no fbcon
 * dump, no RAM dump artifacts. The ramoops reserved-memory zone at
 * 0x95200000 (4 MiB: console 2 MiB then pmsg 2 MiB, DT node ramoops@95200000)
 * survives resets and is exposed by Android as /sys/fs/pstore/console-ramoops-0
 * (ABL console) and /sys/fs/pstore/pmsg-ramoops-0 (the proven marker/diag
 * readback channel). Writing the panic output there makes the next Android
 * boot hand us the backtrace.
 *
 * Format is Linux fs/pstore/ram_core.c persistent_ram semantics, no ECC (the
 * DT node carries no ecc-size):
 *   struct persistent_ram_buffer { u32 sig; u32 start; u32 size; u8 data[]; }
 *   sig   = 0x43474244 ('DBGC' = PERSISTENT_RAM_SIG ^ per-zone sig(0))
 *   start = ring offset of the oldest valid byte, start <= buffer_size
 *   size  = valid byte count, size <= buffer_size
 *   data  = ring at zone offset 12; buffer_size = zone_size - 12
 * The console-zone append mirrors persistent_ram_write() exactly so Linux
 * linearizes our bytes into console-ramoops-0 right after ABL's log.
 *
 * Boot args:
 *   blazer_panicdump=1      explicitly map and arm capture; default 0/off.
 *                          Without it, no phone physical address is touched.
 *   blazer_panictest=<sec>  default 0 (off). >0 arms the QEMU test thread.
 *   blazer_ramoops_base/size, blazer_console_size, blazer_pmsg_base/size
 *                          DT-generated geometry overrides (see tensor-args.py).
 *
 * The panic-time writer is minimal and fault-guarded: no allocation, no
 * locks, no printf, every access bounds-checked against the mapped zone,
 * data written BEFORE the header (a fault mid-write leaves the header
 * untouched, i.e. the old consistent state Linux already knows about).
 */
#ifndef _ARM64_HAKIOS_BLAZER_PANICDUMP_H_
#define _ARM64_HAKIOS_BLAZER_PANICDUMP_H_

#include <stddef.h>
#include <stdint.h>
#include <sys/cdefs.h>                 /* __unused (stub path) */

#ifndef HAKIOS_BLAZER_PANICDUMP_HOSTTEST
#include <mach/mach_types.h>           /* vm_size_t (kernel builds) */
#else
#include <stdint.h>                    /* host: vm_size_t = uint64_t */
typedef uint64_t vm_size_t;
#endif

__BEGIN_DECLS

/*
 * Late arm_init hook: parse boot args, map the ramoops zone (Device memory —
 * the physmap window does NOT cover the low bank on either the device, whose
 * declared-clean bank is 0x8_80000000+, or the 256 MiB QEMU contract that
 * ends at 0x90000000, so phystokv() must not be used here), validate the
 * persistent_ram header, and arm the blazer_panictest thread.
 */
void hakios_blazer_panicdump_init(void);

/*
 * Panic-path hook, called from SavePanicInfo (osfmk/arm/model_dep.c) after
 * paniclog_flush() — the paniclog text (panic string + all backtraces) is
 * complete in [paniclog, paniclog + paniclog_len) at that point. Appends the
 * record into the console zone and writes the pmsg mirror. Safe to call when
 * init never ran (no-ops).
 */
void hakios_blazer_panicdump_capture(const char *msg, const char *paniclog,
    vm_size_t paniclog_len);

/*
 * Late-IOKit hook, called from AppleKeyStore::start (the first laguna IOKit
 * driver — the earliest fully-threaded point on every delivery path):
 * spawns the blazer_panictest panic needle (kernel threads are not runnable
 * during arm_init — spawning there wedges the boot).
 */
void hakios_blazer_panicdump_bootdone(void);

__END_DECLS

#endif /* _ARM64_HAKIOS_BLAZER_PANICDUMP_H_ */
