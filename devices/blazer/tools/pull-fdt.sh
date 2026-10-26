#!/usr/bin/env bash
# Pull the live device tree from a Pixel 10 Pro (blazer) into fdt/laguna.dtb.
# Read-only on the phone. Requires adb + root (Magisk su) on stock Android.
set -euo pipefail
cd "$(dirname "$0")/.."
adb wait-for-device
adb shell 'su -c "cat /sys/firmware/fdt > /data/local/tmp/laguna-fdt.dtb"'
adb pull /data/local/tmp/laguna-fdt.dtb fdt/laguna.dtb
adb shell 'su -c "rm -f /data/local/tmp/laguna-fdt.dtb"'
echo "fdt/laguna.dtb: $(stat -f%z fdt/laguna.dtb) bytes"
# Optional sanity: the expected laguna compatibles should be present.
if command -v dtc >/dev/null 2>&1; then
  dtc -I dtb -O dts fdt/laguna.dtb 2>/dev/null | grep -m3 "google,lga-blazer\|watchdog_cl\|sswrp_dpu" || true
fi
