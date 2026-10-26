#!/usr/bin/env python3
"""Generate two 4 MiB kernel.log-shaped LGLW fixtures for R2 integration.

Valid fixture: seq 11 DYLD_SHARED_REGION in slot 1, seq 12 PANIC in slot 0;
the reader must select PANIC.  Torn fixture: identical except PANIC's DONE is
zero; the reader must reject it and select seq 11.  No device data is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

MAGIC = 0x574C474C
DONE = 0x454E4F44
SIZE = 128
PA0 = 0x95401100
DUMP_BASE = 0x95200000
OFF0 = PA0 - DUMP_BASE


def record(seq, stage, producer, flags, detail, note):
    r = bytearray(SIZE)
    struct.pack_into('<IHHIIII', r, 0, MAGIC, 1, SIZE, seq, stage, producer, flags)
    struct.pack_into('<4Q', r, 0x18, *detail)
    r[0x38:0x38 + min(63, len(note))] = note[:63]
    struct.pack_into('<I', r, 0x78, zlib.crc32(r[:0x78]) & 0xFFFFFFFF)
    struct.pack_into('<I', r, 0x7C, DONE)
    return r


def base_image():
    image = bytearray(0x400000)
    # Valid empty persistent_ram headers make the physical zones discoverable
    # without pretending that the fixed LGLW slots are pmsg ring appends.
    for off in (0, 0x200000):
        struct.pack_into('<III', image, off, 0x43474244, 0, 0)
    dyld = record(11, 11, 5, 0, (0x12345000, 0, 0, 0),
                  b'pid1 shared_region_check_np')
    panic = record(12, 12, 6, 2, (0x4567, 0, 0, 0), b'panic capture rung')
    image[OFF0 + 128:OFF0 + 256] = dyld  # slot 1
    image[OFF0:OFF0 + 128] = panic       # slot 0
    return image


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out_dir', type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 'synthetic kernel.log-shaped LGLW fixtures',
                'dump_base': hex(DUMP_BASE), 'bytes': 0x400000,
                'slot0_file_offset': hex(OFF0),
                'slot1_file_offset': hex(OFF0 + 128), 'fixtures': {}}
    for name, torn in [('lglw-valid-kernel-log.bin', False),
                       ('lglw-torn-kernel-log.bin', True)]:
        image = base_image()
        if torn:
            struct.pack_into('<I', image, OFF0 + 0x7C, 0)
        path = args.out_dir / name
        path.write_bytes(image)
        digest = hashlib.sha256(image).hexdigest()
        manifest['fixtures'][name] = {
            'sha256': digest,
            'expected_selected_stage': 11 if torn else 12,
            'expected_selected_seq': 11 if torn else 12,
            'expected_selected_slot': 1 if torn else 0,
            'torn_candidate_seq': 12 if torn else None,
        }
        print(f'{path} sha256={digest} expect stage={11 if torn else 12}')
    (args.out_dir / 'lglw-fixture-manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n')
    print('decode example: python3 research/laguna-ramdump/reader/read-ramdump.py '
          f'{args.out_dir / "lglw-valid-kernel-log.bin"} '
          '--dump-base 0x95200000 --json')


if __name__ == '__main__':
    main()
