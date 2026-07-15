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

## CLI parity for the slide-window refine

**Context.** The GUI stages `_edit_slide` and lands it on
`plan.seg_slide_lo/hi` at commit; the scriptable `edit_*` session
(`edit_topology` … `edit_commit`) has no equivalent — `edit_set_span`
covers the ALONG range only.

**Wish.** `edit_set_slide <seg#> <lo> <hi>` (and a clearing form), staged
on the session and applied in `cmd_edit_commit`, so a `.buda` script can
reproduce any hand-edited session including its slide refinements — the
sidecar/replay story stays whole.  Where to start:
`src/buda_cmds/edit_cmds.py` + the commit path in
`src/buda_session/edit.py`; validate with the same
intersect-with-structural-range rule as `_edit_slide_at`.
