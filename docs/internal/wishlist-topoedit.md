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

## BDB topology tables as the USER-topo persistence home (hier flows) — OPEN, significant

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

**Why significant.** This is the designer-interaction keystone for hier
designs: today a hier session's hand edits survive only while the sidecar
matches the flow, and cross-session/hier-aware continuation
(`load_pipeline`) silently drops USER candidates.  Where to start:
`src/buda_session/persist.py` (`_persist_topologies`) +
`src/buda_session/hier.py` (`load_pipeline` restore path) +
[`../BDB_REFERENCE.md`](../BDB_REFERENCE.md) schema notes.
