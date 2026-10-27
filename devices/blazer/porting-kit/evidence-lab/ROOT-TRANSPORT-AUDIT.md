# ROOT-TRANSPORT-AUDIT.md — K2 lane (RAMDisk/rootfs transport audit), 2026-10-27 · HOST-ONLY

Scope: map the rootfs transport end-to-end for out/next-boot-v58 (root-v8 profile,
payload 0279e9c0…, boot img 8cdc6dd8…), run P3's verify-payload.py, resolve the
"raw-miss" anomaly, and find where the kernel could read wrong bytes at the launchd
ENOEXEC wall. Device-fresh dumps used: attempts/20261027-135921-v57 and
-141020-v58 (both mach = blazer-r4r5 lineage; v58 kernel log timestamps quoted).

## Verdict (one paragraph)

The HOST-side transport is byte-perfect: root-v8.hfs (29,360,128 B, sha
77dc3e9f3e203481…) is embedded RAW — no compression, no wrapping — as TEGUXNU
segment 10 at payload offset 0xfa8470, byte-identical (verified), with
/chosen/memory-map "RAMDisk" = <0xd4424000, 0x1c00000> added by mkpayload.py to
the ADT copy inside the payload; verify-payload.py = **ALL CHECKS PASS**. On
device, the kernel registered md0/rmd0 at VA 0xfffffff008424000 for 0x1C00000 —
exactly the payload's seg10 — so the ADT/boot-args/segment metadata path is
correct. **The ENOEXEC wall is NOT a byte-corruption wall: it is a FILESYSTEM
CONTRACT BREAK. The v53+ blazer-foundation lineage kernel has NO HFS driver at
all** — vfs_mountroot can only try apfs_mountroot (gated on NXSB magic) and
mockfs_mountroot (gated on Mach-O magic), both of which decline an intact HFSX
volume. The observed "successful" root mount (VFS_ROOT ok, no "cannot mount
root" retry loop, and ZERO hfs lines — there is no hfs code in the binary to
print them) can therefore only be a **silent false-mount by mockfs or apfs on
block-0 bytes that are NOT root-v8** (stale DRAM from the APFS-era boots or
partially-copied kernel bytes). mockfs fabricates /sbin/launchd as a file node
backed by the whole device; execve then reads a non-Mach-O first page →
`exec_mach_imgact` −1 → ENOEXEC (errno 8) → panic — exactly the observed
signature, including the "garbage/zeros first page" evidence and the silent −1
decline. The proven-working F1 lineage printed "hfs: mounted iOSRoot" because
it was the OLDER kernel lineage (v52- and kexec-era payloads contain the hfs
driver + that print string); the v53 lineage switch silently dropped HFS.

## 1. Transport map (build → kernel consumption)

### Build host side
1. `research/laguna-usb-console/tools/build-next-boot.sh` (WITH_ROOT8=1):
   - ROOTFS = root-upgrade/root-v8.hfs, pin `29360128:77dc3e9f…`, MEMSIZE
     0x0A400000 (ADT kernel window 0x0A000000→0x0A400000 for the +4 MiB root).
   - mkadt.py → laguna.adt (dram-base 0xd0000000, mem-size 0x0A400000, NVRAM
     CHRP props; **no RAMDisk prop at this stage**).
   - mkpayload.py kernel adt out --cmdline "…rd=md0 rootrw=1 ignition_level=1
     launchd_unsecure_cache=1 …" --ramdisk root-v8.hfs.
2. `hackios-xnu-kernel/blazer/teguload/mkpayload.py` (the embedder):
   - Rebases LC_DYLD_CHAINED_FIXUPS, then emits: TEGUXNU\0 v1 header
     (virt_base 0xfffffff004000000, entry 0xfffffff0074364a8, phys_base
     0xd0000000, mem_size 0x0A400000), cmdline at 0x48 (608 B), seg table at
     0x2A8 (11 segs × 32 B), kernel segments flat, **then the rootfs appended
     RAW as one more segment** (vmsize = page-rounded 0x1C00000; file 29,360,128
     B = 0x1C00000 exactly, so no zero-fill), ADT copy LAST at payload offset
     0x2ba8470 with **/chosen/memory-map "RAMDisk" = <pa, size> = two u64
     <0xd4424000, 0x1c00000> added by add_ramdisk_to_adt()**. No compression
     anywhere; the kernel never parses TEGUXNU — the shim does.
   - ramdisk VA = kernel_end = 0xfffffff008424000; PA = phys_base + (va −
     virt_base) = 0xd4424000; region ends 0xd6024000 < topOfKernelData
     (metadata top 0xd6030000) — inside the never-handed-to-VM bootstrap area.
3. `shim/build.sh`: appends `TEGUPLD1` + u64 length + the payload bytes at _end
   of the raw arm64 Image and bumps image_size (u64 at _head+16); mkbootimg v4
   wrap + AVB SHA256_RSA4096 footer (verified PASS).

### Device side (from dumps + loader.c)
4. ABL (fastboot boot) loads the Image; shim `loader.c` finds the TEGUPLD1
   envelope, validates admission (payload.h 64 MiB headroom rule: top
   0xd6030000, window_end 0xda400000, headroom 67.81 MiB ≥ 64 MiB — PASS),
   copies each TEGUXNU segment to PA = phys_base + (vmaddr − virt_base)
   (ascending, MMU-off Device-memory word copies, then places ADT at
   0xd6024000 and boot_args at 0xd6030000), EL2→EL1 drop, jump.
5. Kernel: boot_args → ADT → `/chosen/memory-map` "RAMDisk" →
   `IOKitBSDInit.cpp:758-772` IOFindBSDRoot: `mdevadd(-1,
   ml_static_ptovirt(pa)>>12, size>>12, phys=0)` → **md0/rmd0**, then the
   `rd=md0` branch (IOKitBSDInit.cpp:792-827) picks `mdevlookup(0)`.
   On-device v58 log proves this leg: `Added memory device md0/rmd0
   (01000000/0D000000) at FFFFFFF008424000 for 0000000001C00000` + `BSD root:
   md0, major 1, minor 0` — **PA/size/VA match the payload segment exactly**.
6. Read path: `bsd/dev/memdev.c` mdevstrategy — mdPhys=0 (virtual), so every
   block read is a flat `bcopy` from kernel VA mdBase+blkoff. No pager, no
   remap, no caching layer between HFS and the ramdisk DRAM.
7. Root mount: `vfs_mountroot()` (vfs_subr.c:1210) walks vfsconf in order and
   tries each `vfc_mountroot`. **In the blazer-foundation tree
   (research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2 — the tree
   that built the v53+ machs, incl. v57/v58's blazer-r4r5) the static list is:
   devfs, nullfs, bindfs, ramfs (all mountroot=NULL → skipped), APFS
   (apfs_mountroot, gated: `memcmp(sb+32,"NXSB",4)`, EINVAL otherwise,
   apfs_vfsops.c:1044), mockfs (mockfs_mountroot, gated on Mach-O magic in
   device block 0, mockfs_vfsops.c:73-102), routefs (NULL)** — and **there is
   no bsd/hfs or bsd/laguna_hfs in this tree at all** (verified: no such dir;
   mach.blazer-r4r5-tcm has `_apfs_mountroot` + `_mockfs_mountroot`, no hfs
   mount symbols; payload byte-scan: "hfs: mounted" MISS, NXSB present,
   mockfs_mountroot present). hfs would only exist if a driver registered via
   vfs_fsadd — none does in this lineage.

## 2. Integrity verdict

`verify-payload.py ../../laguna-usb-console/out/next-boot-v58/payload-usb.bin
--expect-root ../root-upgrade/root-upgrade-root-v8.hfs --image <raw Image
extracted from shim-boot-laguna.img>` → **ALL CHECKS PASS** (exit 0):
- TEGUXNU v1 header, nsegs=11, cmdline 192 chars.
- ramdisk segment present, **embedded root byte-identical to root-v8.hfs**
  (sha256 77dc3e9f…), vmsize page-rounded 0x1c00000, PA 0xd4424000.
- ADT /chosen/memory-map RAMDisk = (0xd4424000, 0x1c00000) inside the payload.
- shim geometry: metadata top 0xd6030000 ≤ window_end 0xda400000; 64 MiB
  headroom PASS (67.81 MiB).
- Image envelope: image_size 47,091,280 == file size; TEGUPLD1 envelope at _end
  = 1,294,336; ≤128 MiB guard. Budget: payload 43.68 MiB (raw shim cap ~52 MiB,
  ABL boot cap 64 MiB) — fits.
- NOTE: passing `--image shim-boot-laguna.img` (the mkbootimg boot image)
  FAILS the envelope checks by construction — the tool wants the RAW arm64
  Image (extract kernel: offset 0x1000, size 47,091,280 from the v4 header).
  First run's "FAIL image_size/envelope" was this harness misuse, not a defect.
- On-device: md0 registered at exactly the payload's PA/size (see §1.5) — the
  metadata legs of the transport are proven correct by the v58 serial.

**Host integrity: PASS. The payload is not where wrong bytes enter.**

## 3. Anomaly resolutions

1. **"root-v8 bytes not findable raw in the built image" — FALSE ALARM.**
   root-v8.hfs begins with **1024 zero bytes** (HFS reserved area); the volume
   header signature 'HX' sits at +1024 and the first non-zero byte is at 1024.
   A prefix search therefore hits zero-padding, not the file. The real
   fingerprint IS present raw: payload offset 0xfa8870 (= seg10 file_off
   0xfa8470 + 1024) and AVB image offset 0x10e5880. No compression, no
   wrapping: flat segment append only.
2. **"laguna.adt has NO RAMDisk property string" — EXPECTED.** mkadt.py's
   output is the INPUT ADT; mkpayload.py's add_ramdisk_to_adt() adds
   /chosen/memory-map "RAMDisk" = <pa,size> only to the ADT copy embedded in
   payload-usb.bin (verified present: 0xd4424000/0x1c00000).
3. **How Q4 "verified 3 ways"**: research/laguna-usb-fix/root-integration/
   verify-payload.py (byte-identical root vs embedded segment, ADT prop vs
   segment PA/size, envelope math vs Image) + Q4's own ADT byte-check + AVB
   verify — all re-confirmed here for v58.

## 4. Where the kernel could read wrong bytes — the actual wall

- The kernel-side legs are proven correct by the v58 serial (md0 at the exact
  payload PA/VA/size; BSD root md0; vfs_mountroot → VFS_ROOT succeeded;
  bsd_init ran to load_init_program).
- **But the lineage that booted v57/v58 CANNOT MOUNT HFS.** If the ramdisk
  bytes on device were intact root-v8 (block 0 = 1024 zeros), apfs_mountroot
  (no NXSB) and mockfs_mountroot (no Mach-O magic) BOTH decline → all
  mountroot candidates fail → bsd_init prints "cannot mount root, errno = N"
  and retries forever. **That print is absent, and root DID mount** → block 0
  of md0 on device contained NXSB (stale APFS container from the v50/v51-era
  boots, same kernel layout ⇒ same ramdisk PA 0xd4424000) or Mach-O magic
  (stale/partial kernel bytes) — i.e. **the ramdisk DRAM region did not hold
  root-v8 bytes at mount time** (copy skipped/stale-region scenario), and one
  of the two silent gate-filesystems false-mounted it.
- mockfs is the precise ENOEXEC mechanism: it fabricates a three-node tree
  (root, dev, **file node = the whole device as one executable**) — so
  /sbin/launchd ALWAYS opens, then execve reads the device's first page:
  non-Mach-O → `exec_mach_imgact` −1 (silent, kern_exec.c pre-K1 gates) →
  ENOEXEC errno 8 → "Process 1 exec of %s failed, errno %d" panic. This also
  explains why PATH LOOKUP "succeeded" (mockfs invents the file) and the
  first-page reads as garbage/zeros. apfs false-mount would similarly serve a
  stale volume's launchd — but with a real (stale) file, less likely to yield
  plain zeros.
- Corroborating timeline (same wall, four different roots):
  - v55 13:51 (root-v6 default) → "attempting to load /sbin/launchd" @42.60s,
  - v56 13:58 (root-v7 72bf50b9) → errno 8 @108.31s,
  - v57 13:59 + v58 14:10 (root-v8 77dc3e9f) → errno 8 @105.44s / @566.59s.
  **Identical death across v6/v7/v8 roots ⇒ the root CONTENT is exonerated;
  the kernel lineage (no-HFS + silent apfs/mockfs mountroot gates) is the
  culprit.** The F1 precedent decodes: the lineage that printed
  "hfs: mounted iOSRoot" was the older kexec-era kernel (payloads ≤v52 contain
  "hfs: mounted"; v50/v51 are APFS-era, v53+ dropped HFS entirely).
- Alternative (weaker) reading kept for completeness: if one insists hfs
  existed, the failure would have to be offset-dependent corruption of
  launchd's data blocks (~15.5 MiB in, PA 0xd53a2000) with intact metadata —
  but there is no hfs driver in the binary to mount anything, so this reading
  requires evidence that does not exist. K1's execdiag v59 boot will settle it:
  its `execdiag:` line prints the first 16 bytes the kernel actually read for
  /sbin/launchd + parsed magic (zeros → pager/md0/stale-region; garbage →
  wrong-offset; valid cf fa ed fe → gate theory wrong).

## 5. Recommendations (transport side)

1. **Rootfs format contract for the blazer-foundation lineage is APFS** (the
   tree's own comment: "the RAM disks are APFS containers"; v50/v51 payloads
   are the APFS era). root-v7/v8 HFSX volumes cannot ever mount there. Either:
   (a) rebuild the root as an APFS container the mini-apfs accepts, or
   (b) port bsd/laguna_hfs (present, with "hfs: mounted %s on device %s"
   success print, in hackios-xnu-kernel/laguna141/xnu-8796.141.3* and
   research/laguna-{panic-capture,badmacho,renderer}/kernel-wt) into the
   foundation tree and register via vfs_fsadd before mountroot.
2. **Make false-mounts loud**: mockfs/apfs mountroot success at boot should
   print the fs name (neither prints on success — that silence is exactly what
   masqueraded as "hfs mounted ok" for four boots). One printf in
   mockfs_mountroot/apfs_mountroot success paths would have surfaced this wall
   four boots ago.
3. **Verify the shim actually copied the ramdisk**: add a shim console
   checksum (e.g. first 64 B + length of seg10 post-copy) — today nothing on
   the device side proves the ramdisk bytes landed (the md0 registration only
   proves the ADT prop).
4. Keep the boot-args contract (rd=md0 …) unchanged; it is consumed correctly.

## 6. Files touched by this lane

- This file (ROOT-TRANSPORT-AUDIT.md) — new.
- /tmp/fleet/k2-transport-report.md — compact report.
- /tmp/fleet/COORDINATION.md — one line.
- /tmp/k2 scratch (raw Image extract, root-v8 mount) — cleaned up; no repo
  source or build artifacts modified.
