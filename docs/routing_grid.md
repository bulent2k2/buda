# Routing Grid Design (Stage 8)

Stage 8 defines the **physical track structure** of each metal layer — which track positions are POWER, GROUND, CLOCK, SHIELD, or SIGNAL — and exposes this information to both the abstract NUTS solver (for power-grid dilution) and the detailed NUTS solver (for exact signal-track enumeration).

---

## Motivation

The abstract NUTS solver (Stage 4) works with floating-point bus widths and knows nothing about the repeating POWER/SIGNAL/GROUND pattern that physically fills each metal layer. Without this information two problems arise:

1. **Under-reservation**: A bus of width 10 units placed on a layer where only 40% of the area is routable signal track will physically require 25 units, causing DRC violations.
2. **Misalignment**: Bit-wire placement (Stage 9) needs the exact centre coordinates of signal tracks, not just a nominal bus centre.

Stage 8 solves both by providing a `TrackPattern` per layer that encodes the repeating unit cell and lets any stage query which tracks are available at any perpendicular position.

---

## Data Model

### `TrackSlot`

One track position within a repeating unit.

| Field | Type | Description |
|---|---|---|
| `type` | string | Track kind: `POWER`, `GROUND`, `CLOCK`, `SHIELD`, `SIGNAL`, or `CUSTOM` |
| `label` | string | Human-readable label, e.g. `"VDD"`, `"GND"`, `"CLK1"` |
| `width` | double | Track width in layout units |
| `space_after` | double | Gap between this track and the next slot in the unit |

### `TrackPattern`

A repeating unit that tiles an entire layer extent.

| Field / Method | Type | Description |
|---|---|---|
| `origin` | double | Anchor position of the first unit (from chip origin 0,0). Ensures all Hanan channels tile on the same phase across the chip. |
| `slots` | list of TrackSlot | The ordered track types within one repeating unit |
| `unit_pitch()` | → double | Sum of all `width + space_after` across all slots in one unit |
| `signal_density()` | → double | Sum of SIGNAL widths / `unit_pitch` |
| `dilution_factor()` | → double | `1.0 / signal_density` — multiply abstract bus width by this to get physical reservation |
| `tracks_in_range(lo, hi)` | → list of (position, slot) | All track centres in `[lo, hi]`, across as many tiled units as needed |

**Track centre calculation**: within a unit starting at `unit_start`, the centre of slot `i` is:

```
pos = unit_start + sum(width[j] + space_after[j]  for j < i)
centre = pos + width[i] / 2
```

The unit is tiled from `origin` outward until no slot centre falls within `[lo, hi]`. The tiling step starts one unit before `lo` to be safe with floating-point boundaries.

### `PatternOverride`

A region-scoped pattern that shadows the layer's global pattern.

| Field | Description |
|---|---|
| `region` | Integer `Rect` aligned to Hanan grid lines |
| `pattern` | Local `TrackPattern` with its own `origin` |

Overrides allow different power grid pitches in different floorplan regions (e.g. a dense SRAM macro vs. a logic block). Power / clock tracks are **broken** at region boundaries — a DRC gap is accepted at the seam.

First-match wins: the first override whose region contains a query point is used; if none match, the global pattern is returned.

### `RoutingGrid`

Per-layer grid object.

| Method | Description |
|---|---|
| `effective_pattern_at(x, y)` | Returns the first matching override for point `(x, y)`, else the global pattern |
| `signal_tracks_in(x, lo, hi)` | Calls `effective_pattern_at(x, lo)` then filters `tracks_in_range(lo, hi)` to SIGNAL-type slots only |

### `RoutingGridStack`

Registry of all per-layer `RoutingGrid` objects.

| Method | Description |
|---|---|
| `define_layer(layer_id, pattern)` | Set the global pattern for a layer |
| `add_override(layer_id, x1, y1, x2, y2, pattern)` | Add a region-scoped override |
| `get_layer_grid(layer_id)` | Return the mutable `RoutingGrid` for a layer (throws if not defined) |
| `has_layer(layer_id)` | Return true if the layer has been defined |

---

## Example: Standard Signal/Power Pattern

A common 1:2 power/signal ratio pattern:

```
[POWER(w=2, sp=1), SIGNAL(w=1, sp=1), SIGNAL(w=1, sp=1),
 GROUND(w=2, sp=1), SIGNAL(w=1, sp=1), SIGNAL(w=1, sp=1)]
```

Calculations:

| Quantity | Value |
|---|---|
| `unit_pitch` | (2+1)+(1+1)+(1+1)+(2+1)+(1+1)+(1+1) = 14 |
| `signal_density` | (1+1+1+1) / 14 ≈ 0.286 |
| `dilution_factor` | 14/4 = 3.5 |

Signal track centres (origin=0): **3.5, 5.5, 10.5, 12.5**

An abstract bus of width 10 units would physically occupy 35 units on this layer.

---

## Connection to Other Stages

### Stage 4 (Abstract NUTS)

The abstract NUTS solver can use `dilution_factor` to inflate bus widths before solving:

```
physical_reservation = abstract_width × dilution_factor
```

This ensures that the perpendicular track allocation (the NUTS `track_position`) reserves enough space for all the power and clock tracks the bus will cross — even before the individual signal tracks are enumerated.

### Stage 9 (Detailed NUTS)

Stage 9 calls `signal_tracks_in(x, interval_lo, interval_hi)` on each bus segment's layer grid to enumerate the actual signal track positions and snap each bit-wire to a concrete track centre.

---

## `.buda` Commands

See [BUDA_SCRIPT_REFERENCE.md](BUDA_SCRIPT_REFERENCE.md#stage-8--routing-grid) for full syntax.

```buda
# Define a layer's repeating track pattern
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0

# Override the pattern in a specific floorplan region
add_grid_override 4  200 0 400 500  0.0  POWER 2.0 0.5  SIGNAL 1.0 0.5  SIGNAL 1.0 0.5  GROUND 2.0 0.5
```

---

## Testing

Tests live in `test/tests/test_routing_grid.py` (21 unit tests) and the BDD scenarios in `test/tests/features/routing_grid.feature` (12 scenarios). Key cases:

- `unit_pitch` and `signal_density` arithmetic
- `tracks_in_range` tiling across one and two units, with non-zero origin
- Narrow intervals that exclude some track centres
- `effective_pattern_at` with and without overrides
- `signal_tracks_in` filtering to SIGNAL-only

---

## Implementation Files

| File | Contents |
|---|---|
| `src/routing_grid.h` | `TrackSlot`, `TrackPattern`, `PatternOverride`, `RoutingGrid`, `RoutingGridStack` declarations |
| `src/routing_grid.cpp` | Implementation of all methods |
| `src/bindings.cpp` | pybind11 bindings for all five types |
