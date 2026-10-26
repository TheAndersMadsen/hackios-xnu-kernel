#!/usr/bin/env python3
"""Generate the N10 20H392 identity-record overlay.

The exact account facts come from the target OS's own private/etc/passwd,
master.passwd and group.  The native dslocal representation follows Apple's
on-disk convention (arrays of short native keys) and Apple's deterministic
system UUIDs, corroborated by DarwinDirectory.py and real iOS records such as
_usbmuxd.  No random UUIDs and no account IDs are invented.

The cached launchd graph's strict UserName/GroupName minimum is recorded in
MANIFEST.json.  We stage the complete small 20H392 account database instead,
so later daemon-side getpwuid/getgrnam/initgroups calls see the same closure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat

EXPECTED_USERS = 41
EXPECTED_GROUPS = 99
USER_UUID = 'FFFFEEEE-DDDD-CCCC-BBBB-AAAA%08X'
GROUP_UUID = 'ABCDEFAB-CDEF-ABCD-EFAB-CDEF%08X'
GROUP_REALNAMES = {
    'wheel': 'System Group', 'daemon': 'Daemon', 'kmem': 'Kernel Memory',
    'sys': 'System', 'tty': 'Terminal', 'operator': 'System Operators',
    'mail': 'SMTP Mail', 'bin': 'Binary', 'owner': 'Creator Owner',
    'everyone': 'Everyone', 'group': 'Creator Group', 'staff': 'Staff',
    'nogroup': 'No Group', 'nobody': 'Nobody',
    'network': 'Network Config Users', 'localaccounts': 'Local Accounts',
    'netaccounts': 'Network Accounts', 'netusers': 'Network Users',
    'authedusers': 'Authenticated Users',
    'interactusers': 'Interactive Users',
    'consoleusers': 'Terminal Server User', 'admin': 'Administrators',
    'accessibility': 'Accessibility Group', 'dialer': 'Dialup',
}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def lines(path):
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield line.split(':')


def uuid32(template, value):
    return template % (value & 0xffffffff)


def launchd_names(path):
    root = plistlib.loads(path.read_bytes())
    users, groups = set(), set()
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('UserName'), str):
                users.add(value['UserName'])
            if isinstance(value.get('GroupName'), str):
                groups.add(value['GroupName'])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(root)
    return users, groups


def dump(path, value):
    path.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True))
    path.chmod(0o600)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--os-root', type=Path,
                    default=Path('iosroot/hfs/work/rootfs'))
    ap.add_argument('--launchd-plist', type=Path,
                    default=Path('research/laguna-deviceroot/work/root-min/System/Library/xpc/launchd.plist'))
    ap.add_argument('--expected-users', type=int, default=EXPECTED_USERS,
                    help='account count the target root must have '
                         '(the default pins the 20H392 root; pass your own)')
    ap.add_argument('--expected-groups', type=int, default=EXPECTED_GROUPS,
                    help='group count the target root must have '
                         '(the default pins the 20H392 root; pass your own)')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    os_root = a.os_root.resolve()
    launchd_plist = a.launchd_plist.resolve()
    out = a.out.resolve()
    etc = os_root / 'private/etc'
    for name in ('passwd', 'master.passwd', 'group'):
        if not (etc / name).is_file():
            raise SystemExit(f'missing {etc/name}')
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'refusing to overwrite non-empty {out}')
    out.mkdir(parents=True, exist_ok=True)

    master = {}
    for f in lines(etc / 'master.passwd'):
        if len(f) != 10:
            raise SystemExit(f'malformed master.passwd row: {f!r}')
        master[f[0]] = dict(name=f[0], password=f[1], uid=int(f[2]),
                            gid=int(f[3]), realname=f[7], home=f[8], shell=f[9])
    public = {}
    for f in lines(etc / 'passwd'):
        if len(f) != 7:
            raise SystemExit(f'malformed passwd row: {f!r}')
        public[f[0]] = dict(name=f[0], password=f[1], uid=int(f[2]),
                            gid=int(f[3]), realname=f[4], home=f[5], shell=f[6])
    groups = {}
    for f in lines(etc / 'group'):
        if len(f) != 4:
            raise SystemExit(f'malformed group row: {f!r}')
        groups[f[0]] = dict(name=f[0], password=f[1], gid=int(f[2]),
                            members=f[3].split(',') if f[3] else [])
    if len(master) != a.expected_users or len(groups) != a.expected_groups:
        raise SystemExit(f'unexpected account counts: {len(master)} users, '
                         f'{len(groups)} groups (wanted {a.expected_users}/'
                         f'{a.expected_groups}; pass --expected-users/--expected-groups '
                         f'when your target root differs from the 20H392 pin)')
    if set(master) != set(public):
        raise SystemExit('passwd/master.passwd account sets differ')
    for name, user in master.items():
        other = public[name]
        for key in ('name', 'password', 'uid', 'gid', 'realname', 'home', 'shell'):
            if user[key] != other[key]:
                raise SystemExit(f'passwd/master.passwd mismatch: {name} {key}')
    if len({u['uid'] for u in master.values()}) != len(master):
        raise SystemExit('duplicate UID')
    if len({g['gid'] for g in groups.values()}) != len(groups):
        raise SystemExit('duplicate GID')
    job_users, job_groups = launchd_names(launchd_plist)
    if job_users - master.keys() or job_groups - groups.keys():
        raise SystemExit(f'launchd identities absent from OS database: '
                         f'users={sorted(job_users-master.keys())}, '
                         f'groups={sorted(job_groups-groups.keys())}')

    # Byte-exact Libinfo file-module inputs, not regenerated text.
    etc_out = out / 'private/etc'
    etc_out.mkdir(parents=True, exist_ok=True)
    for name, mode in (('passwd', 0o644), ('master.passwd', 0o600), ('group', 0o644)):
        shutil.copyfile(etc / name, etc_out / name)
        (etc_out / name).chmod(mode)
    etc_out.chmod(0o755)

    default = out / 'private/var/db/dslocal/nodes/Default'
    users_out = default / 'users'
    groups_out = default / 'groups'
    config_out = default / 'config'
    for directory in (users_out, groups_out, config_out):
        directory.mkdir(parents=True, exist_ok=True)
    dropped = []
    for name, user in master.items():
        record = {
            'generateduid': [uuid32(USER_UUID, user['uid'])],
            'gid': [str(user['gid'])],
            'home': [user['home']],
            'name': [name],
            # dslocal is not being used as a password store in this overlay.
            'passwd': ['*'],
            'realname': [user['realname']],
            'shell': [user['shell']],
            'uid': [str(user['uid'])],
        }
        dump(users_out / f'{name}.plist', record)
    for name, group in groups.items():
        members = []
        for member in group['members']:
            if member in master:
                members.append(member)
            else:
                dropped.append({'group': name, 'member': member,
                                'reason': 'no UID in 20H392 master.passwd; same rule as Apple DarwinDirectory.py'})
        record = {
            'generateduid': [uuid32(GROUP_UUID, group['gid'])],
            'gid': [str(group['gid'])],
            'name': [name],
            'passwd': ['*'],
            'realname': [GROUP_REALNAMES.get(name,
                         master[name]['realname'] if name in master else name)],
        }
        if members:
            record['users'] = members
            record['groupmembers'] = [uuid32(USER_UUID, master[m]['uid']) for m in members]
        dump(groups_out / f'{name}.plist', record)

    # Match the native node: root-only Default, browsable record directories.
    for directory in [out / 'private', etc_out, out / 'private/var',
                      out / 'private/var/db', out / 'private/var/db/dslocal',
                      out / 'private/var/db/dslocal/nodes', users_out,
                      groups_out, config_out]:
        directory.chmod(0o755)
    default.chmod(0o700)

    files = []
    for path in sorted(p for p in out.rglob('*') if p.is_file()):
        files.append({'path': '/' + str(path.relative_to(out)),
                      'bytes': path.stat().st_size, 'mode': f'{stat.S_IMODE(path.stat().st_mode):04o}',
                      'uid': 0, 'gid': 0, 'sha256': sha256(path)})
    manifest = {
        'format': 'n10-20H392-identity-overlay/1',
        'source_os_root': str(os_root),
        'source_flat_files': {name: {'sha256': sha256(etc / name),
                                    'bytes': (etc / name).stat().st_size}
                              for name in ('passwd', 'master.passwd', 'group')},
        'source_launchd_plist': str(launchd_plist),
        'source_launchd_plist_sha256': sha256(launchd_plist),
        'strict_launchd_minimum': {'users': sorted(job_users), 'groups': sorted(job_groups),
                                   'user_count': len(job_users), 'group_count': len(job_groups)},
        'staged_account_closure': {'users': sorted(master), 'groups': sorted(groups),
                                   'user_count': len(master), 'group_count': len(groups)},
        'job_coverage_complete': True,
        'not_launchd_usernames': ['_launchd', '_backboardd'],
        'backboardd_and_springboard_user': 'mobile',
        'dslocal_layout': 'private/var/db/dslocal/nodes/Default/{users,groups,config}',
        'dslocal_record_format': 'XML plist; native short keys; every value an array',
        'uuid_rules': {'users': USER_UUID, 'groups': GROUP_UUID,
                       'suffix': 'unsigned 32-bit ID, uppercase hexadecimal, zero-padded to 8 digits'},
        'ownership_plan': {'directories': '0:0; Default 0700, others 0755',
                           'dslocal_files': '0:0 0600',
                           'private/etc': 'passwd/group 0:0 0644; master.passwd 0:0 0600'},
        'field_provenance': {
            'exact_from_20H392': ['account names', 'UIDs', 'GIDs', 'home directories', 'shells',
                                  'user real names', 'group members', 'all three flat files'],
            'derived_by_apple_rule': ['GeneratedUUIDs', 'GroupMembers UUIDs'],
            'generated_not_apple_byte_dump': ['dslocal XML serialization', 'group real-name fallback',
                                              'dslocal Password=* (no credential store)'],
        },
        'dropped_group_members_without_uids': dropped,
        'files': files,
    }
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'out': str(out), 'users': len(master), 'groups': len(groups),
                      'strict_job_users': len(job_users), 'strict_job_groups': len(job_groups),
                      'files': len(files), 'dropped_unknown_group_members': len(dropped),
                      'manifest': str(out / 'MANIFEST.json')}, indent=2))


if __name__ == '__main__':
    main()
