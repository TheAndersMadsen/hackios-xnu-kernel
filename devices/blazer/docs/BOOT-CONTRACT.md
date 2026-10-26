# blazer boot, entry, ADT and RAM-disk contract

This is the contract for the **new HakiOS** kernel on this branch, not for an old
`laguna141`/0128 payload. No Qualcomm `soc_*`, MPSS/IPA or Adreno argument is
part of it.

## 1. Entry into XNU

The kernel is an arm64 Mach-O built as `mach.development.bcm2837` (the generic
HakiOS target name; it does not mean Qualcomm or Raspberry Pi hardware).

There are two valid entries from the same marker-patched file:

| Path | Incoming state | `boot_args` delivery |
| --- | --- | --- |
| Direct loader/raw shim | MMU and caches off, one CPU, `x0 = boot_args PA` | Already in x0; the marker leaves it unchanged when no `XNH0` record exists |
| Linux raw `kexec_load` | MMU and caches off, secondaries offline, `x0 = 0`, x1..x3 zero | The entry stub reads the PA from the `XNH0` record in pmsg |

Use `tools/patch-xnu-marker.py` after linking:

```sh
K=xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837
mkdir -p out
python3 devices/blazer/tools/patch-xnu-marker.py \
  --in "$K" --out out/mach.blazer.marked.macho \
  --marker 0x95400100 --handoff 0x95400120 --stage 1
python3 devices/blazer/tools/patch-xnu-marker.py \
  --verify out/mach.blazer.marked.macho --in "$K" \
  --marker 0x95400100 --handoff 0x95400120 --stage 1
```

The tool changes only `LC_UNIXTHREAD`'s entry PC and a code cave. Its branch is
**base-independent ADR + link-time-VA delta**, so it works at any kexec window
base. `--verify` rebuilds the expected stub and checks that no other bytes
changed. Do not run it on the unmodified POCO prebuilt and call the result a
blazer kernel.

### Marker and pmsg layout

Defaults below use the live FDT's pmsg PA `0x95400000`; override the tool if a
new FDT changes it. The pmsg base and size are also emitted by
`tools/tensor-args.py`.

| PA / offset | Contract |
| --- | --- |
| pmsg+`0x100` (`0x95400100`) | `0x5A5A5A5A`: the entry stub executed |
| pmsg+`0x104` | stage word |
| pmsg+`0x108` | incoming x0 (zero for raw kexec, boot_args PA for direct entry) |
| pmsg+`0x110` | final x0 after optional handoff |
| pmsg+`0x118` | ADR-derived stub PA |
| pmsg+`0x120` | `0x30484E58` = `XNH0` handoff magic |
| pmsg+`0x128` | boot_args PA supplied by the Linux first stage |
| pmsg+`0x1000` | **MARKER-SCRATCH** page; preserve the proven stage's scratch/handoff convention |
| pmsg+`0x30000` | CRC'd `XNU-PANIC v1` mirror from `hakios.blazer_panicdump` |

The first stage must preserve the page's `persistent_ram` header and unrelated
bytes. Only the diagnostic span `0x100..0x130` is cleared for a new run. The
marker tool writes entry evidence; **it does not write the `XNH0` record** —
that is the first stage's job.

`boot_args` is revision/version 2 and 736 bytes. `video.v_baseAddr` is at byte
offset 40. Populate video only from a validated linear CPU-PA framebuffer
record (M5's display-handoff validator), never from a guessed ABL address or a
DC9x00 readback.

## 2. Required ADT shape

The loader/first stage creates a flattened Apple device tree. At minimum:

- Root `device-tree` with compatible `google,lga-blazer`. This selects
  `BlazerPlatformExpert` (score 10000); `GenericARMPE` remains the fallback.
- `/chosen` with `dram-base`, `dram-size`, 64-byte `random-seed`,
  `display-scale=3`, `psci-method`, and the identity/security properties the
  userland image needs. `/chosen/memory-map` carries RAMDisk properties.
- `/defaults` (must exist), `/options` (NVRAM), and the full `/product` subtree
  required by MobileGestalt/CoreUI.
- `/cpus` with exactly one boot CPU marked running and valid `reg`, state and
  frequency properties. More CPUs are an SMP follow-up, not a first-boot need.
- `/arm-io` with a **nonzero** parent address in `ranges`, and `/arm-io/gic`
  with the blazar GICD/GICR windows. Do not copy the QEMU GIC tuple to hardware.
- `/chosen/laguna-display-handoff = {record_pa:u64, 128:u64}` points to M5's
  versioned, CRC'd 128-byte display record. `hakios_blazer_display_init()`
  validates it with `laguna_display_boot_video()` before changing video; M2
  never infers or probes a display PA. `/chosen/laguna-display-forbidden` may
  hold additional `{base,size}` pairs and is required for every FDT no-map or
  dynamic exclusion not already covered by the kernel window/record/ramoops.
  The final framebuffer PA must be outside every forbidden range and have a
  validated DPU IOVA contract. A present-but-invalid record clears video and
  stays headless; an absent record preserves loader GOP/ramfb video.
  `cache_kind=CLEAN_ON_PRESENT` cleans the completed MiniIOMFB frame to PoC;
  write-combine takes the presenter barrier path.
- `/pram` when a real persistent panic region exists. If it is absent and
  `blazer_panicdump=1`, the kernel uses its static 512 KiB fallback; a real
  `pram` always wins.

`MiniIOMobileFramebufferStart` creates `/arm-io/disp0` with
`device_type=display-subsystem` and compatible from `fb_compatible` (the port's
value is `google,lga-framebuffer`). Mini services start directly from the
inherited `GenericARMPE::start()` after the registry is live; no second set of
IOResources personalities is needed.

## 3. RAMDisk → md0 (byte-for-byte)

`/chosen/memory-map` contains **properties**, not child nodes:

```text
RAMDisk  = <physical_pa:u64 LE, size_bytes:u64 LE>   -> /dev/md0 (root)
RAMDisk1 = <physical_pa:u64 LE, size_bytes:u64 LE>   -> /dev/md1 (auxiliary)
```

The invariants are:

1. PA and rounded size are 16 KiB aligned. XNU passes `pa >> 12` and
   `size >> 12` to `mdevadd`; its memdev units are 4 KiB.
2. Each span is inside `[boot_args.physBase, physBase + memSize)` and below
   `topOfKernelData` (`ml_static_ptovirt` is not a general map for arbitrary PA).
3. Spans do not overlap Mach-O segments, each other, the ADT, `boot_args`, the
   kexec handoff/scratch pages, or any FDT `no-map`/reserved carve-out.
4. `RAMDisk` is the root; `RAMDisk1` is never implicitly selected as root.
   `rd=md0` is emitted only when `RAMDisk` exists. `rd=md0` without the property
   is a configuration error, not a fallback.
5. Image bytes at handoff are the original raw volume bytes. Host verification
   compares at least head and tail before any mount. `rootrw=1` can replay an
   HFS journal after mount, so post-mount differences must be labelled as such.

`rd=ufsroot0` is a separate explicit request. It fails closed if the live UFS
handoff or named GPT partition is unavailable; it never silently falls back to
an unrelated partition. `md0` remains the normal QEMU/bootstrap root.

## 4. Boot-argument namespaces

### XNU PE boot-args (what the new `/xni` should put in `boot_args.commandLine`)

Bootstrap profile:

```text
debug=0x8 cpus=1 rd=md0 rootrw=1 ignition_level=1 launchd_unsecure_cache=1 fb_compatible=google,lga-framebuffer
```

Optional pieces are enabled individually:

| Argument | Meaning / default |
| --- | --- |
| `blazer_ufs=1` | Safe UFS state-register probe only; requires `blazer_ufs_live=1`; default 0 |
| `blazer_ufs=2` | Take over a live legacy host and publish read-only `ufsroot0`; requires live + root name |
| `blazer_ufs_live=1` | First-stage attestation that Google's UFS/PHY/CPM setup completed and remains live; absent means no UFS MMIO |
| `blazer_ufs_base=0x...` | HC PA, default `0x3c400000` |
| `blazer_ufs_root=NAME` | Required GPT partition name for mode 2; there is no `userdata` default |
| `blazer_ufs_lun=N` | UFS LUN, default 0 |
| `blazer_cpm=1` | CPM client map + read-only probe; default 0 |
| `blazer_cpm_tx=1` | Separate authorization for queue init/transmission; default 0 |
| `blazer_cpm_global=`, `blazer_cpm_req=`, `blazer_cpm_resp=` | DT-derived CPM addresses; defaults `0x05250000`, `0x05260000`, `0x05261000` |
| `blazer_panicdump=1` | Map ramoops and arm guarded console+panic-mirror capture; default 0 |
| `blazer_panictest=N` | Optional forced panic after N seconds; default 0, test only |
| `blazer_ramoops_base=`, `blazer_ramoops_size=` | DT ramoops geometry |
| `blazer_console_size=`, `blazer_pmsg_base=`, `blazer_pmsg_size=` | Console/pmsg geometry, validated before mapping |
| `fb_compatible=google,lga-framebuffer` | MiniIOMFB display nub compatible; no hardware access |
| `gl=1 glwait=1 glenv=OSMESA_SOFTWARE=1` | M5 softpipe render profile; use a `GL=1` userland image |
| `machine_name=1`, `fbwidth=`, `fbheight=`, `fbscale=`, `epoch=` | Existing generic HakiOS platform/display/time controls |

`io.display.probe=0` and `io.display.splash=0` may be present for M5's
handoff/profile discipline, but M2 implements no DC9x00 register probe or
splash driver to enable. **Do not set `gpu=1`; that is Adreno-only.**

### Linux first-stage `/xni` arguments (not XNU PE boot-args)

The existing kexec stage owns `xnu.handoff=`, `xnu.phys_base=`,
`xnu.window_size=`, `xnu.ramdisk_pa=`, `xnu.ramdisk_size=`,
`xnu.ramdisk1_pa=`, `xnu.ramdisk1_size=`, `xnu.ramdisk_max=`, watchdog and
first-stage transport knobs. Do not put those in `devices/blazer/soc.args` and
do not confuse them with PE arguments. `blazer_ufs_live=1` is set only after the
first stage has actually completed the live handoff.

## 5. Kernel-live and return ABI

M2 leaves the new foundation's ABI unchanged:

- `loader_live` remains `XNULIVE!` / `XNULIVE2`, with heartbeat, log pointers,
  recording pointers and stop word at the linker-provided addresses.
- The loader patches the `XNULDRST` slot with `{return_stub_pa, checkpoint,
  PA-minus-VA}` when a compatible resident return path exists.
- If `loader_stub_slot[1] == 0`, `loader_jump()` returns without jumping. This
  is the safe default for a raw Linux/kexec handoff with no resident UEFI
  return stub; do not point the slot at the old shim's code.
- Panic capture runs before any optional return attempt. There is no new M2
  replacement `/xni` fingerprint yet; it will be published with the linked
  Mach-O hash in `research/laguna-blazer/PORT.md`.
