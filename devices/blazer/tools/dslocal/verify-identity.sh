#!/bin/bash
# Read-only verification of a staged identity image against its base.
#
# Environment:
#   IMG              the staged image (required)
#   BASE             the base image it was cloned from (required for the
#                    provenance/inventory-delta checks)
#   RECORDS          the overlay directory whose MANIFEST.json governs it
#   EXPECTED_SHA256  optional pin for BASE, re-checked before and after
#   CACHEROOT_VERIFY optional path to a cacheroot/fsck checker script; it is
#                    run with IMG as its argument when set (the laguna run
#                    used the cachetransport lane's verify-cacheroot.sh)
#   OUT_SHA          optional path to write the final "sha256  image" line to
#
# Three witnesses, in order: an owners-disabled content pass (bytes, modes,
# record closure, base delta), the raw-catalog identity ownership gate, then
# owners-enabled mount witnesses for the decisive uid facts.
set -euo pipefail
cd "$(dirname "$0")"
KIT=$PWD
IMG=${IMG:?set IMG to the staged image}
BASE=${BASE:?set BASE to the base image}
RECORDS=${RECORDS:?set RECORDS to the overlay directory}
EXPECTED=${EXPECTED_SHA256:-}
MNT=${MNT:-/tmp/fleet/identity-final-mnt}
BASE_MNT=${BASE_MNT:-/tmp/fleet/identity-base-verify}
mkdir -p "$(dirname "$MNT")" "$(dirname "$BASE_MNT")"
[ -f "$IMG" ] || { echo "missing $IMG" >&2; exit 1; }
image_before=$(shasum -a 256 "$IMG" | awk '{print $1}')
base_before=$(shasum -a 256 "$BASE" | awk '{print $1}')
if [ -n "$EXPECTED" ]; then
  [ "$base_before" = "$EXPECTED" ] || { echo "base changed: $base_before != pin $EXPECTED" >&2; exit 1; }
fi
rm -rf "$MNT" "$BASE_MNT"
mkdir -p "$MNT" "$BASE_MNT"
cleanup() {
  hdiutil detach "$MNT" -quiet 2>/dev/null || true
  hdiutil detach "$BASE_MNT" -quiet 2>/dev/null || true
}
trap cleanup EXIT
hdiutil attach -readonly -owners off -nobrowse -mountpoint "$BASE_MNT" "$BASE"
hdiutil attach -readonly -owners off -nobrowse -mountpoint "$MNT" "$IMG"
python3 "$KIT/verify-identity.py" --mount "$MNT" --base-mount "$BASE_MNT" \
  --skip-owner-checks --records "$RECORDS"
hdiutil detach "$MNT" -quiet
hdiutil detach "$BASE_MNT" -quiet
trap - EXIT

# Exact raw-catalog identity ownership gate, then independent owners-on mount
# witnesses for the cache/mobile invariants and every staged plist.
python3 "$KIT/hfs-identity-meta.py" "$IMG"
rm -rf "$MNT"; mkdir -p "$MNT"
trap cleanup EXIT
hdiutil attach -readonly -owners on -nobrowse -mountpoint "$MNT" "$IMG"
[ "$(stat -f '%u' "$MNT/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64")" = 0 ]
[ "$(stat -f '%u' "$MNT/sbin/launchd")" = 0 ]
[ "$(stat -f '%u:%g' "$MNT/private/var/mobile")" = 501:20 ]
[ "$(stat -f '%u:%g:%Mp%Lp' "$MNT/private/var/db/dslocal/nodes/Default")" = 0:0:0700 ]
hdiutil detach "$MNT" -quiet
trap - EXIT
echo '== OWNERSHIP VERIFY PASS (all identity records 0:0; cache/launchd uid 0; mobile home 501:20)'

if [ -n "$CACHEROOT_VERIFY" ]; then
  bash "$CACHEROOT_VERIFY" "$IMG"
fi
image_after=$(shasum -a 256 "$IMG" | awk '{print $1}')
base_after=$(shasum -a 256 "$BASE" | awk '{print $1}')
[ "$image_after" = "$image_before" ] || { echo "verification churned image: $image_before -> $image_after" >&2; exit 1; }
if [ -n "$EXPECTED" ]; then
  [ "$base_after" = "$EXPECTED" ] || { echo "verification changed base: $base_after" >&2; exit 1; }
fi
if [ -n "$OUT_SHA" ]; then
  printf '%s  %s\n' "$image_after" "$IMG" > "$OUT_SHA"
fi
printf '== IDENTITY VERIFY COMPLETE (image stable across read-only checks): %s  %s\n' "$image_after" "$IMG"
