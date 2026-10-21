# SEP mismatch: narrowed, not solved

## No public-XNU precedent to borrow

Checked whether Apple's own public XNU tree has a sanctioned "no/stale
SEP" fallback path we could reuse instead of inventing one (`ios-sweet`
had to write their own corecrypto/bearssl + AMFI-bypass patches from
scratch for Snapdragon, which has zero SEP hardware). Candidate: `VMAPPLE`
(Apple Silicon VM target) also lacks real SEP hardware and *is*
officially supported -- if XNU source had a VMAPPLE-gated SEP-bypass
path, that'd be a legitimate, Apple-authored template.

Grepped current XNU (`reference/xnu-11215/`) for any `VMAPPLE`-gated
code near corecrypto/SEP/AMFI: nothing. Reason: AMFI
(`AppleMobileFileIntegrity`), `AppleSEPManager`, and `AppleKeyStore` are
all closed-source kexts, entirely outside the public XNU tree -- so a
"VMAPPLE doesn't have real SEP" fallback, if one exists, lives in code
we can't read, not in anything `apple-oss-distributions` ships. No
shortcut here; confirms `README.md`'s original posture (independently
written bypass, ios-sweet's patches as structural reference only) was
already the right call, not something to second-guess further.

## But: the AP-side SEP driver stack isn't XR-legacy code

Checked the kext-diff data already on disk (`kextlist-xr-ios18.txt` /
`kextlist-i11-ios26.0.txt`, no new downloads needed) for every
SEP/AMFI/trust-related kext:

| Kext | XR (iOS 18.7.10) | iPhone 11 (iOS 26.0) |
|---|---|---|
| `com.apple.driver.AppleMobileFileIntegrity` | present | present |
| `com.apple.driver.AppleSEPManager` | present | present |
| `com.apple.security.AppleImage4` | present | present |
| `com.apple.driver.AppleEffaceableStorage` | present | present |

All four are in the shared-197 set from `kext-diff.md` -- meaning
iOS 26.0's own supported devices run the *exact same* AP-side
SEP-communication driver binaries XR would need, not some
legacy/XR-only variant. There is no kext-availability problem for the
SEP driver stack, same conclusion item 3 already reached for the other
194 shared kexts.

## What's actually unresolved (can't be resolved by more reading)

The real open question is a **runtime pairing/protocol-version
question**, not a code-availability one: whether the AppleSEPManager
driver from an iOS 26.0 build can complete its handshake against XR's
physically-flashed iOS-18-vintage sepOS firmware. SEP firmware updates
ship signed and versioned together with their paired AP build inside
Apple's install bundles; whether the pairing/attestation protocol
itself is version-tolerant (old SEP firmware, new AP driver) isn't
something visible from source on either side -- AMFI/AppleSEPManager
are closed, and SEP's own firmware is a separate signed blob we aren't
analyzing here.

Three possible outcomes, in order of how much work each implies:
1. Pairing succeeds as-is (old SEP firmware, new driver) -- no work
   needed. Optimistic, unverified.
2. Pairing fails but SEP firmware can be *updated* independently via
   the normal "Complete" firmware update path once demoted/unsigned
   boot works -- moderate work, mechanism likely already exists
   (Apple ships SEP firmware updates somewhat independently of full AP
   OS updates already).
3. Pairing fails and SEP firmware can't be updated without full-chain
   signature -- forces `ios-sweet`'s posture after all (treat as no
   working SEP, independently-written AMFI/trustcache/corecrypto
   bypass, same category of work regardless of real hardware
   underneath).

Can't rank these without either an actual boot attempt or non-public
SEP firmware/pairing documentation. Flagging as the honest state
rather than picking one to assume.

## Mitigations implemented

- **Patch 0002** (`xnu/patches/0002-t8020-sep-fallback.patch`): graceful
  trust cache degradation. If Image4/AMFI interfaces fail to register,
  trust_cache_rt stays NULL, all trust cache ops return KERN_NOT_SUPPORTED,
  and root-fs binaries get CS_PLATFORM_BINARY directly in ubc_cs_blob_add.
  Covers outcome 3 at the kernel level.

- **Backup: `mineek/seprmvr64`** (https://github.com/mineek/seprmvr64):
  SEP removal patches for 64-bit devices. If the SEP-less fallback path
  in patch 0002 isn't sufficient (e.g. SEP communication hangs rather
  than failing cleanly), this tool removes SEP dependency entirely.
  Set aside for now -- try SEP-less path first.
