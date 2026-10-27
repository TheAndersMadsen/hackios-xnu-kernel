# BRIDGE-EXEC-DIFF — why the 121.2 foundation kernel can't exec launchd from the HFS+ md0 root, and the minimal port from the 141 bridge lineage

Lane: **K3 (bridge-lineage exec diff)** · 2026-10-27 · **host-only** (no device actions, no QEMU chains — Q1 owns that).
Working dir: `/Users/andersmadsen/Developer/personal/hackios`.
Reads first: `research/laguna-badmacho/DECODE.md` (the merged badmacho-report; §6 = the QEMU-proof boot verdict),
`research/laguna-enoexec/tools/pack-cacheroot-qemu-f1.py` (docstring pins the DEVICEROOT/141 bridge kernel),
`research/laguna-141-bridge.md` (the 121.2→141.3 delta analysis), `research/laguna-usb-fix/ROOT-INTEGRATION.md` (root-v7 contract).

---

## 0. Verdict (5 lines, expanded)

- **Single most likely cause: our 121.2 foundation kernel has NO HFS driver, so its `mockfs` — whose mountroot accepts ANY device — steals the root mount of the HFS+-formatted md0 (root-v7) and serves the RAW 28 MiB volume as a synthetic single file `/sbin/launchd`.** The exec then reads the first page (1,024 zero HFS+ boot-block bytes + the `HX` volume header), `exec_mach_imgact`'s magic gate declines with a silent `-1`, and `execve` returns **ENOEXEC = errno 8** — exactly the observed death.
- The 141 bridge lineage survives the same image because tegu patch 0024-era code added `mockfs_check_macho()` (mockfs refuses any device that is not a Mach-O) **and** registered Apple HFS (`bsd/laguna_hfs`, hfs-627.100.6, `VFS_TBLCANMOUNTROOT`) so `vfs_mountroot()` falls through mockfs and lands on real HFS — QEMU-proven ("hfs: mounted iOSRoot" + launchd exec + dyld stderr, badmacho DECODE §6).
- Evidence chain: valid host bytes (root-v7 `72bf50b9…`, `fsck_hfs -fn` clean) + mount+lookup OK (`BSD root: md0` → `vfs_mountroot` ok in 9.6 ms, no panic) + silent `-1` decline (v58: `load_init_program: failed loading /sbin/launchd: errno 8`, zero execdiag lines because the booted mach predates K1's diagnostics) ⇒ the kernel is reading the WRONG BYTES under the RIGHT NAME — which is precisely what mockfs-as-root produces.
- Minimal patch set: **0001** (port `mockfs_check_macho` — mockfs stops claiming non-Mach-O devices) + **0002** (port `bsd/laguna_hfs` byte-identical + `bsd/conf/files` block + `options LAGUNA_HFS` + the `laguna_hfs_register()` call after `vfsinit()`). Both delivered in `patches-bridge-exec/`, `git apply --check` PASS on the live foundation tree, HFS files reproduce the 141 tree byte-identically (81 files).
- K1's v59 execdiag would confirm without any code change: expect `execdiag: /sbin/launchd: declined: first page is not a Mach-O image: err=-1 hdr[magic=0x00000000 …] fsize=29360128 vdata[0..15]= 00000000 00000000 00000000 00000000` — fsize equal to the WHOLE volume (28 MiB) instead of launchd's 541,600 B is the mockfs-steal signature. Post-port, expect `hfs: mounted iOSRoot on device md0` + `load_init_program` past errno 8.

---

## 1. Trees compared

| Role | Tree | Branch/state |
|---|---|---|
| OURS (device) | `research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` | `hakios` @ c54f35c (pristine Apple 121.2 tag) + 172-file live overlay (uncommitted; banner `xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837`, `Darwin 22.5.0`, mach build 09:21 CET — the v58 payload lineage) |
| 141 bridge | `hackios-xnu-kernel/laguna141/xnu-8796.141.3` | `laguna141-build` @ d4b68ee (0122; = pristine 141.3 + tegu 81-patch series + 0082-bridge rename; the lineage F1 pinned as RAW `mach.laguna141-badmacho` 632175a0, QEMU-proven HFS-md0 exec per DECODE §6) |

The two share xnu ancestry; `bsd/vfs`, `bsd/miscfs/mockfs`, `bsd/dev/memdev.c`, `bsd/kern/mach_loader.c` are point-release-stable between them (measured below), so the bridge deltas transfer mechanically.

## 2. Device evidence recap (the wall being explained)

v58 attempt (`research/laguna-usb-fix/attempts/20261027-141020-v58/reader-full.txt`, sha `8cdc6dd8e1243428`, same wall in v56 `255d40af…` and v57 `96db88f3…`):

```
[ 566.040730]: Darwin Kernel Version 22.5.0: Tue Oct 27 09:21:34 CET 2026; andersmadsen:xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837
[ 566.558811]: Added memory device md0/rmd0 (01000000/0D000000) at FFFFFFF008424000 for 0000000001C00000   ← 28 MiB = root-v7 size, correct
[ 566.558970]: BSD root: md0, major 1, minor 0
[ 566.559053]: bsd_init: vfs_mountroot
[ 566.568538]: bsd_init: calling devfs_kernel_mount        ← mount SUCCEEDED (9.6 ms, no "cannot mount root" loop, no panic)
[ 566.572557]: bsd_init: calling mountroot_post_hook
[ 566.574578]: load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development   → ENOENT (fine)
[ 566.576276]: load_init_program: attempting to load /sbin/launchd
[ 566.598783]: load_init_program: failed loading /sbin/launchd: errno 8                            ← THE WALL
compact stages: ROOT_MOUNTED(8) → LAUNCHD_EXEC(9) ×2 → PANIC(12)                                   ← panic: "Process 1 exec of %s failed, errno %d"
```

## 3. THE DIFF TABLE — HFS-md0 → exec path, 141 does X / 121 does Y

### (a) ramdisk/md0 registration + `rd=` parsing — EQUIVALENT, NOT THE WALL

| File | 141 bridge lineage | Our 121.2 foundation | Risk |
|---|---|---|---|
| `bsd/dev/memdev.c` (md0 device, "Added memory device md0/rmd0") | `mdSize` u32 + 0110 64-bit `(uint64_t)mdSize << 12` casts + MD-DIAG past-EOF print; `memdev_info.mi_base` cast u32 | `mdSize` already `uint64_t` (no wrap possible); same `mdevstrategy` EOF trim; wider `mi_base`; adds `mdevgetkernelrange()` | NONE — device registration works on device (v58 registered 0x1C00000 = 28 MiB exactly). No port needed. |
| `rd=md0` parse (`setconf`/`rootdevice`, `bsd_init.c`) | stock + redwood diagnostics | identical + `bsd_init:` bring-up prints (DEBUG_BSDINIT=1) | NONE — v58 proves the parse works (`BSD root: md0`). |
| ADT `/chosen/memory-map` RAMDisk → memdev | same contract (P3 ROOT-INTEGRATION §2) | same | NONE. |

### (b) ROOT MOUNT OWNERSHIP — **THE WALL LIVES HERE**

| File | 141 bridge lineage | Our 121.2 foundation | Risk |
|---|---|---|---|
| Filesystem roster | **HFS present**: `bsd/laguna_hfs/` (81 files: Apple hfs-627.100.6 core + encodings + `laguna_hfs_iokit.c` + compat header), `optional laguna_hfs` ×40 in `bsd/conf/files:393-433`, enabled by `options LAGUNA_HFS` (`config/MASTER:116`) | **NO HFS AT ALL** (`bsd/` has no `hfs` dir; no `laguna_hfs`/`sweet_hfs`; only `mockfs` + homegrown `apfs` + `ramfs`) | **ROOT CAUSE** — nothing in our kernel can parse the HFS+ root-v7 image |
| `bsd/vfs/vfs_conf.c` (static vfsconf + mountroot try order) | devfs → … → **mockfs LAST**; HFS joins dynamically via `vfs_fsadd` (first empty slot ⇒ AFTER the static entries) | devfs → … → **apfs** (`apfs_mountroot` first: "a device that is not APFS comes back EINVAL and vfs_mountroot() goes on to the next") → **mockfs last** ("accepts any root device") | Same mockfs-last shape; ours adds apfs ahead of it (correctly refuses HFS+) — so **mockfs is who claims the HFS+ md0 in ours** |
| `bsd/miscfs/mockfs/mockfs_vfsops.c` | `mockfs_check_macho()` gate: mountroot `buf_meta_bread`'s block 0 and **refuses any device whose first word is not a Mach-O thin/fat magic** (`EINVAL` → falls through to the next fs); also `mockfs_memory_backed = mi_mdev && mi_phys` (virtual RAMDisk → device I/O, not direct pager map) | the gate is still the **TODO comment** (`mockfs_vfsops.c:75-77`: "TODO: Validate that the device at least LOOKS like a mach-o"); mountroot claims ANY device and serves it as one synthetic executable file | **THE 2-KILOBYTE FIX** — port the gate (patch 0001) |
| `mockfs_lookup` (both trees identical) | names `sbin`→dir, `launchd`→the executable frontend for the backing device; file size = `si_devsize` (the WHOLE device) | identical | explains "lookup OK" on device: `/sbin/launchd` always resolves — to the raw volume |
| HFS mount + reads (UBC/pager/decmpfs) | `hfs_vfsops.c:1847` prints `hfs: mounted %s on device %s` (= "hfs: mounted iOSRoot on device md0"); reads go device-vnode → `memdev` strategy; `bsd/kern/decmpfs.c` present in BOTH trees (diff = 2 cosmetic lines, `UIO_STACKBUF`) — decmpfs is not in play (root-v7 files are uncompressed) | n/a (no HFS) | after port: reads flow through our memdev (64-bit sizes — no 141-0110 wrap bug to re-fix) |

### (c) EXEC PATH — our side is ALREADY permissive; no extra gate to remove

| File | 141 bridge lineage | Our 121.2 foundation | Risk |
|---|---|---|---|
| `bsd/kern/mach_loader.c` | byte-identical between the trees (**0 diff lines**) | same | NONE |
| `bsd/kern/kern_exec.c` magic gates (`exec_mach_imgact`) | stock gates + its own 0126-style execdiag on side branches | stock gates + **K1's unconditional `exec_diag()`** (kern_exec.c:1179-1199, fires with NO boot-arg, caps 16 lines, prints path + hdr fields + `fsize` + first 16 `vdata` bytes) at every decline site; the silent `-1` path is line 1236-1241 `declined: first page is not a Mach-O image` | NONE — but v58's mach (built 09:21) predates the diagnostic, hence the silence. First v59+ boot names the check by itself |
| AMFI / trust-cache / CS gates | `laguna_amfi.c` stand-in (permissive: `mpo_vnode_check_signature`→0, marks `CS_SIGNED\|CS_PLATFORM_BINARY`; policies registered from `kernel_bootstrap_thread` after the 0108 ROM-fix; trust caches v0=unused; `CONFIG_ENFORCE_SIGNED_CODE` nowhere defined ⇒ no exec denial) | **no AMFI stand-in at all** (`policy_check.c` = stock personal policy; no `CONFIG_ENFORCE_SIGNED_CODE`; `kern_trustcache.c` present but unconsulted) | NONE for errno 8 — exec never got past the magic gate; and post-port our exec path will accept unsigned launchd exactly like 141's does |
| `bsd/kern/bsd_init.c` | vfsinit → `laguna_hfs_register()` (line 724-725, right after `vfsinit()`); AMFI/sandbox registration moved OUT to startup.c (0108); sampler start (0111) | `DEBUG_BSDINIT=1` + R4 mirror stage prints; no fs registration beyond vfsinit's static list | port adds the same 2-line registration (in patch 0002) |

### (d) Anything else 141 patches that ours doesn't (checked, non-blocking)

`bsd/net*`, `osfmk/vm`, `bsd/kern` hardening = the upstream 121.2→141.3 point-release delta (bridge doc §1.2: 133 files) — irrelevant to this wall. `sleh.c` +3 / `machine_routines.c` +8 / `locore.s` +14 — crash diagnostics only. No exec/mount-relevant drift beyond the table above.

## 4. Root-cause verdict — evidence chain

1. **The image is valid on the host**: root-v7 = raw case-sensitive journaled HFS+, `HX` at offset 1024, volname `iOSRoot`, 29,360,128 B, sha `72bf50b9…`, `fsck_hfs -fn` clean, real `/sbin/launchd` 541,600 B (ROOT-INTEGRATION §1).
2. **md0 lands correctly**: v58 kernel registers exactly 0x1C00000 B (28 MiB — the v7 size) and `setconf` picks `md0` — so the `rd=md0` + ADT RAMDisk contract works.
3. **The mount succeeds with NO filesystem driver for HFS+**: our kernel's only mountroot-capable fs are `apfs` (first; validates APSB container → EINVAL on an HFS+ volume, by design) and `mockfs` (last; "accepts any root device"). `vfs_mountroot()` returned 0 in 9.6 ms with no HFS/apfs mount lines in the log — the only fs that CAN claim an arbitrary device is mockfs. Claim duration matches a trivial synthetic mount, not a real fs probe.
4. **mockfs serves the whole device as one file named `/sbin/launchd`**: `mockfs_lookup` resolves `sbin`→dir, `launchd`→the executable frontend of the backing device; `mockfs_fsnode_create(MOCKFS_FILE)` sets size = `si_devsize` = 29,360,128. Lookup therefore SUCCEEDS — exactly the observed "mount+lookup OK".
5. **The first page is not a Mach-O**: bytes 0..1023 of an HFS+ volume are the reserved boot blocks (zeroed), then `HX` + the HFS+ Volume Header at 0x400. `exec_mach_imgact` line 1236-1241: `magic != MH_MAGIC && magic != MH_MAGIC_64 → error = -1` — the documented "silent -1 decline". `-1` bubbles out of `exec_activate_image` as **ENOEXEC = 8**, `load_init_program` prints `failed loading /sbin/launchd: errno 8`, bsd_init panics — exactly v58.
6. **The 141 lineage is immune by two specific changes**: (i) its mockfs refuses non-Mach-O devices (`mockfs_check_macho` — the same check our tree still carries as a TODO comment at the same location), so the HFS+ md0 falls through; (ii) it carries a real HFS driver (`bsd/laguna_hfs`, registered with `VFS_TBLCANMOUNTROOT` right after `vfsinit()`, tried after the static entries) which claims the root and prints `hfs: mounted iOSRoot on device md0`. F1's QEMU run proved this exact chain end-to-end on this lineage (DECODE §6: `BSD root: md0` → HFS mount → launchd exec clean → dyld's own `_NDR_record` stderr — i.e. exec PAST the wall we die at).
7. **Why errno 8 and not something later**: nothing between mockfs-claim and the magic gate can reject — `exec_check_permissions` passes (regular file, size 28 MiB, first page read OK from memdev), so the very first exec check that CAN fire is the magic gate. The 22 ms mount→failure gap is one first-page read. "Kernel appears to read garbage first page" = it read the volume header instead of the file.

**Alternative hypotheses, closed**: (a) trust-cache/AMFI rejection — no exec-denying policy exists in our tree, and an AMFI/CS rejection raises `BAD_MACHO` via `load_machfile` (errno 8 too but AFTER the magic gate — yet the magic gate with a valid launchd would have parsed a real Mach-O header; K1's v59 print disambiguates on sight). (b) memdev 32-bit size wrap — ours is already 64-bit (`uint64_t mdSize`), and 28 MiB is nowhere near 4 GiB. (c) pager/UBC short-read → LOAD_IOERROR — that would print `errno 5`, not 8. (d) rd= parse failure — refuted by the md0 registration lines. (e) fat/cputype gates — magic check fires first.

## 5. Minimal port plan (delivered: `patches-bridge-exec/`)

Both patches are `git apply`/`patch -p1` format against the foundation tree root, `git apply --check` PASS on the live tree (read-only check; nothing was modified by this lane).

### Patch 0001 — `0001-blazer121-mockfs-refuse-non-macho-root-device.patch` (sha256 prefix `9b915994d41a92df`)
Whole-file diff of `bsd/miscfs/mockfs/mockfs_vfsops.c` ours→141:
- adds `#include <sys/buf.h>`, `<mach-o/loader.h>`, `<mach-o/fat.h>`;
- adds `mockfs_check_macho()` (reads DEV_BSIZE block 0 via `buf_meta_bread`, accepts only `MH_MAGIC/_64/CIGAM/_64`/`FAT_MAGIC/CIGAM`, `buf_markinvalid` + relse so no stale DEV_BSIZE block is cached for the next fs) and calls it first thing in `mockfs_mountroot` (replacing the TODO comment at line 75-77);
- carries the 141 `mockfs_memory_backed = mi_mdev && mi_phys` correctness hunk (only physical memory devices are pager-mapped) — inert when HFS owns the mount, correct if a Mach-O transport image ever uses mockfs again.

Effect: on an HFS+ md0, mockfs returns EINVAL and `vfs_mountroot` moves to the next fs. On a Mach-O transport image, mockfs behaves exactly as before (deviceroot-style single-binary roots keep working).

### Patch 0002 — `0002-blazer121-port-bridge-laguna_hfs-hfsplus-rootfs.patch` (sha256 prefix `e2554794afe9c570`)
- **new files**: `bsd/laguna_hfs/**` — 81 files (Apple hfs-627.100.6 core + hfs_encodings + `laguna_hfs_compat.h` + `laguna_hfs_iokit.c` + the compat `TargetConditionals.h` + Apple's kext scaffolding plists), **byte-identical to the 141 tree** (verified: applied into a scratch dir, `diff -r` vs `hackios-xnu-kernel/laguna141/xnu-8796.141.3/bsd/laguna_hfs` = empty);
- `bsd/conf/files`: the 40-line `optional laguna_hfs` block inserted after the mockfs entries (taken verbatim from the 141 tree);
- `config/MASTER`: one line `options LAGUNA_HFS` in the "4.4 filesystems" section (mirrors the 141 tree's enablement — no FILESYS_BASE surgery needed);
- `bsd/kern/bsd_init.c`: `extern void laguna_hfs_register(void); laguna_hfs_register();` immediately after `vfsinit();` — the identical placement the 141 lineage uses (`bsd_init.c:724-725`), which registers with `VFS_TBLCANMOUNTROOT` so `vfs_mountroot()`'s scan reaches it after the static entries (apfs → mockfs → hfs).

### Build recipe (owner: kernel build owner / K or M2 lane)
```bash
cd research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2
git apply --check patches-bridge-exec/0001-…patch && git apply --check patches-bridge-exec/0002-…patch
git apply patches-bridge-exec/0001-…patch && git apply patches-bridge-exec/0002-…patch
# wipe obj (fleet-wide lesson: doconf bakes absolute paths — badmacho evidence/kernel-build-attempt1.log)
rm -rf BUILD/obj
# then the standard MACHINE_CONFIG=BCM2837 (whatever the current build flow uses) DEVELOPMENT_ARM64 build
```
Expected compile-risk surface (LOW): the compat header undefs `BSD`/`XNU_KERNEL_PRIVATE`/`CONFIG_*` so the driver compiles kext-style against the 121.2 VFS KPIs; `bsd/vfs`+`mockfs` are point-release-stable between 121.2 and 141.3 (measured above), and `bsd/kern` drift is 13 net-hardening files that don't export KPIs HFS uses. If anything surfaces it will be a 0099-shaped link/export fixup, named by the compiler on the spot.

### Host-side validation recipe (no QEMU, no device)
1. `git apply --check` both patches on the tree (done, PASS).
2. Patch faithfulness: `diff -r` of the created `bsd/laguna_hfs` vs the 141 tree (done, BYTE-IDENTICAL, 81 files).
3. Roster gate (post-apply): `grep -c "laguna_hfs" bsd/conf/files` == 40; `grep -n "options.*LAGUNA_HFS" config/MASTER` == 1 line; `grep -n "laguna_hfs_register" bsd/kern/bsd_init.c` == the post-vfsinit pair.
4. Order gate (post-apply, static reasoning + code check): `bsd/vfs/vfs_conf.c` static order = apfs→mockfs (mockfs LAST); dynamic `vfs_fsadd` inserts at the first empty slot ⇒ HFS tried after both — so an HFS+ md0 must reach HFS. Verify no OTHER mountroot-capable fs was added in our tree (`grep -rn "mountroot" bsd/vfs/vfs_conf.c bsd/miscfs/*/[a-z]*_vfsops.c`).
5. Optional binary gate (post-build): `strings BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837 | grep -c "hfs: mounted"` ≥ 1 and `grep -c "mockfs_check_macho"` — plus K1's execdiag strings must still be present (`execdiag:`).
6. `execdiag-check`-style replay is NOT needed for this wall: tools/execdiag-check.py already proves `/sbin/launchd` bytes PASS every kernel exec check once the kernel reads the right file.

### QEMU note (Q1 owns runs)
If Q1 wants a host proof before a device window: the F1 harness (`research/laguna-enoexec/tools/run-qemu-f1.py`, under the guest-slot lock) already A/B-pins the bridge RAW kernel; packing the SAME payload with our patched 121.2 mach and expecting `hfs: mounted iOSRoot` + past-errno-8 would be the cleanest closure. **K3 ran no QEMU** (lane rail).

## 6. What this does NOT fix (expected next walls, unchanged)

dyld still needs the native cache transport (root-v7 ships the cache directory EMPTY by design; `_NDR_record`-class deaths on closure-stripped roots are the documented next layer), identity/dslocal walls are downstream of N10-lineage content, and the 141 tree's AMFI/Sandbox stand-ins (0120-0122 lineage) are NOT in this port — our exec path is permissive enough for launchd+dyld bring-up without them. Port scope = mount + exec past errno 8 only.

## 7. Summary (5 lines)

1. **Most likely cause**: no HFS driver in the 121.2 foundation kernel; `mockfs` (mountroot accepts ANY device — the 141 lineage's `mockfs_check_macho` gate is still a TODO comment in ours) steals the HFS+-md0 root mount and serves the raw 28 MiB volume as `/sbin/launchd`; the exec magic gate declines silently (`-1`) → errno 8.
2. **Evidence**: v58 = valid 28 MiB md0 + `BSD root: md0` + mount OK in 9.6 ms + `/sbin/launchd` lookup OK + `failed loading /sbin/launchd: errno 8` with zero execdiag lines (mach predates K1's prints); root-v7 is valid HFS+ on host; 141 lineage execs the same image in QEMU via HFS (`hfs: mounted iOSRoot`, DECODE §6).
3. **Minimal patch set**: `patches-bridge-exec/0001` (mockfs Mach-O gate, ~35 lines) + `0002` (byte-identical `bsd/laguna_hfs` port + 40-line conf/files block + one `options LAGUNA_HFS` + 2-line post-vfsinit `laguna_hfs_register()`); both `git apply --check` PASS, HFS files byte-identical to the 141 tree.
4. **K1 v59 confirmation**: on the CURRENT (unpatched) kernel, v59's unconditional execdiag should print `declined: first page is not a Mach-O image` with `magic=0x00000000`, `fsize=29360128` (= the whole volume, not 541,600) and 16 zero vdata bytes — the mockfs-steal fingerprint; if instead `fsize=541600` with a real `0xfeedfacf` header, the cause is downstream (load_machfile), and the same print names it.
5. **Paths**: diff+verdict+plan = `research/laguna-usb-fix/BRIDGE-EXEC-DIFF.md`; patches = `research/laguna-usb-fix/patches-bridge-exec/{0001-blazer121-mockfs-refuse-non-macho-root-device.patch,0002-blazer121-port-bridge-laguna_hfs-hfsplus-rootfs.patch}`; compact report = `/tmp/fleet/k3-bridge-report.md`.
