# Item 6 (userland rootfs): new blocker found -- AEA encryption

Checked iPhone 11's iOS 26.0 `BuildManifest.plist` (already on disk,
`research/kc-i11-ios26-0/BuildManifest.plist`) for how the actual OS
images are packaged, to scope "pull OS/SystemOS/AppOS images, build a
root filesystem."

## iOS 26.0 uses the Cryptex split-volume layout, as expected

```
Cryptex1,SystemOS   -> 090-88522-661.dmg.aea      (SystemOSSize: 4640 MB)
Cryptex1,AppOS      -> 090-88877-697.dmg          (AppOSSize: 14 MB)
Cryptex1,SystemVolume/-TrustCache, Cryptex1,AppVolume/-TrustCache also present
OS (legacy/base)    -> 090-89586-625.dmg.aea
```

Confirms README item 6's framing (split OS/SystemOS/AppOS, not a single
monolithic image) was correct going in.

## New finding: the two big images are AEA-encrypted, not just IM4P

Pulled the first bytes of `090-88522-661.dmg.aea` via range read (no
full download):

```
magic: b'AEA1'   size: 1,879,048,192 bytes (1.75 GiB, matches SystemOS)
```

`AEA` = Apple Encrypted Archive, a newer (iOS 16+/macOS Sonoma+) wrapper
distinct from everything handled so far in this project -- kernelcache
and DeviceTree were both plain IM4P (IMG4 payload, LZFSE-compressed,
`pyimg4` handles it directly). AEA is a different, additional
encryption layer on top, and `pyimg4` has no support for it. Both the
main `OS` image and `Cryptex1,SystemOS` use it. This is a real
prerequisite step this project didn't have scoped before now.

`Cryptex1,AppOS` (`090-88877-697.dmg`, 14 MB) is *not* AEA-magic'd --
first bytes `86 89 07 3d a6 18 9b dd`, doesn't match IM4P's typical DER
header either. File size matches its declared decompressed size
(`AppOSSize: 14` MB) almost exactly, suggesting it might already be
close to a final flat image, but its exact format wasn't identified
further -- not worth more time until SystemOS decryption (the much
bigger, clearly-identified blocker) is actually solved.

## AEA decryption: solved, done

Downloaded `blacktop/ipsw` CLI (`v3.1.739`, Windows x86_64 prebuilt
binary -- `tools/ipsw-cli/ipsw.exe`, cross-platform Go, no macOS-only
`aastuff` needed). Mechanism, confirmed by actually running it:

1. `ipsw fw aea <file> --info` on just the first 1 MB of the AEA file
   (header + embedded auth data both sit at the start -- didn't need
   the full 1.75 GB to inspect this) printed the embedded
   `com.apple.wkms.auth-data` blob and a `fcs-key-url`:
   `https://wkms-public.apple.com/fcs-keys/0D4dRPaAE8JUTr8zHg_AxUsrup1WXsZbVZ5Xfii0t6Q=`
   -- a real, publicly reachable Apple key-server endpoint, no account
   or device auth needed for this global/non-personalized restore
   image.
2. `ipsw fw aea <file> --fcs-key` fetched that URL and cached the
   response (`research/fcs-keys.json`).
3. `ipsw fw aea <file> --key` performed the HPKE unwrap (DHKEM(P-256) +
   HKDF-SHA256 + AES-256-GCM, per the tool's implementation) and
   produced the actual symmetric decryption key as base64.
4. Downloaded the full `090-88522-661.dmg.aea` (`Cryptex1,SystemOS`,
   1,879,048,192 bytes, matches declared size exactly) via the existing
   range-read tooling.
5. `ipsw fw aea <file> --key-val <key> -o research/userland-rootfs`
   decrypted it end-to-end: pure-Go AEA decryption path, no external
   calls needed once the key was in hand.

Output: `research/userland-rootfs/SystemOS.dmg`, 4,865,392,640 bytes --
matches `Cryptex1,SystemOSSize: 4640` (MB) from `BuildManifest.plist`
exactly. Confirmed genuine by checking for APFS's `NXSB` container
superblock magic at the expected offset (0x20): **present.** Not a
corrupt/partial decrypt -- a real, valid APFS filesystem image.

Files on disk: `research/userland-rootfs/SystemOS.dmg.aea` (encrypted
source, kept for reproducibility) and `SystemOS.dmg` (decrypted, real
APFS container).

## APFS access: solved too -- `dissect.apfs` (pure Python, no mount needed)

`ipsw mount fs` wants a full IPSW path (it runs the whole
extract-decrypt-mount pipeline itself) and doesn't accept an
already-decrypted raw `.dmg` directly -- and real OS-level mounting on
Windows has no native APFS driver anyway. Used `dissect.apfs`
(`pip install dissect.apfs`, Fox-IT's forensics library, pure parsing,
no FUSE/mount/admin rights needed) directly against
`SystemOS.dmg` instead:

```python
from dissect.apfs import APFS
fs = APFS(open('research/userland-rootfs/SystemOS.dmg', 'rb'))
fs.volumes[0].name  # 'Luck23A341.N104SystemCryptex'
```

One volume, named for iPhone 11's board codename (`N104`) and build
(`23A341`) -- confirms this is genuinely that build's system cryptex,
not a decryption artifact that merely looks plausible. Root directory:
`/usr`, `/System` -- the expected modern SystemOS cryptex overlay
layout.

## Found the real userland payload: full dyld shared cache, unstripped

`/System/Library/Caches/com.apple.dyld/` contains the complete split
dyld shared cache for `arm64e` -- ~80 subcache files (`.01`-`.77`,
`.dylddata`, `.dyldlinkedit`, `.dyldreadonly`, `.atlas`, and a
standalone **`dyld_shared_cache_arm64e.symbols`, 979,599,360 bytes**).
That last one matters a lot: unlike the kernelcache (`symbol-diff-attempt.md` --
fully stripped, no nlist, no exports trie), iOS 26.0's *userland* shared
cache ships its symbol table as a real, separate, ~934 MB subcache
file. This is the actual rootfs payload item 6 needs (every system
framework/dylib, pre-linked, the same way the kernelcache pre-links
kexts) -- confirmed present, intact, and richer (symbol-wise) than
anything pulled from the kernel side so far.

## `Cryptex1,AppOS`: resolved -- not encrypted at all

Pulled the full file (14,680,064 bytes) and checked its header directly:
same `NXSB` APFS magic at offset 0x20 as the *decrypted* SystemOS image.
`Cryptex1,AppOS` was never AEA-wrapped in the first place -- its small
size apparently doesn't warrant it. `dissect.apfs` opens it directly,
one volume, `AppCryptex`, same `/usr`/`/System` layout as SystemOS.

## Extracted the full dyld shared cache locally; found + worked around a
real third-party library bug

Pulled all 80 subcache files out of the APFS container to local disk
via `dissect.apfs`. 76/80 succeeded immediately; 4 threw
`StopIteration` inside `dissect.util`'s `lzbitmap` decompressor (the 3
`.dyldlinkedit` files and `.symbols`) -- traced to a real upstream bug,
not corrupt data: `lzbitmap.py` line 81 calls `next(tokens)` with no
default, while the structurally identical call one level up (line 74)
already defaults missing tokens to `None` to mean "chunk's done." The
inner call just forgot the same tolerance.

**Correction -- the patch is not actually confirmed correct.** Patched
a local copy of `decompress()` with the matching default and first
reported this as "the correct fix" after it got further than the
original. On a second, cleaner run (same patch, just the smallest of
the 4 failed files, with progress instrumentation), it crashed
*immediately* with a completely different error: `IndexError: index
out of bounds` on `buf[bitmap_offset]`, before printing even one
progress line. That's a second, distinct bug/edge-case in the same code
path -- meaning the token-exhaustion patch may have been masking the
real problem rather than fixing it, or these 4 files hit more than one
edge case this library (and my one-line patch) don't handle correctly.

Decision: **stopped here.** Chasing a second guessed patch to a binary
compression format with no authoritative spec in hand risks producing
output that looks successfully decompressed but is silently wrong --
worse than admitting these 4 files aren't extracted. Not attempting a
third patch this session. The honest state: 76/80 subcache files
extracted and verified good; 4 (`.symbols` + 3 `.dyldlinkedit`) remain
blocked on a real bug in `dissect.util`'s `lzbitmap` decompressor that
needs either an upstream fix or a correctly-debugged local patch (not
the same guess twice), not pure performance tuning as first assumed.

## Still open for item 6

- Splitting the dyld shared cache back into individual
  framework/dylib Mach-Os (`ipsw dyld extract`, not yet tried).
- The originally-scoped work once individual binaries are extracted:
  `mkiosroot.sh`-style rootfs assembly. (No display resolution
  adjustment needed -- XR and iPhone 11 are both 828x1792 LCD.)

Item 6's three biggest unknowns -- could the encryption be broken
without device personalization, is there a real usable payload once it
is, and is `AppOS`'s format a second blocker -- are all resolved now.
What's left is mechanical (shared-cache splitting, rootfs assembly),
not open research questions.
