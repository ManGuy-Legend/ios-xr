#!/usr/bin/env python3
"""
boot_chain.py -- full tethered boot chain for iPhone XR after pwnDFU.

Sequence:
  1. Upload patched iBSS via DFU_DNLOAD (device in pwnDFU)
  2. Wait for device to enter recovery mode
  3. Send patched iBEC via irecovery
  4. Send devicetree via irecovery
  5. Send kernelcache via irecovery
  6. Boot

Prerequisites:
  - Device already in pwnDFU (run exploit first)
  - irecovery installed (pip install pylibirecovery, or brew install
    libirecovery and use the CLI)
  - Patched iBSS, iBEC, devicetree, kernelcache prepared

Usage:
    python3 boot_chain.py \\
        --ibss  patched_iBSS.bin \\
        --ibec  patched_iBEC.bin \\
        --devicetree DeviceTree.n841ap.im4p \\
        --kernelcache kernelcache.im4p \\
        [--trustcache trustcache.img4]
"""

import argparse
import subprocess
import sys
import time

import usb.core
import usb.util

APPLE_VID = 0x05AC
DFU_PID   = 0x1227
RECOV_PID = 0x1281

DFU_DNLOAD    = 1
DFU_GETSTATUS = 3
DFU_CHUNK     = 0x800


def find_device(pid, check_serial=None, timeout=30):
    print(f"[*] waiting for device (05ac:{pid:04x})...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        devs = usb.core.find(idVendor=APPLE_VID, idProduct=pid, find_all=True)
        for dev in (devs or []):
            if check_serial:
                try:
                    serial = usb.util.get_string(dev, dev.iSerialNumber)
                except Exception:
                    continue
                if check_serial not in serial:
                    continue
            print(f" found")
            return dev
        time.sleep(0.5)
        print(".", end="", flush=True)
    print(" timeout")
    return None


def dfu_upload(dev, data):
    """Upload binary via DFU_DNLOAD protocol (pwnDFU mode)."""
    total  = len(data)
    offset = 0
    block  = 0
    print(f"[*] DFU upload: {total:#x} bytes")

    dev.set_configuration()

    while offset < total:
        chunk = data[offset:offset + DFU_CHUNK]
        dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, chunk, timeout=5000)
        status_data = dev.ctrl_transfer(0xA1, DFU_GETSTATUS, 0, 0, 6, timeout=2000)
        if status_data[0] != 0:
            raise RuntimeError(f"DFU error at block {block}: status={status_data[0]:#x}")
        offset += len(chunk)
        block  += 1
        pct = offset * 100 // total
        print(f"\r[*] DFU upload: {pct:3d}%", end="", flush=True)

    print()
    dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, None, timeout=5000)
    try:
        dev.ctrl_transfer(0xA1, DFU_GETSTATUS, 0, 0, 6, timeout=2000)
    except Exception:
        pass  # device may disconnect as iBSS boots


def irecv(cmd, check=True):
    """Run an irecovery command."""
    print(f"[*] irecovery {cmd}")
    r = subprocess.run(["irecovery"] + cmd.split(), capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"[!] irecovery failed: {r.stderr.strip()}")
        return False
    return True


def irecv_send(filepath):
    """Send a file via irecovery."""
    print(f"[*] sending {filepath}")
    r = subprocess.run(["irecovery", "-f", filepath],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[!] send failed: {r.stderr.strip()}")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Full tethered boot chain for XR")
    ap.add_argument("--ibss", required=True, help="Patched iBSS binary")
    ap.add_argument("--ibec", required=True, help="Patched iBEC binary")
    ap.add_argument("--devicetree", required=True, help="DeviceTree (IM4P or raw)")
    ap.add_argument("--kernelcache", required=True, help="Kernelcache (IM4P or raw)")
    ap.add_argument("--trustcache", help="Trustcache (optional)")
    ap.add_argument("--ramdisk", help="Ramdisk DMG (optional)")
    ap.add_argument("--boot-args", default="", help="Kernel boot arguments")
    args = ap.parse_args()

    # Load all files upfront
    with open(args.ibss, "rb") as f:
        ibss_data = f.read()
    print(f"[+] iBSS: {len(ibss_data):#x} bytes")

    # --- Step 1: send iBSS via DFU ---
    dev = find_device(DFU_PID, check_serial="PWND:")
    if dev is None:
        print("[!] no pwnDFU device found. Run exploit first.")
        sys.exit(1)

    try:
        dfu_upload(dev, ibss_data)
    except usb.core.USBError as e:
        if "No such device" not in str(e) and "pipe" not in str(e).lower():
            raise
    print("[+] iBSS uploaded, device should enter recovery mode")

    # --- Step 2: wait for recovery mode ---
    time.sleep(3)

    # --- Step 3: send iBEC ---
    if not irecv_send(args.ibec):
        print("[!] failed to send iBEC")
        sys.exit(1)
    time.sleep(2)

    # --- Step 4: set boot-args if provided ---
    if args.boot_args:
        irecv(f"-c setenv boot-args {args.boot_args}")
    # Always enable verbose boot for debugging
    irecv("-c setenv auto-boot false", check=False)

    # --- Step 5: send devicetree ---
    if not irecv_send(args.devicetree):
        print("[!] failed to send devicetree")
        sys.exit(1)
    irecv("-c devicetree")
    time.sleep(1)

    # --- Step 6: send ramdisk (optional) ---
    if args.ramdisk:
        if not irecv_send(args.ramdisk):
            print("[!] failed to send ramdisk")
            sys.exit(1)
        irecv("-c ramdisk")
        time.sleep(1)

    # --- Step 7: send trustcache (optional) ---
    if args.trustcache:
        if not irecv_send(args.trustcache):
            print("[!] failed to send trustcache")
            sys.exit(1)
        irecv("-c firmware")
        time.sleep(1)

    # --- Step 8: send kernelcache and boot ---
    if not irecv_send(args.kernelcache):
        print("[!] failed to send kernelcache")
        sys.exit(1)
    irecv("-c bootx")

    print("[+] boot command sent -- device should be booting kernel")


if __name__ == "__main__":
    main()
