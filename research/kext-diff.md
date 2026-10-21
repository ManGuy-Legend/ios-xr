# Kext fileset diff: XR's iOS 18 kernelcache vs iPhone 11's iOS 26.0 kernelcache

Pulled both kernelcaches directly from Apple's signed IPSWs via ranged HTTP
reads (no full IPSW download needed -- see `tools/remote_kernelcache.py`),
decompressed the IM4P wrapper with `pyimg4`, parsed as Mach-O filesets with
`lief`.

- Source A: `iPhone11,8_18.7.10_22H374` (iPhone XR, A12/T8020) --
  `kernelcache.release.iphone11b`, 56,590,336 bytes decompressed.
- Source B: `iPhone12,1_26.0_23A341` (iPhone 11, A13/T8030) --
  `kernelcache.release.iphone12b`, 64,225,280 bytes decompressed.

Both are `MH_FILESET` Mach-Os (modern Kernel Collection format): every kext
is an `LC_FILESET_ENTRY` load command with the kext's bundle ID as its
`entry_id`. Enumerating those gives the exact kext list built into each
kernelcache -- ground truth, not guesswork.

## Numbers

- XR iOS 18: **224** fileset entries (`kextlist-xr-ios18.txt`)
- iPhone 11 iOS 26.0: **239** fileset entries (`kextlist-i11-ios26.0.txt`)
- Shared bundle IDs: **197**
- XR-only (not in iPhone 11's build at all): **27**

## The 27 XR-only kexts

```
com.apple.AGXFirmwareKextG11PRTBuddy
com.apple.AGXFirmwareKextRTBuddy64
com.apple.AGXG11P
com.apple.driver.AppleAD5860
com.apple.driver.AppleBasebandD101
com.apple.driver.AppleBasebandPCI
com.apple.driver.AppleBasebandPCIICEControl
com.apple.driver.AppleBasebandPCIICEPDP
com.apple.driver.AppleBluetooth
com.apple.driver.AppleCS42L75Audio
com.apple.driver.AppleHapticsSupportCallan
com.apple.driver.AppleIDBusCharger
com.apple.driver.AppleMCA2-T8020
com.apple.driver.AppleMobileDispH11P
com.apple.driver.AppleT8020
com.apple.driver.AppleT8020CLPCv3
com.apple.driver.AppleT8020PCIe
com.apple.driver.AppleT8020PMGR
com.apple.driver.AppleT8020PMPFirmware
com.apple.driver.AppleT8020PPM
com.apple.driver.AppleT8020SOCTuner
com.apple.driver.AppleT8020USB
com.apple.driver.AppleUSBHSIC
com.apple.driver.AppleVortexErrorHandler
com.apple.driver.usb.AppleUSBEHCI
com.apple.driver.usb.AppleUSBHostT8020
com.apple.driver.usb.AppleUSBOHCI
```

Every one of these is exactly what you'd predict from the SoC-platform
layer: T8020 power/clock management (`PMGR`/`PPM`/`CLPCv3`/`SOCTuner`),
A12's GPU (`AGXG11P`, G11P = Apple's name for A12's GPU core, plus its
firmware/RTBuddy companions), the A12-specific USB host controller
(`AppleUSBHostT8020`, plus legacy EHCI/OHCI/HSIC controllers this
generation still carried), `AppleVortexErrorHandler` (Vortex = A12's
performance-core codename), baseband (XR's Intel modem variant, named
differently than iPhone 11's), audio codec (`AppleCS42L75Audio`,
`AppleMCA2-T8020`), haptics (`AppleHapticsSupportCallan`), and a couple of
smaller peripheral drivers (`AppleAD5860`, `AppleIDBusCharger`,
`AppleBluetooth`, `AppleMobileDispH11P` -- the H11P panel driver, XR's
actual display).

## What this actually resolves

The open question from `README.md` item 3 ("kext ABI compatibility") is now
much more concrete:

- **197/224 (88%) of XR's kernel is a non-issue.** These bundle IDs are
  already present, natively, in the iOS 26.0 kernelcache Apple shipped for
  a supported A13 device. No porting needed for this part at all -- use
  the donor kernelcache's versions as-is.
- **The real unknown narrows to exactly these 27 named kexts.** The
  question is no longer "how much driver work does this need" in the
  abstract -- it's specifically: do these 27 iOS-18-vintage (built ~2025)
  closed-source binaries load and bind against an iOS-26.0-vintage
  (built ~2025 but 8 kernel generations later in KPI terms) kernel's
  exported symbol set.

## Next step to actually answer that -- done, see `research/kpi-abi-check.md`

A literal per-symbol diff turned out to be impossible: both shipped
kernelcaches have no `LC_SYMTAB`, no exports trie, and only a vestigial
~1KB `LC_DYLD_CHAINED_FIXUPS` blob (see `research/symbol-diff-attempt.md`
for the dead end). Instead, extracted `__PRELINK_INFO` (a real plist
Apple embeds in every kernelcache) and used the `OSBundleLibraries`
version-gated dependency data it carries -- the actual mechanism XNU's
kext loader checks at boot, not an approximation. Result: all 27/27 XR
kexts pass iOS 26.0's live KPI compatibility floor, and their dependency
graph closes within the shared 197 + each other. Full detail in
`kpi-abi-check.md`.
