# Porting checklist for `blazer` (Tensor G5 / laguna)

This is the working map of what the port needs and where each piece lives.
It is meant for someone picking up a task, not as a history log.
Build and handoff details live in `docs/BUILD.md` and `docs/BOOT-CONTRACT.md`.
Checked boxes mean implemented in source on this branch; they do not imply an
on-device validation claim.

## 1. Platform layer

- [x] `BlazerPlatformExpert` matches `google,lga-blazer` and **inherits** the
      complete `GenericARMPE::start()`. On the new foundation that parent starts
      the Mini* services directly after the registry is live; the native
      0018/0019 IOResources workaround is not a second patch layer to import.
- [x] The inherited Mini* stand-ins are preserved: `MiniAppleKeyStore`,
      `MiniIOMFB` + `IOCoreSurfaceRoot`, and `MiniIOSurface` globals initialized
      before `IOCoreSurfaceRoot` registration. There is exactly one initialization
      path, so the old `lck_mtx_lock(NULL)` class is not reintroduced.
- [x] AMFI op `0x5a` is already answered with `0xff` under the exact policy
      name `AMFI` by the foundation's `kern_minimac.c`; no second AMFI driver.
- [x] The foundation's permissive-but-logged Sandbox policy supplies the early
      privilege answers and no-op extension handling.
- [x] `tools/patch-xnu-marker.py` implements the dual entry contract: raw
      `kexec_load` x0=0 gets `boot_args` through `XNH0` in pmsg, direct x0=PA
      stays valid, and the marker/scratch slots are documented in
      `docs/BOOT-CONTRACT.md`.

## 2. Display / DPU

- [x] CPM mailbox transport (`xnu/files/bsd/dev/hakios.blazer_cpm.c`):
      queue-based goog-mba v2, TX `0x05260000`, RX `0x05261000`, polled-first,
      `blazer_cpm=1` read-only plus separate `blazer_cpm_tx=1` write gate.
      `files/laguna_mbox.c` remains the reference snippet, not a patch stack.
- [ ] DPU power-on sequence, then a live scanout address — **without** the
      DC9x00 readback (see `SAFETY.md`: it is a RAS crash on real silicon).
- [x] The XNU-side display handoff is implemented: the 128-byte CRC'd record is
      validated before video changes; invalid records go headless, GOP/ramfb is
      preserved when no record exists, and CLEAN_ON_PRESENT/WC have distinct
      presenter cache paths. See `docs/BOOT-CONTRACT.md` for the forbidden ranges.
- [x] `MiniIOMFB` display-config selectors answered: 57 (color remap),
      27/17 (the 3084-byte get/set twin over a shadow block), 84/50/68 accept.
      This is what lets backboardd probe without a FrontBoardServices assert.
- [ ] A software renderer into the scanout (Mesa softpipe) and the
      GLI/EAGL shim for CoreAnimation: `{rctx, disp[1036]}` via
      `getMacroContextPrivate` (slot `n` at `8*(n+1)`), API 3, self-make-current
      wrappers because CA never calls `+setCurrentContext:`.
- [ ] The `kern.exec_inject` carrier (launchd strips `DYLD_*` from non-root
      jobs, so `DYLD_INSERT_LIBRARIES` in a plist is not enough).

## 3. Root + userland image

- [x] `RAMDisk → md0` ADT contract (byte-for-byte), `rd=md0 rootrw=1`,
      `ignition_level=1 launchd_unsecure_cache=1`; exact layout and span checks
      are in `docs/BOOT-CONTRACT.md`.
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
- [x] ramoops/pmsg panic capture (`hakios.blazer_panicdump`): opt-in
      `blazer_panicdump=1`, early+full nofault-guarded capture rungs, persistent_ram
      console append plus the CRC'd pmsg mirror. The missing-`pram` case uses a
      static 512 KiB fallback only when explicitly enabled; a real `pram` wins.

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
