# Building the new blazer kernel

This branch keeps HakiOS's normal source model: `xnu/hakios.patch` modifies
Apple's `xnu-8796.121.2`, `xnu/files/` holds new source files, and
`xnu/apply.sh` reconstructs the tree. Do not apply old laguna patch series or
copy an old build tree's `BUILD/obj` into this checkout.

## Prerequisites

Follow the top-level `xnu/README.md` for the host toolchain. On macOS this is
Xcode or cctools plus the iPhoneOS 16.5 SDK laid out by `xnu-deps/setup.sh`;
Linux uses clang/lld and theos. The Apple XNU source and SDK are fetched by the
normal scripts. No iOS image is needed just to link the kernel.

## Clean source build

```sh
git clone --branch blazer \
  https://github.com/TheAndersMadsen/hackios-xnu-kernel.git
cd hackios-xnu-kernel

xnu/apply.sh
xnu-deps/setup.sh
./xnu-build.sh -j8 -k

K=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837
test -s "$K"
```

`-k` is intentional: upstream documents that the final export-list check can
return an error after the Mach-O has linked. Check the artifact and the actual
compile log rather than treating that known post-link status as proof of
success or failure.

For development after editing `xnu-src/xnu-8796.121.2`, regenerate the clean
patch + overlay **before committing**:

```sh
xnu/export.sh
# Review xnu/hakios.patch and xnu/files/; then rebuild from a fresh apply.sh
```

## Marker and contract validation

```sh
mkdir -p out
python3 devices/blazer/tools/patch-xnu-marker.py \
  --in "$K" --out out/mach.blazer.marked.macho \
  --marker 0x95400100 --handoff 0x95400120 --stage 1
python3 devices/blazer/tools/patch-xnu-marker.py \
  --verify out/mach.blazer.marked.macho --in "$K" \
  --marker 0x95400100 --handoff 0x95400120 --stage 1
sha256sum out/mach.blazer.marked.macho 2>/dev/null || shasum -a 256 out/mach.blazer.marked.macho
```

The raw build and the marker-patched build have different hashes by design.
Publish both if the consumer needs symbol table provenance; use the exact
marked file for kexec and the exact corresponding raw file for symbol context.
See `docs/BOOT-CONTRACT.md` for the first stage's `XNH0`, ADT and md0 duties.

## Host-only checks

```sh
BLAZER_DTB=devices/blazer/fdt/laguna.dtb sh tools/tensor-args-test.sh
sh tools/soc-args-test.sh
python3 devices/blazer/tools/validate-port.py
python3 -m py_compile tools/tensor-args.py \
  devices/blazer/tools/test-tensor-args.py \
  devices/blazer/tools/patch-xnu-marker.py \
  devices/blazer/tools/validate-port.py
```

`tensor-args-test.sh` checks every generated address against the checked-in
FDT and `soc.args`, plus synthetic 64-bit and fail-closed cases. The second
command is a regression check that the Qualcomm POCO generator was not
changed; it does not make any of that stack applicable to Tensor.

## Validation ownership and safety

M1 owns upstream/QEMU validation. Coordinate in `/tmp/fleet/COORDINATION.md`
before booting a guest. M2's build recipe does not flash, run adb/fastboot, or
touch a phone. For a first device handoff, keep UFS/CPM transmit disabled and
do not enable any DC9x00 readback. A marked Mach-O is not by itself a complete
boot image: M3 must also supply the entry handoff, ADT, memory spans and root
transport described in `docs/BOOT-CONTRACT.md`.
