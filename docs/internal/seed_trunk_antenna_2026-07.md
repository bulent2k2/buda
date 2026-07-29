# The seed-trunk ANTENNA family (issue #485, 2026-07)

## What this is

`#483` added the `ANTENNA` check (a segment attached to the route at fewer
than two **distinct** points) and fixed the collinear MST-edge leg that
produced the reported one. A census over the generation corpus then found
**26 antenna segments still generated** — none of them in a *selected*
topology, but all of them candidates the planner may pick under different
congestion. Issue #485 asked why, and whether the generator's existing
"removable seed trunk" pass was the right home for the fix.

It was. **All 26 were `seg 0` — the trunk+MST hybrid's seed trunk** — i.e.
exactly the shape `seed_trunk_is_redundant` (src/topology.cpp) exists to
drop, in candidates that pass its test today. There turned out to be two
independent defects in that pass, one per family.

Census method: per flow / bundle / candidate, build a `ConnTopology`,
synthesize a nominal `NUTSResult` (each segment at its `perp_pos`), call
`check_nuts`, count `ViolationKind::ANTENNA`. The **authoritative detector**,
never a proxy — re-deriving the predicate by hand is what produced the wrong
first census on #485 and is now impossible (see "One predicate" below).

| family | n | why the gate kept it |
|---|---:|---|
| `TRUNK_*_OOB+MST` | 20 | defect 1 — the gate judged the wrong topology |
| `TRUNK_*+MST` (in-bbox) | 6 | defect 2 — correctly "not removable", but antenna-flagged as it stands |

## Defect 1 — the gate judged a topology nobody would route (20 cases)

For OOB trunks the gate requires the spine ITSELF to dangle before dropping
(a genuine OOB detour is a real bridge, and dropping those regressed
`slowdown_rnr` 0/0 → 2/8 historically). To evaluate that it built a copy:

```cpp
Topology oc = topo;
annotate_topology(oc, fp);        // ← the bug
ConnTopology oct;  oct.build(oc, fp);
```

The intent was only to get SEG junctions inferred (the caller's `topo` has
been through `annotate_endpoints` + `complete_relay_junctions` but not the
seg_conns derivation, so every seg would otherwise read 0 conns). But
`annotate_topology` ALSO re-derives `seg_busterms` **geometrically**, putting
back the face landings `complete_relay_junctions` deliberately demoted to
`nullopt` under the single-tap model. So the gate inspected a topology that
is not the one being pooled.

Measured, per candidate, pooled view vs re-annotated view:

```
big.buda b63 cand30 TRUNK_V_OOB+MST@x9788 seg0
    pooled  : ['SEG1@7490']                        gate=dangling
    re-annot: ['BT@5140:blk_03', 'SEG1@7490']      gate=NOT dangling
```

That phantom `BT` appeared in **all 20** OOB cases — 20/20, no exceptions.
Fix: derive only what `ConnTopology` needs (`annotate_seg_conns`), leaving
the candidate's own annotation alone.

## Defect 2 — right answer, wrong question (6 cases)

The in-bbox six are all one shape: two **collinear** stubs meeting at a
point, with the spine hanging off that same point into open space.

```
demo/comprehensive_demo.buda b5 cand17  TRUNK_H+MST@y285
  seg0 (550,285)-(750,285)   H   conns: SEG1@550, SEG2@550   ← both at ONE x
  seg1 (550,150)-(550,285)   V at x=550
  seg2 (550,875)-(550,285)   V at x=550
```

Here the removability test is **correct** to keep the spine: `ConnTopology`
infers a SEG link only for *perpendicular* pairs, so removing seg0 really
does split seg1 from seg2 (`DISCONNECTED`), and a spine-less version would
leave two parallel stubs with no junction constraint — the issue-#84 silent
open that `add_trunk`'s degenerate-spine branch already guards against.

But that is a fact about the **trunk-less** topology, which is never emitted.
The question the gate should ask is whether to offer the planner *this*
candidate, and this candidate carries an antenna: 200 units of metal from
x=550 to x=750 connected to nothing. So it is dropped on that ground —
`seed_trunk_is_antenna`, deliberately independent of the removability test,
which is why the two drop reasons are counted and logged separately:

```
[TopoGen] dropped 7 redundant trunk+MST hybrid(s) (1 removable seed trunk, 6 antenna seed trunk; first: TRUNK_H+MST@y1335).
```

Nothing is stranded: the plain trunk / L / Z / clean-hybrid candidates still
cover the bundle, and `filter_uncovered` runs afterwards — the same contract
the pre-existing removability drop relies on.

## One predicate, one implementation

The gate's own dangling test was a hand-rolled conn-**record** count
(`sc.size() <= 1 && sc[0].block_name.empty()`) — the same defect the Codex
review found in the checker on #483, in a third copy nobody had connected to
the other two. Rather than fix a third copy, `verify.h` now exposes

```cpp
struct SegAttachment { std::set<int> positions; std::set<std::string> through; … };
SegAttachment seg_attachment(const ConnSeg&, const Topology&, const Floorplan&);
```

and both `detect_antennas` and the generator gate call it. `count() < 2` IS
the antenna condition, in one place. (The test helper in
`test_antenna_check.py` had drifted the same way and now asks `check_nuts`.)

## Results

**Antenna census: 26 → 0**, corpus-wide.

**Goldens** — three flows moved, and every changed bundle is an antenna
removal, nothing else:

| flow | changed bundles | matches the census? |
|---|---|---|
| `demo/comprehensive_demo.buda` | 4, 5 (225 → 222 candidates) | yes, exactly |
| `flow/big_data_test/big.buda` | 13 bundles | yes, exactly |
| `flow/rnr/mix.buda` | 6, 14, 18, 71, 80 | yes, exactly |

**QoR** (`tools/qor_corpus.py`, 29 flows): **1 better, 1 worse, 27
unchanged**; total runtime −13% (a smaller candidate pool to plan over).

| flow | base | branch | |
|---|---|---|---|
| `rnr/mix2_fast_on_aligned_sql` | 2/30/2 | **0**/30/2 | BETTER — this is #483's regression healing |
| `rnr/mix` | 0/0/0 | **1/0/1** | WORSE — see below |

An A/B split of the two halves places the regression squarely on defect 1's
fix: defect 1 alone is 0 better / 1 worse (mix), and defect 2 adds the mix2
improvement at no further cost. Defect 2 is a pure win.

## The `mix` regression is a pre-existing gap this exposes

Not a lost candidate: bundle 90's pool is **identical** in both builds (39
candidates; the good one, `TRUNK_H+MST@y765` at index 6, is present in
both). Other bundles' pools shrank, congestion moved, and the planner
switched bundle 90 from index 6 to index 27, a `BITRUNK_H` — a pure
selection-trajectory effect.

What makes index 27 a bad pick is a separate, pre-existing defect:

```
u4 bbox = (1680,1530)-(1730,1580)
seg1 nominal (1035,1555)-(1875,1555)   → covers u4 by pass-through
seg1 per-bit spans after DNUTS: [1085 .. 1479]      ← trimmed
```

Block `chip/i_dnuts2_2/u4` is covered ONLY by seg1 crossing it at x∈
[1680,1730]. seg1's junctions are at x=1095 and x=1455, so DetailedNUTS's
per-bit span adjustment shrinks the bits to the junction extent and the
graze that carried the coverage is gone — `BUSTERM_OPEN`, 10 bits. (Bit
width is NOT the problem: all 10 bits land on tracks 1540.5–1566.5, inside
u4's 50-unit face. Only the along-extent is lost.)

So: **per-bit span adjustment can shrink a segment past a block it was
covering by pass-through.** Nominal and NUTS-level coverage both pass; only
the per-bit stage loses it, which is why no generation gate catches it. That
is its own issue, filed as a follow-up — a close cousin of the antenna
family (dead metal / lost coverage at a segment's ends) but in
`detailed_nuts`, not the generator.

## Files

- `src/verify.h` / `src/verify.cpp` — `SegAttachment` + `seg_attachment()`,
  `detect_antennas` reduced to a caller of it.
- `src/topology.cpp` — `find_spine_index()` factored out;
  `seed_trunk_is_antenna()` added; `seed_trunk_is_redundant()`'s OOB gate
  switched to `annotate_seg_conns` and to the shared predicate; both drop
  sites and the report line count the two reasons separately.
- `test/tests/test_antenna_check.py` — section 3; the helper de-drifted.
- `test/tests/data/topo_golden/{demo_comprehensive_demo,big_data_test_big,rnr_mix}.txt`
  — re-baselined per `tools/regen_goldens.py --write`.
