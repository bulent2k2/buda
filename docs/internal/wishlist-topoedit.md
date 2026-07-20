# Wishlist — Topology editor (TopoEdit)

Deferred follow-ups for the expert hand-editing surface: the explorer's
TopoEdit mode (`src/buda_viz.py`, `TopologyExplorer._edit_*`) and the
scriptable `edit_*` commands (`src/topo_edit.h/cpp`,
`src/buda_cmds/edit_cmds.py`).  Index: [`wishlist.md`](wishlist.md).
Key bindings: [`../KEY_BINDINGS.md`](../KEY_BINDINGS.md) → *TopoEdit mode*.

## Slide-window refine ('W') input precision — ✅ RESOLVED (all three variants)

**Context.** The `W` two-step slide-window refine originally captured each
bound from the RAW mouse-cursor position at key-press time — an unsnapped
float, echoed only in the banner.  Three precision variants were deferred
until real use showed the float capture missing targets; all three have
now landed:

1. **Snap-to-grid variant** — ✅ landed as the DEFAULT (gridded W refine
   batch): bounds snap to the bundle grid's Hanan lines (`[grid]` in the
   banner), and `enter` mid-refine toggles the gridless `[free]` sub-mode
   for off-grid bounds.  Both marks store the raw cursor coordinate, so
   the mode at apply time decides.
2. **Echo marker at the marked bound** — ✅ landed: after the first `W`,
   `_draw_slide_mark_line` draws a transient dashed line + `W:<coord>
   [mode]` label at the bound's EFFECTIVE coordinate (re-snapped or raw
   per the current sub-mode — it re-echoes live when `enter` toggles),
   across the marked segment's along extent; it disappears when the
   second `W` applies the window (or the mark is otherwise discarded).
   Test: `test_edit_mode_slide_mark_echo_marker`.
3. **Precise text entry** — ✅ landed as the CLI parity command
   (`edit_set_slide <seg#> <lo> <hi>`, next section), which the GUI's
   `W`/`w` also log into the `[edit-cmd]` stream and the sidecar op-log.

All three compose, as predicted: snap default + live echo marker + exact
text entry cover the aiming, feedback, and precision paths.  All three compose (snap key + echo marker + text entry
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

## Follow-ons — the fuller picture (2026-07-16)

The three follow-ons above, expanded: what each gap is concretely, where it
bites, the shape of the work, and a priority call.  Recommended order:
**#2 first** (completes the interactive story for hier designs), #1 as a
cheap add-on whenever someone is next in that code, #3 only on demand.

### 1. Op-log provenance in the BDB

**The gap.** A GUI commit leaves two records: the GEOMETRY (BDB topology
tables — what restore actually uses) and the OP-LOG (the sidecar's
`user_topo`: base candidate uid + the applied `edit_*` command sequence).
The BDB carries only the geometry.  Lose the sidecar JSON (or move the
flow) and the candidate still restores perfectly — but *how it was built*
is gone.

**Why it matters (modestly).** The op-log is the human-readable design
intent — "cloned candidate 2, re-spanned the trunk to pair A, moved the
stub layer to M7."  Useful for a teammate auditing a checkpoint months
later ("why does bundle 34 have this odd U?"), for regenerating the
topology against a CHANGED floorplan (geometry can't adapt; ops replayed
against new block positions might), and for folding a GUI session into a
`.buda` script when the original console log is gone.

**Shape of the work.** Small — about a day, mostly tests.  The BDB `meta`
table (key-value) already exists: store `user_ops:<bundle_id>:<topo_uid>`
→ JSON at `edit_commit`, and teach `load_pipeline` to print a one-liner
("candidate 6 of bundle 1 is USER, built from 4 ops — `dump_user_ops 1`
to see them").  No schema version bump if it rides `meta`.  **Value:
documentation/forensics, not correctness** — which is exactly why it was
deferred.

### 2. Explorer (GUI) hier frames — ✅ RESOLVED

**Landed** (follow-on batch): `TopologyExplorer` takes an `fp_resolver`
(the session's `_make_topo_fp_resolver`) and re-points `self.fp` at each
shown bundle's OWN floorplan on open and on every bundle switch
(`_sync_bundle_fp`) — a cell-local template draws and edits against its
cell frame (blocks, bundle Hanan grid, `S`/`P` hit-tests, edit verdicts all
follow the swap; the view re-homes when the frame changes), while flat
bundles, expanded instance wrappers, and same-level hier bundles keep the
session floorplan.  Both construction sites forward the resolver
(`visualize_topologies` directly; `visualize` → `BudaVisualizer` → the `v`
explorer).  Convergence bonus: GUI template edits record CELL-FRAME ops in
the sidecar, which the frame-aware CLI replay (opens item 12) rebuilds on a
fresh flow run — same uid, pin resolved.  Tests:
`test_topo_explorer_hier_frames.py` (cell-frame open, GUI gesture set on a
template, frame swap template↔level-0, GUI-edit → sidecar → rerun round
trip).

**The original gap (for the record).** The CLI edit session is frame-aware — `edit_topology` on a
cell-local template resolves `session._edit_fp` and every op verdicts/taps
in cell coordinates.  The EXPLORER still receives the flat session
floorplan at construction.  Open the topology explorer in a hier session
and press `e` on a cell-local template: the candidate draws at cell
coordinates (say, x 0–420) against a backdrop of top-level blocks at
absolute coordinates — wrong blocks, wrong scale, and clicking a block
for `S`/`P` hits the wrong namespace.

**Why it matters.** This is the real UX hole.  The whole dd-detour
interaction language — hover-arm trunks (`T`/`Y`), click-to-pin spans
(`P`), segment anchors — currently only works correctly on flat bundles
(and expanded instances, which use absolute coords).  A designer wanting
to hand-tune a TEMPLATE interactively must drop to the CLI commands.
Highest-value of the three for a GUI-first workflow.

**Shape of the work.** Medium — a few days including tests (the headless
`_on_key` harness extends naturally).  The explorer needs a per-bundle
floorplan: on bundle switch (`[`/`]`), resolve via the same
`_make_topo_fp_resolver` and swap `self.fp` AND the drawn
blocks/Hanan-grid/busterm names (most derive from `self.fp`, so they
follow the swap).  Subtleties: selection sync with the main viz window
(absolute-frame) needs care; the view must re-home when the frame changes
(cell frame vs die extents differ wildly); the `[Rerun]` path must keep
using the session floorplan.

### 3. Per-instance candidate pools (un-pinned instance commits)

**The gap.** Post-expansion, each instance wrapper persists ONLY its
selected topology — by design (expanded rows are the planner's decision
record, not a candidate store).  Edit an instance's topology and commit
WITHOUT `pin`, and the new USER candidate exists in memory but is
deliberately not persisted (the commit prints a loud note rather than
pretend).

**Why it matters (rarely).** Only one workflow: "I built two alternative
hand shapes for instance 7, want BOTH in the BDB, and will decide next
session."  Today: pin one (persisted) and rebuild the other later — or
keep both by editing PRE-expansion at the template level, where full
pools ARE persisted.

**Shape of the work.** The largest of the three — it changes what an
expanded bundle row MEANS.  Persisting full per-instance pools multiplies
topology rows by instance count (big2-scale designs would feel it),
complicates the loader's compact-index remapping (the Codex #136
territory), and blurs the template-vs-instance provenance the bottom-up
machinery relies on.  **Recommendation: don't build it until a real
workflow demands it** — the pin-to-persist rule is coherent and loudly
surfaced.
