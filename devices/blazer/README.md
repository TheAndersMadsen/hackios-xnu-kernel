<div align="center">

<img src="assets/banner.svg" alt="HakiOS · Pixel 10 Pro — iOS on Tensor G5 (blazer)" width="100%"/>

# Pixel 10 Pro · `blazer`
### HakiOS on Google Tensor G5 (“laguna”) — Apple's XNU, our drivers, your phone

[![Device](https://img.shields.io/badge/device-Pixel%2010%20Pro-6aa8ff)](.)
[![SoC](https://img.shields.io/badge/SoC-Tensor%20G5%20%22laguna%22-5ee7df)](.)
[![Kernel](https://img.shields.io/badge/XNU-8796.121.2-b78cff)](.)
[![Delivery](https://img.shields.io/badge/delivery-fastboot%20boot%20·%20no%20flash-success)](docs/SAFETY.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](.)
[![Discord](https://img.shields.io/badge/contact-@andersmadsen-5865F2)](.)

*An HakiOS device port for the Google Pixel 10 Pro — booting Apple's XNU and the
stock iOS user space on top of our own platform layer and drivers. The loader
goes into RAM through **`fastboot boot`**, the system lives in RAM for a
session, and a reboot brings the phone back to its own Android. Nothing is
flashed, userdata is never erased, and slot A stays stock. A research bench,
not a ROM.*

</div>

---

## Why this directory exists

The kernel, loader and user space are shared with the rest of HakiOS. Everything
that is **specific to this phone** lives here: its device tree, its drivers, its
tools and its notes. A port to another phone is a new directory like this one.

**Laguna is not a Qualcomm SoC.** The `soc_*` register-argument machinery and
the MPSS/IPA/Hexagon modem stack used by the Qualcomm ports do not apply. This
port brings its own platform layer and drivers in the same boot-argument style,
and reuses the SoC-independent pieces: the Mini* stand-ins, the AMFI/dyld-policy
answer, the Mesa/GLI renderer path, the userland image build, and the
`fastboot boot` RAM-only delivery.

---

## Status at a glance

| Area | State |
| --- | --- |
| XNU entry on the phone (kexec ladder) | ✅ Proven ×4 on real hardware (entry marker `0x5A5A5A5A`) |
| Direct tegu-style shim → XNU | ✅ Reaches the kernel; watchdog + fault-screen console |
| launchd + root mount in QEMU | ✅ APFS and HFS roots, full daemon set |
| backboardd → scanout in QEMU | ✅ Display pipeline works end-to-end |
| Software renderer in backboardd | ✅ Mesa softpipe ES3 context created; Apple-logo frame composites |
| Pixel 10 Pro port on the new foundation | 🚧 This branch — platform, DPU, UFS, image |
| On-device root + dyld cache transport | 🚧 userdata-file → F2FS ro-mount → `md0` |
| SpringBoard UI on the phone | ⬜ Not yet (content gap in userland, not the display pipeline) |
| Touch, Wi-Fi, cellular, camera, charging, BT | ⬜ Unstarted on this SoC |
| Secure Enclave / activation / Apple account | ⛔ Out of scope by design |

<details>
<summary><strong>QEMU evidence (not the physical phone)</strong></summary>

<div align="center">
<img src="assets/qemu-logo-frame.png" alt="The Apple-logo frame composited by the software renderer in QEMU" width="320"/>
<p><em>The Apple-logo frame composited by our Mesa softpipe renderer inside
backboardd, captured in QEMU. Included as proof the render path works — it is a
QEMU capture, not a claim of an on-device lock screen.</em></p>
</div>
</details>

---

## The phone

| Thing | Value |
| --- | --- |
| Codename | `blazer` |
| SoC | Google Tensor G5, `laguna` |
| DT compatible | `google,lga-blazer` |
| Panel | 1280 × 2856 — DPU `sswrp_dpu@ec00000`, `dc9x00@200000` |
| Watchdogs | `watchdog_cl0@200c4000`, `watchdog_cl1@200c5000` (`google,wdt`) |
| CPM mailbox | TX `0x05260000`, RX `0x05261000` — goog-mba v2, drives DPU power |
| ramoops | `ramoops@95200000`, 4 MiB, survives resets → `/sys/fs/pstore/console-ramoops-0` |
| pmsg marker zone | PA `0x95400000` (+0x100 entry marker, +0x1000 scratch, +0x30000 panic mirror) |
| kexec window | `0x80000000` + 256 MiB (proven on device) |

The live device tree is in [`fdt/laguna.dtb`](fdt/laguna.dtb). Refresh it with
[`tools/pull-fdt.sh`](tools/pull-fdt.sh) if the boot chain changes.

---

## Delivery

<div align="center">
<img src="assets/bootchain.svg" alt="fastboot boot → mainline Linux → kexec_load → XNU 8796 → launchd → SpringBoard" width="100%"/>
</div>

Two delivery paths are being ported, and **neither requires flashing**:

1. **The kexec ladder** (proven on device): ABL → mainline Linux with
   `CONFIG_KEXEC=y` → a probe that raw-`kexec_load`s XNU at EL1. The entry stub
   is patched with a base-independent marker/branch so a raw kexec delivering
   `x0 = 0` still receives `boot_args` through the pmsg zone.
2. **The direct shim** (tegu-style): ABL → a small shim that hands off to XNU
   in one hop, with early watchdog quiesce and a framebuffer findings console.

Read [`docs/SAFETY.md`](docs/SAFETY.md) before running anything on a real phone.

### Magisk / `init_boot` ramdisk race (rooted phones)

On a rooted Pixel the ABL concatenates the vendor ramdisk fragment with
`init_boot`'s generic ramdisk (Magisk-patched). Magisk's `/init` unpacks last
and wins, so a normal `rdinit=/init` runs **magiskinit**, not our probe. The
workaround here is **`rdinit=/xni`**: the probe's init entry is renamed to a
path the Magisk fragment does not contain, and the kernel's builtin cmdline is
appended last so `rdinit=/xni` wins. Do not “fix” this by touching `init_boot`.

---

## How to help

| Task | What it needs |
| --- | --- |
| **DPU / display** | CPM mailbox → DPU power → a live scanout address. Hard lesson: DC9x00 readback on real silicon is an **async SError → DER Event 20 crash reset** (`0xc00e`), even though it is harmless in QEMU. Keep it off by default; `files/laguna_mbox.c` is the mailbox skeleton. |
| **Touch (TCM)** | The host-tested driver exists (198/198). Needs on-device AOC pad-slot layout, TBN bus-parked check, CPM hard reset/rails, TCM generation detect, SSI clock liveness. |
| **UFS / root transport** | A normal userdata file (adb push) that the ladder's Linux stage ro-mounts (F2FS) and kexec-segments as `md0`. Cache files must be **uid 0**; `/usr/lib/system/libdispatch.dylib` must **not** exist on disk. |
| **SpringBoard content** | The display pipeline works; the gap is userland: LaunchServices/`lsd` bootstrap, first-unlock, scene-building. See [`docs/PORTING.md`](docs/PORTING.md). |
| **Cellular, Wi-Fi, camera, charging, BT** | Unstarted on this SoC. |

---

## Layout

| Path | What |
| --- | --- |
| [`assets/`](assets/) | banner, boot-chain diagram, QEMU evidence |
| [`fdt/`](fdt/) | the phone's device tree |
| [`files/`](files/) | laguna driver sources (mailbox first) |
| [`tools/`](tools/) | device-tree and log tools |
| [`docs/PORTING.md`](docs/PORTING.md) | the full porting checklist and current wall map |
| [`docs/SAFETY.md`](docs/SAFETY.md) | the on-device safety rails — read first |

Nothing here is an iOS binary, image, cache, font, picture or the phone's
firmware. Those come from your own IPSW and your own phone, as in the rest of
the project; see the top-level `LEGAL.md`.

---

## Contributing and contact

**Forks and pull requests are very welcome.** This is an active bring-up, and
help is especially wanted on the DC9x00 display, TCM touch, UFS/root transport
and the remaining SpringBoard/userland walls.

Questions, dumps from your own Pixel 10 Pro, or offers to help: **Discord
`@andersmadsen`**. If you have a laguna/Tensor device and a boot log, that is
often the fastest way to unblock a driver.

Two house rules, same as the rest of the project: no Apple binaries, images,
caches or phone firmware in the repo (see the top-level `LEGAL.md`), and read
[`docs/SAFETY.md`](docs/SAFETY.md) before touching a real phone.
