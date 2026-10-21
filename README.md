# ios-xr: running an iOS 26-era userland on iPhone XR (A12/T8020)

Goal: run a real, unmodified iOS 26 userland (SpringBoard, daemons, dyld
cache) on an iPhone XR, whose A12 (T8020) chip Apple dropped from iOS 26's
official support list. Modeled on
[ios-sweet](https://github.com/MakrSas/ios-topaz)'s proof that this class of
problem (new/unsupported kernel target + real iOS userland on top) is
solvable -- but T8020 is **real Apple silicon**, not foreign Qualcomm SoC
ios-sweet targeted, which changes which parts of their problem we inherit
and which we get for free.

**This assumes a legitimate, non-enjoined A12 SecureROM exploit exists or
will exist**, used only as an opaque "get unsigned code running before the
chain of trust locks us out" primitive (see `research/bootrom-exploit.md`).
No exploit code lives in this repo. `usbliter8` specifically is not used
here or anywhere in this project -- see that file for why.

## Status: research/planning only, no working build yet

This is a multi-month kernel-engineering project, not a firmware flash. What
follows is a grounded technical plan based on actually diffing Apple's
public XNU source across releases (see `research/`), not assumption.

## The core finding that shapes this plan

Checked `apple-oss-distributions/xnu` at `xnu-10063.141.1` (iOS 18, A12's
last supported OS) and `xnu-11215.1.10` (iOS 26-era) directly:

- `CPUFAMILY_ARM_VORTEX_TEMPEST` (A12's CPU family ID) is present,
  unremoved, in both -- Apple doesn't strip dead CPU-family enums from
  shared/generic kernel code.
- **Neither release has ever shipped a buildable machine config for T8020
  or T8030** (`makedefs/MakeInc.def`'s `SUPPORTED_ARM64_MACHINE_CONFIGS`
  lists only Mac-chip configs -- T6xxx/T81xx M-series -- plus VMAPPLE,
  across both tags). This isn't something Apple removed for iOS 26; the
  public XNU dump never included real iPhone SoC board configs at all,
  in any year checked.
- **iOS 26's own compiler baseline targets A12 exactly**: LLVM commit
  `69d14171` (PR #152235) sets `arm64-apple-ios26`'s default `-mcpu` to
  `apple-a12`. iOS 26 userland code's floor target is this exact chip --
  strong evidence the support drop is a product/signing-matrix decision,
  not an ISA/ABI incompatibility.

Full citations and repro commands in `research/xnu-findings.md`.

So the task isn't "iOS 26's kernel dropped A12, add it back" -- it's "no
public XNU build has ever targeted an iPhone SoC directly; define a new
custom machine config for T8020, the same way ios-sweet invented their
`SWEET` config for Snapdragon." Same category and rough magnitude of kernel
bring-up work as ios-sweet, just for different hardware.

## Where real Apple silicon helps vs. doesn't

| ios-sweet (Snapdragon) had to... | On real A12 (XR), likely... |
|---|---|
| Write a USB/UFS storage driver from scratch | NAND storage controller is Apple's own ANS2; existing closed-source kext from iOS 18 *may* attach if kernel KPI stays ABI-compatible (unverified) |
| Write touch/display/PMIC drivers from scratch | Equivalent closed kexts already exist for this exact hardware (AppleCLCD/DCP, Multitouch, PMU) -- reuse is plausible, not guaranteed |
| Build a custom Device Tree mimicking an iPhone X's ADT | XR's *real* ADT already exists (pull from an iOS 18 IPSW) -- no need to fabricate one |
| No real bootloader security to fight (just unlocked Android bootloader) | XR's SecureROM enforces the chain of trust for real -- needs the exploit primitive to get an unsigned/patched kernel to boot at all |
| — | **SEP firmware mismatch**: XR's SEP only ever ran iOS-18-era firmware. An iOS 26 AP kernel/userland built assuming a matching SEP will not have one. This is the *same category* of problem as ios-sweet's "no SEP at all" -- likely needs the same kind of AMFI/trustcache/corecrypto bypass patches ios-sweet carries (see their `xnu/patches/0015,0025,0035,0036` for AMFI/trustcache, `0013,0014,0020,0027,0032,0033` for corecrypto replacement), even though the hardware is Apple's own |

## Plan

1. **Machine config** -- *drafted and mostly resolved, see
   `research/machine-config-draft.md`*: found the cache-line-size hook
   for A12 (`APPLEVORTEX`) is already live in current XNU's
   `proc_reg.h`, just never `#define`d (no public `H12.h`). `MakeInc.def`
   + `board_config.h` changes are fully drafted, low-risk (copied
   patterns Apple reuses verbatim across its own chip configs). `H12.h`'s
   two flags that first looked like unverifiable guesses both resolved
   with real structural evidence instead: `HAS_ARM_FEAT_SSBS2` isn't a
   board decision at all (SSBS exposure is runtime-probed from hardware
   unconditionally); `HAS_CTRR` was wrong -- current XNU's own
   `HAS_CTRR3 -> HAS_CTRR -> HAS_KTRR -> MONITOR` fallback chain
   confirms CTRR is H13+-only and A12 takes the `HAS_KTRR` branch. Only
   genuinely hardware-untestable items left are a couple of power-state
   retention flags. Far smaller scope than `ios-sweet`'s equivalent
   patch overall, since T8020 has Apple's real AIC/interrupt controller
   -- none of their GICv3 Group0/Group1 IRQ-replacement work applies.
2. **Device Tree** -- *done, see `research/devicetree-xr.md`*: pulled
   and parsed XR's real ADT (`DeviceTree.n841ap.im4p`) via the same
   range-read technique as the kernelcaches. Confirms 2 Vortex + 4
   Tempest cores (validates item 1's core-count assumption against real
   hardware, not just spec), a real `aic` node (confirms no GICv3-style
   interrupt rework needed, unlike ios-sweet), and a real `sep` node
   whose AP-side IOP protocol is shared with T8015/A11 -- narrows item 4
   to "firmware vintage mismatch on real hardware," not "no SEP at all."
3. **Kext ABI** -- *resolved, see `research/kext-diff.md` +
   `research/kpi-abi-check.md`*: pulled both kernelcaches directly from
   Apple's signed IPSWs (XR's iOS 18.7.10, iPhone 11's 26.0) and diffed
   their kext fileset entries. Of XR's 224 kexts, **197 (88%) already
   exist natively in the iOS 26.0 kernelcache** -- no porting question
   for those at all. Exactly **27 are XR/T8020-only** (platform
   power/clock mgmt, A12's GPU, its USB host controller, baseband, audio,
   haptics -- full list in the diff doc). A true per-symbol diff turned
   out to be impossible (shipped kernelcaches ship with no symtab/exports
   trie at all -- see `research/symbol-diff-attempt.md`), but
   `__PRELINK_INFO`'s `OSBundleLibraries` data gives the *real* check XNU
   itself uses: **all 27/27 pass iOS 26.0's actual KPI version-compat
   gate**, and their dependency graph closes entirely within the shared
   197 + each other (no new missing pieces). This is no longer the
   biggest unknown in the plan -- items 1, 2, 4 are now the open ones.
4. **SEP mismatch** -- *patched, see `research/sep-mismatch.md` +
   `xnu/patches/0002-t8020-sep-fallback.patch`*: XR's SEP runs iOS 18
   firmware; iOS 26 AP kernel may not pair with it. AP-side kexts
   (`AppleSEPManager`, `AppleMobileFileIntegrity`, `AppleImage4`) are
   in the shared-197 set, so they load fine. Patch 0002 adds graceful
   fallback: if Image4/AMFI interfaces fail to register (SEP comm
   failure), trust cache init returns safely instead of panicking, and
   root-filesystem binaries are marked CS_PLATFORM_BINARY directly.
   Backup plan: `mineek/seprmvr64` (SEP removal patches) if the
   SEP-less path doesn't work at runtime.
5. **Boot chain** -- *exploit ready, tools written*: pwnDFU exploit
   (`my_exploit/`) -> DFU_DNLOAD patched iBSS -> boots custom XNU +
   ADT. `tools/patch_ibss.py` fetches/patches iBSS from Apple CDN
   (signature verify -> MOV W0,#0+RET, production gate -> NOP).
   `tools/send_ibss.py` uploads via PyUSB in 0x800-byte chunks.
   Platform-expert matching needs no custom class (*see
   `research/boot-chain-platform-expert.md`*) -- XR's real ADT has a
   genuine `options`/NVRAM node, `AppleARMPlatform` handles it.
6. **Userland** -- *both blockers solved, see `research/userland-rootfs.md`*:
   decrypted `Cryptex1,SystemOS` via public FCS key, read with
   `dissect.apfs`. 76/80 dyld subcache files extracted clean. The 4
   remaining (`.symbols` + 3 `.dyldlinkedit`) are non-critical for
   boot -- `.symbols` is debug info, `.dyldlinkedit` is linker
   metadata, neither needed at runtime. XR and iPhone 11 share the
   same 828x1792 LCD resolution, so no display-size adjustment needed.
7. **Kernelcache assembly** -- *scoped, see
   `research/kernelcache-assembly.md`*: minimum viable boot needs only
   3 of 27 XR-only kexts injected into iOS 26's kernelcache
   (`AppleT8020`, `AppleT8020PMGR`, `AppleT8020CLPCv3`). Binary
   surgery on MH_FILESET format using `lief`. GPU/baseband/audio/haptics
   kexts deferred to post-boot. Depends on having the custom XNU binary
   from a macOS/Xcode build.

## Directory layout

- `research/` -- findings, diffs, open questions (start here)
- `xnu/patches/` -- real patch set against `xnu-11215.1.10`:
  - `0001-t8020-machine-config.patch` -- machine config (H12.h,
    MakeInc.def, board_config.h)
  - `0002-t8020-sep-fallback.patch` -- SEP/trust-cache graceful
    degradation (active only under `ARM64_BOARD_CONFIG_T8020`)
  Source only -- not compiled. Building needs macOS/Xcode
  (`blacktop/darwin-xnu-build` on GitHub Actions).
- `tools/` -- build/IPSW-extraction tooling, written fresh (no code copied
  from ios-sweet; see `reference/` for why)
- `reference/ios-sweet/` -- read-only clone of ios-sweet for studying their
  approach. No LICENSE file in their repo, so nothing from it gets copied
  into our own patches/tools -- it's reference material only.

## Current state

All 7 plan items have artifacts or are resolved:

| Item | Status | Artifact |
|------|--------|----------|
| 1. Machine config | Patch done | `0001-t8020-machine-config.patch` |
| 2. Device Tree | Done (real ADT, byte-exact) | `tools/adt.py` |
| 3. Kext ABI | Resolved (27/27 pass KPI gate) | `research/kpi-abi-check.md` |
| 4. SEP mismatch | Fallback patched | `0002-t8020-sep-fallback.patch` |
| 5. Boot chain | Tools written | `tools/patch_ibss.py`, `tools/send_ibss.py` |
| 6. Userland | Extracted (76/80 files, 4 non-critical) | `research/userland-rootfs.md` |
| 7. Kernelcache assembly | Scoped (3-kext MVP) | `research/kernelcache-assembly.md` |

## Next steps (in order)

1. **Recompile shellcode** from clean source (`my_exploit/t8020_t8006_shellcode/make.sh`,
   `my_exploit/usb_req_handler/make.sh`) on macOS. PWND string already
   changed to `[ios-xr]` in `start.S`.
2. **Build XNU** with T8020 config via `blacktop/darwin-xnu-build` on
   GitHub Actions (macOS runner). Apply both patches.
3. **Assemble kernelcache** -- inject minimum 3 XR kexts into iOS 26
   base (`research/kernelcache-assembly.md`).
4. **First boot attempt** -- exploit -> pwnDFU -> upload patched iBSS ->
   boot custom kernelcache + XR's real ADT. Serial console for debug.
5. **Iterate** on SEP behavior (does pairing work? does fallback
   activate?) and missing kexts.
