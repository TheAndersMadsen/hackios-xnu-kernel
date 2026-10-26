# Porting checklist for `blazer` (Tensor G5 / laguna)

This is the working map of what the port needs and where each piece lives.
It is meant for someone picking up a task, not as a history log.

## 1. Platform layer

- [ ] A `LagunaPlatformExpert` matching `google,lga-blazer` (the boot must not
      fall through to `GenericARMPE::start()`, or the Mini* stand-ins never
      start — that was the systemic SIGTRAP root cause on the native tree).
- [ ] `IOResources` personalities in `gIOKernelConfigTables` for the Mini*
      stand-ins (the 0018/0019 pattern):
      - `MiniAppleKeyStore` (kills the MobileKeyBag `MKBEventsRegister` trap),
      - `MiniIOMFB` + `IOCoreSurfaceRoot` (the display-config contract),
      - `MiniIOSurface` globals initialized in `IOCoreSurfaceRoot::start()`
        before `registerService()` (avoids the `lck_mtx_lock(NULL)` panic).
- [ ] AMFI op `0x5a` (the dyld-policy check) answered with `0xff` under the
      exact policy name `AMFI`; without it, `DYLD_INSERT_LIBRARIES` /
      `kern.exec_inject` carriers are refused.
- [ ] A permissive-but-logged sandbox policy (privileges granted, extension
      issue/consume as no-ops) for the early bring-up.
- [ ] The kexec entry contract: raw `kexec_load` delivers `x0 = 0`, so the
      entry stub must be patched to take `boot_args` from the pmsg zone and
      to write the `0x5A5A5A5A` entry marker + a MARKER-SCRATCH record.

## 2. Display / DPU

- [ ] CPM mailbox transport (`files/laguna_mbox.c`): queue-based goog-mba v2,
      TX `0x05260000`, RX `0x05261000`, polled-first, boot-arg gated.
- [ ] DPU power-on sequence, then a live scanout address — **without** the
      DC9x00 readback (see `SAFETY.md`: it is a RAS crash on real silicon).
- [ ] `MiniIOMFB` display-config selectors answered: 57 (color remap),
      27/17 (the 3084-byte get/set twin over a shadow block), 84/50/68 accept.
      This is what lets backboardd probe without a FrontBoardServices assert.
- [ ] A software renderer into the scanout (Mesa softpipe) and the
      GLI/EAGL shim for CoreAnimation: `{rctx, disp[1036]}` via
      `getMacroContextPrivate` (slot `n` at `8*(n+1)`), API 3, self-make-current
      wrappers because CA never calls `+setCurrentContext:`.
- [ ] The `kern.exec_inject` carrier (launchd strips `DYLD_*` from non-root
      jobs, so `DYLD_INSERT_LIBRARIES` in a plist is not enough).

## 3. Root + userland image

- [ ] `RAMDisk → md0` ADT contract (byte-for-byte), `rd=md0 rootrw=1`,
      `ignition_level=1 launchd_unsecure_cache=1`.
- [ ] The full dyld shared cache at `/System/Library/Caches/com.apple.dyld`,
      every file owned by **uid 0** (XNU refuses the shared region otherwise).
- [ ] No `/usr/lib/system/libdispatch.dylib` on disk (dyld-1042 override rule).
- [ ] `/private/var/db/dslocal/nodes/Default/` user/group records
      (`mobile` + the daemon accounts), or launchd dies resolving `mobile`.
- [ ] LaunchServices bootstrap that cannot self-deadlock: `lsd`'s own
      `com.apple.lsd.system.mapdb` MachService is queried during its dyld
      init; if `lsd` blocks on it, everything downstream (SpringBoard's
      `DocumentManagerCore` → `CoreServices` sync) wedges. Serve mapdb with a
      small stub, or ship the prebuilt LS database.
- [ ] The MobileCoreServices seeding source under
      `/private/var/mobile/Library/Preferences/com.apple.MobileCoreServices/`.

## 4. Touch, storage, other drivers

- [ ] TCM touch: AOC pad-slot layout, TBN bus-parked check, CPM hard
      reset/rails, TCM generation detect, SSI clock liveness.
- [ ] UFS: root storage and the F2FS read-only mount in the ladder's Linux
      stage (`CONFIG_F2FS_FS=y` is required in that kernel).
- [ ] ramoops/pmsg panic capture: on panic, append the panic string +
      backtrace + msgbuf tail into the ramoops console zone (PA `0x95200000`,
      Linux persistent_ram ring semantics) plus a CRC'd mirror at
      pmsg+`0x30000`; handle the missing `pram` node (`gPanicBase = 0`) with a
      static fallback so `SavePanicInfo` cannot spin.

## 5. Delivery and evidence

- [ ] `fastboot boot` image (boot v4 + AVB hash footer) carrying the kernel,
      the ramdisk contract and the real laguna DTB.
- [ ] `rdinit=/xni` for the Magisk/`init_boot` race (see `README.md`).
- [ ] Evidence: pstore console + the pmsg marker + the `PANIC-CAPTURED`
      decoder (unslid, symbolicated frames against the payload's symbol table).

## Reference evidence

The probes and logs behind each item above were captured on the real phone and
in QEMU during the laguna bring-up. They are deliberately not committed here
(logs and Apple-owned images stay out of the repo); the mechanics are what is
documented in this file and in the driver sources.
