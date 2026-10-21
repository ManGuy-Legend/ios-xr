#!/usr/bin/env python3
"""
boot.py -- one-command boot of iOS 26 on iPhone XR.

Handles everything: fetches + patches iBSS/iBEC from Apple CDN if not
already cached, waits for pwnDFU device, uploads the full boot chain.

Usage:
    python3 boot.py                          # uses defaults, fetches from CDN
    python3 boot.py --ipsw-url <url>         # specify IPSW URL
    python3 boot.py --work-dir ./boot_files  # cache dir for patched files

Prerequisites:
    pip install pyusb pyimg4
    irecovery on PATH (libimobiledevice)
    Device in pwnDFU (run exploit first)
"""

import argparse
import io
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("[!] pyusb not installed: pip install pyusb")

try:
    import pyimg4
except ImportError:
    sys.exit("[!] pyimg4 not installed: pip install pyimg4")


# ── Constants ────────────────────────────────────────────────────────────

APPLE_VID  = 0x05AC
DFU_PID    = 0x1227
DFU_CHUNK  = 0x800
DFU_DNLOAD = 1
DFU_GETSTATUS = 3

BOARD_ID   = "0x0C"   # n841ap (iPhone XR)

DEFAULT_IPSW_API = "https://api.ipsw.me/v4/device/iPhone11,8?type=ipsw"


# ── HTTP Range reader ────────────────────────────────────────────────────

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


# ── IPSW helpers ─────────────────────────────────────────────────────────

def get_latest_ipsw_url():
    """Get latest signed IPSW URL for iPhone XR from ipsw.me API."""
    print("[*] querying ipsw.me for latest signed XR firmware...")
    req = urllib.request.Request(DEFAULT_IPSW_API)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as r:
        import json
        data = json.load(r)
    for fw in data.get("firmwares", []):
        if fw.get("signed", False):
            print(f"[+] found: iOS {fw['version']} ({fw['buildid']})")
            return fw["url"]
    raise RuntimeError("no signed IPSW found for iPhone XR")


def fetch_component(ipsw_url, component_name):
    """Fetch iBSS or iBEC IM4P from remote IPSW via range reads."""
    print(f"[*] fetching {component_name} from IPSW...")
    rf = HTTPRangeFile(ipsw_url)
    zf = zipfile.ZipFile(io.BufferedReader(rf, 1 << 20))

    with zf.open("BuildManifest.plist") as fp:
        manifest = plistlib.load(fp)

    path = None
    for ident in manifest.get("BuildIdentities", []):
        if ident.get("ApBoardID") != BOARD_ID:
            continue
        entry = ident.get("Manifest", {}).get(component_name)
        if entry:
            path = entry["Info"]["Path"]
            break

    if path is None:
        for ident in manifest.get("BuildIdentities", []):
            entry = ident.get("Manifest", {}).get(component_name)
            if entry:
                path = entry["Info"]["Path"]
                break

    if path is None:
        raise RuntimeError(f"{component_name} not found in BuildManifest")

    print(f"[*] {component_name}: {path}")
    with zf.open(path) as fp:
        data = fp.read()
    print(f"[*] {component_name} IM4P: {len(data):#x} bytes")
    return data


def unpack_im4p(im4p_bytes):
    im4p = pyimg4.IM4P(im4p_bytes)
    payload = im4p.payload
    return payload.decompress() if payload.compression else payload.data


# ── Patching ─────────────────────────────────────────────────────────────

def _find_all(haystack, needle):
    start = 0
    while True:
        off = haystack.find(needle, start)
        if off == -1:
            return
        yield off
        start = off + 1

MOVZ_W0_0 = struct.pack("<I", 0x52800000)
RET       = struct.pack("<I", 0xD65F03C0)
NOP       = struct.pack("<I", 0xD503201F)


def patch_iboot(raw, name="iBSS"):
    """Apply sig-check patches to iBSS or iBEC."""
    buf = bytearray(raw)
    applied = 0

    # P1: img4 verify prologue → return 0
    pat = bytes([0xfd, 0x7b, 0xbe, 0xa9, 0xf3, 0x53, 0x01, 0xa9])
    for off in _find_all(buf, pat):
        buf[off:off+8] = (MOVZ_W0_0 + RET)
        print(f"[+] {name} P1 (img4_verify) patched at {off:#x}")
        applied += 1
        break

    # P2: production-mode TBNZ → NOP
    count = 0
    for off in _find_all(buf, b'\x00\x00\x37'):
        if off < 3:
            continue
        ioff = off - 1
        prec = buf[ioff - 4 : ioff]
        if len(prec) == 4 and (prec[3] & 0xFC) == 0xB8:
            buf[ioff:ioff+4] = NOP
            print(f"[+] {name} P2 (prod_mode) patched at {ioff:#x}")
            applied += 1
            count += 1
            if count >= 2:
                break

    # P3: secondary RSA verify → return 0
    pat = bytes([0xfd, 0x7b, 0xbc, 0xa9, 0xf7, 0x63, 0x03, 0xa9])
    for off in _find_all(buf, pat):
        buf[off:off+8] = (MOVZ_W0_0 + RET)
        print(f"[+] {name} P3 (rsa_verify) patched at {off:#x}")
        applied += 1
        break

    if applied == 0:
        print(f"[!] WARNING: no {name} patches applied")
    else:
        print(f"[*] {name}: {applied} patch(es) applied")
    return bytes(buf)


# ── DFU upload ───────────────────────────────────────────────────────────

def wait_for_pwndfu(timeout=60):
    print("[*] waiting for pwnDFU device...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        devs = usb.core.find(idVendor=APPLE_VID, idProduct=DFU_PID,
                             find_all=True)
        for dev in (devs or []):
            try:
                serial = usb.util.get_string(dev, dev.iSerialNumber)
            except Exception:
                continue
            if "PWND:" in serial:
                print(f"\n[+] pwnDFU: {serial}")
                return dev
        time.sleep(0.5)
        print(".", end="", flush=True)
    print("\n[!] timeout waiting for pwnDFU device")
    return None


def dfu_send(dev, data):
    total, offset, block = len(data), 0, 0
    dev.set_configuration()
    while offset < total:
        chunk = data[offset:offset + DFU_CHUNK]
        dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, chunk, timeout=5000)
        st = dev.ctrl_transfer(0xA1, DFU_GETSTATUS, 0, 0, 6, timeout=2000)
        if st[0] != 0:
            raise RuntimeError(f"DFU error block {block}: {st[0]:#x}")
        offset += len(chunk)
        block += 1
        print(f"\r[*] uploading: {offset*100//total:3d}%", end="", flush=True)
    print()
    dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, None, timeout=5000)
    try:
        dev.ctrl_transfer(0xA1, DFU_GETSTATUS, 0, 0, 6, timeout=2000)
    except Exception:
        pass


# ── irecovery helpers ────────────────────────────────────────────────────

def check_irecovery():
    if shutil.which("irecovery") is None:
        sys.exit("[!] irecovery not found on PATH. Install libimobiledevice.")


def irecv_send(filepath):
    print(f"[*] irecovery -f {os.path.basename(filepath)}")
    r = subprocess.run(["irecovery", "-f", filepath],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"[!] failed: {r.stderr.strip()}")
        return False
    return True


def irecv_cmd(cmd):
    print(f"[*] irecovery -c {cmd}")
    subprocess.run(["irecovery", "-c", cmd],
                   capture_output=True, text=True, timeout=10)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="One-command tethered boot of iOS 26 on iPhone XR"
    )
    ap.add_argument("--ipsw-url",
                    help="IPSW URL (auto-detected from ipsw.me if omitted)")
    ap.add_argument("--work-dir", default="boot_files",
                    help="Directory to cache patched files (default: boot_files)")
    ap.add_argument("--devicetree",
                    help="DeviceTree file (fetched from IPSW if omitted)")
    ap.add_argument("--kernelcache",
                    help="Custom kernelcache to boot")
    ap.add_argument("--trustcache",
                    help="Trustcache file (optional)")
    ap.add_argument("--boot-args", default="-v debug=0x14e serial=3",
                    help="Kernel boot-args (default: verbose + serial)")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="Skip fetch/patch, use cached files in work-dir")
    args = ap.parse_args()

    check_irecovery()
    os.makedirs(args.work_dir, exist_ok=True)

    ibss_path = os.path.join(args.work_dir, "patched_iBSS.bin")
    ibec_path = os.path.join(args.work_dir, "patched_iBEC.bin")
    dt_path   = args.devicetree or os.path.join(args.work_dir, "devicetree.im4p")
    kc_path   = args.kernelcache

    # ── Fetch & patch ────────────────────────────────────────────────
    if not args.skip_fetch:
        ipsw_url = args.ipsw_url
        if not ipsw_url:
            ipsw_url = get_latest_ipsw_url()

        if not os.path.exists(ibss_path):
            raw = unpack_im4p(fetch_component(ipsw_url, "iBSS"))
            patched = patch_iboot(raw, "iBSS")
            with open(ibss_path, "wb") as f:
                f.write(patched)
            print(f"[+] saved {ibss_path}")
        else:
            print(f"[*] using cached {ibss_path}")

        if not os.path.exists(ibec_path):
            raw = unpack_im4p(fetch_component(ipsw_url, "iBEC"))
            patched = patch_iboot(raw, "iBEC")
            with open(ibec_path, "wb") as f:
                f.write(patched)
            print(f"[+] saved {ibec_path}")
        else:
            print(f"[*] using cached {ibec_path}")

        if not args.devicetree and not os.path.exists(dt_path):
            dt_data = fetch_component(ipsw_url, "DeviceTree")
            with open(dt_path, "wb") as f:
                f.write(dt_data)
            print(f"[+] saved {dt_path}")

    # ── Validate files exist ─────────────────────────────────────────
    for label, path in [("iBSS", ibss_path), ("iBEC", ibec_path),
                        ("DeviceTree", dt_path)]:
        if not os.path.exists(path):
            sys.exit(f"[!] {label} not found: {path}")

    if kc_path and not os.path.exists(kc_path):
        sys.exit(f"[!] kernelcache not found: {kc_path}")

    with open(ibss_path, "rb") as f:
        ibss_data = f.read()

    # ── Boot chain ───────────────────────────────────────────────────
    print()
    print("=" * 50)
    print("  ios-xr tethered boot")
    print("=" * 50)
    print()

    # Step 1: iBSS via DFU
    dev = wait_for_pwndfu()
    if dev is None:
        sys.exit(1)

    print("[*] step 1/5: uploading iBSS via DFU...")
    try:
        dfu_send(dev, ibss_data)
    except usb.core.USBError as e:
        if "No such device" not in str(e) and "pipe" not in str(e).lower():
            raise
    print("[+] iBSS sent, waiting for recovery mode...")
    time.sleep(4)

    # Step 2: iBEC
    print("[*] step 2/5: sending iBEC...")
    if not irecv_send(ibec_path):
        sys.exit("[!] iBEC send failed")
    time.sleep(3)

    # Step 3: boot-args + devicetree
    print("[*] step 3/5: setting boot-args + devicetree...")
    if args.boot_args:
        irecv_cmd(f"setenv boot-args {args.boot_args}")
    irecv_cmd("setenv auto-boot false")

    if not irecv_send(dt_path):
        sys.exit("[!] devicetree send failed")
    irecv_cmd("devicetree")
    time.sleep(1)

    # Step 4: trustcache (optional)
    if args.trustcache:
        print("[*] step 4/5: sending trustcache...")
        if not irecv_send(args.trustcache):
            sys.exit("[!] trustcache send failed")
        irecv_cmd("firmware")
        time.sleep(1)
    else:
        print("[*] step 4/5: no trustcache (skipped)")

    # Step 5: kernelcache + boot
    if kc_path:
        print("[*] step 5/5: sending kernelcache + booting...")
        if not irecv_send(kc_path):
            sys.exit("[!] kernelcache send failed")
        irecv_cmd("bootx")
        print()
        print("[+] boot command sent. Watch serial console for output.")
    else:
        print("[*] step 5/5: no kernelcache specified -- device in recovery")
        print("[*] send kernelcache manually: irecovery -f <kc> && irecovery -c bootx")

    print()
    print("[+] done")


if __name__ == "__main__":
    main()
