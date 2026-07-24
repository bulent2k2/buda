# big2 bundle 25 — `blk_03` abutment-tap DNUTS open (2026-07, known issue)

## What this is

A diagnosed-but-unfixed design open in `flow/big_data_test/big2/big2.buda`.
Surfaced by the `viol_bundles` column added to `tools/qor_corpus.py`
(PR #432): the flow routes **overlap-free and fully placed** (`overlaps=0`,
`unplaced=0`) yet `check_design` reports

```
Bundle 25: Block 'blk_03': 56 bit(s) — no pass-through/busterm connection
Total: 56 violation(s) in 1 group(s) across 1 bundle(s).
```

i.e. an electrically broken route the `overlaps/unplaced` view reads as clean —
exactly the class the new column exists to catch.

## Not a generation gap, not a wrong selection

Bundle 25 is `DRV:blk_10 → REC:blk_01, blk_03, blk_11` (56-bit `bus_045`).
The two obvious hypotheses were **both ruled out**:

- **Not a generation gap.** All 26 candidates nominally cover `blk_03`
  (`check_topo` clean). The `blk_03` tap-face distribution:

  | face tapped | candidates | |
  |---|---|---|
  | y=2570 (bottom, **free**) | 9 | robust |
  | x=3500 (left, **free**) | 4 | robust |
  | x=4870 (right, **free**) | 2 | robust |
  | pass-through / unset | 6 | |
  | **y=2020 (top, ABUTMENT with blk_10)** | 5 | fragile — the selected class |

- **Not a simple wrong-selection.** The selected `TRUNK_V@x4145` is not
  inherently broken: perturbing the healer trajectory (a throwaway pin) makes
  ripup re-select the *same* candidate and it then routes `blk_03` **clean**.

## Root cause — DNUTS per-bit span-realization instability

`blk_03` (3500,2020)–(4870,2570) **abuts** `blk_10` (3500,1425)–(4870,2020) at
the shared edge **y=2020**, same x-range. The selected trunk is a **pass-through
of blk_10** at x=4145 whose bottom endpoint taps `blk_03` only at that abutment
line. The tap is a short sub-stub below the `seg1` junction at y=1790:

```
seg0 V (4145,1425)->(4145,2020) L7   # inside blk_10; bottom endpoint = blk_03 top face (y=2020)
seg1 H (2000,1790)->(4145,1790) L6   # taps blk_11; junction with seg0 at y=1790
```

At DetailedNUTS the 56 bits of `seg0` are placed with **`span_hi ∈ [1853,1914]`**
— they stop **~106–167 units short** of `blk_03`'s face at y=2020, so all 56
open. `check_topo` and `check_nuts` pass because the *abstract* segment reaches
2020; only the per-bit realization falls short. In a luckier placement
trajectory the same bits reach `span_hi ∈ [2233,2292]` (well into `blk_03`) and
the flow is clean — so the open is **placement-dependent**: DNUTS's per-bit span
adjustment does not reliably extend the last sub-stub (below the y=1790
junction) to the tapped face.

The abutment tap is what makes it fragile: there is zero margin — the wire must
land exactly on the shared blk_10/blk_03 boundary, and the pass-through host
(blk_10) offers no room past it.

## Reproduce

```bash
tools/qor_corpus.py --flows flow/big_data_test/big2/big2.buda    # -> 0/0/1
bin/buda --no-viz flow/big_data_test/big2/big2.buda              # check_design at the end
```

Inspect: the selected bundle-25 candidate is `TRUNK_V@x4145`; its `seg0` bits
land with `span_hi` short of y=2020 in the failing trajectory.

## Fix directions (deferred)

1. **DNUTS span-realization fix** (root cause, `detailed_nuts`): guarantee a
   busterm-tapped endpoint's per-bit span always reaches the tapped face, so the
   last sub-stub below a junction is never dropped. Correct but touches core
   bit placement — needs a full `qor_corpus` QoR/regression sweep.
2. **Generation gate on abutment taps**: demote/drop a candidate that taps a
   receiver **only** on an abutment edge (a face shared with a block the trunk
   passes through) when free-face alternatives exist — steering selection to the
   15+ robust candidates here, in the spirit of the removability / unanchored-
   BITRUNK gates. Does not help a bundle whose only coverage is an abutment tap.

Deferred by decision on 2026-07-24; filed so the `viol_bundles` finding is not
lost.
