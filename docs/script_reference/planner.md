# BUDA Script Reference — Stage 3 — Global router / planner

Topology selection and layer assignment: `set_planner_param`, `run_planner` (incl. `hier` and `post_nuts` modes), `select_topology`, `select_topologies`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 3 — Global router / planner

### `set_planner_param`

```
set_planner_param <name> <value>
```

Tune a global planner cost coefficient. Takes effect at the next `run_planner`:
each run seeds a fresh planner from all values set so far, so knobs may be
adjusted between runs to re-plan with different weights.

| Parameter | Default | Description |
|---|---|---|
| `kCong` | `1.0` | Congestion cost coefficient. Multiplies the overflow ratio `max(0, usage+eff_width−cap)/cap` for each channel band (zero when the segment fits). Note: overflow is gated as a hard constraint first (see `run_planner`); `kCong` only arbitrates among candidates when overflow is genuinely unavoidable, or prices residual soft pressure. |
| `kSpan` | `0.001` | Span-mismatch cost coefficient. Multiplies the excess span outside a layer's `[span_min, span_max]` window. Guides long segments to higher metal and short stubs to lower metal. |
| `base_cost_non_top` | `0.5` | Penalty per segment for using a non-`TOP` layer, scaled by segment span (see `base_span_ref`). Keeps the default preference on top layers without hard-blocking lower ones. |
| `base_span_ref` | 25% of the larger Hanan grid extent | Span at which a segment pays the full `base_cost_non_top`; shorter segments pay proportionally less (`× span/base_span_ref`). Short stubs therefore drop to lower layers when TOP bands saturate instead of detouring on TOP — preserving TOP capacity for long trunks. |
| `kWL` | `0.001` | Wirelength cost per layout unit, added to the topology score. Steers equal-congestion choices toward shorter topologies, so a detour wins only when it avoids real congestion. |
| `kBalance` | `0.01` | TOP-layer load-balancing weight. Adds `kBalance × (layer's committed load / max same-direction layer load)` to each candidate `TOP` layer's segment score, biasing an equal-cost segment toward the **less-loaded** of the same-direction TOP layers. Without it, equal-cost ties (e.g. on TOP layers with no span window, where span/base costs are 0) break toward the highest metal, piling every H segment on the top H layer and every V segment on the top V layer — the over-subscription that drives NUTS track overlaps. `LOW` layers don't compete (they carry `base_cost_non_top`). Set `0` to disable balancing and restore the highest-metal tie-break. Effective range is small: the useful plateau is roughly `[0.005, 0.015]`; above it, over-balancing starts pushing buses onto LOW layers. |
| `kHeight` | `0.05` | Layer-height cost for **short** segments on `TOP` layers — the mirror image of the span-scaled `base_cost_non_top`. Adds `kHeight × height_rank × max(0, 1 − seg_span/base_span_ref)` where `height_rank` is the layer's index among the same-direction TOP layers ascending (lowest TOP metal = 0). A short stub pays per rank to climb the stack (each rank up is a taller via stack for no benefit), so it prefers the **lowest feasible TOP layer**; a long trunk (`span ≥ base_span_ref`) pays nothing and keeps the TOP-most trunk preference. Deliberately above the `kBalance` tie-noise (≤ 0.01) so the steering wins ties, and far below `base_cost_non_top` and any real congestion overflow, so it never overrides capacity. Set `0` to restore the legacy highest-metal tie-break for short segments. Measured (corpus): `rnr/mix` abstract WL −5.4% and residual NUTS overlaps 3→1; `tc3a_flat`/`b4_bus_077` WL −0.3/−0.7%; flows with one TOP layer per direction byte-identical. |

**Example:**
```
set_planner_param kCong 2.0          # stronger congestion avoidance
set_planner_param kSpan 0.005        # stronger span preference
set_planner_param base_cost_non_top 0.1
set_planner_param kWL 0.01           # stronger preference for short routes
set_planner_param kBalance 0.0       # disable TOP-layer load balancing
set_planner_param kHeight 0.0        # legacy: short stubs float to the highest metal
```

---

### `run_planner`

```
run_planner [<iterations>] [signal_tracks]
```

Runs the global congestion-aware router. Bundles are processed widest-first
(fattest-first greedy). For each bundle:

1. Builds a Hanan-grid congestion map (one cut per channel per layer).
2. Scores every topology candidate — for each segment independently selects
   the best layer from the direction-appropriate set (H layers for H segments,
   V layers for V segments).  Segment score = `kCong·overflow/cap + kSpan·excess
   + base_cost_non_top·min(1, seg_span/base_span_ref) + kBalance·load_ratio
   + kHeight·height_rank·max(0, 1 − seg_span/base_span_ref)`,
   where `overflow = max(0, usage+eff_width−cap)` (zero when the segment fits) and
   the non-TOP penalty scales with segment span so short stubs offload to lower
   layers cheaply while long trunks stay on TOP (see `set_planner_param
   base_span_ref`).  The `kBalance` term spreads load across same-direction TOP
   layers: `load_ratio = layer's committed load / max same-direction layer load`
   (TOP layers only), so an otherwise-tied segment prefers the less-loaded TOP
   layer instead of always the highest metal (see `set_planner_param kBalance`).
   The `kHeight` term is its short-segment complement: a short stub also
   prefers the **lowest** same-direction TOP layer (each height rank up is a
   taller via stack), while long trunks pay nothing and keep the TOP-most
   preference (see `set_planner_param kHeight`).
   The congestion charge goes to the cheapest Hanan band the segment's slide
   interval can host the bus in (slide-aware lookup), not just the band at the
   interval centre.  Band capacity is clamped to the slide window's overlap
   with the band: demand confined to a sub-band window (slide bounds are
   usually not Hanan lines) is not priced against the whole band.
   The effective bus width per layer uses the measured per-bit channel cost
   when a track pattern is defined (`bits × unit_pitch/n_signals`, see
   `def_track_pattern`); otherwise the density model (`width × dilution`).
   Topology score = maximum segment score (weakest-link) `+ kWL·wirelength`.
3. Selects the topology with the lowest score. Ties broken by candidate index
   (shortest wirelength first, since candidates are sorted).
4. Commits the winning topology's per-segment layer choices to the running
   congestion state so subsequent bundles see the correct congestion.
5. Applies any architect-pinned selections from the `.json` sidecar file
   (see `visualize_topologies`): for a pinned bundle, only that one topology
   is scored (layer assignment is still computed).

**Overflow is a hard constraint** — an overflowing band cannot physically host
the bus, so NUTS would emit a real overlap. Each bundle walks an escalation
ladder (see [congestion_planner.md](../congestion_planner.md) for the full design):

1. `STRICT` — only candidates that fit their slide windows **and** are
   overflow-free compete on the soft costs above.
2. **Rip-up & replan** — if no candidate is overflow-free, earlier-committed
   bundles are ripped up one at a time, ranked by the demand they hold on the
   failing bundle's contended bands (the actual blocker first; zero-overlap
   victims skipped), and the pair is replanned; accepted only if both end up
   overflow-free.
3. `ALLOW_OVERFLOW` — overflow truly unavoidable: the least-cost candidate is
   committed with a `WARNING`.
4. `BEST_EFFORT` — no candidate even fits its slide windows (e.g. stale sidecar
   pins): committed anyway with a `WARNING` rather than dropping the bundle.

| Argument | Type | Default | Description |
|---|---|---|---|
| `iterations` | int | 5 | Reserved for planned PathFinder-style negotiated-congestion iterations (see [future/planner_ripup_extensions.md](../future/planner_ripup_extensions.md)); currently unused beyond the first pass. |
| `signal_tracks` | keyword | off | Charge band capacity in **signal-track count** instead of layout width (see below). |

**Signal-track band capacity (`signal_tracks`).** By default a Hanan band's
capacity is its geometric layout width. But DetailedNUTS places each bit on a
discrete SIGNAL track from the layer's `def_track_pattern` (power/ground/clock
slots are not usable), and the placeable count is a quantized fraction of the
width that also depends on the pattern's origin/phase and any `add_grid_override`.
So the planner can commit a bundle `overflow=0` while the band is actually short
of signal tracks → a silent DetailedNUTS open. Adding the `signal_tracks` keyword
makes the planner count the discrete SIGNAL tracks each band holds (honouring the
grid's keepouts, exactly as DetailedNUTS will) and charge capacity in track units,
so the shortfall surfaces as **overflow at planning time** and the escalation
ladder below (STRICT → rip-up/replan) avoids it up front.

- Works on both `run_planner` and `run_planner hier`, e.g. `run_planner 5 signal_tracks`
  / `run_planner hier 5 signal_tracks`.
- **Opt-in:** without the keyword the plan is byte-identical to before. Requires at
  least one `def_track_pattern` — requesting `signal_tracks` with none defined is a
  **hard error** (exit 1), not a silent fall-back to the width model, so a
  misconfiguration can't quietly plan with a different capacity model than asked
  for. Layers *individually* without a pattern keep the width model even when the
  keyword is on (only a stack with **no** pattern at all is the error).
- `set_planner_param track_cap_slack <n>` grants `n` extra signal tracks per band
  as a quantization slack (default 0 = exact integer accounting).
- Measured on `flow/rnr/mix.buda` (hier): the width plan yields 236 DetailedNUTS
  opens; `run_planner hier 5 signal_tracks` yields **162** with no `ripup_reroute`,
  because the planner stops over-filling capacity-short bands.
- The slide-window sub-band clamp counts tracks within the clamped interval too,
  so it is exact end-to-end. See `docs/internal/planner_signal_track_capacity.md`.

**Output:** Prints per-bundle selection: topology type, assigned per-segment
layers, ` [pinned]`/` [replanned]` tags, and the raw overflow in layout units
(0 unless a fallback mode committed). Rip-ups print
`[Planner] Rip-up: replanned bundle <P> to free capacity for bundle <B>:`
followed by the victim's new selection line; fallback modes print
`[Planner] WARNING: Bundle <id>: no overflow-free candidate (even after
rip-up); …` or `…: no candidate fits its slide windows …` respectively.

**Side effects:**
- Creates a `GlobalRouter` object accessible to `visualize` for congestion
  overlay drawing.
- Reads and applies `<script>.json` sidecar if it exists.

**Example:**
```
run_planner 5
```

---

### `run_planner hier`

```
run_planner hier [<iterations>] [signal_tracks]
```

Hierarchy-aware variant of `run_planner` for the HBundle pipeline. Requires an
open BDB, `run_hier_bundler`, and `generate_hier_topologies`. Steps:

1. Applies architect-pinned sidecar selections to the pre-expansion bundles.
2. **Expands** each cell-level HBundle into one wrapper per cell instance, with
   candidates offset to absolute coordinates by the instance origin.
3. Assigns each wrapper `priority = -(level·10000 + n_candidates)` and runs the
   congestion planner sorted by `(priority DESC, width DESC)` — depth-0 globals
   first, then within each level the least-flexible bundles first.

Two mechanisms manage the local-vs-global competition this ordering creates
(see [HIER_PLANNER.md](../HIER_PLANNER.md) §7 and
[congestion_planner.md](../congestion_planner.md)):

- **Cell-interior demand reservation.** Each expanded cell-local wrapper parks
  its effective bus width as virtual usage on the TOP-layer bands inside its
  instance bbox before planning starts; the reservation is released right
  before the bundle's own turn. Earlier globals avoid a cell-interior band
  only when it cannot hold both bundles — a "leave room" constraint, not a
  keep-out.
- **Per-level summary.** When bundles span multiple depth levels, the planner
  prints a per-level report after planning:

  ```
  [Planner] Level summary:
    D0: 1 bundles  strict:1  layers{M6:1}
    D1: 1 bundles  strict:1  layers{M5:1 M6:1}
  ```

  Stage counts other than `strict:` (`ripup:` / `overflow:` / `best_effort:`)
  flag levels losing the competition or under-capacity regions.

The span-scaled non-TOP penalty (`base_span_ref`) complements both: short
cell-local stubs drop to lower layers when TOP saturates instead of detouring
on TOP, preserving TOP capacity for long global trunks.

**Side effects:** Replaces the session bundle list with the expanded
per-instance wrappers (see `dump_hbundles expanded`); subsequent `run_nuts` /
`check_design` / `visualize` operate on the expanded set.

**Example:**
```
run_hier_bundler depth 1
generate_hier_topologies
run_planner hier 5
```

Demonstrated end-to-end by `flow/hbundles/08_cross_level.buda` and
`flow/hbundles/09_local_global_compete.buda`.

---

### `select_topology`

```
select_topology <bundle_id> <topo_id>
```

Manually pin a specific topology candidate for a given bundle by its numeric bundle ID and topology candidate ID (1-based index). This manually overrides the planner's selection.

If the planner has already run, layer assignment is automatically re-run with
the pin in place (logged with a `[pinned]` marker), so per-segment layers
always describe the pinned topology's segment list. Pins set before
`run_planner` are honored when it runs.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Numeric ID of the bundle (e.g. `2`). |
| `topo_id` | int | 1-based ID of the topology candidate to pin (e.g. `2` for the second topology). |

**Example:**
```buda
# Pin topology candidate 2 for bundle 2
select_topology 2 2
```

---

### `select_topologies`

```
select_topologies <bundle_ids> <topo_id> [<bundle_ids> <topo_id> ...]
```

Batch pin multiple bundles to specific topology candidates. Bundle IDs within a group can be comma-separated or specify ranges (e.g. `5-9`).

| Argument | Type | Description |
|---|---|---|
| `bundle_ids` | string | Comma-separated list and/or ranges of numeric bundle IDs (e.g. `1,5-9,11`). |
| `topo_id` | int | 1-based ID of the topology candidate to pin for the preceding group. |

**Example:**
```buda
# Pin bundles 1, 5 through 9, 11, and 15 through 19, and 22 to topology 3
# Pin bundles 2 through 4, 10, 12 through 14, 20, 21, and 23 through 30 to topology 1
select_topologies 1,5-9,11,15-19,22 3 2-4,10,12-14,20,21,23-30 1
```

---

---

## Stage 4c — Post-NUTS stub layer reassignment

### `run_planner post_nuts`

```
run_planner post_nuts [top] [V [<short_v> [<long_v>]]] [H [<short_h> [<long_h>]]]
```

Runs a second planner pass **after** `run_nuts` that resolves **channel pin
conflicts** — local stub-on-stub overlaps at block faces that the global
planner cannot predict before concrete track positions are known.

Both V and H directions can be reassigned in a **single invocation**; only one
NUTS re-run is performed regardless of how many directions are specified.

#### The channel pin conflict problem

The global planner (Stage 3) assigns every bundle to a single vertical and
horizontal layer and selects a topology, but it cannot see how many stubs from
adjacent blocks will compete for the same narrow perpendicular interval on the
same layer. When many blocks line up along a channel wall, their stubs pack
into the same Hanan-cell column on M5, exceeding its capacity and causing NUTS
violations.

#### Resolution strategy

For each requested direction, stubs are redistributed across the available
layers for that direction using stub span length as a proxy for routing
distance:

| Stub span (routing-direction extent) | Target layer |
|---|---|
| `< short_thresh` | Lowest layer in scope (e.g. M3, or M5 in `top` mode) — short stubs close to the block face stay on the nearest in-scope metal |
| `> long_thresh`  | Highest layer in scope (e.g. M7) — long stubs crossing the full channel use the highest available metal |
| Between thresholds | Unchanged — stays on the planner-assigned layer |

The set of layers in scope is **all** layers of that direction by default, or
only the `TOP` layers when the `top` modifier is given (see below).

After all reassignments, a single full NUTS re-run makes all layers consistent
with the new assignments.

#### Syntax

| Token | Description |
|---|---|
| `top` | Optional leading modifier. Restrict reassignment to the **`TOP`** layers of each direction, so short stubs land on the next-highest TOP layer rather than a `LOW` escape layer. Applies to every direction in the command. |
| `V` | Enable V-stub reassignment. Up to two numeric thresholds may follow. |
| `H` | Enable H-stub reassignment. Up to two numeric thresholds may follow. |
| `<short>` | Stubs shorter than this move to the lowest layer in scope. |
| `<long>` | Stubs longer than this move to the highest layer in scope. |

**`top` modifier.** Without it, a direction's lowest layer may be a `LOW`
layer, so short stubs can be pushed down onto it — and a `LOW` layer cannot
route over a cell, so its bands are often track-starved. `top` keeps the
redistribution within the TOP layers (e.g. V → M5 short / M7 long, H → M4 short /
M6 long), reserving the over-subscribed top metal for long hauls while spreading
short stubs onto the next TOP tier. If a direction has **fewer than two TOP
layers**, that direction is skipped (it never falls back to a `LOW` layer).

**Default thresholds** (used when a letter is given without explicit values):

| Direction | short | long |
|---|---|---|
| V | 80.0 | 200.0 |
| H | 150.0 | 400.0 |

**Bare `run_planner post_nuts`** (no direction letter) → V with defaults (80 / 200). Backward compatible with the previous two-argument form.

#### Notes

- Requires `run_nuts` to have been called first.
- Bundles are classified by the **longest** segment span within the bundle for
  each direction, so all stubs in a bundle move together to the same new layer.
- A single NUTS re-run is performed after all direction reassignments; any
  previous `run_nuts_on_layer` overrides are superseded.
- Thresholds are in layout units. Inspect the NUTS log or use `visualize` to
  estimate typical stub lengths for your floorplan.

#### Examples

```buda
# V only — backward-compatible forms
run_planner post_nuts               # V defaults (80 / 200)
run_planner post_nuts V             # same
run_planner post_nuts V 100 280     # custom V thresholds

# H only
run_planner post_nuts H             # H defaults (150 / 400)
run_planner post_nuts H 120 350     # custom H thresholds

# Both directions in one pass (single NUTS re-run)
run_planner post_nuts V 80 200 H 150 400
run_planner post_nuts V H           # both with defaults

# top mode — keep stubs on TOP layers (no LOW fallback)
run_planner post_nuts top V H               # V → M5/M7, H → M4/M6
run_planner post_nuts top V 100 280 H 150 400
```

#### Typical script pattern (congested channel)

```buda
run_planner 5
run_nuts 2.0
run_planner post_nuts V 100 280 H 150 400   # redistribute stubs to M3/M5/M7
visualize
```

---
