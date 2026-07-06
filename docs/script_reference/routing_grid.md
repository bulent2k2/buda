# BUDA Script Reference — Stage 8 — Routing grid

Physical track patterns consumed by Detailed NUTS: `def_track_pattern`, `add_grid_override`, `report_overhead`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 8 — Routing grid

Stage 8 defines the physical track structure of each metal layer. Commands in this stage can appear anywhere in the script — they only take effect when `run_detailed_nuts` (Stage 9) is called. See [docs/routing_grid.md](../routing_grid.md) for the full design.

### `def_track_pattern`

```
def_track_pattern <layer_id> <origin> <type1> <w1> <sp1> [<type2> <w2> <sp2> …]
```

Define the repeating track pattern for a layer. The pattern tiles from `origin` outward across the full layer extent.

| Argument | Type | Description |
|---|---|---|
| `layer_id` | int | Layer ID as registered with `def_layer` |
| `origin` | float | Anchor position of the first unit in layout units (use `0.0` to align with chip origin) |
| `type` | string | Track type: `POWER`, `GROUND`, `CLOCK`, `SHIELD`, `SIGNAL`, or `CUSTOM` |
| `w` | float | Track width in layout units |
| `sp` | float | Space after this track (gap to the next slot), in layout units |

Each `<type> <w> <sp>` triple defines one slot in the repeating unit. Slots are listed in order from low to high perpendicular position. The unit pitch is the sum of all `(w + sp)` values.

**Notes:**
- Each layer may have at most one global pattern. Calling `def_track_pattern` a second time on the same layer replaces the existing pattern.
- The `origin` should be consistent across layers and with the power-grid intent — mismatched origins produce phase drift across layers.
- At least one `SIGNAL` slot is required for `run_detailed_nuts` to place any bit-wires.
- Calling `run_detailed_nuts` without a defined pattern for a bus segment's layer causes that bus to be counted as unplaced.
- Defining a pattern also syncs the layer's **dilution factor** and **per-bit
  channel cost** (`unit_pitch / n_signal_slots`) into the layer stack. The
  per-bit cost supersedes `def_layer`'s overhead% for planner and NUTS
  effective bus widths: a 16-bit bus on a 34-pitch/8-signal layer is priced
  at 16 × 4.25 = 68 units, matching what detailed NUTS can actually place.

**Example:**
```buda
# 4 signal tracks per 14-unit period, surrounded by POWER/GROUND rails
def_track_pattern 4 0.0  POWER 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0  GROUND 2.0 1.0  SIGNAL 1.0 1.0  SIGNAL 1.0 1.0

# M3 (vertical): denser signal pitch, CLOCK rail every 8 tracks
def_track_pattern 3 0.0  CLOCK 1.0 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5
```

---

### `add_grid_override`

```
add_grid_override <layer_id> <x1> <y1> <x2> <y2> <origin> <type1> <w1> <sp1> [<type2> <w2> <sp2> …]
```

Override the track pattern for a rectangular region on a layer. Useful when a floorplan region (e.g. an SRAM macro) uses a different power-grid pitch than the rest of the chip.

| Argument | Type | Description |
|---|---|---|
| `layer_id` | int | Layer to override |
| `x1 y1 x2 y2` | int | Bounding box of the override region (Hanan-grid-aligned) |
| `origin` | float | Anchor for the local pattern within this region |
| `type w sp …` | — | Same slot format as `def_track_pattern` |

First-match wins: if a point falls in multiple override regions, the first one defined takes precedence.

**Notes:**
- Power and clock tracks are broken at region boundaries — a DRC gap at the seam is accepted.
- The global pattern (from `def_track_pattern`) still applies outside the override region.
- Override regions and the global pattern are independent objects; each has its own `origin`.

**Example:**
```buda
# Dense SRAM macro at (200,0)-(400,500) uses half-pitch POWER/SIGNAL layout
add_grid_override 4  200 0 400 500  0.0  POWER 1.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5  GROUND 1.5 0.5  SIGNAL 0.5 0.5  SIGNAL 0.5 0.5
```

---

### `report_overhead`

```
report_overhead
```

Compares the overhead percentage set in each `def_layer` command against the overhead implied by the layer's `def_track_pattern`. Call this after defining track patterns to verify that the two values are consistent — a mismatch means abstract NUTS (which uses the `def_layer` dilution) and detailed NUTS (which uses the actual track geometry) will disagree on how much space a bus occupies.

For each layer the command prints:

| Column | Meaning |
|---|---|
| `def_layer%` | Overhead set in `def_layer` (or `(not set)` if omitted / zero) |
| `actual%` | Overhead derived from the track pattern: `(1 − signal_density) × 100` |
| `dilution` | `unit_pitch / signal_width_sum` from the track pattern |
| `status` | `OK` if the two values agree within 0.05%, otherwise `MISMATCH` |

For any layer with a mismatch, the command also prints a corrected `def_layer` line ready to paste back into the script.

**Example output** for a layer with 33.33% set in `def_layer` but a track pattern whose actual overhead is 55.56%:
```
[report_overhead] Layer overhead analysis:
  Layer       def_layer%   actual%  dilution  status
  ---------- ----------- --------- ---------  ------
  M4              33.33%    55.56%    2.2500  MISMATCH (diff=22.23%)
    -> suggested: def_layer 4 M4 H TOP 55.56
  M5              33.33%    55.56%    2.2500  MISMATCH (diff=22.23%)
    -> suggested: def_layer 5 M5 V TOP 55.56
```

**Notes:**
- `report_overhead` is read-only — it does not change any layer settings.
- Only the global pattern per layer is checked; per-region overrides (`add_grid_override`) are not compared.
- Layers with no track pattern defined show `(no pattern)` in the `actual%` column.

---
