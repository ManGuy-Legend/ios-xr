# Draft machine config for T8020 (A12 Vortex/Tempest)

Checked current XNU (`xnu-11215.1.10`, iOS26-era, cloned to
`reference/xnu-11215/`) directly. Finding: **the cache-line-size hook for
A12's core family is already wired into the current public tree and has
never been removed.**

```
osfmk/arm64/proc_reg.h:169:  #elif defined (APPLEVORTEX)
osfmk/arm64/proc_reg.h:170-174:
    /* I-Cache, 128KB 8-way for Vortex, 48KB 6-way for Tempest. */
    #define MMU_I_CLINE 6
    /* D-Cache, 128KB 8-way for Vortex, 32KB 4-way for Tempest. */
    #define MMU_CLINE   6
```

`APPLEVORTEX` is referenced in exactly that one spot, nowhere else, and
is never `#define`d anywhere in the tree (confirmed by grep across the
whole repo) -- because the one file that would define it,
`pexpert/pexpert/arm64/H12.h`, was never shipped publicly (same pattern
as the missing T8020 board config itself -- see `xnu-findings.md` item
2). The hook is real; only the header that flips it on is missing.

Also still present and correctly wired, unmodified:

- `osfmk/arm/cpuid.h`: `CPU_PART_VORTEX = 0xB`, `CPU_PART_TEMPEST = 0xC`
  (plus `_ARUBA` variants for A12X/A12Z's die).
- `osfmk/arm64/proc_reg.h`: `MIDR_CYPRUS_VORTEX`/`MIDR_CYPRUS_TEMPEST`
  (A12's SoC codename is "Cyprus"; "Aruba" = A12X/A12Z).
- `osfmk/arm/cpuid.c`: `cpuid_get_cpufamily()`/`cpuid_get_cpusubfamily()`
  both still correctly classify `CPU_PART_VORTEX`/`TEMPEST` into
  `CPUFAMILY_ARM_VORTEX_TEMPEST` / `CPUSUBFAMILY_ARM_HP`.

None of this is dead/pruned code -- it's identical in shape to every
other supported chip's entry in the same switch statements, just never
reachable today because nothing defines the macros that would select
it at build time. This is stronger evidence than `xnu-findings.md`'s
original CPUFAMILY-enum-only finding: cpuid *detection and
classification* logic for real A12 hardware is still first-class and
maintained-looking in Apple's current kernel, not just an unremoved
enum value.

## What's needed to actually turn this on

Three pieces, mirroring `ios-sweet`'s `0004-sweet-machine-config.patch`
structure but far smaller, since T8020 is real Apple silicon with a
real AIC/cluster/PMGR -- none of SWEET's GICv3-Group0/Group1 IRQ
replacement, `sleh.c`/`pe_fiq.c`/`start.s` patches apply here at all.
Those existed in `ios-sweet` specifically because Qualcomm's SoC lacks
Apple's proprietary interrupt controller; T8020 has one.

### 1. `makedefs/MakeInc.def`

```diff
-SUPPORTED_ARM64_MACHINE_CONFIGS = T6000 T6020 T6030 T6031 T8101 T8103 T8112 T8122 T8132 VMAPPLE
+SUPPORTED_ARM64_MACHINE_CONFIGS = T6000 T6020 T6030 T6031 T8020 T8101 T8103 T8112 T8122 T8132 VMAPPLE
...
+MACHINE_FLAGS_ARM64_T8020 = -DARM64_BOARD_CONFIG_T8020           -mcpu=apple-a12
```

`apple-a12` confirmed valid as an LLVM `-mcpu` target (`xnu-findings.md`
item 3 -- it's iOS 26's own compiler default baseline). No `armv8.x`
fallback needed the way SWEET required (non-Apple core, had to target
generic ARM arch string); A12 gets the named Apple CPU model directly.

### 2. `pexpert/pexpert/arm64/board_config.h`

New block, modeled directly on the existing `T8103` block (M1, same
"2 cluster, PMAP_CS on" shape -- the actual core/cluster *count* is A12's
own, not M1's):

```c
#ifdef ARM64_BOARD_CONFIG_T8020
#include <pexpert/arm64/H12.h>

#define MAX_L2_CLINE                   7
#define MAX_CPUS                       6   /* 2 Vortex (perf) + 4 Tempest (eff) */
#define MAX_CPU_CLUSTERS               2

#define PMAP_CS                        1
#define PMAP_CS_ENABLE                 1
#define XNU_MONITOR                    1 /* Secure pmap runtime */
#endif  /* ARM64_BOARD_CONFIG_T8020 */
```

`MAX_CPUS=6` is Apple's own public spec for A12 (2 performance + 4
efficiency cores) -- not a guess. `MAX_L2_CLINE=7` and the
`PMAP_CS`/`XNU_MONITOR` trio copied verbatim from every other Apple-core
config in the file (T8101/T8103/T8112/etc. all agree on these three;
no SoC in the current tree sets them differently).

### 3. `pexpert/pexpert/arm64/H12.h` (new file, doesn't exist publicly in
any year of the public tree -- this is the real unknown)

Drafted by diffing what H13.h (`APPLEFIRESTORM`, A14/M1) declares against
what's architecturally true for A12 two generations earlier. Split by
confidence:

**High confidence (public, documented, or directly inferable from
ARM/Apple's own published facts):**

```c
#define APPLEVORTEX              /* turns on the existing proc_reg.h hook */
#define NO_MONITOR              1 /* no EL3 for any Apple AP core, ever */
#define HAS_CLUSTER             1 /* 2 Vortex + 4 Tempest, separate clusters -- Apple's own public A12 spec */
#define __ARM_AMP__              1 /* same reason */
#define HAS_IPI                  1 /* every multi-core Apple AP SoC has this */
#define CPU_HAS_APPLE_PAC        1 /* A12 is the FIRST Apple PAC/arm64e chip -- public, well-documented fact */
#define __ARM_PAN_AVAILABLE__    1 /* ARMv8.1 feature, below A12's baseline */
```

**Corrected via structural evidence in current XNU, not just literature:**
`apple_arm64_common.h` (lines 48-58) shows kernel-integrity enforcement
is selected via `#if HAS_CTRR3 / #elif HAS_CTRR / #elif HAS_KTRR / #elif MONITOR`
-- `HAS_CTRR`/`HAS_CTRR3` are what H13+ (A14-and-newer) chips define;
`HAS_KTRR` is the *explicit predecessor fallback* still wired in the
current tree (one reference, not pruned, same pattern as `APPLEVORTEX`).
KTRR (Kernel Text Read-only Region) is the well-established A10-A12
generation mechanism in public jailbreak literature (checkra1n/Pongo);
this XNU-side fallback chain structurally confirms CTRR is strictly
H13+-and-later, so A12 takes the KTRR branch:

```c
#define HAS_KTRR                 1   /* NOT HAS_CTRR -- CTRR is H13+ (A14-and-newer) only */
```

**Resolved, not actually a board-config decision at all:** `HAS_ARM_FEAT_SSBS2`
looked like another "guess A12's hardware generation" flag, but checking
`osfmk/arm/commpage/commpage.c`'s `commpage_init_arm_optional_features_pfr1()`
shows userland-visible SSBS exposure is runtime-probed from the real
`ID_AA64PFR1_EL1` register unconditionally, on every boot, regardless of
board macros -- no flag needed for that half. `HAS_ARM_FEAT_SSBS2` only
gates the *kernel's own* default `SCTLR_DSSBS` bit (`proc_reg.h:467-474`)
-- a kernel-policy choice, not a hardware-presence question. Leaving it
undefined is safe either way (worst case: kernel runs without Apple's
explicit SSBS-enabled default and falls back to the architectural reset
value instead -- a security-posture difference, not a boot-risk one),
so the draft leaves it unset with a verified reason now, not a guess:

```c
/* HAS_ARM_FEAT_SSBS2 intentionally left undefined -- safe default, not a hardware unknown */
```

**Should stay OFF (ARMv8.5-era or later features A12's ARMv8.3-ish
baseline predates):**

```c
/* HAS_E0PD not defined -- ARMv8.5-only (Meltdown TCR mitigation), A12 predates it */
/* HAS_SPECRES not defined -- same generation gap */
/* UNCORE_*/CPMU_* perf-counter block not copied -- H13's specific counter counts (10 core,
   16 uncore) are Firestorm/Icestorm numbers, not Vortex/Tempest's; needs A12-specific
   counter topology, not currently known, not guessed here */
```

Getting one of the "should stay off" wrong in the other direction (i.e.
turning on an ARMv8.5 feature A12 lacks) risks a boot-time trap on the
first instruction that assumes the register exists -- that's the kind
of mistake only a real boot attempt (or a verified datasheet) resolves,
not more reading. Flagging explicitly rather than guessing further.

## PPL/KTRR and the exploit (non-issue)

Concern: does the SecureROM exploit's post-state (shellcode disables MMU,
copies handler, re-enables MMU in SRAM) conflict with XNU_MONITOR (PPL)
or HAS_KTRR integrity checks?

Answer: **no**. The exploit runs entirely in SecureROM context (EL1 over
SRAM at `0x19C000000`). iBSS is a clean binary uploaded into DRAM via
DFU_DNLOAD after the exploit. XNU boots from clean DRAM with no residual
exploit state. KTRR lockdown (`rorgn_lockdown()` in
`osfmk/arm64/amcc_rorgn_ppl.c`) is called by XNU itself during boot to
lock down its own `__TEXT_EXEC` — SecureROM doesn't set KTRR registers.
PPL's `rorgn_validate_core()` runs per-core during init and validates the
KTRR region XNU itself configured, not anything from the exploit stage.

`__ARM_KERNEL_PROTECT__` is NOT defined in our H12.h or anywhere in its
include chain (`apple_arm64_common.h`), so the `tlb.h:359` error
(`__ARM_RANGE_TLBI__ + __ARM_KERNEL_PROTECT__ is not currently supported`)
does not fire.

## What this changes in the plan

Item 1 (machine config) is no longer "unscoped -- needs invention from
scratch." Two of its three pieces (`MakeInc.def`, `board_config.h`) are
now fully drafted and low-risk (copied patterns Apple itself reuses
verbatim across its own chip family). The third (`H12.h`) has a
confident skeleton and an explicitly bounded set of genuinely unverified
flags -- a short, named list to resolve by testing, not an open-ended
unknown anymore.
