# Per-symbol KPI diff: attempted, blocked by data not existing in shipped binary

Tried the next step from `kext-diff.md`: diff each of the 27 XR-only kexts'
imported symbols against iOS 26.0's kernel exports. Result: **the data
needed doesn't exist in the shipped kernelcache at all.** Not a tooling
limitation worked around -- a real negative finding.

## What's actually in `kc-xr-ios18/kernelcache.bin`

Checked via `lief`, then confirmed independently by scanning raw bytes:

- `LC_SYMTAB`: absent. `b.symbols` == 0, for the top-level binary and for
  every one of the 224 `filesets[i]` sub-binaries individually.
- `dyld_exports_trie`: absent (`has_dyld_exports_trie == False`).
- `LC_DYLD_CHAINED_FIXUPS`: present but near-empty -- `data_size = 0x44c`
  (1,100 bytes) in a 56 MB file, `imports_count = 0`. Not the real
  cross-kext binding mechanism; too small to encode binding info for 224
  images.
- Raw byte scan for common KPI symbol strings (null-terminated, as nlist
  stringtable entries would appear): `_IOMalloc\x00` and `_kernel_task\x00`
  -- **absent**. (`_panic\x00` matched, but that's a cstring literal used
  in log/panic format strings, not a symbol-table entry -- not evidence of
  a surviving symtab.)
- Bundle ID strings (e.g. `com.apple.driver.AppleT8020PMGR`) *are* present
  as raw strings -- confirms the file isn't corrupt/truncated, just that
  symbol names specifically are gone.

## Why: this is expected, not a bug

Apple's shipped release kernelcaches are a single, fully prelinked
`MH_FILESET` Mach-O. Kext-to-kernel binding happens once, at Apple's
kextcache build time, by patching direct addresses into fixup chains --
not via named bind opcodes resolved at runtime. Once that's done, symbol
*names* serve no purpose for the shipped binary to function, so they're
stripped for the public release build. (Apple's own crash/panic
symbolication relies on separate, non-public symbol stores -- not
anything in the IPSW.)

Net: there's no symbol-name-level "what does kext X import" data sitting
in the signed IPSWs we can pull. The planned diff (`kext-diff.md`'s "next
step") can't be done this way, period -- not with better tooling, with
this data source.

## What would actually answer the KPI-gap question

1. **Xcode iOS DeviceSupport symbol packages.** Xcode downloads
   per-build, per-device "Symbols" bundles (kernelcache + kexts with
   nlist symtabs restored) when a real device connects, for crash
   symbolication. These exist for both iOS 18 (XR) and iOS 26 builds.
   This is the realistic unblock -- unverified whether a T8020/iOS18.7.10
   bundle and a T8030/iOS26.0 bundle are both actually obtainable this
   way without owning both physical devices; needs checking.
2. **Public XNU `.exports` files** (`config/*.exports` --
   `osfmk.exports`, `bsd.exports`, `iokit.exports`, etc. in
   `apple-oss-distributions/xnu`) do list the kernel's intended KPI
   export surface by name, per release, straight from source. This gives
   "what iOS 26.0's kernel exports" independent of the stripped binary.
   Doesn't give "what the 27 kexts import" (that half still needs
   symbol-rich kext binaries from source 1), but it's real, public,
   already-available data for the other half of the diff.
3. Failing both: fall back to behavioral/structural inference instead of
   a symbol diff -- e.g. checking whether IOKit class hierarchies and
   provider/nub names referenced by these 27 kexts' Info.plist
   `IOKitPersonalities` still exist in iOS 26.0's kext set, since IOKit
   matching happens by class/property name, not symbol linkage, and is
   visible without any binary symbol data at all (`IOKitPersonalities`
   are plist data embedded in each kext's `__info_plist` section, not
   stripped the way symbols are -- unverified but worth checking next,
   lower effort than hunting for DeviceSupport bundles).

Next concrete action: check whether `__info_plist`/`__TEXT.__info_plist`
sections survive in these stripped fileset entries -- if yes, (3) is
immediately actionable with data already on disk, no new downloads
needed.
