#!/usr/bin/env python3
"""decode-device-panic.py — pull the laguna 0123/0124-format panic capture
out of REAL device pstore artifacts pulled off the recovering Android boot.
The next ladder's 0128 mach carries this same capture format.

Two channels carry the panic output written by the laguna_panicdump kernel
module (0123/0124 lineage, boot-arg lagunapanicdump=1 default ON):

1. console-ramoops-0 — the persistent_ram console stream (Linux
   fs/pstore/ram_core.c: the FILE content is data[start..start+size)
   wrap-linearized, oldest first, no 12-byte header). The kernel APPENDS a
   text record there with sentinels:

       ----- XNU-PANIC-BEGIN (laguna141 xnu-8796.141.3) -----
       panic message: <panic string>
       <paniclog: panic(cpu N caller 0x...) header, version info,
                  "lr: 0x…  fp: 0x…" backtrace frames, ...>
       ----- msgbuf tail (last N bytes) -----
       <kernel ring tail>
       ----- XNU-PANIC-END -----

   so it sits among (usually AFTER) the ABL/CDD console noise of the crash
   boot. ABL may append its NEXT boot's log after it, and ABL may erase the
   pstore console zone on ladder retries (the tegu-style ladder only skips
   the erase on the FINAL try) — so the decoder never assumes the record is
   at the tail or even present: it scans the whole stream, lists EVERY
   record (one per ladder attempt that panicked), prefers the LAST complete
   one, and flags torn records (BEGIN without END = ring wrap mid-write or
   erased tail).

2. pmsg-ramoops-0 — the same linearized-stream format; the kernel writes a
   raw fixed-offset mirror record at PHYSICAL zone offset 0x30000 (i.e.
   data[] offset 0x2FFF4; file offset 0x2FFF4 while the zone's start==0).
   Android's own /dev/pmsg0 writes and ring wraps can shift or clobber it,
   so the decoder SCANS for the magic and validates instead of trusting the
   offset:

       +0   u8[16]  "XNU-PANIC v1" (NUL-padded)
       +16  u32    tag 0x50414E31 ('PAN1')   — quick-scan validity
       +20  u32    CRC-32/IEEE over payload
       +24  u32    payload length (<= 0x2000)
       +28  u32    seq (1-based, prev+1 per boot)
       +32  u32    reserved
       +36  u32    reserved
       +40  u8[len] payload = "panic message: ...\n" + first 8 KiB of
                    the paniclog (includes backtrace frames)

   Newest wins (highest seq); every valid record is reported, and a record
   with a bad CRC is reported as CORRUPT rather than silently dropped.

Unsliding/symbolication: frame lines carry runtime kernel VAs. The paniclog's
"Kernel text base:" line and the selected Mach-O's __TEXT vmaddr determine the
slide. Runtime caller/LR addresses are UNSLID BEFORE symbol lookup (console and
mirror alike), then resolved against instruction-section LC_SYMTAB symbols with
containing-range checks; an out-of-range runtime address is never mislabeled as
a huge offset from unrelated data. --mach PATH (default: the exact 0128 payload
when present) enables nearest-preceding code symbols without external tools,
e.g. `laguna_panicdump_capture+0x94`. Without a usable LC_SYMTAB the decoder
still emits runtime and unslid addresses, explicitly without symbol names.

Usage:
  decode-device-panic.py --pmsg pmsg-ramoops-0 [--console console-ramoops-0]
                         [--mach PATH] [--json]
  decode-device-panic.py --zone ramoops-4MiB-dump.bin   # raw zone (QEMU
                         evidence format: console@0 + pmsg@0x200000)
  decode-device-panic.py --selftest [--mach PATH]       # synthetic fixtures;
                                                         # test PATH's real symtab

Exit: 0 record decoded, 1 nothing found (zones empty/erased or no record),
2 usage error.
"""
import argparse
import bisect
import hashlib
import json
import os
import re
import struct
import sys

MIRROR_MAGIC = b"XNU-PANIC v1"
MIRROR_TAG = 0x50414E31          # 'PAN1'
MIRROR_WINDOW = 0x2040           # pmsg zone: record window size
MIRROR_PAYLOAD_MAX = 0x2000      # payload cap
MIRROR_ZONE_OFF = 0x30000        # physical zone offset of the record
PRZ_HDR = 12                     # persistent_ram_buffer header size
PRZ_SIG = 0x43474244             # 'DBGC'
CONSOLE_ZONE_SIZE = 0x200000
PMSG_ZONE_OFF = 0x200000         # pmsg sub-zone inside the 4 MiB ramoops zone
KERNEL_LINK_BASE = 0xFFFFFFF007004000

BEGIN = "----- XNU-PANIC-BEGIN"
END = "----- XNU-PANIC-END"
MSGBUF_TAG = "----- msgbuf tail"

# Next device ladder: marker-patched 0128 (0123/0124 capture + DC9x00 probe
# default OFF).  Its embedded LC_SYMTAB is usable directly; the raw companion
# is not required for symbolication.  Prefer it over the older 0124 lane mach.
MACH0128_SHA256 = "cd6ca158ca4804be2e19d486d94d39e0b5b6c78af7e646152dee43c951d4e094"


def default_mach_path():
    lane = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    root = os.path.abspath(os.path.join(lane, "..", ".."))
    candidates = [
        os.path.join(root, "out", "xnu-laguna141-paniccap-0128.macho"),
        os.path.join(root, "out", "mach.laguna141-paniccap-0128"),
        os.path.join(lane, "mach.laguna141-paniccap"),
    ]
    return next((p for p in candidates if os.path.exists(p)), candidates[-1])


def crc32_ieee(data: bytes) -> int:
    """CRC-32/IEEE, table-free — same polynomial the kernel module uses."""
    c = 0xFFFFFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 & (-(c & 1) & 0xFFFFFFFF))
    return c ^ 0xFFFFFFFF


# ---------------------------------------------------------------- mach symbols

class MachSymCache:
    """Mach-O LC_SYMTAB lookup, with code-only range-checked frame lookups.

    The generic table is retained for auditing.  Panic PCs use lookup_code():
    only N_SECT symbols from instruction-bearing sections participate, and the
    runtime/unslid address must actually lie in that same section.  Thus a slid
    address outside the Mach-O cannot turn into a nonsense nearest symbol.
    """

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.blob = f.read()
        b = self.blob
        self.sha256 = hashlib.sha256(b).hexdigest()
        if len(b) < 32:
            raise ValueError(f"{path}: truncated Mach-O header")
        magic, = struct.unpack_from("<I", b, 0)
        if magic != 0xFEEDFACF:
            raise ValueError(f"{path}: not a little-endian 64-bit Mach-O")
        ncmds, sizeofcmds = struct.unpack_from("<II", b, 16)
        if 32 + sizeofcmds > len(b):
            raise ValueError(f"{path}: truncated load commands")

        self.link_base = None
        symtab = None
        code_sections = {}      # 1-based n_sect -> (start, end)
        self.code_ranges = []
        section_index = 0
        off = 32
        for _ in range(ncmds):
            if off + 8 > 32 + sizeofcmds:
                raise ValueError(f"{path}: truncated load command")
            cmd, size = struct.unpack_from("<II", b, off)
            if size < 8 or off + size > 32 + sizeofcmds:
                raise ValueError(f"{path}: invalid load command size {size:#x}")
            if cmd == 0x19:     # LC_SEGMENT_64
                if size < 72:
                    raise ValueError(f"{path}: short LC_SEGMENT_64")
                segname = b[off + 8:off + 24].split(b"\0", 1)[0]
                vmaddr, vmsize = struct.unpack_from("<QQ", b, off + 24)
                nsects, = struct.unpack_from("<I", b, off + 64)
                if segname == b"__TEXT":
                    self.link_base = vmaddr
                if 72 + nsects * 80 > size:
                    raise ValueError(f"{path}: truncated section table")
                for j in range(nsects):
                    section_index += 1
                    so = off + 72 + j * 80
                    addr, ssize = struct.unpack_from("<QQ", b, so + 32)
                    flags, = struct.unpack_from("<I", b, so + 64)
                    # S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS
                    if ssize and flags & 0x80000400:
                        bounds = (addr, addr + ssize)
                        code_sections[section_index] = bounds
                        self.code_ranges.append(bounds)
            elif cmd == 0x2:    # LC_SYMTAB
                if size < 24:
                    raise ValueError(f"{path}: short LC_SYMTAB")
                symtab = struct.unpack_from("<IIII", b, off + 8)
            off += size

        if self.link_base is None:
            raise ValueError(f"{path}: no __TEXT segment; cannot unslide")
        if symtab is None:
            raise ValueError(f"{path}: no LC_SYMTAB; frame addresses only "
                             "(use a matching unstripped mach for XNU symbols)")
        symoff, nsyms, stroff, strsize = symtab
        if symoff + nsyms * 16 > len(b) or stroff + strsize > len(b):
            raise ValueError(f"{path}: LC_SYMTAB lies outside the file")

        self.syms = []          # all usable nlist_64 symbols [(value, name)]
        self.code_syms = []     # instruction-section symbols only
        for i in range(nsyms):
            s = symoff + i * 16
            strx, n_type, n_sect, _n_desc, val = \
                struct.unpack_from("<IBBHQ", b, s)
            if val == 0 or strx >= strsize:
                continue
            e = b.find(b"\0", stroff + strx, stroff + strsize)
            if e < 0:
                continue
            name = b[stroff + strx:e].decode("ascii", "replace")
            if not name:
                continue
            # Mach-O adds one leading underscore to C names.  Strip exactly
            # one: lstrip("_") also destroys the second underscore in C++ names.
            if name.startswith("_"):
                name = name[1:]
            entry = (val, name)
            self.syms.append(entry)
            bounds = code_sections.get(n_sect)
            if ((n_type & 0x0E) == 0x0E and bounds is not None
                    and bounds[0] <= val < bounds[1]):
                self.code_syms.append(entry)
        self.syms.sort()
        self.code_syms.sort()
        self.code_ranges.sort()
        self._addrs = [v for v, _ in self.syms]
        self._code_addrs = [v for v, _ in self.code_syms]

    @staticmethod
    def _format_symbol(entry, addr):
        val, name = entry
        return f"{name}+{addr - val:#x}" if addr != val else name

    def lookup(self, addr: int):
        """Nearest preceding symbol in the full table (audit/diagnostic use)."""
        i = bisect.bisect_right(self._addrs, addr) - 1
        return self._format_symbol(self.syms[i], addr) if i >= 0 else None

    def lookup_code(self, addr: int):
        """Nearest preceding code symbol, bounded to the containing section."""
        bounds = next((r for r in self.code_ranges if r[0] <= addr < r[1]), None)
        if bounds is None:
            return None
        i = bisect.bisect_right(self._code_addrs, addr) - 1
        if i < 0:
            return None
        entry = self.code_syms[i]
        if not bounds[0] <= entry[0] < bounds[1]:
            return None
        return self._format_symbol(entry, addr)


# ---------------------------------------------------------------- console side

def extract_console_records(text: str) -> list:
    """All 0123 records in the stream, in stream order (chronological).

    ABL noise tolerance: records are found by sentinel scan anywhere in the
    stream (ABL/CDD logs, multi-attempt consoles, and next-boot appends are
    ignored). A block whose END never comes before the NEXT BEGIN (or before
    end-of-stream) is TORN — ring wrap mid-write, erased tail, or a partial
    flush; it is reported but never parsed as a good record.
    """
    records = []
    i = 0
    while True:
        b = text.find(BEGIN, i)
        if b < 0:
            break
        e = text.find(END, b)
        b2 = text.find(BEGIN, b + len(BEGIN))
        if e < 0 or (0 <= b2 < e):
            # torn: no END, or the next BEGIN arrives first
            end_of_torn = b2 if (0 <= b2 and (e < 0 or b2 < e)) else len(text)
            records.append({"complete": False, "torn": True,
                            "start_off": b, "bytes": end_of_torn - b,
                            "head": text[b:b + 120]})
            i = b2 if b2 >= 0 else len(text)
            continue
        blob = text[b:e + len(END)]
        records.append({"complete": True, "torn": False,
                        "start_off": b, "end_off": e + len(END),
                        "bytes": len(blob), "text": blob})
        i = e + len(END)
    return records


def parse_console_record(rec: dict, symcache: MachSymCache = None) -> dict:
    """Structured view of one console/mirror payload, with slide-correct PCs."""
    t = rec.get("text", rec.get("head", ""))
    # Panic metadata/backtraces end at the msgbuf marker.  The ring tail can
    # itself contain old panic/lr lines; never count those as THIS backtrace.
    mb = t.find(MSGBUF_TAG)
    panic_text = t[:mb] if mb >= 0 else t
    lines = panic_text.splitlines()
    out = dict(rec)
    link_base = symcache.link_base if symcache is not None else KERNEL_LINK_BASE
    out["kernel_link_base"] = link_base

    text_base = None
    for line in lines:
        s = line.strip()
        if s.startswith("Kernel text base:"):
            try:
                text_base = int(s.split(":", 1)[1].strip(), 16)
            except ValueError:
                pass
            break
    if text_base is not None:
        out["kernel_text_base"] = text_base
        out["slide"] = text_base - link_base

    def resolve_pc(addr):
        # Symbolication MUST use the unslid link-time address.  Looking up the
        # runtime address first produced plausible-looking but false symbols.
        unslid = unslide(addr, text_base, link_base)
        lookup_addr = unslid if unslid is not None else addr
        sym = symcache.lookup_code(lookup_addr) if symcache is not None else None
        return unslid, sym

    msg = None
    for line in lines:
        s = line.strip()
        if s.startswith("panic message: "):
            msg = s[len("panic message: "):]
            break
    out["message"] = msg

    caller = None
    for line in lines:
        s = line.strip()
        m = re.search(r"^panic\(cpu\s+(\d+)\s+caller\s+"
                      r"(0x[0-9a-fA-F]+)\)", s)
        if m:
            caller = {"cpu": int(m.group(1)), "addr": int(m.group(2), 16),
                      "line": s}
            unslid, sym = resolve_pc(caller["addr"])
            if unslid is not None:
                caller["unslid_addr"] = unslid
            if sym is not None:
                caller["sym"] = sym
            break
    out["panic_header"] = caller

    frames = []
    for line in lines:
        s = line.strip()
        # kernel prints "lr: 0x%016llx  fp: 0x%016llx"; tolerate whitespace
        # changes and copy/decode variants that drop the 0x prefix.
        m = re.match(r"^lr:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]+)\s+"
                     r"fp:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]+)", s)
        if not m:
            continue
        try:
            lr, fp = int(m.group(1), 16), int(m.group(2), 16)
        except ValueError:
            continue
        entry = {"lr": lr, "fp": fp}
        unslid, sym = resolve_pc(lr)
        if unslid is not None:
            entry["unslid_lr"] = unslid
        if sym is not None:
            entry["lr_sym"] = sym
        frames.append(entry)
    out["frames"] = frames

    if mb >= 0:
        # Skip the WHOLE "----- msgbuf tail (last N bytes) -----" line;
        # --msgbuf must not prepend its residual "(last N bytes) -----" text.
        tail_start = t.find("\n", mb)
        tail = t[tail_start + 1:] if tail_start >= 0 else ""
        tail = tail[:tail.find(END)] if END in tail else tail
        out["msgbuf_tail"] = tail.strip("\n")
        out["msgbuf_tail_bytes"] = len(out["msgbuf_tail"])
    else:
        out["msgbuf_tail"] = None
        out["msgbuf_tail_bytes"] = None
    kv = None
    for line in lines:
        if "Darwin Kernel Version" in line:
            kv = line.strip()
            break
    out["kernel_version"] = kv
    return out


def unslide(addr: int, text_base: int, link_base: int = KERNEL_LINK_BASE):
    return addr - text_base + link_base if text_base else None


# ------------------------------------------------------------------- pmsg side

def scan_pmsg_mirror(data: bytes) -> list:
    """All valid XNU-PANIC mirror records anywhere in the (linearized) pmsg
    stream; corrupt-magic hits are reported too, newest = highest seq."""
    hits = []
    i = 0
    while True:
        o = data.find(MIRROR_MAGIC, i)
        if o < 0:
            break
        i = o + 1
        rec = {"file_off": o, "assumed_zone_off": o + PRZ_HDR,
               "note": ""}
        if o + 40 > len(data):
            rec.update(valid=False, corrupt="truncated header")
            hits.append(rec)
            continue
        tag, crc, ln, seq = struct.unpack_from("<IIII", data, o + 16)
        rec.update(tag=tag, seq=seq, declared_len=ln)
        if tag != MIRROR_TAG:
            rec.update(valid=False, corrupt=f"tag {tag:#x} != {MIRROR_TAG:#x}")
            hits.append(rec)
            continue
        if ln > MIRROR_PAYLOAD_MAX or ln == 0:
            rec.update(valid=False, corrupt=f"bad len {ln:#x}")
            hits.append(rec)
            continue
        if o + 40 + ln > len(data):
            rec.update(valid=False, corrupt="payload truncated")
            hits.append(rec)
            continue
        payload = data[o + 40:o + 40 + ln]
        ok = crc32_ieee(payload) == crc
        payload_text = payload.decode("ascii", "replace")
        rec.update(valid=ok, crc_ok=ok, crc=crc,
                   payload=payload_text,
                   message=payload_text.split("\n", 1)[0])
        if not ok:
            rec["corrupt"] = "CRC mismatch (torn write or clobbered ring)"
        hits.append(rec)
    return hits


def pick_newest_mirror(records: list):
    valid = [r for r in records if r.get("valid")]
    return max(valid, key=lambda r: r.get("seq", 0)) if valid else None


# --------------------------------------------------------------- raw zone mode

def split_zone_dump(zone: bytes):
    """A 4 MiB pmemsave of the ramoops zone: console persistent_ram at +0,
    raw pmsg zone at +0x200000. Returns (console_text, pmsg_zone_bytes)."""
    if len(zone) < PMSG_ZONE_OFF:
        raise ValueError(f"zone dump too small: {len(zone):#x}")
    sig, start, size = struct.unpack_from("<III", zone, 0)
    note = f"console header sig={sig:#x} start={start:#x} size={size:#x}"
    if sig != PRZ_SIG or start > CONSOLE_ZONE_SIZE - PRZ_HDR \
            or size > CONSOLE_ZONE_SIZE - PRZ_HDR:
        console_text = ""
        note += " (INVALID — Linux would zap; console record unreadable)"
    else:
        bufsz = CONSOLE_ZONE_SIZE - PRZ_HDR
        data = zone[PRZ_HDR:PRZ_HDR + bufsz]
        stream = data[start:start + size] + data[:start]
        console_text = stream.decode("ascii", "replace")
        note += f" (linearized {size:#x} bytes)"
    return console_text, note, zone[PMSG_ZONE_OFF:]


# --------------------------------------------------------------------- report

def decode(pmsg_data=None, console_text=None, zone_dump=None,
           symcache: MachSymCache = None, as_json=False):
    """Full decode; returns (exit_code, summary_dict, human_lines)."""
    human = []
    doc = {"records_console": [], "records_mirror": [], "zones": {}}
    if symcache is not None:
        doc["symbolication"] = {
            "available": True, "mach": symcache.path, "sha256": symcache.sha256,
            "kernel_link_base": symcache.link_base,
            "symbols": len(symcache.syms), "code_symbols": len(symcache.code_syms),
        }
        human.append(f"symbol source: {symcache.path} (sha256 {symcache.sha256}, "
                     f"{len(symcache.syms)} symbols, link {symcache.link_base:#x})")
    else:
        doc["symbolication"] = {"available": False, "frames": "addresses only"}
        human.append("symbol source: unavailable — frame addresses only")
    chosen_console = None
    chosen_mirror = None

    if zone_dump is not None:
        console_text, note, pmsg_data = split_zone_dump(zone_dump)
        doc["zones"]["ramoops_dump"] = note
        human.append(f"zone dump: {note}")

    if console_text is not None:
        recs = extract_console_records(console_text)
        for r in recs:
            p = parse_console_record(r, symcache)
            doc["records_console"].append(
                {k: v for k, v in p.items() if k != "text"})
        complete = [r for r in recs if r.get("complete")]
        human.append(f"console stream: {len(console_text)} bytes, "
                     f"{len(recs)} XNU-PANIC record(s) "
                     f"({len(complete)} complete, {len(recs) - len(complete)} torn)")
        if complete:
            chosen_console = parse_console_record(complete[-1], symcache)
        elif recs:
            human.append("  !! record(s) present but TORN (wrap/erase mid-write)")

    if pmsg_data is not None:
        mirrors = scan_pmsg_mirror(pmsg_data)
        for m in mirrors:
            keep = {k: v for k, v in m.items() if k not in ("payload",)}
            if m.get("payload"):
                keep["payload_head"] = m["payload"][:160]
            doc["records_mirror"].append(keep)
        state = "clean" if mirrors else "empty/erased (all no magic hits)"
        human.append(f"pmsg stream: {len(pmsg_data)} bytes, "
                     f"{len(mirrors)} mirror record(s) ({state})")
        chosen_mirror = pick_newest_mirror(mirrors)
        for m in mirrors:
            if not m.get("valid"):
                human.append(f"  !! corrupt mirror at file {m['file_off']:#x}: "
                             f"{m.get('corrupt')}")

    # ---- the chosen record, rendered
    chosen = None
    if chosen_mirror and (not chosen_console
                          or chosen_mirror.get("seq", 0) * 1000 > 0):
        chosen = ("mirror", chosen_mirror)
    if chosen_console:
        chosen = ("console", chosen_console)

    decoded = {"panic_message": None, "frames": [], "kernel_version": None,
               "msgbuf_tail_bytes": None, "msgbuf_tail": None,
               "channel": None}
    if chosen is None:
        human.append("NO XNU-PANIC record found (0123/0124 format, including 0128).")
        if console_text is not None and not console_text.strip():
            human.append("  console stream is empty/erased — note: ABL erases "
                         "pstore on ladder retries and only skips the erase on "
                         "the FINAL try; the panic capture survives only on a "
                         "non-erased path (final attempt / fallback boot).")
        code = 1
    else:
        kind, rec = chosen
        decoded["channel"] = kind
        code = 0
        if kind == "mirror":
            mirror = rec
            human.append(f"MIRROR record (newest, seq {mirror['seq']}) at file "
                         f"{mirror['file_off']:#x} (zone ~{mirror['assumed_zone_off']:#x}), "
                         f"payload {mirror['declared_len']:#x} B, CRC ok")
            decoded["mirror"] = {"seq": mirror["seq"],
                                 "file_off": mirror["file_off"],
                                 "declared_len": mirror["declared_len"]}
            # Mirror payload is the same message+paniclog grammar as the console
            # record, merely truncated to 8 KiB and without sentinels/msgbuf.
            # Reuse the parser so mirror-only captures get identical unsliding
            # and symbolication instead of raw, unannotated LR strings.
            rec = parse_console_record({"text": mirror.get("payload", "")},
                                       symcache)
            human.append(f"  (mirror payload = message + first "
                         f"{MIRROR_PAYLOAD_MAX:#x} B of paniclog)")
        else:
            human.append(f"CONSOLE record at stream offset "
                         f"{rec.get('start_off', 0):#x}, {rec.get('bytes', 0)} B, "
                         f"complete")

        decoded["panic_message"] = rec.get("message")
        decoded["kernel_version"] = rec.get("kernel_version")
        decoded["msgbuf_tail_bytes"] = rec.get("msgbuf_tail_bytes")
        human.append(f"  panic message: {rec.get('message')}")
        if rec.get("panic_header"):
            ph = rec["panic_header"]
            decoded["panic_header"] = ph
            line = f"  {ph['line']}"
            if ph.get("unslid_addr") is not None \
                    and ph["unslid_addr"] != ph["addr"]:
                line += f"  [unslid caller {ph['unslid_addr']:#x}]"
            if ph.get("sym"):
                line += f"  [caller sym: {ph['sym']}]"
            human.append(line)
        if rec.get("kernel_version"):
            human.append(f"  {rec['kernel_version']}")
        tb = rec.get("kernel_text_base")
        if tb is not None:
            link_base = rec.get("kernel_link_base", KERNEL_LINK_BASE)
            slide = rec.get("slide", tb - link_base)
            decoded["kernel_text_base"] = tb
            decoded["kernel_link_base"] = link_base
            decoded["slide"] = slide
            human.append(f"  kernel text base {tb:#x} (link {link_base:#x}, "
                         f"slide {slide:#x}{' — unslid' if slide == 0 else ''})")
        decoded["frames"] = rec.get("frames", [])
        human.append(f"  backtrace frames: {len(decoded['frames'])}")
        for fr in decoded["frames"]:
            line = f"  lr {fr['lr']:#018x} fp {fr['fp']:#018x}"
            if fr.get("unslid_lr") is not None:
                line += f"  (unslid lr {fr['unslid_lr']:#x})"
            if fr.get("lr_sym"):
                line += f"  [{fr['lr_sym']}]"
            human.append(line)
        if rec.get("msgbuf_tail_bytes") is not None:
            human.append(f"  msgbuf tail: {rec['msgbuf_tail_bytes']} B "
                         f"(in record; --msgbuf prints it)")
        decoded["msgbuf_tail"] = rec.get("msgbuf_tail")

    doc["decoded"] = decoded
    if as_json:
        print(json.dumps(doc, indent=2))
    else:
        for line in human:
            print(line)
    return code, doc, human


def build_synthetic_mirror(message: str, paniclog: str, seq: int = 1) -> bytes:
    """Exact-format mirror record — used by --selftest and verify.py cases."""
    payload = (message + "\n" + paniclog).encode("ascii", "replace")
    payload = payload[:MIRROR_PAYLOAD_MAX]
    rec = bytearray(40)
    rec[0:16] = MIRROR_MAGIC.ljust(16, b"\0")
    struct.pack_into("<IIIII", rec, 16, MIRROR_TAG,
                     crc32_ieee(payload), len(payload), seq, 0)
    return bytes(rec) + payload


def selftest(mach_path=None) -> int:
    ok = True

    def ck(name, cond, detail=""):
        nonlocal ok
        print(f"selftest {name}: {'PASS' if cond else 'FAIL'}{'' if cond else ' — ' + detail}")
        ok &= bool(cond)

    # 1. console record amid ABL noise, complete (frame format matches the
    #    kernel's print_one_backtrace exactly: "lr: 0x%016llx  fp: 0x%016llx")
    frames = "".join(f"\t\t  lr: {0xfffffff00741d000 + i * 0x40:#018x}  "
                     f"fp: {0xfffffff65066f000 + i * 0x20:#018x}\n" for i in range(10))
    record = (f"\n\n{BEGIN} (laguna141 xnu-8796.141.3) -----\n"
              "panic message: lagunapanictest: forced panic\n"
              "panic(cpu 0 caller 0xfffffff00741d6e8): lagunapanictest: forced panic "
              "@laguna_panicdump.c:473\n"
              "Debugger message: panic\n"
              "Kernel text base:  0xfffffff007004000\n"
              "Kernel version: Darwin Kernel Version 22.6.0: test; root:xnu-8796\n"
              f"Panicked thread: 0xdead, backtrace: 0xbeef, tid: 122\n{frames}"
              f"{MSGBUF_TAG} (last 4096 bytes) -----\n"
              "[    0.100000]: LAGUNA PANICDUMP: armed\n"
              "----- XNU-PANIC-END -----\n")
    noise = ("ABL logging lots of stuff\n" * 200) + "CDD reboot reason 0xbab9\n"
    stream = noise + record + "\nABL next-boot log after the crash\n" * 50
    recs = extract_console_records(stream)
    ck("console/1 record found amid ABL noise", len(recs) == 1 and recs[0]["complete"])
    p = parse_console_record(recs[0])
    ck("console/2 message", p["message"] == "lagunapanictest: forced panic")
    ck("console/3 panic header", p["panic_header"] and
       p["panic_header"]["addr"] == 0xfffffff00741d6e8)
    ck("console/4 frames", len(p["frames"]) == 10 and
       p["frames"][0]["lr"] == 0xfffffff00741d000)
    ck("console/5 text base", p.get("kernel_text_base") == KERNEL_LINK_BASE)
    ck("console/6 msgbuf tail", p.get("msgbuf_tail_bytes", 0) >= 30
       and p["msgbuf_tail"].startswith("[    0.100000]:")
       and "last 4096 bytes" not in p["msgbuf_tail"])
    ck("console/7 record not at tail tolerated", stream.find(END) < len(stream) - 100)

    # 2. torn record + complete later one -> last complete wins
    torn = noise + f"{BEGIN} (laguna141 xnu-8796.141.3) -----\npanic message: trun"
    recs2 = extract_console_records(torn + record)
    ck("console/8 torn detected + complete picked",
       len(recs2) == 2 and not recs2[0]["complete"] and recs2[1]["complete"])

    # 3. erased zone (all zero) and empty stream
    recs3 = extract_console_records("\0" * 4096)
    ck("console/9 erased zone -> no records", recs3 == [])

    # 4. mirror scan: valid, corrupt-CRC neighbor, multiple seqs -> newest
    good = build_synthetic_mirror("panic message: one", "panic(cpu 0 caller 0x0): x", 1)
    newer = build_synthetic_mirror("panic message: two", "panic(cpu 1 caller 0x1): y", 2)
    bad = bytearray(build_synthetic_mirror("panic message: bad", "z", 3))
    bad[20 + 24] ^= 0xFF     # flip a CRC... (crc at +20, flip payload-side byte)
    pad = b"\0" * 0x2FFF4
    blob = pad + good + (b"\xAB" * 64) + bytes(bad) + (b"\xC9" * 32) + newer
    hits = scan_pmsg_mirror(blob)
    ck("mirror/1 three hits", len(hits) == 3)
    ck("mirror/2 corrupt flagged", any(not h.get("valid") for h in hits))
    newest = pick_newest_mirror(hits)
    ck("mirror/3 newest by seq", newest and newest["seq"] == 2
       and newest["message"] == "panic message: two")
    ck("mirror/4 zone offset annotation",
       hits[0]["assumed_zone_off"] == MIRROR_ZONE_OFF)

    # 5. raw zone dump split (QEMU evidence shape)
    zone = bytearray(0x400000)
    struct.pack_into("<III", zone, 0, PRZ_SIG, 0, 0x100)
    zone[PRZ_HDR:PRZ_HDR + 0x100] = record.encode()
    mrec = build_synthetic_mirror("panic message: z", "lr: 0x0", 5)
    zone[0x200000 + MIRROR_ZONE_OFF:0x200000 + MIRROR_ZONE_OFF + len(mrec)] = mrec
    ctext, note, pmsg = split_zone_dump(bytes(zone))
    ck("zone/1 console linearized", "panic message: lagunapanictest" in ctext)
    hits5 = scan_pmsg_mirror(pmsg)
    ck("zone/2 mirror found at raw zone offset",
       len(hits5) == 1 and hits5[0]["file_off"] == MIRROR_ZONE_OFF,
       f"file_off={hits5[0]['file_off']:#x}" if hits5 else "no hits")

    # 6. Real Mach-O symbolication.  --selftest --mach PATH is deliberately
    # supported so the device runbook tests the EXACT payload being flashed.
    mach = mach_path or default_mach_path()
    if os.path.exists(mach):
        sc = MachSymCache(mach)
        print(f"selftest mach source: {mach} sha256={sc.sha256} "
              f"symbols={len(sc.syms)} code={len(sc.code_syms)}")
        capture = next((v for v, n in sc.code_syms
                        if n == "laguna_panicdump_capture"), None)
        handle = next((v for v, n in sc.code_syms
                       if n == "handle_debugger_trap"), None)
        ck("mach/1 real LC_SYMTAB resolves capture",
           capture is not None and sc.link_base == KERNEL_LINK_BASE
           and sc.lookup_code(capture) == "laguna_panicdump_capture")

        if capture is None or handle is None:
            ck("mach/2 required real code symbols", False,
               f"capture={capture} handle={handle}")
        else:
            slide = 0x12345000
            runtime_base = sc.link_base + slide
            capture_lr = capture + 0x40 + slide
            handle_lr = handle + 0x524 + slide
            caller = capture + slide
            paniclog = (
                f"panic(cpu 0 caller {caller:#018x}): synthetic 0128 panic\n"
                "Debugger message: panic\n"
                f"Kernel text base:  {runtime_base:#018x}\n"
                "Kernel version: Darwin Kernel Version 22.6.0: synthetic; "
                "root:xnu-8796.141.3\n"
                f"\t\t  lr: {capture_lr:#018x}  fp: 0xfffffff65066f000\n"
                f"\t\t  lr: {handle_lr:#018x}  fp: 0xfffffff65066eff0\n")
            record0128 = (
                f"{BEGIN} (laguna141 xnu-8796.141.3) -----\n"
                "panic message: lagunapanictest: synthetic 0128 panic\n"
                f"{paniclog}"
                f"{MSGBUF_TAG} (last 4096 bytes) -----\n"
                "[    0.100000]: LAGUNA DC9x00 probe: ADT disabled disabled; "
                "MMIO probe skipped\n"
                "lr: 0xdead  fp: 0xbeef  (old msgbuf noise, not a frame)\n"
                f"{END} -----\n")

            # A runtime address outside every instruction section must never
            # become a huge, bogus offset from an unrelated data symbol.
            ck("mach/2 out-of-range runtime PC rejected",
               sc.lookup_code(capture_lr) is None)

            parsed = parse_console_record(
                extract_console_records(record0128)[0], sc)
            fr0 = parsed["frames"][0]
            ck("mach/3 nonzero slide unslides before symbol lookup",
               parsed.get("slide") == slide
               and fr0.get("unslid_lr") == capture + 0x40
               and fr0.get("lr_sym") == "laguna_panicdump_capture+0x40"
               and parsed["panic_header"].get("unslid_addr") == capture
               and parsed["panic_header"].get("sym") == "laguna_panicdump_capture",
               repr(parsed["frames"]))

            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                code0128, doc0128, human0128 = decode(
                    console_text="ABL noise\n" + record0128 + "next boot\n",
                    symcache=sc)
            decoded0128 = doc0128["decoded"]
            ck("mach/4 end-to-end 0128 console decode",
               code0128 == 0 and decoded0128["channel"] == "console"
               and decoded0128.get("slide") == slide
               and len(decoded0128["frames"]) == 2
               and decoded0128["frames"][1].get("unslid_lr") == handle + 0x524
               and decoded0128["frames"][1].get("lr_sym")
                   == "handle_debugger_trap+0x524"
               and doc0128["symbolication"]["sha256"] == sc.sha256
               and "MMIO probe skipped" in decoded0128["msgbuf_tail"]
               and any("laguna_panicdump_capture+0x40" in line
                       for line in human0128))

            mirror0128 = build_synthetic_mirror(
                "panic message: lagunapanictest: synthetic 0128 panic",
                paniclog, seq=8)
            with contextlib.redirect_stdout(io.StringIO()):
                mirror_code, mirror_doc, _ = decode(
                    pmsg_data=b"\0" * 0x2FFF4 + mirror0128, symcache=sc)
            ck("mach/5 mirror-only 0128 decode is symbolicated",
               mirror_code == 0 and mirror_doc["decoded"]["channel"] == "mirror"
               and mirror_doc["decoded"]["panic_message"]
                   == "lagunapanictest: synthetic 0128 panic"
               and mirror_doc["decoded"].get("slide") == slide
               and mirror_doc["decoded"]["frames"][0].get("lr_sym")
                   == "laguna_panicdump_capture+0x40")

            if sc.sha256 == MACH0128_SHA256:
                ck("mach/6 exact cd6ca158 0128 symbol addresses",
                   capture == 0xFFFFFFF00741D4E4
                   and handle == 0xFFFFFFF0072A1F3C
                   and sc.lookup_code(0xFFFFFFF00741D728)
                       == "laguna_panicdump_capture+0x244"
                   and sc.lookup_code(0xFFFFFFF0072A2460)
                       == "handle_debugger_trap+0x524")
            else:
                print("selftest mach/6 exact 0128: SKIP "
                      f"(selected mach sha256={sc.sha256})")
    else:
        if mach_path:
            ck("mach/1 explicit --mach exists", False, mach)
        else:
            print("selftest mach: SKIP (payload mach not found)")

    print(f"selftest {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pmsg", help="pmsg-ramoops-0 dump (linearized stream)")
    ap.add_argument("--console", help="console-ramoops-0 dump (text stream)")
    ap.add_argument("--zone", help="raw 4 MiB ramoops zone dump (QEMU pmemsave "
                                   "of PA 0x95200000: console@0 + pmsg@0x200000)")
    ap.add_argument("--mach", default=None,
                    help="payload Mach-O for symbolication (default: 0128 "
                         "payload, then raw 0128, then legacy lane mach); "
                         "also selects the real table tested by --selftest")
    ap.add_argument("--msgbuf", action="store_true",
                    help="print the captured msgbuf tail verbatim")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args.mach)

    mach = args.mach or default_mach_path()
    symcache = None
    if os.path.exists(mach):
        try:
            symcache = MachSymCache(mach)
        except ValueError as e:
            print(f"(symbolication unavailable: {e}; frame addresses only)",
                  file=sys.stderr)
    else:
        print(f"(symbolication unavailable: {mach}: file not found; "
              "frame addresses only)", file=sys.stderr)

    console_text = None
    pmsg_data = None
    if args.zone:
        zone_dump = open(args.zone, "rb").read()
    else:
        zone_dump = None
        if args.console:
            console_text = open(args.console, "rb").read().decode("ascii", "replace")
        if args.pmsg:
            pmsg_data = open(args.pmsg, "rb").read()
        if not console_text and not pmsg_data:
            ap.error("nothing to decode: pass --pmsg/--console/--zone/--selftest")

    code, doc, _ = decode(pmsg_data, console_text, zone_dump, symcache,
                          as_json=args.json)
    if args.msgbuf and doc.get("decoded", {}).get("msgbuf_tail"):
        print("\n----- captured msgbuf tail -----")
        print(doc["decoded"]["msgbuf_tail"])
    return code


if __name__ == "__main__":
    sys.exit(main())
