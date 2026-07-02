# Hier per-instance offset must carry the seg_busterms annotation

## Symptom

In the **hierarchical** flow, an `L`-topology connecting two blocks could show an
**unintentional feedthru** through a *third* block whose corner coincidentally
sits on the L's bend — e.g. `flow/rnr/mix.buda` bundle **b171** (`i_dogleg2_0`),
candidate **L_VH**: the net connects `bot2`→`top3`, but both segments were
reported tapping `top2` (a non-terminal neighbour) at the bend `(1800,1140)`,
so the route read as `bot2`→`top2`→`top3`. It did **not** reproduce in a flat
flow with the same two blocks.

## Root cause

`ConnTopology::infer_connections` (`conn_topology.cpp`) has two paths:

1. **Authoritative** — uses `Topology::seg_busterms` (populated by
   `annotate_endpoints` in `topology.cpp`). Each segment endpoint is either a
   named busterm or `nullopt` (a bend / SEG junction). A coincidental block-face
   touch at a bend is correctly ignored.
2. **Geometric fallback** — for *unannotated* topologies: any endpoint lying on a
   block face becomes a `BUSTERM`, and `if (found) continue;` skips SEG-junction
   detection for that endpoint. A bend that grazes a block corner is thus
   mis-tapped, and the two segments end up "connected" only *through* that block
   — a silent feedthru.

The hier flow generates candidates in a **cell-local** floorplan, then places one
copy per instance by **offsetting** the coordinates. The old
`buda_cli._offset_topology` rebuilt the `Topology` but **dropped `seg_busterms`**
(and `bridge_segments`), so every per-instance candidate was *unannotated* → it
hit the geometric fallback → coincidental-corner feedthru. Flat candidates keep
their annotation, which is why flat never reproduced it.

## Fix

`Topology offset_topology(const Topology&, dx, dy, name_prefix="")`
(`topology.cpp`, bound in `bind_routing.cpp`) deep-copies the topology and offsets
**all** geometry — segments, every `seg_busterms` busterm `bbox`/`orig_bbox`/
`rects`, and bridge segments. When `name_prefix` is set (the instance path), every
cell-local block name (no `/`) — in `seg_busterms`, `connected_block_names`,
`feedthru_blocks`, and bridge keys — is qualified to `prefix/name` so it resolves
against the global instance-coord floorplan. `buda_cli` calls it with the
instance name during hier expansion (replacing the old ad-hoc name rewrite).

With the annotation carried, hier candidates use the **authoritative** path: the
bend is a SEG junction, the neighbour is not tapped, and the topology verifies
clean.

## Effect

Correcting the connectivity also sharpens slide ranges (they follow the real
conns), which improved detailed-NUTS placement on `mix.buda`:

| metric (no ripup) | before | after |
|---|---|---|
| width-model DNUTS opens | 236 | **156** |
| signal-track DNUTS opens | 162 | **128** |
| full-flow dnuts violations (with ripup) | 156 / 8 bundles | **66 / 4 bundles** |

## Tests

`test/tests/test_offset_topology.py` — reproduces the `dogleg2` corner geometry:
asserts the offset candidate carries/qualifies `seg_busterms`, does **not** tap
the corner neighbour, keeps the bend as a SEG junction, and verifies clean; a
sanity test confirms the *unannotated* fallback still taps the corner (documenting
what the annotation prevents). The `mix` baseline in
`test_planner_signal_tracks.py` was updated 236 → 156 accordingly.
