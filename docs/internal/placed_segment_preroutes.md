# PlacedSegmentBase + First-Class Pre-Routes (Phase G) — Plan

Status: **PLANNED** → implemented on the branch this doc lands with.
This is Phase G of [`nuts_dnuts_refactor.md`](nuts_dnuts_refactor.md),
deferred there until pre-routes became first-class — this plan makes them
first-class, which is what unlocks the base type.  Same ground rules as the
parent refactor: the routing pipeline's *results* stay **byte-identical**
(the type unification is layout-only), every existing pybind name unchanged,
additions only.

## 1. Problem / motivation

CLAUDE.md's "Segment Type Hierarchy (target state)" has described an intended
unification for a long time:

```
PlacedSegmentBase          kind, layer, span, track_position, width, placed
├── BusSegment             …
├── NetSegment             …
└── PreRoutedSegment       label, track_index
```

As built, nothing shares a base type, and pre-routes exist only *implicitly*
as non-SIGNAL `TrackSlot`s inside `TrackPattern` — there is no object that
says "there is a VDD rail on M4 at y=210 spanning the die", so:

- the visualizer's planned `draw_preroutes` (CLAUDE.md stage 7) has nothing
  to draw from — the existing `[Tracks]` rails view (`_build_rail_artists`)
  re-derives stripes ad hoc from `tracks_in_range` and is gated to detailed
  mode only;
- nothing downstream (reports, future BDB pre-route rows, OA/GDS export of
  power rails) can consume pre-routes as data;
- the three placed-segment types can't share `kind`-dispatched code paths.

## 2. Design

### 2.1 `PlacedSegmentBase` (`src/placed_segment.h`, header-only)

```cpp
enum class SegKind { BUS, NET, PREROUTE };

struct PlacedSegmentBase {
    SegKind kind;
    int     layer   = 0;
    double  span_lo = 0, span_hi = 0;    // routing-direction extent
    double  track_position = 0.0;        // perpendicular position
    double  width   = 1.0;
    bool    placed  = false;
    explicit PlacedSegmentBase(SegKind k) : kind(k) {}
};
```

- **`TrackSegment : PlacedSegmentBase`** (kind `BUS`) — the stage-4 placed
  bus segment.  Its ctor keeps today's defaults (`track_position` = NaN =
  unplaced).  All field *names* are unchanged, so every use site and every
  `def_readwrite` in `bind_nuts.cpp` compiles as-is (pybind accepts
  base-class member pointers on a derived-class binding).
- **`NetSegment : PlacedSegmentBase`** (kind `NET`) — the stage-9 bit-wire.
  Gains `placed` from the base but it stays **unbound** (deliberately: e.g.
  `tools/wl_corpus.py` treats a missing `placed` attribute as True for
  detailed segments; binding it would silently change that accounting.
  Bind it if/when DNUTS starts emitting unplaced rows instead of counting).
- **`PreRoutedSegment : PlacedSegmentBase`** (kind `PREROUTE`) — NEW:
  `std::string label` (the `TrackSlot` label, falling back to its type) and
  `int track_index` (index within its layer's enumeration — a viz/report
  identity, not a global track id).  `placed` = true by construction.

**Deviation from the CLAUDE.md sketch, resolved deliberately:** the sketch
lists `BusSegment` under the base, but today's `BusSegment` is the stage-9
*input descriptor* (pre-placement: intervals, bit_width, bit_order,
connections) — it has no placement of its own; the placed abstract bus
segment is `TrackSegment`.  Folding them into one type would require
renaming bound fields (`track_position` ↔ `abstract_pos`), i.e. a breaking
pybind change for zero behavior gain.  So the hierarchy as built is
`TrackSegment / NetSegment / PreRoutedSegment : PlacedSegmentBase`, with
`BusSegment` remaining the engine-input struct; CLAUDE.md's target-state
section is updated to match.  Revisit the Track/BusSegment merge only with a
deliberate binding-breaking version.

### 2.2 Pre-route enumeration (`RoutingGrid::preroutes_in`)

```cpp
// routing_grid.h
std::vector<PreRoutedSegment> RoutingGrid::preroutes_in(
    double perp_lo, double perp_hi,
    double along_lo, double along_hi) const;
std::vector<PreRoutedSegment> RoutingGridStack::preroutes(
    int layer_id, double perp_lo, double perp_hi,
    double along_lo, double along_hi) const;   // stamps .layer
```

- Global pattern: `tracks_in_range(perp_lo, perp_hi)`, keep **non-SIGNAL**
  slots, one `PreRoutedSegment` per slot occurrence — `track_position` =
  slot centre, `width` = slot width, span = the along window, `label` =
  `slot.label` (else `slot.type`), `track_index` = running index per layer.
- `PatternOverride`s: each override's local pattern is enumerated within
  (region ∩ perp window) with span clipped to (region ∩ along window) —
  pre-routes break at region boundaries, as CLAUDE.md documents.
  **v1 approximation:** the *global* pattern's bands are not split where an
  override shadows them (the same approximation the rails view and
  `signal_tracks_in`'s single-x sampling already make); exact
  global-band splitting is wishlisted, not blocking.
- Orientation from `is_horizontal_` decides which region coords are
  perp vs along (H layer: perp = y, along = x).

### 2.3 pybind (additive only)

- `SegKind` enum; `def_readonly("kind", …)` on `TrackSegment`, `NetSegment`
  (NEW names — nothing existing moves).
- `PreRoutedSegment` class with all fields.
- `RoutingGridStack.preroutes(layer_id, perp_lo, perp_hi, along_lo, along_hi)`.

### 2.4 Visualizer: `draw_preroutes` (CLAUDE.md stage 7, planned → real)

Follows the `draw_detailed_tracks` + `_build_rail_artists` lazy-build
template exactly:

- `draw_preroutes(routing_grid_stack, layer_stack)` stores the handles,
  reveals the button, no artists yet.
- Lazy `_build_preroute_artists()`: enumerate `stack.preroutes(...)` per
  layer over the floorplan-blocks bbox (the rails-view extent idiom), one
  `PatchCollection` per `(layer, slot type)`, colored by the (promoted,
  SHIELD-extended) rail palette, registered in a flat
  `self._preroute_artists` list (not per-bundle — pre-routes belong to no
  bundle).
- **Per-type visibility** via one cycling `[Preroutes]` button:
  `off → ALL → POWER → GROUND → CLOCK → SHIELD → off` (label shows the
  mode).  State lives in `ui_state.ViewState` (`preroutes_mode` +
  `cycle_preroutes()`), synced like `[Detailed]`/`[Tracks]`.  Unlike the
  `[Tracks]` rails view this works in the **abstract** view too — that is
  its point (see pre-route congestion/blockage context before detailed
  routing exists).
- Wiring: `cmd_visualize` calls `viz.draw_preroutes(session.routing_grid,
  session.layers)` whenever `session.routing_grid` exists.

### 2.5 Follow-ups explicitly out of v1

- BDB pre-route rows / GDS export of rails (needs schema thought).
- Exact global-band splitting at override regions.
- A dedicated per-type button row (the cycle button keeps the left panel
  compact; revisit if types multiply).
- `TrackSegment`/`BusSegment` merge (binding-breaking; see §2.1).

## 3. Gates

1. **Placement goldens byte-identical** (`tools/nuts_snapshot.py` corpus) —
   the inheritance change must not move a single wire; pre-route enumeration
   and viz are additive.
2. Fast + mid tiers green (`bin/bb test` / `mid`), incl. the untouched
   binding surface (`test_viz_collections.py` extended, not modified).
3. New unit tests: kind tags through pybind; `preroutes()` enumeration
   (counts/labels/positions on a known pattern; override clipping;
   SIGNAL exclusion); viz lazy-build + cycle-toggle visibility on a flow
   with track patterns (Agg backend, the `test_viz_collections.py` pattern).
4. CLAUDE.md: stage 7 `draw_preroutes` un-planned; "Segment Type Hierarchy"
   section rewritten to as-built; source map gains `placed_segment.h`.
