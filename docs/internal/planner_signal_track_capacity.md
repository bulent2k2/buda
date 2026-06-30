# Plan: signal-track-count band capacity — `run_planner ... signal_tracks`

Status: **design / not yet implemented.** This is the detailed plan for the
wishlist item *"Gap A part 2: model band capacity in signal-track count, not
layout width."* See also `docs/internal/planner_low_layer_over_cell.md` (Gap A/C
breakdown) and `docs/congestion_planner.md` (cost model + escalation ladder).

## Context — why

The bundle planner models a Hanan band's capacity as available **layout width**
(`band_available_length`, `src/congestion_planner.cpp:74` → `GlobalCut.band_cap`):
the geometric length of the band minus keepout-blocked stretches. But
DetailedNUTS places each bit on a discrete **SIGNAL track** drawn from the layer's
`TrackPattern` — POWER/GROUND/CLOCK slots are not usable, and the usable count is
a quantized fraction of the length that also depends on the pattern's origin/phase
and any `add_grid_override` regions.

So at a contended interval the binding constraint is the **per-track signal
count**, not the width. The planner can commit a bundle `overflow=0` while the
band is actually short of signal tracks → a silent open that only DetailedNUTS
discovers.

**Evidence (`flow/rnr/mix.buda`, hier flow).** Baseline 21 NUTS overlaps / 236
DNUTS opens. Running `ripup_reroute` after `run_nuts` (stage a) drives overlaps
**21 → 0** and opens **236 → 150** — but the remaining **150 opens sit at
`overlaps == 0`**, i.e. they are *not* contention; they are pure signal-track
capacity shortfalls the width model never predicts. (On `big2.buda` the same
signature appears as 3 NUTS-clean DNUTS-open bundles: 10, 14, 37.)

The **demand** side already uses a diluted width (`eff_bus_width`,
`src/layering.cpp:42` = `bits × unit_pitch / n_signal` when a measured bit pitch
is set, else `base_width × dilution_factor`). The gap is the **capacity** side
still using continuous geometric length — and the fact that **the planner never
sees the `RoutingGridStack`** (it is constructed with `(Floorplan, LayerStack)`
only, `src/congestion_planner.h:129`). That missing input is the whole fix.

## Goal

Make over-subscription surface as `overflow` at **planning** time — so the
existing STRICT → rip-up/replan → ALLOW_OVERFLOW → BEST_EFFORT ladder engages and
avoids the open up front — instead of failing silently at DetailedNUTS.

Ship it as an **opt-in** `run_planner` option so existing flows are byte-identical
(no regression risk on the already-over-congested `big2.buda`).

## Option surface

A new keyword on both planner branches (default off):

```buda
run_planner 5 signal_tracks          # flat
run_planner hier 5 signal_tracks     # hier
```

When set, the planner charges band capacity in **signal-track count** and demand
in **bit count** on every layer that has a `def_track_pattern`; layers without a
pattern keep the width model (mixed stacks work). Requires at least one
`def_track_pattern` (error otherwise, like `run_detailed_nuts`).

## Implementation

### 1. Plumb the routing grid into the planner
- Add `CongestionPlanner::set_routing_grid(const RoutingGridStack*)` and
  `set_capacity_mode(CapacityMode)` where `CapacityMode { WIDTH (default),
  SIGNAL_TRACKS }`. Keep them optional setters (backward compatible; no ctor
  change).
- Expose both via `bind_routing.cpp`.
- `buda_cli`: in the `run_planner` / `run_planner hier` branches, when the
  `signal_tracks` keyword is present, call `planner.set_routing_grid(self.routing_grid)`
  and `planner.set_capacity_mode(SIGNAL_TRACKS)` **before** `build_congestion_map()`.

### 2. Capacity in track count (`rebuild_cuts_`)
- In SIGNAL_TRACKS mode, when the cut's layer has a grid in the stack, set
  `band_cap[b] = grid.signal_tracks_in(cut_coord, band_lo, band_hi).size()`
  instead of `band_available_length(...)`.
- **Coordinate convention** (verify against `RoutingGrid::signal_tracks_in(x, lo,
  hi)` and `effective_pattern_at(x, y)`):
  - V-cut / H-layer: cut at `x_mid`, perpendicular = Y → `signal_tracks_in(x_mid,
    y_lo, y_hi)`.
  - H-cut / V-layer: cut at `y_mid`, perpendicular = X → the transpose; confirm
    the grid's first arg is the along-routing position used to pick the override
    region for that direction.
  - Add a unit test asserting the planner's per-band count equals a direct
    `signal_tracks_in` call for both directions.

### 3. Slide-window clamp (`usable_band_cap`)
- Count signal tracks within `[max(band_lo, slide_lo), min(band_hi, slide_hi)]`
  rather than scaling a length ratio.

### 4. Demand in bit count
- At the ~6 `eff_bus_width(nbits, width, lid) + track_pitch_` sites
  (`congestion_planner.cpp:376, 431, 643, 655, 707, 717, 770`): in SIGNAL_TRACKS
  mode and on a patterned layer, charge `nbits + guard_tracks` (one bit = one
  track) instead of diluted width. `guard_tracks` = inter-bus spacing in track
  units (the track-count analogue of `+track_pitch_`), default 1, configurable via
  `set_planner_param`.

### 5. CLI parsing
- Parse `signal_tracks` in both planner branches (alongside the existing arg
  parsing); error if no `def_track_pattern` is defined; print a one-line
  `[Planner] capacity mode = signal-tracks` confirmation. Update
  `KNOWN_COMMANDS`/help and the planner-capacity summary print to show track
  units when active.

## Subtleties to get right

- **Leaf-cell over-cell exclusion.** DetailedNUTS installs LOW-layer keepouts over
  solid (non-container) leaf cells *before* its solve
  (`buda_cli._run_detailed_nuts`, the `low_layer_keepouts` install). For the
  planner's track count to match DNUTS, those keepouts must be present on the grid
  (or computed) **at plan time** in `signal_tracks` mode — else the planner counts
  tracks DNUTS will not use on LOW layers. Plan: install them idempotently before
  `build_congestion_map` when the mode is on. (TOP layers cross cells freely, so
  this only matters for LOW layers — exactly as in `rebuild_cuts_` today.)
- **Quantization slack.** Exact integer counts can reject a feasible route by a
  single track; `guard_tracks` doubles as the slack knob to avoid over-tightening.
- **Pitch / `set_track_pitch` interaction.** Today the planner reserves
  `+track_pitch_` width between buses (Gap 1) and `run_nuts` enforces the same
  pitch. In track units the spacing is implicit in the pattern; the guard track
  replaces the explicit `+pitch`. Keep `set_track_pitch` meaningful for the NUTS
  side; only the planner's *charge* changes.

## Validation

- **`flow/rnr/mix.buda`**: with `run_planner hier 5 signal_tracks`, the ~150
  capacity-driven opens should surface as planner `overflow` → rip-up/replan
  engages → materially fewer DNUTS opens **before** any `ripup_reroute`. Measure
  end-to-end opens with vs. without the keyword; expect the residual after
  `ripup_reroute` (currently ~30) to drop further.
- **`flow/big_data_test/big2/big2.buda`**: the 3 NUTS-clean DNUTS-open bundles
  (10/14/37) should become planner overflow warnings; total unplaced should drop.
- **Opt-in safety**: every existing flow/test omits `signal_tracks` → byte-identical
  behavior. Full fast+mid+slow tiers stay green.
- **New focused tests**: (a) a tiny floorplan + pattern where width-capacity says
  OK but signal-track count says overflow → assert the planner reports overflow
  only in `signal_tracks` mode; (b) the per-direction coordinate-convention unit
  test from step 2.

## Risks / scope

- More rip-up/replan firing in `signal_tracks` mode → longer `run_planner`
  (acceptable; opt-in, and the point is to pay the cost up front instead of at
  DNUTS).
- Coordinate-convention bugs in `signal_tracks_in` per direction — pinned by the
  unit test above.
- **Out of scope (v1):** making `signal_tracks` the default; a per-layer override
  to force one mode; folding the guard track into the NUTS pitch model. These are
  follow-ons once the opt-in mode is validated on real designs.
