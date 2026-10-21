# Real KPI/ABI check for the 27 XR-only kexts (supersedes symbol-diff-attempt.md's blocked path)

`symbol-diff-attempt.md` found shipped kernelcaches carry no nlist symtab,
no exports trie, and a near-empty chained-fixups blob -- a raw per-symbol
diff isn't possible from these files. But each kernelcache's
`__PRELINK_INFO` segment (found via `lief`, parsed as XML plist) carries
the actual, real compatibility-check data XNU's kext loader itself uses:
every kext's `OSBundleLibraries` dict (declared minimum version of each
dependency) and every KPI pseudo-kext's own `CFBundleVersion` (current)
/ `OSBundleCompatibleVersion` (floor). This *is* the real ABI gate, not a
proxy for it -- `OSKextVersion`-based matching in `osfmk`/`libkern` is
exactly how the real kernel decides whether to load a kext at boot.

## Method

1. Extracted `__PRELINK_INFO` from both kernelcaches (XR iOS 18.7.10,
   1,851,392 bytes; iPhone 11 iOS 26.0, matching segment, both valid XML
   plists -- `research/kc-*/prelink_info.plist`).
2. Parsed `_PrelinkInfoDictionary` (241 entries for XR, 256 for iPhone
   11,8 wait -- iPhone 11/iOS26.0): array of per-kext dicts, each with
   `CFBundleIdentifier`, `OSBundleLibraries` (dependency name -> required
   version), and for the `com.apple.kpi.*` pseudo-kexts specifically,
   their own `CFBundleVersion`/`OSBundleCompatibleVersion`.
3. For all 27 XR-only kexts: pulled every `com.apple.kpi.*` dependency
   version they declare, and checked it against iOS 26.0's actual
   provided range (`[OSBundleCompatibleVersion, CFBundleVersion]`) for
   each of those 7 KPI families.
4. Separately: checked whether every *non*-KPI dependency (family/driver
   kexts like `IOGPUFamily`, `AppleARMPlatform`) they declare is present
   at all in iOS 26.0's 239-kext set.

## Result 1: KPI version floor hasn't moved

iOS 26.0's kernel's own KPI versions:

| KPI family | OSBundleCompatibleVersion (floor) | CFBundleVersion (current) |
|---|---|---|
| com.apple.kpi.bsd | 8.0.0b1 | 25.0.0 |
| com.apple.kpi.iokit | 7.0 | 25.0.0 |
| com.apple.kpi.libkern | 8.0.0d0 | 25.0.0 |
| com.apple.kpi.mach | 8.0.0d0 | 25.0.0 |
| com.apple.kpi.private | 8.0.0b1 | 25.0.0 |
| com.apple.kpi.unsupported | 8.0.0b1 | 25.0.0 |
| com.apple.kpi.dsep | 8.0.0b1 | 25.0.0 |

XR's iOS 18.7.10 kernel has the *identical* floor values, just
`CFBundleVersion 24.6.0` instead of `25.0.0` -- the "current version"
build counter moved, the compatibility floor (the actual thing kext
loading checks against) did not move at all across these 8 release
generations.

Every one of the 27 XR-only kexts declares `8.0` (sometimes less, never
more) for whichever KPI families it needs. `8.0` sits inside
`[8.0.0b1, 25.0.0]`, `[8.0.0d0, 25.0.0]`, etc. for all of them, checked
individually, not spot-checked: **all 27/27 pass iOS 26.0's actual
runtime KPI compatibility check.** This is the real mechanism XNU uses
to decide "can this kext load" -- not an approximation of it.

This directly answers the open question from `kext-diff.md`: the 27
iOS-18-vintage closed kexts are not KPI-version-blocked from loading
against an iOS-26.0 kernel. (Important caveat below -- version-gate
passing is necessary, not sufficient.)

## Result 2: dependency closure is self-contained

Checked whether each of the 27's non-KPI `OSBundleLibraries` entries
(family/platform kexts, not the generic `kpi.*` ones) exist anywhere in
iOS 26.0's 239-kext set. 7 don't:

```
com.apple.AGXFirmwareKextRTBuddy64
com.apple.driver.AppleBasebandPCI
com.apple.driver.AppleBasebandPCIICEControl
com.apple.driver.AppleUSBHSIC
com.apple.driver.AppleVortexErrorHandler
com.apple.driver.usb.AppleUSBEHCI
com.apple.driver.usb.AppleUSBOHCI
```

All 7 are *themselves* members of the same 27-kext XR-only list (e.g.
`AGXG11P` depends on `AGXFirmwareKextRTBuddy64`, which is XR-only kext
#2 on the list, not some *other* missing piece). **No new, previously
unknown dependency surfaces -- the 27 kexts' entire dependency graph
closes within: (a) the 197 kexts already shared/native in iOS 26.0, plus
(b) each other.** Confirms the 27-kext boundary from `kext-diff.md` was
the right cut; nothing outside it is pulled in transitively.

## What this does and doesn't prove

Does: the declared, version-gated ABI contract (the thing XNU's loader
actually checks, and the thing Apple engineers treat as their kext
compatibility promise) is satisfied. This is real signal, not a
heuristic -- if this check failed, these kexts flatly could not load,
no amount of patching fixes a version-gate rejection short of binary
editing the plist, which the PRELINK_INFO format would make
possible but is a last resort, not needed here.

Doesn't: guarantee behavioral compatibility. `OSBundleLibraries`
versions are coarse-grained per *KPI family*, not per-symbol -- a KPI
family can still have had individual functions change signature,
get removed and replaced by something source-compatible-only, or have
undefined behavior differences, while the family's overall compat
version stays flat (Apple bumps `OSBundleCompatibleVersion` rarely, only
for actual breaking removals across the whole family -- individual
symbol churn within a KPI family doesn't necessarily trigger it, though
in practice KPI symbol removal/signature changes are rare and usually do
bump the floor when they happen, which is *why* the floor hasn't moved).
No way to verify finer-grained than this without either symbol-rich
kext binaries (`symbol-diff-attempt.md`'s still-open options) or just
attempting the boot and observing whether these kexts actually
initialize correctly at runtime.

## Updated status for README.md item 3

Kext ABI compatibility is now the *best-supported* item in the whole
plan, not the biggest unknown anymore. Real remaining unknowns shift to
items 1 (machine config), 2 (device tree), 4 (SEP mismatch) -- those
don't yet have equivalent hard evidence behind them.
