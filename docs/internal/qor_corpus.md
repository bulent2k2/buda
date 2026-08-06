# QoR corpus — the standing routing benchmark

The reference set for any topology / planner / NUTS change that claims a
corpus-wide result, and what the
[`set_drop_dangling` modes sweep](drop_dangling_modes_2026-07.md) and the
[healer-effectiveness sweep](healer_effectiveness_2026-07.md) measure over.

## Where the list lives

**`CORPUS` in [`tools/qor_corpus.py`](../../tools/qor_corpus.py)** — a curated
list, and the only definition. It is no longer derivable by a `grep`: this
document used to carry

```bash
# HISTORICAL — do not use.  Reproduces neither the membership nor the count.
for d in flow/big_data_test flow/big_data_test/big2 flow/hbundles flow/rnr; do \
  grep -rl run_detailed_nuts $d/*.buda; done | sort -u
```

which was accurate while membership *was* "every full-pipeline flow in four
directories". It has not been since: the corpus gained `flow/chip/` vehicles,
per-cell layer-policy vehicles and `_2x` track-density twins, while several
flows those directories contain were deliberately left out. The recipe now
over-reports in one direction and under-reports in the other, so it is recorded
here only to stop someone re-deriving the list from it. **96 full-pipeline flows
sit outside the corpus** — membership is a judgement, not a filter.

(The recipe was also wrong in a subtler way: `grep -l run_detailed_nuts` counts
a flow that never reaches the command. `rnr/mix2_repro.buda` `exit`s five lines
above its `run_detailed_nuts` and is not a full-pipeline flow at all.)

Each row carries a comment saying what it defends. That is the standard for a
new one: a corpus row costs runtime on every sweep, so it should be there to
catch something no other row would.

## The numbers live in `qor/qor_table.md`

**[`qor/qor_table.md`](../../qor/qor_table.md) is the authoritative snapshot** —
per-flow bundle / segment counts, abstract and detailed wirelength, the
`(overlaps, unplaced, viol_bundles)` triple, and runtime. It is regenerated
nightly and opens a PR when the semantic columns move.

This document deliberately **does not** reproduce that table. It used to carry a
34-row copy, which drifted: the corpus grew past 34 while the table stayed. A
hand-kept duplicate of generated data has exactly one failure mode and it is
this one. (`flow/chip/ReadMe.md` learned the same lesson separately — its
baseline table sat five days at numbers ~18× off.)

Run it yourself:

```bash
tools/qor_corpus.py --out mine.json            # sweep the corpus
tools/qor_corpus.py --compare base.json mine.json
tools/qor_table.py  --out qor/qor_table.md     # render the snapshot
```

The baseline-vs-branch recipe every routing change should run is in the
`tools/qor_corpus.py` module docstring: build `main`, sweep, build the branch,
sweep, `--compare`.

## What membership is for

- **A row defends a capability.** `chip_stack_topdown` is in because it is the
  only row exercising the mirrored track origins on the top-down path; its
  bottom-up twin is the *copy* path, where a broken origin degrades quietly to
  bits-solved-alone rather than failing.
- **A row costs runtime on every sweep**, paid by everyone measuring anything.
  `chip_stack_topdown` costs ~101s serial; `chip_stack_bottomup` ~30s. Cost is
  stated in the comment beside each chip row so the trade is visible.
- **Residuals can be the point.** Several rows are deliberately non-clean:
  `tc3a` is a hopeless over-capacity design (the ripup convergence guard's
  target), the `mix2_fast*` variants are healer-less ablations,
  `mix2_fast_on_aligned_sql` is the last live supply-doomed-seat exemplar. A
  change that "fixes" one of those has usually removed the coverage instead —
  which is why the `_2x` track-density twins were added *beside* their
  originals rather than replacing them
  ([track_density_doubling.md](track_density_doubling.md)).

## Finding candidates: `--candidates`

```bash
tools/qor_corpus.py --candidates              # static: coverage only, seconds
tools/qor_corpus.py --candidates --quantify -j 4   # also run each one
```

Reports every full-pipeline flow under `flow/` and `demo/` that is **not** a
corpus member, ranked by feature coverage no member exercises. `--quantify`
additionally runs the new-coverage ones so their size, QoR and runtime sit
beside their coverage — coverage argues for adding a row, runtime argues
against, and the decision needs both.

**It never edits `CORPUS`.** Adding or removing a flow is the owner's call; the
tool supplies evidence. (`test_qor_candidates.py` pins this.)

### How coverage is measured, and what it is worth

A flow's coverage is its set of **feature tokens**, taken over the commands it
**reaches** in the flow and everything it `source`s transitively:

- the command name (`run_nuts`, `add_keepout`);
- plus `cmd:mode` for each argument that is a **known mode word** for that
  command (`run_bundler:combined`, `generate_topologies:multi_trunk`).

Two rules do the real work, and both exist because the obvious implementation
manufactures findings:

**Modes come from a closed vocabulary (`_MODE_WORDS`), never from position.**
Position cannot tell a mode from an object: `set_bundling <prefix> <mode>` and
`set_bottom_up <cell> [on|off]` lead with an object, so a first-argument rule
emits `set_bundling:clk_` — every new net prefix reads as new coverage — while
missing `strict` vs `combined` entirely. Position also misses a mode that
moves (`run_hier_bundler depth 3 COMBINED` has its strategy third). Matching a
vocabulary anywhere in the argument list fixes both, and an object name can
never collide with it. The cost is that an **unlisted** mode is silently not
coverage — the report under-claims rather than inventing a finding, but
`_MODE_WORDS` must grow when a command gains a mode.

**Only REACHABLE commands count.** `exit` raises `SystemExit`, so everything
after it — in that file or in whatever sourced it — is dead script. Counting it
would score `rnr/mix2_repro.buda` as a full pipeline it never runs. Eligibility
and coverage read the same scan, so the two cannot disagree.

Two further exclusions: **report/inspection** commands take bus-name hints, so
`dump_topologies bus_007` would make every flow look unique; and **viz / audit**
commands drive nothing the headless sweep would not drive anyway (it runs
`no_viz` and calls `check_design` itself).

**This is a proxy, and the weak direction is "no new tokens".** Two flows can
call an identical command set and still exercise different code — different
geometry, different congestion, a different candidate winning selection. Read
that verdict as *nothing obviously new*, never as *redundant*. The strong
direction is the other one: if a token appears in no corpus flow, that feature
genuinely has no corpus coverage.

### What it found (2026-08-06, corpus at 41 flows / 46 tokens)

96 candidates: **36 with new coverage, 60 without.** The largest gaps, by what
they would defend rather than by token count:

| gap | flows | assessment |
|---|---|---|
| **`add_keepout`** — no corpus flow declares a keepout ZONE (verified: zero members, sourced files included) | `keepout_demo`, `nuts_group_pull`, `dnuts_track_override_kor`, `nuts_corner_overlap_3layer`, `mempool_tile`, `comprehensive_demo`, `comprehensive_regression` | The most substantive hole, with one qualifier: the hier flows *do* exercise leaf footprints blocking LOW layers, but that is a different mechanism. Declared zones are what `KEEPOUT_CROSS` audits (`zone_fp`, session floorplan) and what carves signal-track supply out of a span — the input to dead-span escalation and the DNUTS cull. That path is guarded by unit tests only, never end-to-end. `nuts_group_pull` (1 bundle, 0.0s) or `keepout_demo` (2 bundles, 0.0s) is nearly free. |
| **TopoEdit `edit_*`** — the whole expert-edit family | `c_dd_detour`, `c_ddd_detour`, `c_double_detour` | Seven uncovered commands, the largest single block. All three are 1-bundle, 0.0s, 0/0/0. A USER candidate committed by `edit_commit` reaches the planner and NUTS like any other, so the path is real. |
| **`add_grid_override`** — region-scoped track patterns | `dnuts_track_override_kor` | Overlapping the keepout gap; one flow covers both (5 bundles, 0.0s). |
| **`set_min_stub_length_dir` / `_layer`** | `four_blocks`, `dnuts2`, `hier_four_blocks[_cell]` | Per-direction / per-layer stub floors. All trivial (≤4 bundles, 0.0s). |
| **`run_planner post_nuts`** | `channel_stress`, `ariane136`, `ariane_buda5` | Post-NUTS stub layer reassignment — a whole planner stage with no corpus row. `channel_stress` is 62 bundles at 0.1s and clean. |
| **`generate_topologies spine_relays`** | `big_6bundles_mst`, `big_6bundles_trunk_plus_mst` | The opt-in MST relay-hub collector spine. 6 bundles, 0.4s, 0/0/0. |
| **`select_topologies`** (plural pin form) | `dogleg1/2`, `quickstart`, `no_planner_flow`, … | Cheap, but pinning is well covered by unit tests; weakest case here. |
| **`set_track_pitch`** | `large_scale_demo`, `comprehensive_demo` | Explicit pitch declaration before planning. |

**Two candidates are broken and errored under `--quantify`** — a finding in
itself, since nothing else runs these files:

- `flow/layer_assignment.buda` — line 1 is `corner_margins 4.0 6.0`. The command
  is `corner_margin`, singular, and takes `dx <n> [dy <n>]`; the name and the
  argument form are both wrong, so the CLI fail-fasts and the flow has never
  run. (It surfaced *as* a coverage finding — `corner_margins` is a token no
  corpus flow has, because it is not a command. An errored candidate's "new"
  token is worth reading as a possible typo.)
- `flow/g1_bundle.buda` — `add_block` with fractional coordinates
  (`68.300`); block coords are integers, so it raises on parse.

Neither is a corpus candidate until fixed, and neither is fixed here — repairing
a dead flow changes what it routes, which is its own decision.

## Measuring a per-flow COST — what this corpus cannot do

`qor_corpus.py` prints runtime as **informational, never a gate** ("single-run
and noisy") — and that caveat is load-bearing, not boilerplate.  A corpus
wall-time delta cannot resolve a per-flow cost of a few percent or less.  Use
it to spot a 2× blow-up; do NOT use it as evidence for or against a small
one.

**The worked example (2026-08-02, the pair-align default-flip).**  The
measured-accept alignment heal adds one extra DNUTS solve per eligible flow.
A single heal-on/heal-off corpus run put that at **+3.3% wall** (1270s →
1312s), and that number was reported as the cost bar.  It was noise.  The
corpus contains 6 bottom-up vehicles whose `hier.locked` bundles make the
heal return BEFORE solving — they cannot pay the cost at all — and they moved
**+4.2%**, MORE than the flows that actually pay (+2.5%).

The warm microbenchmark then measured the real per-flow cost of that one
solve: **`big` 2.1% of flow wall, `big2` 1.6%, `tc3a` 0.15%, `mix` 0.13%,
`mix2_fast_topdown` 0.10%, `bigHalf` 0.05%** — every one of them BELOW the
corpus figure, and most of them far below it (the spread across flows is
itself ~40×, which a single aggregate number cannot express at all).  That
changed the verdict's basis: the flip is refused for lack of benefit, not
for cost.

**Two techniques that catch this — both cheap:**

1. **Look for an accidental control group.**  Is there a subset of flows the
   change provably CANNOT affect (feature-gated off, scoped out, a different
   code path)?  Measure it alongside.  If the control moves as much as the
   treated set, the delta is host noise and the experiment has answered
   nothing.  The gate conditions in a heal's early-return are usually a
   ready-made control group, free of charge.
2. **Microbenchmark the specific operation, warm, best-of-N**, then express it
   as a fraction of the flow's wall time.  A `time.perf_counter()` loop around
   the one call under test (5 reps, take the min) resolves milliseconds that a
   whole-flow run buries under process start-up, I/O and scheduler jitter.

**This is about runtime ONLY — do not carry it over to the WL columns.**
Both informational diffs say "not a guard", but for opposite reasons, and
conflating them would throw away a good signal:

- **runtime** is not a guard because it is *not reproducible* — single-run
  wall time, and the parallel sweep inflates `sec` under CPU load.  Small
  deltas are noise, which is what this section is about.
- **wirelength** is not a guard because a topology/planner change may
  *legitimately trade* WL for routability — not because the number is
  unreliable.  WL is **deterministic** per host and byte-identical between
  serial and parallel sweeps, so a small WL delta is real signal and should
  be read, not discarded.  In this very experiment the accepted heal's
  benefit was a −0.02% WL move on one flow: far too small to survive a
  runtime-style noise argument, and entirely trustworthy as WL.

The QoR metrics (overlaps/unplaced/viol_bundles) are likewise deterministic
per host, and remain the gate.

## A measurement caveat that outlived its table

Corpus flows run **as written with `script_path` set**, so the shipped
`kSegsRel` 0.02 default and the `run_nuts` dead-span escalation engage on flows
that declare `healersAhead` — the endpoints are the **real shipped ones**. A
*scriptless* harness (what the `drop_dangling_modes` sweep's `base` column used)
suppresses both. The two agree on healer-less flows and diverge on healer flows;
see [`dedup_default_2026-07.md`](dedup_default_2026-07.md) for the confound
analysis.

One flow is not a shipped-CLI baseline: **`rnr/mix2_repro.buda`** has a
mid-script `exit` right after `run_nuts`, ahead of its healers and
`run_detailed_nuts`, so under the shipped CLI it stops there. It is a `mix2`
debug repro — use `mix2` as the canonical row. It is not a corpus member, and
`--candidates` excludes it too: reaching `run_detailed_nuts` is the bar, and it
does not. (The note is kept because the file is still in the tree and still
tempting to measure.)

See also: [`drop_dangling_modes_2026-07.md`](drop_dangling_modes_2026-07.md),
[`healer_effectiveness_2026-07.md`](healer_effectiveness_2026-07.md),
[`gentopo_loci_multitrunk_2026-07.md`](gentopo_loci_multitrunk_2026-07.md) (same
corpus, different experiments — the last is the `no_hanan_loci` × `multi_trunk`
gen-topo knob sweep), [`dedup_default_2026-07.md`](dedup_default_2026-07.md)
(the `kSegsRel`/`script_path` measurement caveat), and
[`track_density_doubling.md`](track_density_doubling.md) (why the `_2x` twins
sit beside their originals rather than replacing them).
