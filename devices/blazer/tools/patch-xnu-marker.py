#!/usr/bin/env python3
"""Rewrite an arm64 XNU Mach-O so its entry point first persists stage-marker
words to a physical diagnostic slot (default 0x95400100, the ramoops pmsg-zone
diag slot), then branches to the original entry -- on-hardware boot progress
evidence with no console.

v2 stub (KEXEC-XNU contract): the branch to the original entry is BASE-
INDEPENDENT (ADR of its own PC + link-time VA delta), so the same patched
file is valid at any physical window base — required because the raw
kexec_load path relocates the payload (PA = base + (VA - VIRT_BASE)) while
the v1 literal-based branch only worked at the patched --phys-base. The stub
also smuggles boot_args: raw arm64 kexec enters with x0 = dtb_mem = 0 (only
kexec_file_load sets it), so the loader publishes a handoff record at a
fixed physical slot (--handoff, default 0x95400120) and the stub loads x0
from it when the magic word is present. Under the shim handoff (x0 =
boot_args PA, no handoff record) the stub leaves x0 untouched — both entry
paths stay valid with one binary.

The shim (blazer/shim/core.c shim_plan_macho) maps the kernel with
  PA = phys_base + (VA - SHIM_KERNEL_VIRT_BASE)
SHIM_KERNEL_VIRT_BASE = 0xfffffff004000000, so with the QEMU contract
phys_base=0x80000000 the stock entry VA 0xfffffff00745e4a8 hands off as
PA 0x8345e4a8. The stub is planted in a code cave (zero/nop padding after the
last content of an executable file-backed segment -- XNU kernels pad their
segments to pages by construction), and only LC_UNIXTHREAD's PC field is
rewritten to point at the cave.

Stub v2 (clobbers x15/x16/x17 only — ARM AAPCS intra-call temporaries):

    movz  w16, #<magic_lo>            ; marker magic = 0x5A5A5A5A
    movk  w16, #<magic_hi>, lsl #16
    movz/movk x17, #<marker PA>       ; 0x95400100
    str   w16, [x17]                  ; +0x00: marker magic
    str   x0,  [x17, #8]              ; +0x08: INCOMING x0 (0 under kexec,
                                      ;        boot_args PA under the shim)
    movz  w16, #<stage>
    str   w16, [x17, #4]              ; +0x04: boot-stage word
    ldr   x17, [pc, #off]             ; literal: handoff slot PA (0x95400120)
    ldr   w16, [x17]                  ; handoff magic candidate
    movz  w15, #<hm_lo>; movk w15, #<hm_hi>, lsl 16   ; 0x30484E58 "XNH0"
    cmp   w16, w15
    b.ne  1f
    ldr   x0,  [x17, #8]              ; x0 = boot_args PA (smuggled)
1:  ldr   x17, [pc, #off]             ; literal: marker PA
    str   x0,  [x17, #0x10]           ; +0x10: FINAL x0 after smuggle
    adr   x15, .                      ; x15 = physical PC of this insn
    str   x15, [x17, #0x18]           ; +0x18: stub PA (ADR diagnostic)
    adr   x16, .                      ; x16 = physical PC of this insn
    movz/movk x17, #|delta|           ; link-time VA delta
    add/sub x16, x16, x17             ; original entry PA at runtime base
    br    x16                         ; -> original entry

Diagnostic slot map (pmsg zone, read back via /sys/fs/pstore/pmsg-ramoops-0
at absolute offset pa-0x95400000; loader zeroes 0x100..0x130 before the
jump so every word is unambiguously from THIS boot):

    0x95400100  u32 0x5A5A5A5A      marker magic (stub ran)
    0x95400104  u32 stage           boot-stage word
    0x95400108  u64 incoming x0     handoff register evidence
    0x95400110  u64 final x0        x0 after the smuggle
    0x95400118  u64 stub PA         ADR-derived physical PC
    0x95400120  u32 0x30484E58      handoff magic ("XNH0", loader-written)
    0x95400124  u32 0               reserved
    0x95400128  u64 boot_args PA    (stub loads it into x0)

Stub v3 adds a VISIBLE progress mark: after the marker writes the stub loads
boot_args->video.base_addr ([x0, #40] — struct shim_boot_args: revision@0,
version@2, virtBase@8, physBase@16, memSize@24, topOfKernelData@32, video@
40; offset 40 confirmed by core.c's _Static_assert chain) and, when nonzero,
fills the first --fb-rows scanlines of the framebuffer with --fb-color using
a simple countdown store loop (ASSUMPTION: 32bpp XRGB/BGRA, 1 word = 1 pixel;
row stride assumed == width*4). The shim's own bringup boot_args leave video
zeroed, so the fill is skipped there; a kexec/shim loader that publishes a
real video.base_addr gets a magenta banner = "entry stub executed", visible
even with USB dead. When x0 == 0 (raw kexec_load, no smuggle record yet) the
fill is skipped with a cbz guard — no [0, #40] translation fault.

Usage:
  patch:  patch-xnu-marker.py --in SRC --out DST [--phys-base 0x80000000]
          [--marker 0x95400100] [--handoff 0x95400120] [--magic 0x5A5A5A5A]
          [--stage 1] [--fb-color 0xFF00FF55] [--fb-width 1280] [--fb-rows 100]
  verify: patch-xnu-marker.py --verify DST [--in SRC] [same options]

verify re-runs the deterministic cave search, rebuilds the stub byte-for-byte
(same inputs), checks the handoff/diagnostic literals and the VA-delta math,
and, when --in is given, that every byte outside {LC_UNIXTHREAD PC field,
cave range} is identical.
"""
import argparse
import struct
import sys

MACHO_MAGIC = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_EXECUTE = 2
LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 5
ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 68

SHIM_KERNEL_VIRT_BASE = 0xFFFFFFF004000000  # blazer/shim/core.h
VIRT_BASE_OFFSET = 0x03000000               # segments must start past this

NOP = 0xD503201F


def err(msg):
    print(f'patch-xnu-marker: {msg}', file=sys.stderr)
    raise SystemExit(1)


def movz(rd, imm16, hw):
    return 0xD2800000 | (hw << 21) | (imm16 << 5) | rd


def movk(rd, imm16, hw):
    return 0xF2800000 | (hw << 21) | (imm16 << 5) | rd


def movz_w(rd, imm16, hw):
    return 0x52800000 | (hw << 21) | (imm16 << 5) | rd


def movk_w(rd, imm16, hw):
    return 0x72800000 | (hw << 21) | (imm16 << 5) | rd


def movz_x(rd, imm16, hw):
    return movz(rd, imm16, hw)


def movk_x(rd, imm16, hw):
    return movk(rd, imm16, hw)


def str_imm_w(rt, rn, byte_off):
    """STR Wt,[Xn,#off] — byte offset, scaled to the imm12 field (×4)."""
    assert byte_off % 4 == 0 and 0 <= byte_off // 4 < 4096
    return 0xB9000000 | ((byte_off // 4) << 10) | (rn << 5) | rt


def str_imm_x(rt, rn, byte_off):
    """STR Xt,[Xn,#off] — byte offset, scaled to the imm12 field (×8)."""
    assert byte_off % 8 == 0 and 0 <= byte_off // 8 < 4096
    return 0xF9000000 | ((byte_off // 8) << 10) | (rn << 5) | rt


def ldr_imm_w(rt, rn, byte_off):
    """LDR Wt,[Xn,#off] — byte offset, scaled to the imm12 field (×4)."""
    assert byte_off % 4 == 0 and 0 <= byte_off // 4 < 4096
    return 0xB9400000 | ((byte_off // 4) << 10) | (rn << 5) | rt


def ldr_imm_x(rt, rn, byte_off):
    """LDR Xt,[Xn,#off] — byte offset, scaled to the imm12 field (×8)."""
    assert byte_off % 8 == 0 and 0 <= byte_off // 8 < 4096
    return 0xF9400000 | ((byte_off // 8) << 10) | (rn << 5) | rt


def cmp_w(rn, rm):
    """SUBS wzr, wn, wm (register) — sets flags, discards result."""
    return 0x6B000000 | (rm << 16) | (rn << 5) | 31


def b_cond(cond, imm_insns):
    assert -(1 << 18) <= imm_insns < (1 << 18)
    imm19 = imm_insns & 0x7FFFF
    return 0x54000000 | (imm19 << 5) | cond


COND_NE = 0b0001


def adr(rd, imm):
    assert -(1 << 20) <= imm < (1 << 20) and imm % 4 == 0
    immlo = (imm >> 2) & 0x3
    immhi = ((imm >> 2) >> 2) & 0x7FFFF
    return 0x10000000 | (immlo << 29) | (immhi << 5) | rd


def add_x(rd, rn, rm):
    return 0x8B000000 | (rm << 16) | (rn << 5) | rd


def sub_x(rd, rn, rm):
    return 0xCB000000 | (rm << 16) | (rn << 5) | rd


def ldr_literal(rt, byte_offset):
    assert byte_offset % 4 == 0 and -(1 << 20) < byte_offset < (1 << 20)
    imm19 = (byte_offset // 4) & 0x7FFFF
    return 0x58000000 | (imm19 << 5) | rt


def br(rn):
    return 0xD61F0000 | (rn << 5)


def cbz_x(rt, imm_insns):
    """CBZ Xt, #imm — 64-bit compare-and-branch (forward/backward)."""
    assert -(1 << 18) <= imm_insns < (1 << 18)
    imm19 = imm_insns & 0x7FFFF
    return 0xB4000000 | (imm19 << 5) | rt


def str_post_imm_w(rt, rn, byte_off):
    """STR Wt,[Xn],#imm9 — post-indexed store (writeback after store)."""
    assert -256 <= byte_off < 256 and byte_off % 4 == 0
    return 0xB8000400 | ((byte_off & 0x1FF) << 12) | (rn << 5) | rt


def subs_imm_x(rd, rn, imm12):
    """SUBS Xd,Xn,#imm12 — 64-bit subtract, sets flags (loop countdown)."""
    assert 0 <= imm12 < 4096
    return 0xF1000000 | (imm12 << 10) | (rn << 5) | rd


def mov_imm64(rd, value):
    """movz/movk sequence materializing an arbitrary 48-bit unsigned value."""
    if value >= (1 << 48) or value == 0:
        err(f'cannot materialize immediate {value:#x}')
    words, seen = [], False
    for hw in range(4):
        lane = (value >> (16 * hw)) & 0xFFFF
        if lane == 0:
            continue
        if not seen:
            words.append(movz(rd, lane, hw))
            seen = True
        else:
            words.append(movk(rd, lane, hw))
    return words


def mov_imm32_w(rd, value):
    """movz/movk W form for a 32-bit value (always exactly 2 words)."""
    assert value < (1 << 32)
    return [movz_w(rd, value & 0xFFFF, 0),
            movk_w(rd, (value >> 16) & 0xFFFF, 1)]


HANDOFF_MAGIC = 0x30484E58  # "XNH0" little-endian; must match kexec-xnu.c

# --- v3 framebuffer progress fill ------------------------------------------
# offsetof(struct shim_boot_args, video.base_addr) == 40, derived from
# blazer/shim/core.c's _Static_assert chain (virtBase@8, deviceTreeP@96,
# commandLine@108, bootFlags@720, sizeof==736): revision u16@0, version u16@2,
# pad@4, virtBase@8, physBase@16, memSize@24, topOfKernelData@32, then
# struct shim_boot_video (6 x u64) at 40..87, machineType@88, pad, DT@96.
VIDEO_BASE_OFFSET = 40
FB_COLOR_DEFAULT = 0xFF00FF55   # task contract value ("magenta-ish")
FB_WIDTH_DEFAULT = 1280         # pixels/scanline
FB_ROWS_DEFAULT = 100           # scanlines to fill
# ASSUMPTION (logged by --verify/patch output): framebuffer is 32bpp XRGB/
# BGRA little-endian, 1 u32 store = 1 pixel, stride == width * 4.
FB_FILL_WORDS = FB_WIDTH_DEFAULT * FB_ROWS_DEFAULT   # 128000 words = 512000 B


def build_fb_fill(color, words_count):
    """v3 fb-fill block: exactly 10 instructions, position-independent.

        cbz  x0, 10f            ; x0 == 0 (raw kexec, no boot_args) -> skip
        ldr  x17, [x0, #40]     ; x17 = video.base_addr (shim/loader-filled)
        cbz  x17, 8f            ; no framebuffer published -> skip
        movz/movk w16, #color   ; 32-bit ARGB pixel word
        movz/movk x15, #count   ; pixels to fill (width*rows)
    7:  str  w16, [x17], #4     ; *x17++ = color   (countdown loop)
        subs x15, x15, #1
        b.ne 7b
    10: (block end; the adr x16 branch anchor follows)

    Runs with MMU off at handoff (plain DRAM stores, same as the marker
    writes); x15/x16/x17 stay within the stub's declared clobber set."""
    if not 0 < words_count < (1 << 32):
        err(f'fb fill count {words_count} must be in 1..2^32-1 words')
    if color >> 32:
        err(f'fb color {color:#x} must fit in 32 bits')
    return [
        cbz_x(0, 10),                          # guard x0 == 0
        ldr_imm_x(17, 0, VIDEO_BASE_OFFSET),   # fb base from boot_args
        cbz_x(17, 8),                          # guard fb == 0
        movz_w(16, color & 0xFFFF, 0),
        movk_w(16, (color >> 16) & 0xFFFF, 1),
        movz(15, words_count & 0xFFFF, 0),
        movk(15, (words_count >> 16) & 0xFFFF, 1),
        str_post_imm_w(16, 17, 4),             # loop head: *fb++ = color
        subs_imm_x(15, 15, 1),
        b_cond(COND_NE, -2),
    ]


def build_stub(marker_pa, magic, stage, handoff_pa, entry_va, cave_va,
               fb_color=FB_COLOR_DEFAULT, fb_words=FB_FILL_WORDS):
    """v3 stub: marker writes, incoming-x0 capture, handoff-slot x0 smuggle,
    fb progress fill, ADR-based base-independent branch to the original entry.
    Word indices of the ADR and the literal pool depend on the marker's
    movz/movk lane count, so the branch delta is computed against the final
    ADR position. Default sizes: 28 words (marker/smuggle) + 10 words (fb
    fill) + 4 words literal pool = 168 bytes."""
    if magic >> 32:
        err(f'magic {magic:#x} must fit in 32 bits')
    if handoff_pa >> 32:
        err(f'handoff slot {handoff_pa:#x} must fit in 32 bits')
    mm = mov_imm64(17, marker_pa)
    words = []
    words += mov_imm32_w(16, magic)
    words += mm
    words.append(str_imm_w(16, 17, 0))        # [marker+0x00] = magic
    words.append(str_imm_x(0, 17, 8))         # [marker+0x08] = incoming x0
    words.append(movz_w(16, stage & 0xFFFF, 0))
    words.append(str_imm_w(16, 17, 4))        # [marker+0x04] = stage
    idx_lit_handoff = len(words)
    words.append(None)                        # ldr x17, [pc, #off] (handoff)
    words.append(ldr_imm_w(16, 17, 0))        # candidate magic
    words += mov_imm32_w(15, HANDOFF_MAGIC)
    idx_cmp = len(words)
    words.append(cmp_w(16, 15))
    idx_bcond = len(words)
    words.append(b_cond(COND_NE, 2))          # skip the x0 reload
    words.append(ldr_imm_x(0, 17, 8))         # x0 = [handoff+8] (boot_args)
    idx_lit_marker = len(words)
    words.append(None)                        # ldr x17, [pc, #off] (marker)
    words.append(str_imm_x(0, 17, 0x10))      # [marker+0x10] = final x0
    words.append(adr(15, 0))                  # x15 = PC of this insn
    words.append(str_imm_x(15, 17, 0x18))     # [marker+0x18] = stub PA
    words += build_fb_fill(fb_color, fb_words)  # v3: visible progress fill
    idx_adr = len(words)
    words.append(adr(16, 0))                  # x16 = PC of this insn
    delta = entry_va - (cave_va + idx_adr * 4)
    if abs(delta) >= (1 << 32):
        err(f'entry-to-cave delta {delta:#x} does not fit in 32 bits')
    words += [movz_x(17, abs(delta) & 0xFFFF, 0),
              movk_x(17, (abs(delta) >> 16) & 0xFFFF, 1)]
    words.append(add_x(16, 16, 17) if delta >= 0 else sub_x(16, 16, 17))
    words.append(br(16))
    while len(words) % 2:                     # pad literal pool to 8 bytes
        words.append(NOP)
    lit_handoff = len(words)
    words += [handoff_pa & 0xFFFFFFFF, (handoff_pa >> 32) & 0xFFFFFFFF]
    lit_marker = len(words)
    words += [marker_pa & 0xFFFFFFFF, (marker_pa >> 32) & 0xFFFFFFFF]
    words[idx_lit_handoff] = ldr_literal(
        17, (lit_handoff - idx_lit_handoff) * 4)
    words[idx_lit_marker] = ldr_literal(
        17, (lit_marker - idx_lit_marker) * 4)
    blob = b''.join(struct.pack('<I', w) for w in words)
    assert len(words[idx_cmp:idx_bcond + 1]) == 2
    return blob


class Segment:
    def __init__(self, segname, vmaddr, vmsize, fileoff, filesize, maxprot,
                 initprot, nsects, cmd_off):
        self.segname, self.vmaddr, self.vmsize = segname, vmaddr, vmsize
        self.fileoff, self.filesize = fileoff, filesize
        self.maxprot, self.initprot, self.nsects = maxprot, initprot, nsects
        self.cmd_off = cmd_off
        self.sections = []  # (sectname, addr, size, offset, flags)


def parse_macho(data, name):
    """Return (segments, unixthread_cmd_off, entry_va). Header/command checks
    mirror shim_plan_macho so anything we accept, the shim accepts too."""
    if len(data) < 32:
        err(f'{name}: too small for a Mach-O header')
    magic, cputype, _cpusub, filetype, ncmds, cmdsize, _flags, _res = \
        struct.unpack_from('<IiiIIIII', data, 0)
    if (magic, cputype, filetype) != (MACHO_MAGIC, CPU_TYPE_ARM64, MH_EXECUTE):
        err(f'{name}: not an arm64 Mach-O executable (the shim would reject it)')
    if not ncmds or ncmds > 4096 or cmdsize > len(data) - 32 or \
            ncmds > cmdsize // 8 or cmdsize % 8:
        err(f'{name}: malformed load commands')
    segments, thread_off, entry_va, thread_count = [], None, 0, 0
    off, cmd_end = 32, 32 + cmdsize
    while off < cmd_end:
        if cmd_end - off < 8:
            err(f'{name}: truncated load command')
        cmd, length = struct.unpack_from('<II', data, off)
        if length < 8 or length % 8 or length > cmd_end - off:
            err(f'{name}: malformed load command length')
        if cmd == LC_SEGMENT_64:
            if length < 72:
                err(f'{name}: undersized LC_SEGMENT_64')
            segname = data[off + 8:off + 24].split(b'\0')[0].decode('ascii', 'replace')
            vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, _fl = \
                struct.unpack_from('<QQQQiiII', data, off + 24)
            if nsects != (length - 72) // 80 or length != 72 + nsects * 80:
                err(f'{name}: malformed section list in {segname}')
            seg = Segment(segname, vmaddr, vmsize, fileoff, filesize, maxprot,
                          initprot, nsects, off)
            for s in range(nsects):
                so = off + 72 + s * 80
                sectname = data[so:so + 16].split(b'\0')[0].decode('ascii', 'replace')
                addr, size = struct.unpack_from('<QQ', data, so + 32)
                offset, _align, _rel, _nrel, flags = struct.unpack_from('<IIIII', data, so + 48)
                seg.sections.append((sectname, addr, size, offset, flags))
            segments.append(seg)
        elif cmd == LC_UNIXTHREAD:
            thread_count += 1
            if thread_count > 1 or length != 288:
                err(f'{name}: unexpected LC_UNIXTHREAD layout')
            flavor, count = struct.unpack_from('<II', data, off + 8)
            if (flavor, count) != (ARM_THREAD_STATE64, ARM_THREAD_STATE64_COUNT):
                err(f'{name}: unexpected thread flavor/count')
            (entry_va,) = struct.unpack_from('<Q', data, off + 16 + 32 * 8)
            thread_off = off
        off += length
    if off != cmd_end:
        err(f'{name}: load commands overrun cmdsize')
    if thread_count != 1:
        err(f'{name}: expected exactly one LC_UNIXTHREAD')
    return segments, thread_off, entry_va


def seg_of_va(segments, va, need_exec):
    """The file-backed segment containing va; optionally require exec prot.
    Mirrors the shim's entry rule: prot & 4 and va inside [vmaddr, +filesize)."""
    for seg in segments:
        if seg.filesize == 0:
            continue
        if seg.vmaddr <= va < seg.vmaddr + seg.filesize:
            if need_exec and not seg.initprot & 4:
                continue
            return seg
    return None


def pa_of(va, phys_base):
    return phys_base + (va - SHIM_KERNEL_VIRT_BASE)


def file_off_of(seg, va):
    return seg.fileoff + (va - seg.vmaddr)


def find_cave(data, segments, min_bytes):
    """Deterministic search: in every executable, file-backed, nonempty
    segment, scan 4-aligned bytes after the segment's last section content up
    to the segment's file end for runs of >= min_bytes zeros/nops. XNU
    segments are page-padded by construction, so this is where caves live.
    Returns (cave_va, cave_fileoff, run_len) or None."""
    candidates = []  # (va, fileoff, full) — first 8-aligned wins, else first
    for seg in segments:
        if seg.filesize == 0 or not seg.initprot & 4:
            continue
        content_end = seg.fileoff  # last byte of in-file section content
        for (_n, addr, size, offset, flags) in seg.sections:
            kind = flags & 0xFF
            if size and kind in (0, 1, 0xC, 0x12) and seg.vmaddr <= addr:
                end = offset + size
                if content_end < end <= seg.fileoff + seg.filesize:
                    content_end = end
        start = content_end
        # 4-align the scan start relative to the VA (fileoff and vmaddr move in
        # lockstep inside a segment and vmaddr is page-aligned).
        start += (-(start - seg.fileoff + seg.vmaddr)) % 4
        run_start, run = None, 0
        limit = seg.fileoff + seg.filesize
        p = start
        while p < limit:
            word = struct.unpack_from('<I', data, p)[0]
            if word == 0 or word == NOP:
                if run == 0:
                    run_start = p
                run += 4
                if run >= min_bytes:
                    # Measure the full run for reporting, but keep the first
                    # qualifying offset (deterministic).
                    full = run
                    q = p + 4
                    while q < limit:
                        word = struct.unpack_from('<I', data, q)[0]
                        if word != 0 and word != NOP:
                            break
                        full += 4
                        q += 4
                    va = seg.vmaddr + (run_start - seg.fileoff)
                    # The stub stream needs an 8-aligned VA; slide the start
                    # forward inside the measured run when possible.
                    slide = (-(va % 8)) % 8
                    if slide and full >= min_bytes + slide:
                        va += slide
                        run_start += slide
                        full -= slide
                    candidates.append((va, run_start, full))
                    p = q  # resume after this measured run
                    run_start, run = None, 0
                    continue
            else:
                run = 0
            p += 4
    if not candidates:
        return None
    for c in candidates:
        if c[0] % 8 == 0:
            return c
    return candidates[0]


def patch(args):
    data = bytearray(open(args.src, 'rb').read())
    segments, thread_off, entry_va = parse_macho(data, args.src)
    entry_seg = seg_of_va(segments, entry_va, need_exec=True)
    if entry_seg is None:
        err(f'entry {entry_va:#x} is not inside an executable file-backed '
            'segment (shim would reject)')
    entry_pa = pa_of(entry_va, args.phys_base)
    entry_foff = file_off_of(entry_seg, entry_va)

    cave = find_cave(data, segments, min_bytes=192)
    if cave is None:
        err('no code cave found (need >=192 bytes of zero/nop padding after '
            'section content in an executable segment); section-extension '
            'fallback deliberately not implemented')
    cave_va, cave_foff, run_len = cave
    if cave_va % 8:
        err(f'cave VA {cave_va:#x} is not 8-aligned')
    cave_pa = pa_of(cave_va, args.phys_base)
    if cave_va == entry_va:
        err('cave and entry coincide')

    stub = build_stub(args.marker, args.magic, args.stage, args.handoff,
                      entry_va, cave_va, args.fb_color,
                      args.fb_width * args.fb_rows)
    data[cave_foff:cave_foff + len(stub)] = stub
    struct.pack_into('<Q', data, thread_off + 16 + 32 * 8, cave_va)

    open(args.dst, 'wb').write(data)

    stub_words = [struct.unpack_from('<I', stub, i)[0]
                  for i in range(0, len(stub) - 16, 4)]
    adr_idx = stub_words.index(adr(16, 0))  # ADR x16, #0 (the branch anchor)
    delta = entry_va - (cave_va + adr_idx * 4)
    print(f'source           {args.src}')
    print(f'output           {args.dst}')
    print(f'entry VA         {entry_va:#x}  ({entry_seg.segname})')
    print(f'entry PA         {entry_pa:#x}  file+{entry_foff:#x} (untouched, '
          f'at --phys-base)')
    print(f'cave VA          {cave_va:#x}  file+{cave_foff:#x} '
          f'({run_len:#x} bytes of padding available)')
    print(f'cave PA          {cave_pa:#x}  (new handoff entry, at '
          f'--phys-base)')
    print(f'branch           ADR + delta {delta:+#x} (base-independent)')
    print(f'marker slot      {args.marker:#x}  <- magic {args.magic:#x}, '
          f'stage {args.stage:#x}')
    print(f'handoff slot     {args.handoff:#x}  <- magic '
          f'{HANDOFF_MAGIC:#x} + boot_args PA (written by kexec-xnu loader)')
    fb_bytes = args.fb_width * args.fb_rows * 4
    print(f'fb progress fill [x0,#0x{VIDEO_BASE_OFFSET:x}]: color '
          f'{args.fb_color:#010x}, {args.fb_rows} rows x {args.fb_width} px '
          f'= {fb_bytes} B ({args.fb_width * args.fb_rows} words, 32bpp '
          f'XRGB/BGRA assumed); skipped when x0==0 or video.base_addr==0')
    print('stub words:')
    for i in range(0, len(stub), 4):
        w = struct.unpack_from('<I', stub, i)[0]
        print(f'  +{i:#04x}: {w:#010x}')
    print(f'  stub bytes total: {len(stub)}')


MOVZ_W_OP, MOVK_W_OP = 0b010100101, 0b011100101   # bits 31..23, 32-bit forms
MOVZ_X_OP, MOVK_X_OP = 0b110100101, 0b111100101   # bits 31..23, 64-bit forms


def decode_imm(w, op_movz, op_movk):
    """Recover the immediate from a movz/movk wide-immediate word, or None."""
    op = w >> 23
    if op != op_movz and op != op_movk:
        return None
    return ((w >> 5) & 0xFFFF) << (16 * ((w >> 21) & 3))


def verify(args):
    patched = open(args.dst, 'rb').read()
    segments, thread_off, entry_va = parse_macho(patched, args.dst)
    seg = seg_of_va(segments, entry_va, need_exec=True)
    if seg is None:
        err(f'patched entry {entry_va:#x} not in an executable file-backed '
            'segment')
    cave_foff = file_off_of(seg, entry_va)
    if cave_foff + 512 > len(patched):
        err('entry lies too close to end of file to hold the stub (the v3 '
            'stub is 168 bytes + its literal pool)')
    stub_region = patched[cave_foff:cave_foff + 512]
    words = struct.unpack_from('<40I', stub_region, 0)

    ok = True

    def chk(name, cond, detail=''):
        nonlocal ok
        ok &= bool(cond)
        print(f'{"PASS" if cond else "FAIL"}: {name}' + (f' ({detail})' if detail else ''))

    orig = None
    if args.src:
        orig = open(args.src, 'rb').read()
        _segs, _toff, orig_va = parse_macho(orig, args.src)
        # Exact stub rebuild: same inputs must yield byte-identical cave bytes.
        expected = build_stub(args.marker_pa, args.magic, args.stage,
                              args.handoff, orig_va, entry_va,
                              args.fb_color, args.fb_width * args.fb_rows)
        chk('cave bytes == rebuilt stub (magic/stage/handoff/fb-fill/delta/'
            'literals)',
            stub_region[:len(expected)] == expected,
            f'{len(expected)} bytes, handoff {args.handoff:#x}, fb '
            f'{args.fb_color:#x} {args.fb_width}x{args.fb_rows}')
        oseg = seg_of_va(_segs, orig_va, need_exec=True)
        if oseg:
            ofoff = file_off_of(oseg, orig_va)
            chk('original entry code bytes untouched',
                patched[ofoff:ofoff + 16] == orig[ofoff:ofoff + 16])
        # Whole-file diff: only the thread PC field and the cave may differ.
        diffs = [i for i in range(min(len(orig), len(patched)))
                 if orig[i] != patched[i]]
        cave_range = set(range(cave_foff, cave_foff + len(expected)))
        pc_lo = thread_off + 16 + 32 * 8
        pc_field = set(range(pc_lo, pc_lo + 8))
        strays = [d for d in diffs if d not in cave_range and d not in pc_field]
        chk('only entry-PC field + cave bytes differ from source',
            not strays and len(orig) == len(patched),
            f'{len(diffs)} bytes differ, strays={strays[:8]}')
    else:
        # Loose decode: walk the v2 word sequence shape. The literal pool is
        # the first non-NOP word after the final br x17: two .quad slots.
        stub = stub_region
        magic_lo = decode_imm(words[0], MOVZ_W_OP, MOVK_W_OP)
        magic_hi = decode_imm(words[1], MOVZ_W_OP, MOVK_W_OP)
        magic = (magic_lo | magic_hi) if None not in (magic_lo, magic_hi) else None
        chk('w16 <- marker magic (movz+movk)', magic == args.magic,
            f'{magic:#x}' if magic is not None else 'undecodable')
        marker, i = 0, 2
        while i < 10:
            imm = decode_imm(words[i], MOVZ_X_OP, MOVK_X_OP)
            if imm is None:
                break
            marker |= imm
            i += 1
        chk('x17 <- marker slot PA', marker == args.marker_pa, f'{marker:#x}')
        chk('str w16,[x17] stores magic', words[i] == str_imm_w(16, 17, 0),
            f'{words[i]:#x}')
        chk('str x0,[x17,#8] stores incoming x0',
            words[i + 1] == str_imm_x(0, 17, 8), f'{words[i + 1]:#x}')
        stage = decode_imm(words[i + 2], MOVZ_W_OP, MOVK_W_OP)
        chk('w16 <- stage word', stage == args.stage,
            f'{stage:#x}' if stage is not None else 'undecodable')
        chk('str w16,[x17,#4] stores stage',
            words[i + 3] == str_imm_w(16, 17, 4), f'{words[i + 3]:#x}')
        j = i + 4
        # handoff literal load: find the ldr x17, [pc, #off] word
        handoff_idx = None
        for k in range(j, min(j + 6, len(words))):
            w = words[k]
            if (w >> 24) == 0x58 and (w & 31) == 17:
                handoff_idx = k
                break
        chk('ldr x17, [pc] literal load (handoff slot)',
            handoff_idx is not None, f'{words[handoff_idx]:#x}'
            if handoff_idx is not None else 'none')
        br_idx = max(k for k, w in enumerate(words) if w == br(16))
        pool_idx = br_idx + 1
        while words[pool_idx] == NOP:
            pool_idx += 1
        lit_handoff, = struct.unpack_from('<Q', stub, pool_idx * 4)
        lit_marker, = struct.unpack_from('<Q', stub, (pool_idx + 2) * 4)
        chk('handoff literal == handoff slot PA',
            lit_handoff == args.handoff, f'{lit_handoff:#x}')
        chk('marker literal == marker slot PA',
            lit_marker == args.marker_pa, f'{lit_marker:#x}')
        # v3 fb progress fill: located right after the [marker+0x18] store.
        # The block is position-independent, so its 10 words are fully
        # determined by (fb_color, fb_words) — byte-compare them exactly and
        # additionally decode each field for a readable report.
        fill = build_fb_fill(args.fb_color, args.fb_width * args.fb_rows)
        cap_idx = words.index(str_imm_x(15, 17, 0x18))
        f = cap_idx + 1
        got_fill = stub[f * 4:(f + len(fill)) * 4]
        want_fill = b''.join(struct.pack('<I', w) for w in fill)
        chk('fb-fill block byte-exact (guards + ldr [x0,#0x28] + loop)',
            got_fill == want_fill,
            f'{len(got_fill)} B at word {f}')
        chk('ldr x17,[x0,#0x28] loads video.base_addr',
            words[f + 1] == ldr_imm_x(17, 0, VIDEO_BASE_OFFSET),
            f'{words[f + 1]:#x} (boot_args video offset '
            f'{VIDEO_BASE_OFFSET})')
        color = (decode_imm(words[f + 3], MOVZ_W_OP, MOVK_W_OP) or 0) | \
                (decode_imm(words[f + 4], MOVZ_W_OP, MOVK_W_OP) or 0)
        chk('w16 <- fb color', color == args.fb_color, f'{color:#010x}')
        count = (decode_imm(words[f + 5], MOVZ_X_OP, MOVK_X_OP) or 0) | \
                (decode_imm(words[f + 6], MOVZ_X_OP, MOVK_X_OP) or 0)
        chk('x15 <- fb pixel count (width*rows)',
            count == args.fb_width * args.fb_rows,
            f'{count} = {args.fb_width}x{args.fb_rows}')
        chk('countdown loop: str w16,[x17],#4 / subs x15,x15,#1 / b.ne',
            words[f + 7] == str_post_imm_w(16, 17, 4)
            and words[f + 8] == subs_imm_x(15, 15, 1)
            and words[f + 9] == b_cond(COND_NE, -2),
            f'{words[f + 7]:#x} {words[f + 8]:#x} {words[f + 9]:#x}')
        chk('fb guards: cbz x0 / cbz x17 skip the fill',
            words[f + 0] == cbz_x(0, 10) and words[f + 2] == cbz_x(17, 8),
            f'{words[f + 0]:#x} {words[f + 2]:#x}')
        # delta math: locate ADR x16, #0 and verify the movz/movk + add/sub
        adr_idx = words.index(adr(16, 0))
        delta_imm = (decode_imm(words[adr_idx + 1], MOVZ_X_OP, MOVK_X_OP) or 0)
        delta_imm |= (decode_imm(words[adr_idx + 2], MOVZ_X_OP, MOVK_X_OP)
                      or 0)
        is_add = words[adr_idx + 3] == add_x(16, 16, 17)
        is_sub = words[adr_idx + 3] == sub_x(16, 16, 17)
        got = delta_imm if is_add else (-delta_imm if is_sub else None)
        chk('branch delta sign/materialization consistent',
            got is not None and abs(got) < (1 << 32),
            f'{"add" if is_add else "sub"} {delta_imm:#x}')
        # The patched PC is the cave VA; the delta must branch back into
        # file-backed executable code (the original entry).
        target_va = entry_va + adr_idx * 4 + got if got is not None else 0
        tseg = seg_of_va(segments, target_va, need_exec=True) \
            if target_va % 4 == 0 else None
        chk('ADR+delta branch target = executable code (original entry)',
            tseg is not None, f'{target_va:#x} in '
            f'{tseg.segname if tseg else "none"}')
        chk('br x16 after add/sub', words[adr_idx + 4] == br(16),
            f'{words[adr_idx + 4]:#x}')

    # Shim-planner shape: entry VA 4-aligned and inside the kernel window.
    chk('patched entry VA 4-aligned and inside kernel window',
        entry_va % 4 == 0 and SHIM_KERNEL_VIRT_BASE <= entry_va
        < SHIM_KERNEL_VIRT_BASE + (1 << 32), f'{entry_va:#x}')
    print(f'info: handoff entry PA = {pa_of(entry_va, args.phys_base):#x} '
          f'(at --phys-base; valid at ANY base), marker slot = '
          f'{args.marker_pa:#x}, handoff slot = {args.handoff:#x}')
    if not ok:
        err('verify FAILED')
    print('verify OK')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--in', dest='src', type=str, help='input Mach-O to patch')
    p.add_argument('--out', dest='dst', type=str, help='patched output path')
    p.add_argument('--verify', dest='verify', type=str,
                   help='verify an already-patched Mach-O and exit')
    p.add_argument('--phys-base', type=lambda x: int(x, 0), default=0x80000000,
                   help='handoff physical base of the kernel window '
                        '(QEMU contract default 0x80000000)')
    p.add_argument('--marker', type=lambda x: int(x, 0), default=0x95400100,
                   help='diagnostic slot physical address (pmsg-zone diag slot)')
    p.add_argument('--handoff', type=lambda x: int(x, 0), default=0x95400120,
                   help='handoff record slot physical address (magic + '
                        'boot_args PA, written by the kexec-xnu loader)')
    p.add_argument('--magic', type=lambda x: int(x, 0), default=0x5A5A5A5A,
                   help='marker magic word stored at [marker]')
    p.add_argument('--stage', type=lambda x: int(x, 0), default=1,
                   help='boot-stage word stored at [marker+4]')
    p.add_argument('--fb-color', type=lambda x: int(x, 0),
                   default=FB_COLOR_DEFAULT,
                   help='32bpp pixel word for the fb progress fill '
                        '(default 0xFF00FF55)')
    p.add_argument('--fb-width', type=int, default=FB_WIDTH_DEFAULT,
                   help='framebuffer width in pixels (default 1280)')
    p.add_argument('--fb-rows', type=int, default=FB_ROWS_DEFAULT,
                   help='number of top scanlines to fill (default 100)')
    a = p.parse_args()
    a.marker_pa = a.marker
    if a.marker_pa & 3:
        err(f'marker address {a.marker_pa:#x} must be 4-aligned')
    if a.handoff & 3:
        err(f'handoff address {a.handoff:#x} must be 4-aligned')
    if a.stage >> 16:
        err('stage word must fit in 16 bits (movz)')
    if a.fb_width <= 0 or a.fb_rows <= 0 or \
            a.fb_width * a.fb_rows >= (1 << 32):
        err('fb width/rows must be >= 1 with width*rows < 2^32')
    if a.verify:
        a.dst = a.verify
        verify(a)
        return
    if not a.src or not a.dst:
        p.error('patching requires --in and --out (or use --verify)')
    patch(a)


if __name__ == '__main__':
    main()
