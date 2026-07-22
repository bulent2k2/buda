# BUDA Script Reference — Stage 2 — Topology generator

Candidate enumeration and expert editing: `generate_topologies`, `generate_topologies_for_bundle`, `generate_more_topologies`, the TopoEdit session (`edit_topology` … `edit_commit`), `generate_hier_topologies`, `generate_topologies_for_hbundle`, `set_prune_dominated`, `set_dedup_loci`, `set_drop_dangling`.

Part of the [BUDA Script Reference](../BUDA_SCRIPT_REFERENCE.md) — see its pipeline overview for where these commands run in the flow.

---

## Stage 2 — Topology generator

### `generate_topologies_for_bundle`

```
generate_topologies_for_bundle <hint> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci]
```

Generate routing topology candidates for the bundle whose first net name
starts with `<hint>`. Source and destination block names are derived
automatically from the netlist.

**Positional arguments:**

| Argument | Description |
|---|---|
| `hint` | Prefix of the first net name in the target bundle, e.g. `t0_b3`. |

**Optional flags** (append anywhere after the hint):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |
| `multi_trunk` | Also generate two-level `BITRUNK_HVH` / `BITRUNK_VHV` datapath trees (opt-in — see the multicast table below). |
| `no_hanan_loci` | Opt OUT of the default Hanan-line trunk loci for this run: n-pin trunk loci are sampled only at the channel midpoints (the pre-flip behavior). Since the default flip, loci ON the in-bbox Hanan lines (block/keepout edges) are sampled BY DEFAULT, so a block-edge-aligned trunk can nominal at the geometric WL floor; the default pool is ~1.3-1.6x the midpoint-only pool and the WL-sorted candidate indices `select_topology` pins DIFFER between the two settings, so pins must come from the matching run. The legacy `hanan_loci` flag remains accepted as a keep-on no-op (backward compatibility for pre-flip scripts and v15 knob memos). The degenerate-loci soundness gate is built in (see wishlist-topo piece (a) / hanan_loci_flip_audit.md): face/abutment-coincident loci get their stub↔spine junctions restored, a loci-only candidate whose slide window collapses under the final block contract is dropped with a note, and `DISCONNECTED` island-split candidates are dropped by generation's coverage gate (declared-feedthru candidates exempt). |

**Candidate shapes generated (2-pin):**

| Type string | Segments | Description |
|---|---|---|
| `I_H` / `I_V` | 1 | Straight H or V wire; generated when margin-inset bboxes overlap on one axis. Always shortest; tried first. |
| `L_HV@x{bend}@y{hy}` | 2 | H then V; two corner variants (top/bottom face exit). |
| `L_VH@y{bend}@x{vx}` | 2 | V then H; two corner variants (left/right face exit). |
| `Z_HVH@x{cut}@y{ty}` | 2–3 | H stub · V trunk · H stub; one candidate per Hanan channel midpoint strictly between the two blocks. |
| `Z_VHV@y{cut}@x{vx}` | 2–3 | V stub · H trunk · V stub; symmetric. |
| `U_HVH@x{cut}` | 2–3 | H stub · V trunk (outside block bbox in X) · H stub. |
| `U_VHV@y{cut}` | 2–3 | V stub · H trunk (outside block bbox in Y) · V stub. |
| `UU_VHV@y{cut}` | 3–4 | H+V L-exit from src side face, then H trunk OOB. Requires `double_detour`. |
| `UU_HVH@x{cut}` | 3–4 | V+H L-exit from src side face, then V trunk OOB. Requires `double_detour`. |

**Candidate shapes generated (multicast):**

| Type string | Description |
|---|---|
| `TRUNK_H@y{trunk}` | H spine + V stubs to each receiver. Optimised with pass-through snapping and extreme-stub slide. Loci sampled at the in-bbox Hanan channel midpoints AND on the Hanan lines themselves (the post-flip default; `no_hanan_loci` restores midpoint-only sampling). For receivers with `teg_mode over`, may carry a `bridge_segments` entry (see below). |
| `TRUNK_V@x{trunk}` | V spine + H stubs. Symmetric. Same locus sampling and bridge logic apply. |
| `TRUNK_H+MST@y{trunk}` | TRUNK_H hybrid: adds MST inter-branch edges between the branch blocks (those with explicit stubs) on top of the spine, shortening inter-block paths. Generated for 3+ blocks. Type string is `TRUNK_H+MST@y{trunk}`. |
| `TRUNK_V+MST@x{trunk}` | TRUNK_V hybrid with MST inter-branch edges. Symmetric. |
| `TRUNK_H_OOB@y{trunk}` | H spine outside the pin bounding box + V stubs (detour equivalent of U-shape). |
| `TRUNK_V_OOB@x{trunk}` | V spine outside the pin bounding box + H stubs. |
| `MST_HV` | Prim MST on block bboxes, L-bends H-first. Lower total wirelength for scattered pins. Generated for 4+ blocks. |
| `MST_VH` | Prim MST, L-bends V-first. |
| `BITRUNK_H` | Two parallel H spines at 25th/75th percentile Y + vertical backbone. Generated for 4+ receivers. |
| `BITRUNK_HVH@…` | **Requires `multi_trunk`.** Two-level datapath tree: a root H spine feeds perpendicular V branch trunks, each tapping a cluster of x-aligned blocks (a column becomes a multi-tap pass-through trunk). Wins on column-aligned datapaths. |
| `BITRUNK_VHV@…` | **Requires `multi_trunk`.** Row-oriented mirror: a root V spine feeds H branch trunks tapping y-aligned block rows. |

**Bridge segments (`teg_mode over`):**

When a receiver block uses `teg_mode over` and the trunk falls in the gap
between its rects, the topology carries a **bridge segment** for that block.
The bridge is a short wire segment placed along the outer face of the block's
union bounding box (top face for an H-trunk gap; right face for a V-trunk gap).
It is stored in `topology.bridge_segments[block_name]` — separate from the main
`topology.segments` list — so the planner and visualizer can distinguish routing
wires from bridge annotations. Bridge topologies have higher adjusted wirelength
than their `thru` counterparts (the bridge adds explicit wire) and are therefore
ranked after `thru` candidates when all else is equal.

**Notes:**
- Each call targets exactly one bundle. For N bundles, call N times.
- If no bundle matches the hint, a warning is printed and no error is raised.
- Candidates are stored on the bundle and consumed by `run_planner`.

**Example:**
```
generate_topologies_for_bundle t0_b3
generate_topologies_for_bundle t0_b3  center_mode
generate_topologies_for_bundle bus_rsp  # multicast
```

---

### `generate_more_topologies`

```
generate_more_topologies <hint> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci]
```

**Additive** sibling of `generate_topologies_for_bundle` /
`generate_topologies_for_hbundle`: runs the generator with the given knobs and
**appends** the resulting candidates to the bundle's existing pool instead of
replacing it, deduplicated by stable content identity (`topo_uid`).
Append-only means existing candidate indices — and therefore the bundle's pin
(`select_topology` / sidecar) and plan state — are untouched, so an expert can
accrete a candidate pool across knob experiments without losing selections.
Re-running with the same knobs adds nothing (all duplicates skipped,
reported).  The enlarged pool is re-persisted to the open BDB.

**Hier flow**: in a hier-bundled session (`run_hier_bundler`) the command is
fully hier-aware —

- the `hint` matches an **HBundle id** (numeric) or the bundle's first
  net-name prefix, exactly as `dump_hbundles` shows them;
- generation goes through the same **3-case dispatch** as
  `generate_hier_topologies` (cell-local / cross-level / cross-block
  floorplans), so the fresh candidates are born in the bundle's own frame;
- a hint that matches a **replica** redirects to its template — the bundle
  that actually carries the routing for every instance (a printed note says
  so);
- the accretion is recorded in the per-bundle **knob memo** (v15), and a bulk
  `generate_hier_topologies` re-applies it additively, so the accreted pool
  survives regeneration;
- accretion happens on **pre-expansion templates**: after `run_planner hier`
  the pools live on per-instance expanded wrappers, so the command refuses
  with the re-run recipe (`generate_hier_topologies` →
  `generate_more_topologies` → `run_planner hier`).

```
generate_more_topologies bus_rsp multi_trunk    # add BITRUNK trees to the pool
generate_more_topologies t0_b3 double_detour    # add UU variants
generate_more_topologies 7 center_mode          # hier: accrete on HBundle 7
```

---

### TopoEdit session — `edit_topology` … `edit_commit`

```
edit_topology <bundle_id> [<cand#>|new]
edit_add_trunk <H|V> <perp_pos> [<along_lo> <along_hi>] [layer <id>]
edit_add_stub <block> <seg#> [layer <id>]
edit_set_span <seg#> <along_lo> <along_hi>
edit_connect <seg_i> <seg_j>
edit_disconnect <seg_i> <seg_j> <retract_to>
edit_remove_segment <seg#>
edit_status
edit_commit [pin]
edit_abort
dump_user_ops <bundle_id>
```

Expert hand-editing as a **transactional session** (topo_conn_unification
Phase E3b).  `edit_topology` opens a working **copy** of the given candidate
(1-based; default = the selected one) — or an empty topology with `new`.  Each
`edit_*` op mutates the copy, maintains the authoritative annotations
(`seg_busterms` seeding, junction re-derivation), and prints a verdict:
`check_topo` violations, zero-slide pinch, and the wire-graph component count
(a split tree is caught even though every tap still looks fine).

* `edit_add_trunk` — pick an axis and a Hanan line; without an explicit span
  the trunk gets the **full Hanan extent** on that axis.  Layer defaults to
  the stack's TOP layer for the direction.
* `edit_add_stub` — drops a perpendicular stub from the block's nearest face
  to segment `<seg#>` (0-based, as printed by `edit_status`), seeding the
  busterm tap exactly like the generators (margin bbox, multi-rect, TEG).
* `edit_set_span` — override a segment's along-span.  (A *slide* override is
  per-plan state, not topology content: use the `plan.seg_slide_lo/hi`
  NUTS hatch.)
* `edit_connect` / `edit_disconnect` — junction editing on a perpendicular
  pair: connect moves the nearest free endpoint to the crossing (extending
  the partner if it falls short; a busterm-tapped endpoint is refused),
  disconnect retracts the landing endpoint to `<retract_to>`.
* `edit_commit` — appends the result to the bundle's pool as a `USER`
  candidate, deduplicated by `topo_uid` (an identical topology is reported,
  not duplicated); `pin` also selects it.  A not-clean topology commits with
  a WARNING (visible to `check_design`), mirroring generation's
  never-strand rule.  The pool is re-persisted to the open BDB — along with
  the session's **op-log provenance** (`user_ops:<bundle_id>:<topo_uid>` in
  BDB meta: the base candidate's uid — `'new'` for an empty session — plus
  the applied `edit_*` command lines, block/face references verbatim).  The
  GUI's TopoEdit commit stores the same record through the explorer's
  `user_ops_sink`.  Restore never needs it (the topology rows are the
  geometric truth); it is the human-readable how-it-was-built record.
* `dump_user_ops` — print the persisted op-log of the bundle's USER
  candidates as a replayable command sequence (`edit_topology …` → ops →
  `edit_commit pin`).  `load_pipeline` prints a pointer to it for every
  restored USER candidate that has one.

**Sidecar re-run precedence.** A GUI TopoEdit commit records its op-log in
the flow's JSON sidecar; on a fresh re-run of the same flow the replay
rebuilds the hand-built candidate and **pins it, overriding any
`select_topology` in the script** (the script can only reference an
auto-generated candidate, so a hand-built USER topo is reachable no other
way — without the override it was silently dropped and its slide windows
discarded).  A prior `select_topology` that reset the plan's slide windows
is re-applied on the override, so the USER topo's `W`/`edit_set_slide`
refinements survive.  A *plain* sidecar selection (a remembered generated
candidate, no edit) still defers to the script's `select_topology`.

```
edit_topology 1 new
edit_add_trunk V 450          # full-span column trunk at x=450
edit_add_stub A 0
edit_add_stub B 0
edit_commit pin               # pin the hand-built route; then run_planner…
```

---

### `generate_topologies`

```
generate_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci]
```

Generate routing topology candidates for **all** bundles produced by `run_bundler`.
Source and destination block names are derived automatically from the netlist
(registered at `add_net` / `add_bus` time — no manual `hint`, `src`, or `dst` needed).

**Optional flags** (same as `generate_topologies_for_bundle`):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |
| `multi_trunk` | Also generate two-level `BITRUNK_HVH` / `BITRUNK_VHV` datapath trees for high-fan-out column/row-aligned buses (opt-in; wins on datapaths, QoR-neutral elsewhere at a small candidate-count cost). |
| `no_hanan_loci` | Opt OUT of the default Hanan-line trunk loci for this run: n-pin trunk loci are sampled only at the channel midpoints (the pre-flip behavior). Since the default flip, loci ON the in-bbox Hanan lines (block/keepout edges) are sampled BY DEFAULT, so a block-edge-aligned trunk can nominal at the geometric WL floor; the default pool is ~1.3-1.6x the midpoint-only pool and the WL-sorted candidate indices `select_topology` pins DIFFER between the two settings, so pins must come from the matching run. The legacy `hanan_loci` flag remains accepted as a keep-on no-op (backward compatibility for pre-flip scripts and v15 knob memos). The degenerate-loci soundness gate is built in (see wishlist-topo piece (a) / hanan_loci_flip_audit.md): face/abutment-coincident loci get their stub↔spine junctions restored, a loci-only candidate whose slide window collapses under the final block contract is dropped with a note, and `DISCONNECTED` island-split candidates are dropped by generation's coverage gate (declared-feedthru candidates exempt). |

**Notes:**
- Replaces N individual `generate_topologies_for_bundle` calls with one line.
- Must be called after `run_bundler` and before `run_planner`.
- Candidate shapes generated are identical to `generate_topologies_for_bundle` (I, L, Z, U, UU, multicast TRUNK/MST/BITRUNK variants).
- Bundles with no registered endpoint info emit a warning and are skipped.

**Coverage gate.** Every generation path ends in an automatic coverage filter:
each candidate is verified with `check_topo`, and two silent-open risks the
planner cannot detect are **dropped** (with a one-line `[TopoGen] dropped …`
note):
- `BUSTERM_OPEN` — a candidate that leaves one of the bundle's blocks with **no
  busterm tap and no pass-through segment** (an uncovered block). **Always
  dropped.**
- `FEEDTHRU_RELAY` — the legacy multi-rect / rootless trunk+MST fallback whose
  incident wires do not physically touch (a silent feedthru relay no downstream
  stage catches). **Dropped too, but only when a clean candidate — neither open
  nor relay — survives**, so a bundle whose only options are relays is never
  stranded (those stay visible to `check_design` / `dump_topologies
  --problems`).

If **every** candidate is uncovered — or a bundle's only options are relays —
the list is kept unchanged with a `[TopoGen] WARNING` so the bundle is never
stranded (the planner's escalation ladder still commits one). This keeps
`run_planner` focused on capacity/congestion: it never sees an uncovered
candidate.

**Example:**
```
run_bundler strict
generate_topologies
run_planner 5
```

With flags:
```
run_bundler strict
generate_topologies  double_detour
run_planner 5
```

On a column/row-aligned datapath, add `multi_trunk` to enable the two-level trees:
```
run_bundler
generate_topologies  multi_trunk
run_planner
```

---

### `generate_hier_topologies`

```
generate_hier_topologies [center_mode] [double_detour] [multi_trunk] [no_hanan_loci]
```

Generate routing topology candidates for all HBundles generated by `run_hier_bundler`. Must be called after `run_hier_bundler` and before `run_planner`.

The topology generator automatically determines the routing context for each HBundle:
1. **Intra-cell / Same-level local routing**: generates candidates relative to the cell's local floorplan and boundaries.
2. **Cross-level routing**: generates candidates spanning between different depths in the hierarchy using absolute global coordinates.

**Optional flags** (same as `generate_topologies_for_bundle`):

| Flag | Effect |
|---|---|
| `center_mode` | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | Also generate `UU_VHV` / `UU_HVH` high-detour candidates for very congested situations. |
| `multi_trunk` | Also generate two-level `BITRUNK_HVH` / `BITRUNK_VHV` datapath trees, exactly as in the flat `generate_topologies` (opt-in). Applied per HBundle across all three routing cases (cell-local / cross-level / cross-block). |
| `no_hanan_loci` | Opt OUT of the default Hanan-line trunk loci, exactly as in the flat `generate_topologies` (see its flag table for the full semantics; `hanan_loci` = keep-on no-op). Applied per HBundle across all three routing cases (cell-local / cross-level / cross-block), and round-tripped by the v15 knob memo across bulk regenerations. |

**Zero-candidate warning:** If any HBundle ends up with 0 topology candidates, the CLI prints:
```
  WARNING: HierTopo D{level}: bundle {id} ({label}) 0 candidates — bundle will be unrouted!
```
Downstream stages (`run_planner`, `run_nuts`) silently skip bundles with no candidates, so this warning is the only indication that a bundle will not be routed. Common causes: source or destination block not present in the floorplan, or extreme span/layer constraints ruling out all shapes.

**Example:**
```buda
run_hier_bundler depth 1
generate_hier_topologies
run_planner hier 5
```

---

### `generate_topologies_for_hbundle`

```
generate_topologies_for_hbundle <bundle_id> [center_mode] [double_detour] [multi_trunk] [no_hanan_loci]
```

Re-run topology generation for a single HBundle identified by its integer ID. Uses the same 3-case dispatch as `generate_hier_topologies` (cell-local / cross-level / cross-block). Useful for debugging when a specific bundle has zero candidates or when experimenting with flags without re-running all bundles.

| Argument | Type | Description |
|---|---|---|
| `bundle_id` | int | Integer bundle ID (as shown by `dump_hbundles`). |
| `center_mode` | keyword | Use block centres as connection points instead of the nearest busterm face. |
| `double_detour` | keyword | Also generate `UU_VHV` / `UU_HVH` high-detour candidates. |
| `multi_trunk` | keyword | Also generate two-level `BITRUNK_HVH` / `BITRUNK_VHV` datapath trees (opt-in). |
| `no_hanan_loci` | keyword | Opt OUT of the default Hanan-line trunk loci (midpoint-only pool; `hanan_loci` = keep-on no-op). An explicit polarity is recorded in the bundle's v15 knob memo, so a later bulk `generate_hier_topologies` keeps this bundle's pool as generated. |

**Requirements:** open BDB, `run_hier_bundler` already called.

**Zero-candidate warning:** Same WARNING line as `generate_hier_topologies` if the bundle ends up with 0 candidates.

**Post-expansion advisory:** If `run_planner hier` has already been called and the specified bundle ID no longer appears in `self.bundles` (because it was expanded into per-instance wrappers), the CLI prints:
```
Note: bundle {id} was expanded by run_planner hier — re-run generate_hier_topologies before planning.
```

**Example:**
```buda
generate_topologies_for_hbundle 4              # re-generate candidates for hb-4
generate_topologies_for_hbundle 4 center_mode  # with centre-mode flag
```

---

### `set_prune_dominated`

```
set_prune_dominated [on|off]
```

Opt-in **WL-dominance candidate pruning** (default **off** — flows are
bit-identical without it).  When on, every generation command
(`generate_topologies`, `generate_hier_topologies`,
`generate_topologies_for_bundle`, `generate_topologies_for_hbundle` — but
**not** the additive `generate_more_topologies`, whose contract is to only
grow the pool) runs a prune pass after the pool is final: a candidate whose
WL envelope bottom `wl_lo` exceeds another candidate's envelope top `wl_hi`
(the `wl[lo..hi]` column of `dump_topologies`) is *deterministically*
WL-dominated — its **best** realization is longer than the survivor's
**worst** — and is dropped with a printed note.

**The equivalence gate is the load-bearing part.**  WL-dominance alone is
NOT overall dominance: the planner scores congestion/span/layer/balance/peak
*before* weighted WL, and a longer candidate can be the only overflow-free or
window-feasible option (the escalation ladder and ripup's OOB-trunk promotion
exist for exactly that).  So a dominated candidate is pruned **only** when a
dominating survivor is equivalent in every non-WL respect:

- same **block contract** (`connected_block_names`) and **feedthru
  declarations** (`feedthru_blocks`);
- same **segment count**, and a one-to-one segment matching where each pair
  has the same **orientation** and **layer hint**;
- the survivor's **slide window covers** the dominated segment's (the safe
  containment direction — every band/track placement reachable by the
  dominated candidate is reachable by the survivor);
- the survivor's **along-span lies inside** the dominated segment's (the
  survivor crosses a subset of the planner cuts with the same bus width, so
  any overflow-free assignment for the dominated candidate maps to one for
  the survivor);
- the survivor's **nominal WL** does not exceed the dominated one's (the
  nominal carries dangling wire the connection-based envelope excludes).

Candidates with TEG bridge segments, fan-in per-bit taper (`seg_bits`),
adopted dogleg jogs, or U_OVL perp clamps never participate; `USER`
candidates take no part at all — they are never pruned AND never act as
survivors (`edit_commit` accepts a not-clean hand edit with only a warning,
so an invalid-but-shorter USER candidate must not evict a valid generated
alternative); the currently selected/pinned candidate is never pruned
either (a pruned pool remaps the selection index by `topo_uid`).  When in
doubt the
gate refuses — a missed prune is only wasted planner work, a false prune
could strand the only routable topology.

Each run prints a summary:

```
[TopoPrune] pruned N WL-dominated candidate(s) across M bundle(s); K dominated pair(s) refused by the equivalence gate.
```

Measured on the corpus (b44 no-pin, comprehensive_demo, rnr/mix hier,
big2/b4_bus_077): **0 pruned**, 11/459/3010/30 dominated pairs refused —
every WL-dominated pair on these flows differs in shape family or corridor,
i.e. is exactly the kind the ladder may need — with all QoR endpoints
(overlaps, unplaced, abstract+detailed WL) identical to the off runs.

**Ordering:** declare *before* the generation command.  Pruning renumbers the
1-based candidate indices, so `select_topology` pins in a flow must be taken
from an opted-in run (the prune runs before sidecar restore and BDB
persistence, so `dump_topologies`, persisted `topology` rows, and later pins
all see the same pruned pool).

**Example:**
```buda
set_prune_dominated on
generate_topologies            # pool pruned, note + summary printed
set_prune_dominated off        # later regenerations keep everything again
```

---

### `set_dedup_loci`

```
set_dedup_loci [on|off]
```

Opt-in **nominal-locus candidate dedup** (default **off** — flows are
bit-identical without it).  When on, every generation command (the same set as
`set_prune_dominated`; **not** the additive `generate_more_topologies`)
collapses candidates that are the **same topological choice** differing only in
a **nominal trunk locus that lies inside a shared slide window**.

Why they exist: the Hanan-line loci sampler emits one candidate per Hanan line,
so a trunk that passes *through* a block (a pass-through with a full-block slide
window) is emitted once per line inside that window — e.g. `TRUNK_H@y10830`,
`TRUNK_H@y11330`, `TRUNK_H@y11830`, all with the trunk on slide window
`[10830,11830]`.  They differ only in the *nominal* trunk position; their
connectivity, per-segment slide windows, block taps and net-pull are identical,
and the trunk simply slides with its stubs — so NUTS realizes them within
placement-noise of each other, and the content `topo_uid` (which hashes the
nominal geometry) needlessly keeps them apart.  An MST hybrid can even realize
the *same* skeleton as a plain trunk and collapse with it.

When on, a **structural canonical** — per-segment `(direction, layer, slide
window, block taps, net-pull)` plus the junction adjacency, deliberately
**excluding** the slide-dependent nominal — groups these candidates and keeps
the **best-estimated (lowest-WL) member**; the rest are dropped with a note:

```
[TopoDedup] bundle B: dropped TYPE (idx I) — same slide-window/connectivity as idx J (nominal-locus variant)
[TopoDedup] collapsed N nominal-locus duplicate candidate(s) across M bundle(s).
```

`USER` candidates never collapse, and the selected/pinned candidate is never
dropped (the selection index is remapped by `topo_uid`).  Candidates carrying
content the ConnSeg skeleton does not fully describe — **TEG bridge segments**,
**fan-in per-bit taper** (`seg_bits`), **dogleg jogs**, or **U_OVL perp
clamps** — never participate (exactly as `set_prune_dominated` excludes them),
so a bridged candidate can never be collapsed into a same-skeleton non-bridge
one.

This is a **lossy** cleanup: because NUTS is nominal-sensitive, a few collapsed
members can realize a couple percent apart (the b44 realization sensitivity),
so the dedup trades those variants for a tidier pool.  The planner's default
pick is unchanged — the surviving representative is the lowest-WL member it
would have selected anyway.  On the reported b44 vehicle the single bundle drops
from **35 → 24** candidates.

**Ordering:** declare *before* the generation command; the dedup renumbers the
1-based candidate indices, so `select_topology` pins must be taken from an
opted-in run (it runs before sidecar restore and BDB persistence, so
`dump_topologies`, persisted rows and later pins all see the collapsed pool).

---

### `set_drop_dangling`

```
set_drop_dangling [on|off]
```

Opt-in **dangling / unclamped-segment candidate drop** (default **off** — flows
are bit-identical without it).  When on, every generation command (the same set
as `set_prune_dominated`; **not** `generate_more_topologies`) drops a candidate
that has either:

- a **dangling segment** — a segment with a single non-block connection, i.e. a
  wire whose other end connects to nothing; or
- an **unbounded slide window** — a segment whose perpendicular slide was never
  clamped (the `±2³⁰` no-clamp sentinel).

These are the **out-of-bounds detour** and **MST-relay** shapes: they pass the
generation coverage gate (every block is tapped and the wire graph is one
connected island) but render as a **dangling overshoot** — a spine that extends
past its only tap, or a relay segment NUTS can fling arbitrarily far.  On b44
these are the `TRUNK_*_OOB` and `TRUNK_*_OOB+MST` candidates.

Each dropped candidate prints a note:

```
[TopoDangling] bundle B: dropped TYPE (idx I) — seg J has an unbounded (unclamped) slide window
[TopoDangling] dropped N candidate(s) with dangling / unclamped-slide segments across M bundle(s).
```

`USER` candidates and the selected/pinned candidate are never dropped, and the
pass **never strands a bundle** — if every candidate is flagged, the pool is
kept intact.  On the reported b44 vehicle the single bundle drops from **24 →
14** with `set_dedup_loci` also on (both knobs compose).

**Ordering:** declare *before* the generation command; like the other pool
cleanups it renumbers indices, so `select_topology` pins must come from an
opted-in run.

**Example (both cleanups):**
```buda
set_dedup_loci on
set_drop_dangling on
generate_topologies            # collapse nominal-locus variants, drop danglers
```

---
