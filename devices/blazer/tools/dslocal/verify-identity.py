#!/usr/bin/env python3
"""Verify the mounted N10 image against its record manifest and N1 base.

Run with ownership-enabled read-only mounts.  This checks every staged byte,
mode and owner, launchd's strict account coverage, key N1 invariants, and the
non-directory inventory delta against the base.  HFS mount/fsevents artifacts
are reported separately rather than mistaken for identity records.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import sys

MOUNT_LITTER = ('.fseventsd', '.Trashes', '.metadata_never_index', '.VolumeIcon.icns')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def is_litter(rel):
    return any(part in MOUNT_LITTER for part in rel.parts)


def inventory(root):
    result = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_dir() and not path.is_symlink():
                continue
            rel = path.relative_to(root)
            s = path.lstat()
            result[str(rel)] = (stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid,
                                s.st_size, s.st_nlink, stat.S_IFMT(s.st_mode))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mount', type=Path, required=True)
    ap.add_argument('--content-mount', type=Path,
                    help='same read-only image mounted with owners disabled; '
                         'allows a non-root verifier to read root:wheel 0600 files')
    ap.add_argument('--skip-owner-checks', action='store_true',
                    help='content/mode pass on an owners-disabled mount; pair '
                         'with hfs-identity-meta.py and an owners-on spot check')
    ap.add_argument('--records', type=Path, required=True)
    ap.add_argument('--base-mount', type=Path)
    a = ap.parse_args()
    mount = a.mount.resolve()
    content = a.content_mount.resolve() if a.content_mount else mount
    records = a.records.resolve()
    manifest = json.loads((records / 'MANIFEST.json').read_text())
    failures = []
    passes = []
    def check(ok, message):
        (passes if ok else failures).append(message)

    # Every staged regular file: exact bytes plus root ownership and mode.
    for entry in manifest['files']:
        rel = entry['path'].lstrip('/')
        path = mount / rel
        content_path = content / rel
        ok = path.is_file()
        check(ok, f'present {entry["path"]}')
        if not ok:
            continue
        s = path.stat()
        check(sha(content_path) == entry['sha256'], f'bytes {entry["path"]}')
        check(stat.S_IMODE(s.st_mode) == int(entry['mode'], 8), f'mode {entry["path"]}')
        if not a.skip_owner_checks:
            check((s.st_uid, s.st_gid) == (entry['uid'], entry['gid']),
                  f'owner {entry["path"]}')

    default = mount / 'private/var/db/dslocal/nodes/Default'
    for rel, mode in (
        ('private/etc', 0o755),
        ('private/var/db/dslocal', 0o755),
        ('private/var/db/dslocal/nodes', 0o755),
        ('private/var/db/dslocal/nodes/Default', 0o700),
        ('private/var/db/dslocal/nodes/Default/users', 0o755),
        ('private/var/db/dslocal/nodes/Default/groups', 0o755),
        ('private/var/db/dslocal/nodes/Default/config', 0o755),
    ):
        path = mount / rel
        check(path.is_dir(), f'directory {rel}')
        if path.is_dir():
            s = path.stat()
            if not a.skip_owner_checks:
                check((s.st_uid, s.st_gid) == (0, 0), f'directory owner {rel}')
            check(stat.S_IMODE(s.st_mode) == mode, f'directory mode {rel}')

    for kind in ('users', 'groups'):
        expected = set(manifest['staged_account_closure'][kind])
        actual = {p.stem for p in (default / kind).glob('*.plist')}
        check(actual == expected, f'dslocal {kind} set ({len(actual)}/{len(expected)})')
        for name in sorted(expected & actual):
            value = plistlib.loads((content / 'private/var/db/dslocal/nodes/Default' /
                                    kind / f'{name}.plist').read_bytes())
            check(all(isinstance(v, list) for v in value.values()), f'array values {kind}/{name}')
            if kind == 'users':
                uid = master_uid = int(value['uid'][0])
                check(value['name'][0] == name, f'record name users/{name}')
                check(value['generateduid'][0] == manifest['uuid_rules']['users'] % (master_uid & 0xffffffff),
                      f'generateduid users/{name}')
            else:
                gid = int(value['gid'][0])
                check(value['name'][0] == name, f'record name groups/{name}')
                check(value['generateduid'][0] == manifest['uuid_rules']['groups'] % (gid & 0xffffffff),
                      f'generateduid groups/{name}')
                if 'users' in value:
                    check(len(value['users']) == len(value.get('groupmembers', [])),
                          f'parallel members groups/{name}')

    # Launchd's strict minimum is fully represented in both native stores.
    minimum = manifest['strict_launchd_minimum']
    for name in minimum['users']:
        check((default / 'users' / f'{name}.plist').is_file(), f'job user {name}')
    for name in minimum['groups']:
        check((default / 'groups' / f'{name}.plist').is_file(), f'job group {name}')
    content_default = content / 'private/var/db/dslocal/nodes/Default'
    mobile = plistlib.loads((content_default / 'users/mobile.plist').read_bytes())
    check((mobile['uid'][0], mobile['gid'][0], mobile['home'][0]) == ('501', '501', '/var/mobile'),
          'mobile is 501:501 /var/mobile')
    root = plistlib.loads((content_default / 'users/root.plist').read_bytes())
    check((root['uid'][0], root['gid'][0]) == ('0', '0'), 'root is 0:0')
    securityd = plistlib.loads((content_default / 'users/_securityd.plist').read_bytes())
    check((securityd['uid'][0], securityd['gid'][0]) == ('64', '64'), '_securityd is 64:64')
    launchd = plistlib.loads((content / 'System/Library/xpc/launchd.plist').read_bytes())
    backboard = launchd['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.backboardd.plist']
    springboard = launchd['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.SpringBoard.plist']
    check(backboard['UserName'] == springboard['UserName'] == 'mobile',
          'backboardd and SpringBoard run as mobile (not _backboardd)')
    check('_launchd' not in manifest['staged_account_closure']['users'], 'no invented _launchd account')

    # N1's decisive invariants must survive.
    check(not (mount / 'usr/lib/system/libdispatch.dylib').exists(), 'libdispatch remains pruned')
    cache = mount / 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
    check(cache.is_file() and (a.skip_owner_checks or cache.stat().st_uid == 0),
          'shared cache remains present (uid checked separately)' if a.skip_owner_checks
          else 'shared cache remains uid 0')
    mobile_home = mount / 'private/var/mobile'
    if not a.skip_owner_checks:
        check((mobile_home.stat().st_uid, mobile_home.stat().st_gid) == (501, 20),
              'mobile home remains 501:20')

    delta = None
    if a.base_mount:
        base = a.base_mount.resolve()
        before = inventory(base)
        after = inventory(mount)
        missing_all = sorted(set(before) - set(after))
        missing = [name for name in missing_all if not is_litter(Path(name))]
        added = sorted(set(after) - set(before))
        changed = sorted(name for name in set(before) & set(after)
                         if before[name] != after[name] and not is_litter(Path(name)))
        authorized = {e['path'].lstrip('/') for e in manifest['files']}
        unauthorized_added = [name for name in added if name not in authorized and not is_litter(Path(name))]
        check(not missing, f'no pre-existing files removed ({len(missing)} missing)')
        check(not changed, f'no pre-existing non-directory metadata changed ({len(changed)} changed)')
        check(not unauthorized_added, f'no unauthorized added paths ({len(unauthorized_added)})')
        # Content witnesses for the key pre-existing boot inputs.
        for rel in ('sbin/launchd', 'System/Library/xpc/launchd.plist'):
            check(sha(base / rel) == sha(mount / rel), f'unchanged bytes {rel}')
        with (base / 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64').open('rb') as f:
            old_head = hashlib.md5(f.read(1 << 20)).hexdigest()
        with cache.open('rb') as f:
            new_head = hashlib.md5(f.read(1 << 20)).hexdigest()
        check(old_head == new_head, 'unchanged 1 MiB shared-cache head')
        delta = {'added': added, 'missing': missing, 'changed': changed,
                 'unauthorized_added': unauthorized_added,
                 'mount_litter_added': [n for n in added if is_litter(Path(n))],
                 'mount_litter_missing': [n for n in missing_all if is_litter(Path(n))],
                 'preexisting_non_directory_count': len(before)}

    report = {'pass_count': len(passes), 'fail_count': len(failures),
              'failures': failures, 'inventory_delta': delta,
              'staged_file_count': len(manifest['files']),
              'strict_job_user_count': minimum['user_count'],
              'strict_job_group_count': minimum['group_count']}
    print(json.dumps(report, indent=2))
    if failures:
        print('\nFAILURES:', file=sys.stderr)
        for failure in failures:
            print('  ' + failure, file=sys.stderr)
        raise SystemExit(1)
    print(f'== IDENTITY VERIFY PASS ({len(passes)} checks; {len(manifest["files"])} staged files)')


if __name__ == '__main__':
    main()
