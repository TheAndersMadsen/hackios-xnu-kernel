#!/bin/bash
# Read blazer's live FDT over adb and regenerate its Tensor handoff arguments.
# No modem/IPA/Qualcomm soc_* arguments exist on this platform. The display
# compatible is HakiOS policy, not an MMIO enable; all hardware drivers remain
# default-off until their explicit live-handoff gates are supplied.
set -e
cd "$(dirname "$0")/../.."
PATH=$PWD/tools/macos-bin/platform-tools:$PATH
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
adb pull /sys/firmware/fdt "$tmp/fdt" >/dev/null || {
	echo "pull-soc.sh: cannot read /sys/firmware/fdt over adb" >&2
	exit 1
}
{
	tools/tensor-args.py "$tmp/fdt"
	echo "fb_compatible=google,lga-framebuffer"
} | tee "$(dirname "$0")/soc.args"
echo "pull-soc.sh: wrote $(dirname "$0")/soc.args"
