#!/bin/sh
# Validate Tensor DT generation and the checked-in blazer handoff arguments.
# Set BLAZER_DTB to a saved live FDT. Synthetic tests always run, including
# 64-bit addresses and fail-closed malformed/missing-node cases.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$root/devices/blazer/tools/test-tensor-args.py" "$root/tools/tensor-args.py"

dtb=${BLAZER_DTB:-$root/devices/blazer/prebuilt/device-tree.dtb}
if [ ! -f "$dtb" ]; then
	echo "tensor-args-test: SKIP live-DTB comparison (set BLAZER_DTB)"
	exit 0
fi
got="$(python3 "$root/tools/tensor-args.py" "$dtb"; echo fb_compatible=google,lga-framebuffer)"
expected='blazer_ufs_base=0x3c400000
blazer_ufs_top_base=0x3c410000
blazer_ufs_psm_hsios=0x3c00b108
blazer_ufs_psm_hc=0x3c00d210
blazer_ufs_psm_phy=0x3c00e108
blazer_cpm_global=0x5250000
blazer_cpm_req=0x5260000
blazer_cpm_resp=0x5261000
blazer_ramoops_base=0x95200000
blazer_ramoops_size=0x400000
blazer_console_size=0x200000
blazer_pmsg_base=0x95400000
blazer_pmsg_size=0x200000
fb_compatible=google,lga-framebuffer'
if [ "$got" != "$expected" ]; then
	echo "tensor-args-test: FAIL -- generator no longer reproduces blazer's values" >&2
	printf '%s\n' "$expected" | diff -u - "$root/devices/blazer/soc.args" || true
	exit 1
fi
if [ "$got" != "$(cat "$root/devices/blazer/soc.args")" ]; then
	echo "tensor-args-test: FAIL -- devices/blazer/soc.args is out of step" >&2
	exit 1
fi
echo "tensor-args-test: synthetic checks and all 14 live-DTB handoff values PASS"
