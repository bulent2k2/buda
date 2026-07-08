# Detailed NUTS Visualisation (Stage 9 View)

  Option 1 — Overlay bit-wires on the existing NUTS view
  
  Add draw_detailed_tracks(detailed_result, routing_grid_stack) to BudaVisualizer. For each NetSegment, draw a thin line at its track_position spanning [span_lo, span_hi]. Register each line under its
  bundle_id so existing click-to-highlight and layer visibility toggles work for free. Power/ground/clock grid rails from the RoutingGridStack are drawn as faint horizontal/vertical bands behind the
  signal wires. Wire color = layer color, thickness ∝ track width.

  Tradeoff: Quick to implement (~50 lines); bit-wires are drawn on top of the existing bus-band view, so it gets visually busy on wide buses. No new controls needed.

  ---
  Option 2 — Toggle between abstract (NUTS) and detailed (dNUTS) views
  
  Add a [Detailed] toggle button to the existing toolbar. Clicking it swaps draw_nuts_tracks artists out and draw_detailed_tracks artists in (and back). Grid rails are drawn as semi-transparent
  POWER/GROUND/CLOCK stripes across the full layout extent. Signal wires are thin lines, color-coded by bundle. The bundle panel, layer panel, and overlap list all stay functional.

  Tradeoff: Cleaner UX — one view at a time so nothing clutters the other. Requires managing two artist sets and a toggle bit, ~100–150 lines. The overlap list stays wired to abstract NUTS; a new
  num_unplaced count would be shown in the status bar.

  ---
  Option 3 — Zoomable per-segment detail panel
  
  On clicking a bus segment in the NUTS view, open a second inset axes (or a second figure) showing a cross-section of that segment's perpendicular interval: the full track pattern (POWER/GND/CLK rails
  as colored rectangles, SIGNAL slots as white rectangles), with the assigned bit-wire positions highlighted as filled circles numbered by bit_index. This is the most informative — you can immediately
  see which tracks a bus occupies and whether the contiguous-window constraint was met.

  Tradeoff: Most work (~200 lines, needs pick-event plumbing to connect a segment click → cross-section panel). Best for debugging dNUTS placement; not useful for whole-layout overview.

  ---
  Recommendation: Start with Option 2. It keeps the layout view uncluttered, the toggle button fits naturally in the existing toolbar, and the grid-stripe rendering gives an immediate visual of how
  densely the layout is packed. Option 3 is a good follow-on for debugging specific segments once Option 2 is in place.

# Option 2

The BUDA visualiser can display the output of Stage 9 (Detailed NUTS) as an
overlay on the layout: routing-grid rail stripes in the background, individual
bit-wire lines in the foreground.  A single **[☐ Detailed]** toggle button in
the left panel switches between the abstract Stage 4 view and the Stage 9 view.

---

## What is drawn

### Abstract mode (default)

The existing Stage 4 view: each bus is a thick coloured bar whose width equals
the abstract bus width, with dashed boundary lines showing the Hanan-cell
interval constraint.

### Detailed mode (`[☐ Detailed]` toggled on)

Two layers of geometry are drawn:

**Routing-grid rail stripes**

For every layer that has a `TrackPattern` defined:

| Layer direction | Stripe axis | Slot types rendered |
|---|---|---|
| H (M4 style) | Horizontal bands across full X extent | POWER (pale red), GROUND (pale blue), CLOCK (pale yellow), SHIELD (pale lavender), SIGNAL (near-white) |
| V (M5 style) | Vertical bands across full Y extent | same colours |

Stripes are drawn with very low alpha (0.15 for pre-route slots, 0.10 for the
SIGNAL stripes) so the bit-wires remain legible on top.  The rails are built
from the same enumeration as the abstract-view `[Preroutes]` bands
(`RoutingGridStack.preroutes(..., include_signal=True)` through the shared
`_track_band_rects` helper), so the two views share one palette and region
overrides render their local patterns with spans broken at region boundaries.

**Bit-wire lines (`NetSegment`s)**

Each `NetSegment` from `DetailedNUTSResult` is drawn as a thin line at its
`track_position`:

- H layer: horizontal line at Y = `track_position`, spanning X = `[span_lo, span_hi]`
- V layer: vertical line at X = `track_position`, spanning Y = `[span_lo, span_hi]`
- Line width ∝ `track_width` (actual signal track width from `TrackSlot`)
- Colour = same layer colour used in the abstract view

Every bit-wire is registered under its `bundle_id`, so click-to-highlight,
per-bundle visibility, per-layer visibility, and the Solo toggle all work
identically to the abstract view.

**Per-bit via markers (`NetVia`s)**

Each `NetVia` from `DetailedNUTSResult.net_vias` (one per bit per layer
transition — the bundle-level symbolic via fanned out to individual bits) is
drawn as a small white square outlined in the **upper layer's** colour
(`max(from_layer, to_layer)`), at the bit's exact crossing, above the bit-wires.

Efficiency: vias are batched into **one scatter `PathCollection` per
(bundle, upper layer)** — thousands of via markers collapse into a handful of
artists, the same trick as the bit-wire `LineCollection`s — and are built in the
same lazy `_build_detailed_artists()` pass (first `[Detailed]` toggle), so they
add no cost to the initial window load. They are registered under their
`bundle_id` like the bit-wires (highlight / layer / bundle / Solo gating), plus
an extra hard gate: visible only when **detailed mode AND `[Vias/Conns]`** are
both on (`_apply_detailed_via_visibility()`), so the existing Vias/Conns button
controls them exactly as it controls the abstract junction markers.

---

## Toggle behaviour

`_toggle_detailed()` flips `self._detailed_mode` and calls `_refresh_highlight()`.

`_refresh_highlight()` checks `_detailed_mode` to decide which artist registry to
apply highlight/dim/visibility rules to:

| State | Active registry | Grid rails |
|---|---|---|
| `_detailed_mode = False` | `_bundle_artists` (abstract) | hidden |
| `_detailed_mode = True` | `_detailed_bundle_artists` (bit-wires) | visible |

The inactive registry is hidden in bulk when the mode switches; the active
registry is then styled by the normal highlight/layer/bundle rules.

---

## CLI integration

In `buda_cli.py`, the `visualize` command calls
`viz.draw_detailed_tracks(detailed_result, routing_grid, layers)` immediately
after `draw_nuts_tracks` whenever `self.detailed_result` is not `None`.  The
button is visible only when detailed data is present; it is hidden otherwise.

```
visualize
  └─ draw_blocks()
  └─ draw_congestion_map(...)    # if planner ran
  └─ draw_hanan_grid()
  └─ draw_nuts_tracks(nuts_result)           # always when nuts ran
  └─ draw_detailed_tracks(detailed_result,   # only when dnuts ran
                          routing_grid,
                          layers)
  └─ show()
```

---

## Data flow

```
DetailedNUTSResult.net_segments   ──▶  _detailed_bundle_artists  (bit-wire lines)
RoutingGridStack (per layer)       ──▶  _grid_rail_artists        (stripe patches)
LayerStack (H/V direction map)     ──▶  layer_is_h dict (H→Y axis, V→X axis)
```

`draw_detailed_tracks` is idempotent: calling it again replaces all previous
detailed artists cleanly.

---

## Artist management

| Collection | Content | Registered under |
|---|---|---|
| `_bundle_artists` | Abstract NUTS bus bars + interval bands | bundle_id (existing) |
| `_detailed_bundle_artists` | Bit-wire `NetSegment` lines | bundle_id |
| `_grid_rail_artists` | Track rail stripe `PatchCollection`s, one per (layer, kind) | (none — not per-bundle) |

`_register_detailed(bid, artist, ...)` mirrors `_register` but populates
`_detailed_bundle_artists` instead of `_bundle_artists`.

Grid rail artists are `PatchCollection`s stored in `_grid_rail_artists`, built
lazily on the first `[Tracks]` enable and toggled by `_toggle_tracks` /
`_toggle_detailed` directly, bypassing the per-bundle highlight machinery
(layer visibility still dims them via `_refresh_highlight`'s alpha gate).
