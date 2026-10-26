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

**Use `-j8`, not higher.** A `-j15` build hit a real race: the generated
syscall family truncated (`bsd.syscalls.master` came out 0 bytes → 44-line
`sysproto.h` → 1355 `init_sysent.c` errors). The remedy that worked: delete
the generated family (`bsd.syscalls.master`, `sysproto.h`, `syscall.h`,
`init_sysent.c`, `syscalls.c`, `audit_kevents.c`, `systrace_args.c` in both
`obj` and `EXPORT_HDRS`) and rebuild `-j8` — the kernel then links with zero
compile errors. If a build fails with `struct ..._args incomplete` in
`init_sysent.c`, suspect truncated generated files before anything else.

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

## Userland images

The kernel alone does not boot: launchd needs the system root and the small
`/redwood` auxiliary image. The proven chain builds both from your own IPSW
(no Apple content is committed here; bring your own).

```sh
# 1. Fetch and select the iOS root (one-time, ~7.4 GB download).
sysimage/fetch.sh                       # or reuse a local 20F66 extraction:
python3 sysimage/fetch-from-ipsw.py ... # see sysimage/select-system.py usage

# 2. Build the complete userland. REST=1 selects the "rest" job set (the
#    daemons stock launchd asks for); SYSTEM=1 forces the system image,
#    SHARE=1 keeps keys/secrets out, CHECK=1 runs apfsck on every image,
#    PREBUILT=1 uses the prebuilt programs when no cross toolchain exists.
PREBUILT=1 REST=1 SYSTEM=1 SHARE=1 CHECK=1 userland/build.sh
```

Outputs: `userland/system.apfs` (the RAM root, published as `RAMDisk` →
`md0`, currently ~2.3 GB) and `userland/root.apfs` (the `/redwood`
auxiliary, published as `RAMDisk1` → `md1`, a few MiB). `CHECK=1` makes
apfsck gate every container at build time.

What the image already covers, so a porter does not re-fight old walls:

- every dyld-cache file uid/gid **0:0** (XNU refuses the shared region
  otherwise), cache in the cryptex-native location
  `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld` — do **not**
  add the old `ignition_level=1` hard-link recipe;
- **no** top-level `/usr/lib/system/*.dylib` at all — the dyld-1042
  disk-override rule cannot fire, so the old libdispatch prune is unnecessary
  on this image (the prune still applies to older HFS-cacheroot chains);
- identity: `/private/etc/{passwd,master.passwd,group}` carry the full mobile
  account closure, and real `lsd`/`mapdb` jobs are in the image — no dslocal
  overlay, no mapdb stub needed on this path (for HFS-cacheroot chains that
  do need dslocal, see `tools/dslocal/`);
- LaunchServices is deliberately unseeded: lsd builds the store from
  `/Applications` on first boot; allow a long first boot instead of seeding.

One retained build delta: the pinned prebuilt bundle omits `var_store`, so
`/bin/var_store` (the `/private/var` archive helper) must be compiled once
from the unmodified `userland/src/var_store.c` + `userland/libmini` as static
arm64 (`ld64.lld`) and `root.apfs` rebuilt — its launchd plist alone is not
functional. **Keep `var_store=1 ufs_write=1` off on laguna** until a safe
Tensor storage backend with independent bounds and read-back verification
exists; RAM-only boots repeating first-boot LS work is the correct interim
behavior.

Boot arguments for an image boot (see `docs/BOOT-CONTRACT.md` §4 for the full
namespaces): `rd=md0` plus the platform diagnostics. Do **not** carry the old
HFS command line wholesale — `ignition_level=1`, `launchd_unsecure_cache=1`
and `rootrw=1` are not consumed by the new foundation's RAM boot.

Transport: the 2+ GB system image is too large for the boot payload. Use the
config-mode resident-ramdisk delivery (`xnu.ramdisk_pa` + `xnu.ramdisk_size`,
with range and hash checks) rather than squeezing it into the ~64 MiB
payload; the ADT side is the byte-for-byte `RAMDisk`/`RAMDisk1` contract in
`docs/BOOT-CONTRACT.md` §3.

## Boot image and delivery

A marked Mach-O is not a boot image. Delivery is `fastboot boot` (nothing is
flashed) with an Android boot header v4 + AVB SHA256_RSA4096 footer carrying
the kernel, the ramdisk contract and the real laguna DTB
(`fdt/laguna.dtb`). Two host-built, host-validated transport controls exist
as the worked examples:

1. **kexec control** (primary): ABL → Linux → `/xni` → raw `kexec_load` of
   the marked kernel. The Linux Image carries `rdinit=/xni
   console=pstore printk.devkmsg=on` in its **builtin** command line with
   `CONFIG_CMDLINE_EXTEND=y`, so `rdinit=/xni` is appended **last** and wins
   over Magisk's concatenated `/init` — the Magisk immunity described in
   `README.md`. The runbook requires `fastboot oem watchdog enable` to return
   OKAY before anything boots.
2. **direct-embedded control** (fallback): the raw shim + payload linked into
   the kernel Image, no ramdisk dependency — used only after evidence proves
   the uploaded fragment missing.

The entry contract both rely on (direct `x0=boot_args`, kexec `x0=0` +
`XNH0` in pmsg) is `docs/BOOT-CONTRACT.md` §1, implemented by
`tools/patch-xnu-marker.py`. Device runs are operator-owned and follow
`docs/SAFETY.md`: bootloader fastboot (not fastbootd), slot a active,
12→30 minute no-interrupt rule, immediate pstore harvest, and no flash of
any kind.

## Validation ownership and safety

QEMU validation of the linked kernel happened once already and is the
milestone behind the status table in `README.md`: the blazer-branch kernel
booted the verified userland to the rendered lock screen with zero panics
(the full report is the working lane's `research/laguna-blazer-qemu`
evidence; the QEMU harness itself is not part of this repo). For a first
device handoff, keep UFS/CPM transmit disabled and do not enable any DC9x00
readback; the display path on hardware starts from the validated first-stage
handoff in `docs/RENDER.md` §5, not from probing. Build and host checks here
never flash, run adb/fastboot, or touch a phone.
