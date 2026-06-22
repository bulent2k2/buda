# Topology Generation — `tc3a_flat` Findings (Analysis Iteration 1)

**Scope:** read-only analysis of the candidate topologies the generator produces for
`flow/big_data_test/tc3a_flat.buda`. No engine algorithm was changed. Companion to the
status reconciliation in
[topology_tree_gen_design.md §16](topology_tree_gen_design.md#16-implementation-status).

**How to reproduce:**

```bash
./buda --no-viz flow/big_data_test/tc3a_flat_dump.buda    # per-bundle candidate tables
./buda --no-viz flow/big_data_test/tc3a_flat.buda         # full route (planner→NUTS→dNUTS)
```

`tc3a_flat_dump.buda` mirrors `tc3a_flat.buda` up to `generate_topologies`, then runs the
new `dump_topologies --problems` command.

---

## 1. The design at a glance

`tc3a_flat` is the flat flow: ~40 leaf blocks + IO pads, 80 bundles (buses), generic
`.p`-style pins, no multi-rect blocks or keepouts. Each bundle is one driver fanning out
to 1–5 receivers.

| Metric (`dump_topologies` summary) | Value |
|---|---|
| Bundles | 80 |
| Total candidates | **2780** |
| Candidates per bundle | avg **34.8**, median 36.5, min 7, max 55 |
| Bundles with geometric duplicates | 11 / 80 |
| Bundles with a pinched (`min_slide=0`) candidate | 0 / 80 |
| Single-candidate bundles | 0 / 80 |
| Bundles whose candidates pass a trunk through a block | **70 / 80** |

### Shape histogram (candidate count by shape family)

```
TRUNK_H        528    TRUNK_H+MST    516    TRUNK_V        485    TRUNK_V+MST    453
TRUNK_H_OOB    140    TRUNK_V_OOB    139    TRUNK_H_OOB+MST 138   TRUNK_V_OOB+MST 137
MST_HV          56    MST_VH          56    BITRUNK_H       56
U_VHV           20    U_HVH           20    L_VH             9    Z_VHV            8
L_HV             7    I_V              6    Z_HVH            4    I_H              2
```

**Straight-trunk variants (`TRUNK_*`) account for 2674 / 2780 = ~96 % of all
candidates.** The genuinely distinct topologies a designer cares about — L, Z, U, I,
and the MST/branching family — are **112 candidates total, ~4 %.**

---

## 2. Problem patterns

### P1 — Trunk-sweep explosion (the dominant noise source)

For every bundle the generator sweeps a *straight* trunk across **every Hanan line** in
each direction, and emits each one again as a `+MST` twin and again as an `OOB` variant.
The result is dozens of nearly-identical candidates that differ only by the trunk's
perpendicular coordinate.

**Concrete example — bundle 1** (`blk_00` → `blk_06, blk_33, blk_17, blk_05, blk_21`,
28 nets, 55 candidates):

```
 idx type                 wl segs pass  mslide
   0 MST_HV             6510    6    0     900   ← best WL, branches to every leaf
   1 MST_VH             6510    6    0     900
   2 TRUNK_H@y8425     11580    5    2     900   ← +78 % WL, passes through 2 blocks
   3 TRUNK_H@y9125     11615    6    1      50
   ...                                            (51 more straight-trunk rows)
  54 TRUNK_V_OOB+MST@x11761 38725 12   0     700   ← 6× the WL of the MST
```

53 of bundle 1's 55 candidates are straight-trunk sweeps; only candidates 0–1 (the MST
shapes) reach all five leaves with a real branch. Every bundle has this profile.

**Why it matters:** the planner (Stage 3) must score all 35 candidates/bundle on average;
~96 % carry no spatial information the others don't, so this is wasted planner work and a
haystack the few good shapes hide in.

### P2 — `+MST` twinning is near-pure redundancy

`TRUNK_H` (528) vs `TRUNK_H+MST` (516) and `TRUNK_V` (485) vs `TRUNK_V+MST` (453) appear
in an almost 1:1 ratio. When a straight trunk already reaches every leaf, bolting an MST
onto it only *adds* segments and wirelength (see bundle 1: idx 5 `TRUNK_H@y7440` wl 11950
vs idx 20 `TRUNK_H+MST@y7440` wl 18460 — same trunk position, +54 % WL). The twin is never
selected and never preferred; it is dead candidate volume.

### P3 — Trunks pass straight through blocks instead of branching (70/80 bundles)

`pass_through_count > 0` means the trunk crosses an intervening block's bbox without
generating a stub to it. This is the **structural** case where a *richer* trunk shape is
the right answer: as the trunk passes a leaf it should drop a **Z-stub** (or sub-trunk)
into that block, rather than either (a) flying over it as a straight wire or (b) being
re-swept to a different Hanan line. The current generator does neither — it only offers
straight trunks (P1) and a global MST (which over-corrects by abandoning trunk reuse).

### P4 — Geometric duplicates (11/80 bundles)

11 bundles contain candidates whose **segment geometry is byte-identical** to another
candidate but carry a different type string (e.g. a `TRUNK_H@y…` and its `TRUNK_H+MST@y…`
twin that collapse to the same wires because the MST added nothing). `dump_topologies
--problems` flags these as `DUP`. This is exactly the `deduplicate` post-processing step
specified in design §4 Phase 5 but never implemented.

### P5 — Wirelength-only ranking buries flexible candidates

`annotate_and_sort` orders by raw `estimated_wirelength`, ignoring slide freedom. In
bundle 1, `TRUNK_H@y9125` (`min_slide=50`) sorts *above* `TRUNK_H@y10010`
(`min_slide=1100`) purely because it is 2285 units shorter, even though the latter gives
the layer optimiser 22× more room. This is the §10 flexibility-score gap, observable
directly in the dump's `mslide` column. (Lower priority this cycle, but it compounds P1:
low-quality trunks float to the top of the haystack.)

---

## 3. Downstream correlation (full route)

Running the complete `tc3a_flat.buda`:

- Connectivity **passes** at NUTS and detailed-NUTS levels (`no opens found`).
- Detailed NUTS: **11316 net segments placed, 0 bits unplaced.**
- Abstract NUTS, however, leaves **8 track overlaps** the planner did *not* predict
  (every bundle was committed with `overflow=0`):
  - M6: 6 overlaps — `B59×B74, B43×B74, B13×B72, B22×B64, B28×B61, B59×B79`
  - M7: 2 overlaps — `B31×B56, B1×B56`

Several of the colliding bundles were committed as **straight trunks** (e.g. B22 →
`TRUNK_H@y7165`, B13/B59 trunks) sharing the same congested top-layer band. The planner
had no *spatially distinct* alternative to relieve the contention: the 35 candidates it
saw were almost all the same straight trunk at slightly different coordinates (P1), not
genuinely different routes. A branching/multi-level trunk (richer shapes) or a deduped,
position-snapped candidate set would give the planner real choices here.

> Note: the larger `tc3a_flat_x10.buda` (10× nets/bundle) is where the heavy
> "unplaced bits" pressure shows; the x1 design already exhibits the same candidate-set
> pathology that causes it, in a form small enough to inspect bundle-by-bundle.

---

## 4. How the prioritized backlog addresses these

| Finding | Fix (iteration 2) |
|---|---|
| P1 trunk-sweep explosion | **Dedup + noise reduction:** snap trunk positions to a meaningful subset of Hanan lines (block-edge-aligned), not every grid line. |
| P2 `+MST` twins | **Dedup:** suppress the `+MST` variant when the base trunk already covers all leaves (MST adds zero segments). |
| P3 pass-through trunks | **Richer trunk shapes:** per-leaf Z-stub / sub-trunk so the trunk *branches into* each block it passes (§4.5 / §6), and general multi-level trunks (§4.4). |
| P4 geometric duplicates | **Dedup:** implement design §4 Phase 5 `deduplicate` (collapse identical `(x1,y1,x2,y2)` segment-sets); `dump_topologies --problems` already detects them. |
| P5 WL-only ranking | **Flexibility score (later):** adjusted-WL sort using the `min_slide` the dump already prints (§10). |

**Net effect targeted:** replace ~35 mostly-redundant candidates/bundle with a smaller set
of genuinely distinct, branch-aware topologies — fewer candidates, higher quality, and
real spatial alternatives for the congestion planner.

---

## 5. Tooling added this iteration

- `dump_topologies [hint] [--problems]` (`src/buda_cli.py`) — per-bundle candidate table
  + aggregate summary; `--problems` filters to flagged bundles. Read-only.
- `flow/big_data_test/tc3a_flat_dump.buda` — scratch flow that drives the dump.

Re-run the dump after each backlog item to measure candidate-count, duplicate-rate, and
shape-mix improvements.
