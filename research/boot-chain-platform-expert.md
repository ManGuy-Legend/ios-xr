# Item 5 prep: platform-expert matching needs no custom code (unlike ios-sweet)

Item 5 (boot chain) is otherwise fully gated on the assumed exploit
primitive -- nothing to research about the exploit step itself. But one
piece of boot-chain glue *is* exploit-independent and checkable now:
how XNU matches and brings up its root platform-expert object from the
Device Tree, which is the first real IOKit object instantiated after
the kernel starts walking the ADT.

## ios-sweet needed a custom platform expert class

`reference/ios-sweet/xnu/patches/0016-sweet-device-tree-platform-expert.patch`
adds `SweetPlatformExpert` (subclass of `IODTPlatformExpert`), matched
via a new entry in `iokit/KernelConfigTables.cpp`'s
`gIOKernelConfigTables` on `IONameMatch = 'xiaomi,sweet'`. Needed
because: no Apple NVRAM controller (so `processTopLevel` can't do the
stock NVRAM-dependent `/options` handling) and the root compatible
string doesn't match anything Apple's own platform-matching logic
already knows about.

## Checked whether T8020 needs the same -- it doesn't

1. **XR's real ADT has a genuine `options` node** (confirmed directly,
   `research/devicetree-xr.md` -- root children include `options`
   alongside `chosen`, `cpus`, etc.). Real Apple NVRAM, not something
   that needs working around.
2. **Current XNU's `gIOKernelConfigTables` has no generic Apple
   ARM-platform matcher at all** -- checked directly
   (`reference/xnu-11215/iokit/KernelConfigTables.cpp`), the only entry
   is the `IOPanicPlatform` catch-all fallback. Real Apple platform
   matching for Apple Device Trees isn't wired through this static
   personality table.
3. **`com.apple.driver.AppleARMPlatform` -- confirmed present in both
   the XR and iPhone 11/iOS 26.0 kext lists** (shared-197 set from
   `kext-diff.md`). This is the real, closed-source kext that handles
   generic Apple-ADT platform-expert matching/bringup -- the actual
   functional equivalent of `IODTPlatformExpert`'s role in ios-sweet's
   patch, just implemented as a shared binary kext instead of open
   XNU source.

Net: XR's real ADT + the same `AppleARMPlatform` binary iOS 26.0's own
supported devices use should bring up the root platform expert with
**no custom source patch at all** -- a `SweetPlatformExpert`-equivalent
isn't needed. This is the same pattern as items 1-4: wherever ios-sweet
had to write source because they lacked real Apple hardware/firmware,
T8020 usually doesn't need the equivalent work, because the real thing
(hardware, ADT, or in this case, closed kext) already exists and is
already KPI-compatible (item 3).

## What's still genuinely unresolved in item 5

This only covers *platform-expert matching*, one specific early-boot
step. The actual boot chain assembly -- getting from
"[exploit primitive gets us code exec]" to "[our XNU build + our real
ADT + donor userland all actually running]" -- has no equivalent
shortcut and can't be scoped further without the exploit existing.
Nothing here changes that; this is incremental de-risking of one piece
that happens to be checkable independently of it.
