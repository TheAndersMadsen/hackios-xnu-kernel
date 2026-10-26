#!/usr/bin/env python3
"""Host-only structural checks for the new-foundation blazer port."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
checks = []


def require(name, condition):
    checks.append((name, bool(condition)))


def text(relative):
    return (ROOT / relative).read_text()


def main():
    pe = text("xnu/files/iokit/Kernel/GenericARMPE.cpp")
    require("Blazer expert inherits GenericARMPE", "class BlazerPlatformExpert : public GenericARMPE" in pe)
    require("Mini IOSurface starts before other Mini services", pe.index("MiniIOSurfaceStart(this);") < pe.index("MiniIOMobileFramebufferStart(this);") < pe.index("MiniAppleKeyStoreStart();"))
    require("panic test starts only after Mini globals", pe.index("MiniAppleKeyStoreStart();") < pe.index("hakios_blazer_panicdump_bootdone();"))
    surface = text("xnu/files/iokit/Kernel/MiniIOSurface.cpp")
    require("IOSurface locks precede root registration", surface.index("gSurfaceLock = IOLockAlloc();") < surface.index("root->start(provider)"))
    fb = text("xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp")
    for selector in ("kFBMethodGetColorRemapMode", "kFBMethodGetDisplayConfig", "kFBMethodSetDisplayConfig",
                     "kFBMethodSetDisplayParameter", "kFBMethodSetDisplayConfigFlags", "kFBMethodSetDisplayConfigOption"):
        require(f"MiniIOMFB answers {selector}", f"case {selector}:" in fb)
    require("display config has bounded shadow", "gFBConfigBlock[4096]" in fb and "args->structureInputSize > sizeof(gFBConfigBlock)" in fb)
    require("display compatible is board data", 'TUNABLE_STR(gFBCompatible, 64, "fb_compatible"' in fb)
    require("Mini presenter cache hook", "hakios_blazer_display_clean_on_present" in fb and "FlushPoC_DcacheRegion" in fb)
    display = text("xnu/files/osfmk/arm64/hakios.blazer_display.c")
    require("ADT display handoff consumer", 'SecureDTLookupEntry(NULL, "/chosen/laguna-display-handoff"' in display)
    require("display forbidden-range consumer", 'SecureDTLookupEntry(NULL, "/chosen/laguna-display-forbidden"' in display)
    require("display kernel window excluded", "args->physBase, args->memSize" in display)
    require("invalid display record goes headless", "display_headless(args);" in display)
    require("no display MMIO in ADT consumer", "DC9x00" not in display and "ufs_read" not in display)
    handoff = text("xnu/files/osfmk/arm64/laguna_display_handoff.c")
    require("wire handoff is 128 bytes + CRC", "LAGUNA_DISPLAY_HANDOFF_SIZE" in handoff and "handoff_crc" in handoff)
    require("handoff producer check needs no libc memchr", "producer_has_nul" in handoff and "memchr(" not in handoff)

    mac = text("xnu/files/bsd/kern/kern_minimac.c")
    require("AMFI policy exact name", '.mpc_name = "AMFI"' in mac)
    require("AMFI op 0x5a", "AMFI_SYSCALL_CHECK_DYLD_POLICY  0x5a" in mac)
    require("AMFI ALLOW_ALL 0xff", "AMFI_DYLD_OUTPUT_ALLOW_ALL      0xffULL" in mac)
    require("Sandbox exact name", '.mpc_name = "Sandbox"' in mac)
    require("Sandbox requests logged", "minisandbox_seen" in mac and "first seen from" in mac)

    ufs = text("xnu/files/bsd/dev/hakios.blazer_ufs.c")
    require("UFS defaults off", 'PE_parse_boot_argn("blazer_ufs", &ufs.mode' in ufs)
    require("UFS requires live handoff", 'PE_parse_boot_argn("blazer_ufs_live", &live' in ufs)
    require("UFS root name required", 'PE_parse_boot_arg_str("blazer_ufs_root", root' in ufs)
    require("G5 IID=1", "ucd->cmd[4] = UPIU_IID_ONE;" in ufs)
    require("legacy MCQ refusal", "MCQ_MODE_SELECT" in ufs and "legacy doorbell is not safe" in ufs)
    require("read-only block open", "(flags & FWRITE) ? EROFS : 0" in ufs)
    require("no WRITE(10) opcode", "0x2a" not in ufs)
    require("no UIC clear-on-read access", "0x38..0x48 are clear-on-read UIC error registers: deliberately absent" in ufs)
    require("36-bit DMA bound", "G5_DMA_LIMIT        (1ULL << 36)" in ufs)

    cpm = text("xnu/files/bsd/dev/hakios.blazer_cpm.c")
    require("CPM read probe gate", 'PE_parse_boot_argn("blazer_cpm", &enabled' in cpm)
    require("CPM separate TX gate", 'PE_parse_boot_argn("blazer_cpm_tx", &tx' in cpm)
    require("CPM queue init resets next cursor", "cpm_tx.cursor = 0;" in cpm)
    require("CPM receive copies before ACK", cpm.index("out[n * HAKIOS_BLAZER_CPM_WORDS + i]") < cpm.index("cpm_write(cpm_rx.regs, MBA_IRQ_STATUS, MBA_IRQ_MESSAGE);"))

    panic = text("xnu/files/osfmk/arm64/hakios.blazer_panicdump.c")
    require("panic capture default off", "uint32_t dump_arg = 0;" in panic)
    for arg in ("blazer_ramoops_base", "blazer_ramoops_size", "blazer_console_size", "blazer_pmsg_base", "blazer_pmsg_size"):
        require(f"panic geometry {arg}", f'PE_parse_boot_argn("{arg}"' in panic)
    require("panic nofault staging", "ml_nofault_copy" in panic and "panic_validate_ptr" in panic)
    require("persistent_ram signature", "0x43474244" in panic)
    require("CRC mirror at pmsg+0x30000", "LAGUNA_MIRROR_OFF         0x30000ull" in panic)
    pe_init = text("xnu/hakios.patch")
    require("missing-pram fallback", "blazer_panic_region[0x80000]" in pe_init and "gPanicBase = (vm_offset_t)blazer_panic_region" in pe_init)
    require("root compatible personality", "'IONameMatch'     = 'google,lga-blazer'" in pe_init)
    require("UFS root hook", 'strcmp(rdBootVar, "ufsroot0") == 0' in pe_init)

    marker = text("devices/blazer/tools/patch-xnu-marker.py")
    require("dual-entry XNH0", 'HANDOFF_MAGIC = 0x30484E58' in marker)
    require("base-independent ADR branch", "adr(16, 0)" in marker and "branch delta" in marker)
    require("marker verifier", "only entry-PC field + cave bytes differ" in marker)

    generated = subprocess.check_output([sys.executable, str(ROOT / "tools/tensor-args.py"), str(ROOT / "devices/blazer/fdt/laguna.dtb")], text=True)
    expected = generated + "fb_compatible=google,lga-framebuffer\n"
    require("soc.args matches live FDT", (ROOT / "devices/blazer/soc.args").read_text() == expected)
    require("no DC9x00 driver imported", not list((ROOT / "xnu/files").glob("**/*dc9x00*")))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    if failed:
        raise SystemExit(f"validate-port: {len(failed)} failure(s): {failed}")
    print(f"validate-port: ALL {len(checks)} CHECKS PASSED")


if __name__ == "__main__":
    main()
