# Kernelcache assembly: merging XR kexts into an iOS 26 kernel

## Problem

iOS 26's kernelcache for iPhone 11 (T8030) contains 239 kexts. iPhone XR
(T8020) needs 27 additional platform kexts that only exist in its iOS 18
kernelcache (see `kext-diff.md`). The final kernelcache must combine:

1. Our custom XNU kernel built with `ARM64_BOARD_CONFIG_T8020`
2. The 197 shared kexts (from iOS 26's kernelcache, already compiled for
   arm64e and iOS 26's KPI)
3. The 27 XR-only kexts (from XR's iOS 18 kernelcache, compiled for
   arm64e, KPI-compatible per `kpi-abi-check.md`)

## Format: MH_FILESET (Kernel Collection)

Both kernelcaches are `MH_FILESET` Mach-Os. Each kext is an
`LC_FILESET_ENTRY` load command pointing to a sub-Mach-O embedded in the
fileset. The `__PRELINK_INFO` segment carries an XML plist with each kext's
`OSBundleLibraries`, load address, and metadata.

## Approach

### Option A: Build from source (ideal but blocked)

Build XNU with `ARM64_BOARD_CONFIG_T8020` using
`blacktop/darwin-xnu-build` on GitHub Actions (macOS runner). This
produces a bare kernel. Kext injection still needed — Apple's internal
`kextcache`/KernelManagement tools aren't public for fileset assembly.

### Option B: Binary surgery on existing kernelcache (pragmatic)

1. Start with iPhone 11's iOS 26 kernelcache (decompressed MH_FILESET)
2. Extract the 27 XR-only kexts from XR's iOS 18 kernelcache as raw
   Mach-O sub-images (each `LC_FILESET_ENTRY` has a `fileoff` and the
   sub-image has its own Mach-O header with segment sizes)
3. Append them to the fileset, adding new `LC_FILESET_ENTRY` commands
4. Merge their `__PRELINK_INFO` entries
5. Replace the kernel sub-image with our T8020-configured build

Tools needed:
- `lief` (Python): parse/modify MH_FILESET load commands
- Custom script: VM address allocation for injected kexts (find free
  regions in the virtual address map), fixup chain updates
- `pyimg4`: re-wrap as IM4P (unsigned — iBSS signature check is patched)

### Fixup chains

Modern kernelcaches use chained fixups (`LC_DYLD_CHAINED_FIXUPS`) instead
of traditional relocations. Injected kexts may reference symbols in the
shared set — these binds must resolve against the iOS 26 kernel's symbol
addresses. Since shipped kernelcaches have no symtab (`symbol-diff-attempt.md`),
symbol resolution for cross-kext references is the hardest sub-problem.

Mitigation: the 27 XR-only kexts' `OSBundleLibraries` close entirely
within the shared-197 + each other (`kpi-abi-check.md`). Their KPI
dependencies are on stable `com.apple.kpi.*` bundles whose ABI is
version-gated and compatible. The kexts are closed-source binary blobs
compiled against iOS 18's KPI, which has the same floor versions as iOS
26's. Runtime KPI dispatch goes through well-known vtable/function-pointer
patterns that don't need symbol-level fixups — the KPI stubs are at
fixed virtual addresses within the kernelcache.

### What we skip for initial boot

- GPU kexts (`AGXG11P`, firmware RTBuddy): not needed for console boot.
  Display can run through the framebuffer set up by iBSS/iBoot, or
  through the simpler `AppleMobileDispH11P` panel driver.
- Baseband kexts (`AppleBasebandD101`, PCI variants): not needed for
  boot, only for cellular.
- Audio/haptics: not needed for boot.

**Minimum viable kext set for first boot**: `AppleT8020` (SoC core),
`AppleT8020PMGR` (power), `AppleT8020CLPCv3` (clock), plus the
shared-197 set. That's 3 kexts to inject, not 27.

## Next step

Write `tools/assemble_kernelcache.py` that:
1. Parses both kernelcaches with `lief`
2. Extracts the minimum 3 kexts from XR's iOS 18 cache
3. Injects them into iOS 26's cache with correct LC_FILESET_ENTRY
4. Updates __PRELINK_INFO
5. Outputs unsigned IM4P for upload via `send_ibss.py`

This depends on having the custom XNU kernel binary (from Option A).
Build pipeline must be set up first.
