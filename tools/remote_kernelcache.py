#!/usr/bin/env python3
"""
Pull just the BuildManifest.plist + kernelcache entry out of a remote IPSW
zip over HTTP range requests, without downloading the whole (multi-GB)
file. Written fresh for this project (not copied from ios-sweet's
remotezip.py -- same general range-read technique, independent code).

Usage: python3 remote_kernelcache.py <ipsw_url> <out_dir>
"""
import io
import sys
import os
import plistlib
import urllib.request
import zipfile


class HTTPRangeFile(io.RawIOBase):
    """A read-only, seekable file-like object backed by HTTP Range requests."""

    def __init__(self, url):
        self.url = url
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.length = int(resp.headers["Content-Length"])
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.length + offset
        return self.pos

    def tell(self):
        return self.pos

    def readinto(self, b):
        n = len(b)
        if n == 0 or self.pos >= self.length:
            return 0
        end = min(self.pos + n, self.length) - 1
        req = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.pos}-{end}"}
        )
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def find_kernelcache_path(manifest):
    # BuildManifest.plist -> BuildIdentities[*].Manifest.KernelCache.Info.Path
    # Pick the "Customer Erase Install (IPSW)" / default identity (no
    # RestoreBehavior == "Erase" filtering needed for our purposes --
    # any identity's kernelcache path is fine, they're the same file).
    for ident in manifest.get("BuildIdentities", []):
        kc = ident.get("Manifest", {}).get("KernelCache")
        if kc and "Info" in kc and "Path" in kc["Info"]:
            return kc["Info"]["Path"]
    raise RuntimeError("No KernelCache entry found in BuildManifest.plist")


def main():
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <ipsw_url> <out_dir>")
    url, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    print(f"Opening remote zip (range reads): {url}")
    f = HTTPRangeFile(url)
    buffered = io.BufferedReader(f, buffer_size=1 << 20)
    zf = zipfile.ZipFile(buffered)

    print("Reading BuildManifest.plist...")
    with zf.open("BuildManifest.plist") as fp:
        manifest = plistlib.load(fp)

    kc_path = find_kernelcache_path(manifest)
    print(f"KernelCache path in IPSW: {kc_path}")

    out_im4p = os.path.join(out_dir, "kernelcache.im4p")
    with zf.open(kc_path) as fp, open(out_im4p, "wb") as out:
        out.write(fp.read())
    print(f"Wrote {out_im4p} ({os.path.getsize(out_im4p)} bytes)")

    manifest_out = os.path.join(out_dir, "BuildManifest.plist")
    with open(manifest_out, "wb") as out:
        plistlib.dump(manifest, out)
    print(f"Wrote {manifest_out}")


if __name__ == "__main__":
    main()
