<div align="center">

<img src="devices/blazer/assets/banner.svg" alt="HakiOS · Pixel 10 Pro — iOS on Tensor G5 (blazer)" width="100%"/>

# HakiOS · the Pixel 10 Pro branch

**Apple's XNU and the stock iOS user space, running on a Google Pixel 10 Pro
(`blazer`, Tensor G5 “laguna”) — on our own platform layer and drivers.**

[![Device](https://img.shields.io/badge/device-Pixel%2010%20Pro-6aa8ff)](devices/blazer/)
[![Delivery](https://img.shields.io/badge/delivery-fastboot%20boot%20·%20no%20flash-success)](devices/blazer/docs/SAFETY.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](devices/blazer/)
[![Discord](https://img.shields.io/badge/contact-@andersmadsen-5865F2)](devices/blazer/)

**👉 [Start here: the Pixel 10 Pro port](devices/blazer/README.md)** — status,
hardware map, the delivery chain, how to build and how to help.

</div>

---

## What this branch is

This branch carries the **Pixel 10 Pro device port** of HakiOS on top of the
project's current foundation (`origin/main`). The goal is the same as the rest
of the project — boot Apple's open-source XNU and the stock iOS user space on a
phone that was never meant to run it — but for Google's Tensor G5 instead of a
Qualcomm SoC.

The intended delivery is the RAM-only research-bench model: the loader goes in
through **`fastboot boot`**, the system lives in RAM for a session, and a reboot
returns the phone to its own Android. **Nothing is flashed, userdata is never
erased, and slot A stays stock.**

**Already proven on the phone:** XNU entry via the kexec ladder (×4, entry
marker `0x5A5A5A5A`) and the direct shim. **In progress:** the full laguna
driver bring-up, on-device root/cache transport, and SpringBoard content.
**Not a ROM, and not an activation path** — no Secure Enclave, no Apple account.

## Where things live

| Path | What |
| --- | --- |
| [`devices/blazer/`](devices/blazer/) | **The Pixel 10 Pro port** — README, porting checklist, safety rails, device tree, drivers, tools |
| [`devices/blazer/assets/`](devices/blazer/assets/) | banner, boot-chain diagram, QEMU render evidence |
| [`README-hakios.md`](README-hakios.md) | the upstream HakiOS README (POCO X5 Pro, project background) |
| [`docs/`](docs/) | project documentation (GPU, cellular, notes) |
| [`xnu/`](xnu/), [`xnuloader/`](xnuloader/), [`userland/`](userland/) | the shared kernel patch, loader and user space |

## For contributors

**Forks and pull requests are very welcome** — especially on the DC9x00
display, TCM touch, UFS/root transport and the remaining SpringBoard/userland
walls. The full task list and the hardware lessons are in
[`devices/blazer/docs/PORTING.md`](devices/blazer/docs/PORTING.md).

Questions, dumps from your own Pixel 10 Pro, or offers to help: **Discord
`@andersmadsen`**.

Please follow the two house rules: no Apple binaries, images, caches or phone
firmware in the repo (see `LEGAL.md`), and read
[`devices/blazer/docs/SAFETY.md`](devices/blazer/docs/SAFETY.md) before any
on-device work.

---

## The short version for a new reader

- This is a **research bring-up of iOS on a Pixel 10 Pro**, not a ROM and not a
  way to make a phone pass as an iPhone to Apple's services.
- The kernel is Apple's open-source XNU; the drivers and the platform layer are
  written for Google's Tensor G5 (`laguna`).
- Delivery is `fastboot boot` into RAM; the phone returns to stock Android on
  reboot.
- Want to help? Start at [`devices/blazer/README.md`](devices/blazer/README.md)
  and pick a task from the “How to help” table.
