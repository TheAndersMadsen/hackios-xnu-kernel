#!/bin/bash
# Stage a generated identity-record overlay onto a cacheroot HFS+ image.
# Host-only; no guest and no device.  The source image is never opened rw.
#
# Environment:
#   BASE             the base image to clone and extend (must exist)
#   RECORDS          the overlay directory from make-identity-records.py
#                    (must contain MANIFEST.json)
#   OUT              the new image to create (must not exist)
#   EXPECTED_SHA256  optional pin; when set, BASE must hash to it, proving you
#                    are staging onto the exact base you audited
#   MNT              scratch mountpoint (default under /tmp/fleet)
#
# The laguna run that proved this recipe: BASE = N1's pruned/root-owned
# cacheroot (libdispatch pruned, cache uid 0), OUT = a 150/150-records-0:0
# image that passed a 940-check identity gate plus the cacheroot/fsck gate.
set -euo pipefail
cd "$(dirname "$0")"
KIT=$PWD
BASE=${BASE:?set BASE to the cacheroot image to extend}
RECORDS=${RECORDS:?set RECORDS to the generated overlay directory}
OUT=${OUT:?set OUT to the new image path}
EXPECTED=${EXPECTED_SHA256:-}
MNT=${MNT:-/tmp/fleet/identity-stage-mnt}

[ -f "$BASE" ] || { echo "missing base $BASE" >&2; exit 1; }
[ -f "$RECORDS/MANIFEST.json" ] || { echo "missing generated records; run make-identity-records.py" >&2; exit 1; }
[ ! -e "$OUT" ] || { echo "refusing to overwrite $OUT" >&2; exit 1; }
if [ -n "$EXPECTED" ]; then
  actual=$(shasum -a 256 "$BASE" | awk '{print $1}')
  [ "$actual" = "$EXPECTED" ] || { echo "base sha $actual != pin $EXPECTED" >&2; exit 1; }
fi
mkdir -p "$(dirname "$OUT")"
cp -c "$BASE" "$OUT"
actual=$(shasum -a 256 "$OUT" | awk '{print $1}')
[ "$actual" = "$(shasum -a 256 "$BASE" | awk '{print $1}')" ] || { echo "clone sha mismatch $actual" >&2; exit 1; }

rm -rf "$MNT"
mkdir -p "$MNT"
cleanup() { hdiutil detach "$MNT" -quiet 2>/dev/null || true; }
trap cleanup EXIT
hdiutil attach -owners off -nobrowse -mountpoint "$MNT" "$OUT"
[ ! -e "$MNT/usr/lib/system/libdispatch.dylib" ] || { echo "base still has the split libdispatch; prune it first" >&2; exit 1; }
[ ! -e "$MNT/private/var/db/dslocal" ] || { echo "base unexpectedly already has dslocal" >&2; exit 1; }

gcp -a "$RECORDS/private/." "$MNT/private/"
# Explicit modes; the raw catalog pass below supplies uid/gid 0 without sudo.
chmod 0755 "$MNT/private/etc" \
  "$MNT/private/var/db/dslocal" \
  "$MNT/private/var/db/dslocal/nodes" \
  "$MNT/private/var/db/dslocal/nodes/Default/users" \
  "$MNT/private/var/db/dslocal/nodes/Default/groups" \
  "$MNT/private/var/db/dslocal/nodes/Default/config"
chmod 0700 "$MNT/private/var/db/dslocal/nodes/Default"
chmod 0644 "$MNT/private/etc/passwd" "$MNT/private/etc/group"
chmod 0600 "$MNT/private/etc/master.passwd"
find "$MNT/private/var/db/dslocal/nodes/Default" -type f -name '*.plist' -exec chmod 0600 {} +
sync
hdiutil detach "$MNT" -quiet
trap - EXIT

# Scope-limited raw HFS+ uid patch: only the newly added identity trees.
python3 "$KIT/hfs-rootown-identity.py" "$OUT" --apply
if [ -n "$EXPECTED" ]; then
  actual=$(shasum -a 256 "$BASE" | awk '{print $1}')
  [ "$actual" = "$EXPECTED" ] || { echo "SOURCE CHANGED $actual" >&2; exit 1; }
fi
shasum -a 256 "$OUT"
printf 'STAGED %s\n' "$OUT"
