#!/usr/bin/env python3
"""Rewrite ownership/modes inside a raw HFS+ image WITHOUT root (macOS chown needs it).

  hfs-fixmeta.py IMAGE --manifest system.manifest [--rules extra.rules] [--apply]
  hfs-fixmeta.py IMAGE --manifest system.manifest [--rules R] --check   (default)

Why: the tegu recipe (reference/ios-tegu/tools/mkiosroot.sh) rsyncs as root, so Apple's
uid/gid survive into the image. This macOS host has no passwordless sudo, so population
runs as uid 501 and every on-disk owner would be 501. The Apple owners (0 root, 272 _logd,
501 mobile, 33 _installd, ...) are known exactly — select-system.py carried them out of
the 7z listing into system.manifest ("mode\tuid\tgid\tpath [target]"). This tool walks the
catalog B-tree and patches HFSPlusBSDInfo {ownerID, groupID, mode} in place.

Safety design (calibrated on a live macOS-created HFS+ volume, every value verified):
  * record boundaries come from the node's OFFSET ARRAY (authoritative; the array lives at
    the node tail, entry i at end-2*(i+1), ASCENDING record order),
  * HFSPlusBSDInfo sits at record+32 and is only rewritten where the current uid == 501
    (everything the population created) — records already owned by 0 (Apple/kernel-managed,
    journals, the hardlink-encoding link records whose uid/gid fields are NOT owners) and
    thread records are never touched,
  * hard links: macOS stores the REAL metadata in a hidden 'iNode<cnid>' file record; those
    ARE ours (uid 501) and get the target values, the visible link records stay untouched —
    the kernel resolves links through the inode record.
Rules per record (first match wins):
  1. path in system.manifest        -> uid/gid/mode from the manifest (exact Apple values)
  2. path in the rules file         -> "path uid gid [mode]"
  3. private/var/mobile/...         -> 501:501 (mobile:mobile), mode kept
  4. private/var/db/{diagnostics,uuidtext}/... -> 272:272 (_logd), mode kept
  5. hidden 'HFS+ Private' / 'iNode*' -> 0:0, mode kept
  6. anything else                  -> 0:0 (root:wheel), mode kept
The volume must be UNMOUNTED (we bypass the kernel); run fsck afterwards (the build script
does). Names are matched with NFC/NFD fallbacks (HFS+ stores names decomposed; the manifest
lists them composed).
"""
import struct
import sys
import unicodedata

VH_OFF = 1024
ROOT_CNID = 2
PERM_OFF = 32                     # recordType(2) flags(2) valence(4) id(4) 5*4 dates -> BSDInfo
RT_FOLDER, RT_FILE, RT_FOLDER_THREAD, RT_FILE_THREAD = 1, 2, 3, 4


def be(fmt, buf, off=0):
    return struct.unpack_from('>' + fmt, buf, off)


class CatalogError(Exception):
    pass


class HFSImage:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'r+b')
        self.f.seek(VH_OFF)
        vh = self.f.read(512)
        sig, version = be('HH', vh, 0)
        if sig not in (0x482B, 0x4858) or version not in (4, 5):
            raise CatalogError(f'not an HFS+/HFSX volume (signature 0x{sig:04X}, version {version})')
        self.sig = sig
        self.blockSize, = be('I', vh, 40)
        self.freeBlocks, = be('I', vh, 48)
        self.cat_size, _clump, self.cat_ext = self._fork(vh, 272)
        self.cat = bytearray(self._read_fork())
        kind = struct.unpack_from('>b', self.cat, 8)[0]
        if kind != 1:
            raise CatalogError(f'catalog node 0 kind {kind} != 1 (header node)')
        self.firstLeaf, = be('i', self.cat, 24)
        self.nodeSize, = be('H', self.cat, 32)
        if self.nodeSize not in (512, 1024, 2048, 4096, 8192, 16384, 32768):
            raise CatalogError(f'implausible B-tree node size {self.nodeSize}')

    def _fork(self, vh, off=272):
        size, = be('Q', vh, off)
        ext = []
        for i in range(8):
            start, count = be('II', vh, off + 16 + 8 * i)
            if count:
                ext.append((start, count))
        return size, 0, ext

    def _read_fork(self):
        size = self.cat_size
        buf = bytearray(size)
        pos = 0
        for start, count in self.cat_ext:
            self.f.seek(start * self.blockSize)
            data = self.f.read(count * self.blockSize)
            buf[pos:pos + len(data)] = data
            pos += len(data)
        if pos < size:
            raise CatalogError(f'catalog fork extents cover {pos} < {size} bytes')
        return bytes(buf[:size])

    def records(self):
        """Yield (node, rec_off, key_parent, key_name, rec_type, cnid) using the offset arrays."""
        node = self.firstLeaf
        while node != 0:
            base = node * self.nodeSize
            fLink, _blink, kind, _height, num = be('IIbbH', self.cat, base)
            if kind != -1:
                raise CatalogError(f'node {node} is not a leaf (kind {kind})')
            end = base + self.nodeSize
            offs = [be('H', self.cat, end - 2 * (i + 1))[0] for i in range(num + 1)]
            # offs[0..num-1] = record i offsets (ascending); offs[num] = end of last record
            for i in range(num):
                off = offs[i]
                nxt = offs[i + 1]
                if not 14 <= off < nxt <= self.nodeSize:
                    raise CatalogError(f'node {node}: bogus offset pair ({off},{nxt})')
                keyLen, parent = be('HI', self.cat, base + off)
                nlen, = be('H', self.cat, base + off + 6)
                if 8 + 2 * nlen > 2 + keyLen or keyLen > nxt - off:
                    raise CatalogError(f'node {node} rec{i}: implausible key (len {keyLen}, nlen {nlen})')
                name = bytes(self.cat[base + off + 8:base + off + 8 + 2 * nlen]).decode('utf-16-be')
                rec_off = off + 2 + keyLen
                rtype, = be('H', self.cat, base + rec_off)
                cnid = None
                if rtype in (RT_FOLDER, RT_FILE):
                    cnid, = be('I', self.cat, base + rec_off + 8)
                elif rtype not in (RT_FOLDER_THREAD, RT_FILE_THREAD):
                    raise CatalogError(f'node {node} rec{i}: unknown record type {rtype}')
                yield node, rec_off, parent, name, rtype, cnid
            node = fLink
            if node and node * self.nodeSize >= len(self.cat):
                raise CatalogError(f'leaf chain runs past the catalog (node {node})')

    def write_back(self):
        pos = 0
        for start, count in self.cat_ext:
            n = count * self.blockSize
            self.f.seek(start * self.blockSize)
            self.f.write(self.cat[pos:pos + n])
            pos += n
        self.f.flush()
        # NO truncate(): the catalog fork lives inside the image; truncating here would
        # cut the image file at the catalog's end and destroy the volume.


def norm_set(name):
    return {name, unicodedata.normalize('NFC', name), unicodedata.normalize('NFD', name)}


def main():
    argv = sys.argv[1:]
    if '--manifest' not in argv or len(argv) < 3:
        print(__doc__)
        return 2
    image = argv[0]
    manifest_path = argv[argv.index('--manifest') + 1]
    rules_path = argv[argv.index('--rules') + 1] if '--rules' in argv else None
    apply = '--apply' in argv

    h = HFSImage(image)

    want = {}                                          # path -> (uid, gid, perm|None)
    full_type = {}                                     # path -> manifest type bits (S_IFLNK etc.)
    for line in open(manifest_path, errors='replace'):
        p = line.rstrip('\n').split('\t')
        if len(p) < 4:
            continue
        mode = int(p[0], 8)
        want[p[3]] = (int(p[1]), int(p[2]), mode & 0o7777)
        full_type[p[3]] = mode & 0o170000
    if rules_path:
        for line in open(rules_path):
            line = line.split('#')[0].strip()
            if not line:
                continue
            f = line.split()
            m = int(f[3], 8) if len(f) > 3 else None
            want[f[0]] = (int(f[1]), int(f[2]), m & 0o7777 if m is not None else None)
            if m is not None:
                full_type[f[0]] = m & 0o170000

    parent_of, name_of, where, type_of = {}, {}, {}, {}
    nrecords = 0
    for node, off, key_parent, key_name, rtype, cnid in h.records():
        nrecords += 1
        if rtype in (RT_FOLDER, RT_FILE):
            parent_of[cnid] = key_parent
            name_of[cnid] = key_name
            where[cnid] = (node, off)
            type_of[cnid] = rtype

    path_of = {}
    for cnid in parent_of:
        parts, cur, seen = [], cnid, set()
        ok = True
        while cur != ROOT_CNID:
            if cur in seen or cur not in parent_of:
                ok = False
                break
            seen.add(cur)
            parts.append(name_of[cur])
            cur = parent_of[cur]
        path_of[cnid] = '/'.join(reversed(parts)) if ok else f'<orphan:{cnid}>'

    private_dirs = {cnid for cnid, p in path_of.items()
                    if 'HFS+ Private' in p or name_of.get(cnid, '').startswith('iNode')}

    lookup = {}
    for cnid, p in path_of.items():
        for n in norm_set(p):
            lookup.setdefault(n, cnid)

    matched, plan, skipped_foreign = set(), [], 0
    repair = '--repair-corrupt' in argv
    for cnid in sorted(where):
        if cnid == ROOT_CNID:
            continue
        node, off = where[cnid]
        po = node * h.nodeSize + off + PERM_OFF
        cur_uid, cur_gid = be('II', h.cat, po)
        admin_flags, owner_flags = be('BB', h.cat, po + 8)
        cur_mode, cur_special = be('HH', h.cat, po + 10)
        if repair:
            # v1-offset-bug signatures: (a) mode field 0 with the true mode parked in the
            # BSD flags; (b) mode field holding the old `special` (e.g. 4) -> invalid BSD
            # file type. Recover (a) from the flags, (b) from the manifest, else a sane
            # default by record type. uid/gid/special were written correctly by the bug.
            valid_type = (cur_mode & 0o170000) in (0o100000, 0o040000, 0o120000, 0o020000,
                                                   0o060000, 0o140000)
            if valid_type:
                continue
            flags_mode = (admin_flags << 8) | owner_flags
            if (flags_mode & 0o170000) in (0o100000, 0o040000, 0o120000):
                fixed = flags_mode
            else:
                want_m = None
                for n in norm_set(path_of[cnid]):
                    if n in want:
                        want_m = want[n][2]
                        break
                if want_m is not None:
                    fixed = want_m | (0o100000 if type_of[cnid] == RT_FILE else 0o040000)
                else:
                    fixed = (0o040000 if type_of[cnid] == RT_FOLDER else 0o100000) | 0o755
            plan.append((po, cur_uid, cur_gid, fixed, cur_special, path_of[cnid],
                         cur_uid, cur_gid, cur_mode))
            continue
        p = path_of[cnid]
        explicit = None
        for n in norm_set(p):
            if n in want:
                explicit = want[n]
                matched.add(n)
                break
        if explicit is not None:
            target = explicit                     # manifest/rules: always applied
        elif cnid in private_dirs:
            target = (0, 0, None)
        elif p.startswith('private/var/mobile'):
            target = (501, 501, None)
        elif p.startswith(('private/var/db/diagnostics', 'private/var/db/uuidtext')):
            target = (272, 272, None)
        elif cur_uid != 501:
            # no target and not created by the population run (Apple/kernel records,
            # hardlink-encoding link records): never touch
            skipped_foreign += 1
            continue
        else:
            target = (0, 0, None)
        uid, gid, perm = target
        # PRESERVE the BSD type bits: the manifest carries them (S_IFLNK for symlinks!),
        # the rules/default targets carry only permissions. Writing permission-only modes
        # turned every manifest symlink into a regular file (whose content is its target).
        new_perm = perm if perm is not None else (cur_mode & 0o7777)
        rec_t = type_of[cnid]
        man_t = full_type.get(p)
        if rec_t == RT_FILE:
            # a file record may be a regular file or a symlink: the manifest decides
            new_type = man_t if man_t in (0o100000, 0o120000) else 0o100000
        else:
            new_type = 0o040000
        new_mode = new_type | new_perm
        needs_flag_reset = (admin_flags, owner_flags) != (0, 0)
        if (cur_uid, cur_gid, cur_mode) != (uid, gid, new_mode) or needs_flag_reset:
            plan.append((po, uid, gid, new_mode, cur_special, p, cur_uid, cur_gid, cur_mode))

    print(f'catalog: {nrecords} records, {len(where)} folder/file, nodeSize {h.nodeSize}, '
          f'volume free blocks {h.freeBlocks}')
    print(f'{"APPLY" if apply else "PLAN"}: {len(plan)} of {len(where)} records to rewrite, '
          f'{skipped_foreign} not ours (uid!=501: Apple/kernel records, hardlink encodings), '
          f'{len(want) - len(matched)} of {len(want)} mapping paths unmatched')

    unmatched = sorted(set(want) - matched)
    if unmatched and not repair:
        print(f'-- {len(unmatched)} mapping paths NOT matched in the catalog (first 15):')
        for p in unmatched[:15]:
            print('   ', p)
    if apply and not repair and len(unmatched) > 0.02 * len(want):
        print('REFUSING: too many unmatched paths — manifest/volume mismatch? (nothing written)')
        return 2

    if apply:
        for po, uid, gid, mode, special, _p, _cu, _cg, _cm in plan:
            # HFSPlusBSDInfo: ownerID@+0 groupID@+4 adminFlags@+8 ownerFlags@+9 mode@+10
            # special@+12 — flags stay 0 (extraction carries no BSD flags), mode is a u16
            struct.pack_into('>IIBBHH', h.cat, po, uid, gid, 0, 0, mode, special)
        h.write_back()
        print(f'written: {len(plan)} catalog records patched, catalog fork written back '
              f'({len(h.cat)} bytes via {len(h.cat_ext)} extent(s))')
    else:
        for po, uid, gid, mode, _sp, p, cu, cg, cm in plan[:25]:
            print(f'   {p}: {cu}:{cg} {cm:o} -> {uid}:{gid} {mode:o}')
        if len(plan) > 25:
            print(f'   ... and {len(plan) - 25} more')
    return 0


if __name__ == '__main__':
    sys.exit(main())
