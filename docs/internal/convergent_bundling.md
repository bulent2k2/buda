# CONVERGENT bundling — does it make sense?

Investigation of the bundler's `CONVERGENT` strategy end-to-end through the flat
routing pipeline. Short answer at the time of the investigation: **as then
modelled it was unsound** — it grouped nets the topology generator could not
faithfully route, so all but one driver of a convergent bundle were silently
left unconnected. This note records the evidence, the root cause, and the fix.

**STATUS (2026-07-11): FIXED — fan-in trees landed.** A multi-driver CONVERGENT
bundle now routes as a fan-in tree rooted at the shared sink with every driver
block as a leaf (`BudaSession._bundle_endpoints` derives generation endpoints
from ALL of a bundle's nets, and the existing multicast trunk+branch / MST
machinery — which connects a root to N leaves direction-agnostically — serves
the fan-in with the arrows reversed).  The missing **net-driver fidelity
check** landed too: `check_design` now emits `NET_DRIVER_OPEN` when a net
endpoint block is absent from a topology's `connected_block_names` contract
(`_net_driver_fidelity`, flat flow).  **Per-bit taper** (Codex #268 P1): a
fan-in tree is not all-bits-everywhere — `derive_fanin_seg_bits`
(`topology.cpp`) walks each net's driver→sink path through the seg_conns
graph and stores per-segment bit membership (`Topology::seg_bits`, derived,
never persisted), so a driver stub carries ONLY its own sub-bus: the planner
charges member-bit widths (`plan_bundle`/`commit_plan`/`plan_band_overlap`),
NUTS extracts tapered `TrackSegment.width`s, and DNUTS places only member
bits per segment (`BusSegment.bit_list`, global indices — via pairing and
`net_names[bit_index]` unchanged), so no net's wire ever lands on another
driver's block.  A bit with no derivable path falls back to all segments
(conservative) and the fidelity check reports it per net.  The historical
sections below are kept
as the record of the gap; the pipeline test's collapse assertions are inverted
into the acceptance tests (`test_convergent_fanin_routes_every_driver`,
`test_convergent_topo_check_passes_and_fidelity_flags_dropped_driver`, plus
the mixed shared+distinct-driver case).  QoR showcase: `demo/ariane136_l2` —
the 1024-bit rdata merge generates `[ic_data_0..3,dc_data_0..7]->cpu_core
fan-in` (12 drivers, 24 candidates) and passes `check_design topo`/`nuts`
clean.  Remaining follow-on (out of scope here): a CONVERGENT mode for the
HIER bundler — `run_hier_bundler` supports STRICT/BIDIRECTIONAL only.

Reproduced by `test/tests/test_bundler_convergent_pipeline.py`.

**COMBINED (2026-07-12).** The strategies form a lattice: STRICT (finest) is
refined by CONVERGENT and BIDIRECTIONAL — incomparable coarsenings — and the
only genuinely new combination is their JOIN: `run_bundler COMBINED` merges
nets connected by a CHAIN of either relation (union-find over the two
signature families, `_generalized_bundles` in `bundling_cmds.py`; the pure
C++ path stays byte-identical when neither COMBINED nor an override is in
play, pinned by equivalence tests).  Mixed chain groups (a bidirectional
pair joined to a convergent partner) route correctly because the fan-in
realization is direction-agnostic and per-bit tapered — verified end-to-end
with clean topo/dnuts checks.  `set_bundling <prefix>|* <mode>` gates each
relation per net prefix (both nets must permit a merge), and
`set_max_bundle_bits <N|auto>` bounds bundle size as a balanced,
bus-preserving split pass — `auto` derives a per-bundle cap from the
shortest busterm edge vs the bits the taper actually lands on each block.
QoR: `demo/congestion_demo`'s cpu+gpu→display fan-in merges under COMBINED
(3→2 bundles) with abstract WL 2224→1580 (−29%) at zero overlaps.  Tests:
`test/tests/test_bundler_combined.py`.

## The two strategies

`Bundler` (stage 1, `bundler.h/cpp`) groups nets by a string *signature*:

| Strategy | Signature | Intent |
|---|---|---|
| `STRICT` | driver instance + sorted receiver instances | nets that share *both* endpoints — a true parallel bus |
| `CONVERGENT` | sorted receiver instances **only** (driver ignored) | nets from *different* drivers that fan in to a common sink |

`CONVERGENT` only differs from `STRICT` when nets share a receiver instance but
have **different driver instances** — a many-to-one fan-in. That is exactly the
case examined here.

## Experiment

Four source blocks at separated rows, each driving one net into a single shared
sink on the right (`test_bundler_convergent_pipeline.py`):

```
add_block src0 0 0   100 80     add_net a0 src0.tx sink.r0
add_block src1 0 200 100 280    add_net a1 src1.tx sink.r1
add_block src2 0 400 100 480    add_net a2 src2.tx sink.r2
add_block src3 0 600 100 680    add_net a3 src3.tx sink.r3
add_block sink 800 250 950 450
```

Driven through `run_bundler {STRICT|CONVERGENT} → generate_topologies →
run_planner → run_nuts`:

| | STRICT | CONVERGENT |
|---|---|---|
| bundles | **4** (one per driver) | **1** (`reason=REC:sink`, nets a0–a3) |
| topology gen | `src0→sink`, `src1→sink`, `src2→sink`, `src3→sink` | **`src0→sink` only**, 6 units wide |
| NUTS horizontal runs (rows) | `[78, 265, 425, 601]` — **all four sources** | `[74]` — **src0 only** |
| `check_connectivity topo` | success | **success** (!) |

The CONVERGENT bundle's eight topology candidates are byte-for-byte the same as
the lone `src0→sink` bundle's candidates in the STRICT run — i.e. the bundle is
modelled purely from `src0`'s geometry, just widened to 4 bits.

## Root cause

`TopologyGenerator` (stage 2) derives **one** `src→dst` pair per bundle. A
bundle whose nets have different drivers therefore picks a single representative
driver (the first, `src0`) and routes *all* bits from there. The wires for
`a1/a2/a3` are drawn from `src0`, not from `src1/src2/src3`, so those three
drivers are never physically connected — the bus never goes near their rows.

`check_connectivity` (`verify.h/cpp`) does **not** catch this: it validates a
topology's *internal* self-consistency (segment continuity, busterm faces, block
coverage) against the bundle's own single-source geometry. It has no view of the
original per-net drivers, so a bundle that dropped three of them still "passes".

This is almost certainly why `src/buda_cli.py`'s `run_bundler` historically
hard-coded `STRICT` and ignored its argument: the `CONVERGENT` path produces
physically wrong routes.

## Verdict

- For nets that share **both** endpoints, `CONVERGENT` == `STRICT` (redundant).
- For genuinely convergent (different-driver) nets — the only case it exists for
  — it is **unsound**: the result silently omits all drivers but one.

So `CONVERGENT` does not make true sense in the present pipeline. Its *intent* is
real (fan-in patterns: multiple masters → one slave, write data → memory), and it
*might* prove useful, but only if topology generation gains **multi-source /
fan-in tree** support so a bundle can root at several drivers and merge toward the
shared sink. Until then it is a foot-gun.

## Why BIDIRECTIONAL is NOT in the same bucket

`run_bundler BIDIRECTIONAL` (`Strategy::BIDIRECTIONAL`) is direction-agnostic:
its signature is the sorted set of **all** endpoint instances (driver +
receivers), so nets connecting the same group of blocks in any roles bundle
together — A→B with its return B→A, or the cyclic a→b,c / b→c,a / c→b,a.

The crucial difference from CONVERGENT: a bidirectional bundle connects the
**same** blocks (just in mixed directions), whereas a convergent fan-in bundle
connects **different** driver blocks at different locations. Routing is
block-to-block and direction-agnostic — the single trunk that spans the group's
blocks physically connects every one of them — so **every net is routed**. It is
sound and needs no warning. (The only wrinkle is cosmetic: a busterm in such a
bundle is both a driver and a receiver, so the visualizer draws it with its own
symbol — a green diamond — instead of the driver-square/receiver-circle split.)
See `test/tests/test_bundler_bidirectional.py`.

BIDIRECTIONAL is available in **both** bundlers: `run_bundler BIDIRECTIONAL`
(flat) and `run_hier_bundler … BIDIRECTIONAL` (hierarchical). The hier bundler
keys on the sorted set of all endpoint names at each bundle depth
(`HierarchicalBundler::_bidir_sig` / `_sig`), and `_parse_bundle_reason`
(`buda_cli.py`) roots the block-to-block topology at the first instance of a
`BIDIR:` reason. A single hier bundle can then hold both bidirectional pairs and
plain one-way nets between the same blocks — see
`test/tests/test_hier_bidirectional.py`.

## What we did about it (historical — superseded by the fan-in fix)

- `run_bundler` now **honours** its `STRICT|CONVERGENT|BIDIRECTIONAL` argument
  (previously only STRICT, and the argument was ignored; default remains
  `STRICT`). `CONVERGENT` printed a warning that a bundle spanning multiple
  driver *blocks* routes from a single driver — **downgraded (2026-07-11)** to
  an informational note now that multi-driver bundles route as fan-in trees.
  `BIDIRECTIONAL` is sound (same blocks, direction-agnostic) so it never
  warned.
- `test_bundler_convergent_pipeline.py` locked in the CONVERGENT gap; its
  collapse assertions are now **inverted** into the fan-in acceptance tests
  (every driver covered by placed routing; the fidelity check flags a
  regressed single-driver topology; mixed shared+distinct drivers).
  `test_bundler_bidirectional.py` locks in that BIDIRECTIONAL groups the
  cyclic case and the single trunk routes the whole group.

## If we ever make CONVERGENT real — DONE (2026-07-11, as-built)

*(The plan below shipped exactly as sketched; see the STATUS block at the top.)*

Topology generation treats a multi-driver bundle as a fan-in tree: the
endpoint derivation (`_bundle_endpoints`, `src/buda_session/hier.py`) walks
ALL of a bundle's nets — a single-driver bundle returns the first net's
`(driver, receivers)` byte-identically to the old behavior; a multi-driver
bundle returns `(sink, [driver blocks + extra receivers], fanin=True)` and
`generate_candidates` produces its usual root-to-N-leaves multicast
trunk+branch / MST shapes, which are direction-agnostic — so every driver
gets a stub tap or pass-through and `connected_block_names` carries the full
endpoint set. All three flat generation commands share the derivation
(`generate_topologies`, `generate_topologies_for_bundle`,
`generate_more_topologies`; the knob-memo replay receives the derived
endpoints from its callers). `check_design` verifies fidelity:
`_net_driver_fidelity` (`src/buda_session/reports.py`) reports
`NET_DRIVER_OPEN` for any net endpoint block missing from the topology's
contract — gated to the flat flow (hier bundles' endpoint instances live in
a different name space than their generation floorplans) and skipping
hand-built candidates with an empty contract.

## Test-case assessment (2026-07-10)

Coverage for the future fan-in work already exists in two tiers, with known
gaps:

**Tier 1 — minimal synthetic repro (acceptance harness, inversion-ready).**
`test_bundler_convergent_pipeline.py`'s 4-drivers-at-separated-rows → 1-sink
scenario makes the defect *measurable from NUTS output* (`_h_rows` /
`_covered`): STRICT reaches all four source rows, CONVERGENT reaches one.
When the fan-in tree lands, two tests invert into the acceptance tests:

- `test_convergent_fanin_collapses_to_one_driver` →
  `_covered(rows) == set(_SOURCE_ROWS)` (every driver physically routed);
- `test_convergent_topo_check_does_not_flag_missing_drivers` → the new
  net-driver fidelity check FLAGS a bundle with an unattached driver.

**Tier 2 — realistic scale vehicle.** `demo/ariane136_l2.buda` (25 SRAM
blocks + cpu_core, already `run_bundler CONVERGENT`): the per-way/per-bank
read-data buses have 12 different SRAM drivers fanning in to cpu_core —
1024 bits in one genuinely convergent merge. Today it exhibits the unsound
single-driver route at scale; after the fix it is the QoR showcase. Its
sibling `ariane136_l2b.buda` hand-aggregates the same rdata into
broadcast-style single-driver buses — a ready A/B of "fan-in tree" vs
"manual aggregation".

**Gaps to fill alongside the implementation:**

- No unit-level topology test for the multi-source shape itself (per-driver
  busterms/segments; interplay with `finalize_candidates`' coverage gate) —
  make it fixtureless so `bin/u2b` can render it.
- No mixed-case test: a bundle where some nets share a driver and some
  don't, and fan-in meeting the multicast trunk+branch shapes.
- No hier variant: `run_hier_bundler` supports STRICT/BIDIRECTIONAL only —
  scope decision needed (see the hier rows in the scan below).
- Neither ariane demo is in the golden or `wl_corpus` sets — no ratchet.
  Add `ariane136_l2` to `wl_corpus` once the fan-in tree routes it.

## Corpus scan: where CONVERGENT would change bundling today (2026-07-10)

Every `flow/` + `demo/` script was scanned for **fan-in merges**: nets
sharing a receiver-instance set with ≥2 distinct driver instances — exactly
where CONVERGENT diverges from STRICT. Rerun with
`PYTHONPATH=build python3 tools/scan_fanin.py` (static `add_net`/`add_bus`
parse following `source`; BDB-fixture flows materialized via
`bdb_serialize.py` and read at leaf endpoint level, which approximates the
depth-aware hier signatures). 40 of the flows/demos contain at least one
merge; curated below.

**Genuine fan-in semantics — good flow-test / QoR candidates once the
feature lands** (all currently STRICT):

| Flow | Fan-in pattern | Scale |
|---|---|---|
| `demo/mempool_tile.buda` | 4 cores → shared bank array (TCDM crossbar write path), core pairs → shared icaches | 256b + 2×64b |
| `demo/mempool_group.buda` | 3 interconnect ports → the tile array | 108b |
| `demo/mempool_cluster.buda` | all-to-all: each group receives from the other 3 — four *overlapping* fan-ins (stress case for trunk sharing between fan-in trees) | 4×192b |
| `demo/ariane/ariane_core.buda` | alu/lsu/mult/id_stage → issue (writeback!) and alu/lsu/mult → regfile | 44b + 18b |
| `demo/ariane/ariane.buda` | dcache_hi/lo + execute → lsu; execute + icache → frontend | 34b + 26b |
| `demo/ariane136.buda`, `demo/ariane_buda5.buda` | dcache_data + icache_data → cpu_core | 192b |
| `demo/large_scale_demo.buda` (+ `_pseudo_hier`; flow-side twins `flow/test6.buda`, `flow/large_scale_demo_buses.buda`) | SoC: 7 masters → NoC, 4 → L3, 3 → sec, 2 → disp | 1400b + 352b + … |
| `demo/congestion_demo.buda` | cpu + gpu → display — the minimal *real* example | 32b |

The mempool trio and `ariane_core`'s writeback fan-in are the most
faithful to the feature's intent (multiple masters → one slave);
`large_scale_demo`'s 7-master NoC merge is the big-QoR target, and its
`flow/test6.buda` twin means a flow-tier regression vehicle already exists.

**Incidental signature collisions — do NOT switch these** (the shared
receiver is a coincidence of the scenario, and merging would change what
the flow tests):

- `flow/channel_stress.buda` (+ `test4`/`test4_nets` copies, and `test5`'s
  3×3 variant) — many top drivers → each bottom block, but these flows
  exist to stress *per-bus* channel packing; merging 62 bundles into 8
  would test something else entirely.
- `flow/datapath_multi_trunk.buda`, `flow/datapath_row_vhv.buda`,
  `flow/synth_{hv,vh}_bitrunk.buda` — column taps from two sources; these
  exercise BITRUNK shapes, not fan-in.
- Small regression flows (`dnuts1/2`, `planner6`, `pull1`,
  `sel_topos_typo`, `three_blocks_3_bundles`, `four_blocks_3_bundles`,
  `future/nuts_span_stretch_gap3`) — premises depend on their exact
  bundle counts.
- Hier flows (`hbundles/04/05/08/10`, `hier_bundle1/2`) — leaf-level
  fan-ins exist (e.g. hbundles/10 shows 59 merge groups), but the hier
  bundler has no CONVERGENT mode; these become relevant only if the mode
  is added there, and the depth-aware signature may split the leaf-level
  groups differently.
- BDB-fixture flows (`rnr/mix`, `mix2`, `slowdown*`) — 12 leaf-level
  merges each, but all are template-replicated copies of the incidental
  dnuts1 pattern (`u0`+`v0` → `u11` per instance); not fan-in intent.
