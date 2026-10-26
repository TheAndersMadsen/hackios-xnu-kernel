#!/usr/bin/env python3
"""Read a raw Laguna DRAM dump without touching a device.

Finds the 0x95200000 ramoops console/pmsg zones by physical layout or by
signature, linearizes Linux persistent_ram records, decodes the 0123/0124
XNU-PANIC channels, reconstructs R4's always-on XNU-MSGBUF v1 mirror at
pmsg+0x40000, decodes compact and CRC/DONE LGLW stage breadcrumbs, locates
XNU's LP64 msgbuf as a fallback, harvests DISPLAY-PROBE/KEXEC-XNU evidence,
unslides and symbolicates frames with the matching Mach-O LC_SYMTAB, and emits
one mechanical VERDICT line with stage and dyld progress.

The default symbol source is the exact 0128 payload (cd6ca158..., 52,004
symbols).  A msgbuf header alone is NOT runtime evidence: initialized headers
also occur in static Mach-O data.  Ring text, a fixed pmsg marker, or a panic
record is required before reporting XNU progress.

Usage:
  read-ramdump.py DRAM.bin [--mach out/xnu-laguna141-paniccap-0128.macho]
  read-ramdump.py DRAM.bin --dump-base 0x80000000 --logs --tail 32768
  read-ramdump.py DRAM.bin --json
  read-ramdump.py --selftest

Address options:
  --ramoops-pa PA       Physical console base (default 0x95200000; pmsg +2 MiB)
  --dump-base PA        PA represented by file offset zero (alias --phys-base)
  --kernel-text-base VA Override the panic record's runtime __TEXT base
  --slide N             Override the KASLR slide for raw lr: lists
  --kernel-map V:P      Optional direct-map VA base V and physical base P; used
                        for msg_bufc when Mach symbol-relative mapping is absent

Exit status is 0 after a successful host-side analysis (including NOTHING),
1 for a failed selftest, and 2 for an input/usage error.  NOTHING means "no
surviving evidence", not proof of where a silent death occurred.
"""

import argparse
import importlib.util
import json
import mmap
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MACH0128_NAME = "xnu-laguna141-paniccap-0128.macho"


def _find_mach0128():
    """Locate the exact 0128 payload: env override first, then out/ near us.

    The canonical fleet workspace keeps it in <workspace>/out/; the portable
    kit may sit several directories below the workspace, so walk up a bounded
    number of parents looking for an out/ that holds it.  A fresh checkout
    without the payload still analyzes dumps (symbolication just degrades);
    `LAGUNA_READER_ROOT=<workspace>` or `--mach <path>` restores full fidelity.
    """
    env = os.environ.get("LAGUNA_READER_ROOT")
    if env:
        return Path(env) / "out" / MACH0128_NAME
    here = HERE
    for _ in range(6):
        cand = here / "out" / MACH0128_NAME
        if cand.exists():
            return cand
        if here == here.parent:
            break
        here = here.parent
    return ROOT / "out" / MACH0128_NAME


MACH0128 = _find_mach0128()
MACH0128_SHA256 = "cd6ca158ca4804be2e19d486d94d39e0b5b6c78af7e646152dee43c951d4e094"


def _companion(canonical, kit_name):
    """Fleet research-tree path when it exists, else the kit copy beside us.

    Canonical layout is the laguna workspace (research/laguna-*); the portable
    porting kit ships both companions next to this reader, so the reader works
    from this directory alone.
    """
    cand = ROOT / canonical
    return cand if cand.exists() else HERE / kit_name


DECODER_PATH = _companion(
    Path("research") / "laguna-panic-capture" / "tools" / "decode-device-panic.py",
    "decode-device-panic.py")
FIXTURE_GENERATOR = _companion(
    Path("research") / "laguna-ramdump" / "producer" / "tests" / "make-r2-fixtures.py",
    "make-r2-fixtures.py")


def _load_decoder():
    spec = importlib.util.spec_from_file_location("decode_device_panic", DECODER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {DECODER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ddp = _load_decoder()

RAMOOPS_PA = 0x95200000
CONSOLE_SIZE = 0x200000
PMSG_SIZE = 0x200000
PMSG_OFF = CONSOLE_SIZE
PRZ_HDR = 12
PRZ_SIG = 0x43474244
PRZ_SIG_BYTES = struct.pack("<I", PRZ_SIG)
MARKER_MAGIC = 0x5A5A5A5A
MARKER_BYTES = struct.pack("<I", MARKER_MAGIC)
HANDOFF_MAGIC = 0x30484E58
MARKER_OFF = 0x100
SCRATCH_OFF = 0x1000
DIAG_COUNT_OFF = 0x1080
DIAG_RING_OFF = 0x2000
DIAG_REC_SIZE = 0x400
DIAG_REC_COUNT = 64
MIRROR_OFF = 0x30000          # 0123/0124 PAN1 panic mirror
MSGBUF_MIRROR_OFF = 0x40000   # R4 always-on XNU msgbuf mirror
MSGBUF_MIRROR_MAGIC = b"XNU-MSGBUF v1"
MSGBUF_MIRROR_HEADER = struct.Struct("<16sIIQQIIQII")
MSGBUF_MIRROR_RING_MAX = 0x100000
STAGE_COMPACT_OFF = 0x1050
STAGE_COMPACT_BYTES = 0x30
LGLW_OFF = 0x1100
LGLW_SLOT_SIZE = 0x80
LGLW_SLOT_COUNT = 2
LGLW_MAGIC = 0x574C474C
LGLW_COMMIT = 0x454E4F44
LGLW_SEQ_MAX = 0xFFFFFFFD
STAGE_NAMES = {
    1: "ABL_SEEN", 2: "KERNEL_UP", 3: "PROBE_START", 4: "ROOT_STAGED",
    5: "KEXEC_DONE", 6: "XNU_ENTRY", 7: "XNU_INIT", 8: "ROOT_MOUNTED",
    9: "LAUNCHD_EXEC", 10: "DYLD_STAGE", 11: "DYLD_SHARED_REGION", 12: "PANIC",
}
PRODUCER_NAMES = {
    1: "ABL", 2: "LINUX_PROBE", 3: "KEXEC_LOADER", 4: "XNU_STUB",
    5: "XNU_KERNEL", 6: "XNU_PANIC",
}
MSG_MAGIC = 0x063061
MSG_MAGIC_BYTES = struct.pack("<I", MSG_MAGIC)
MSG_MAX = 0x100000
MSG_HEADER_SIZE = 24
DEFAULT_LINK_BASE = ddp.KERNEL_LINK_BASE

PROBE_RX = re.compile(r"DISPLAY-PROBE\s*:|KEXEC-XNU\s*:", re.I)
BUILD_RX = re.compile(r"\bdp-[0-9a-fA-F]{8,64}\b")
DARWIN_RX = re.compile(r"Darwin Kernel Version\b")
BSD_ROOT_RX = re.compile(r"\bBSD root:\s*([^\s,]+)", re.I)
ROOT_MOUNT_RX = re.compile(
    r"\bhfs:\s+mounted\s+\S+(?:\s+on\s+device\b|\b)|"
    r"\bapfs:\s+(?:volume\s+\d+\s+\"[^\"]+\"\s+of\s+container|.*\bmounted\b)",
    re.I,
)
LAUNCHD_ATTEMPT_RX = re.compile(
    r"load_init_program:\s+attempting\s+(?:to\s+load\s+)?/sbin/launchd\b", re.I
)
LAUNCHD_EXEC_RES = [
    re.compile(r"(?m)^\s*(?:\[[^]]+\]:\s*)?exec\s+(?:\S*/)?launchd(?:\.[A-Za-z0-9_-]+)?\s*$", re.I),
    re.compile(r"LAGUNA\s+(?:SBTRACE:\s*launchd\s+syscall|Sandbox:\s*launchd)\s+op\s+[24]\b", re.I),
    re.compile(r"LAGUNA\s+NET:\s*launchd\s+lo0\s+add\s+address\s+127\.0\.0\.1\b", re.I),
    re.compile(r"CODE\s+SIGNING:\s+cs_invalid_page\([^\n]*p=1\[launchd\]", re.I),
    re.compile(r"Darwin\s+Bootstrapper\s+Version\b", re.I),
    re.compile(r"\bpid\s+1\s+exited\b[^\n]*(?:namespace|reason|dyld|launchd)", re.I),
]
PANIC_RX = re.compile(
    r"(?m)^\s*(?:panic\s+message:|panic\(cpu\s+\d+\s+caller\s+|"
    r"Debugger\s+message:\s*panic\b|Panicked\s+thread:)"
)
LR_RX = re.compile(
    r"^\s*lr:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,16})"
    r"(?:\s+fp:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,16}))?(?:\s.*)?$"
)
MARKER_READ_RX = re.compile(
    r"MARKER-READ\s+marker=(0x[0-9a-fA-F]+)\s+stage=(0x[0-9a-fA-F]+)\s+"
    r"inx0=(0x[0-9a-fA-F]+)\s+finx0=(0x[0-9a-fA-F]+)\s+"
    r"stub=(0x[0-9a-fA-F]+)\s+handoff=(0x[0-9a-fA-F]+)\s+bapa=(0x[0-9a-fA-F]+)"
)

RAW_TOKENS = (
    b"DISPLAY-PROBE", b"KEXEC-XNU", b"MARKER-READ", b"DIAG-RING",
    b"DIAG-FLUSH", b"Linux version", b"hackios-init",
)


def _int(value):
    return int(value, 0)


def _find_offsets(data, needle, limit=4096):
    """Bounded mmap/bytes search; returns (offsets, truncated)."""
    out = []
    pos = 0
    while len(out) < limit:
        hit = data.find(needle, pos)
        if hit < 0:
            return out, False
        out.append(hit)
        pos = hit + 1
    return out, data.find(needle, pos) >= 0


def _slice(data, start, length):
    if start < 0 or length < 0 or start + length > len(data):
        return None
    return bytes(data[start:start + length])


def _stream_tail(text, count):
    if count <= 0:
        return ""
    return text[-count:]


def _printable_ratio(raw):
    nonzero = bytes(b for b in raw if b)
    if not nonzero:
        return 1.0
    good = sum(b in (9, 10, 13) or 0x20 <= b <= 0x7E or b >= 0x80 for b in nonzero)
    return good / len(nonzero)


def _decode_ring(raw):
    # XNU's own LLDB MsgBuffer._values() skips NULs while walking the ring.
    return raw.replace(b"\0", b"").decode("utf-8", "replace")


def _prz_header(data, off, zone_size):
    if off < 0 or off + PRZ_HDR > len(data) or zone_size <= PRZ_HDR:
        return None
    sig, start, used = struct.unpack_from("<III", data, off)
    capacity = zone_size - PRZ_HDR
    if sig != PRZ_SIG or not (start <= used <= capacity):
        return None
    return {
        "file_offset": off,
        "signature": sig,
        "start": start,
        "used": used,
        "capacity": capacity,
        "zone_truncated": off + zone_size > len(data),
        "stream_truncated": off + PRZ_HDR + used > len(data),
    }


def _linearize_prz(data, off, zone_size):
    """Linux persistent_ram_save_old: data[start:size] + data[:start].

    `size` is the occupied byte count, not the next-write position.  In
    particular, a partial non-wrapped record has start == size; treating size
    as a span beginning at start duplicates stale bytes.  Full rings use the
    same formula with size == capacity.
    """
    hdr = _prz_header(data, off, zone_size)
    if hdr is None:
        return None
    hdr = dict(hdr)
    if hdr["stream_truncated"]:
        hdr["stream"] = None
        return hdr
    start, used = hdr["start"], hdr["used"]
    ring = bytes(data[off + PRZ_HDR:off + PRZ_HDR + used])
    stream = ring[start:used] + ring[:start]
    hdr["stream_raw"] = stream
    hdr["stream"] = _decode_ring(stream)
    hdr["stream_bytes"] = len(stream)
    return hdr


def _marker_slots(raw):
    out = {
        "present": False, "record_valid": False, "confidence": "absent",
        "offset": MARKER_OFF,
    }
    if len(raw) < 0x130:
        out["truncated"] = True
        return out
    marker, stage, in_x0, final_x0, stub, handoff, reserved, boot_args = \
        struct.unpack_from("<IIQQQIIQ", raw, MARKER_OFF)
    out.update({
        "magic": marker, "stage": stage, "incoming_x0": in_x0,
        "final_x0": final_x0, "stub_pa": stub, "handoff": handoff,
        "handoff_reserved": reserved, "boot_args_pa": boot_args,
    })
    out["present"] = marker == MARKER_MAGIC
    kexec = (
        out["present"] and stage != 0 and handoff == HANDOFF_MAGIC and
        reserved == 0 and final_x0 != 0 and final_x0 == boot_args and
        stub != 0 and stub % 4 == 0
    )
    direct = (
        out["present"] and stage != 0 and handoff == 0 and final_x0 != 0 and
        stub != 0 and stub % 4 == 0
    )
    out["record_valid"] = bool(kexec or direct)
    out["entry_mode"] = "kexec" if kexec else ("direct" if direct else None)
    if out["record_valid"]:
        out["confidence"] = "strong"
    elif out["present"]:
        out["confidence"] = "magic-only"
    return out


def _marker_scratch(raw):
    out = {"present": False, "marker_present": False, "offset": SCRATCH_OFF}
    if len(raw) < SCRATCH_OFF + 0x44:
        out["truncated"] = True
        return out
    rec = raw[SCRATCH_OFF:SCRATCH_OFF + 0x40]
    out["boot_counter"] = struct.unpack_from("<I", raw, SCRATCH_OFF + 0x40)[0]
    out["present"] = rec[:4] == b"M1\0\0"
    if out["present"]:
        marker = struct.unpack_from("<I", rec, 4)[0]
        in_x0, final_x0, stub = struct.unpack_from("<QQQ", rec, 8)
        handoff = struct.unpack_from("<I", rec, 32)[0]
        boot_args = struct.unpack_from("<Q", rec, 40)[0]
        out.update({
            "marker_present": marker == MARKER_MAGIC, "magic": marker,
            "incoming_x0": in_x0, "final_x0": final_x0, "stub_pa": stub,
            "handoff": handoff, "boot_args_pa": boot_args,
        })
    return out


def _parse_msgbuf_mirror(raw, tail_count=16384, decode=True):
    """Decode R4's fixed 64-byte header + live-size ring at pmsg+0x40000.

    Wire layout (all little-endian): magic[16], seq:u32, flags:u32,
    msgbufp:u64, msg_bufc:u64, size:u32, write:u32, absolute_total:u64,
    tail_off:u32, tail_len:u32.  tail_off is a ring offset, not a pstore/file
    offset.  Exactly tail_len bytes are linearized; stale ring bytes outside
    that span are never included.  A single fixed record has no predecessor,
    so temporal seq monotonicity cannot be proved from one dump.
    """
    doc = {
        "offset": MSGBUF_MIRROR_OFF, "header_size": MSGBUF_MIRROR_HEADER.size,
        "ring_max": MSGBUF_MIRROR_RING_MAX, "present": False, "valid": False,
        "tail_reconstructed": False, "seq_monotonicity": "no-predecessor-in-dump",
    }
    start = MSGBUF_MIRROR_OFF
    if len(raw) < start + MSGBUF_MIRROR_HEADER.size:
        doc.update(truncated=True, error="truncated 64-byte mirror header")
        return doc, None
    header = raw[start:start + MSGBUF_MIRROR_HEADER.size]
    magic, seq, flags, msgbufp, msg_bufc, size, write, total, tail_off, tail_len = \
        MSGBUF_MIRROR_HEADER.unpack(header)
    doc.update({
        "seq": seq, "flags": flags, "msgbufp_va": msgbufp,
        "msg_bufc_va": msg_bufc, "size": size, "write_position": write,
        "stream_total": total, "tail_offset": tail_off, "tail_length": tail_len,
        "seq_nonzero": seq != 0,
        "live_vas_kernel_shaped": bool(
            msgbufp & 0xFFFF000000000000 == 0xFFFF000000000000 and
            msg_bufc & 0xFFFF000000000000 == 0xFFFF000000000000
        ),
    })
    if magic != MSGBUF_MIRROR_MAGIC.ljust(16, b"\0"):
        doc["header_present"] = any(header)
        doc["error"] = "empty/bad magic" if any(header) else "empty"
        return doc, None
    doc["present"] = True
    doc["magic"] = MSGBUF_MIRROR_MAGIC.decode("ascii")
    errors = []
    if seq == 0:
        errors.append("seq is zero")
    if not (0 < size <= MSGBUF_MIRROR_RING_MAX):
        errors.append(f"live size {size:#x} outside 1..1MiB")
    if size and write > size:
        errors.append("write index exceeds live size")
    if tail_len > size:
        errors.append("tail length exceeds live size")
    if total < tail_len:
        errors.append("absolute stream total precedes tail length")
    if size and tail_off >= size:
        errors.append("tail offset outside live ring")
    available = max(0, len(raw) - start - MSGBUF_MIRROR_HEADER.size)
    doc["ring_available"] = min(available, size)
    doc["ring_truncated"] = available < size
    if tail_len and doc["ring_truncated"]:
        errors.append("live ring truncated in dump")

    expected_tail = total - tail_len if total >= tail_len else None
    if size and expected_tail is not None:
        expected_off = expected_tail % size
        doc["expected_tail_offset"] = expected_off
        doc["tail_offset_matches_stream"] = tail_len == 0 or tail_off == expected_off
        doc["write_matches_stream"] = write % size == total % size
        doc["absolute_tail_start"] = expected_tail
        if not doc["tail_offset_matches_stream"]:
            errors.append("tail offset disagrees with absolute stream geometry")
        if not doc["write_matches_stream"]:
            errors.append("live write index disagrees with absolute stream total")
    doc["errors"] = errors
    doc["valid"] = not errors
    if not doc["valid"]:
        doc["error"] = "; ".join(errors)
        return doc, None
    if not decode:
        return doc, None

    ring_start = start + MSGBUF_MIRROR_HEADER.size
    ring = raw[ring_start:ring_start + size]
    if tail_len == 0:
        linear = b""
    elif tail_off + tail_len <= size:
        linear = ring[tail_off:tail_off + tail_len]
    else:
        lead = size - tail_off
        linear = ring[tail_off:] + ring[:tail_len - lead]
    if len(linear) != tail_len:
        doc.update(valid=False, tail_reconstructed=False,
                   error="tail reconstruction length mismatch")
        return doc, None
    text = _decode_ring(linear)
    doc.update({
        "tail_reconstructed": True, "reconstructed_bytes": len(linear),
        "text_bytes": len(text.encode("utf-8", "replace")),
        "log_lines": len(text.splitlines()),
        "darwin_banner": bool(DARWIN_RX.search(text)),
        "tail": _stream_tail(text, tail_count), "tail_is_runtime": True,
    })
    return doc, text


def _parse_lglw_slot(raw, slot):
    off = LGLW_OFF + slot * LGLW_SLOT_SIZE
    doc = {"slot": slot, "offset": off, "valid": False, "magic_present": False}
    if len(raw) < off + LGLW_SLOT_SIZE:
        doc["error"] = "truncated"
        return doc
    rec = raw[off:off + LGLW_SLOT_SIZE]
    magic, version, size, seq, stage, producer, flags = struct.unpack_from("<IHHIIII", rec, 0)
    detail = struct.unpack_from("<4Q", rec, 0x18)
    crc, commit = struct.unpack_from("<II", rec, 0x78)
    crc_ok = ddp.crc32_ieee(rec[:0x78]) == crc
    note = rec[0x38:0x78].split(b"\0", 1)[0].decode("ascii", "replace")
    doc.update({
        "magic": magic, "version": version, "bytes": size, "seq": seq,
        "stage": stage, "stage_name": STAGE_NAMES.get(stage, f"UNKNOWN_{stage:#x}"),
        "producer": producer,
        "producer_name": PRODUCER_NAMES.get(producer, f"UNKNOWN_{producer:#x}"),
        "flags": flags, "attempt": bool(flags & 1), "failure": bool(flags & 2),
        "detail": list(detail), "note": note, "crc": crc, "crc_ok": crc_ok,
        "commit": commit, "commit_ok": commit == LGLW_COMMIT,
        "magic_present": magic == LGLW_MAGIC,
        "seq_valid": 1 <= seq <= LGLW_SEQ_MAX,
    })
    checks = {
        "magic": magic == LGLW_MAGIC, "version": version == 1,
        "size": size == LGLW_SLOT_SIZE, "seq": doc["seq_valid"],
        "stage": stage != 0, "producer": producer != 0,
        "crc": crc_ok, "commit": doc["commit_ok"],
    }
    doc["checks"] = checks
    doc["valid"] = all(checks.values())
    doc["torn"] = doc["magic_present"] and not doc["commit_ok"]
    doc["corrupt"] = doc["magic_present"] and not crc_ok
    if not doc["valid"]:
        doc["error"] = ",".join(name for name, ok in checks.items() if not ok)
    return doc


def _decode_stage_records(raw):
    """Decode compact R4 breadcrumbs and R3's committed LGLW slots."""
    compact = {
        "offset": STAGE_COMPACT_OFF, "bytes": STAGE_COMPACT_BYTES,
        "present": False, "valid": False, "entries": [], "invalid_words": [],
        "monotonic": True,
    }
    if len(raw) >= STAGE_COMPACT_OFF + STAGE_COMPACT_BYTES:
        words = struct.unpack_from("<12I", raw, STAGE_COMPACT_OFF)
        compact["words"] = list(words)
        compact["present"] = any(words)
        previous = 0
        for index, stage in enumerate(words):
            if stage == 0:
                continue
            if stage not in STAGE_NAMES:
                compact["invalid_words"].append({"index": index, "value": stage})
                compact["monotonic"] = False
                continue
            if stage < previous:
                compact["monotonic"] = False
            previous = stage
            compact["entries"].append({
                "index": index, "stage": stage, "stage_name": STAGE_NAMES[stage],
            })
        compact["valid"] = bool(compact["present"] and compact["monotonic"] and
                               compact["entries"] and not compact["invalid_words"])
        if compact["valid"]:
            compact["selected"] = compact["entries"][-1]
    else:
        compact["truncated"] = True

    slots = [_parse_lglw_slot(raw, slot) for slot in range(LGLW_SLOT_COUNT)]
    valid_slots = [rec for rec in slots if rec["valid"]]
    newest_lglw = max(valid_slots, key=lambda rec: rec["seq"]) if valid_slots else None
    selected = None
    if newest_lglw is not None:
        selected = dict(newest_lglw)
        selected["source"] = "lglw"
    elif compact.get("selected") is not None:
        selected = dict(compact["selected"])
        selected.update({
            "source": "compact", "seq": None, "producer": None,
            "producer_name": None, "flags": None, "detail": [], "note": "",
        })

    compact_stage = compact.get("selected", {}).get("stage")
    lglw_stage = newest_lglw.get("stage") if newest_lglw else None
    conflict = bool(compact_stage and lglw_stage and compact_stage != lglw_stage)
    return {
        "compact": compact, "slots": slots, "valid_lglw_slots": len(valid_slots),
        "selected_lglw_slot": newest_lglw["slot"] if newest_lglw else None,
        "selected": selected, "channel_conflict": conflict,
        "invalid_lglw_candidates": sum(rec["magic_present"] and not rec["valid"]
                                       for rec in slots),
        "stage": selected.get("stage") if selected else None,
        "stage_name": selected.get("stage_name") if selected else None,
        "present": selected is not None,
    }


def _fixed_mirror(raw):
    if len(raw) <= MIRROR_OFF:
        return []
    hits = ddp.scan_pmsg_mirror(raw[MIRROR_OFF:MIRROR_OFF + ddp.MIRROR_WINDOW])
    for hit in hits:
        hit["file_off"] += MIRROR_OFF
        hit["assumed_zone_off"] = hit["file_off"] + PRZ_HDR
    return hits


def _decode_diag(raw):
    out = {"counter": None, "records": [], "valid_records": 0, "text": ""}
    if len(raw) >= DIAG_COUNT_OFF + 4:
        out["counter"] = struct.unpack_from("<I", raw, DIAG_COUNT_OFF)[0]
    records = []
    for slot in range(DIAG_REC_COUNT):
        off = DIAG_RING_OFF + slot * DIAG_REC_SIZE
        if off + DIAG_REC_SIZE > len(raw):
            break
        rec = raw[off:off + DIAG_REC_SIZE]
        if rec[:8] != b"DIAGREC\0":
            continue
        seq, length, crc, boot, reserved0, reserved1 = struct.unpack_from("<6I", rec, 8)
        item = {"slot": slot, "seq": seq, "len": length, "boot": boot,
                "reserved": [reserved0, reserved1], "valid": False}
        if 0 < length <= DIAG_REC_SIZE - 32:
            payload = rec[32:32 + length]
            item["crc_ok"] = ddp.crc32_ieee(payload) == crc
            if item["crc_ok"]:
                item["valid"] = True
                item["text"] = payload.decode("utf-8", "replace")
                item["headline"] = item["text"].splitlines()[0] if item["text"] else ""
        else:
            item["crc_ok"] = False
            item["error"] = "bad length"
        records.append(item)
    records.sort(key=lambda r: (r["seq"], r["slot"]))
    out["records"] = records
    out["valid_records"] = sum(r["valid"] for r in records)
    out["text"] = "\n".join(r["text"] for r in records if r["valid"])
    return out


def _public_prz(hdr):
    if hdr is None:
        return None
    return {k: v for k, v in hdr.items() if k not in ("stream", "stream_raw")}


def _find_zones(data, dump_base, ramoops_pa, warnings):
    expected_console = ramoops_pa - dump_base if dump_base is not None else None
    expected_pmsg = expected_console + PMSG_OFF if expected_console is not None else None
    pmsg_pa = ramoops_pa + PMSG_OFF

    consoles = {}

    def add_console(off, method):
        if off < 0 or off >= len(data):
            return
        hdr = _linearize_prz(data, off, CONSOLE_SIZE)
        if hdr is None:
            return
        item = consoles.get(off)
        if item is None:
            item = {"off": off, "hdr": hdr, "methods": set()}
            consoles[off] = item
        item["methods"].add(method)

    if expected_console is not None:
        before = len(consoles)
        add_console(expected_console, "physical-layout")
        if len(consoles) == before and 0 <= expected_console < len(data):
            warnings.append(f"physical console header at {expected_console:#x} is invalid")
    prz_hits, prz_truncated = _find_offsets(data, PRZ_SIG_BYTES, 16384)
    if prz_truncated:
        warnings.append("DBGC scan hit limit; additional persistent_ram zones may exist")
    for hit in prz_hits:
        add_console(hit, "persistent_ram-signature")

    pmsgs = {}

    def add_pmsg(off, method):
        if off < 0 or off >= len(data):
            return
        item = pmsgs.get(off)
        if item is None:
            item = {"off": off, "methods": set()}
            pmsgs[off] = item
        item["methods"].add(method)

    if expected_pmsg is not None:
        add_pmsg(expected_pmsg, "physical-layout")
    for off in consoles:
        add_pmsg(off + PMSG_OFF, "console-adjacent")
        # A valid pmsg PRZ header is not itself a console candidate when the
        # surrounding fixed-offset records identify its real role.
        add_pmsg(off, "persistent_ram-signature")

    for needle, delta, method, limit in (
        (ddp.MIRROR_MAGIC, MIRROR_OFF, "panic-mirror-signature", 128),
        (MSGBUF_MIRROR_MAGIC, MSGBUF_MIRROR_OFF, "xnu-msgbuf-mirror-signature", 32),
        (struct.pack("<IHH", LGLW_MAGIC, 1, LGLW_SLOT_SIZE),
         LGLW_OFF, "lglw-slot0-signature", 32),
        (struct.pack("<IHH", LGLW_MAGIC, 1, LGLW_SLOT_SIZE),
         LGLW_OFF + LGLW_SLOT_SIZE, "lglw-slot1-signature", 32),
        (b"M1\0\0" + MARKER_BYTES, SCRATCH_OFF, "marker-scratch-signature", 128),
        (struct.pack("<II", HANDOFF_MAGIC, 0), 0x120, "handoff-signature", 1024),
        (MARKER_BYTES, MARKER_OFF, "entry-marker-signature", 4096),
    ):
        hits, truncated = _find_offsets(data, needle, limit)
        if truncated:
            warnings.append(f"{method} scan hit limit; results may be incomplete")
        for hit in hits:
            add_pmsg(hit - delta, method)

    for item in pmsgs.values():
        off = item["off"]
        if (item["methods"] == {"entry-marker-signature"} and
                off != expected_pmsg and off - PMSG_OFF not in consoles):
            # Do not copy/scan 2 MiB for every bare 0x5a5a5a5a hit in a poisoned
            # or static-image region.  A marker-only discovery needs either a
            # coherent slot record or a valid zone header; other discoveries
            # (scratch/mirror/handoff/adjacency) provide their own anchor.
            quick_raw = bytes(data[off:off + min(0x130, len(data) - off)])
            quick_marker = _marker_slots(quick_raw)
            if not quick_marker["record_valid"] and _prz_header(data, off, PMSG_SIZE) is None:
                item.update(score=-1, identified=False)
                continue
        raw = bytes(data[off:off + min(PMSG_SIZE, len(data) - off)])
        marker = _marker_slots(raw)
        scratch = _marker_scratch(raw)
        fixed = _fixed_mirror(raw)
        msgbuf_mirror, _ = _parse_msgbuf_mirror(raw, decode=False)
        stage_records = _decode_stage_records(raw)
        prz = _prz_header(data, off, PMSG_SIZE)
        pair = consoles.get(off - PMSG_OFF)
        handoff_present = bool(
            len(raw) >= 0x130 and marker.get("handoff") == HANDOFF_MAGIC and
            marker.get("handoff_reserved") == 0 and marker.get("boot_args_pa")
        )
        score = 0
        if off == expected_pmsg:
            score += 80
        if pair is not None:
            score += 45
        if prz is not None:
            score += 5
        if marker["present"]:
            score += 20
        if marker["record_valid"]:
            score += 30
        if scratch["present"]:
            score += 25
        if handoff_present:
            score += 10
        if any(m.get("valid") for m in fixed):
            score += 50
        elif fixed:
            score += 15
        if msgbuf_mirror["valid"]:
            score += 70
        elif msgbuf_mirror["present"]:
            score += 20
        if stage_records["selected_lglw_slot"] is not None:
            score += 40
        elif stage_records["invalid_lglw_candidates"]:
            score += 10
        identified = bool(
            off == expected_pmsg or pair is not None or fixed or
            msgbuf_mirror["present"] or stage_records["selected_lglw_slot"] is not None or
            scratch["present"] or handoff_present or marker["record_valid"] or
            (marker["present"] and prz is not None)
        )
        item.update(raw=raw, marker=marker, scratch=scratch, fixed=fixed,
                    msgbuf_mirror=msgbuf_mirror, stage_records=stage_records,
                    prz=prz, pair=pair, score=score, identified=identified,
                    handoff_present=handoff_present)

    for item in consoles.values():
        text = item["hdr"].get("stream") or ""
        score = 20
        if item["off"] == expected_console:
            score += 80
        if item["hdr"]["used"]:
            score += 5
        if ddp.BEGIN in text or "panic message:" in text:
            score += 50
        if PROBE_RX.search(text) or "Linux version" in text:
            score += 20
        if item["hdr"]["stream_truncated"]:
            score -= 20
        item["score"] = score

    pmsg_choice = None
    if pmsgs:
        # Prefer corroborated fixed-offset records; physical layout and a valid
        # console+pmsg pair outrank an isolated bare magic word.
        pmsg_choice = max(pmsgs.values(), key=lambda x: (x["score"], -x["off"]))
        if pmsg_choice["score"] <= 0 or not pmsg_choice["identified"]:
            # A lone valid DBGC header is role-agnostic.  Without a fixed pmsg
            # anchor or an adjacent console it must not steal the console role.
            pmsg_choice = None

    console_choice = None
    if pmsg_choice is not None and pmsg_choice.get("pair") is not None:
        console_choice = pmsg_choice["pair"]
    else:
        candidates = [c for c in consoles.values()
                      if pmsg_choice is None or c["off"] != pmsg_choice["off"]]
        if candidates:
            console_choice = max(candidates, key=lambda x: (x["score"], -x["off"]))

    # If an isolated pmsg PRZ candidate won only because no other signature
    # exists, do not steal a stronger real console.  Empty adjacent zones are
    # still found through the pair bonus above.
    if (pmsg_choice is not None and console_choice is not None and
            pmsg_choice["off"] == console_choice["off"]):
        pmsg_choice = None

    votes = []
    if console_choice is not None:
        votes.append((ramoops_pa - console_choice["off"], console_choice["score"], "console"))
    if pmsg_choice is not None:
        votes.append((pmsg_pa - pmsg_choice["off"], pmsg_choice["score"], "pmsg"))
    inferred_base = dump_base
    base_origin = "explicit" if dump_base is not None else None
    if dump_base is None and votes:
        votes.sort(key=lambda x: x[1], reverse=True)
        inferred_base = votes[0][0]
        base_origin = f"inferred-{votes[0][2]}-signature"
        if len({v[0] for v in votes}) > 1:
            warnings.append("console/pmsg imply different dump bases; using the stronger zone")
        elif len(votes) == 2:
            base_origin = "inferred-console+pmsg-signatures"

    console_doc = None
    console_text = None
    if console_choice is not None:
        console_doc = dict(console_choice["hdr"])
        console_doc.update({
            "discovery": sorted(console_choice["methods"]),
            "score": console_choice["score"],
            "physical_address": (inferred_base + console_choice["off"]
                                 if inferred_base is not None else None),
        })
        console_text = console_choice["hdr"].get("stream")
        console_doc = _public_prz(console_doc)

    pmsg_doc = None
    pmsg_raw = None
    diag_text = ""
    if pmsg_choice is not None:
        raw = pmsg_choice["raw"]
        pmsg_raw = raw
        mirrors = ddp.scan_pmsg_mirror(raw)
        diag = _decode_diag(raw)
        diag_text = diag["text"]
        mirror_docs = []
        for m in mirrors:
            keep = {k: v for k, v in m.items()
                    if k not in ("payload", "assumed_zone_off")}
            # This is a raw physical pmsg zone, not a linearized pstore file;
            # there is no 12-byte file-origin delta to add.
            keep["zone_offset"] = m["file_off"]
            keep["dump_file_offset"] = pmsg_choice["off"] + m["file_off"]
            if m.get("payload"):
                keep["payload_head"] = m["payload"][:160]
            mirror_docs.append(keep)
        diag_public = {k: v for k, v in diag.items() if k != "text"}
        diag_public["records"] = [
            {k: v for k, v in r.items() if k != "text"} for r in diag["records"]
        ]
        pmsg_doc = {
            "file_offset": pmsg_choice["off"],
            "physical_address": (inferred_base + pmsg_choice["off"]
                                 if inferred_base is not None else None),
            "available_bytes": len(raw), "zone_size": PMSG_SIZE,
            "zone_truncated": len(raw) < PMSG_SIZE,
            "discovery": sorted(pmsg_choice["methods"]),
            "score": pmsg_choice["score"],
            "persistent_ram_header": _public_prz(pmsg_choice["prz"]),
            "handoff_present": pmsg_choice["handoff_present"],
            "marker": pmsg_choice["marker"], "marker_scratch": pmsg_choice["scratch"],
            "mirrors": mirror_docs, "diag_ring": diag_public,
            "msgbuf_mirror_header": pmsg_choice["msgbuf_mirror"],
            "stage_summary": {
                "present": pmsg_choice["stage_records"]["present"],
                "stage": pmsg_choice["stage_records"]["stage"],
                "stage_name": pmsg_choice["stage_records"]["stage_name"],
                "selected_lglw_slot": pmsg_choice["stage_records"]["selected_lglw_slot"],
                "valid_lglw_slots": pmsg_choice["stage_records"]["valid_lglw_slots"],
                "compact_valid": pmsg_choice["stage_records"]["compact"]["valid"],
            },
        }

    return {
        "console": console_doc, "pmsg": pmsg_doc,
        "dump_base": inferred_base, "dump_base_origin": base_origin,
        "console_text": console_text, "pmsg_raw": pmsg_raw,
        "diag_text": diag_text,
        "msgbuf_mirror_header": (pmsg_choice["msgbuf_mirror"]
                                 if pmsg_choice is not None else None),
        "stage_records": (pmsg_choice["stage_records"]
                          if pmsg_choice is not None else None),
    }


def _symbol_value(symcache, name):
    if symcache is None:
        return None
    return next((value for value, symbol in symcache.syms if symbol == name), None)


def _resolve_pc(addr, text_base, link_base, symcache):
    unslid = ddp.unslide(addr, text_base, link_base) if text_base is not None else None
    lookup_addr = unslid if unslid is not None else addr
    sym = symcache.lookup_code(lookup_addr) if symcache is not None else None
    return unslid, sym


def _parse_panic_text(text, symcache, channel, complete, text_base_override=None,
                      slide_override=None):
    base = ddp.parse_console_record({"text": text}, symcache)
    link_base = symcache.link_base if symcache is not None else DEFAULT_LINK_BASE
    text_base = base.get("kernel_text_base")
    if text_base_override is not None:
        text_base = text_base_override
    elif slide_override is not None:
        text_base = link_base + slide_override
    base["kernel_link_base"] = link_base
    if text_base is not None:
        base["kernel_text_base"] = text_base
        base["slide"] = text_base - link_base

    panic_part = text.split(ddp.MSGBUF_TAG, 1)[0]
    lines = panic_part.splitlines()
    frames = []
    for line in lines:
        match = LR_RX.match(line)
        if not match:
            continue
        lr = int(match.group(1), 16)
        frame = {"lr": lr}
        if match.group(2):
            frame["fp"] = int(match.group(2), 16)
        unslid, sym = _resolve_pc(lr, text_base, link_base, symcache)
        if unslid is not None:
            frame["unslid_lr"] = unslid
        if sym is not None:
            frame["lr_sym"] = sym
        frames.append(frame)
    base["frames"] = frames

    header_message = None
    plain_message = None
    for line in lines:
        stripped = line.strip()
        match = re.match(
            r"panic\(cpu\s+\d+\s+caller\s+(?:0x)?[0-9a-fA-F]+\)\s*:\s*(.*)$",
            stripped,
        )
        if match and header_message is None:
            header_message = match.group(1).strip()
        match = re.match(r"panic\s*:\s*(.+)$", stripped, re.I)
        if match and plain_message is None:
            plain_message = match.group(1).strip()
    current_message = (base.get("message") or "").strip()
    generic = current_message.lower() in ("", "panic", "(null)", "unknown")
    if header_message and generic:
        base["message"] = header_message
    elif not current_message:
        base["message"] = header_message or plain_message

    # Re-resolve the panic caller under an explicit override as well.
    caller = base.get("panic_header")
    if caller:
        unslid, sym = _resolve_pc(caller["addr"], text_base, link_base, symcache)
        caller.pop("unslid_addr", None)
        caller.pop("sym", None)
        if unslid is not None:
            caller["unslid_addr"] = unslid
        if sym is not None:
            caller["sym"] = sym

    base.update({
        "channel": channel, "complete": complete, "panicked": True,
        "panic_message": base.get("message"),
    })
    base.pop("text", None)
    base.pop("head", None)
    return base


def _decode_panic(console_text, pmsg_raw, symcache, text_base_override, slide_override):
    console_records = []
    complete = []
    torn = []
    if console_text is not None:
        for rec in ddp.extract_console_records(console_text):
            parsed = _parse_panic_text(
                rec.get("text", rec.get("head", "")), symcache,
                "console" if rec.get("complete") else "console-torn",
                bool(rec.get("complete")), text_base_override, slide_override,
            )
            for key in ("start_off", "end_off", "bytes"):
                if key in rec:
                    parsed[key] = rec[key]
            console_records.append(parsed)
            (complete if rec.get("complete") else torn).append(parsed)

    mirrors = ddp.scan_pmsg_mirror(pmsg_raw) if pmsg_raw is not None else []
    newest = ddp.pick_newest_mirror(mirrors)
    chosen = None
    if complete:
        chosen = complete[-1]
    elif newest is not None:
        chosen = _parse_panic_text(
            newest.get("payload", ""), symcache, "mirror", True,
            text_base_override, slide_override,
        )
        chosen["mirror"] = {
            "seq": newest.get("seq"), "file_off": newest.get("file_off"),
            "declared_len": newest.get("declared_len"), "crc_valid": True,
        }
    elif torn:
        chosen = torn[-1]

    return chosen, {
        "console_records": console_records,
        "mirror_records_found": len(mirrors),
        "mirror_records_valid": sum(bool(m.get("valid")) for m in mirrors),
        "mirror_records_corrupt": sum(not m.get("valid") for m in mirrors),
    }


def _find_msgbuf(data, symcache, dump_base, slide_hint, kernel_map, tail_count, warnings):
    msgbuf_va = _symbol_value(symcache, "msgbuf")
    smsg_va = _symbol_value(symcache, "smsg_bufc")
    relative = smsg_va - msgbuf_va if msgbuf_va is not None and smsg_va is not None else None
    link_base = symcache.link_base if symcache is not None else DEFAULT_LINK_BASE
    hits, truncated = _find_offsets(data, MSG_MAGIC_BYTES, 8192)
    if truncated:
        warnings.append("msgbuf magic scan hit limit; results may be incomplete")
    candidates = []
    rejected = 0
    for off in hits:
        if off + MSG_HEADER_SIZE > len(data):
            continue
        magic, size, bufx, bufr, pointer = struct.unpack_from("<4IQ", data, off)
        if not (128 <= size <= MSG_MAX and bufx <= size and bufr <= size):
            continue
        if pointer & 0xFFFF000000000000 != 0xFFFF000000000000:
            continue
        implied_slide = pointer - smsg_va if smsg_va is not None else None
        # This is the exact initialized image shape from subr_log.c.  File
        # bytes beside it are not a runtime ring, even if they happen to look
        # like Darwin/LAGUNA strings.  A real wrapped buffer normally has a
        # nonzero write position or a nonzero slide.
        static_image_header = bool(bufx == 0 and bufr == 0 and implied_slide == 0)
        mappings = []
        if relative is not None:
            # Both objects live in XNU's direct map, so their VA delta equals
            # their PA/file delta and KASLR cancels.  The pointer still provides
            # an independent slide consistency check.
            pointer_ok = slide_hint is None or implied_slide == slide_hint
            mappings.append(("mach-symbol-relative", off + relative, pointer_ok,
                             implied_slide))
        if kernel_map is not None and dump_base is not None:
            virt_base, phys_base = kernel_map
            use_slide = slide_hint
            if use_slide is None:
                use_slide = implied_slide if implied_slide is not None else 0
            ring_pa = phys_base + pointer - (virt_base + use_slide)
            mappings.append(("explicit-direct-map", ring_pa - dump_base, True,
                             use_slide))

        best = None
        for method, ring_off, pointer_ok, mapping_slide in mappings:
            raw = _slice(data, ring_off, size)
            if raw is None:
                continue
            write = bufx % size
            linear = raw[write:] + raw[:write]
            text = _decode_ring(linear)
            ratio = _printable_ratio(linear)
            evidence = sum(bool(rx.search(text)) for rx in (
                DARWIN_RX, BSD_ROOT_RX, ROOT_MOUNT_RX, PANIC_RX,
                re.compile(r"LAGUNA\s"), re.compile(r"launchd", re.I),
            ))
            score = 10 + min(20, len(text.splitlines()))
            if static_image_header:
                score -= 120
            if bufx:
                score += 5
            if method == "mach-symbol-relative":
                score += 25
            else:
                score += 20
            if pointer_ok:
                score += 20
            else:
                score -= 60
            if ratio >= 0.75:
                score += 10
            else:
                score -= 40
            score += evidence * 50
            if DARWIN_RX.search(text):
                score += 100
            item = {
                "file_offset": off, "magic": magic, "size": size,
                "write_position": bufx, "read_position": bufr,
                "buffer_va": pointer, "ring_file_offset": ring_off,
                "mapping": method, "pointer_slide_consistent": pointer_ok,
                "implied_slide": mapping_slide, "printable_ratio": ratio,
                "text": text, "text_bytes": len(text.encode("utf-8", "replace")),
                "runtime_content": bool(text) and not static_image_header,
                "static_image_header": static_image_header, "score": score,
                "physical_address": dump_base + off if dump_base is not None else None,
                "ring_physical_address": (dump_base + ring_off
                                          if dump_base is not None else None),
            }
            if best is None or item["score"] > best["score"]:
                best = item
        if best is not None:
            candidates.append(best)
        else:
            rejected += 1

    if not candidates:
        return None, {"headers_seen": len(hits), "headers_rejected": rejected,
                      "symbol_relative_available": relative is not None}
    chosen = max(candidates, key=lambda x: (x["score"], -x["file_offset"]))
    text = chosen.pop("text")
    chosen["log_lines"] = len(text.splitlines())
    chosen["tail"] = _stream_tail(text, tail_count)
    chosen["darwin_banner"] = (bool(DARWIN_RX.search(text)) and
                               not chosen["static_image_header"])
    chosen["static_header_only"] = chosen["static_image_header"]
    chosen["tail_is_runtime"] = not chosen["static_image_header"]
    chosen["alternative_headers_decoded"] = len(candidates) - 1
    return (chosen, text), {"headers_seen": len(hits), "headers_rejected": rejected,
                            "headers_decoded": len(candidates),
                            "symbol_relative_available": relative is not None}


def _clean_raw_line(raw):
    raw = raw.replace(b"\0", b"")
    raw = bytes(b if b in (9,) or 0x20 <= b <= 0x7E else 0x20 for b in raw)
    return raw.decode("ascii", "replace").strip()


def _harvest_raw_lines(data, warnings):
    """Recover first-stage lines even if Linux re-headered the console.

    Only first-stage tokens are harvested from the whole dump.  XNU milestone
    strings are deliberately not accepted from this unconstrained channel:
    format strings in a Mach-O are not runtime log evidence.
    """
    found = {}
    for token in RAW_TOKENS:
        hits, truncated = _find_offsets(data, token, 256)
        if truncated:
            warnings.append(f"raw {token.decode('ascii', 'replace')} line scan hit limit")
        for hit in hits:
            left = max(0, hit - 2048)
            right = min(len(data), hit + 4096)
            start = data.rfind(b"\n", left, hit)
            end = data.find(b"\n", hit, right)
            start = left if start < 0 else start + 1
            end = right if end < 0 else end
            line = _clean_raw_line(bytes(data[start:end]))
            if token.decode("ascii") not in line or not line or len(line) > 2048:
                continue
            # Ignore source-like format strings accidentally encountered in a
            # payload; dynamically assembled probe lines do not contain these.
            if "%s" in line or "%d" in line or "%llu" in line:
                continue
            found[(hit, line)] = {"file_offset": hit, "line": line, "token": token.decode("ascii")}
    return [found[key] for key in sorted(found)]


def _marker_read_evidence(text):
    records = []
    for match in MARKER_READ_RX.finditer(text):
        marker, stage, _inx0, final_x0, stub, handoff, boot_args = \
            [int(v, 16) for v in match.groups()]
        valid = (
            marker == MARKER_MAGIC and stage != 0 and handoff == HANDOFF_MAGIC and
            final_x0 != 0 and final_x0 == boot_args and stub != 0 and stub % 4 == 0
        )
        records.append({"valid": valid, "line": match.group(0)})
    return records


def _first_match_line(patterns, text):
    if isinstance(patterns, re.Pattern):
        patterns = [patterns]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)[:500]
    return None


def _symbolication_doc(symcache, error=None):
    if symcache is None:
        return {"available": False, "error": error,
                "expected_0128_sha256": MACH0128_SHA256}
    return {
        "available": True, "mach": symcache.path, "sha256": symcache.sha256,
        "actual_0128": symcache.sha256 == MACH0128_SHA256,
        "note": error,
        "symbols": len(symcache.syms), "code_symbols": len(symcache.code_syms),
        "kernel_link_base": symcache.link_base,
    }


def _verdict(milestones, panic, probe_build):
    if milestones["panicked"]:
        cls = "PANICKED"
    elif milestones.get("silent_pre_console"):
        cls = "SILENT-PRE-CONSOLE"
    elif milestones["launchd_executed"]:
        cls = "LAUNCHD-EXEC"
    elif milestones["root_mounted"]:
        cls = "ROOT-MOUNTED"
    elif milestones["xnu_entered"]:
        cls = "XNU-ENTERED"
    elif milestones["probe_ran"]:
        cls = "PROBE-RAN"
    else:
        cls = "NOTHING"
    milestones["nothing"] = cls == "NOTHING"

    def yn(value):
        return "yes" if value else "no"

    probe = yn(milestones["probe_ran"])
    if probe_build:
        probe += f" ({probe_build})"
    panic_part = yn(milestones["panicked"])
    if panic:
        message = panic.get("panic_message") or "(no panic string recovered)"
        message = message.replace("\n", " ")[:240]
        panic_part += f" (\"{message}\", frames={len(panic.get('frames', []))})"
    stage = milestones.get("stage")
    stage_name = milestones.get("stage_name")
    stage_text = f"{stage_name}({stage})" if stage is not None else "none"
    dyld = milestones.get("dyld_status", "not-seen")
    silent = milestones.get("silent_pre_console", False)
    line = (
        f"VERDICT: {cls} | stage={stage_text} | "
        f"XNU entered={yn(milestones['xnu_entered'])} | "
        f"probe ran={probe} | root mounted={yn(milestones['root_mounted'])} | "
        f"launchd exec'd={yn(milestones['launchd_executed'])} | "
        f"dyld={dyld} | panicked={panic_part} | "
        f"silent pre-console={yn(silent)} | nothing={yn(milestones['nothing'])}"
    )
    return {"class": cls, "line": line, "stage": stage,
            "stage_name": stage_name, "dyld_status": dyld,
            "silent_pre_console": silent}


def analyze(data, symcache=None, symbol_error=None, dump_path=None, dump_base=None,
            ramoops_pa=RAMOOPS_PA, tail_count=16384, text_base_override=None,
            slide_override=None, kernel_map=None):
    warnings = []
    zones = _find_zones(data, dump_base, ramoops_pa, warnings)
    console_text = zones["console_text"]
    pmsg_raw = zones["pmsg_raw"]
    stage_records = zones["stage_records"] or _decode_stage_records(b"")
    if pmsg_raw is not None:
        msgbuf_mirror, mirror_text = _parse_msgbuf_mirror(pmsg_raw, tail_count, decode=True)
    else:
        msgbuf_mirror, mirror_text = None, None
    mirror_valid = bool(msgbuf_mirror and msgbuf_mirror["valid"])
    if msgbuf_mirror and msgbuf_mirror["present"] and not mirror_valid:
        warnings.append(f"invalid XNU-MSGBUF mirror: {msgbuf_mirror.get('error')}")
    if stage_records["channel_conflict"]:
        warnings.append("compact and LGLW stage channels disagree; committed LGLW wins")
    if stage_records["compact"]["present"] and not stage_records["compact"]["valid"]:
        warnings.append("compact stage window is nonmonotonic/invalid; not used as progress")
    if stage_records["invalid_lglw_candidates"]:
        warnings.append("torn/corrupt LGLW candidate(s) present; only committed CRC-valid slots count")

    panic, panic_channels = _decode_panic(
        console_text, pmsg_raw, symcache, text_base_override, slide_override
    )
    if panic is None and mirror_text and PANIC_RX.search(mirror_text):
        panic = _parse_panic_text(
            mirror_text, symcache, "xnu-msgbuf-mirror", None,
            text_base_override, slide_override,
        )
    link_base = symcache.link_base if symcache is not None else DEFAULT_LINK_BASE
    slide_hint = slide_override
    if slide_hint is None and text_base_override is not None:
        slide_hint = text_base_override - link_base
    if slide_hint is None and panic and panic.get("slide") is not None:
        slide_hint = panic["slide"]

    msgbuf, msgbuf_scan = _find_msgbuf(
        data, symcache, zones["dump_base"], slide_hint, kernel_map,
        tail_count, warnings,
    )
    msgbuf_doc = msgbuf[0] if msgbuf else None
    msgbuf_text = (msgbuf[1] if msgbuf and not msgbuf_doc["static_header_only"]
                   else None)

    # A raw XNU msgbuf can itself be the only panic channel.
    if panic is None and msgbuf_text and PANIC_RX.search(msgbuf_text):
        panic = _parse_panic_text(
            msgbuf_text, symcache, "xnu-msgbuf", None,
            text_base_override, slide_override,
        )

    raw_lines = _harvest_raw_lines(data, warnings)
    raw_text = "\n".join(item["line"] for item in raw_lines)
    linux_parts = [console_text or "", zones["diag_text"], raw_text]
    linux_text = "\n".join(part for part in linux_parts if part)
    xnu_parts = []
    if mirror_text:
        xnu_parts.append(mirror_text)
    if msgbuf_text and msgbuf_text != mirror_text:
        xnu_parts.append(msgbuf_text)
    if panic:
        # Parsed records retain msgbuf_tail and version metadata; the original
        # text is unavailable here, so use console records for milestone scans.
        if panic.get("msgbuf_tail"):
            xnu_parts.append(panic["msgbuf_tail"])
        if panic.get("kernel_version"):
            xnu_parts.append(panic["kernel_version"])
        if panic.get("panic_message"):
            xnu_parts.append("panic message: " + panic["panic_message"])
    for rec in panic_channels["console_records"]:
        if rec.get("msgbuf_tail"):
            xnu_parts.append(rec["msgbuf_tail"])
        if rec.get("kernel_version"):
            xnu_parts.append(rec["kernel_version"])
        if rec.get("panic_message"):
            xnu_parts.append("panic message: " + rec["panic_message"])
    xnu_text = "\n".join(xnu_parts)

    marker_records = _marker_read_evidence(linux_text)
    pmsg = zones["pmsg"]
    marker_present = bool(pmsg and (
        pmsg["marker"].get("present") or pmsg["marker_scratch"].get("marker_present")
    ))
    marker_valid = bool(pmsg and pmsg["marker"].get("record_valid")) or any(
        r["valid"] for r in marker_records
    )
    darwin = bool(DARWIN_RX.search(xnu_text))
    runtime_log_text = mirror_text or msgbuf_text
    msgbuf_progress = bool(runtime_log_text and re.search(
        r"\bLAGUNA\s+[A-Z0-9_:-]+|\bKernel text base:|\bBacktrace\b|^\s*lr:",
        runtime_log_text, re.M,
    ))
    bsd_root_match = BSD_ROOT_RX.search(xnu_text)
    root_evidence = _first_match_line(ROOT_MOUNT_RX, xnu_text)
    launchd_attempt = _first_match_line(LAUNCHD_ATTEMPT_RX, xnu_text)
    launchd_evidence = _first_match_line(LAUNCHD_EXEC_RES, xnu_text)
    probe_match = PROBE_RX.search(linux_text)
    build_match = BUILD_RX.search(linux_text)
    probe_build = build_match.group(0) if build_match else None

    stage = stage_records["stage"]
    stage_name = stage_records["stage_name"]
    stage_record = stage_records["selected"] or {}
    observed_stages = set()
    if stage_records["compact"]["valid"]:
        observed_stages.update(e["stage"] for e in stage_records["compact"]["entries"])
    if stage is not None:
        observed_stages.add(stage)
    if stage_record.get("source") == "lglw":
        # The other slot is evidence, not automatically current-boot progress.
        # Count it only when its older sequence and stage fit the selected
        # record's progression; a newer KERNEL_UP after an old PANIC must not
        # inherit the old boot's root/launchd/dyld milestones.
        for rec in stage_records["slots"]:
            if (rec["valid"] and rec["seq"] < stage_record["seq"] and
                    rec["stage"] <= stage_record["stage"]):
                observed_stages.add(rec["stage"])
    stage_xnu = any(6 <= value <= 12 for value in observed_stages)
    stage_probe = any(2 <= value <= 5 for value in observed_stages)
    # PANIC(12) can happen at any point and therefore does not imply root,
    # launchd, or dyld progress.  Explicit earlier records still count.
    stage_root = any(8 <= value <= 11 for value in observed_stages)
    stage_launchd_attempt = any(9 <= value <= 11 for value in observed_stages)
    stage_launchd_exec = any(10 <= value <= 11 for value in observed_stages)
    if panic is None and stage == 12:
        panic = {
            "channel": "stage-breadcrumb", "complete": stage_record.get("source") == "lglw",
            "panicked": True, "panic_message": None, "frames": [],
            "stage": stage, "stage_record": stage_record,
        }

    shared_region_log = re.search(
        r"shared_region:[^\n]*\[1\(launchd\)\]|pid1 shared_region_check_np|"
        r"LAGUNA TRACE: launchd open [^\n]*dyld_shared_cache", xnu_text, re.I,
    )
    dyld_error_log = re.search(
        r"pid 1 exited[^\n]*namespace 6|dyld_shared_cache|Library not loaded:|"
        r"Symbol not found:|EXEC_EXIT_REASON_BAD_MACHO", xnu_text, re.I,
    )
    if 11 in observed_stages or shared_region_log:
        dyld_status = "shared-region"
    elif 10 in observed_stages:
        dyld_status = "exec-activate"
    elif dyld_error_log:
        dyld_status = "dyld-log"
    elif launchd_evidence:
        dyld_status = "userland-progress"
    else:
        dyld_status = "not-seen"
    xnu_console_seen = bool(mirror_text or msgbuf_text)
    silent_pre_console = bool(stage_records["present"] and not xnu_console_seen and not panic)

    milestones = {
        "xnu_entered": bool(marker_present or marker_valid or mirror_valid or stage_xnu or
                            panic or darwin or msgbuf_progress or bsd_root_match or
                            root_evidence or launchd_evidence),
        "probe_ran": bool(probe_match or stage_probe),
        "bsd_root_seen": bool(bsd_root_match),
        "root_mounted": bool(root_evidence or stage_root),
        "launchd_attempted": bool(launchd_attempt or stage_launchd_attempt),
        "launchd_executed": bool(launchd_evidence or stage_launchd_exec),
        "panicked": bool(panic),
        "stage": stage, "stage_name": stage_name,
        "stage_of_death": stage_name if silent_pre_console else None,
        "dyld_status": dyld_status,
        "silent_pre_console": silent_pre_console,
    }
    verdict = _verdict(milestones, panic, probe_build)
    evidence = {
        "marker_present": marker_present, "marker_record_valid": marker_valid,
        "marker_read_records": marker_records,
        "darwin_banner": _first_match_line(DARWIN_RX, xnu_text),
        "msgbuf_runtime_log": msgbuf_progress,
        "msgbuf_mirror_valid": mirror_valid,
        "stage": stage, "stage_name": stage_name,
        "dyld_status": dyld_status,
        "bsd_root": bsd_root_match.group(0) if bsd_root_match else None,
        "root_mount": root_evidence,
        "launchd_attempt": launchd_attempt,
        "launchd_exec": launchd_evidence,
        "probe": probe_match.group(0) if probe_match else None,
        "probe_build": probe_build,
        "linux_present": bool(re.search(
            r"Linux version|DISPLAY-PROBE|KEXEC-XNU|hackios-init|DIAG\s+boot",
            linux_text, re.I,
        )),
    }

    console_tail = _stream_tail(console_text or "", tail_count)
    diag_tail = _stream_tail(zones["diag_text"], tail_count)
    if mirror_valid:
        preferred_msgbuf_tail = msgbuf_mirror["tail"]
        preferred_msgbuf_source = "pmsg+0x40000 (XNU-MSGBUF v1)"
    else:
        preferred_msgbuf_tail = (msgbuf_doc["tail"]
                                 if msgbuf_doc and msgbuf_doc["tail_is_runtime"] else "")
        preferred_msgbuf_source = ("live msgbuf header" if preferred_msgbuf_tail else "none")
    doc = {
        "tool": "laguna-ramdump-reader-r2-v2",
        "dump": {
            "path": str(dump_path) if dump_path else None,
            "bytes": len(data), "physical_base": zones["dump_base"],
            "physical_base_origin": zones["dump_base_origin"],
            "ramoops_pa": ramoops_pa, "pmsg_pa": ramoops_pa + PMSG_OFF,
        },
        "symbolication": _symbolication_doc(symcache, symbol_error),
        "zones": {"console": zones["console"], "pmsg": pmsg},
        "xnu_msgbuf_mirror": msgbuf_mirror,
        "stages": stage_records,
        "msgbuf": msgbuf_doc,
        "msgbuf_scan": msgbuf_scan,
        "panic": panic,
        "panic_channels": panic_channels,
        "milestones": milestones,
        "evidence": evidence,
        "verdict": verdict,
        "logs": {
            "xnu_msgbuf_tail": preferred_msgbuf_tail,
            "xnu_msgbuf_source": preferred_msgbuf_source,
            "console_tail": console_tail,
            "diag_tail": diag_tail,
            "raw_first_stage_lines": raw_lines,
        },
        "warnings": warnings,
    }
    return doc


def print_human(doc, show_logs=False):
    print(doc["verdict"]["line"])
    dump = doc["dump"]
    base = dump["physical_base"]
    base_text = f"{base:#x}" if base is not None else "unknown"
    print(f"dump: {dump['bytes']:#x} bytes; base={base_text} "
          f"({dump['physical_base_origin'] or 'not inferred'})")
    sym = doc["symbolication"]
    if sym["available"]:
        print(f"symbols: {sym['mach']} sha256={sym['sha256']} "
              f"symbols={sym['symbols']} code={sym['code_symbols']} "
              f"link={sym['kernel_link_base']:#x}")
    else:
        print(f"symbols: unavailable ({sym.get('error') or 'no matching Mach-O'}); addresses only")

    console = doc["zones"]["console"]
    if console:
        pa = console.get("physical_address")
        print(f"console zone: file={console['file_offset']:#x} "
              f"PA={pa:#x} " if pa is not None else
              f"console zone: file={console['file_offset']:#x} PA=unknown ", end="")
        print(f"start={console['start']:#x} used={console['used']:#x} "
              f"stream={console.get('stream_bytes', 'truncated')} "
              f"via={','.join(console['discovery'])}")
    else:
        print("console zone: not found/valid")
    pmsg = doc["zones"]["pmsg"]
    if pmsg:
        pa = pmsg.get("physical_address")
        prefix = (f"pmsg zone: file={pmsg['file_offset']:#x} PA={pa:#x} "
                  if pa is not None else
                  f"pmsg zone: file={pmsg['file_offset']:#x} PA=unknown ")
        marker = pmsg["marker"]
        scratch = pmsg["marker_scratch"]
        print(prefix + f"marker={'yes' if marker.get('present') else 'no'} "
              f"valid={'yes' if marker.get('record_valid') else 'no'} "
              f"scratch={'yes' if scratch.get('present') else 'no'} "
              f"mirrors={len(pmsg['mirrors'])} diag={pmsg['diag_ring']['valid_records']} "
              f"msgbuf-mirror={'yes' if pmsg['msgbuf_mirror_header']['valid'] else 'no'} "
              f"stage={pmsg['stage_summary']['stage_name'] or 'none'}")
    else:
        print("pmsg zone: not found/valid")

    msgbuf = doc["msgbuf"]
    if msgbuf:
        print(f"XNU msgbuf: header={msgbuf['file_offset']:#x} "
              f"ring={msgbuf['ring_file_offset']:#x} size={msgbuf['size']:#x} "
              f"write={msgbuf['write_position']:#x} mapping={msgbuf['mapping']} "
              f"text={msgbuf['text_bytes']} B lines={msgbuf['log_lines']} "
              f"banner={'yes' if msgbuf['darwin_banner'] else 'no'} "
              f"static-image={'yes' if msgbuf['static_header_only'] else 'no'}")
    else:
        print(f"XNU msgbuf: no mappable ring ({doc['msgbuf_scan']})")

    mirror = doc["xnu_msgbuf_mirror"]
    if mirror and mirror["present"]:
        print(f"XNU-MSGBUF mirror: valid={'yes' if mirror['valid'] else 'no'} "
              f"seq={mirror['seq']} size={mirror['size']:#x} write={mirror['write_position']:#x} "
              f"total={mirror['stream_total']} tail={mirror['tail_offset']:#x}/"
              f"{mirror['tail_length']:#x} reconstructed={'yes' if mirror['tail_reconstructed'] else 'no'} "
              f"text={mirror.get('text_bytes', 0)} B")
        if not mirror["valid"]:
            print(f"  mirror error: {mirror.get('error')}")
    stages = doc["stages"]
    selected = stages["selected"]
    if selected:
        print(f"stage: {selected['stage_name']}({selected['stage']}) "
              f"source={selected['source']} seq={selected.get('seq')} "
              f"producer={selected.get('producer_name')} flags={selected.get('flags')} "
              f"note={selected.get('note')!r}")
        for rec in stages["slots"]:
            if rec["magic_present"]:
                print(f"  LGLW slot{rec['slot']}: valid={'yes' if rec['valid'] else 'no'} "
                      f"seq={rec.get('seq')} stage={rec.get('stage_name')} "
                      f"CRC={'ok' if rec.get('crc_ok') else 'bad'} "
                      f"DONE={'ok' if rec.get('commit_ok') else 'bad'}")
        if stages["compact"]["present"]:
            entries = ", ".join(f"{e['stage_name']}({e['stage']})"
                                for e in stages["compact"]["entries"])
            print(f"  compact stages: valid={'yes' if stages['compact']['valid'] else 'no'} {entries}")

    ev = doc["evidence"]
    print(f"evidence: Darwin={'yes' if ev['darwin_banner'] else 'no'} "
          f"BSD-root={'yes' if doc['milestones']['bsd_root_seen'] else 'no'} "
          f"root-mount={'yes' if doc['milestones']['root_mounted'] else 'no'} "
          f"launchd-attempt={'yes' if doc['milestones']['launchd_attempted'] else 'no'} "
          f"launchd-exec={'yes' if doc['milestones']['launchd_executed'] else 'no'} "
          f"dyld={ev['dyld_status']} probe-build={ev['probe_build'] or 'unknown'}")
    for key in ("darwin_banner", "bsd_root", "root_mount", "launchd_exec", "probe"):
        if ev.get(key):
            print(f"  {key}: {ev[key]}")

    panic = doc["panic"]
    if panic:
        print(f"panic: channel={panic['channel']} complete={panic['complete']} "
              f"message={panic.get('panic_message')}")
        if panic.get("kernel_text_base") is not None:
            print(f"  Kernel text base={panic['kernel_text_base']:#x} "
                  f"link={panic['kernel_link_base']:#x} slide={panic['slide']:#x}")
        caller = panic.get("panic_header")
        if caller:
            extra = f" [{caller['sym']}]" if caller.get("sym") else ""
            print(f"  {caller['line']}{extra}")
        for index, frame in enumerate(panic.get("frames", [])):
            line = f"  [{index:02d}] lr={frame['lr']:#018x}"
            if frame.get("fp") is not None:
                line += f" fp={frame['fp']:#018x}"
            if frame.get("unslid_lr") is not None:
                line += f" unslid={frame['unslid_lr']:#x}"
            if frame.get("lr_sym"):
                line += f" [{frame['lr_sym']}]"
            print(line)
    else:
        print("panic: no valid console/mirror/msgbuf panic record")

    for warning in doc["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    if show_logs:
        for title, key in (
            ("XNU msgbuf tail", "xnu_msgbuf_tail"),
            ("persistent_ram console tail", "console_tail"),
            ("pmsg DIAG ring tail", "diag_tail"),
        ):
            text = doc["logs"][key]
            if text:
                print(f"\n----- {title} -----\n{text}")
        if doc["logs"]["raw_first_stage_lines"]:
            print("\n----- raw first-stage evidence lines -----")
            for item in doc["logs"]["raw_first_stage_lines"]:
                print(f"{item['file_offset']:#x}: {item['line']}")


def _install_prz(data, off, linear, zone_size=CONSOLE_SIZE, start=None):
    """Selftest helper: create exact persistent_ram on-disk ring semantics."""
    capacity = zone_size - PRZ_HDR
    linear = linear[-capacity:]
    used = len(linear)
    full = used == capacity
    if start is None:
        start = 0 if full else used
    assert 0 <= start <= used
    if not full:
        assert start == used
    struct.pack_into("<III", data, off, PRZ_SIG, start, used)
    if full:
        ring = linear[used - start:] + linear[:used - start]
    else:
        ring = linear
    data[off + PRZ_HDR:off + PRZ_HDR + used] = ring
    return {"start": start, "used": used, "linear": linear}


def _install_marker(data, pmsg, boot_args=0x884444000):
    struct.pack_into("<IIQQQIIQ", data, pmsg + MARKER_OFF,
                     MARKER_MAGIC, 1, 0, boot_args, 0x883A752BC,
                     HANDOFF_MAGIC, 0, boot_args)
    rec = bytearray(0x40)
    rec[:4] = b"M1\0\0"
    struct.pack_into("<I", rec, 4, MARKER_MAGIC)
    struct.pack_into("<QQQ", rec, 8, 0, boot_args, 0x883A752BC)
    struct.pack_into("<I", rec, 32, HANDOFF_MAGIC)
    struct.pack_into("<Q", rec, 40, boot_args)
    data[pmsg + SCRATCH_OFF:pmsg + SCRATCH_OFF + 0x40] = rec
    struct.pack_into("<I", data, pmsg + SCRATCH_OFF + 0x40, 7)


def _install_diag(data, pmsg, text, seq=1):
    payload = text.encode("ascii", "replace")[:DIAG_REC_SIZE - 32]
    rec = bytearray(DIAG_REC_SIZE)
    struct.pack_into("<8s6I", rec, 0, b"DIAGREC\0", seq, len(payload),
                     ddp.crc32_ieee(payload), 7, 0, 0)
    rec[32:32 + len(payload)] = payload
    off = pmsg + DIAG_RING_OFF + ((seq - 1) % DIAG_REC_COUNT) * DIAG_REC_SIZE
    data[off:off + DIAG_REC_SIZE] = rec
    struct.pack_into("<I", data, pmsg + DIAG_COUNT_OFF, seq)


def _paniclog(symcache, slide, raw_only=False):
    capture = _symbol_value(symcache, "laguna_panicdump_capture")
    handle = _symbol_value(symcache, "handle_debugger_trap")
    base = symcache.link_base + slide
    lines = [
        f"panic(cpu 0 caller {capture + slide:#018x}): synthetic ramdump panic",
        "Debugger message: panic",
        f"Kernel text base:  {base:#018x}",
        "Kernel version: Darwin Kernel Version 22.6.0: synthetic; root:xnu-8796.141.3",
    ]
    if not raw_only:
        lines.append(f"\t\t  lr: {capture + 0x40 + slide:#018x}  fp: 0xfffffff65066f000")
    lines.append(f"lr: {handle + 0x524 + slide:#018x}")
    return "\n".join(lines) + "\n"


def _install_msgbuf(data, head, symcache, log, slide=0x12345000,
                    size=0x8000, wrap=False, static_only=False):
    msgbuf_va = _symbol_value(symcache, "msgbuf")
    smsg_va = _symbol_value(symcache, "smsg_bufc")
    ring = head + smsg_va - msgbuf_va
    raw_log = log.encode("utf-8")[-size:]
    if wrap:
        assert len(raw_log) == size
        write = 0x321
        physical = raw_log[size - write:] + raw_log[:size - write]
    else:
        write = 0 if static_only else len(raw_log)
        physical = raw_log
    data[ring:ring + len(physical)] = physical
    pointer = smsg_va + (0 if static_only else slide)
    struct.pack_into("<4IQ", data, head, MSG_MAGIC, size, write, 0, pointer)
    return {"header": head, "ring": ring, "write": write,
            "linear": raw_log, "size": size}


def _install_r4_mirror(data, pmsg, stream, symcache, seq=1, size=0x10000,
                       flags=0, slide=0x12345000):
    raw = stream.encode("utf-8") if isinstance(stream, str) else bytes(stream)
    total = len(raw)
    tail = raw[-min(total, size):] if total else b""
    tail_off = (total - len(tail)) % size
    write = total % size
    ring = pmsg + MSGBUF_MIRROR_OFF + MSGBUF_MIRROR_HEADER.size
    # Poison unused bytes to prove reconstruction is bounded to tail_len.
    data[ring:ring + size] = b"\xA5" * size
    if tail:
        if tail_off + len(tail) <= size:
            data[ring + tail_off:ring + tail_off + len(tail)] = tail
        else:
            lead = size - tail_off
            data[ring + tail_off:ring + size] = tail[:lead]
            data[ring:ring + len(tail) - lead] = tail[lead:]
    msgbuf_va = _symbol_value(symcache, "msgbuf") + slide
    smsg_va = _symbol_value(symcache, "smsg_bufc") + slide
    MSGBUF_MIRROR_HEADER.pack_into(
        data, pmsg + MSGBUF_MIRROR_OFF,
        MSGBUF_MIRROR_MAGIC.ljust(16, b"\0"), seq, flags,
        msgbuf_va, smsg_va, size, write, total, tail_off, len(tail),
    )
    return {"seq": seq, "size": size, "write": write, "total": total,
            "tail_off": tail_off, "tail_len": len(tail), "tail": tail,
            "ring": ring, "msgbufp": msgbuf_va, "msg_bufc": smsg_va}


def _install_compact_stages(data, pmsg, stages):
    words = list(stages)[:12]
    words += [0] * (12 - len(words))
    struct.pack_into("<12I", data, pmsg + STAGE_COMPACT_OFF, *words)
    return words


def _lglw_record(seq, stage, producer, flags=0, detail=(0, 0, 0, 0), note=b""):
    rec = bytearray(LGLW_SLOT_SIZE)
    struct.pack_into("<IHHIIII", rec, 0, LGLW_MAGIC, 1, LGLW_SLOT_SIZE,
                     seq, stage, producer, flags)
    struct.pack_into("<4Q", rec, 0x18, *detail)
    rec[0x38:0x38 + min(63, len(note))] = note[:63]
    struct.pack_into("<I", rec, 0x78, ddp.crc32_ieee(rec[:0x78]))
    struct.pack_into("<I", rec, 0x7C, LGLW_COMMIT)
    return rec


def _r4_zone(console_text=""):
    data = bytearray(0x400000)
    _install_prz(data, 0, console_text.encode("utf-8"))
    _install_prz(data, PMSG_OFF, b"")
    return data


def _synthetic_dump(symcache, kind, prefix=0x300000, wrap=False):
    data = bytearray(prefix + 0x800000)
    console = prefix
    pmsg = prefix + PMSG_OFF
    slide = 0x12345000
    probe = (
        "Linux version 7.3-next synthetic blazer\n"
        "DISPLAY-PROBE: v1 start build=dp-0123456789abcdef0123 kmsg_fd=3\n"
        "DISPLAY-PROBE: summary scanout=dead fb=0x0 layers_active=0/12\n"
        "KEXEC-XNU: kexec_load(entry=0x883a752bc) result=0\n"
    )
    console_text = "ABL synthetic noise\n"
    paniclog = None
    if kind in ("probe", "panic"):
        console_text += probe
    if kind == "panic":
        paniclog = _paniclog(symcache, slide)
        record = (
            f"{ddp.BEGIN} (laguna141 xnu-8796.141.3) -----\n"
            "panic message: synthetic ramdump root failure\n" + paniclog +
            f"{ddp.MSGBUF_TAG} (last 4096 bytes) -----\n"
            "[    0.100000]: LAGUNA PANICDUMP: armed\n"
            f"{ddp.END} -----\n"
        )
        console_text += record + "ABL next boot\n"
        mirror = ddp.build_synthetic_mirror(
            "panic message: synthetic ramdump root failure", paniclog, seq=9
        )
        data[pmsg + MIRROR_OFF:pmsg + MIRROR_OFF + len(mirror)] = mirror
        bad = bytearray(ddp.build_synthetic_mirror("panic message: corrupt", "old", seq=2))
        bad[20] ^= 0xFF
        bad_off = pmsg + MIRROR_OFF + 0x2040
        data[bad_off:bad_off + len(bad)] = bad
    if kind in ("xnu", "panic"):
        _install_marker(data, pmsg)
    if kind in ("probe", "panic"):
        _install_diag(data, pmsg, "DIAG boot=7 scanout=dead fb=0x0 kexec=1\n" + probe)

    meta = {"console": console, "pmsg": pmsg, "paniclog": paniclog}
    if wrap:
        # Fill the ring to exercise start != 0 and size == capacity.
        capacity = CONSOLE_SIZE - PRZ_HDR
        encoded = console_text.encode("utf-8")
        filler = ("[    0.000001]: synthetic older console line\n" *
                  ((capacity // 44) + 2)).encode("ascii")
        linear = (filler + encoded)[-capacity:]
        meta["prz"] = _install_prz(data, console, linear, start=0x345)
    else:
        meta["prz"] = _install_prz(data, console, console_text.encode("utf-8"))

    xnu_log = ""
    if kind in ("darwin", "root", "root-attempt", "launchd", "panic", "msgbuf-panic"):
        xnu_log = (
            "Darwin Kernel Version 22.6.0: synthetic; root:xnu-8796.141.3\n"
            "[    0.010000]: LAGUNA PANICDUMP: armed\n"
        )
    if kind in ("root", "root-attempt", "launchd"):
        xnu_log += "BSD root: md0, major 1, minor 0\nhfs: mounted iOSRoot on device b(1, 0)\n"
    if kind == "root-attempt":
        xnu_log += "load_init_program: attempting to load /sbin/launchd\n"
    if kind == "launchd":
        xnu_log += "load_init_program: attempting to load /sbin/launchd\nexec launchd\n"
    if kind == "msgbuf-panic":
        xnu_log += _paniclog(symcache, slide)

    if xnu_log:
        head = prefix + 0x500000
        # A static initialized header is a decoy, not proof of execution.
        _install_msgbuf(data, head - 0x10000, symcache, "", static_only=True)
        if wrap:
            size = 0x8000
            filler = "[    0.001000]: synthetic msgbuf filler\n"
            encoded = (filler * (size // len(filler) + 2) + xnu_log).encode("utf-8")[-size:]
            meta["msgbuf"] = _install_msgbuf(data, head, symcache, encoded.decode("utf-8"),
                                             slide=slide, size=size, wrap=True)
        else:
            meta["msgbuf"] = _install_msgbuf(data, head, symcache, xnu_log, slide=slide)
        meta["xnu_log"] = xnu_log
    return data, meta


def selftest(mach_path=None):
    checks = 0
    failures = 0

    def ck(name, condition, detail=""):
        nonlocal checks, failures
        checks += 1
        good = bool(condition)
        if not good:
            failures += 1
        print(f"selftest/{checks:02d} {name}: {'PASS' if good else 'FAIL'}"
              f"{'' if good or not detail else ' — ' + str(detail)}")

    path = mach_path or str(MACH0128)
    try:
        sc = ddp.MachSymCache(path)
        ck("exact 0128 payload and 52,004-symbol LC_SYMTAB",
           sc.sha256 == MACH0128_SHA256 and len(sc.syms) == 52004 and
           len(sc.code_syms) == 27059,
           f"sha={sc.sha256} symbols={len(sc.syms)} code={len(sc.code_syms)}")
    except (OSError, ValueError) as error:
        ck("load real 0128 Mach-O", False, error)
        print(f"selftest FAIL ({checks - failures}/{checks})")
        return 1

    # Small, exact persistent_ram semantics tests: partial start==size and a
    # wrapped full ring.  These catch the old start:start+size mis-decoder.
    tiny = bytearray(0x2000)
    partial = b"ABL\nDISPLAY-PROBE: partial\n"
    _install_prz(tiny, 0, partial, zone_size=0x1000)
    got = _linearize_prz(tiny, 0, 0x1000)
    ck("persistent_ram partial linearization",
       got["start"] == len(partial) and got["stream_raw"] == partial)
    full_linear = bytes((i * 37 + 11) & 0xFF for i in range(0x1000 - PRZ_HDR))
    _install_prz(tiny, 0, full_linear, zone_size=0x1000, start=0x123)
    got = _linearize_prz(tiny, 0, 0x1000)
    ck("persistent_ram wrapped full-ring linearization",
       got["start"] == 0x123 and got["stream_raw"] == full_linear)

    classes = {}
    docs = {}

    full, full_meta = _synthetic_dump(sc, "panic", wrap=True)
    doc = analyze(full, symcache=sc)
    docs["PANICKED"] = doc
    classes[doc["verdict"]["class"]] = True
    ck("offset signature finds console+pmsg and infers dump base",
       doc["zones"]["console"]["file_offset"] == full_meta["console"] and
       doc["zones"]["pmsg"]["file_offset"] == full_meta["pmsg"] and
       doc["dump"]["physical_base"] == RAMOOPS_PA - full_meta["console"])
    ck("physical-layout mode agrees with signature mode",
       analyze(full, symcache=sc, dump_base=RAMOOPS_PA - full_meta["console"])
       ["zones"]["pmsg"]["file_offset"] == full_meta["pmsg"])
    ck("wrapped console complete panic record survives next-boot noise",
       doc["panic"] and doc["panic"]["channel"] == "console" and
       doc["panic"]["complete"] and doc["panic"]["panic_message"] ==
       "synthetic ramdump root failure")
    ck("raw lr list + nonzero slide + real 0128 symbolication",
       len(doc["panic"]["frames"]) == 2 and
       doc["panic"]["frames"][1].get("fp") is None and
       doc["panic"]["frames"][0].get("lr_sym") == "laguna_panicdump_capture+0x40" and
       doc["panic"]["frames"][1].get("lr_sym") == "handle_debugger_trap+0x524" and
       doc["panic"]["frames"][1].get("unslid_lr") == 0xFFFFFFF0072A2460,
       repr(doc["panic"]["frames"]))
    generic_doc = _parse_panic_text(
        "panic message: panic\n" + _paniclog(sc, 0x12345000), sc,
        "xnu-msgbuf", None,
    )
    ck("detailed panic(cpu) reason supersedes generic 'panic' string",
       generic_doc["panic_message"] == "synthetic ramdump panic")
    ck("valid mirror + corrupt mirror both accounted",
       doc["panic_channels"]["mirror_records_valid"] == 1 and
       doc["panic_channels"]["mirror_records_corrupt"] == 1)
    ck("msgbuf outside pmsg found by Mach symbol delta",
       doc["msgbuf"] and
       doc["msgbuf"]["file_offset"] == full_meta["msgbuf"]["header"] and
       doc["msgbuf"]["ring_file_offset"] == full_meta["msgbuf"]["ring"] and
       doc["msgbuf"]["mapping"] == "mach-symbol-relative" and
       doc["msgbuf"]["darwin_banner"])
    ck("wrapped msgbuf tail is oldest-to-newest",
       doc["msgbuf"]["tail"].endswith(full_meta["xnu_log"]) and
       "synthetic msgbuf filler" in doc["msgbuf"]["tail"])
    ck("full dump milestones and verdict",
       doc["milestones"]["xnu_entered"] and doc["milestones"]["probe_ran"] and
       doc["milestones"]["panicked"] and doc["verdict"]["class"] == "PANICKED" and
       doc["verdict"]["line"].count("VERDICT:") == 1)

    for kind, expected in (
        ("empty", "NOTHING"), ("probe", "PROBE-RAN"),
        ("xnu", "XNU-ENTERED"), ("darwin", "XNU-ENTERED"),
        ("root", "ROOT-MOUNTED"), ("root-attempt", "ROOT-MOUNTED"),
        ("launchd", "LAUNCHD-EXEC"),
    ):
        if kind == "empty":
            synthetic = bytearray(0x4000)
        else:
            synthetic, _ = _synthetic_dump(sc, kind, prefix=0x100000)
        current = analyze(synthetic, symcache=sc)
        classes[current["verdict"]["class"]] = True
        docs[kind] = current
        ck(f"verdict class {kind} -> {expected}",
           current["verdict"]["class"] == expected,
           current["verdict"]["line"])

    ck("launchd attempt alone is not exec",
       docs["root-attempt"]["milestones"]["launchd_attempted"] and
       not docs["root-attempt"]["milestones"]["launchd_executed"])
    ck("root mount needs a mount success line",
       docs["root"]["milestones"]["bsd_root_seen"] and
       docs["root"]["milestones"]["root_mounted"])
    ck("probe build fingerprint recovered",
       docs["probe"]["evidence"]["probe_build"] == "dp-0123456789abcdef0123" and
       docs["probe"]["zones"]["pmsg"]["diag_ring"]["valid_records"] == 1)
    ck("static msgbuf header alone is not XNU execution",
       docs["empty"]["verdict"]["class"] == "NOTHING" and
       not docs["empty"]["milestones"]["xnu_entered"])

    direct, _ = _synthetic_dump(sc, "msgbuf-panic", prefix=0x100000)
    direct_doc = analyze(direct, symcache=sc)
    ck("msgbuf-only panic and raw lr decode",
       direct_doc["verdict"]["class"] == "PANICKED" and
       direct_doc["panic"]["channel"] == "xnu-msgbuf" and
       direct_doc["panic"]["frames"][-1].get("lr_sym") == "handle_debugger_trap+0x524")

    corrupt = bytearray(0x600000)
    pmsg = 0x200000
    _install_prz(corrupt, 0, b"ABL only\n")
    bad = bytearray(ddp.build_synthetic_mirror("panic message: untrusted", "lr: 0xdead", 4))
    bad[45] ^= 0x80
    corrupt[pmsg + MIRROR_OFF:pmsg + MIRROR_OFF + len(bad)] = bad
    corrupt_doc = analyze(corrupt, symcache=sc)
    ck("CRC-bad mirror is reported, never believed",
       corrupt_doc["panic"] is None and
       corrupt_doc["panic_channels"]["mirror_records_corrupt"] == 1 and
       not corrupt_doc["milestones"]["panicked"])

    mirror_only = bytearray(0x400000)
    mirror_pmsg = 0x200000
    mirror_log = _paniclog(sc, 0x12345000)
    mirror = ddp.build_synthetic_mirror(
        "panic message: mirror-only synthetic panic", mirror_log, seq=11
    )
    mirror_only[mirror_pmsg + MIRROR_OFF:mirror_pmsg + MIRROR_OFF + len(mirror)] = mirror
    mirror_doc = analyze(mirror_only, symcache=sc)
    ck("mirror-only signature finds raw pmsg and symbolicates frames",
       mirror_doc["zones"]["pmsg"]["file_offset"] == mirror_pmsg and
       mirror_doc["panic"]["channel"] == "mirror" and
       mirror_doc["panic"]["frames"][-1].get("lr_sym") == "handle_debugger_trap+0x524" and
       mirror_doc["zones"]["pmsg"]["mirrors"][0]["zone_offset"] == MIRROR_OFF)

    raw_probe = bytearray(0x100000)
    raw_line = b"DISPLAY-PROBE: v1 start build=dp-fedcba98765432100123 kmsg_fd=3\n"
    raw_probe[0x80000:0x80000 + len(raw_line)] = raw_line
    raw_probe_doc = analyze(raw_probe, symcache=sc)
    ck("raw first-stage log survives missing/erased zone headers",
       raw_probe_doc["verdict"]["class"] == "PROBE-RAN" and
       raw_probe_doc["evidence"]["probe_build"] == "dp-fedcba98765432100123" and
       raw_probe_doc["logs"]["raw_first_stage_lines"])

    truncated_console = bytearray(CONSOLE_SIZE)
    _install_prz(truncated_console, 0,
                 b"DISPLAY-PROBE: v1 start build=dp-0123456789abcdef0123\n")
    truncated_doc = analyze(truncated_console, symcache=sc)
    ck("console-only truncated dump is not mislabeled as pmsg",
       truncated_doc["zones"]["console"]["file_offset"] == 0 and
       truncated_doc["zones"]["pmsg"] is None and
       truncated_doc["verdict"]["class"] == "PROBE-RAN")

    mapped, mapped_meta = _synthetic_dump(sc, "root", prefix=0x100000)
    explicit_dump_base = RAMOOPS_PA - 0x100000
    explicit_virt = 0xFFFFFFF004000000
    explicit_phys = ((explicit_dump_base + mapped_meta["msgbuf"]["ring"]) -
                     (_symbol_value(sc, "smsg_bufc") - explicit_virt))
    mapped_doc = analyze(mapped, symcache=None, dump_base=explicit_dump_base,
                         slide_override=0x12345000,
                         kernel_map=(explicit_virt, explicit_phys))
    ck("explicit direct map extracts msgbuf without a symbol table",
       mapped_doc["msgbuf"] and
       mapped_doc["msgbuf"]["mapping"] == "explicit-direct-map" and
       mapped_doc["msgbuf"]["file_offset"] == mapped_meta["msgbuf"]["header"] and
       mapped_doc["verdict"]["class"] == "ROOT-MOUNTED")

    static = bytearray(0x100000)
    _install_msgbuf(static, 0x40000, sc,
                    "Darwin Kernel Version fake static string\n"
                    "LAGUNA PANICDUMP: armed\n"
                    "BSD root: md0\nhfs: mounted iOSRoot on device b(1, 0)\n",
                    static_only=True)
    static_doc = analyze(static, symcache=sc)
    ck("initialized static header/fake log strings cannot manufacture liveness",
       static_doc["msgbuf"] is not None and static_doc["msgbuf"]["static_header_only"] and
       not static_doc["logs"]["xnu_msgbuf_tail"] and
       static_doc["verdict"]["class"] == "NOTHING")

    stray_marker = bytearray(0x100000)
    stray_marker[0x50000:0x50004] = MARKER_BYTES
    stray_doc = analyze(stray_marker, symcache=sc)
    ck("unanchored marker word is not a pmsg zone or XNU entry",
       stray_doc["zones"]["pmsg"] is None and
       not stray_doc["milestones"]["xnu_entered"] and
       stray_doc["verdict"]["class"] == "NOTHING")

    laguna_only = bytearray(0x100000)
    _install_msgbuf(laguna_only, 0x40000, sc,
                    "[    8.000000]: LAGUNA PANICDUMP: armed\n")
    laguna_doc = analyze(laguna_only, symcache=sc)
    ck("LAGUNA-only runtime msgbuf tail proves XNU progress",
       laguna_doc["evidence"]["msgbuf_runtime_log"] and
       laguna_doc["verdict"]["class"] == "XNU-ENTERED")

    # R4 always-on mirror: wrapped content, exact tail geometry, all milestone
    # channels, and the compact stage window at raw file offset 0x201050.
    r4_linux = (
        "DISPLAY-PROBE: v1 start build=dp-0123456789abcdef0123\n"
        "KEXEC-XNU: kexec_load(entry=0x883a752bc) result=0 segments=14\n"
    )
    r4 = _r4_zone(r4_linux)
    r4_stream = ("[    0.000001]: old wrapped mirror line\n" * 6000 +
                 "Darwin Kernel Version 22.6.0: R4 synthetic\n"
                 "[    0.100000]: LAGUNA PANICDUMP: armed\n"
                 "BSD root: md0, major 1, minor 0\n"
                 "hfs: mounted iOSRoot on device b(1, 0)\n"
                 "exec launchd\n"
                 "shared_region: synthetic[1(launchd)] check_np(\n")
    r4_meta = _install_r4_mirror(r4, PMSG_OFF, r4_stream, sc, seq=7)
    _install_compact_stages(r4, PMSG_OFF, list(range(1, 12)))
    parsed_r4, parsed_r4_text = _parse_msgbuf_mirror(
        r4[PMSG_OFF:], tail_count=len(r4_meta["tail"]) + 1
    )
    ck("R4 mirror wrapped tail reconstructs exact bytes at file 0x240000",
       parsed_r4["valid"] and parsed_r4["tail_reconstructed"] and
       parsed_r4["tail_offset_matches_stream"] and parsed_r4["write_matches_stream"] and
       parsed_r4_text == r4_meta["tail"].decode("utf-8") and
       PMSG_OFF + MSGBUF_MIRROR_OFF == 0x240000 and
       parsed_r4["seq_nonzero"] and parsed_r4["seq_monotonicity"] == "no-predecessor-in-dump")
    max_zone = _r4_zone()
    max_stream = (b"x" * (MSGBUF_MIRROR_RING_MAX + 17) +
                  b"\nDarwin Kernel Version max-size mirror\n")
    max_meta = _install_r4_mirror(max_zone, PMSG_OFF, max_stream, sc, seq=8,
                                  size=MSGBUF_MIRROR_RING_MAX)
    max_parsed, max_text = _parse_msgbuf_mirror(max_zone[PMSG_OFF:], tail_count=100)
    ck("R4 mirror supports wrapped full 1MiB MAX_MSG_BSIZE ring",
       max_parsed["valid"] and max_parsed["tail_reconstructed"] and
       max_parsed["size"] == MSGBUF_MIRROR_RING_MAX and
       max_parsed["reconstructed_bytes"] == MSGBUF_MIRROR_RING_MAX and
       len(max_meta["tail"]) == MSGBUF_MIRROR_RING_MAX and
       max_text.endswith("Darwin Kernel Version max-size mirror\n") and
       max_parsed["write_matches_stream"])

    r4_doc = analyze(r4, symcache=sc, dump_base=RAMOOPS_PA)
    ck("R4 mirror + all compact stages unify root/launchd/dyld verdict",
       r4_doc["verdict"]["class"] == "LAUNCHD-EXEC" and
       r4_doc["stages"]["stage"] == 11 and
       r4_doc["stages"]["compact"]["offset"] == STAGE_COMPACT_OFF and
       PMSG_OFF + STAGE_COMPACT_OFF == 0x201050 and
       r4_doc["milestones"]["root_mounted"] and
       r4_doc["milestones"]["launchd_executed"] and
       r4_doc["milestones"]["dyld_status"] == "shared-region" and
       r4_doc["logs"]["xnu_msgbuf_source"].startswith("pmsg+0x40000") and
       not r4_doc["milestones"]["silent_pre_console"])

    r4_panic = _r4_zone(r4_linux)
    _install_r4_mirror(
        r4_panic, PMSG_OFF,
        "[    0.000001]: panic mirror wrap filler\n" * 5000 + _paniclog(sc, 0x12345000),
        sc, seq=21,
    )
    _install_compact_stages(r4_panic, PMSG_OFF, [2, 3, 4, 5, 6, 12])
    r4_panic_doc = analyze(r4_panic, symcache=sc, dump_base=RAMOOPS_PA)
    ck("R4 mirror panic symbolicates and lone PANIC implies no later milestones",
       r4_panic_doc["verdict"]["class"] == "PANICKED" and
       r4_panic_doc["panic"]["channel"] == "xnu-msgbuf-mirror" and
       r4_panic_doc["panic"]["frames"][0]["lr_sym"] == "laguna_panicdump_capture+0x40" and
       r4_panic_doc["panic"]["frames"][1]["lr_sym"] == "handle_debugger_trap+0x524" and
       not r4_panic_doc["milestones"]["root_mounted"] and
       not r4_panic_doc["milestones"]["launchd_executed"] and
       r4_panic_doc["milestones"]["dyld_status"] == "not-seen")

    for latest in range(1, 12):
        staged = _r4_zone()
        _install_compact_stages(staged, PMSG_OFF, list(range(1, latest + 1)))
        staged_doc = analyze(staged, symcache=sc, dump_base=RAMOOPS_PA)
        expected_dyld = ("shared-region" if latest == 11 else
                         "exec-activate" if latest == 10 else "not-seen")
        ck(f"compact stage {latest} {STAGE_NAMES[latest]} localizes silent death",
           staged_doc["verdict"]["class"] == "SILENT-PRE-CONSOLE" and
           staged_doc["stages"]["stage"] == latest and
           staged_doc["milestones"]["stage_of_death"] == STAGE_NAMES[latest] and
           staged_doc["milestones"]["xnu_entered"] == (latest >= 6) and
           staged_doc["milestones"]["probe_ran"] == (latest >= 2) and
           staged_doc["milestones"]["root_mounted"] == (latest >= 8) and
           staged_doc["milestones"]["launchd_attempted"] == (latest >= 9) and
           staged_doc["milestones"]["launchd_executed"] == (latest >= 10) and
           staged_doc["milestones"]["dyld_status"] == expected_dyld and
           staged_doc["milestones"]["silent_pre_console"] and
           not staged_doc["milestones"]["nothing"],
           staged_doc["verdict"]["line"])

    stage_panic = _r4_zone()
    _install_compact_stages(stage_panic, PMSG_OFF, list(range(1, 13)))
    stage_panic_doc = analyze(stage_panic, symcache=sc, dump_base=RAMOOPS_PA)
    ck("stage-only PANIC keeps explicit earlier root/launchd/dyld evidence",
       stage_panic_doc["verdict"]["class"] == "PANICKED" and
       stage_panic_doc["panic"]["channel"] == "stage-breadcrumb" and
       not stage_panic_doc["panic"]["frames"] and
       stage_panic_doc["milestones"]["root_mounted"] and
       stage_panic_doc["milestones"]["launchd_executed"] and
       stage_panic_doc["milestones"]["dyld_status"] == "shared-region")

    empty_r4_doc = analyze(_r4_zone(), symcache=sc, dump_base=RAMOOPS_PA)
    ck("empty mirror + empty breadcrumbs remain NOTHING",
       empty_r4_doc["verdict"]["class"] == "NOTHING" and
       not empty_r4_doc["xnu_msgbuf_mirror"]["present"] and
       not empty_r4_doc["stages"]["present"] and
       not empty_r4_doc["milestones"]["silent_pre_console"])

    bad_stages = _r4_zone()
    _install_compact_stages(bad_stages, PMSG_OFF, [1, 3, 2])
    bad_stages_doc = analyze(bad_stages, symcache=sc, dump_base=RAMOOPS_PA)
    ck("nonmonotonic compact breadcrumbs cannot advance a verdict",
       bad_stages_doc["stages"]["compact"]["present"] and
       not bad_stages_doc["stages"]["compact"]["valid"] and
       bad_stages_doc["stages"]["selected"] is None and
       bad_stages_doc["verdict"]["class"] == "NOTHING")

    bad_seq = _r4_zone()
    _install_r4_mirror(bad_seq, PMSG_OFF, "Darwin Kernel Version invalid seq\n", sc, seq=0)
    bad_seq_doc = analyze(bad_seq, symcache=sc, dump_base=RAMOOPS_PA)
    ck("mirror seq zero is rejected and its text is not evidence",
       bad_seq_doc["xnu_msgbuf_mirror"]["present"] and
       not bad_seq_doc["xnu_msgbuf_mirror"]["valid"] and
       "seq is zero" in bad_seq_doc["xnu_msgbuf_mirror"]["error"] and
       bad_seq_doc["verdict"]["class"] == "NOTHING")

    bad_tail = _r4_zone()
    bad_tail_meta = _install_r4_mirror(
        bad_tail, PMSG_OFF, "Darwin Kernel Version bad tail geometry\n", sc, seq=2
    )
    struct.pack_into("<I", bad_tail, PMSG_OFF + MSGBUF_MIRROR_OFF + 0x38,
                     (bad_tail_meta["tail_off"] + 1) % bad_tail_meta["size"])
    bad_tail_doc = analyze(bad_tail, symcache=sc, dump_base=RAMOOPS_PA)
    ck("mirror tail/stream geometry mismatch is rejected",
       bad_tail_doc["xnu_msgbuf_mirror"]["present"] and
       not bad_tail_doc["xnu_msgbuf_mirror"]["valid"] and
       "tail offset" in bad_tail_doc["xnu_msgbuf_mirror"]["error"] and
       bad_tail_doc["verdict"]["class"] == "NOTHING")

    # Consume R3's independently generated, official valid/torn fixtures.
    with tempfile.TemporaryDirectory(prefix="laguna-r3-r2-fixtures-") as tmp:
        generator = FIXTURE_GENERATOR
        proc = subprocess.run([sys.executable, str(generator), tmp], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        valid_path = Path(tmp) / "lglw-valid-kernel-log.bin"
        torn_path = Path(tmp) / "lglw-torn-kernel-log.bin"
        valid_doc = analyze(valid_path.read_bytes(), symcache=sc, dump_base=RAMOOPS_PA)
        torn_doc = analyze(torn_path.read_bytes(), symcache=sc, dump_base=RAMOOPS_PA)
        ck("R3 official fixture selects seq12/PANIC slot0 and retains both slots",
           proc.returncode == 0 and valid_doc["stages"]["stage"] == 12 and
           valid_doc["stages"]["selected_lglw_slot"] == 0 and
           valid_doc["stages"]["valid_lglw_slots"] == 2 and
           valid_doc["stages"]["slots"][0]["offset"] == 0x1100 and
           valid_doc["stages"]["slots"][1]["offset"] == 0x1180 and
           valid_doc["verdict"]["class"] == "PANICKED")
        ck("R3 torn fixture rejects DONE=0 and falls back to seq11 slot1",
           torn_doc["stages"]["stage"] == 11 and
           torn_doc["stages"]["selected_lglw_slot"] == 1 and
           torn_doc["stages"]["valid_lglw_slots"] == 1 and
           torn_doc["stages"]["invalid_lglw_candidates"] == 1 and
           torn_doc["stages"]["slots"][0]["torn"] and
           torn_doc["verdict"]["class"] == "SILENT-PRE-CONSOLE" and
           torn_doc["milestones"]["dyld_status"] == "shared-region")

    stale = _r4_zone()
    stale[PMSG_OFF + LGLW_OFF:PMSG_OFF + LGLW_OFF + LGLW_SLOT_SIZE] = \
        _lglw_record(20, 2, 2, note=b"new boot linux")
    stale[PMSG_OFF + LGLW_OFF + LGLW_SLOT_SIZE:
          PMSG_OFF + LGLW_OFF + 2 * LGLW_SLOT_SIZE] = \
        _lglw_record(19, 11, 5, note=b"old boot dyld")
    stale_doc = analyze(stale, symcache=sc, dump_base=RAMOOPS_PA)
    ck("older LGLW slot cannot leak prior-boot milestones into newer KERNEL_UP",
       stale_doc["stages"]["stage"] == 2 and
       stale_doc["stages"]["valid_lglw_slots"] == 2 and
       not stale_doc["milestones"]["xnu_entered"] and
       not stale_doc["milestones"]["root_mounted"] and
       not stale_doc["milestones"]["launchd_executed"] and
       stale_doc["milestones"]["dyld_status"] == "not-seen" and
       stale_doc["verdict"]["class"] == "SILENT-PRE-CONSOLE")

    expected_classes = {"NOTHING", "PROBE-RAN", "XNU-ENTERED", "ROOT-MOUNTED",
                        "LAUNCHD-EXEC", "PANICKED"}
    ck("all six verdict classes decoded", expected_classes <= set(classes),
       repr(sorted(classes)))

    # One actual CLI/mmap/JSON round trip, still using only a synthetic file.
    with tempfile.TemporaryDirectory(prefix="laguna-ramdump-selftest-") as tmp:
        dump_path = Path(tmp) / "synthetic.bin"
        dump_path.write_bytes(full)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(dump_path),
             "--json", "--mach", sc.path],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        try:
            cli_doc = json.loads(proc.stdout) if proc.returncode == 0 else None
        except json.JSONDecodeError:
            cli_doc = None
        ck("CLI read-only mmap + JSON round trip",
           proc.returncode == 0 and cli_doc and
           cli_doc["verdict"]["class"] == "PANICKED" and
           cli_doc["panic"]["frames"][1].get("lr_sym") == "handle_debugger_trap+0x524",
           proc.stderr[-500:])

    print(f"selftest {'PASS' if failures == 0 else 'FAIL'} "
          f"({checks - failures}/{checks})")
    return 0 if failures == 0 else 1


def _default_mach():
    if MACH0128.exists():
        return str(MACH0128)
    return ddp.default_mach_path()


def _parse_kernel_map(value):
    try:
        virt, phys = re.split(r"[:,]", value, maxsplit=1)
        return int(virt, 0), int(phys, 0)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("expected VIRT:PHYS (integers accept 0x)") from error


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?", help="raw host-side DRAM dump (never modified)")
    ap.add_argument("--mach", help="matching Mach-O for unsliding/symbolication")
    ap.add_argument("--dump-base", "--phys-base", type=_int,
                    help="physical address represented by dump file offset zero")
    ap.add_argument("--ramoops-pa", type=_int, default=RAMOOPS_PA,
                    help="console-zone PA (default %(default)#x); pmsg is +0x200000")
    slide_group = ap.add_mutually_exclusive_group()
    slide_group.add_argument("--kernel-text-base", type=_int,
                             help="override runtime kernel __TEXT base for raw frames")
    slide_group.add_argument("--slide", type=_int,
                             help="override KASLR slide for raw frames/msgbuf mapping")
    ap.add_argument("--kernel-map", type=_parse_kernel_map,
                    help="optional direct-map fallback VIRT_BASE:PHYS_BASE")
    ap.add_argument("--tail", type=_int, default=16384,
                    help="log tail characters retained/printed (default %(default)s)")
    ap.add_argument("--logs", action="store_true", help="print recovered log tails")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--selftest", action="store_true", help="run host-only synthetic tests")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(args.mach)
    if not args.dump:
        ap.error("a DRAM dump is required unless --selftest is used")
    if args.tail < 0:
        ap.error("--tail must be nonnegative")

    mach = args.mach or _default_mach()
    symcache = None
    symbol_error = None
    try:
        symcache = ddp.MachSymCache(mach)
        if symcache.sha256 != MACH0128_SHA256:
            symbol_error = ("selected Mach-O is not the exact 0128 payload; "
                            "it must still match the dumped kernel")
    except (OSError, ValueError) as error:
        symbol_error = str(error)

    path = Path(args.dump)
    try:
        with path.open("rb") as source:
            if os.fstat(source.fileno()).st_size == 0:
                doc = analyze(b"", symcache, symbol_error, path, args.dump_base,
                              args.ramoops_pa, args.tail, args.kernel_text_base,
                              args.slide, args.kernel_map)
            else:
                with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    doc = analyze(data, symcache, symbol_error, path, args.dump_base,
                                  args.ramoops_pa, args.tail, args.kernel_text_base,
                                  args.slide, args.kernel_map)
    except (OSError, ValueError) as error:
        print(f"read-ramdump: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        print_human(doc, args.logs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
