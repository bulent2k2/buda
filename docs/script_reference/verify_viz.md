# BUDA Script Reference — Verification & visualisation

Design audits and interactive inspection: `check_design`, `dump_topologies`, `visualize`, `visualize_topologies`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Verification commands

### `check_design`

```
check_design [stage] [all]        (alias: check_connectivity)
```

Audit the design at the given stage: verify signal/bus electrical
connectivity and report any open connections, missing stubs, or track
routing violations. The `nuts` and `dnuts` stages also flag layer-direction
violations: a segment (or its bit wires) assigned to a layer whose routing
direction does not match the segment's orientation is reported as
unbuildable.

> **Renamed from `check_connectivity`:** the command outgrew its original
> name — it audits far more than connectivity (layer directions, keepout
> crossings, unplaced bits). `check_connectivity` remains registered as a
> legacy alias with identical behavior, so existing scripts keep working.

Every stage after planning flags an **unrouted bundle** (`UNROUTED_BUNDLE`):
a bundle that has candidate topologies but which the planner selected none
of, so it carries no layer assignment and no wire. The audit walks bundles by
their selected candidate, so such a bundle previously contributed nothing and
the run reported `Success: no violations found` over a bus with no metal
anywhere. The planner names the *cause* at the moment it happens (see its
`NOTHING committed` warning — most often a layer restriction with no legal
layer for a segment's direction); this is the safety net that refuses to call
the design clean whatever the cause was. Before `run_planner` no bundle has a
selection and none is flagged.

The `dnuts` stage additionally audits every bundle governed by a
[non-default rule](ndr.md): **`NDR_WIDTH`** (a governed bit placed narrower
than its rule's width), **`NDR_SPACING`** (foreign metal — another bundle's
wire, or a CLOCK/CUSTOM pre-route rail — inside the rule's reserved run), and
**`NDR_SHIELD`** (the placed shield count does not match the rule, accounting
for any rail-credited ends; and, on a run with no culled bits, the placed rows
in ascending track order do not play the roles the rule's layout declares — so
a same-count shield sitting in the wrong gap is caught in every shield mode.
A culled run skips the arrangement check: its missing bits are already LOUD as
`UNPLACED`, and matching them against the intended layout would flag a correct
run). A design with no rules declared produces no NDR output at all.

The `nuts` and `dnuts` stages additionally audit **keepout crossings**
(`KEEPOUT_CROSS`): at `nuts`, a placed bus segment whose physical extent
lies on a keepout that overlaps its span (the exhausted-window fallback
commit — the same event `NUTSResult.num_keepout_conflicts` counts); at
`dnuts`, a bit-wire whose track sits inside a keepout overlapping its final
span (defense-in-depth — DetailedNUTS's crossing cull removes such bits
before any result is returned, so a hit means a stage regressed). Keepout
zones are taken from the session floorplan the NUTS engine placed against,
so hierarchical bundles' cell-local generation floorplans cannot mask a
conflict. See `docs/internal/keepout_model_audit.md`.

The `nuts` and `dnuts` stages also flag **antennas** (`ANTENNA`, issue
#482): a segment attached to the route at **fewer than two DISTINCT
points**, so everything past that single attachment is a dangling wire that
terminates in nothing. That is electrically inert metal (it loads the net
with capacitance and can violate antenna rules) and a sign the generator
emitted a segment no route needs. Three joints count as an attachment — a
busterm tap (at its face coordinate), a seg junction (at its `at_pos`), and
a **pass-through block** (a connected block the segment merely crosses) —
and they are counted by *position*, so several conn records meeting at one
physical point count once. The detector is structural (conn records +
nominal geometry, not placement), so both stages give the same verdict; the
message names the segment, its geometry, and its one attachment:

```
Bundle 22: Segment 3 (H along [1250,1740] @ 2985) attaches to the route at
  1 point(s) (0 busterm, 1 seg, 0 pass-through) at along=1740 — a dangling
  'antenna' wire: the rest of it terminates in nothing (dnuts)
```

`DISCONNECTED` is the complementary global property (the wire graph splits
into islands); an antenna keeps the graph connected, it just hangs off it. A
one-segment topology is never flagged — it has no junctions by construction,
and a missing block tap there is `BUSTERM_OPEN`'s business. Generation's own
candidate-level knob for dangling shapes is
[`set_drop_dangling`](topologies.md).

The generator does not merely get audited by this check — it **applies the
same predicate itself**. `verify` exposes `seg_attachment()` and the
trunk+MST pass drops a hybrid whose seed trunk is an antenna, alongside its
older "removable seed trunk" drop (the two reasons are counted separately in
the `[TopoGen] dropped N redundant trunk+MST hybrid(s) (…)` line). Sharing
one implementation is deliberate: the predicate had been re-rolled by hand
three times as a conn-*record* count, and each copy was wrong in the same
way. See [seed-trunk antenna family](../internal/seed_trunk_antenna_2026-07.md)
(issue #485).

| Argument | Type | Default | Description |
|---|---|---|---|
| `stage` | str | `dnuts` | Routing stage to verify: `topo` (topology candidates), `nuts` (abstract track sharing), or `dnuts` (detailed bit placement). |
| `all` | keyword | — | Checks all candidate topologies instead of just the selected one. Only applicable for the `topo` stage. Automatically enabled if no topology is selected yet. |

**Hierarchical design — missing-block warning:** When `check_design` is called after `run_planner hier`, it additionally checks that every `connected_block_name` referenced in the selected topologies exists in the current floorplan. If any are missing:
```
  Warning: N block(s) referenced in topologies but not in floorplan: name1, name2, ...
  Hint: call 'add_blocks_from_bdb N skip' for all required depths.
```
This catches the common error of calling only `add_blocks_from_bdb 0` when depth-1 cell-level bundles also need `add_blocks_from_bdb 1 skip` (because they reference absolute paths like `proc_i/pa_i`). The check is only active when `run_planner hier` has been used (detected by `_hier_expansion_map` being non-empty).

**Example:**
```buda
# Audit detailed NUTS placement (typically at the end of script)
check_design dnuts
```

---

## Visualisation commands

### `visualize`

```
visualize [debug]
```

Opens the interactive NUTS result viewer (matplotlib window).

**`debug` flag:** the topology explorer this window opens (the `v` key /
"View Topologies" button) starts in the **debug cost view** — candidates stepped
by increasing planner cost, with the cost + its components shown — exactly as
[`visualize_topologies … debug`](#visualize_topologies) (see **Debug cost view**
there for the full description). Without the flag that explorer opens in the
plain wirelength-ordered view. The flag only affects the spawned explorer; the
main NUTS/layout window is unchanged.

`debug` is the only option `visualize` accepts — it takes no other arguments, so
any other token (`visualize foo`) is a hard error (the PR #467 unknown-option
guard), caught before the window opens (so `--no-viz` runs catch it too).

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
dump_topologies [<hint>] [--problems] [--conn] [--grouped]
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
| `--grouped` | Collapse **nominal-locus families** to one representative row each — the set-up for a `group:<N>` super-candidate pin (see below). |

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

**`--grouped` — one row per nominal-locus family.** A bundle's pool usually
holds **families** of near-identical candidates that differ only in where the
trunk sits (its perpendicular locus) within one shared slide window — since the
trunk slides with its stubs, every member routes within NUTS realization-noise
of the others. `--grouped` collapses each family to a single representative row
(its lowest-WL member) under a `cands=N → M families` header, and adds two
notes to that row:

- `family:+K@lo..hi` — this family has **K** other members, their trunk loci
  spanning `lo..hi`.
- `group:<N>` — the **exact, pinnable token**: paste it verbatim after the
  bundle hint to pin the whole family, `select_topology <bundle> group:<N>`. `N`
  is the representative's **1-based** candidate id (the `idx` column is 0-based,
  and the ordinal position among families is neither — so always copy this mark
  rather than deriving it).

`--grouped` is read-only display: it never drops or reorders candidates, so a
plain dump and the whole pipeline are unchanged. It is the discovery step for a
super-candidate pin, which restricts the planner to a family and lets it refine
*which* member wins instead of hand-picking one nominal — see
[**Super-candidate (family) pins**](planner.md#super-candidate-family-pins--groupn)
in the planner reference (and [`set_dedup_loci`](topologies.md), which collapses
the same families destructively).

**Hierarchical flow — slide columns resolve against the generation-time
floorplan.** The slide-derived columns (`mslide` and the `wl[lo..hi]` envelope)
are computed by building each candidate's `ConnTopology` against the floorplan
its candidates were **generated in** — for a pre-planning hier bundle the dump
resolves the cell-local / depth / endpoint floorplan (the same resolution
`check_design` uses), so a **cell-level HBundle template** shows real
finite slides and an honest envelope the moment candidates exist, matching the
flat flow; after `run_planner hier` the expanded per-instance wrappers report
the same slide magnitudes in absolute coordinates. `free` remains the display
for a genuinely unresolvable slide (never the raw sentinel).

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
junction position) — and **`passthru:`**, the **bundle's** blocks the segment
geometrically crosses without tapping (the coverage/feedthru-relevant set,
consistent with the table's `pass` column; a declared feedthru is marked
`[feedthru]`). Unrelated floorplan blocks the wire crosses split by the
segment's **effective layer**: on a TOP layer (or over a container envelope,
transparent to LOW layers) the crossing is ordinary over-the-cell routing —
listed as **`otc-over:`**, not a problem indicator. (This `--conn` text report
always prints the full `otc-over:` list; only the *interactive* topology
explorer's j/k banner makes it opt-in — a terse `otc:N` count by default, the
full names toggled with the `o` key.) On a
**non-TOP** layer a
leaf footprint is an implicit keepout, so the crossing is flagged
**`low-cross:`** — a real problem to notice (a pinned or best-effort plan, or a
`+`/`-` restyle, can land a segment there). Both lines are omitted when empty. Crossing is tested against
each block's **solid** geometry and requires interior overlap: a segment through
the notch/gap of a multi-rect (TEG) block does not count, and neither does a
single-point abutment (a trunk endpoint landing on the face of the block
flanking its junction) or riding a block's face line. The bundle header also
echoes any declared `feedthru=` blocks. This is the same connectivity view the planner and NUTS
consume, so it is the first place to look when a bundle routes with an open or an
unexpected slide/pull.

```
dump_topologies                 # every bundle + summary
dump_topologies bus_007         # bundles whose first net starts with bus_007
dump_topologies --problems      # only flagged bundles + summary
dump_topologies bus_044 --grouped   # families + the group:<N> pin token for each
```

See [internal/topology_tc3a_findings.md](../internal/topology_tc3a_findings.md) for an
analysis driven by this command.

### `visualize_topologies`

```
visualize_topologies <hint> [debug]
visualize_topologies -all [<hint1> <hint2> …] [debug]
```

Opens the topology explorer for one or more bundles. Allows stepping through
all generated topology candidates and pinning a selection for the planner.

| Form | Behaviour |
|---|---|
| `visualize_topologies <hint>` | Open explorer for the first bundle whose first net name starts with `<hint>`. |
| `visualize_topologies -all` | Open explorers for every bundle (one window per bundle, opened sequentially). |
| `visualize_topologies -all <hint1> <hint2> …` | Open explorers for all bundles matching any of the given hints. |
| `… debug` (flag, anywhere in the args) | Order candidate stepping by **increasing planner cost** and show the cost + its components (see **Debug cost view** below). |

**Option validation:** the only keyword options are `-all` and `debug`; every
other token is a free-form bundle hint. Unknown options are a hard error (not a
silent skip), the same guard PR #467 added across the CLI: a mistyped flag
(`-al`, `--debug`), a misplaced `-all` (valid only as the first argument), or —
because without `-all` only the first hint is honored — **more than one hint
without `-all`** all stop the flow with a message (use `-all <h1> <h2> …` to
open several). Validated before the window opens, so `--no-viz` batch/CI runs
catch the typo too.

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

**Debug cost view (`debug` flag):** Candidates ship (and step, by default) in
**increasing wirelength** order. Passing `debug` re-orders the `a`/`d` stepping
by **increasing planner cost** instead — a *hybrid* source:

- **After `run_planner`** the explorer shows the **real** cost the planner would
  charge each candidate against the current committed band state — the true
  congestion the *other* committed bundles impose — computed read-only by
  `CongestionPlanner::candidate_costs` (it recharges the committed usage around
  the probe and leaves the committed plan untouched). The title's second line
  reads `[debug] cost=<total>  rank R/N  =  seg <seg_cost>  +  wl <wl_term>`,
  where `seg_cost` is the max-over-segments segment score (congestion + span +
  layer terms) and `wl_term` is `kWL·(wirelength [+ envelope] [+ kSegs])`. When a
  segment is selected (`j`/`k`), its info panel gains a `[debug] seg cost … = cong
  … + span … + …` line — the congestion (and other) cost that segment contributes.
- **Before planning** there is no committed state, so the cost falls back to the
  candidate's **intrinsic wirelength** (`cost≈WL <wl>`, congestion unknown); the
  ordering is then just the wirelength ordering the pool already ships in.

The candidate index and group/family IDs are **unchanged and still displayed**
(`topo i/n`, `▸fam …`) — only the traversal *order* changes, so pins,
`select_topology`, and group-pins all keep referring to the same candidates.

**Hierarchical flow deduplication:** After `run_planner hier`, `self.bundles` holds one wrapper per cell instance. Without deduplication, `visualize_topologies -all` would open the same cell-level bundle template once per instance (e.g. two windows for `pa→pb` if there are two proc instances). Instead, cell-level bundles are deduplicated by `(cell_context, reason)`. The first instance is shown with a title annotation: `(N instances — showing first)`. This avoids redundant exploration windows while still accurately representing the template topology.

**Example:**
```
visualize_topologies t0_b3          # explore one bundle
visualize_topologies -all           # explore every bundle
visualize_topologies -all t0_ t1_   # explore all bundles starting with t0_ or t1_
visualize_topologies t0_b3 debug    # step candidates in increasing planner cost
```

---

## Advisory export commands

The routed plan as something a downstream P&R tool can **adopt**, rather than
look at. `export_gds` writes geometry, and a GDS rectangle carries no net
identity, so it is a picture; these two carry the identity.

### `emit_guides`

```
emit_guides <file.json> [margin <n>] [csv <file.csv>] [tcl <file.tcl>]
```

Write the **corridor manifest** — the positive intent, "route these nets
here". Per bundle: the net names, and one rectangle per placed bus segment
(its span × its placed track extent, grown by `margin` on every side), with
the assigned layer id and name.

Each corridor names **its own** nets. A tapered fan-in branch carries only a
subset of the bundle's bits (`Topology::seg_bits` — NUTS sizes the segment
from that subset), and naming the whole bundle on such a branch would direct
nets into wires they never traverse: a wrong instruction, not a loose one.
Corridors carry `nets` / `n_nets` / `tapered` for this reason.

`csv` writes the same rows as a flat table (one row per corridor); `tcl`
writes a worked `create_route_guide -net_list {…} -layer <L> -rect {…}`
script — the adapt-me example, since every tool spells guides differently.
All three outputs are byte-deterministic, so a diff means a real change.

Run it **after** `run_nuts` (or `run_detailed_nuts`): an unplaced plan
reserves nothing, and emitting it would advertise corridors that do not
exist. Emitting early prints `no placed bus segments` and writes an empty
manifest rather than a plausible-looking one.

### `export_def_blockages`

```
export_def_blockages <file.def> [density <frac>] [margin <n>]
```

Write a DEF carrying only what DEF can **honestly** say. The obvious move —
one `BLOCKAGES` rect per corridor — is exactly backwards: a blockage tells
the router to STAY OUT, so it would forbid the routing the plan is asking
for. What goes in instead:

- the design's real keep-clear regions (BUDA's own keepouts) as hard
  `LAYER … RECT` blockages, which is what a blockage means; and
- with `density <frac>`, `PLACEMENT + PARTIAL <frac> RECT` blockages over the
  corridors.

That second one is narrower than it first reads, and the narrowing is the
point: `PARTIAL maxDensity` is a **placement**-blockage option in DEF 5.8,
not a layer routing-blockage one, so it caps how densely *cells* may be
placed under a planned bus. That helps pin access, but DEF has no
routing-density concept at all — the routing reservation lives in the
`emit_guides` manifest and nowhere else.

The emitted file round-trips through BUDA's own DEF reader and is
byte-deterministic.

**Example:**
```
run_nuts
emit_guides out/guides.json margin 2 csv out/guides.csv tcl out/guides.tcl
export_def_blockages out/advisory.def density 0.6
```

---

## Diagnostics

### `dump_messages`

```
dump_messages
```

Print the **message catalogue**: every identified diagnostic BUDA can emit,
with its id and severity.

```
[Messages] 9 identified diagnostic(s)
  BUDA-1601  ERROR    A cell in the DEF has no footprint in the LEF.
  BUDA-1602  WARNING  Imported design counts differ from the counts the file declares.
  ...
```

A methodology needs to know what it may waive or gate on *before* the
message fires, which is what an id buys over prose that changes with the
next wording improvement. Identified diagnostics print as

```
BUDA-<NNNN>: <SEVERITY>: <text>
```

and the id, once issued, never changes meaning. The severity on the line is
authoritative — `set_unit_check warn` downgrades BUDA-1901 rather than
renaming it, so a gate on the id keeps working either way. Full contract:
[message ids](../internal/message_ids.md).

---
