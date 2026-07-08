# BUDA Script Reference — Verification & visualisation

Connectivity audits and interactive inspection: `check_connectivity`, `dump_topologies`, `visualize`, `visualize_topologies`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Verification commands

### `check_connectivity`

```
check_connectivity [stage] [all]
```

Verify signal/bus electrical connectivity and report any open connections, missing stubs, or track routing violations. The `nuts` and `dnuts` stages also
flag layer-direction violations: a segment (or its bit wires) assigned to a
layer whose routing direction does not match the segment's orientation is
reported as unbuildable.

| Argument | Type | Default | Description |
|---|---|---|---|
| `stage` | str | `dnuts` | Routing stage to verify: `topo` (topology candidates), `nuts` (abstract track sharing), or `dnuts` (detailed bit placement). |
| `all` | keyword | — | Checks all candidate topologies instead of just the selected one. Only applicable for the `topo` stage. Automatically enabled if no topology is selected yet. |

**Hierarchical design — missing-block warning:** When `check_connectivity` is called after `run_planner hier`, it additionally checks that every `connected_block_name` referenced in the selected topologies exists in the current floorplan. If any are missing:
```
  Warning: N block(s) referenced in topologies but not in floorplan: name1, name2, ...
  Hint: call 'add_blocks_from_bdb N skip' for all required depths.
```
This catches the common error of calling only `add_blocks_from_bdb 0` when depth-1 cell-level bundles also need `add_blocks_from_bdb 1 skip` (because they reference absolute paths like `proc_i/pa_i`). The check is only active when `run_planner hier` has been used (detected by `_hier_expansion_map` being non-empty).

**Example:**
```buda
# Check detailed NUTS placement for opens (typically at the end of script)
check_connectivity dnuts
```

---

## Visualisation commands

### `visualize`

```
visualize
```

Opens the interactive NUTS result viewer (matplotlib window). No arguments.

**What is shown:**
- Floorplan blocks (grey rectangles, always visible).
- Hanan grid (faint dashed lines).
- If `run_nuts` has been called: bus segments at their NUTS-assigned
  `track_position`s, coloured by layer, with faint interval-constraint bands.
- If `run_planner` has been called: congestion-map cut utilisation overlay.
- If `run_nuts` has *not* been called: topology segments at their nominal
  (pre-NUTS) coordinates.

**Interactive controls:**

| Action | Effect |
|---|---|
| Click a segment or terminal | Highlight that bundle; dim all others |
| Click the same bundle again, or click background | Clear highlight |
| Layer checkboxes (right panel) | Toggle per-layer visibility |
| ☑ All Layers button | Toggle all layers on/off |
| Bundle list (right panel) — left click | Highlight bundle |
| Bundle list — right click (on label) | Toggle bundle visibility |
| ☑ All Bundles button | Toggle all bundles on/off |
| Bundle list scroll ▲ / ▼ | Scroll bundle list |
| Solo button | Isolate highlighted bundle; hide all others |
| Next / Prev buttons | Walk through bundles sequentially |

**Sidecar:** topology selections saved from `visualize_topologies` are
preserved in `<script>.json` and loaded by the next `run_planner` invocation.

---

### `dump_topologies`

```
dump_topologies [<hint>] [--problems] [--conn]
```

Text (non-GUI) inspection of the candidate topologies generated for each bundle.
Run it after `generate_topologies` (or `generate_hier_topologies`). Read-only — it
never mutates session state, so it is safe to insert anywhere after topology
generation.

| Argument | Behaviour |
|---|---|
| *(none)* | Dump every bundle's candidate table, then an aggregate summary. |
| `<hint>` | Only bundles whose first net name starts with `<hint>`. |
| `--problems` | Only bundles with a flagged candidate (see below), plus the summary. |
| `--conn` | After each shown bundle's table, print a per-segment connectivity detail for the selected candidate (see below). |

Each candidate row prints: `idx`, `type`, `wl` (estimated / as-generated
wirelength), `wl[lo..hi]` (the routing **WL envelope** the candidate's slide/span
DOF permit — `lo` is the tightest routing, i.e. the total span minimized jointly
over the slide box; `hi` a loose outer bound — a wide envelope means the candidate
gives NUTS a lot of freedom to shorten; see
[`report_wirelength`](nuts.md)), `segs` (segment count), `pass`
(`pass_through_count` — blocks the trunk crosses with no stub), `mslide` (minimum
perpendicular slide freedom across the candidate's ConnSegs, via `ConnTopology`;
`0` = pinched, `free` = unbounded/unresolved slide, `-` = not computable), and notes
(`*SEL` selected, `dup`, `pinch`). Per-bundle flags: `DUP(n)` geometric duplicates,
`PINCH(n)` zero-slide candidates, `SINGLE` only one candidate, `PASSTHRU(n)`
pass-through candidates. The summary reports the candidate-count distribution,
duplicate / pinched / single / pass-through bundle counts, and a shape-family
histogram (trunk `@coord` suffixes collapsed).

**Hierarchical flow — slide columns resolve after planning.** The slide-derived
columns (`mslide` and the `wl[lo..hi]` envelope's upper bound) are computed by
building each candidate's `ConnTopology` against the current floorplan. A
**cell-level HBundle template** is still in cell-local coordinates before
`run_planner hier`, so its candidates' block faces don't resolve against the
absolute floorplan and every segment reads as unbounded — `mslide` prints `free`
and the envelope `hi` is only a loose extent-clamped bound. `run_planner hier`
expands each template into per-instance absolute-coordinate wrappers, after which
these columns are finite and correct. So in the hier flow, dump **after**
`run_planner hier` when you care about slide freedom or the WL envelope; a
pre-planning dump is still fine for the candidate pool (types, counts, nominal
`wl`, pass-through). In the flat flow every column is already correct right after
`generate_topologies`.

**`--conn` detail.** For the selected candidate of each shown bundle (candidate 0
if not yet planned), `ConnTopology` is rebuilt and each segment is printed with:
its orientation and **layer**, along extent and perpendicular position, **slide
range** (`perp_lo..perp_hi`; `free` when unbounded, `PINCHED` when zero), and
**net-pull** preference (`→hi` / `→lo` / `none`). The layer is the planner's
*effective* assignment (`plan.seg_layers` — the metal NUTS actually routes on);
before planning, or for a candidate that is not the planned one, it falls back to
the candidate's generation hint and is marked `·hint` (e.g. `M6·hint`). Under each
segment three lines list **what it connects to** — `busterms:` (block-face taps,
with `@face=` coord and `(end)`/`(mid)`) and `segs:` (other segments, by index and
junction position) — and **`passthru:`**, the blocks the segment geometrically
crosses without tapping. Pass-through is tested against each block's **solid**
geometry, so a segment through the notch/gap of a multi-rect (TEG) block does not
count; a declared feedthru is marked `[feedthru]`. The bundle header also echoes any
declared `feedthru=` blocks. This is the same connectivity view the planner and NUTS
consume, so it is the first place to look when a bundle routes with an open or an
unexpected slide/pull.

```
dump_topologies                 # every bundle + summary
dump_topologies bus_007         # bundles whose first net starts with bus_007
dump_topologies --problems      # only flagged bundles + summary
```

See [internal/topology_tc3a_findings.md](../internal/topology_tc3a_findings.md) for an
analysis driven by this command.

### `visualize_topologies`

```
visualize_topologies <hint>
visualize_topologies -all [<hint1> <hint2> …]
```

Opens the topology explorer for one or more bundles. Allows stepping through
all generated topology candidates and pinning a selection for the planner.

| Form | Behaviour |
|---|---|
| `visualize_topologies <hint>` | Open explorer for the first bundle whose first net name starts with `<hint>`. |
| `visualize_topologies -all` | Open explorers for every bundle (one window per bundle, opened sequentially). |
| `visualize_topologies -all <hint1> <hint2> …` | Open explorers for all bundles matching any of the given hints. |

**Explorer controls:**

| Action | Effect |
|---|---|
| `<` / `>` buttons | Step through topology candidates |
| `Select` button | Pin this topology; saves to `<script>.json` sidecar |
| `Deselect` button | Remove the pin for this bundle |
| `▶  Re-run & Refresh` button | Re-run the planner (respecting all pinned topologies) + NUTS, then refresh the main layout window. Equivalent to pressing `r`. |
| `r` key | Same as `▶  Re-run & Refresh` |

The `▶  Re-run & Refresh` button appears only when the session was started
with `run_nuts` already completed (i.e. `visualize` was called after NUTS).

**Persistence:** Selected topologies are saved to `<script>.json` alongside
the `.buda` file. The next `run_planner` will load and honour these pins,
overriding the congestion-based choice for pinned bundles.

**Window title:** `<first_net_name> (Bundle N)` — identifies which bundle is
being explored.

**Hierarchical flow deduplication:** After `run_planner hier`, `self.bundles` holds one wrapper per cell instance. Without deduplication, `visualize_topologies -all` would open the same cell-level bundle template once per instance (e.g. two windows for `pa→pb` if there are two proc instances). Instead, cell-level bundles are deduplicated by `(cell_context, reason)`. The first instance is shown with a title annotation: `(N instances — showing first)`. This avoids redundant exploration windows while still accurately representing the template topology.

**Example:**
```
visualize_topologies t0_b3          # explore one bundle
visualize_topologies -all           # explore every bundle
visualize_topologies -all t0_ t1_   # explore all bundles starting with t0_ or t1_
```

---
