# Porting checklist for `blazer` (Tensor G5 / laguna)

This is the working map of what the port needs and where each piece lives.
It is meant for someone picking up a task, not as a history log.
Build, image and handoff details live in `docs/BUILD.md`,
`docs/BOOT-CONTRACT.md` and `docs/RENDER.md`. Checked boxes mean implemented
in source on this branch or covered by the documented build recipe; they do
not imply an on-device validation claim — QEMU results are labeled QEMU.

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
      The sequence itself is host-tested and specified to the word — CPM
      queue reset, DPU top/DSI0/FE0(/FE1) power frames, the 128-byte
      first-stage handoff, and the fail-closed gate table — see
      `docs/RENDER.md` §2–§6. What remains is the device-gated integration:
      a display-owning Linux first stage (cold Google UFS/PHY/CPM and DPU
      production live there by design) that emits a valid record.
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
      The bridge itself is proven where it counts so far: the upstream
      EAGL/GLI path + Mini CPU-copy presenter rendered the stock lock screen
      in QEMU (non-GL image, CPU path). What remains is the laguna GL flag
      chain — a `GL=1` image plus `gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1` —
      and the device scanout; `gpu=1` (Adreno) is forbidden on Tensor.
      The full guide is `docs/RENDER.md`.
- [x] The `kern.exec_inject` carrier is in the foundation: `kern_minimac.c`
      answers AMFI op `0x5a` with `0xff` under the exact policy name `AMFI`,
      so `kern.exec_inject` injects the GL bridge without any plist
      `DYLD_*` (which launchd strips from non-root jobs anyway).

## 3. Root + userland image

- [x] `RAMDisk → md0` ADT contract (byte-for-byte), with `RAMDisk1` → `md1`
      for the `/redwood` auxiliary image; `rd=md0` only; exact layout and
      span checks are in `docs/BOOT-CONTRACT.md`. Do not blindly add the old
      `ignition_level=1`, `launchd_unsecure_cache=1`, `rootrw=1` tokens —
      the new foundation's RAM boot does not consume them.
- [x] The full dyld shared cache, every file owned by **uid 0**: covered by
      the verified image build (`docs/BUILD.md`), with the cache in the
      cryptex-native `/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld`
      location dyld-1042 searches. No `ignition` argument is needed on this
      image.
- [x] No `/usr/lib/system/libdispatch.dylib` on disk: the built APFS image
      has zero top-level split dylibs, so the dyld-1042 disk-override rule
      cannot fire. The explicit prune still applies to older HFS-cacheroot
      chains (N1's pruned cacheroot is the worked example).
- [x] Account identity: the built image carries the complete
      `/private/etc/{passwd,master.passwd,group}` closure (mobile/501) and
      real `lsd`, so launchd resolves every daemon account. HFS-cacheroot
      chains that additionally need dslocal records use the real generated
      records — 41 users + 99 groups from the target root's own files,
      staged root-owned (`tools/dslocal/`, proven at 150/150 records `0:0`
      with a 940-check gate). No invented accounts.
- [x] LaunchServices bootstrap without self-deadlock: the image ships real
      mobile/system `lsd` with all 13 mobile services including
      `com.apple.lsd.mapdb` — no mapdb stub. The LS store is deliberately
      unseeded (lsd rebuilds from `/Applications` on first boot); allow the
      long first boot instead of seeding or deleting the store.
- [x] MobileCoreServices seeding: stock first-boot rebuild is the design;
      no `.csstore` seed/delete delta is applied.

## 4. Touch, storage, other drivers

- [ ] TCM touch: AOC pad-slot layout, TBN bus-parked check, CPM hard
      reset/rails, TCM generation detect, SSI clock liveness. The host-tested
      driver exists (198/198); the on-device bring-up is deferred to a later
      rotation.
- [ ] Cold UFS production: the in-kernel side is source-integrated and
      fail-closed — `xnu/files/bsd/dev/hakios.blazer_ufs.c` is a read-only
      DWC UFS takeover requiring `blazer_ufs_live=1` plus an explicit
      `blazer_ufs_root=NAME`, exposing `/dev/ufsroot0` with a matching
      `rd=ufsroot0` root-selection branch. **Cold Google UFS/PHY/CPM
      initialization deliberately stays in the Linux first stage** — XNU
      does not guess the bring-up sequence, writes nothing, and the
      userdata-file → F2FS ro-mount → `md0` ladder remains the on-device
      root transport.
- [ ] `var_store` backend: the persistence mechanism is complete in the
      image (`/bin/var_store` + its launchd plist), but Tensor
      `var_store=1 ufs_write=1` stay **off** until a safe storage/write
      backend with partition-independent bounds and read-back verification
      exists. RAM-only boots repeat first-boot LS work by design until then.
- [x] ramoops/pmsg panic capture (`hakios.blazer_panicdump`): opt-in
      `blazer_panicdump=1`, early+full nofault-guarded capture rungs, persistent_ram
      console append plus the CRC'd pmsg mirror. The missing-`pram` case uses a
      static 512 KiB fallback only when explicitly enabled; a real `pram` wins.

## 5. Delivery and evidence

- [x] `fastboot boot` images (boot v4 + AVB SHA256_RSA4096 footer) carrying
      the kernel, the ramdisk contract and the real laguna DTB — host-built
      and host-validated as the two transport controls (kexec primary,
      direct-embedded fallback), with `avbtool verify_image`, unpack
      round-trip and strict cpio walk gates. Device delivery is
      operator-owned per `SAFETY.md`; nothing is ever flashed.
- [x] `rdinit=/xni` for the Magisk/`init_boot` race: the Linux Image's
      **builtin** command line carries `rdinit=/xni` with
      `CONFIG_CMDLINE_EXTEND=y`, so it is appended last and wins over
      Magisk's concatenated `/init`; `/xni` does not exist in the Magisk
      fragment (see `README.md`).
- [ ] Evidence on the phone: pstore console + the pmsg marker + the
      `PANIC-CAPTURED` decoder (unslid, symbolicated frames against the
      payload's symbol table), plus the uploaded-ramdisk provenance checks.
      The QEMU-side equivalent is already proven (lock screen, zero panics).

## 6. Where help is wanted now

| Task | The wall |
| --- | --- |
| Cold UFS/DPU production | The Linux first stage that owns cold Google UFS/PHY/CPM init **and** the display-owning DPU producer (a dedicated linear scanout preserved across kexec, emitting the 128-byte handoff). This unlocks the first device render. |
| TCM touch on device | AOC pad-slot layout, TBN bus-parked check, CPM hard reset/rails, generation detect, SSI clock liveness. |
| GL flag chain | A `GL=1 REST=1` userland build exercised with the softpipe boot-args on the platform (`docs/RENDER.md` §8–§9). |
| `var_store` backend | A safe Tensor storage/write path with independent bounds and read-back verification. |
| Cellular future | Unstarted on this SoC — no MPSS/IPA/Hexagon work is planned for Tensor; a new modem stack would be its own project. Wi-Fi, camera, charging and BT likewise. |

## Reference evidence

The probes and logs behind each item above were captured on the real phone,
in QEMU, and in the host build/validation lanes during the laguna bring-up.
They are deliberately not committed here (logs and Apple-owned images stay
out of the repo); the mechanics are what is documented in this file and in
the driver sources. The QEMU render milestone (stock lock screen on the
blazer-branch kernel, zero panics, pixel-match 2.9–3.1/255 against an
independent control) and the image/dslocal verification gates are described
in `README.md`, `docs/BUILD.md` and `tools/dslocal/README.md` with their
exact commands and pass criteria.
