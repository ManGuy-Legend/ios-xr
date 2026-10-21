# XR's real Device Tree, extracted and parsed

Pulled `Firmware/all_flash/DeviceTree.n841ap.im4p` from the same
iPhone11,8_18.7.10_22H374 IPSW via range read (same technique as
`tools/remote_kernelcache.py`), decompressed with `pyimg4`
(compression type 2, LZFSE -- 37,026 -> 208,156 bytes), wrote a minimal
ADT (Apple Device Tree binary format) parser (no existing library in
the installed Python env handles this format; it's a simple recursive
`nprop:u32, nchild:u32, [32-byte-name + u32-len + data]*, [child]*`
layout, 4-byte aligned). Parser consumed exactly 208,156 of 208,156
bytes with no overrun -- confirms correct parsing, not guesswork.

Files: `research/devicetree-xr/DeviceTree.im4p` (raw),
`DeviceTree.bin` (decompressed).

## Root node -- confirms this is genuinely XR's tree

```
model        -> iPhone11,8
compatible   -> N841AP
target-type  -> N841
```

n841 is XR's real internal board codename. No fabrication needed here,
as called out in README's comparison table -- confirmed directly, not
assumed.

## `cpus`: validates the machine-config draft against real hardware

```
cpu0..cpu3  compatible: apple,tempest   (4x efficiency cores)
cpu4,cpu5   compatible: apple,vortex    (2x performance cores)
```

6 cores total, 2+4 split -- matches `machine-config-draft.md`'s
`MAX_CPUS=6` exactly, now grounded in XR's actual device tree rather
than public marketing specs alone.

## `arm-io`: 94 real peripheral nodes, no fabrication needed

Full child list pulled (`aic`, `pmgr`, `sep`, `disp0`, `ans`, `dart-*`
x11, `mca0`-`mca5`, `isp`/`avd`/`ave`/`jpeg0`/`jpeg1`/`scaler0`
camera pipeline, `usb-complex`, `smc`, `spmi`, `gpio`, `aop`, etc. --
94 total, see raw dump). Three checked in detail:

- **`aic`** (`compatible: aic,1`): real Apple Interrupt Controller node
  present with real MMIO ranges. Confirms the machine-config draft's
  central claim -- T8020 has Apple's actual AIC hardware, unlike
  `ios-sweet`'s Snapdragon target, so none of their GICv3
  Group0/Group1 IRQ-replacement patches are needed here.
- **`pmgr`** (`compatible: pmgr1,t8020`): real power-manager node,
  `t8020`-suffixed compatible string -- Apple's own device trees use
  the exact same `t8020` naming this project's draft `ARM64_BOARD_CONFIG_T8020`
  macro already follows, independently arrived at.
- **`sep`** (`compatible: iop,t8020 iop,t8015`): Secure Enclave node
  present, and its AP-side IOP mailbox protocol compat string is shared
  with T8015 (A11) -- the AP<->SEP communication protocol itself is a
  long-lived, cross-generation interface, not something that changes
  per chip. This narrows item 4's problem: it's not "no SEP exists" the
  way `ios-sweet` faced (no SEP hardware at all on Snapdragon) -- XR
  has real SEP hardware with a stable, known AP-side protocol. The
  actual problem stays what `README.md` already says: the *firmware*
  running on that real SEP is iOS-18-vintage, and won't match whatever
  key-hierarchy/feature expectations an iOS-26 AP build has. Real SEP
  presence is a (modest) advantage over ios-sweet's position, not
  parity with it.

## What this resolves for item 2

Item 2 ("pull XR's real ADT, no fabrication needed") is done -- the
data is in hand, parses cleanly, and cross-validates the machine-config
draft's core-count/cluster assumptions independently. Next use of this
data: when an actual forked-XNU build attempt starts, this ADT is what
gets fed to `pexpert`'s device-tree code directly (iBoot normally hands
XNU a decompressed ADT blob at boot; our assumed exploit primitive's
job includes getting this exact blob to the kernel however boot chain
assembly ends up working -- item 5, still open).

## Artifact: `tools/adt.py`

Wrote a real dump/validate tool for this project (fresh code, not
copied from `reference/ios-sweet/tools/adt.py` -- same ADT format,
independent implementation, see README's reference/ note). Validated
against both real files:

```
$ py -3 tools/adt.py validate research/devicetree-xr/DeviceTree.bin
OK: ... -- 208156 bytes, parsed and re-serialized byte-exact
$ py -3 tools/adt.py validate research/devicetree-xr/DeviceTree.im4p
OK: ... -- 208156 bytes, parsed and re-serialized byte-exact
```

Byte-exact parse+reserialize round trip on both the raw and IM4P-wrapped
forms proves the parser is structurally correct (not just "didn't
crash") and that `DeviceTree.bin` is already the exact boot-usable blob
-- no fabrication, no transform, no patch needed, unlike `ios-sweet`'s
`mkadt.py` which has to synthesize a tree from scratch for foreign
silicon with no real Apple ADT of its own.
