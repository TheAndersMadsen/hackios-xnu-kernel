# ENOEXEC-ROOT-MOUNT — the errno-8 wall after the first real root mount (v55 → v59)

Lane: K7 (ledger + story doc) · 2026-10-27 · host-only, no device actions.
Evidence: `attempts/20261027-135113-v55/reader-full.txt` …
`attempts/20261027-150908-v59/reader-full.txt`, kernel source
`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2` (branch
hakios), binaries `mach.blazer-r4r5-tcm` (68906 syms, `b39179b5…`) and
`mach.blazer-r4r6-execdiag` (68909 syms, `34b300e0…`).
Companion docs: INIT-NVRAM.md (the v54 wall this one succeeds), INIT-ASSERTION.md
(the v53 wall), BRIDGE-EXEC-DIFF.md (K3's source-level diagnosis this doc confirms
on device), ROOT-TRANSPORT-AUDIT.md (K2's transport exoneration).

---

## (a) Symptom progression v55 → v59

Five boots, one wall. Every attempt reached the SAME ladder and died the SAME
death; each iteration removed one hypothesis.

| boot | candidate | what it tested | ladder | death |
|---|---|---|---|---|
| v55 13:51 | `00c23ba634495784…` (v55 CHRP-NVRAM ADT, mach r4r5-tcm) | the NVRAM fix | 8 rungs → ROOT-MOUNT | `errno 8` @42.62 s, panic, NO panic string recovered |
| v56 13:55 | `255d40afb6c5fc59…` (root-v7, DRAM window 0x0A400000) | the +4 MiB window | same → ROOT-MOUNT | `errno 8` @108.32 s |
| v57 13:59 | `96db88f3a1757aa8…` (root-v8, libdispatch restored) | root content | same → ROOT-MOUNT | `errno 8` @105.45 s |
| v58 14:10 | `8cdc6dd8e1243428…` (root-v8, `systrace=1` in cmdline) | trace the decline | same → ROOT-MOUNT | `errno 8` @566.60 s, **zero trace output** |
| v59 15:09 | `51f3d78d71f403fc…` (root-v8, mach r4r6-execdiag) | see the bytes | same → ROOT-MOUNT | **execdiag fingerprint** @3413.24 s |

What each boot PROVED:

- **v55 — first root-mount success.** The v54 NVRAM panic is gone (INIT-NVRAM.md
  fix verified on device): `NVRAM size is 8192 bytes` (IONVRAM.cpp:1364),
  `_bankSize=0x2000, _bankCount=0x1, _currentBank=0` (:1176), unserializeImage ok.
  MILESTONES reached 8/10: XNU-ENTRY, PE-INTCTRL, PE-TIMER, VM-BOOTSTRAP-DONE,
  **NVRAM-OK**, PE-1695-PASSED, **CPU-REG**, **ROOT-MOUNT** (`BSD root: md0`,
  `vfs_mountroot ok`, `bsd_init: done`). Then `load_init_program: attempting to
  load /sbin/launchd` → `failed loading /sbin/launchd: errno 8` → `MACH Reboot`.
  The panic channel carried NO message string (the reader's `wall=UNKNOWN-PANIC`)
  because the fatal `panic()` here is a *format-string* panic at the end of
  `load_init_program` and this boot's capture ring did not recover its text —
  the console `errno 8` line is the real verdict.
- **v56 — window exonerated.** v56 needed the 0x0A400000 DRAM window for its
  larger payload (dram-size 0x0A000000→0x0A400000, root-v7). Identical ladder,
  identical death ⇒ the relocated window does not corrupt the boot.
- **v57 — root content exonerated.** root-v8 (Q5's restore of the split
  `libdispatch.dylib`) died identically to root-v7 ⇒ no root generation is at
  fault; the kernel never actually READS either of them at exec time.
- **v58 — the decline is SILENT.** `systrace=1` armed the kern_exec.c TUNABLE
  (kern_exec.c:1165-1166) and produced NOTHING: the TUNABLE declares the
  variable but the booted mach (r4r5-tcm) has no print at the decline site.
  `exec_mach_imgact`'s magic gate returns a bare `-1`, the activator loop
  collapses to ENOEXEC, no trace. This motivated K1's unconditional instrument.
- **v59 — fingerprint.** execdiag fired exactly as K3 predicted
  (BRIDGE-EXEC-DIFF.md §verdict):

```
[3413.221081]: load_init_program: attempting to load /sbin/launchd
[3413.242753]: execdiag: /sbin/launchd: declined: first page is not a Mach-O image: err=-1 hdr[magic=0x00000000 cputype=0x00000000 cpusub=0x00000000 filetype=0x00000000] fsize=29360128 vdata[0..15]= 00000000 00000000 00000000 00000000
[3413.243090]: execdiag: /sbin/launchd: ENOEXEC: no execsw image activator accepted this file: err=8 hdr[magic=0x00000000 …] fsize=29360128 vdata[0..15]= 00000000 …
[3413.243453]: load_init_program: failed loading /sbin/launchd: errno 8
```

  Two numbers convict: **fsize=29360128** (= 0x1C00000 = 28 MiB = the WHOLE
  root-v8 volume, not launchd's 541,600 B) and **vdata = 16 zero bytes** (the
  HFS+ reserved boot block; the `HX` volume header lives at offset 1024).
  The kernel exec'd the raw volume as a "file" — mockfs stole the root mount.

---

## (b) Root cause chain — file:line

```
bsd_init()                                              bsd/kern/bsd_init.c
 ├─ vfs_mountroot()                                     :889
 │   └─ vfs_mountroot()                                 bsd/kern/vfs_subr.c:1210
 │       └─ walk vfsconf in registration order
 │           ├─ apfs_mountroot()     → EINVAL on an HFS+ volume (by design;
 │           │                         NXSB container gate)  vfs_conf.c:239
 │           └─ mockfs_mountroot()   → CLAIMS ANY DEVICE  vfs_conf.c:264
 │               └─ mockfs_mountroot()                  bsd/miscfs/mockfs/
 │                   │                                   mockfs_vfsops.c:105
 │                   │   (v55–v59 binary: NO gate — the
 │                   │    mockfs_check_macho TODO; K3's gate
 │                   │    is applied in source NOW at :73-102,
 │                   │    NOT in the booted machs)
 │                   └─ builds a synthetic tree: root dir + devfs
 │                       mountpoint + ONE file node backed by the WHOLE
 │                       rootvp  (mockfs_vfsops.c:58-63, :129)
 ├─ LW_STAGE_ROOT_MOUNTED "vfs_mountroot ok"            bsd_init.c:927-930
 │     (the ROOT-MOUNT milestone is real for mount, blind for CONTENT)
 ├─ VFS_ROOT → set_rootvnode                            bsd_init.c:915-924
 ├─ bsd_do_post → load_init_program(p)                  bsd_init.c:1113
 │   └─ load_init_program()                             bsd/kern/kern_exec.c:7160
 │       ├─ LW_STAGE_LAUNCHD_EXEC (attempt flag)        kern_exec.c:7114
 │       │     ← the LAUNCHD_EXEC(9) compact stage = ATTEMPT, not success
 │       ├─ load_init_program_at_path("/sbin/launchd")  kern_exec.c:7033
 │       │   └─ execve()
 │       │       └─ exec_activate_image()               kern_exec.c:2180
 │       │           ├─ namei OK — mockfs resolves the path      :2218
 │       │           ├─ exec_check_permissions OK — mockfs reports a
 │       │           │   regular file (va_data_size = 29360128)  :2247
 │       │           ├─ first-page vn_rdwr reads 4096 ZERO bytes
 │       │           │   (HFS+ reserved boot block)              :2262
 │       │           ├─ execsw loop: exec_mach_imgact parses
 │       │           │   magic=0x00000000 → declines SILENT -1
 │       │           │                                           :1236-1241
 │       │           │   (exec_fat_imgact + exec_shell_imgact
 │       │           │    decline too; execsw @ :2143-2148)
 │       │           └─ error still -1 → ENOEXEC + exec_diag     :2340-2342
 │       └─ printf "failed loading … errno 8"                    :7206-7209
 │           └─ panic("Process 1 exec of %s failed, errno %d")   :7215
 │               (DEVELOPMENT: /sbin/launchd via launchdsuffix
 │                path, :7182-7188; same panic either way)
 └─ MACH Reboot
```

The design flaw in one sentence: `mockfs_mountroot`'s contract comment says it
assumes "rootvp is an executable" (mockfs_vfsops.c:60-63) and its gate was
still a TODO in this lineage — so the LAST filesystem in the mountroot scan
always wins on a device no earlier fs claimed, and every read under the root
returns the raw device. apfs (correctly) refuses an HFS+ volume, mockfs (wrongly)
accepts everything, HFS does not exist in this tree ⇒ the HFS+ md0 can only
land on mockfs. `exec_check_permissions` passes because mockfs's file node IS a
regular file; only the Mach-O magic gate can refuse — and it does, silently.

Why the panic string went missing: the fatal panic is the *formatted* one at
kern_exec.c:7215 reached only after the loop exhausted every `init_programs[]`
entry (kern_exec.c:7120+). v55's capture recovered no text for it
(`panic: channel=mirror complete=True message=None`), so the reader showed
`wall=UNKNOWN-PANIC` — the console `errno 8` line is the authoritative verdict,
and from v56 on the ladder's evidence was read from the console tail, not the
panic string.

---

## (c) The execdiag instrument (K1)

Source: `bsd/kern/kern_exec.c`, mach `research/laguna-usb-fix/
mach.blazer-r4r6-execdiag` (sha `34b300e039da9207…`, 16,416,968 B, +112 B over
r4r5-tcm; banner 14:22:25; `execdiag` strings + `_exec_diag`/`_exec_diag_lines`
symbols + the systrace TUNABLE entry verified in-binary). r4r5-tcm is untouched.

- **`exec_diag()` — kern_exec.c:1181-1199.** Prints path, the Mach-O header
  fields PARSED FROM THE PAGE THE KERNEL ACTUALLY READ (`imgp->ip_vdata`),
  the file size recorded at permission-check time (`ip_arch_size`), and the
  first 16 bytes of that page. Answers "did the kernel read the file we packed,
  or garbage/zeros?" without a ramdump.
- **Unconditional — no boot-arg** (:1168-1178 comment, :1179 counter): the exec
  that matters at bring-up happens exactly ONCE (`load_init_program` → launchd)
  immediately before a fatal panic; a boot-arg gate is useless if you forget to
  arm it (v58 proved that lesson the hard way with systrace).
- **Capped at 16 lines/boot** (`exec_diag_lines`, :1188-1191) so a userland
  storm of failing execs cannot flood the msgbuf.
- **Sites (7 hunks / 9 prints):** every decline in `exec_mach_imgact` —
  CIGAM :1232, not-Mach-O :1239, filetype :1245 — plus the activator-loop
  ENOEXEC collapse :2342, `exec_check_permissions` failure :2249
  (annotated: ENOEXEC here = zero-length file), first-page `vn_rdwr` failure
  :2267, and the `load_machfile` failure upgrade. The `systrace` TUNABLE
  (:1165-1166) is left untouched (it has no reader in this tree — v58).
- **Ground truth for the read** (K1, pre-registered in
  `/tmp/fleet/k1-execdiag-report.md`): `/sbin/launchd` in root-v8 is 541,600 B
  at extent block 3966, first 16 bytes `cf fa ed fe 0c 00 00 01 00 00 00 00
  02 00 00 00` (valid arm64 MH_EXECUTE, re-hashes to Q5's audited
  `bcb112ed…`). The v59 output matched the zeros/fsize=28MiB prediction
  exactly — the falsifier was NOT triggered; the mockfs-steal theory survived.

Decoder table for future execdiag lines (from K1's report):
`vdata` zeros → pager/md0 or synthetic-file bug (THIS CASE) · garbage → wrong
offset · a different real file → path resolution · perfect launchd bytes with
ENOEXEC → the magic-gate theory itself is wrong · `fsize=0` perms failure → HFS
reporting `va_data_size=0` · no lines at all → exec passed, wall moved.

---

## (d) The fix — K3's patches + K4's build, expected signature

Already applied to the foundation tree
(`research/laguna-foundation/kernel-blazer/xnu-src/xnu-8796.121.2`) after v59;
originals in `research/laguna-usb-fix/patches-bridge-exec/`:

1. **Patch 0001 — `0001-blazer121-mockfs-refuse-non-macho-root-device.patch`**
   (2 KB, sha256 prefix `9b915994d41a92df`). Adds `mockfs_check_macho()`
   (mockfs_vfsops.c:73-102): `buf_meta_bread` block 0, accept only
   MH_MAGIC/_64/CIGAM/_64 + FAT_MAGIC/CIGAM, `buf_markinvalid` + relse so no
   stale DEV_BSIZE block is cached for the next fs; called first thing in
   `mockfs_mountroot` (:118-120, replacing the TODO). On an HFS+ md0 mockfs now
   returns EINVAL and the mountroot scan falls through.
2. **Patch 0002 — `0002-blazer121-port-bridge-laguna_hfs-hfsplus-rootfs.patch`**
   (sha256 prefix `e2554794afe9c570`; ~85k lines, laguna_hfs byte-identical to
   the 141 bridge lineage, 81 files). Ports `bsd/laguna_hfs/` (Apple
   hfs-627.100.6: core/, hfs_encodings/, compat header, IOKit shim), adds the
   `bsd/conf/files` block (now `standard` :197+), `options LAGUNA_HFS`
   (config/MASTER:116), and the 2-line registration
   `extern void laguna_hfs_register(void); laguna_hfs_register();` immediately
   after `vfsinit()` (bsd_init.c:710-711 — the identical placement the 141
   lineage uses, registering with `VFS_TBLCANMOUNTROOT` so
   the vfsconf scan reaches HFS after the static entries: apfs → mockfs → hfs).
3. **K4 build-green lane (in flight):** first build FAILED with
   `bsd/laguna_hfs` compile errors — `hfs_file_is_compressed` implicit,
   `AppleKeyStore/AppleKeyStoreFSServices.h` not found, `sysctlbyname`
   implicit, `cp_handle_vnop` + `CP_READ/WRITE_ACCESS` undeclared,
   `decmpfs_update_attributes` implicit (more expected behind them). Mach
   target: `research/laguna-usb-fix/mach.blazer-r4r7-hfs`. Build:
   `cd research/laguna-foundation/kernel-blazer && ./xnu-build.sh -j8 -k`
   (incremental; RC=2 export-list tail is the documented baseline, not an
   error; doconf absolute-path pitfall: `rm -rf BUILD/obj` after applying
   patches). K1's execdiag stays in the tree and MUST survive the build.
4. **Payload:** `R4_MACHO=research/laguna-usb-fix/mach.blazer-r4r7-hfs
   OUT=$PWD/research/laguna-usb-console/out/next-boot-v60 WITH_ROOT8=1
   research/laguna-usb-console/tools/build-next-boot.sh` (boot.img symlink
   quirk: ROOT-INTEGRATION.md ~line 66; root-v8 sha `77dc3e9f…`).

Expected console signature on the first green boot (from the 141 lineage,
QEMU-proven there — BRIDGE-EXEC-DIFF.md §6):

```
BSD root: md0, major 1, minor 0
bsd_init: vfs_mountroot
hfs: mounted iOSRoot on device md0      ← hfs_vfsops.c:1847 ("hfs: mounted %s on device %s")
bsd_init: calling VFS_ROOT
…
load_init_program: attempting to load /sbin/launchd
(launchd exec SUCCEEDS — no errno 8, no panic)
```

### (e) What proves success on the next boot (v60)

In order of appearance:

1. `hfs: mounted iOSRoot on device md0` in the console tail — the HFS driver
   claimed the root instead of mockfs. Its ABSENCE with a still-successful
   mount means mockfs claimed again ⇒ 0001 not in the mach (check
   `strings …/mach.blazer-r4r7-hfs | grep mockfs_check_macho` ≥ 1).
2. Reader MILESTONES `last=LAUNCHD-EXEC | next-expected=DYLD` — the ladder's
   9th rung goes from "attempted" to "reached" (compact stages record
   LAUNCHD_EXEC(9) with the success flag; today's boots record the attempt
   then PANIC(12)).
3. NO `load_init_program: failed loading … errno N`, NO
   `execdiag:` lines, NO `panic`, NO `MACH Reboot` — boot proceeds past
   `bsd_init: done` into launchd.
4. If launchd exec is ever declined again, the execdiag line must now show
   **launchd's own bytes** — `magic=0xfeedfacf cputype=0x0100000c …
   fsize=541600 vdata[0..15]= cffaedfe 0c000001 00000000 02000000` — which
   would re-point the investigation downstream (dyld/libdispatch territory,
   K5's wall map), never back to mockfs.
5. Freshness discipline (EVIDENCE-MAP §4.3): read only the current-boot
   mirror (`ladder-freshness: mirror-current=yes`) and the attempt's own
   `reader-full.txt`; the panic channel staying `message=None` is expected —
   success on this boot means the panic channel never fires at all.
