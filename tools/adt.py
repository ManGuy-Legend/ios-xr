#!/usr/bin/env python3
"""Apple Device Tree (ADT) dump/validate tool.

Written fresh for this project (no code shared with reference/ios-sweet's
tools/adt.py -- see README.md's reference/ note). Unlike ios-sweet, which
has to synthesize a minimal ADT for foreign Qualcomm silicon (no real Apple
tree exists for that board), this project's ADT is XR's own real,
signed-device tree pulled from an Apple IPSW. This tool's job is to prove
that blob round-trips cleanly -- i.e. it's already boot-usable as-is, with
no fabrication and no patch -- not to build one from scratch.

Usage:
  adt.py dump FILE [--hex]     FILE = raw ADT .bin or an IM4P-wrapped DeviceTree
  adt.py validate FILE         parse + re-serialize, assert byte-exact round trip
  adt.py unwrap IM4P OUT       decompress an IM4P-wrapped DeviceTree to a raw .bin

ADT layout: node = {u32 nprops, u32 nchildren, prop..., child...};
prop = {char name[32], u32 size (bit31 = placeholder flag), value padded to 4}.
"""
import struct
import sys


def load(path):
    buf = open(path, "rb").read()
    if buf[:1] == b"\x30":
        import pyimg4
        payload = pyimg4.IM4P(buf).payload
        payload.decompress()
        buf = bytes(payload.data)
    return buf


class Node:
    __slots__ = ("props", "children")

    def __init__(self):
        self.props = []  # list of (name:str, flags:int, value:bytes)
        self.children = []

    @property
    def name(self):
        for n, _, v in self.props:
            if n == "name":
                return v.split(b"\0")[0].decode()
        return "?"


def parse(buf, off=0):
    node = Node()
    nprops, nchildren = struct.unpack_from("<II", buf, off)
    off += 8
    for _ in range(nprops):
        name = buf[off:off + 32].split(b"\0")[0].decode()
        size, = struct.unpack_from("<I", buf, off + 32)
        flags, ln = size >> 31, size & 0x7FFFFFFF
        node.props.append((name, flags, buf[off + 36:off + 36 + ln]))
        off += 36 + ((ln + 3) & ~3)
    for _ in range(nchildren):
        child, off = parse(buf, off)
        node.children.append(child)
    return node, off


def serialize(node):
    out = bytearray(struct.pack("<II", len(node.props), len(node.children)))
    for name, flags, val in node.props:
        out += name.encode().ljust(32, b"\0")[:32]
        out += struct.pack("<I", len(val) | (flags << 31))
        out += val + b"\0" * (-len(val) & 3)
    for c in node.children:
        out += serialize(c)
    return bytes(out)


def fmt(v):
    if v and v[-1:] == b"\0" and all(32 <= c < 127 for c in v[:-1].replace(b"\0", b" ")) and len(v) > 1:
        return repr(v.rstrip(b"\0").replace(b"\0", b"|").decode())
    if len(v) in (4, 8):
        return hex(int.from_bytes(v, "little"))
    return v[:48].hex() + ("..." if len(v) > 48 else "") + f" ({len(v)} bytes)"


def dump(node, depth=0):
    ind = "  " * depth
    print(f"{ind}{node.name} {{")
    for n, flags, v in node.props:
        if n == "name":
            continue
        print(f"{ind}  {n}{' [placeholder]' if flags else ''} = {fmt(v)}")
    for c in node.children:
        dump(c, depth + 1)
    print(f"{ind}}}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "dump":
        buf = load(args[0])
        root, end = parse(buf)
        assert end == len(buf), f"trailing {len(buf) - end} bytes unparsed"
        dump(root)
    elif cmd == "validate":
        buf = load(args[0])
        root, end = parse(buf)
        assert end == len(buf), f"trailing {len(buf) - end} bytes unparsed"
        rt = serialize(root)
        assert rt == buf[:end], "round-trip mismatch -- parser/serializer disagree"
        print(f"OK: {args[0]} -- {end} bytes, parsed and re-serialized byte-exact")
    elif cmd == "unwrap":
        open(args[1], "wb").write(load(args[0]))
    else:
        sys.exit(__doc__)
