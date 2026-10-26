#!/usr/bin/env bash
# Host-only builder for a parent-approved temporary next boot. No device access.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
LANE="$ROOT/research/laguna-usb-console"
TL="$ROOT/hackios-xnu-kernel/blazer/teguload"
OUT=${OUT:-$LANE/out/next-boot}
R4_MACHO=${R4_MACHO:?"set R4_MACHO to the R4 msgbuf-mirror Mach-O"}
# Root of record (2026-10-26): the xnu.ramdisk bytes EMBEDDED IN the proven v6
# vendor_boot (out/blazer-devicerun-primary-vendor_boot-avb-v6.img, sha256
# 5a039972...) — i.e. the 24 MiB root actually staged as kexec md0 in the
# proven on-device ladder (probe log "ramdisk md0 ... bytes=25165824").
# The earlier pin b17e7fd9... referenced a work-dir file that was regenerated
# (now 4d9a9f59...) and no longer matches anything that ran on device.
ROOTFS=${ROOTFS:-$ROOT/research/laguna-deviceroot/work/bootstrap-root-proven-v6.hfs}
AVBTOOL="$ROOT/research/native-20261024/reuse/felix-working-source/baremetal_loader/avbtool.py"
KEY=/tmp/test-rsa4096.pem
SALT=666bb996ba0b27623969515f883297de04eaad27d148950c5f24ce0e47c1cc54
ROLLBACK=1791158400
PARTITION_SIZE=67108864

for input in "$R4_MACHO" "$ROOTFS" "$AVBTOOL" "$KEY"; do
    [[ -f $input ]] || { echo "missing required input: $input" >&2; exit 1; }
done
python3 - "$R4_MACHO" "$ROOTFS" <<'PY'
from pathlib import Path
import hashlib, sys
macho, root = map(Path, sys.argv[1:])
data = macho.read_bytes()
if b"XNU-MSGBUF v1" not in data:
    raise SystemExit("R4 Mach-O lacks XNU-MSGBUF v1; refusing a mirror-less next boot")
root_data = root.read_bytes()
if len(root_data) != 25165824 or hashlib.sha256(root_data).hexdigest() != \
        "aebc9a8a410369b19c60c3e845cb814896bd9709264d2c085283cf816439b4bf":
    raise SystemExit("root is not the proven v6 xnu.ramdisk (25165824 B, aebc9a8a...)")
PY

mkdir -p "$OUT"
python3 "$TL/mkadt.py" "$OUT/laguna.adt" --mem-size=0x10000000
CMDLINE='debug=0x8 serial=3 cpus=1 io.display.iomfb=1 rd=md0 rootrw=1 ignition_level=1 launchd_unsecure_cache=1 kern.jetsam_critical_threshold=1 interrupt-masked-debug-mode=1 lagunalogdump=1'
python3 "$TL/mkpayload.py" "$R4_MACHO" "$OUT/laguna.adt" "$OUT/payload-usb.bin" \
    --cmdline "$CMDLINE" --ramdisk "$ROOTFS"
bash "$TL/shim/build.sh" "$OUT/payload-usb.bin"
cp "$TL/shim/out/shim-boot-laguna.img" "$OUT/shim-boot-laguna.img"
cp "$OUT/shim-boot-laguna.img" "$OUT/laguna-usb-next-boot-avb.img"
python3 "$AVBTOOL" add_hash_footer --image "$OUT/laguna-usb-next-boot-avb.img" \
    --partition_name boot --partition_size "$PARTITION_SIZE" --salt "$SALT" \
    --algorithm SHA256_RSA4096 --rollback_index "$ROLLBACK" --key "$KEY"
python3 "$AVBTOOL" verify_image --image "$OUT/laguna-usb-next-boot-avb.img"
shasum -a 256 "$OUT/payload-usb.bin" "$OUT/shim-boot-laguna.img" \
    "$OUT/laguna-usb-next-boot-avb.img" | tee "$OUT/SHA256SUMS"
printf 'Built host-only next-boot candidate: %s\n' "$OUT/laguna-usb-next-boot-avb.img"
