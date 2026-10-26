# dslocal identity records — the staging kit

XNU's launchd resolves every daemon's `UserName`/`GroupName` through the
account database, backboardd and SpringBoard run as `mobile`, and XNU refuses
the dyld shared region unless the cache file is owned by uid 0. A root image
that carries no account records dies early (`unable to resolve mobile`), and a
root image built by a non-root host builder carries the builder's uid 501
everywhere. This kit stages **real** identity records for the target OS into a
cacheroot image, root-owned, with every change verified.

Everything here is the project's own Python/bash code — no Apple content is
committed or distributed. The records are **generated from your own target
root** (your IPSW's `private/etc/{passwd,master.passwd,group}`), following
Apple's on-disk dslocal conventions: XML plists, arrays of short native keys,
and Apple's deterministic system UUID schemes. No account IDs, UUIDs or
passwords are invented; `passwd` is staged as `*` because dslocal is not used
as a credential store here.

These tools were written for the laguna (Pixel 10 Pro / Tensor G5) bring-up
and are device-independent — any port stages identity the same way. Files are
copied from the working lanes with their licensing preserved; the repository's
`LICENSE`/`LICENSE.APSL` terms apply unchanged.

## Files

| File | Role |
| --- | --- |
| `make-identity-records.py` | generate the overlay: flat `private/etc` files byte-exact from the target root, plus one dslocal plist per user/group, and `MANIFEST.json` pinning every byte, mode and owner |
| `stage-identity.sh` | clone the base image, copy the overlay in, set exact modes, then raw-catalog root-own the identity trees |
| `hfs-identity-meta.py` | check/set exact metadata for the overlay **in the raw HFS+ catalog** — the acceptance gate for ownership |
| `hfs-rootown-identity.py` | scope-limited root-own wrapper (only `/private/etc` and `/private/var/db/dslocal`) |
| `hfs-fixmeta.py` | the authoritative HFS+ catalog offset-array/leaf-chain parser the two tools above are built on |
| `hfs-rootown.py` | the generic catalog root-own patcher (shared-cache uid-0 rule) |
| `verify-identity.py` | verify every staged byte/mode/owner, launchd's strict account coverage, and the inventory delta against the base |
| `verify-identity.sh` | the full read-only gate: content pass → raw-catalog ownership gate → owners-on mount witnesses |

## The recipe

```sh
cd devices/blazer/tools/dslocal

# 1. Generate records from YOUR target root.
#    --expected-users/--expected-groups pin the account counts; the defaults
#    (41/99) are the pin of the 20H392 root the recipe was proven on. Pass
#    your own counts. The launchd plist is the cached graph whose strict
#    UserName/GroupName minimum must be covered by the records.
python3 make-identity-records.py \
  --os-root /path/to/your/target-rootfs \
  --launchd-plist /path/to/System/Library/xpc/launchd.plist \
  --expected-users 41 --expected-groups 99 \
  --out /tmp/identity-records

# 2. Stage onto your cacheroot (clone; the base is never opened read-write).
BASE=/path/to/cacheroot.hfs \
RECORDS=/tmp/identity-records \
OUT=/tmp/cacheroot-identity.hfs \
EXPECTED_SHA256=<sha256 of the audited base, optional but recommended> \
  bash stage-identity.sh

# 3. Verify: 940-check-class identity gate + ownership witnesses.
IMG=/tmp/cacheroot-identity.hfs \
BASE=/path/to/cacheroot.hfs \
RECORDS=/tmp/identity-records \
EXPECTED_SHA256=<same pin> \
  bash verify-identity.sh
```

`stage-identity.sh` refuses to run when the base still has
`/usr/lib/system/libdispatch.dylib` (the dyld-1042 disk-override rule requires
it absent — prune it first) or when the base already has a dslocal tree.

## What the verification proves

The laguna run this recipe was proven with: 41 users + 99 groups generated
from the root's own flat files; 140 dslocal plists + 3 flat files + 7
directories = **150 authorized targets, all `0:0`**; modes `0600` for dslocal
plists and `master.passwd`, `0644` for `passwd`/`group`, `0700` for the
`Default` node, `0755` for its record directories; complete coverage of the
cached launchd graph's strict minimum (22 users, 14 groups); root `0:0`,
`mobile` `501:501` with home `/var/mobile`; shared cache and `launchd` left
uid 0; `mobile`'s home left `501:20`; zero missing/changed pre-existing
records and zero unauthorized additions — the full gate (940 checks) plus the
cacheroot fsck gate passed, with the image byte-stable across the read-only
checks and the source base unchanged.

Two lessons are baked into the tools:

- **Ownership needs the raw catalog, not a mount.** The first pass copied
  correct content but an owners-enabled mount showed the plists still
  `501:20`, because the builder's uid survives staging and macOS `chown`
  needs root. `hfs-rootown-identity.py` patches the catalog `uid/gid` fields
  in place without sudo, and `hfs-identity-meta.py` re-checks every target.
- **The naive catalog scanner lies.** An all-node scanner missed every record
  allocated in the current catalog leaf chain and reported `would patch 0`.
  The parser used here (`hfs-fixmeta.py`) derives record boundaries from the
  node offset array and walks the leaf chain; the verifier separately proves
  ownership with owners-enabled mounts so no single method is trusted alone.

## Porting notes

- The overlay also ships the byte-exact `private/etc/{passwd,master.passwd,group}`
  from your target root, so later `getpwuid`/`getgrnam`/`initgroups` calls see
  the same closure in both stores (flat files and dslocal).
- The image must be unmounted for the raw-catalog tools, and the verifier's
  owners-enabled witnesses need macOS to mount with owners on (it will show
  the real uid/gid; the content pass runs on a second owners-disabled mount
  so a non-root operator can still read `0600` files).
- `stage-identity.sh` uses `cp -c` (APFS clonefile) and `gcp -a` (GNU coreutils
  cp, `brew install coreutils` on macOS); on Linux, substitute `cp
  --reflink=auto -a`.
- Honest boundary: these records fix the *identity* wall. Daemon-level dslocal
  consumers beyond launchd's account resolution can still have their own
  content requirements — diagnose those separately rather than assuming the
  account closure covers them.
