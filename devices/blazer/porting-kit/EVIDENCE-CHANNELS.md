# Evidence channels on laguna — what survives XNU death

Measured on the Pixel 10 Pro (`blazer`) ramdumps retrieved through the fastboot
`kernel.log` staging path, 2026-10-26. The on-device delivery chain is proven —
the probe runs, loads **14 kexec segments**, `kexec_load` returns **result=0**,
and XNU receives the handoff (magic **`0x30484e58`** = ASCII `XNH0` at
pmsg+`0x120`) — and XNU still dies **before `arm_vm_init`**, silently, on every
live console. This page is the map of which memory channels carry evidence
across that death, which ones get clobbered and by whom, and the operating
rules that follow. If you port to another Tensor device, measure your own map
before trusting placements — the method is at the bottom.

## The aperture

| Thing | Value |
| --- | --- |
| ramoops reserved window | PA `0x95200000`, 4 MiB (`no-map` in `lga.dtsi`) |
| persistent_ram console | PA `0x95200000`, 2 MiB (`console-ramoops-0`) |
| pmsg zone | PA `0x95400000`, 2 MiB |
| `kernel.log` fastboot staging dump | the raw aperture from PA `0x95200000`; **file offset = PA − `0x95200000`** |
| pmsg in that file | offset `0x200000` |

Decode the raw aperture, not Android's filtered `/sys/fs/pstore` text files —
pstore applies its own truncation and the fixed headers are not in it.

## The measured pmsg collision map

What actually occupies pmsg (PA `0x95400000`) in retrieved dumps:

| pmsg offset | Owner | Evidence in the dump |
| --- | --- | --- |
| `+0x000..0x900` | **ABL/TEE fastboot re-entry** | `METASEC`/`TENS` strings; the low window is rewritten every time the boot chain comes back up through fastboot |
| `+0x000..0xFFF` | **Linux-kexec probe handoff page** | magic `0x30484e58` (`XNH0`, little-endian) at `+0x120`; entry marker/stage record around `+0x100` |
| `+0x1080..0x5C00` | **probe log ring** | one record per `0x400` stride |
| `+0x40000..0x140040` | **R4 XNU-MSGBUF v1 mirror** (64-byte header + 1 MiB byte ring) | **survives** — decodable by the published contract (`mirror-panic-capture/XNU-MSGBUF-v1.md`), header at PA `0x95440000` = file offset `0x240000` |

Published residents that share the same 2 MiB window (all validated
mechanically by the reader before being believed): `M1` marker at `+0x1000`,
boot counter at `+0x1040`, the compact 12×u32 stage trail at
`+0x1050..0x107F`, the DIAG counter at `+0x1080`, the CRC/DONE LGLW slots at
`+0x1100`/`+0x1180`, DIAG records from `+0x2000`, and the PAN1 panic mirror at
`+0x30000`. One measured caveat: the probe log ring's span
(`+0x1080..0x5C00`) overlaps the `+0x1080` DIAG counter and the LGLW slot
window, so a byte range there is only evidence if the record's CRC and `DONE`
commit validate — and committed LGLW is authoritative if channels ever
disagree (the STAMPS.md rule the reader implements).

## Lessons — the operating rules

1. **Read the ramdump BEFORE any reboot.** ABL/TEE fastboot re-entry clobbers
   pmsg+`0x000..0x900` the moment the chain comes back up; every reboot risks
   the low windows, and the ladder can erase the pstore console zone on
   retries. Retrieve first, reboot after. Anything you left below `+0x1000`
   should be assumed gone.
2. **A header alone is not runtime evidence.** Initialized XNU-MSGBUF headers
   also occur in static Mach-O data. Ring text, a fixed pmsg marker, or a
   panic record is required before claiming XNU progress — the reader enforces
   this so you don't have to remember it.
3. **Prefer the live USB EP0 console: it is the only instrument that survives
   death.** VID:PID `1209:0001`, product string `Tegu USB diagnostic`, log
   read `0xc0/0x5a` with offset32 encoded as `wValue | (wIndex << 16)`,
   `wLength ≤ 512`, append-only 1 MiB log. It serves bytes from shim start to
   reset — including everything the post-mortem channels can never show you
   (which stage was running *when* it died).
4. **One EP0 client at a time.** Stop the logger before reboot, stamp, or
   framebuffer requests; the protocol stalls rather than races.
5. **Post-death order of operations:** USB console log (if a reader was
   attached live) → fastboot `kernel.log` staging retrieval → decode the raw
   aperture with `ramdump-reader/read-ramdump.py DUMP --dump-base 0x95200000
   --logs` → the mirror header is at file offset `0x240000`, its verdict line
   is mechanical, and the JSON includes LGLW/PAN1/mirror cross-checks.

## The kit this page belongs to

- [`ramdump-reader/`](ramdump-reader/) — standalone reader + `./selftest`
  (57/57 host-only checks). Give it any raw dump; it finds the zones by
  physical layout or signature and prints one verdict line.
- [`mirror-panic-capture/`](mirror-panic-capture/) — the always-on XNU msgbuf
  mirror producer patch (`0001-laguna-0129`, Mach-O hash `cae95ab12…`) and the
  XNU-MSGBUF v1 wire contract.
- [`usb-console/`](usb-console/) — the EP0 console: `PROTOCOL.md` (exact
  request ABI), `BRINGUP.md` (live-FDT resources, CPM, warm USB2 sequence),
  `HARDWARE-ENABLE.md`, `RUNBOOK.md` (the approved temporary-boot procedure),
  and `build-next-boot.sh` (host-only candidate builder).

## Reproducing the collision map on your device

The map above was derived by classifying every candidate window in a retrieved
4 MiB `kernel.log` by its `0xFF` fraction, `0x00` fraction, printable
fraction, and marker strings — foreign writers announce themselves
(`TENS`/`METASEC`/`TEE` for the TEE path, `XNH0`/`kexec`/`seg[` for the probe),
clear windows are uniform-`FF` or uniform-zero, and our own windows count as
"surviving" only when their structure validates (magic, CRC/DONE, geometry).
A dozen lines of Python against your own dump reproduce it; start from the
window list in this table and your own `read-ramdump.py --json` output.

Contact: Discord **`@andersmadsen`** — if you have a laguna/Tensor dump, the
collision map for your exact boot chain is the first thing worth comparing.
