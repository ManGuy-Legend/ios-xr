# Confirmed findings (with sources/repro)

## 1. A12's CPU family enum is not removed from current XNU

```
curl -s https://raw.githubusercontent.com/apple-oss-distributions/xnu/xnu-10063.141.1/osfmk/mach/machine.h | grep VORTEX_TEMPEST
curl -s https://raw.githubusercontent.com/apple-oss-distributions/xnu/xnu-11215.1.10/osfmk/mach/machine.h | grep VORTEX_TEMPEST
```
Both print `#define CPUFAMILY_ARM_VORTEX_TEMPEST 0x07d34b9f` (A12's id).
Unremoved between iOS 18 (last A12-supporting release) and the iOS 26-era tag.

## 2. Public XNU has never had a buildable T8020/T8030 machine config

`makedefs/MakeInc.def`, `SUPPORTED_ARM64_MACHINE_CONFIGS`:

- `xnu-10063.141.1` (iOS 18): `T6000 T8101 T8103 VMAPPLE`
- `xnu-11215.1.10` (iOS 26-era): `T6000 T6020 T6030 T6031 T8101 T8103 T8112 T8122 T8132 VMAPPLE`

No T8020 (A12) or T8030 (A13) in either. This was never present, in any
year checked -- not something iOS 26 removed. Public XNU dumps only ever
shipped Mac-chip configs (+ a few shared SoCs: T8112/T8122/T8132) and
VMAPPLE. Conclusion: defining a buildable iPhone-SoC machine config is
*always* custom work on top of the public tree, regardless of target iOS
version -- ios-sweet's `SWEET` config is the precedent for how, not an
exception to some removed default.

## 3. iOS 26's compiler baseline targets A12 exactly -- confirmed via LLVM source

LLVM commit `69d141712a107ed9f38fbfa12b0c35504f8bfb7c` (PR #152235, "[AArch64]
Bump default CPUs for iOS 26/watchOS 26 to A12/S6", Ahmed Bougacha):

```
arm64-apple-ios26    -> apple-a12   (default -mcpu)
arm64e-apple-ios26    -> apple-a12   (unchanged, already the baseline)
```

iOS 26 userland code's default compiled target is `apple-a12` -- the same
chip in the XR. This is strong evidence the support drop is a product/
signing-matrix decision (Apple's own stated reasoning: Neural Engine
performance for iOS 26's on-device AI features), not an instruction-set or
ABI incompatibility. iOS 26 binaries should run correctly on real A12
silicon at the CPU level.

## Open questions (not yet resolved)

- Whether A12's actual clang target in a buildable XNU machine config needs
  `-mcpu=apple-a12` or something more specific for cache-line/PAC handling
  in `osfmk/arm64/proc_reg.h` (ios-sweet's equivalent patch, `0004`, had to
  hand-write cache line sizes for their non-Apple cores -- A12 is a real
  Apple core so this should be more direct, but unverified without actually
  attempting the build).
- Kext ABI compatibility between iOS 18-era closed-source A12 peripheral
  kexts and an iOS 26-vintage kernel's KPI exports (the actual fork in the
  road for how much driver work this needs -- see README.md item 3).
- SEP firmware mismatch handling specifics (assumed same category as
  ios-sweet's no-SEP posture, not yet scoped in detail).
