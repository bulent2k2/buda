# bigHalf `bus_005` dangling-segment scan (2026-07)

## What this is

A focused repro + audit of the DANGLING candidate geometry on `bus_005`
(bundle 67 in `flow/big_data_test/bigHalf.buda` — the 48-bit `blk_34 →
[blk_19, io_pad_br]` corner-IO bus that has been a healer hot spot, cf.
issue #399 and `relay_jog_phantom_span_2026-07.md`).

- **Repro flow:** `flow/big_data_test/bigHalf_bus005_dangling.buda` — the
  bigHalf setup, but `generate_topologies_for_bundle bus_005` (src/dst
  auto-derived) instead of the whole-design `generate_topologies`, so the
  candidate pool is just this one bus. Ends with `dump_topologies bus_005
  --conn` + `check_design topo all`.
- **Scanner:** `tools/scan_dangling.py <flow.buda> <hint>` — runs the flow,
  then walks every candidate of the matching bundle, builds its
  `ConnTopology`, and categorizes each dangling segment. Reusable on any
  flow/bundle.

## Two kinds of "dangling"

Mirrors `set_drop_dangling`'s predicates (`src/buda_session/edit.py`
`_topo_dangling_reason` / `_topo_truly_dangling_reason`):

- **A — truly-dangling stub:** a `ConnSeg` with a *single* connection that
  is *not* a block tap — a wire whose other end connects to nothing.
  DetailedNUTS silently shrinks it when it re-derives bits, which is why the
  NUTS-level viz never flagged it.
- **B — unbounded slide window:** the `±2³⁰` no-clamp sentinel on
  `perp_lo/hi` — an OOB-detour trunk whose slide window was never bounded.
  `set_drop_dangling clamp` bounds these to the design extent.

## Finding (36 candidates on this fixture, 2026-07-23)

| Category | Candidates | Types |
|---|---|---|
| A — truly-dangling stub | 3 | `TRUNK_H+MST@y2770`, `@y3095`, `@y2410` — **all `TRUNK_*+MST`** |
| B — unbounded window | 5 | `TRUNK_H_OOB+MST@y-232`, `TRUNK_V_OOB+MST@x-569`, `TRUNK_H+MST@y2410` (MST) **and** `TRUNK_V_OOB@x-569`, `TRUNK_H_OOB@y-232` (pure OOB) |

**The hypothesis holds for genuine dangling stubs:** every Category-A stub is
a `TRUNK_*+MST` hybrid — the MST-relay artifact (`complete_relay_junctions`
leaves a relay stub whose far end is only a junction, so the extend-only
span guarantee bakes a phantom tail). None are pure trunks.

**Unbounded windows (Category B) are a separate class.** They ride the
OOB-detour geometry and appear on *pure* OOB trunks (`TRUNK_V_OOB@x-569`,
`TRUNK_H_OOB@y-232`) as well as MST hybrids. Conflating the two would
misattribute the OOB-window issue to MST.

Note candidate 35 (`TRUNK_H+MST@y2410`, 11 segments) exhibits **both**: a
truly-dangling seg0 and six unbounded-window segs — the pathological
high-segment MST hybrid.

## Why nothing caught it before

`check_design topo all` on this pool reports **"no violations found."** A
dangling stub is not a `DISCONNECTED`/`FEEDTHRU_RELAY`/`BUSTERM_OPEN`
violation — the block is still covered and the graph is still one island —
and abstract NUTS renders the stub within the design extent, so neither the
audit nor the NUTS viz flags it. Only DetailedNUTS's re-derivation shrinks
the truly-dangling stub (masking it), and only the opt-in `set_drop_dangling`
(drop / clamp / clamp_drop) acts on either category. `scan_dangling.py` is
the standalone lens.

## Root cause of the Category-A stubs (non-OOB `TRUNK_*+MST`)

Ignoring the OOB detour class (Category B on `TRUNK_*_OOB`), the focus is the
pure `TRUNK_H+MST` hybrids. Of the 12 non-OOB MST candidates only 3 dangle
(idx 32 `@y2770`, 34 `@y3095`, 35 `@y2410`); the other 9 are clean. The
discriminator is **where the trunk locus lands**.

`bus_005` endpoints: `blk_34` (100,1700)-(700,2050) [src], `blk_19`
(100,2770)-(1050,3420), `io_pad_br` (6290,100)-(6790,500) [far-right corner].

**Clean — idx 18 `TRUNK_H+MST@y1700`:**
```
seg0 H (700,1700)->(1050,1700)  conns=[blk:blk_34, seg1, seg2]   # taps blk_34
```
The locus y=1700 *is* blk_34's bottom face, so the spine taps the source —
short, anchored, not dangling.

**Dangling — idx 32 `TRUNK_H+MST@y2770`:**
```
seg0 H (700,2770)->(6290,2770)  conns=[seg1]           <<DANGLING>>
seg1 V (700,500)->(700,2770)    conns=[seg0, seg2, blk:blk_19]
seg2 H (700,500)->(6290,500)    conns=[seg1, blk:io_pad_br]
```

The three dangling loci (y2410/2770/3095) sit in the blk_19 band, far from any
block the horizontal spine can tap along its x-run. Sequence:

1. `add_trunk_mst_candidates` emits a full-width `TRUNK_H` spine (seg0,
   x700→6290, reaching toward io_pad_br's column at x6290).
2. The MST/relay completion connects io_pad_br more cheaply via a **side
   L-path** — seg1 (V down x700→y500) + seg2 (H across at y500) — with blk_19
   on seg1 and blk_34 grazed by seg1's right face.
3. seg0 is now **fully redundant**: drop it and src/blk_19/io_pad_br are still
   one connected tree. Its reach to (6290,2770) lands on nothing → a dangling
   stub. `complete_relay_junctions` never prunes the vestigial trunk.

So MST does not *add* a dangling wire — the MST edges make the nominal trunk
spine **vestigial**, and the completion pass leaves the orphaned spine instead
of collapsing the candidate to the L/Z it effectively became. The clean MST
candidates escape only because their trunk locus coincides with a block face.

Candidate 35 (`@y2410`, 11 segments) is the pathological case: the vestigial
spine (seg0) plus a tangle of unbounded relay jogs (seg3/5/6/7/8/9).

## Fix (shipped) — drop the candidate when the seed trunk is REDUNDANT

Implemented in `add_trunk_mst_candidates` (`topology.cpp`). Originally scoped to
**non-OOB** trunk+MST hybrids (PR #418); **extended to OOB hybrids** in a
follow-up (below). After `complete_relay_junctions`,
`seed_trunk_is_redundant()`:

1. finds the spine — the longest segment matching the trunk orientation at
   `trunk_pos`;
2. builds the **trunk-less** topology (drop the spine), re-derives its
   connectivity from scratch (`annotate_topology`, like a hand-built candidate);
3. returns true (redundant → drop) iff removing the spine leaves **a valid
   route** — no uncovered block (`BUSTERM_OPEN`), no split wire graph (unbridged
   `DISCONNECTED`), no newly-exposed feedthrough relay (`FEEDTHRU_RELAY`).

**Removability is the exact test** — it directly asks "does the tree still work
without this trunk?" and so gets right the cases the earlier heuristics got
wrong:

- A spine that is a block's **only** coverage or connection is *not* removable
  and is **kept** — this is Codex P2 (#418) stated correctly, and it holds even
  for `<4`-block bundles where the hybrid is the only MST-type candidate
  (`test_load_bearing_passthrough_trunk_is_kept`).
- A spine the MST edges make **superfluous** *is* removable and is dropped —
  including bus_005 `TRUNK_H+MST@y3095` (it grazes/covers `blk_19` but the rest
  of the tree already reaches everything) and the "covers a block yet still
  removable" shape (a spine crossing `M` whose branches reach `M` another way).

Two heuristics were tried and rejected first: a **single-point-attachment** test
kept `@y3095` (a covered-but-redundant spine), and its **any-overshoot**
generalization was a QoR regression — dropping partial-overshoot trunks the
planner actually uses roughly **doubled** `mix`'s DNUTS opens (156→308).
Removability keeps those load-bearing/DNUTS-healed trunks while still dropping
the genuinely-redundant ones.

Both emit paths gate on it (completed-tree + legacy), and a redundant legacy
hybrid is not pooled as a `<4`-block fallback. The bundle is never stranded —
the plain trunk / L / Z / a clean hybrid still cover it, and the coverage gate
runs after.

**Why drop, not trim.** A redundant spine carries no wire the tree needs.
Dropping is exact; the earlier post-hoc trim (reverted) had to rebuild geometry
and fought the completion's `edge_id`/junction/WL invariants (11 test
regressions vs. 0 here).

**Impact (QoR unchanged).** NUTS placement goldens are byte-identical and the
flow-script / big2 / mix / ripup QoR tests pass — the dropped candidates were
*never selected* by the planner (a redundant trunk is only extra wire, so it
ranks worse than its trunk-less equivalent, which the generator emits anyway).
Only the candidate-pool snapshots move: the `topo_analysis` goldens shed their
redundant `+MST` candidates (big / comprehensive / dogleg2 / four_blocks / mix).
bus_005: non-OOB dangling stubs **3 → 0**.

## Extension — OOB hybrids, with a dangling-spine discriminator (2026-07-24)

The user's `flow/mst_bad.buda` repro pinned `bus_033` candidate 29
(`TRUNK_V_OOB+MST@x5880`) — a vestigial **OOB** trunk that runs out of the die
and hangs off the tree at one junction — which the non-OOB scope above missed.
Un-scoping removability to OOB catches it, but a naive "drop every redundant OOB
hybrid" is **too aggressive**: a genuine OOB detour trunk is a real bridge whose
spine attaches at *both* ends — connectivity-redundant (the MST provides an
alternate path) yet **congestion-load-bearing**. Dropping that class regresses
the previously-clean `flow/rnr/slowdown_rnr.buda` from 0/0 to 2/8, because its
`ripup_reroute`/`negotiate_congestion` healer *promotes* those OOB+MST candidates
to escape congestion the planner's band model under-predicts.

The discriminator (`seed_trunk_is_redundant`'s `require_dangling_spine`, on for
OOB only): after confirming the spine is connectivity-redundant, additionally
require the **spine itself to dangle** — build the annotated `ConnTopology` and
check the spine's `ConnSeg` has ≤1 connection and no block face (the
`set_drop_dangling` truly-dangling predicate). A vestigial OOB trunk (cand 29)
has a dangling spine and is dropped; a real OOB detour bridge does not and is
kept. (The `topo` handed to the gate has only `annotate_endpoints`, not the full
`annotate_topology` — so the check annotates a copy first, else every seg reads
zero conns and the discriminator silently no-ops.)

**QoR of the OOB extension** (corpus, vs baseline; the user accepted this
trade-off — a *net-positive* five-better / one-worse):

| flow | baseline | with drop |
|---|---|---|
| rnr/mix | 2/44 | **0/0** |
| rnr/mix2_fast_bottomup | 19/210 | **17/196** |
| rnr/mix2_fast (×2 variants) | 33/256 | 34/241 |
| big/tc3a (already hopeless) | 817/8360 | 764/8342 |
| **rnr/slowdown_rnr** | **0/0** | **2/8** |

The dual-use nature is fundamental: no generation-time geometry separates cand 29
from slowdown_rnr's copies — the same class the planner auto-selects *badly* in
congested flows (dropping helps) is what a healer promotes to *escape* congestion
(dropping hurts). A future "deprioritize, don't drop" candidate-tier could keep
both; it was scoped out here.

## Reproduce

```bash
python3 tools/scan_dangling.py \
    flow/big_data_test/bigHalf_bus005_dangling.buda bus_005
# or run the flow directly:
bin/buda --no-viz flow/big_data_test/bigHalf_bus005_dangling.buda
```
