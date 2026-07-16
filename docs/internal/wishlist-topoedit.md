# Wishlist — Topology editor (TopoEdit)

Deferred follow-ups for the expert hand-editing surface: the explorer's
TopoEdit mode (`src/buda_viz.py`, `TopologyExplorer._edit_*`) and the
scriptable `edit_*` commands (`src/topo_edit.h/cpp`,
`src/buda_cmds/edit_cmds.py`).  Index: [`wishlist.md`](wishlist.md).
Key bindings: [`../KEY_BINDINGS.md`](../KEY_BINDINGS.md) → *TopoEdit mode*.

## Slide-window refine ('W') input precision

**Context.** The `W` two-step slide-window refine (branch
`claude/topo-edit-ui`) captures each bound from the RAW mouse-cursor
position at key-press time — the cursor's perpendicular component as an
unsnapped float, echoed in the banner (`slide bound at 2743 — press W at
the other bound`) and clamped into the segment's structural slide range at
apply time.  Deliberate: slide bounds are continuous quantities (the
discrete track choice happens later in NUTS/DNUTS), so free placement is
the right default granularity.  But a cursor-derived float may prove too
loose in practice — a user aiming at a specific track or block face gets
whatever pixel they hovered.

**Wish.** If/when raw capture proves too imprecise, add one or more of:

1. **Snap-to-grid variant** — a modifier or alternate key that snaps the
   captured bound to the nearest bundle-grid line (the busterm-block +
   keepout edges `_bundle_hanan_grid` already computes for `T`/`Y`), so a
   bound lands exactly on a block face.  Where to start: reuse `_snap` in
   `_edit_slide_at`, gated on the chosen key/modifier.
2. **Echo marker at the marked bound** — after the first `W`, draw a
   transient dashed line (perpendicular to the segment, at the captured
   coordinate) so the user sees exactly where the first bound landed
   before committing the second.  Where to start: a one-shot artist in
   `_draw` keyed off `_edit_slide_mark`, styled like the slide-bound
   dotted lines.
3. **Precise text entry** — an exact-coordinate path for both bounds,
   either a matplotlib TextBox popped by a key (GUI) or simply the CLI
   parity command (`edit_set_slide <seg#> <lo> <hi>`, mirroring
   `edit_set_span`) so a script or an expert who knows the number types
   it.  Where to start: the CLI command is the cheap one — stage into the
   session the way the GUI stages `_edit_slide`, land on
   `plan.seg_slide_lo/hi` in `cmd_edit_commit`.

**Why deferred.** The raw capture shipped first to validate the workflow;
none of these variants is needed until real use shows the float capture
missing targets.  All three compose (snap key + echo marker + text entry
are independent).

## CLI parity for the slide-window refine — ✅ RESOLVED

**Landed** (dd-detour batch): `edit_set_slide <seg#> <lo> <hi>` /
`edit_set_slide <seg#> clear` stages per-segment windows on the CLI
session (clamped to the structural slide range at stage time, revalidated
at `edit_commit`, re-keyed by `edit_remove_segment`), and lands them on
`plan.seg_slide_lo/hi` exactly like the GUI commit.  The GUI's `W`/`w`
now log the same command into the `[edit-cmd]` stream and the sidecar
op-log, so a replayed session keeps its slide refinements — the
sidecar/replay story is whole.

## BDB topology tables as the USER-topo persistence home (hier flows) — ✅ RESOLVED (core), follow-ons below

**Context.** Hand-built USER candidates now persist via the SIDECAR
op-log (`user_topo`: base uid + applied `edit_*` commands, replayed after
`generate_topologies`) — which covers the FLAT flow well.  The hier flow
already has a richer, native persistence surface the op-log does not use:
the BDB `topology`/`topology_segment` tables (+ `topology_seg_busterm` /
`topology_bridge_segment` links, `perp_clamp_lo/hi` since v16, `topo_uid`
identity) that `generate_[hier_]topologies` writes and `load_pipeline`
restores.  A USER candidate committed in a hier session should live
THERE — first-class rows, not a JSON side channel.

**Wish.** Develop + test USER-topology persistence through the BDB for
hier flows:

1. **Persist**: `edit_commit` (CLI + explorer) writes the committed USER
   candidate into the open BDB's topology tables exactly like generated
   candidates (`_persist_topologies` already re-persists the pool — audit
   that the USER rows carry taps, clamps, layer hints, and the op-log as
   a provenance blob so the sidecar replay can be reconstructed FROM the
   BDB).
2. **Restore**: `load_pipeline` (both views) rehydrates USER candidates
   with their seg_busterm links and slide overrides, and the pin
   resolves by `topo_uid` — no sidecar needed when a BDB is open.
3. **Hier semantics**: decide template-vs-instance scope — a USER edit
   on a cell-local template should replicate to instances (the
   template/replica linkage), while an edit on one expanded instance
   wrapper stays instance-local; both must survive `run_planner hier`
   expansion and be exercised by tests (cell-local, cross-level, and
   post-expansion edits).
4. **Tests**: BDB fixture round trips (commit → save → reopen →
   `load_pipeline` → pin resolves; the `bdb_input` copy-to-temp fixture),
   plus a hier flow whose template edit lands on every instance.

**Landed** (opens item 12 batch).  What the investigation found and fixed:

- *Flat was already whole* (v15 `source='user'`): commit persists, restore
  resolves the pin by uid — locked in by `test_bdb_user_topo.py`.
- *TopoEdit is now frame-aware*: `edit_topology` resolves the bundle's OWN
  floorplan via the same `_floorplan_for_hbundle` cases check_design uses,
  so a CELL-LOCAL template edits in cell-local block names/coordinates
  (`session._edit_fp`; flat/expanded/same-level stay on `session.fp`).
- *The loader resolves frames too*: `_restore_wrapper` restores the
  persisted `entry/exit_busterm_ids` (written since v-early, never read
  back — and `entry_busterm_ids` is exactly the cell-local case's gate) and,
  with the loader's shared `fp_env`, validates each bundle in its own frame
  — a PRE-planner hier checkpoint (templates only, incl. a hand-committed
  USER candidate) now loads instead of tripping the missing-block gate,
  and the resumed `run_planner hier` replicates the pinned USER template
  to every instance.
- *Post-expansion commits persist through the planner's expanded path*:
  `edit_commit` on an expanded session used to rewrite the per-instance
  wrappers as NORMAL bundle rows via `_persist_topologies`, clobbering the
  `is_expanded` checkpoint IN PLACE (binary BDBs open read-write); it now
  routes through `_persist_planner_output`, so an instance-local edit
  stays instance-local and `load_pipeline expanded` round-trips.

**Follow-ons (not blocking):**

1. **Op-log provenance in the BDB** — the sidecar `user_topo` replay log
   (base uid + edit_* commands) could be mirrored into BDB meta so the
   geometric restore carries its editing provenance; restore is geometric
   either way, so this is documentation value only.
2. **Explorer (GUI) hier frames** — the CLI edit session is frame-aware;
   the explorer still draws/edits against the session floorplan, so
   editing a cell-local template in the GUI shows the wrong backdrop.
   Needs the explorer to accept a per-bundle floorplan (same resolver).
3. **Un-pinned instance commits are session-only** (expanded rows persist
   only their selection, by design) — surfaced with a printed note; a
   full per-instance candidate-pool persistence would be a schema change
   with little value until a workflow needs it.
