# ADT node requirements for the laguna XNU (G2 audit, 2026-10-27)

What `mkadt.py` must emit so the early-boot path in xnu-8796.121.2 (the
research/laguna-foundation/kernel-blazer tree, DEVELOPMENT_ARM64_BCM2837)
finds everything it looks up. Every row was verified against the kernel
source on 2026-10-27; "consumer" cites the file/line that reads the node or
property. Host-side replica of every lookup (strcmp/find_entry semantics from
pexpert/gen/device_tree.c) passes against the regenerated `laguna.adt` and
`laguna-qemu.adt` (see "Validation").

## The three device-log gaps and their fixes

The 2026-10-27 v51 device boot
(research/laguna-usb-fix/attempts/20261027-103925-v51/reader-full.txt) printed:

```
pe_arm_map_interrupt_controller: soc_phys:  0x5880000
pe_arm_map_interrupt_controller: failed to find the interrupt-controller.
pe_arm_init_debug: failed to find cpu-debug-interface
debug_log_init: Error!! gPanicBase is still not initialized   (x2)
```

Root causes, all ADT-side:

1. **`interrupt-controller` must be the C-string `"master"`.**
   `pe_identify_machine.c:536` does `SecureDTFindEntry("interrupt-controller",
   "master")`, and `find_entry` compares the *raw property value* with
   `strcmp` (device_tree.c:312). The old tree emitted an empty value (the tegu
   shape), which never matches → `gPicBase` stays 0. Note tegu boots fine with
   the same warning (tegu NOTES.md) — the warning itself was never fatal; the
   fix removes it and un-gates the timer lookup (see 2).

2. **`device_type = "timer"` node is mandatory after (1).**
   `pe_arm_map_interrupt_controller` next requires `device_type "timer"` with
   a `reg` (pe_identify_machine.c:547) or it *still* returns 0 ("failed to
   find the timer."). The old tree had no timer node; the failure was masked
   by (1) returning early. On arm64 both mappings are map-only
   (`pe_arm_init_timer` installs empty tbd funcs; `ml_init_timebase` only
   stores them — machine_routines.c:845, and every consumer is NULL-guarded),
   so the nodes only need real device-space addresses, never RAM.

3. **`device_type = "cpu-debug-interface"` node.**
   `pe_arm_init_debug` (pe_identify_machine.c:485) hands the node's `reg` to
   `ml_init_arm_debug_interface(args, ml_io_map(gSocPhys + reg[0], reg[1]))`,
   which stores `cpu_data_t.cpu_debug_interface_map` and calls `do_debugid()`
   — a register read of ID_AA64DFR0_EL1 only (machine_cpuid.c:74). No reader
   of the pointer dereferences it in this lineage. **Deliberately no
   `panic-trace-mode` property**: `panic_trace` is a TUNABLE_DT that defaults
   to `panic_trace_disabled` (pe_identify_machine.c:207), and without it
   `pe_init_debug_command` never runs, so the debug command buffers stay
   empty and no MMIO is ever written through this mapping. (This also keeps
   the v51-observed "panic_trace: PE_arm_debug_enable_trace enter/exit" calls
   in arm_init.c:795 / debug.c:547 as no-ops.)

4. **`/pram` + `/chosen embedded-panic-log-size` fix `gPanicBase`.**
   `check_for_panic_log` (pe_init.c:139–155) is the ADT path to
   `gPanicBase = ml_io_map_wcomb(pram reg[0], embedded-panic-log-size)`;
   without it `debug_log_init` bails (osfmk/kern/debug.c:471) and the panic
   log/stackshot machinery stays dead. The BCM2837 board header documents the
   contract explicitly: "The panic log region comes from the device tree
   instead (/pram and /chosen/embedded-panic-log-size), like on Apple
   devices" (pexpert/pexpert/arm64/BCM2837.h). A real pram always wins over
   the blazer_panicdump .bss fallback (pe_init.c:102–112).
   **Placement — pram reg = 0x95300000, size = 0x100000, panic log size =
   0x80000 (device mode only):**
   - 0x95300000 is the upper half of the lga.dtsi ramoops console zone
     (probe.h `PSTORE_CONSOLE` 0x95200000 + 0x200000). It is guaranteed RAM
     the kexec loader never hands to XNU (the fleet window ends ≤ 0x90000000),
     so `vm_page_bootstrap` can never alias the wcomb panic mapping.
   - It starts *after* the persistent_ram zone header + shim console records
     (v51 used 73849 bytes), so `console-ramoops-0` survives an XNU panic;
     and it ends exactly at the pmsg zone start 0x95400000, so the kexec
     handoff records (0x95400100/0x95400120) and the compact panic mirror
     (0x95430000, the panic-capture lane's authoritative channel) are never
     touched. 0x80000 of log keeps the record inside 0x95300000..0x95380000.
   - QEMU mode keeps the old shape (no pram): 0x95200000 is not guest RAM on
     virt, and tegu NOTES.md documents the gPanicBase warning as benign there.

## Node/property inventory (what the generator emits and why)

| node | props | consumer (xnu-8796.121.2) | notes |
|---|---|---|---|
| `/` (device-tree) | `compatible` incl. `google,lga-blazer` | 0016 LagunaPlatformExpert IONameMatch; absence panics IOPanicPlatform | unchanged |
| `/chosen` | `dram-base`/`dram-size` | mirrors the boot-args window; kept consistent by the ramoops guard | unchanged |
| `/chosen` | `embedded-panic-log-size` u32 = 0x80000 | pe_init.c:151 → `gPanicSize` | **new, device only** |
| `/chosen/memory-map` | (empty) | no reader found in this lineage (grep of osfmk/pexpert/iokit) | kept empty — fail-closed: XNU RAM comes from boot args, not the ADT, so a "reserved" child here would be documentation, not enforcement |
| `/pram` | `reg` = 0x95300000,0x100000 | pe_init.c:139–155 → `gPanicBase` | **new, device only**; placement rationale above |
| `/defaults` | `no-effaceable-storage` | keybagd effaceable path | unchanged |
| `/cpus/cpuN` | MPIDR `reg`, frequencies | pe_identify_machine.c clock freq block; 0018 cpu driver; ml_parse_cpu_topology | unchanged |
| `/arm-io` | `ranges[1]` = soc base 0x5880000 | pe_arm_get_soc_base_phys (pe_identify_machine.c:178–197) → `gPESoCBasePhys`/`gSocPhys` | unchanged; parent 0 would disable serial + all maps |
| `/arm-io/gic` | **`interrupt-controller` = `"master\0"`**, `reg` 32 B (GICD, GICR), `virtual-timer-intid` | pe_identify_machine.c:536 → `gPicBase` (map-only); pe_fiq.c pe_init_fiq → GICD/GICR + vtimer INTID | **value fixed** from empty to "master"; reg/vtimer unchanged (the v51 log proves pe_init_fiq works) |
| `/arm-io/timer` | `device_type "timer"`, `reg` = 0x24074000−soc = 0x1e7f4000,0x10000 | pe_identify_machine.c:547 → `gTimerBase` (map-only on arm64) | **new**; laguna APT `apt@24074000` from the live FDT |
| `/arm-io/cpu-debug-interface` | `device_type "cpu-debug-interface"`, `reg` = 0x23802000−soc = 0x1df82000,0x1000 | pe_identify_machine.c:485 → `ml_init_arm_debug_interface` (store + register read) | **new**; CoreSight ETF `etf@23802000` from the live FDT; **no `panic-trace-mode`** (see 3) |
| `/arm-io/wdt` | `no-pmu` | watchdogd | unchanged |
| `/arm-io/uart0` | PL011, `boot-console` | pe_serial (QEMU only) | unchanged; laguna device mode has no uart node (DW-APB driverless, console = fb) |
| `/arm-io/disp0` | `display-subsystem`, canvas | IOMobileFramebuffer / AppleCLCD; absent laguna-dpu = DC probe gate (smoke check) | unchanged |
| `/laguna-fbcon` | fb reg/geometry | 0003 fbcon_probe | unchanged |

Rejected as not-read-by-this-kernel (fail-closed): `/arm-io/socs`,
`/arm-io/pmgr`, `/arm-io/power`, any `/chosen/memory-map` reserved children,
a video node beyond `disp0` (initialize_screen uses the boot-args Video
record, not the ADT), `/arm-io/console` (serial=3 → fb console path).

## Reserved-memory exclusions (ramoops)

XNU takes its RAM from the boot-args window (`args->memSize`,
arm_vm_init.c:1834), so the ADT cannot carve the ramoops zone out. The
generator enforces the exclusion instead: **device-mode builds fail with a
targeted error if `phys_base..phys_base+mem_size` overlaps
0x95200000..0x95600000** (console + pmsg + the 0x95430000 stamps). The
kexec-proven 256 M window (0x80000000..0x90000000) passes; the file's old 2 G
default now correctly refuses to build. QEMU mode is exempt (guest RAM
legitimately spans the address; there is no evidence zone on virt).

## Window override flags

`--phys-base=` (16 K-aligned, ≥ 0x80000000) and `--mem-size=` (16 K-aligned,
≤ 6 G) work unchanged and run through the same ramoops guard. `--qemu` and
`--bank1` unchanged.

## Validation (host, 2026-10-27)

- `python3 mkadt.py /tmp/fleet/g2-adt/laguna.adt --mem-size=0x10000000` and
  `... laguna-qemu.adt --qemu`: build OK (10384 B / 4904 B).
- adt round-trip: `serialize(parse(blob)) == blob` for both trees.
- Host replica of `SecureDTFindEntry`/`SecureDTLookupEntry` (strcmp + root
  child semantics from pexpert/gen/device_tree.c) against both trees: gSocPhys
  0x5880000/0x8000000, `gPicBase` = GICD, `gTimerBase` = 0x24074000,
  cpu-debug-interface = 0x23802000, `/arm-io/gic` reg 32 B + vtimer 27,
  pram/embedded-panic-log-size resolve only in device mode. All OK.
- 2 G default window: rejected with the ramoops overlap error (exit 1). OK.
- QEMU boot check (mach.blazer.marked.macho + new qemu ADT through the
  teguload shim pipeline, `-M virt,gic-version=3 -m 3G`): the guest exits at
  a clean PSCI shutdown BEFORE the kernel initializes msgbufp — identically
  with the new ADT and with a reconstructed old-shape control ADT (same
  44-line qemu log: handled shim data-aborts → one PSCI call → exit 0). The
  pre-XNU exit is pre-existing behavior of the mach.blazer × teguload-shim
  pairing (needs the lane's usual /tmp/fleet/mach.laguna kernel), not an ADT
  regression; the four additions cause zero behavioral change pre-XNU. Full
  "failed-to-find lines gone" proof left to Q1 (qemu-validation/); artifacts
  in /tmp/fleet/g2-adt/, verdict in /tmp/fleet/adt-nodes-report.md.
- Lane suite `tests/host-admission.sh`: exit 0 (0 failures).
