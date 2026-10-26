#!/usr/bin/env python3
"""Root-own an HFS+ iOS-root image by patching catalog b-tree uid fields.

WHY (F1 ENOEXEC lane, boot 2 evidence): XNU's dyld shared-region map
REFUSES the dyld shared cache unless the cache file is owned by uid 0
("shared_region: ... map(...'dyld_shared_cache_arm64'): owned by uid=501
instead of 0"). The fleet's macOS-built images carry the builder's uid 501
everywhere because there is no sudo on this host (verify-cacheroot.sh and
build-cacheroot.sh attach user-mountable, and chown to root needs root).
This tool rewrites the HFS+ catalog leaf records' HFSPlusBSDInfo.owner
501 -> 0 IN PLACE on the raw image (unmounted, cleanly detached), keeping
mobile-owned trees mobile so the rootfs keeps real iOS ownership
semantics:

  KEEP uid (no patch): /private/var/mobile, /private/var/containers,
                       /private/var/protected,
                       /private/var/Managed Preferences/mobile
  PATCH  501 -> 0    : everything else (system: /System /usr /sbin /dev
                       /private/preboot cryptexes, /private/var/db, ...)

Group ids are left untouched (the kernel's shared-region gate checks the
uid only). Modes are untouched. Only the 4-byte owner field of affected
catalog records changes.

Usage: hfs-rootown.py IMG.hfs [--apply]   (default: dry-run census)
"""
import struct
import sys
from pathlib import Path

KEEP_PREFIXES = (
    '/private/var/mobile',
    '/private/var/containers',
    '/private/var/protected',
    '/private/var/Managed Preferences/mobile',
)
# HFS+ PRIVATE structures — NEVER patch. The hidden hard-link machinery
# stores the LINK COUNT in the hidden record's c_uid field (TN2166 /
# hfs_fsck): visible 'hlnk' records point (fileId) at hidden iNode*/dir_*
# records under 'HFS+ Private Directory Data\r' whose c_uid IS the count.
# Zeroing those = "Incorrect number of file hard links" (measured, F1).
HIDDEN_COMPONENTS = ('HFS+ Private Directory Data\r', '.journal',
                     '.journal_info_block', '.debug_hsdb')


def is_hidden_path(path):
    for part in path.split('/'):
        if part in HIDDEN_COMPONENTS or part.startswith(('iNode', 'dir_')):
            return True
    return False


def fail(msg):
    raise SystemExit('ERROR: ' + msg)


def main():
    img = Path(sys.argv[1])
    apply = '--apply' in sys.argv
    data = bytearray(img.read_bytes())
    if data[1024:1026] not in (b'H+', b'HX'):
        fail('not a raw HFS+ volume at offset 1024')
    vh = 1024
    (sig, version, attributes, lastMounted, jib, cdate, mdate, bdate, ckdate,
     fileCount, folderCount, blockSize, totalBlocks, freeBlocks,
     nextAlloc, rsrcClump, dataClump, nextCatID, writeCount) = struct.unpack_from(
        '>2sHIIIIIIIIIIIIIIIII', data, vh)
    # catalogFile fork at vh+272 (2s+H = 4, then 17 u32 fields = 68B,
    # encodingsBitmap 8B, finderInfo 32B, then allocFile/extFile 80B each)
    cat = vh + 4 + 68 + 8 + 32 + 160
    cat_size, cat_clump, cat_blocks = struct.unpack_from('>QII', data, cat)
    extents = struct.unpack_from('>16I', data, cat + 16)
    ext_pairs = [(extents[2 * i], extents[2 * i + 1]) for i in range(8)]
    covered = sum(n for _, n in ext_pairs) * blockSize
    if covered < cat_size:
        fail(f'catalog fork needs extent overflow ({covered} < {cat_size}); '
             'not implemented — refusing')
    cat_offset = {}
    pos = 0
    for start, n in ext_pairs:
        for i in range(n):
            cat_offset[pos // blockSize] = (start + i) * blockSize
            pos += blockSize

    # b-tree header node (node 0)
    hn = cat_offset[0]
    node_size, = struct.unpack_from('>H', data, hn + 32)
    first_leaf = struct.unpack_from('>I', data, hn + 24)[0]
    if node_size not in (512, 1024, 2048, 4096, 8192, 16384, 32768):
        fail(f'implausible node size {node_size}')
    per_node = node_size // blockSize
    if node_size % blockSize:
        fail('node size not a multiple of the block size')

    def node_buf(idx):
        base_block = (idx // per_node) * per_node
        start = cat_offset[base_block] + (idx % per_node) * node_size
        return start

    # --- pass 1: walk ALL nodes; parse genuine leaves (chain-independent;
    # this image's f_link chain is inconsistent — loops at node 16) ---------
    folders = {}   # cnid -> (parent_id, name)
    recs = []      # (abs_off, kind, parent_id, name)
    total_nodes = cat_size // node_size
    n_leaves = 0
    for n in range(total_nodes):
        off = node_buf(n)
        f_link, _, kind, height, nrec = struct.unpack_from('>IIBBH', data, off)
        if kind != 0xFF or height != 1:  # kBTLeafNode (0xFF = signed -1)
            continue
        n_leaves += 1
        if nrec > node_size // 16:
            fail(f'implausible record count at leaf {n}: {nrec}')
        for r in range(nrec):
            rec_off = off + struct.unpack_from('>H', data, off + node_size - 2 * (r + 1))[0]
            klen, = struct.unpack_from('>H', data, rec_off)
            parent_id = struct.unpack_from('>I', data, rec_off + 2)[0]
            namelen = struct.unpack_from('>H', data, rec_off + 6)[0]
            name = data[rec_off + 8: rec_off + 8 + 2 * namelen].decode('utf-16-be')
            dstart = rec_off + 2 + klen
            if dstart & 1:
                dstart += 1
            rtype, = struct.unpack_from('>H', data, dstart)
            if rtype == 1:   # folder
                cnid = struct.unpack_from('>I', data, dstart + 8)[0]
                folders[cnid] = (parent_id, name)
                recs.append((dstart, 'dir', parent_id, name))
            elif rtype == 2: # file
                recs.append((dstart, 'file', parent_id, name))
            # rtype 3 = thread record: skip
        n = f_link

    def path_of(parent_id, name):
        parts = [name]
        cnid = parent_id
        seen = set()
        while cnid not in (1, 2) and cnid not in seen:
            seen.add(cnid)
            if cnid not in folders:
                return None
            p, nm = folders[cnid]
            parts.append(nm)
            cnid = p
        return '/' + '/'.join(reversed(parts))

    # --- pass 2: patch uid 501 -> 0 outside the mobile-owned trees -------
    census = {}
    patched = 0
    samples = []
    for dstart, kind, parent_id, name in recs:
        path = path_of(parent_id, name)
        if path is None:
            continue
        p = path.rstrip('/')
        keep = any(p == k or p.startswith(k + '/') for k in KEEP_PREFIXES) \
            or is_hidden_path(path)
        owner, = struct.unpack_from('>I', data, dstart + 32)
        key = 'keep' if keep else ('patch' if owner != ROOT_UID else 'root')
        census[key] = census.get(key, 0) + 1
        if not keep and owner != ROOT_UID:
            struct.pack_into('>I', data, dstart + 32, ROOT_UID)
            patched += 1
            if len(samples) < 12 and kind == 'file':
                samples.append(f'{owner} -> 0  {path}')

    print(f'catalog: {len(recs)} records across {n_leaves}/{total_nodes} leaf nodes; census {census}; would patch {patched}')
    for s in samples:
        print('  e.g.', s)
    for probe in ('/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64',
                  '/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64',
                  '/sbin/launchd', '/private/var/mobile'):
        print('  probe', probe, '->', 'KEEP' if any(probe == k or probe.startswith(k + '/') for k in KEEP_PREFIXES) else 'PATCH')
    if not apply:
        print('DRY RUN — rerun with --apply to write')
        return
    img.write_bytes(data)
    print(f'APPLIED: {patched} records patched, image written ({img})')


if __name__ == '__main__':
    main()
