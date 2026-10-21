#!/usr/bin/env python3
"""
patch_ibec.py -- fetch, unpack, and patch iPhone XR iBEC for pwnDFU boot chain.

iBEC is the second-stage bootloader (loaded by iBSS). It loads and boots
the devicetree, kernelcache, and optional ramdisk. Same sig-check
patching as iBSS since both are iBoot variants sharing the same codebase.

Usage:
    python3 patch_ibec.py --ipsw-url <url> --out patched_iBEC.bin
    python3 patch_ibec.py --im4p iBEC.n841ap.RELEASE.im4p --out patched_iBEC.bin
"""

import argparse
import io
import os
import plistlib
import struct
import sys
import urllib.request
import zipfile

import pyimg4


# ---------------------------------------------------------------------------
# HTTP Range reader (shared with patch_ibss.py)
# ---------------------------------------------------------------------------

class HTTPRangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        with urllib.request.urlopen(
            urllib.request.Request(url, method="HEAD")
        ) as r:
            self.length = int(r.headers["Content-Length"])
        self.pos = 0

    def readable(self):  return True
    def seekable(self):  return True

    def seek(self, offset, whence=io.SEEK_SET):
        if   whence == io.SEEK_SET: self.pos  = offset
        elif whence == io.SEEK_CUR: self.pos += offset
        elif whence == io.SEEK_END: self.pos  = self.length + offset
        return self.pos

    def tell(self): return self.pos

    def readinto(self, b):
        n = len(b)
        if n == 0 or self.pos >= self.length:
            return 0
        end = min(self.pos + n, self.length) - 1
        req = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.pos}-{end}"}
        )
        with urllib.request.urlopen(req) as r:
            data = r.read()
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


def fetch_ibec_im4p(ipsw_url: str) -> bytes:
    print(f"[*] opening remote IPSW (range reads): {ipsw_url}")
    rf = HTTPRangeFile(ipsw_url)
    zf = zipfile.ZipFile(io.BufferedReader(rf, 1 << 20))

    with zf.open("BuildManifest.plist") as fp:
        manifest = plistlib.load(fp)

    ibec_path = None
    for ident in manifest.get("BuildIdentities", []):
        if ident.get("ApBoardID") != "0x0C":
            continue
        ibec = ident.get("Manifest", {}).get("iBEC")
        if ibec:
            ibec_path = ibec["Info"]["Path"]
            break

    if ibec_path is None:
        for ident in manifest.get("BuildIdentities", []):
            ibec = ident.get("Manifest", {}).get("iBEC")
            if ibec:
                ibec_path = ibec["Info"]["Path"]
                break

    if ibec_path is None:
        raise RuntimeError("iBEC not found in BuildManifest.plist")

    print(f"[*] iBEC path in IPSW: {ibec_path}")
    with zf.open(ibec_path) as fp:
        data = fp.read()
    print(f"[*] iBEC IM4P: {len(data):#x} bytes")
    return data


def unpack_im4p(im4p_bytes: bytes) -> bytes:
    im4p = pyimg4.IM4P(im4p_bytes)
    payload = im4p.payload
    data = payload.decompress() if payload.compression else payload.data
    print(f"[*] unpacked payload: {len(data):#x} bytes")
    return data


# ---------------------------------------------------------------------------
# Binary patching (same patterns as iBSS -- shared iBoot codebase)
# ---------------------------------------------------------------------------

def find_all(haystack: bytes, needle: bytes):
    start = 0
    while True:
        off = haystack.find(needle, start)
        if off == -1:
            return
        yield off
        start = off + 1


def patch_at(buf: bytearray, offset: int, old: bytes, new: bytes,
             name: str) -> bool:
    if buf[offset:offset + len(old)] != old:
        print(f"[!] {name}: bytes at {offset:#x} don't match, skipping")
        return False
    buf[offset:offset + len(new)] = new
    print(f"[+] {name}: patched at {offset:#x}")
    return True


def _movz_w0_0():
    return struct.pack("<I", 0x52800000)

def _ret():
    return struct.pack("<I", 0xD65F03C0)

def _nop():
    return struct.pack("<I", 0xD503201F)


def apply_patches(raw: bytes) -> bytes:
    buf = bytearray(raw)
    applied = 0

    # P1: img4/RSA verify function → MOV W0,#0 + RET
    VERIFY_PROLOGUE = bytes([0xfd, 0x7b, 0xbe, 0xa9,
                              0xf3, 0x53, 0x01, 0xa9])
    for off in find_all(buf, VERIFY_PROLOGUE):
        new = _movz_w0_0() + _ret() + bytes(len(VERIFY_PROLOGUE) - 8)
        if patch_at(buf, off, VERIFY_PROLOGUE, new[:len(VERIFY_PROLOGUE)],
                    f"P1(img4_verify@{off:#x})"):
            applied += 1
            break

    # P2: production-mode TBNZ → NOP
    TBNZ_BIT0_SKIP8 = b'\x00\x00\x37'
    count = 0
    for off in find_all(buf, TBNZ_BIT0_SKIP8):
        if off < 3:
            continue
        instr_off = off - 1
        preceding = buf[instr_off - 4 : instr_off]
        if len(preceding) == 4 and (preceding[3] & 0xFC) == 0xB8:
            new_instr = _nop()
            old_instr = bytes(buf[instr_off:instr_off + 4])
            if patch_at(buf, instr_off, old_instr, new_instr,
                        f"P2(prod_mode_tbnz@{instr_off:#x})"):
                applied += 1
                count += 1
                if count >= 2:
                    break

    # P3: secondary RSA verify → MOV W0,#0 + RET
    RSA_PROLOGUE = bytes([0xfd, 0x7b, 0xbc, 0xa9,
                          0xf7, 0x63, 0x03, 0xa9])
    for off in find_all(buf, RSA_PROLOGUE):
        new = _movz_w0_0() + _ret() + bytes(len(RSA_PROLOGUE) - 8)
        if patch_at(buf, off, RSA_PROLOGUE, new[:len(RSA_PROLOGUE)],
                    f"P3(rsa_verify@{off:#x})"):
            applied += 1
            break

    # P4: iBEC-specific -- ticket (SHSH) validation
    # iBEC validates the AP ticket before booting. Pattern: the function
    # that checks ticket presence/validity. Same approach: find its
    # prologue and short-circuit to return 0.
    # Common iBEC ticket-check prologue (distinct frame size from P1/P3):
    #   STP x29,x30,[sp,#-0x40]!  (fd 7b bd a9)
    #   STP x21,x22,[sp,#0x20]    (f5 5b 02 a9)
    TICKET_PROLOGUE = bytes([0xfd, 0x7b, 0xbd, 0xa9,
                              0xf5, 0x5b, 0x02, 0xa9])
    for off in find_all(buf, TICKET_PROLOGUE):
        new = _movz_w0_0() + _ret() + bytes(len(TICKET_PROLOGUE) - 8)
        if patch_at(buf, off, TICKET_PROLOGUE, new[:len(TICKET_PROLOGUE)],
                    f"P4(ticket_check@{off:#x})"):
            applied += 1
            break

    if applied == 0:
        print("[!] WARNING: no patches applied -- check iBEC binary version")
    else:
        print(f"[*] {applied} patch(es) applied")

    return bytes(buf)


def main():
    ap = argparse.ArgumentParser(
        description="Fetch + unpack + patch XR iBEC for pwnDFU boot chain"
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ipsw-url", metavar="URL",
                     help="Remote IPSW URL (range read, no full download)")
    src.add_argument("--im4p",   metavar="FILE",
                     help="Already-downloaded iBEC IM4P file")
    ap.add_argument("--out", default="patched_iBEC.bin",
                    help="Output path (default: patched_iBEC.bin)")
    args = ap.parse_args()

    if args.ipsw_url:
        im4p_bytes = fetch_ibec_im4p(args.ipsw_url)
    else:
        with open(args.im4p, "rb") as f:
            im4p_bytes = f.read()
        print(f"[*] loaded IM4P: {len(im4p_bytes):#x} bytes")

    raw     = unpack_im4p(im4p_bytes)
    patched = apply_patches(raw)

    with open(args.out, "wb") as f:
        f.write(patched)
    print(f"[+] wrote {args.out} ({len(patched):#x} bytes)")


if __name__ == "__main__":
    main()
