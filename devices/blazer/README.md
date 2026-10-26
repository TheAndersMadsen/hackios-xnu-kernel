# Pixel 10 Pro (`blazer`, Google Tensor G5 "laguna")

An HakiOS device port for the Google Pixel 10 Pro. This directory is where
everything that is specific to this phone lives: its device tree, its drivers,
its tools and its notes. The kernel, loader and user space are shared with the
rest of the project; a port to another phone is a new directory like this one.

**Laguna is not a Qualcomm SoC.** The `soc_*` register-argument machinery and
the MPSS/IPA/Hexagon modem stack in `tools/soc-args.py` and `devices/poco-x5-pro/`
do not apply here. This port brings its own platform layer and drivers in the
same boot-argument style, and reuses the SoC-independent pieces (the Mini*
stand-ins, the AMFI/dyld-policy answer, the Mesa/GLI renderer path, the
userland image build, and the `fastboot boot` RAM-only delivery).

## Status

Proven on real hardware so far:

- **XNU entry on the phone (×4).** The ABL → mainline Linux → raw `kexec_load`
  chain reaches the XNU entry stub (marker `0x5A5A5A5A`, boot_args smuggled via
  the pmsg zone). The direct tegu-style shim path also reaches the kernel.
- The launchd-capable kernel (the 141.3 bridge lineage and the 121.2 native
  lineage) reaches VM bootstrap, APFS/HFS root mount and `launchd` in QEMU.
- backboardd composites frames to the scanout in QEMU (the display pipeline
  works end-to-end); a software renderer (Mesa softpipe) creates an ES3 context
  inside backboardd, and the Apple-logo frame composites.

In progress: the full driver bring-up on the new foundation (this branch), the
on-device root/cache transport, and the render content past the logo frame.

Not working yet: on-device SpringBoard UI, touch, cellular, Wi-Fi, camera,
and anything that needs the Secure Enclave (activation, Apple account — out of
scope by design).

## The phone

| Thing | Value |
| --- | --- |
| Codename | `blazer` |
| SoC | Google Tensor G5, `laguna` |
| DT compatible | `google,lga-blazer` |
| Panel | 1280 × 2856 (DPU `sswrp_dpu@ec00000`, `dc9x00@200000`) |
| Watchdogs | `watchdog_cl0@200c4000`, `watchdog_cl1@200c5000` (`google,wdt`) |
| CPM mailbox | TX `0x05260000`, RX `0x05261000` (goog-mba v2, drives DPU power) |
| ramoops | `ramoops@95200000` (4 MiB, survives resets; Android exposes it at `/sys/fs/pstore/console-ramoops-0`) |
| pmsg marker zone | PA `0x95400000` (+0x100 entry marker, +0x1000 MARKER-SCRATCH, +0x30000 panic mirror) |
| kexec window | `0x80000000` + 256 MiB (proven on device) |

The live device tree is in `fdt/laguna.dtb` (pulled from the phone). Refresh it
with `tools/pull-fdt.sh` if the boot chain changes.

## Delivery: `fastboot boot`, nothing flashed

The intended delivery is the project's RAM-only model: the loader goes into
RAM through `fastboot boot`, the system lives in RAM for a session, and a
reboot returns the phone to its own Android. Nothing is written to the boot
partitions, no userdata is erased, slot A stays stock.

Two delivery paths are being ported:

1. **The kexec ladder** (proven on device): ABL → mainline Linux (a custom
   kernel with `CONFIG_KEXEC=y`) → a probe that raw-`kexec_load`s XNU at EL1.
   The XNU entry stub is patched with a base-independent marker/branch so a
   raw kexec that delivers `x0 = 0` still gets `boot_args` via the pmsg zone.
2. **The direct shim** (tegu-style): ABL → a small shim that hands off to XNU
   in one hop, with early watchdog quiesce and a framebuffer findings console.

Read `docs/SAFETY.md` before running anything on a real phone.

## Magisk / `init_boot` ramdisk race (rooted phones)

On a rooted Pixel the ABL concatenates the vendor ramdisk fragment with
`init_boot`'s generic ramdisk (Magisk-patched). Magisk's `/init` unpacks last
and wins, so a normal `rdinit=/init` runs **magiskinit**, not our probe. The
workaround used here is `rdinit=/xni`: the probe's init entry is renamed to a
path the Magisk fragment does not contain, and the kernel's builtin cmdline is
appended last so `rdinit=/xni` wins. Do not "fix" this by touching `init_boot`.

## What needs doing (how to help)

- **DPU / display.** Bring the DC9x00 scanout up safely. Hard lesson: a
  readback of the DC9x00 MAIN-layer registers on real silicon raises an
  **asynchronous SError classified RAS → DER Event 20 crash reset** (reason
  `0xc00e`). In QEMU the same access is a recoverable synchronous abort. Keep
  DC9x00 readback **off by default**; power the DPU via the CPM mailbox first
  (`files/laguna_mbox.c` is the goog-mba v2 transport skeleton) and only then
  look for a live scanout address.
- **Touch.** The TCM driver exists as a host-tested skeleton (198/198); it
  needs the on-device AOC pad-slot layout, TBN bus-parked check, CPM hard
  reset/rails, TCM generation detect and SSI clock liveness probes.
- **UFS / root transport.** The root + dyld cache must reach XNU without
  erasing userdata: a normal userdata file (adb push) that the ladder's Linux
  stage ro-mounts (F2FS) and kexec-segments as `md0`. The cache files must be
  owned by **uid 0** (XNU's `vm_shared_region` refuses otherwise), and
  `/usr/lib/system/libdispatch.dylib` must **not** exist on disk (dyld-1042
  always overrides it from disk, which breaks the mapped cache).
- **SpringBoard content.** The render pipeline works; the content gap is in
  userland (LaunchServices/`lsd` bootstrap, first-unlock, scene-building),
  tracked in `docs/PORTING.md`.
- **Cellular, Wi-Fi, camera, charging, BT.** Unstarted on this SoC.

## Contributing and contact

**Forks and pull requests are very welcome.** This is an active bring-up, and
help is especially wanted on the DC9x00 display, TCM touch, UFS/root transport
and the remaining SpringBoard/userland walls (see `docs/PORTING.md`).

Questions, dumps from your own Pixel 10 Pro, or offers to help: **Discord
`@andersmadsen`**. If you have a laguna/Tensor device and a boot log, that is
often the fastest way to unblock a driver.

Two house rules, same as the rest of the project: no Apple binaries, images,
caches or phone firmware in the repo (see the top-level `LEGAL.md`), and read
`docs/SAFETY.md` before touching a real phone.

## Layout

| Path | What |
| --- | --- |
| `fdt/` | the phone's device tree |
| `files/` | laguna driver sources (mailbox first) |
| `tools/` | device-tree and log tools |
| `docs/PORTING.md` | the porting checklist and current wall map |
| `docs/SAFETY.md` | the on-device safety rails (read first) |

Nothing in this directory is an iOS binary, image, cache, font, picture or the
phone's firmware. Those come from your own IPSW and your own phone, as in the
rest of the project; see the top-level `LEGAL.md`.
