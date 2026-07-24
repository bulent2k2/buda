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
  (`check_topo` clean). `blk_03` (x1=3500, y1=2020, x2=4870, y2=2570; the repo
  convention is **y1=bottom, y2=top**) is boxed in on all four faces, so no face
  is geometrically "free" — what matters is whether the block adjoining the
  tapped face *hosts the trunk*. The `blk_03` tap-face distribution:

  | face tapped | adjoining block | candidates | |
  |---|---|---|---|
  | y=2570 (top) | blk_09 | 9 | robust |
  | x=3500 (left) | blk_18 | 4 | robust |
  | x=4870 (right) | blk_22 / blk_29 | 2 | robust |
  | pass-through / unset | — | 6 | |
  | **y=2020 (BOTTOM, abuts blk_10 — the trunk host)** | blk_10 | 5 | fragile — the selected class |

  The fragile class is not "the abutment face" per se (every face abuts a
  block); it is the face shared with **blk_10, the block the trunk passes
  through**, so the tap has no room past the shared boundary.

- **Not a simple wrong-selection.** The selected `TRUNK_V@x4145` is not
  inherently broken: perturbing the healer trajectory (a throwaway pin) makes
  ripup re-select the *same* candidate and it then routes `blk_03` **clean**.
  (The open is also healer-trajectory-dependent: the no-healer `tools/render.py`
  pipeline places this candidate's bits reaching the face, so it does **not**
  reproduce the open — only the full flow's healer trajectory does.)

## Root cause — DNUTS per-bit span-realization instability

`blk_03` (3500,2020)–(4870,2570) sits directly **above** `blk_10`
(3500,1425)–(4870,2020), abutting at the shared edge **y=2020** (blk_10's top
`y2` = blk_03's bottom `y1`), same x-range. The selected trunk is a
**pass-through of blk_10** at x=4145 whose top endpoint taps `blk_03` only at
that abutment line (blk_03's **bottom** face). The tap is a short sub-stub
**above** the `seg1` junction at y=1790:

```
seg0 V (4145,1425)->(4145,2020) L7   # inside blk_10; top endpoint = blk_03 bottom face (y=2020)
seg1 H (2000,1790)->(4145,1790) L6   # taps blk_11; junction with seg0 at y=1790
```

At DetailedNUTS the 56 bits of `seg0` are placed with **`span_hi ∈ [1853,1914]`**
— they stop **~106–167 units short** of `blk_03`'s bottom face at y=2020, so all
56 open. `check_topo` and `check_nuts` pass because the *abstract* segment
reaches 2020; only the per-bit realization falls short. In a luckier placement
trajectory the same bits reach `span_hi ∈ [2233,2292]` (well into `blk_03`) and
the flow is clean — so the open is **placement-dependent**: DNUTS's per-bit span
adjustment does not reliably extend the last sub-stub (above the y=1790 junction,
up to y=2020) to the tapped face.

The tap onto blk_10's shared boundary is what makes it fragile: there is zero
margin — the wire must land exactly on the shared blk_10/blk_03 edge, and the
pass-through host (blk_10) offers no room short of it.

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
2. **Generation gate on trunk-host taps**: demote/drop a candidate that taps a
   receiver **only** on the face it shares with a block the trunk **passes
   through** (blk_10 here) when alternatives tapping a face adjoining a non-host
   block exist — steering selection to the 15 robust candidates here (top/left/
   right faces), in the spirit of the removability / unanchored-BITRUNK gates.
   The predicate is "adjoining block hosts the trunk", NOT "abutment face" (every
   face abuts a block) — see the reclassification above. Does not help a bundle
   whose only coverage is a trunk-host-shared tap.

Deferred by decision on 2026-07-24; filed so the `viol_bundles` finding is not
lost.
