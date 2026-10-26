# On-device safety rails (Pixel 10 Pro / blazer)

Read this before any `fastboot`, `adb` or flash command on a real phone.

## Hard rules

- **Never erase userdata.** The root/cache is delivered as a normal userdata
  file (adb push) or into RAM, never by wiping.
- **Never touch slot A.** Slot A is the stock Android image and must stay
  untouched and bootable. Experiments, if any flash is unavoidable, go to
  slot B only, and slot A is restored active afterwards.
- **Never flash bootloader or firmware.** No `bootloader`, `radio`, `tz`, `dtbo`
  or other firmware partitions. Only `boot` / `vendor_boot` style images are
  ever built here, and the preferred delivery is `fastboot boot` (no flash).
- **ARB warning.** A May-2026 anti-rollback bootloader permanently hard-bricks
  the phone on downgrade below its rollback index. Never flash older
  bootloader images, and do not take OTAs mid-project without re-checking.
- **ABL watchdog.** The ABL watchdog is the only guaranteed recovery on the
  direct-shim path. Before any experiment: `fastboot oem watchdog enable` and
  require `OKAY`; if it refuses, stop.
- **Announce reboots** to whoever owns the phone, and keep device work under
  one owner at a time.

## The boot-failure park

Flashing an experimental pair to slot B and letting the ABL exhaust its boot
retries leaves the phone in fastboot with "Boot failure" and can wipe the
pstore evidence. Recovery is always:

```
fastboot --set-active=a
fastboot reboot
```

Then Android on slot A boots normally. Prefer `fastboot boot` so this never
happens.

## Headless is evidence, not a hang

After a ladder starts, the phone disappearing from `adb` for more than ~12
minutes usually means **XNU is running** (there is no USB driver in our XNU),
not that it hung. Do not fastboot-interrupt: wait ~30 minutes, then
`fastboot reboot` to Android and pull the evidence
(`/sys/fs/pstore/console-ramoops-0` and the pmsg marker).

## DC9x00 / RAS

Do not probe the DC9x00 display controller's registers on real hardware as a
"safe read". On this SoC that access is an asynchronous SError classified as a
RAS event and crash-resets the phone (reboot reason `0xc00e`, "DER Event 20").
It looks harmless in QEMU and is not.
