#!/usr/bin/env python3
"""Host-only synthetic tests for tools/tensor-args.py (no device/QEMU)."""
import os
import struct
import subprocess
import sys
import tempfile

MAGIC = 0xD00DFEED


class Tree:
    def __init__(self):
        self.strings = bytearray()
        self.offsets = {}

    def string(self, value):
        raw = value.encode()
        if value not in self.offsets:
            self.offsets[value] = len(self.strings)
            self.strings += raw + b"\0"
        return self.offsets[value]

    @staticmethod
    def align(raw):
        return raw + b"\0" * ((-len(raw)) & 3)

    def prop(self, name, value):
        return (struct.pack(">III", 3, len(value), self.string(name)) +
                self.align(value))

    def node(self, name, props=(), children=()):
        raw = struct.pack(">I", 1) + self.align(name.encode() + b"\0")
        raw += b"".join(self.prop(*prop) for prop in props)
        raw += b"".join(children)
        return raw + struct.pack(">I", 2)

    def finish(self, root):
        struct_blob = root + struct.pack(">I", 9)
        off_struct = 40
        off_strings = off_struct + len(struct_blob) + 16
        header = struct.pack(">10I", MAGIC, off_strings + len(self.strings),
            off_struct, off_strings, off_struct + len(struct_blob), 17, 16, 0,
            len(self.strings), len(struct_blob))
        return header + struct_blob + b"\0" * 16 + self.strings


def u32(value):
    return struct.pack(">I", value)


def cells(*values):
    return b"".join(u32(value) for value in values)


def text(value):
    return value.encode() + b"\0"


def fixture(tree, complete=True):
    root_props = (("compatible", text("google,lga-blazer\0google,lga")),
                  ("#address-cells", u32(2)), ("#size-cells", u32(2)))
    ufs_regs = cells(0x8, 0x3c400000, 0, 0x1000,
                     0, 0x3c410000, 0, 0x504,
                     0, 0x3c700000, 0, 0x14000,
                     0, 0x3c00b108, 0, 4,
                     0, 0x3c00d210, 0, 4,
                     0, 0x3c00e108, 0, 4)
    ufs_names = text("ufs_hc\0ufs_top\0ufs_phy_sram\0psm_status_hsios\0"
                     "psm_status_ufs_hc\0psm_status_ufs_phy")
    ufs = tree.node("ufs@83c400000", (("compatible", text("google,ufshc")),
                                      ("reg", ufs_regs), ("reg-names", ufs_names)))
    cpm_global = tree.node("cpm_mba_syscon@5250000",
        (("compatible", text("google,cpm-mba-global\0syscon")),
         ("reg", cells(0, 0x5250000, 0, 0x50))))
    req = tree.node("cpm_ap_ns_req_mba_client_0@5260000",
        (("compatible", text("google,mba-ctrl")),
         ("reg", cells(0, 0x5260000, 0, 0x1000)),
         ("queue-mode", b""), ("payload-size", u32(4))))
    resp = tree.node("cpm_ap_ns_resp_mba_client_1@5261000",
        (("compatible", text("google,mba-ctrl")),
         ("reg", cells(0, 0x5261000, 0, 0x1000)),
         ("queue-mode", b""), ("payload-size", u32(4))))
    ramoops = tree.node("ramoops@95200000",
        (("compatible", text("ramoops")),
         ("reg", cells(0, 0x95200000, 0, 0x400000)),
         ("console-size", u32(0x200000)), ("pmsg-size", u32(0x200000))))
    reserved = tree.node("reserved-memory", (), (ramoops,))
    children = [ufs, cpm_global, req, reserved]
    if complete:
        children.insert(3, resp)
    return tree.finish(tree.node("", root_props, children))


def run(tool, path):
    return subprocess.run([sys.executable, tool, path], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: test-tensor-args.py /path/to/tensor-args.py")
    tool = os.path.abspath(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="tensor-args-test-") as work:
        good = os.path.join(work, "good.dtb")
        missing = os.path.join(work, "missing.dtb")
        with open(good, "wb") as out:
            out.write(fixture(Tree()))
        with open(missing, "wb") as out:
            out.write(fixture(Tree(), complete=False))
        result = run(tool, good)
        assert result.returncode == 0, result.stderr
        expected = {
            "blazer_ufs_base": "0x83c400000",  # 64-bit address-cell coverage
            "blazer_ufs_top_base": "0x3c410000",
            "blazer_ufs_psm_hsios": "0x3c00b108",
            "blazer_ufs_psm_hc": "0x3c00d210",
            "blazer_ufs_psm_phy": "0x3c00e108",
            "blazer_cpm_global": "0x5250000",
            "blazer_cpm_req": "0x5260000",
            "blazer_cpm_resp": "0x5261000",
            "blazer_ramoops_base": "0x95200000",
            "blazer_ramoops_size": "0x400000",
            "blazer_console_size": "0x200000",
            "blazer_pmsg_base": "0x95400000",
            "blazer_pmsg_size": "0x200000",
        }
        got = dict(line.split("=", 1) for line in result.stdout.splitlines())
        assert got == expected, (got, expected)
        assert run(tool, missing).returncode != 0
        truncated = os.path.join(work, "truncated.dtb")
        with open(truncated, "wb") as out:
            out.write(open(good, "rb").read()[:47])
        assert run(tool, truncated).returncode != 0
    print("test-tensor-args: PASS (64-bit cells, every handoff value, missing node and truncation fail closed)")


if __name__ == "__main__":
    main()
