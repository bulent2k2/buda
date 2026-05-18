# Detailed NUTS Design (Stage 9)

Stage 9 expands each abstract `TrackSegment` from Stage 4 into N concrete `NetSegment`s — one per bit-wire — snapped to exact signal-track positions provided by the `RoutingGridStack` (Stage 8).

---

## Role in the Pipeline

```
Stage 4 (Abstract NUTS)
    NUTSResult.segments  ← one TrackSegment per BusSegment
          │
          ▼
Stage 9 (Detailed NUTS)
    DetailedNUTSResult.net_segments  ← one NetSegment per bit, per segment
```

After Stage 4, each bus segment has a floating-point `track_position` (its abstract perpendicular centre) and a `width` (total physical reservation including power-grid dilution). Stage 9 ignores those real-valued placements and instead calls `signal_tracks_in()` on the routing grid to enumerate exactly which signal track centres fall inside the Hanan-cell interval, then assigns each bit of the bus to one of those tracks.

---

## Data Types

### `BusSegment` (input)

Converted from a Stage 4 `TrackSegment` before the engine runs.

| Field | Type | Description |
|---|---|---|
| `bundle_id` | int | Bundle identifier (carried through from Stage 1) |
| `seg_idx` | int | Segment index within the bundle's topology |
| `layer` | int | Metal layer ID (must be registered in the `RoutingGridStack`) |
| `span_lo`, `span_hi` | double | Routing-direction extent (propagated to output `NetSegment`s) |
| `interval_lo`, `interval_hi` | double | Perpendicular Hanan-cell constraint — only tracks within this range are candidates |
| `bit_width` | int | Number of signal tracks needed (= number of bits in the bus segment) |
| `bit_order` | string | `"LO_HI"` or `"HI_LO"` — see Bit Ordering below |
| `timing_critical` | bool | If true, all bits must be on contiguous signal tracks (no power/ground between them) |

### `NetSegment` (output)

One bit-wire, fully resolved to a physical track.

| Field | Type | Description |
|---|---|---|
| `bundle_id`, `seg_idx` | int | Propagated from the parent `BusSegment` |
| `bit_index` | int | Zero-based position within the bus (0 = first bit per `bit_order`) |
| `track_position` | double | Centre of the signal track in layout units |
| `width` | double | Track width from the `TrackSlot` (not from the `BusSegment`) |
| `layer`, `span_lo`, `span_hi` | — | Propagated from the parent `BusSegment` |

### `DetailedNUTSResult`

| Field | Type | Description |
|---|---|---|
| `net_segments` | list of NetSegment | All placed bit-wires, across all input `BusSegment`s |
| `num_unplaced` | int | Total number of bits that could not be placed (all-or-nothing per bus — see below) |

---

## Algorithm

For each `BusSegment` in the input list:

1. **Look up the layer grid.** If the layer has not been registered in the `RoutingGridStack`, skip the bus and add `bit_width` to `num_unplaced`.

2. **Enumerate signal tracks.** Call `signal_tracks_in(x, interval_lo, interval_hi)` where `x = (span_lo + span_hi) / 2`. Returns a sorted list of `(centre, TrackSlot)` pairs for all SIGNAL-type tracks whose centre lies in `[interval_lo, interval_hi]`.

3. **Feasibility check.** If the number of available signal tracks is less than `bit_width`, the entire bus is **unplaced** (`num_unplaced += bit_width`; no `NetSegment`s are emitted).

4. **Select tracks.**
   - Without `timing_critical`: take the first `bit_width` tracks (LO_HI) or last `bit_width` tracks (HI_LO) from the sorted list.
   - With `timing_critical`: search for a *contiguous window* (see below). If none found, the bus is unplaced.

5. **Emit `NetSegment`s.** One per selected track, with `bit_index = 0, 1, …, bit_width-1`.

---

## Bit Ordering

### `LO_HI` (default)

`bit_index=0` is assigned to the **lowest** (most negative perpendicular position) available signal track. Bit indices increase toward higher tracks.

```
Signal tracks: [3.5, 5.5, 10.5, 12.5]  bit_width=2  →  bit_index=0→3.5,  bit_index=1→5.5
```

### `HI_LO`

`bit_index=0` is assigned to the **highest** available signal track. Bit indices increase toward lower tracks.

```
Signal tracks: [3.5, 5.5, 10.5, 12.5]  bit_width=2  →  bit_index=0→12.5, bit_index=1→10.5
```

---

## Timing-Critical Placement

When `timing_critical=True`, all bits of the bus must occupy *contiguous* signal tracks — no POWER, GROUND, CLOCK, or SHIELD track may have its centre between two consecutive bits. This constraint is necessary for timing closure on high-frequency buses where skew from unequal RC loading must be minimised.

### Contiguity definition

Two adjacent signal tracks (by perpendicular position) are **contiguous** if no non-SIGNAL track centre from `tracks_in_range(interval_lo, interval_hi)` falls strictly between them.

Example using the standard power/signal pattern (unit\_pitch = 14):

| Signal pair | Non-SIGNAL between? | Contiguous? |
|---|---|---|
| (3.5, 5.5) | POWER@1.0 is below 3.5 — no | **yes** |
| (5.5, 10.5) | GROUND@8.0 is between them | **no** |
| (10.5, 12.5) | nothing between | **yes** |

Contiguous windows of size 2: `(3.5, 5.5)` and `(10.5, 12.5)`.  
No contiguous window of size 3 or larger exists in one unit.

### Window search

- **LO_HI**: scan from the lowest signal track; return the first window of `bit_width` consecutive contiguous tracks.
- **HI_LO**: scan from the highest signal track; return the last window (bit_index=0 → highest track in the window).

If no valid window exists, `num_unplaced += bit_width` and no `NetSegment`s are emitted for that bus.

---

## All-or-Nothing Semantics

When a bus cannot be placed (too few signal tracks, or no valid contiguous window), **all** of its bits are counted as unplaced and **no** `NetSegment`s are emitted for that bus. Partial placement is never produced — a partially routed bus would silently lose bits, which is harder to diagnose than an explicit unplaced count.

---

## Pattern Tiling Across Multiple Units

`signal_tracks_in` delegates to `TrackPattern::tracks_in_range(lo, hi)`, which tiles the pattern from `origin` outward to cover the full interval. If the interval spans multiple tiling units, all signal tracks from all units are returned in sorted order.

```
Interval [0, 28] with unit_pitch=14:
  Unit 0 signal tracks: 3.5, 5.5, 10.5, 12.5
  Unit 1 signal tracks: 17.5, 19.5, 24.5, 26.5
  Total: 8 signal tracks → can place up to an 8-bit bus
```

---

## Connection to Other Stages

### Receives from Stage 4

`NUTSResult.segments` (list of `TrackSegment`) is converted to `BusSegment` objects:
- `bundle_id`, `seg_idx`, `layer`, `span_lo`, `span_hi`, `interval_lo`, `interval_hi` are direct field copies
- `bit_width` = `round(TrackSegment.width)` (abstract NUTS width → number of bits)

### Receives from Stage 8

`RoutingGridStack` provides the per-layer track patterns. The detailed NUTS engine is constructed with a reference to the stack and calls `signal_tracks_in` on it for each segment.

### Feeds Stage 7 (Visualizer, planned)

`draw_detailed_tracks(detailed_result)` will draw individual bit-wire lines at their `track_position`s with per-type visibility toggles.

---

## `.buda` Command

See [BUDA_SCRIPT_REFERENCE.md](BUDA_SCRIPT_REFERENCE.md#stage-9--detailed-nuts) for full syntax.

```buda
# Snap all bus segments to concrete signal tracks (LO_HI ordering by default)
run_detailed_nuts

# Force HI_LO ordering for all buses
run_detailed_nuts hi_lo
```

---

## Testing

Tests live in `test/tests/test_detailed_nuts.py` (15 unit tests) and BDD scenarios in `test/tests/features/detailed_track_assignment.feature` (12 scenarios). Key cases:

| Test | What it checks |
|---|---|
| `test_lo_hi_two_bits_assigned_from_lowest_signal_tracks` | LO_HI ordering; bit_index=0 lands on lowest track |
| `test_hi_lo_two_bits_assigned_from_highest_signal_tracks` | HI_LO ordering; bit_index=0 lands on highest track |
| `test_power_and_ground_track_centres_never_assigned` | POWER and GROUND centres not in output |
| `test_narrow_interval_returns_subset_of_signal_tracks` | Interval clipping works correctly |
| `test_timing_critical_selects_contiguous_pair_at_lo_end` | Finds first contiguous window |
| `test_timing_critical_skips_non_contiguous_and_finds_next_window` | GROUND between tracks → skip, find next window |
| `test_timing_critical_with_no_valid_window_leaves_bus_unplaced` | No window of size 3 → num_unplaced = 3 |
| `test_num_unplaced_when_more_bits_than_signal_tracks` | 6 bits, 4 tracks → all 6 unplaced |
| `test_two_bus_segments_expanded_independently` | Two buses with non-overlapping intervals |
| `test_interval_spanning_two_units_yields_eight_signal_tracks` | Tiling across two pattern units |

---

## Implementation Files

| File | Contents |
|---|---|
| `buda_system_v2/src/detailed_nuts.h` | `BusSegment`, `NetSegment`, `DetailedNUTSResult`, `DetailedNUTSEngine` declarations |
| `buda_system_v2/src/detailed_nuts.cpp` | Full algorithm: LO_HI/HI_LO ordering, contiguity check, window search |
| `buda_system_v2/src/bindings.cpp` | pybind11 bindings for all four types |
