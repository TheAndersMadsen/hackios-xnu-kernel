#!/usr/bin/env python3
"""Print Tensor SoC handoff addresses as HakiOS boot-args from a flattened DT.

usage: tools/tensor-args.py DEVICE-TREE.dtb

This is the Google/Tensor counterpart of tools/soc-args.py, not a generalization
of that Qualcomm modem/IPA generator. Laguna has no qcom MPSS/IPA/RPMh stack.
Only numeric handoff facts are emitted. An address is not permission to touch a
block: the first stage must prove its power/clock state and leave it live.

The supported nodes are blazer's google,ufshc register tuples, the CPM goog-mba
v2 request/response clients and the reserved ramoops zone. 32-bit and 64-bit
address/size cells are handled; no node contents other than numbers are copied.
"""
import struct
import sys


def cells(blob, count):
    if count not in (1, 2):
        raise ValueError(f"unsupported cell count {count}")
    if len(blob) != count * 4:
        raise ValueError("short cell value")
    words = struct.unpack(f">{count}I", blob)
    value = 0
    for word in words:
        value = (value << 32) | word
    return value


def nodes_of(blob):
    if len(blob) < 40 or blob[:4] != b"\xd0\x0d\xfe\xed":
        raise ValueError("not a flattened device tree")
    total, off_struct, off_strings = struct.unpack(">III", blob[4:16])
    size_strings, = struct.unpack(">I", blob[32:36])
    if total > len(blob) or off_struct >= total or off_strings + size_strings > total:
        raise ValueError("truncated device tree")
    strings = blob[off_strings:off_strings + size_strings]
    out, path, at = {}, [], off_struct
    while True:
        if at + 4 > total:
            raise ValueError("unterminated device-tree structure")
        token, = struct.unpack(">I", blob[at:at + 4])
        at += 4
        if token == 1:  # FDT_BEGIN_NODE
            end = blob.find(b"\0", at, total)
            if end < 0:
                raise ValueError("unterminated node name")
            path.append(blob[at:end].decode("utf-8"))
            at = (end + 4) & ~3
            out["/".join(path)] = {}
        elif token == 2:  # FDT_END_NODE
            if not path:
                raise ValueError("unbalanced device-tree nodes")
            path.pop()
        elif token == 3:  # FDT_PROP
            if not path or at + 8 > total:
                raise ValueError("property outside a node")
            length, name_off = struct.unpack(">II", blob[at:at + 8])
            at += 8
            name_end = strings.find(b"\0", name_off)
            if name_end < 0 or at + length > total:
                raise ValueError("truncated property")
            name = strings[name_off:name_end].decode("utf-8")
            out["/".join(path)][name] = blob[at:at + length]
            at = (at + length + 3) & ~3
        elif token == 9:  # FDT_END
            if path:
                raise ValueError("device tree ended inside a node")
            return out
        elif token != 4:  # FDT_NOP
            raise ValueError(f"unexpected device-tree token {token}")


def find(nodes, compatible):
    wanted = compatible.encode()
    for path, props in nodes.items():
        if wanted in props.get("compatible", b"").split(b"\0"):
            return path, props
    raise ValueError(f"no node compatible with {compatible}")


def find_named(nodes, leaf):
    for path, props in nodes.items():
        if path.rsplit("/", 1)[-1].split("@", 1)[0] == leaf:
            return path, props
    raise ValueError(f"no device-tree node named {leaf}")


def strings(value):
    return [part.decode("utf-8") for part in value.split(b"\0") if part]


def reg_tuples(props, root):
    address_cells = cells(root.get("#address-cells", b"\0\0\0\2"), 1)
    size_cells = cells(root.get("#size-cells", b"\0\0\0\2"), 1)
    width = (address_cells + size_cells) * 4
    raw = props.get("reg", b"")
    if not raw or len(raw) % width:
        raise ValueError("reg is empty or not whole address/size tuples")
    result = []
    for off in range(0, len(raw), width):
        at = off
        address = cells(raw[at:at + address_cells * 4], address_cells)
        at += address_cells * 4
        size = cells(raw[at:at + size_cells * 4], size_cells)
        result.append((address, size))
    return result


def named_regions(nodes, path, props):
    root = nodes.get("")
    if root is None:
        raise ValueError("device tree has no root node")
    regions = reg_tuples(props, root)
    names = strings(props.get("reg-names", b""))
    if names and len(names) != len(regions):
        raise ValueError(f"{path}: reg-names and reg have different lengths")
    if names:
        return dict(zip(names, regions))
    if len(regions) == 1:
        return {"reg": regions[0]}
    return {str(i): region for i, region in enumerate(regions)}


def one_u32(props, name, default=0):
    if name not in props:
        return default
    return cells(props[name], 1)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        nodes = nodes_of(open(sys.argv[1], "rb").read())
        args = []

        path, ufs = find(nodes, "google,ufshc")
        regions = named_regions(nodes, path, ufs)
        for name, arg in (
            ("ufs_hc", "blazer_ufs_base"),
            ("ufs_top", "blazer_ufs_top_base"),
            ("psm_status_hsios", "blazer_ufs_psm_hsios"),
            ("psm_status_ufs_hc", "blazer_ufs_psm_hc"),
            ("psm_status_ufs_phy", "blazer_ufs_psm_phy"),
        ):
            if name not in regions:
                raise ValueError(f"{path}: missing register region {name}")
            args.append((arg, regions[name][0]))

        path, global_node = find(nodes, "google,cpm-mba-global")
        args.append(("blazer_cpm_global", named_regions(nodes, path, global_node)["reg"][0]))
        for leaf, arg in (
            ("cpm_ap_ns_req_mba_client_0", "blazer_cpm_req"),
            ("cpm_ap_ns_resp_mba_client_1", "blazer_cpm_resp"),
        ):
            path, client = find_named(nodes, leaf)
            if "google,mba-ctrl" not in strings(client.get("compatible", b"")):
                raise ValueError(f"{path}: not a google,mba-ctrl client")
            if "queue-mode" not in client:
                raise ValueError(f"{path}: CPM client is not queue mode")
            if one_u32(client, "payload-size") != 4:
                raise ValueError(f"{path}: expected a four-word CPM payload")
            args.append((arg, named_regions(nodes, path, client)["reg"][0]))

        path, ramoops = find(nodes, "ramoops")
        base, total = named_regions(nodes, path, ramoops)["reg"]
        console = one_u32(ramoops, "console-size")
        pmsg = one_u32(ramoops, "pmsg-size")
        if console == 0 or pmsg == 0 or console + pmsg > total:
            raise ValueError(f"{path}: invalid console/pmsg sizes")
        args.extend((("blazer_ramoops_base", base), ("blazer_ramoops_size", total),
                     ("blazer_console_size", console), ("blazer_pmsg_base", base + console),
                     ("blazer_pmsg_size", pmsg)))
    except (OSError, ValueError, struct.error) as error:
        sys.exit(f"tensor-args.py: {error}")

    for name, value in args:
        print(f"{name}={value:#x}")


if __name__ == "__main__":
    main()
