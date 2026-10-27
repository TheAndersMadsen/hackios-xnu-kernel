#!/usr/bin/env python3
"""Decode the teguload shim TSHIMST1 early-evidence stamps from a ramdump.

The v3.2 shim stamps its stage history at physical 0x95430000 (the S1-claimed
pmsg window +0x30000). The writes are plain DRAM stores, so the trail survives
the PSCI reset bounce, the TEE clobber (+0x000..0x900) and XNU itself. Read it
from the standard 4 MiB kernel.log aperture (dump-base 0x95200000):

    python3 tools/decode-shim-stamps.py evidence/kernel.log

    layout: 64-byte header (magic "TSHIMST1", nonce, count, seq, last stage,
    flags, cntfrq, t0, tlast, dropped, reason) followed by up to 8 x 16-byte
    slots {seq u32, stage u16, flags u16, arg u64}.

Standalone: imports nothing from research/laguna-ramdump (R5 owns that tree).
Self-test:  python3 tools/decode-shim-stamps.py --selftest
"""
import argparse
import json
import struct
import sys

SHIM_STAMP_PA = 0x95430000  # pmsg+0x30000, S1 claim in /tmp/fleet/COORDINATION.md
DEFAULT_DUMP_BASE = 0x95200000
MAGIC = b"TSHIMST2"          # v2 ABI (128 slots, 24 B slots with timestamps)
MAGIC_V1 = b"TSHIMST1"       # v1 ABI (8 slots, 16 B slots, no timestamps)
MAX_SLOTS = 128

STAGES = {
    0x0001: "entry",
    0x0002: "cpu-state(mmu-off evidence)",
    0x0003: "wdt-survey",
    0x0004: "fdt-parse",
    0x0005: "console-init",
    0x0006: "wdt-pat",
    0x0007: "usb-preflight",
    0x0008: "dma-admit",
    0x0009: "cpm-ack-wait",
    0x000A: "cpm-ack-done",
    0x000B: "cpm-send-wait",
    0x000C: "phy-resume",
    0x000D: "dwc3-halt-wait",
    0x000E: "dwc3-reset-wait",
    0x000F: "ep0-config",
    0x0010: "ep0-ready",
    0x0011: "usb-enum-wait",
    0x0012: "payload-admit",
    0x0013: "pre-xnu-handoff",
    0x0014: "wdt-quiesce-late",
    0x0015: "usb-pwr-status",
    0x0016: "usb-gsnpsid",
    0x0017: "usb-gctl",
    0x0018: "usb-evt-hi",
    0x0019: "usb-refused-bad-id",
    0x001A: "usb-refused-prtcap",
    0x001B: "usb-refused-stale-evt",
    0x001C: "usb-refused-not-d0",
    0x001D: "console-paint-done",
    0x001E: "cpm-first",
    0x001F: "cpm-geo",
    0x0021: "cpm-clk",
    0x0022: "cpm-rst",
    0x0023: "usb-warm",
    0x0024: "usb-dctl",
    0x0025: "mba-live",
    0x0026: "mba-send",
    0x0027: "mba-ack",
    0x0028: "el-drop",
    0x0029: "mba-resp0",
    0x002A: "cpm-geo2",
    0x002B: "lpcm-req0",
    0x002C: "lpcm-req1",
    0x002D: "lpcm-reply0",
    0x002E: "lpcm-reply1",
}

FLAGS = [(0x0001, "ENTER"), (0x0002, "DONE"), (0x0004, "TIMEOUT"),
         (0x0008, "FAULT"), (0x0010, "REFUSED")]

REASONS = {
    0: None,
    1: "timeout",
    2: "fault",
    3: "usb-fatal",
    4: "refused",
    5: "handed-off-to-xnu",
    6: "cpm-never-came-up",
    7: "in-progress",
}

HDR = "<8sQIIIIQQQII"  # magic, nonce, count, seq, last_stage, flags,
                       # freq, t0, tlast, dropped, reason
HDR_SIZE = 64
SLOT_V1 = "<IHHQ"   # seq, stage, flags, arg (v1: no per-slot timestamp)
SLOT_V2 = "<IHHQQ"  # seq, stage, flags, t, arg
SLOT_SIZE_V1 = 16
SLOT_SIZE_V2 = 24
assert struct.calcsize(HDR) == HDR_SIZE, "TSHIMST header layout must be 64 B"
assert struct.calcsize(SLOT_V1) == SLOT_SIZE_V1, "v1 slot layout must be 16 B"
assert struct.calcsize(SLOT_V2) == SLOT_SIZE_V2, "v2 slot layout must be 24 B"


class StampError(Exception):
    pass


def decode_header(buf, off):
    if off < 0 or off + HDR_SIZE > len(buf):
        raise StampError("header offset outside the dump")
    fields = struct.unpack_from(HDR, buf, off)
    keys = ("magic", "nonce", "count", "seq", "last_stage", "flags",
            "freq", "t0", "tlast", "dropped", "reason")
    header = dict(zip(keys, fields))
    if header["magic"] == MAGIC_V1:
        header["abi"] = 1
    elif header["magic"] == MAGIC:
        header["abi"] = 2
    else:
        raise StampError("no TSHIMST1/TSHIMST2 magic at PA 0x%x (file offset 0x%x)"
                         % (SHIM_STAMP_PA, off))
    return header


def decode_slots(buf, off, header):
    abi = header["abi"]
    count = min(header["count"], 8 if abi == 1 else MAX_SLOTS)
    slot_fmt = SLOT_V1 if abi == 1 else SLOT_V2
    slot_size = SLOT_SIZE_V1 if abi == 1 else SLOT_SIZE_V2
    slots = []
    for i in range(count):
        base = off + HDR_SIZE + i * slot_size
        if base + slot_size > len(buf):
            raise StampError("slot %d outside the dump" % i)
        seq, stage, flags = struct.unpack_from("<IHH", buf, base)
        arg, t = None, None
        if abi == 1:
            (arg,) = struct.unpack_from("<Q", buf, base + 8)
        else:
            t, arg = struct.unpack_from("<QQ", buf, base + 8)
        if seq == 0:
            break  # a never-written slot is invalid, not a stage 0 stamp
        slots.append({"i": i, "seq": seq, "stage": stage, "flags": flags,
                      "arg": arg, "t": t})
    return slots


def flag_names(flags):
    return "|".join(name for bit, name in FLAGS if flags & bit) or "-"


def stage_name(stage):
    return STAGES.get(stage, "stage-0x%04x" % stage)


def delta_ms(header, slot):
    if not header["freq"] or slot.get("t") is None:
        return None
    return (slot["t"] - header["t0"]) * 1000.0 / header["freq"]


def decode(buf, dump_base=DEFAULT_DUMP_BASE):
    off = SHIM_STAMP_PA - dump_base
    if off < 0:
        raise StampError("shim stamp PA 0x%x is below dump-base 0x%x"
                         % (SHIM_STAMP_PA, dump_base))
    header = decode_header(buf, off)
    slots = decode_slots(buf, off, header)
    return {
        "pa": SHIM_STAMP_PA,
        "offset": off,
        "abi": header["abi"],
        "nonce": "0x%016x" % header["nonce"],
        "count": header["count"],
        "seq": header["seq"],
        "last_stage": stage_name(header["last_stage"]),
        "last_flags": flag_names(header["flags"]),
        "freq": header["freq"],
        "dropped": header["dropped"],
        "reason": REASONS.get(header["reason"], "reason-%d" % header["reason"]),
        "slots": [
            {
                "i": s["i"],
                "seq": s["seq"],
                "stage": stage_name(s["stage"]),
                "stage_id": "0x%04x" % s["stage"],
                "flags": flag_names(s["flags"]),
                "arg": "0x%x" % s["arg"],
                "ms_since_entry": delta_ms(header, s),
            }
            for s in slots
        ],
    }


def render(decoded):
    lines = []
    lines.append("TSHIMST%d shim stamp history @ PA 0x%x (file offset 0x%x)"
                 % (decoded["abi"], decoded["pa"], decoded["offset"]))
    lines.append("  nonce=%s count=%d/%d seq=%d dropped=%d reason=%s"
                 % (decoded["nonce"], decoded["count"],
                    8 if decoded["abi"] == 1 else MAX_SLOTS, decoded["seq"],
                    decoded["dropped"], decoded["reason"] or "none"))
    lines.append("  last stage: %s [%s]"
                 % (decoded["last_stage"], decoded["last_flags"]))
    if not decoded["slots"]:
        lines.append("  (no slots decoded)")
        return "\n".join(lines)
    lines.append("  %-3s %-4s %-26s %-14s %-18s %s"
                 % ("#", "seq", "stage", "flags", "arg", "ms since entry"))
    for s in decoded["slots"]:
        ms = s["ms_since_entry"]
        lines.append("  %-3d %-4d %-26s %-14s %-18s %s"
                     % (s["i"], s["seq"], s["stage"], s["flags"], s["arg"],
                        "%.1f" % ms if ms is not None else "-"))
    last = decoded["slots"][-1]
    lines.append("  HANG EVIDENCE: the shim's last recorded stage is #%d %s [%s]"
                 % (last["i"], last["stage"], last["flags"]))
    return "\n".join(lines)


def selftest():
    """Build a synthetic v2 boot trail, decode it, and check interpretation.
    Exercises: v2 24 B slots with per-slot timestamps, saturation-free normal
    flow, timeout terminal stamp, torn-count under-read, v1 backward compat,
    and a foreign magic."""
    blob = bytearray(4 * 1024 * 1024)
    base = SHIM_STAMP_PA - DEFAULT_DUMP_BASE
    freq = 38400000
    stamps = [
        (1, 0x0001, 0x0001, 0),           # entry (ENTER)
        (2, 0x0002, 0x0002, 0x80d01998),  # cpu-state EL2 MMU off (DONE)
        (3, 0x0003, 0x0002, 0x3),         # wdt survey: both id_ok (DONE)
        (4, 0x001d, 0x0002, 0xf0000000),  # console paint done
        (5, 0x0015, 0x0002, 0x0),         # usb pwr status D0
        (6, 0x0016, 0x0002, 0x33313130),  # GSNPSID value evidence
        (7, 0x0017, 0x0002, 0x1020),      # GCTL value evidence
        (8, 0x0018, 0x0002, 0x0),         # EVT_HI hang-check ok
        (9, 0x0009, 0x0001, 3000),        # cpm-ack-wait ENTER ... timeout
    ]
    t0 = 1_000_000
    struct.pack_into("<8sQIIIIQQQII", blob, base, MAGIC, 0x1234deadbeef,
                     len(stamps), len(stamps), 0x0009, 0x0005, freq, t0,
                     t0 + freq, 0, 1)
    for i, (seq, stage, flags, arg) in enumerate(stamps):
        t = t0 + freq * (i + 1) // (len(stamps) + 1)
        struct.pack_into("<IHHQQ", blob, base + HDR_SIZE + i * SLOT_SIZE_V2,
                         seq, stage, flags, t, arg)
    decoded = decode(bytes(blob))
    got = render(decoded)
    assert decoded["abi"] == 2
    assert decoded["count"] == 9, decoded["count"]
    assert decoded["reason"] == "timeout", decoded["reason"]
    assert decoded["slots"][-1]["stage"] == "cpm-ack-wait"
    assert decoded["slots"][-1]["flags"] == "ENTER"
    assert decoded["slots"][5]["stage"] == "usb-gsnpsid"
    assert decoded["slots"][5]["arg"] == "0x33313130"
    assert decoded["slots"][1]["stage"] == "cpu-state(mmu-off evidence)"
    # Per-slot timestamps: every slot now carries its own elapsed ms.
    ms = [s["ms_since_entry"] for s in decoded["slots"]]
    assert all(m is not None for m in ms), ms
    assert ms == sorted(ms), "timestamps must be monotonic"
    assert abs(ms[-1] - 900.0) < 0.1, ms[-1]
    assert "HANG EVIDENCE" in got
    # Torn count: header count under-states a fully written slot 8.
    struct.pack_into("<I", blob, base + 0x10, 8)
    decoded2 = decode(bytes(blob))
    assert len(decoded2["slots"]) == 8
    assert decoded2["slots"][-1]["seq"] == 8
    # v1 backward compat: an 8-slot 16 B "TSHIMST1" dump still decodes.
    v1 = bytearray(4 * 1024 * 1024)
    v1_stamps = [
        (1, 0x0001, 0x0001, 0),
        (2, 0x0007, 0x0001, 0),
    ]
    struct.pack_into("<8sQIIIIQQQII", v1, base, MAGIC_V1, 0x6dbf3154abd454ef,
                     len(v1_stamps), 2, 0x0007, 0x0001, freq, t0, t0 + 1017,
                     16, 3)
    for i, (seq, stage, flags, arg) in enumerate(v1_stamps):
        struct.pack_into("<IHHQ", v1, base + HDR_SIZE + i * SLOT_SIZE_V1,
                         seq, stage, flags, arg)
    decoded1 = decode(bytes(v1))
    assert decoded1["abi"] == 1
    assert decoded1["reason"] == "usb-fatal"
    assert decoded1["dropped"] == 16
    assert decoded1["slots"][-1]["stage"] == "usb-preflight"
    assert decoded1["slots"][-1]["ms_since_entry"] is None  # v1: no slot time
    # Foreign magic: refuse.
    blob[base] = 0x00
    try:
        decode(bytes(blob))
    except StampError:
        pass
    else:
        raise AssertionError("decoder accepted a foreign header")
    print("PASS decode-shim-stamps selftest: 12 checks")
    print(got)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump", nargs="?", help="4 MiB kernel.log ramdump aperture")
    ap.add_argument("--dump-base", default="0x%x" % DEFAULT_DUMP_BASE)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    if not args.dump:
        ap.error("a kernel.log dump is required (or --selftest)")
    with open(args.dump, "rb") as f:
        blob = f.read()
    try:
        decoded = decode(blob, int(args.dump_base, 0))
    except StampError as e:
        print("FAIL %s" % e, file=sys.stderr)
        print("The shim either never initialized the channel (died before "
              "start.S entry) or this dump predates the v3.2 shim.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(decoded, indent=2))
    else:
        print(render(decoded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
