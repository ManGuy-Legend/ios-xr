#!/usr/bin/env python3
"""
patch_ibss.py -- fetch, unpack, and patch iPhone XR iBSS for pwnDFU boot.

What this does:
  1. Fetches the iBSS.n841ap.RELEASE.im4p from a remote XR IPSW using
     HTTP Range requests (no full IPSW download needed).
  2. Unpacks the IM4P payload with pyimg4 (LZSS/LZFSE decompression).
  3. Applies binary patches so the booted iBSS accepts unsigned next-stage
     images (custom XNU build) without signature verification.
  4. Writes the patched raw binary, ready for send_ibss.py.

Usage:
    python3 patch_ibss.py --ipsw-url <url> --out patched_iBSS.bin
    python3 patch_ibss.py --ipsw-url <url>          # writes patched_iBSS.bin

    # If you already have the .im4p:
    python3 patch_ibss.py --im4p iBSS.n841ap.RELEASE.im4p --out patched_iBSS.bin

Fetch the iOS 18 XR IPSW URL:
    curl -s 'https://api.ipsw.me/v4/device/iPhone11,8' | python3 -c \
        "import json,sys; [print(f['url']) for f in json.load(sys.stdin)['firmwares'] if f['version'].startswith('18.')]"
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
# HTTP Range reader (same technique as remote_kernelcache.py)
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


def fetch_ibss_im4p(ipsw_url: str) -> bytes:
    print(f"[*] opening remote IPSW (range reads): {ipsw_url}")
    rf = HTTPRangeFile(ipsw_url)
    zf = zipfile.ZipFile(io.BufferedReader(rf, 1 << 20))

    with zf.open("BuildManifest.plist") as fp:
        manifest = plistlib.load(fp)

    # Find the iBSS path for n841ap (iPhone XR board ID)
    ibss_path = None
    for ident in manifest.get("BuildIdentities", []):
        if ident.get("ApBoardID") != "0x0C":   # n841ap board ID
            continue
        ibss = ident.get("Manifest", {}).get("iBSS")
        if ibss:
            ibss_path = ibss["Info"]["Path"]
            break

    if ibss_path is None:
        # Fallback: first iBSS entry regardless of board
        for ident in manifest.get("BuildIdentities", []):
            ibss = ident.get("Manifest", {}).get("iBSS")
            if ibss:
                ibss_path = ibss["Info"]["Path"]
                break

    if ibss_path is None:
        raise RuntimeError("iBSS not found in BuildManifest.plist")

    print(f"[*] iBSS path in IPSW: {ibss_path}")
    with zf.open(ibss_path) as fp:
        data = fp.read()
    print(f"[*] iBSS IM4P: {len(data):#x} bytes")
    return data


# ---------------------------------------------------------------------------
# IM4P unpack
# ---------------------------------------------------------------------------

def unpack_im4p(im4p_bytes: bytes) -> bytes:
    im4p = pyimg4.IM4P(im4p_bytes)
    payload = im4p.payload
    data = payload.decompress() if payload.compression else payload.data
    print(f"[*] unpacked payload: {len(data):#x} bytes")
    return data


# ---------------------------------------------------------------------------
# Binary patching helpers
# ---------------------------------------------------------------------------

def find_all(haystack: bytes, needle: bytes):
    """Yield all offsets of needle in haystack."""
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


# ARM64 instruction helpers
def _movz_w0_0():
    # MOVZ W0, #0  (sf=0 opc=10 hw=00 imm16=0 Rd=0)
    return struct.pack("<I", 0x52800000)

def _ret():
    return struct.pack("<I", 0xD65F03C0)

def _nop():
    return struct.pack("<I", 0xD503201F)

def _mov_w0_wzr():
    # MOV W0, WZR  = ORR W0, WZR, WZR
    return struct.pack("<I", 0x2A1F03E0)


# ---------------------------------------------------------------------------
# Patch suite for XR iOS 18 iBSS (n841ap / T8020)
# ---------------------------------------------------------------------------
#
# Three patches are applied via pattern search; each is broad enough to
# survive minor compiler-layout differences across 18.x point releases.
#
# P1 -- RSA/img4 signature verification stub:
#   The iBSS calls an internal verify helper before booting the next stage.
#   Find the function prologue pattern (STP x29,x30 + ADRP targeting the
#   "No img4" / "verification failed" error string area), then replace the
#   first two instructions with MOV W0,#0 / RET so it always returns success.
#
# P2 -- production-mode check:
#   iBSS reads a flag that controls whether unsigned images are allowed.
#   The check is a TBNZ / CBZ that skips the "unsigned allowed" path.
#   Flip it to an unconditional branch (B) or NOP so we always take the
#   unsigned path.
#
# P3 -- RSA verify low-level stub (belt-and-suspenders):
#   A secondary verify call exists in the DFU receive path.  Same treatment:
#   MOV W0,#0 / RET.
#
# These patterns were derived from public checkm8/palera1n A12 iBSS analysis
# and cross-checked against the XR iOS 18.7.x iBSS binary structure.
# ---------------------------------------------------------------------------

def apply_patches(raw: bytes) -> bytes:
    buf = bytearray(raw)
    applied = 0

    # ------------------------------------------------------------------
    # P1: img4/RSA verify function -- make it always return 0 (success)
    #
    # Pattern: STP x29,x30,[sp,#-N]! followed shortly by ADRP that
    # references a nearby string table (the error-message pool used only
    # by the verify path sits within ±4 MB).
    #
    # We look for the two-instruction pair:
    #   STP x29, x30, [sp, #-0x30]!   (fd 7b be a9)
    #   STP x19, x20, [sp, #0x10]     (f3 53 01 a9)   ← distinct to verify fn
    # ------------------------------------------------------------------
    VERIFY_PROLOGUE = bytes([0xfd, 0x7b, 0xbe, 0xa9,   # stp x29,x30,[sp,#-0x30]!
                              0xf3, 0x53, 0x01, 0xa9])  # stp x19,x20,[sp,#0x10]
    for off in find_all(buf, VERIFY_PROLOGUE):
        new = _movz_w0_0() + _ret() + bytes(len(VERIFY_PROLOGUE) - 8)
        if patch_at(buf, off, VERIFY_PROLOGUE, new[:len(VERIFY_PROLOGUE)],
                    f"P1(img4_verify@{off:#x})"):
            applied += 1
            break   # only first match is the real verify fn

    # ------------------------------------------------------------------
    # P2: production-mode gate -- flip TBNZ W*, #N, +offset to NOP
    #
    # The check typically looks like:
    #   ADRP  x8, <production_flags_page>
    #   LDR   w8, [x8, #off]
    #   TBNZ  w8, #0, <reject_unsigned>   <- patch this to NOP
    #
    # TBNZ with bit=0 encoding: lower byte is always 0x37 for 32-bit Wn.
    # We look for the specific TBNZ-skipping-over-2-instructions pattern
    # (imm14 = 2, which is +8 bytes forward): 0x37 in byte[3], imm14=2
    #
    # Encoding: TBNZ Wn, #0, +8  →  xx 00 00 37  (imm14=2, b40=0, b5=0)
    # The xx varies by Wn.  We NOP any such short skip.
    # ------------------------------------------------------------------
    TBNZ_BIT0_SKIP8 = b'\x00\x00\x37'   # bytes [1:4] of TBNZ Wn,#0,+8
    count = 0
    for off in find_all(buf, TBNZ_BIT0_SKIP8):
        if off < 3:
            continue
        instr_off = off - 1
        # confirm byte[3] == 0x37 (already checked via suffix match above)
        # confirm preceding 4 bytes look like LDR w*, [x*, #N]
        preceding = buf[instr_off - 4 : instr_off]
        if len(preceding) == 4 and (preceding[3] & 0xFC) == 0xB8:
            # B9 prefix = LDR W, unsigned offset -- plausible flag load
            new_instr = _nop()
            old_instr = bytes(buf[instr_off:instr_off + 4])
            if patch_at(buf, instr_off, old_instr, new_instr,
                        f"P2(prod_mode_tbnz@{instr_off:#x})"):
                applied += 1
                count += 1
                if count >= 2:
                    break

    # ------------------------------------------------------------------
    # P3: secondary RSA verify (belt-and-suspenders)
    #
    # Distinct prologue used by the lower-level rsa_verify call:
    #   STP x29,x30,[sp,#-0x50]!  (fd 7b bc a9)
    #   STP x23,x24,[sp,#0x30]    (f7 63 03 a9)
    # ------------------------------------------------------------------
    RSA_PROLOGUE = bytes([0xfd, 0x7b, 0xbc, 0xa9,
                          0xf7, 0x63, 0x03, 0xa9])
    for off in find_all(buf, RSA_PROLOGUE):
        new = _movz_w0_0() + _ret() + bytes(len(RSA_PROLOGUE) - 8)
        if patch_at(buf, off, RSA_PROLOGUE, new[:len(RSA_PROLOGUE)],
                    f"P3(rsa_verify@{off:#x})"):
            applied += 1
            break

    if applied == 0:
        print("[!] WARNING: no patches applied -- check iBSS binary version")
    else:
        print(f"[*] {applied} patch(es) applied")

    return bytes(buf)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Fetch + unpack + patch XR iBSS for pwnDFU boot"
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ipsw-url", metavar="URL",
                     help="Remote IPSW URL (range read, no full download)")
    src.add_argument("--im4p",   metavar="FILE",
                     help="Already-downloaded iBSS IM4P file")
    ap.add_argument("--out", default="patched_iBSS.bin",
                    help="Output path for patched raw binary (default: patched_iBSS.bin)")
    args = ap.parse_args()

    if args.ipsw_url:
        im4p_bytes = fetch_ibss_im4p(args.ipsw_url)
    else:
        with open(args.im4p, "rb") as f:
            im4p_bytes = f.read()
        print(f"[*] loaded IM4P: {len(im4p_bytes):#x} bytes")

    raw    = unpack_im4p(im4p_bytes)
    patched = apply_patches(raw)

    with open(args.out, "wb") as f:
        f.write(patched)
    print(f"[+] wrote {args.out} ({len(patched):#x} bytes)")
    print(f"[*] send with: python3 send_ibss.py {args.out}")


if __name__ == "__main__":
    main()
