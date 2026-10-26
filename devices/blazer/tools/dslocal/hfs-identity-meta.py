#!/usr/bin/env python3
"""Set/check exact metadata for N10's identity overlay in a raw HFS+ image.

The first N10 owner pass used the older all-node catalog scanner.  That scanner
does not see records allocated in the current catalog leaf chain, so the 140
dslocal plists and five descendant directories remained 501:20.  This tool
uses iosroot's authoritative offset-array/leaf-chain parser and touches only
the paths authorized by records/MANIFEST.json plus the seven overlay
directories.  Content and modes are never changed.

The image must be unmounted.  With no --apply, exit zero only when all targets
are already 0:0.  With --apply, rewrite owner/group to 0:0 and re-check.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import stat
import struct


HERE = Path(__file__).resolve().parent
# The kit ships the authoritative parser (hfs-fixmeta.py) next to this tool.
# Fall back to the un-vendored host checkout layout, iosroot/hfs/tools/.
_FIXMETA_CANDIDATES = [HERE / 'hfs-fixmeta.py']
_FIXMETA_CANDIDATES += [parent / 'iosroot' / 'hfs' / 'tools' / 'hfs-fixmeta.py'
                        for parent in HERE.parents]
FIXMETA = next((c for c in _FIXMETA_CANDIDATES if c.is_file()),
               _FIXMETA_CANDIDATES[0])

spec = importlib.util.spec_from_file_location('daybreak_hfs_fixmeta', FIXMETA)
fixmeta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixmeta)

DIRECTORIES = {
    'private/etc': 0o755,
    'private/var/db/dslocal': 0o755,
    'private/var/db/dslocal/nodes': 0o755,
    'private/var/db/dslocal/nodes/Default': 0o700,
    'private/var/db/dslocal/nodes/Default/users': 0o755,
    'private/var/db/dslocal/nodes/Default/groups': 0o755,
    'private/var/db/dslocal/nodes/Default/config': 0o755,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image', type=Path)
    ap.add_argument('--manifest', type=Path, required=True,
                    help="the overlay's MANIFEST.json (from "
                         "make-identity-records.py); it authorizes every "
                         "path this tool may touch")
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    manifest = json.loads(a.manifest.read_text())
    targets = dict(DIRECTORIES)
    for entry in manifest['files']:
        path = entry['path'].lstrip('/')
        if path in targets:
            raise SystemExit(f'duplicate target in manifest: {path}')
        targets[path] = int(entry['mode'], 8)

    h = fixmeta.HFSImage(a.image)
    parent_of, name_of, where, type_of = {}, {}, {}, {}
    records = 0
    for node, off, parent, name, rtype, cnid in h.records():
        records += 1
        if rtype in (fixmeta.RT_FOLDER, fixmeta.RT_FILE):
            parent_of[cnid] = parent
            name_of[cnid] = name
            where[cnid] = (node, off)
            type_of[cnid] = rtype

    path_to_cnid = {}
    for cnid in parent_of:
        parts, cur, seen = [], cnid, set()
        while cur != fixmeta.ROOT_CNID:
            if cur in seen or cur not in parent_of:
                break
            seen.add(cur)
            parts.append(name_of[cur])
            cur = parent_of[cur]
        else:
            path_to_cnid['/'.join(reversed(parts))] = cnid

    missing = sorted(set(targets) - set(path_to_cnid))
    if missing:
        raise SystemExit('identity paths missing from catalog: ' + ', '.join(missing))

    rows, changes = [], []
    for path, expected_perm in sorted(targets.items()):
        cnid = path_to_cnid[path]
        node, off = where[cnid]
        po = node * h.nodeSize + off + fixmeta.PERM_OFF
        uid, gid = fixmeta.be('II', h.cat, po)
        mode, = fixmeta.be('H', h.cat, po + 10)
        rtype = type_of[cnid]
        expected_type = stat.S_IFDIR if path in DIRECTORIES else stat.S_IFREG
        if stat.S_IFMT(mode) != expected_type:
            raise SystemExit(f'{path}: type {mode:o}, expected {expected_type:o}')
        if stat.S_IMODE(mode) != expected_perm:
            raise SystemExit(f'{path}: mode {mode:o}, expected permissions {expected_perm:o}')
        row = {'path': path, 'uid': uid, 'gid': gid, 'mode': f'{mode:o}'}
        rows.append(row)
        if (uid, gid) != (0, 0):
            changes.append((po, path, uid, gid))

    print(json.dumps({
        'image': str(a.image.resolve()),
        'catalog_records': records,
        'catalog_objects': len(where),
        'authorized_targets': len(targets),
        'non_root_targets': len(changes),
        'sample_changes': [
            {'path': path, 'from': f'{uid}:{gid}', 'to': '0:0'}
            for _po, path, uid, gid in changes[:20]
        ],
    }, indent=2))

    if not a.apply:
        return 1 if changes else 0

    for po, _path, _uid, _gid in changes:
        struct.pack_into('>II', h.cat, po, 0, 0)
    h.write_back()

    # Verify the in-memory catalog after the exact rewrite.  A subsequent
    # ownership-enabled mount is still the independent acceptance gate.
    for po, path, _uid, _gid in changes:
        if fixmeta.be('II', h.cat, po) != (0, 0):
            raise SystemExit(f'post-write metadata mismatch: {path}')
    print(f'APPLIED: {len(changes)} exact identity records -> 0:0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
