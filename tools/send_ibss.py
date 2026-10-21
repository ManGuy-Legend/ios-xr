#!/usr/bin/env python3
"""
send_ibss.py -- upload a patched iBSS to an iPhone XR in pwnDFU mode.

Usage:
    python3 send_ibss.py <patched_iBSS_path>

Requirements:
    pip install pyusb
    Linux: udev rules granting r/w on 05ac:1227 without sudo
    macOS: no extra setup needed
"""

import sys
import time
import usb.core
import usb.util

APPLE_VID   = 0x05AC
DFU_PID     = 0x1227

# DFU class requests
DFU_DNLOAD    = 1
DFU_GETSTATUS = 3
DFU_CLRSTATUS = 4
DFU_ABORT     = 6

DFU_CHUNK     = 0x800   # max transfer size SecureROM accepts

# DFU status codes
DFU_STATUS_OK          = 0x00
DFU_STATE_DFU_DNLOAD_IDLE = 5
DFU_STATE_DFU_MANIFEST    = 7


def find_pwndfu():
    devs = usb.core.find(idVendor=APPLE_VID, idProduct=DFU_PID, find_all=True)
    for dev in (devs or []):
        try:
            serial = usb.util.get_string(dev, dev.iSerialNumber)
        except Exception:
            continue
        if "PWND:" in serial:
            print(f"[+] pwnDFU device: {serial}")
            return dev
    return None


def dfu_get_status(dev):
    # returns (bStatus, bState)
    data = dev.ctrl_transfer(0xA1, DFU_GETSTATUS, 0, 0, 6, timeout=2000)
    return data[0], data[4]


def dfu_clear_status(dev):
    dev.ctrl_transfer(0x21, DFU_CLRSTATUS, 0, 0, None, timeout=2000)


def dfu_abort(dev):
    dev.ctrl_transfer(0x21, DFU_ABORT, 0, 0, None, timeout=2000)


def upload_ibss(dev, data):
    total   = len(data)
    offset  = 0
    block   = 0

    print(f"[*] uploading {total:#x} bytes in {DFU_CHUNK:#x}-byte chunks")

    while offset < total:
        chunk = data[offset:offset + DFU_CHUNK]
        dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, chunk, timeout=5000)

        status, state = dfu_get_status(dev)
        if status != DFU_STATUS_OK:
            dfu_clear_status(dev)
            raise RuntimeError(f"DFU error at block {block}: status={status:#x} state={state:#x}")

        offset += len(chunk)
        block  += 1

        pct = offset * 100 // total
        print(f"\r[*] {pct:3d}%  block {block}", end="", flush=True)

    print()

    # zero-length transfer signals end-of-download
    dev.ctrl_transfer(0x21, DFU_DNLOAD, block, 0, None, timeout=5000)
    status, state = dfu_get_status(dev)
    print(f"[*] final status={status:#x} state={state:#x}")


def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <patched_iBSS>")
        sys.exit(1)

    ibss_path = sys.argv[1]
    with open(ibss_path, "rb") as f:
        ibss_data = f.read()

    print(f"[*] iBSS: {ibss_path} ({len(ibss_data):#x} bytes)")
    print("[*] waiting for pwnDFU device (05ac:1227 with PWND:)...")

    dev = None
    while dev is None:
        dev = find_pwndfu()
        if dev is None:
            time.sleep(0.5)

    dev.set_configuration()

    try:
        upload_ibss(dev, ibss_data)
    except usb.core.USBError as e:
        # device disappearing after final transfer = it booted; not an error
        if "No such device" in str(e) or "pipe error" in str(e).lower():
            print("[+] device disconnected -- iBSS is booting")
        else:
            raise

    print("[+] done")


if __name__ == "__main__":
    main()
