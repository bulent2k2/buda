# Keepout-model audit — abstract vs detailed agreement

**Status: CLOSED (classes 1–3 fixed; classes 4–5 noted, out of scope).**
Follow-up promised in [`wishlist-planner.md`](wishlist-planner.md) → *"LOW-layer
abutment crossings … ✅ RESOLVED"* ("the abstract/detailed keepout-model
mismatch … is unchanged") and ranked as item 4 in [`opens.md`](opens.md).

BUDA has three keepout representations, populated from the same two sources
(user `add_keepout` zones + implicit LOW-layer leaf-cell footprints):

| Model | Store | Consumers |
|---|---|---|
| `KeepoutZone` (bbox + layer_ids) | `Floorplan::low_keepouts()` | topology generation culls, abstract NUTS (`keepout_occupied`), planner (`band_available_length`) |
| Grid keepouts (`Rect` per layer) | `RoutingGrid::keepouts_` (installed by `_install_leaf_grid_keepouts` / `cmd_add_keepout`) | DetailedNUTS track queries, planner `count_signal_tracks_in` (signal_tracks mode), viz rails |
| Band capacity subtraction | planner cut/band bookkeeping | congestion cost + overflow |

The audit asked: **do the abstract and detailed stages agree about what a
keepout blocks?** They did not, in five distinct ways.

## Mismatch classes

### 1. DNUTS sampled keepouts at ONE point — the span midpoint — FIXED

`signal_tracks_in(x, lo, hi)` filters tracks against keepouts at a single
along-coordinate `x`, and DetailedNUTS passed the bus segment's span
*midpoint*. A keepout overlapping the wire's span but missing the midpoint
was invisible: every bit routed **straight through it** while every metric
stayed clean.

Minimal repro (locked in `test/tests/test_keepout_model.py`): blocks
A(0,0,100,100) → B(500,0,600,100), keepout x[350,450] y[0,100] on M4 —
covering the wire's whole slide window but not its span midpoint (x=300).
Pinned to M4, **main**: NUTS 0 overlaps / 0 violations, DNUTS 0 unplaced,
**8/8 bits through the keepout**. In the wild, `channel_stress.buda` has
always emitted **3 such illegal bit-wires** (user `add_keepout` zones) and
`hbundles/10_chip_units_blocks_leaf.buda` **7** (implicit leaf-cell
footprint keepouts on non-TOP M7 — bundles 1 and 3, seg 1), both measured
on main with the same crossing predicate; no other corpus/flow-test flow
crosses.

**Fix — two halves, zero false positives:**

- **Preferred pool** (`RoutingGrid::signal_tracks_in_span`,
  `src/routing_grid.cpp`): a span-aware sibling of `signal_tracks_in` — a
  track is blocked when a keepout's perp extent covers the track centre AND
  its along extent overlaps the *whole abstract span*. DetailedNUTS prefers
  this span-clear pool and **falls back to the classic midpoint pool** when
  the clear pool holds fewer than `bit_width` tracks.
- **Post-adjustment cull** (`DetailedNUTSEngine::cull_keepout_crossers`,
  `src/detailed_nuts.cpp`): after `adjust_bit_spans`, any bit whose FINAL
  junction-adjusted span still crosses a keepout is removed, counted in
  `num_unplaced` + the new `num_keepout_bits`, and warned about — an illegal
  wire is never emitted.

**Why not just hard-filter on the abstract span?** That was the first
attempt, and the corpus falsified it: **495 newly-unplaced bits** vs the
**3 real crossings** measured on main. Bit spans get junction-adjusted
*after* placement and mostly stop short of the abstract span's far reaches —
the abstract span wildly over-approximates what the wire finally occupies.
The preferred-pool + final-span-cull design keeps the dodge (bits move to
span-clear tracks when they exist), degrades gracefully (midpoint pool when
they don't), and judges legality only on the real final geometry (the cull
has zero false positives by construction). Result: `channel_stress` reports
exactly the 3 honest opens; `four_blocks`/`tc3a_flat` goldens byte-identical;
`rnr_mix` placements shift (preferred pools pick different tracks) with 0
unplaced.

### 2. Abstract NUTS's exhausted-window fallback committed onto keepouts silently — FIXED (report channel)

`keepout_occupied` feeds keepout intervals into placement occupancy, so
abstract NUTS *avoids* keepouts when it can — but the exhausted-window
fallback commits the interval **centre** with no metric of any kind. A
segment sitting bodily ON a keepout reported `overlaps=0, violations=0`.

**Fix:** `count_keepout_conflicts` (`src/nuts_geom.h`) — placed segments
whose physical extent `[pos ± w/2]` strictly overlaps a keepout that
overlaps their span — computed in `run()` and `rerun_layer`, exposed as
`NUTSResult::num_keepout_conflicts` (bound to Python) and printed as a
`[NUTS] WARNING` when non-zero. Report-only: placement behavior is
unchanged (the ripup/negotiate machinery keys off DNUTS opens, which class 1
now makes honest — the abstract counter is the early-warning mirror of the
same event, `=1` on the pinned repro).

### 3. Empty `layer_ids` semantic divergence — FIXED

Topology generation's keepout predicates treat a `KeepoutZone` with empty
`layer_ids` as **blocking every layer**; every other production consumer
silently *ignored* such zones (`layer_ids.count(t->layer)` /
`lid in koz.layer_ids` on an empty set is never true). Such a zone is only
creatable via the Python `Floorplan.add_keepout_zone(..., [])` API (the CLI
requires explicit layers), but through that door it fell through **four**
paths at once: abstract NUTS (`keepout_occupied`), the planner's band
capacity (`band_available_length`), and both grid-installation paths
(`def_track_pattern`'s re-apply, `_install_leaf_keepouts`) — so the planner
assumed capacity through it and DetailedNUTS routed bits straight through
it (Codex review caught the propagation gap beyond `keepout_occupied`).

One convention now, empty = blocks all, at every consumer:
`keepout_occupied` and `band_available_length` test
`!layer_ids.empty() && !layer_ids.count(...)`, and
`_install_leaf_keepouts` is the single grid-sync point for all-layer zones
(installed on every defined grid, TOP included; `def_track_pattern`'s
re-apply stays explicit-only to avoid double-installing). End-to-end
regression: `test_keepout_model.py::test_all_layer_zone_blocks_every_stage`.
No corpus flow declares an all-layer zone, so goldens are unaffected.

### 4. `verify.cpp` is keepout-blind — NOTED, open

`check_nuts` / `check_dnuts` audit connectivity, layer direction, and
unplaced bits, but never test a placed wire against keepouts — the class-1
illegal wires sailed through `check_connectivity` too. With the cull in
place DNUTS no longer emits such wires, so the check would currently find
nothing; still, a `KEEPOUT_CROSS` violation type would make verify
self-sufficient (defense in depth if a future stage regresses). Deferred —
tracked in [`wishlist-nuts.md`](wishlist-nuts.md).

### 5. Planner band sampling at cut coordinates — NOTED, open (and mitigated)

`band_available_length` subtracts keepouts along the *cut line*, and
signal-track mode's `count_signal_tracks_in` samples the pattern at the cut
coordinate — both point-samples along the segment's travel, the same
approximation class as 1 (as is `effective_pattern_at`'s point sampling for
pattern overrides, documented in `routing_grid.h`). The blast radius is
different: a planner miss means a *cost* misestimate that classes 1–2 now
surface downstream (honest DNUTS opens → `negotiate_congestion` /
`ripup_reroute` feedback), not a silent illegal wire. Full span-aware band
accounting is a planner-cost-model change with real churn — deferred,
tracked in [`wishlist-planner.md`](wishlist-planner.md).

## Corpus impact (golden re-baseline)

- `channel_stress.txt` — DNUTS `unplaced 0 → 3`, netsegs 555 → 552, vias
  355 → 352: the 3 historical keepout crossings are now culled and counted
  instead of emitted as illegal wires. Deliberate.
- `rnr_mix.txt` — bit placements shift (span-clear preferred pools pick
  different tracks); 0 unplaced before and after. Deliberate.
- `four_blocks.txt`, `tc3a_flat.txt` — byte-identical.
- Topology goldens — untouched (no generation change).

## Tests

- `test/tests/test_keepout_model.py` — the pinned compound repro
  (conflicts=1, unplaced=8, keepout_bits=8, zero crossing bits) and the
  graze case (partial-window keepout → all 8 bits dodge legally, all
  counters 0 — the false-positive guard).
- `test/tests/test_flow_scripts.py::test_channel_stress_packs_clean` —
  premise updated to the 3 honest opens + cull warning.
