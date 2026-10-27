# INIT-ASSERTION — v51 "MACH Reboot" root cause (G1, host-only source analysis)

**Date:** 2026-10-27 · **Owner:** G1 (XNU init assertion analysis)
**Evidence:** `attempts/20261027-103925-v51/late-kernel.log` + `reader-full.txt` (device, mirror v2),
`../qemu-validation/evidence/runA-runB` (QEMU — XNU never entered; no UART panic args),
`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch `hakios`),
`research/laguna-usb-fix/mach.blazer-r4r5-tcm` (the RUNNING binary: banner `xnu-8796.121.2 …
DEVELOPMENT_ARM64_BCM2837` built `Oct 27 09:21:34` == file mtime; 68,906 symbols).

---

## 1. VERDICT

```
panic: IOPlatformExpert.cpp:1695 Assertion failed: gIOOptionsEntry != NULL
site : IODTPlatformExpert::processTopLevel()  (iokit/Kernel/IOPlatformExpert.cpp:1695)
cause: our laguna ADT (hackios-xnu-kernel/blazer/teguload/mkadt.py) emits NO /options node.
       IOPMrootDomain init races fine; the config thread then walks PE start → configure →
       processTopLevel → publishNVRAM() → createNVRAM() silently early-returns on the missing
       /options → gIOOptionsEntry stays NULL → stock assert() → panic → "MACH Reboot".
```

The panic record's `"%s:%d Assertion failed: %s"` (format only, no args in the dump) is the
signature of the plain `assert()` macro: `assert.h:97` → `Assert(file, line, expr)` →
`panic_plain("%s:%d Assertion failed: %s", file, line, expression)` (`osfmk/kern/debug.c:705`).
With the args restored, the rendered line is exactly:

```
IOPlatformExpert.cpp:1695 Assertion failed: gIOOptionsEntry != NULL
```

> **UPDATE 2026-10-27 11:52:** v54 (ADT with `/options` + G2 nodes) **PASSED
> `IOPlatformExpert.cpp:1695`** — no `gIOOptionsEntry` assert; on-device
> proof `pe_arm_map_interrupt_controller: found interrupt-controller` + `found
> timer`, then the full init ladder to `iBoot version: laguna-shim`. The new
> wall is `IONVRAMCHRPHandler creation failed`
> (`IONVRAM.cpp:getNVRAMProperties:1151 - Unable to find nvram-bank-size
> property`) — see INIT-NVRAM.md (attempts/20261027-114400-v54/reader-full.txt).

---

## 2. HOW IT WAS PINNED WITHOUT UART ARGS

QEMU runA/runB never entered XNU (shim stamps: last stage `usb-warm [FAULT]`), so the file:line
had to come from the binary itself. `assert()` embeds `__FILE_NAME__` / `__LINE__` / `#expr` as
literal operands at each call site, and clang moves the failure paths into `.cold.N` sections.
I disassembled `mach.blazer-r4r5-tcm` and decoded **all 204 `Assert()` call sites**
(`bl _Assert` with `adrp/add x0` = file string, `mov w1, #line`, `adrp/add x2` = expression —
full table: `/tmp/fleet/assert-sites.txt`, generator
`/private/var/folders/sl/srcjy8n90xn143tgfg5sl30c0000gn/T/opencode/assert-scan.py`).
The winning site is in the table as:

```
__ZN18IODTPlatformExpert15processTopLevelEP15IORegistryEntry.cold.1
    @0xfffffff007ac9f9c: IOPlatformExpert.cpp:1695 expr=gIOOptionsEntry != NULL
```

### Bracketing from the console mirror

* Last prints before the record: the PMRD storm
  `PowerChangeOverride (ON_STATE->ON_STATE, f, 0x2) tag 0x50008` / `PowerChangeDone` —
  PM-workloop output of the root domain's first `changePowerStateWithTagToPriv(ON_STATE,
  kCPSReasonInit)` storm.
* `GenericARMPE::start` (iokit/Kernel/GenericARMPE.cpp:214) begins with
  `if (!super::start(provider)) return false;` and the **next statement is
  `IOLog("GenericARMPE: platform \"%s\"\n", …)`**. `IOLog` unconditionally writes the serial
  console (`IOLib.cpp:_IOLogv` → `console_printbuf_putc`, unless `disable_iolog_serial_output`).
  That line **never appears** ⇒ the config thread died **inside `super::start`**, i.e. inside
  `IOPlatformExpert::start` (IOPlatformExpert.cpp:131) — whose steps are:
  1. `:174  assert(physicalRanges)` — passed (the storm printed after it),
  2. `:181  PMInstantiatePowerDomains()` — **the PMRD storm**, runs on the PM workloop
     concurrently (that's what the mirror captured),
  3. `:184  publishPlatformUUIDAndSerial()` — `:1425 assert(provider != NULL)` — trivially true,
  4. `:199  return configure(provider)` → virtual → `IODTPlatformExpert::configure`
     (IOPlatformExpert.cpp:1588) → `super::configure` (no "top-level" property in our ADT → no-op)
     → **`processTopLevel(provider)`** (IOPlatformExpert.cpp:1679):
     * `:1694  publishNVRAM();` → `init_gIOOptionsEntry()` returns −1 (no `/options` in the DT)
       → `provider->createNVRAM()` (IOPlatformExpert.cpp:2149) →
       `IORegistryEntry::fromPath("/options") == NULL` → **silent early return** (comment says so
       verbatim) → `gIOOptionsEntry` still NULL;
     * `:1695  assert(gIOOptionsEntry != NULL);` → **FIRES.**
* Sanity checks that eliminate the rest of the in-window assert pool:
  * `createNVRAM`'s own asserts (`:2163 gIOOptionsEntry == NULL`, `:2166 != NULL`) only run when
    `/options` **exists** — ours doesn't, so they're unreachable.
  * `IODTPlatformExpert::createNubs` CPU asserts (`:1644 tmpData != NULL`, `:1645 getLength()
    >= sizeof(UInt32)`, `:1665/1667 logical_id_added_to_ioreg`) run **after** the `:1695` assert
    inside `processTopLevel`, and our mkadt cpu nodes already carry
    `device_type 'cpu'` + 4-byte `reg` — they never got the chance, and wouldn't fire anyway.
  * `publishPlatformUUIDAndSerial:1425` / `publishNVRAM:1439` `assert(provider != NULL)` —
    `provider` is `start()`'s argument, non-NULL.
  * The PM-workloop machinery asserts (`executePMRequest:8098`, `queuePMRequest:8823`,
    `tellSystemCapabilityChange:5946`, …) are platform-independent; the felix r5-v2 QEMU run
    (same PM code) completed the identical storm cleanly (6 Override/Done pairs, tags
    0x20064/0x10001/0x40064/0x50064/0x30002/0x60008 + both `sysPowerDownHandler`
    capability messages). Laguna's storm shape matches it tail-to-tail; the cut-off is the
    panic freezing the world mid-storm, not a PM bug.
* Cross-check with the felix reference (`/tmp/fleet/paniccap-qemu-r5-v2/msgbuf-live-tail.bin`):
  after the storm, felix printed its PE-registered/AKS/WDT/HID/battery lines and kept booting —
  felix's tree **has** an `/options`-bearing shape; laguna's doesn't.

---

## 3. CROSS-REFERENCE: the FDT/DTD gaps the log exposed vs XNU expectations

| v51 log line | XNU site | Fatal? | mkadt.py state (post-v51) |
|---|---|---|---|
| `pe_arm_map_interrupt_controller: soc_phys 0x5880000 … failed to find the interrupt-controller.` | `pexpert/arm/pe_identify_machine.c` — kprintf only | **No** — port's GIC fallback registered (`gic: v3 …` line follows) | FIXED since v51: `/arm-io/gic` now carries `('interrupt-controller', cstr('master'))` (mkadt.py:255) |
| `pe_arm_init_debug: failed to find cpu-debug-interface` | `pexpert/arm/pe_identify_machine.c` — kprintf only | **No** | FIXED since v51: `/arm-io/cpu-debug-interface` node emitted (mkadt.py:282) |
| `debug_log_init: Error!! gPanicBase is still not initialized` (×2) | `osfmk/kern/debug.c debug_log_init` (needs `/pram` + `embedded-panic-log-size`) | No (degrades panic capture only) | FIXED since v51: `/pram` node emitted device-mode only (mkadt.py:~350) |
| **— no message; silent —** | **`iokit/Kernel/IOPlatformExpert.cpp:1695 assert(gIOOptionsEntry != NULL)`** | **YES — this is the v51 kill** | **MISSING: no `/options` node anywhere in mkadt.py** |

Note: mkadt.py's mtime (Oct 27 11:17) postdates the v51 attempt (10:39); the gic/cpu-debug/
pram comments in mkadt.py quote the exact v51 failure lines — they were written in response to
this boot family. `/options` is the one gap that survived.

---

## 4. TOP-3 CANDIDATE ASSERTIONS (ranked)

### #1 — `IOPlatformExpert.cpp:1695` `assert(gIOOptionsEntry != NULL)`  ← THE KILLER
* **Where:** `IODTPlatformExpert::processTopLevel()`, reached on the config thread inside
  `GenericARMPE::start → super::start → configure()` at every PE start.
* **Why it fires:** ADT lacks `/options`. `createNVRAM()` returns silently both at
  `ConfigureIOKit` (configureDefaults) and inside `publishNVRAM()`; stock xnu then asserts.
* **Fix (preferred, ADT):** mkadt.py — add the node and include it in the root children:
  ```python
  options = node('options', [])          # node() adds the 'name' property itself
  return node('device-tree', [ … ],
              [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + pram + [options])
  ```
  (`node('options', [ph()])` is equally fine; precedent: `UniXNU-sheng/make-sheng-adt.py:180`,
  `pandora mkadt_pandora.py:353`.) `GenericARMPE::excludeList` already excludes `'options'` from
  nub creation, so no nub is made; with the node present, `createNVRAM()` instantiates
  `IODTNVRAM` (iokit/Kernel/IONVRAM.cpp) at configureDefaults — **before** the PMRD storm — and
  `processTopLevel` passes.
* **Fix (code gate, fallback only):** relax the assert in our tree
  (`if (!gIOOptionsEntry) kprintf("LAGUNA: no /options; skipping NVRAM\n");`) — do NOT prefer
  this; upstream keeps the invariant for a reason and later NVRAM readers
  (`PEReadNVRAMProperty` etc.) still call `init_gIOOptionsEntry()`.
* **Watch item:** `IODTNVRAM::start` on a bare `/options` — felix booted with an options node,
  so minimal risk; if a NEW panic appears here it will be the next assert in line (see #3).

### #2 — `IOPlatformExpert.cpp:1644/1645` `assert(tmpData != NULL)` / `assert(getLength() >= sizeof(UInt32))` (createNubs, CPU nodes)
* **Where:** `IODTPlatformExpert::createNubs()` — any DT node with `device_type == "cpu"`
  must carry a ≥4-byte `reg`.
* **Status:** satisfied by current mkadt.py (cpu0..cpu7 with `('reg', u32(core << 8))`),
  and it sits *after* #1 in `processTopLevel`, so it cannot be the v51 firer.
* **Fix:** none needed; keep the invariant if the ADT evolves (e.g. real MPIDRs). If
  `ml_get_cpu_number(phys_id) < 0` the node is skipped safely (no assert).

### #3 — PM-workloop internal asserts (`IOServicePM.cpp:8098 kIOPM_Finished == fMachineState`, `:8823 onThread()`, `:5946 kIOPM_BadMachineState != nextMS`, `:2858 desiredState < fNumberOfPowerStates`)
* **Where:** the storm running on the IOPMWorkQueue thread.
* **Status:** platform-independent; the identical storm completed on felix r5-v2. Only plausibly
  reachable AFTER #1 is fixed, if the root domain then runs capability stages laguna never got to
  (the v51 storm died before `sysPowerDownHandler` capability messages ever printed).
* **Fix:** none preemptively; Q1's QEMU lane (working UART) will print the rendered line
  verbatim if one of these ever fires.

---

## 5. EXPECTED SIGNATURES AFTER THE FIX (for the next attempt's triage)

* **v52 with `/options`:** expect `GenericARMPE: platform "iPhone10,3"` to print right after the
  PMRD storm block, then the Mini* registration prints; boot should progress toward CPU
  registration / root mount.
* **If v51's ADT were replayed under Q1's working UART**, the missing line would read:
  `IOPlatformExpert.cpp:1695 Assertion failed: gIOOptionsEntry != NULL`.
* The 12 ms stagger between `changePowerStateWithTagToPriv(ON_STATE, 10001)` and its first
  Override is the PM workloop getting scheduled on a single running CPU — normal, not a hang.

## 6. CROSS-LANE ASKS

* **A-DT (mkadt.py owner):** add the `/options` node (diff in §4 #1) + a line in
  `laguna-adt-requirements.md` ("xnu requires /options; its absence asserts
  IOPlatformExpert.cpp:1695 in processTopLevel").
* **Q1 (QEMU lane):** fix the shim `usb-warm FAULT` (stamps stage #9) so runA hands off; the
  first XNU-on-QEMU boot with the CURRENT ADT should show the same assert on UART — a free,
  zero-risk confirmation before any device v52.
* **Coordinator:** sequence v52 payload (mkadt fix) only after a QEMU or shim-side sanity pass;
  the ADT blob rides the payload, so this is a rebuild-and-flash of the same image shape.
* **B4/evidence:** v51's missing `gPanicBase` warnings are already fixed by mkadt's `/pram`; keep
  it device-mode-only as written.

## 7. ARTIFACTS

* `/tmp/fleet/assert-sites.txt` — all 204 Assert() call sites decoded from the running binary.
* `/tmp/fleet/init-assertion-report.md` — fleet report (same verdict, condensed).
* Decoder script: `/private/var/folders/sl/srcjy8n90xn143tgfg5sl30c0000gn/T/opencode/assert-scan.py`
  (objdump + nm + `__cstring` maps; reusable for any future assert triage on this kernel).

**Boundaries honored:** host-only; no adb/fastboot/device access; only files owned by G1 written
(INIT-ASSERTION.md, COORDINATION.md entry, /tmp/fleet reports); shim tree, staged images and other
lanes' files untouched.
