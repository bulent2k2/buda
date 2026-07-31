# Per-Cell Layer Caps — Design Plan

Status: **PROPOSED PLAN** — awaiting review of the open questions in §10.
Scope: reserve higher routing layers for higher levels of the hierarchy in
bottom-up flows.  Each cell gets a **layer cap** (a ceiling in the metal
stack); its cell-local interconnect may only use layers at or below the cap.
Leaf cells route in e.g. M2/M3 only; the next level up adds M4/M5; the top
adds M6/M7.  This is the standard custom-layout BKM: lower levels must not
consume the routing resource the levels above them will need, and the copied
leaf routing then leaves the high layers *entirely* clean over every instance
instead of merely keeping keepout-shaped holes in them.

Companion docs: [hier_bottom_up_planning.md](hier_bottom_up_planning.md) (the
bottom-up template machinery this builds on),
[HIER_PLANNER.md](../HIER_PLANNER.md) (top-down expansion + ladder),
[bottomup_healer_templates.md](bottomup_healer_templates.md) (class/release
healer passes that must stay cap-compliant).

---

## 1. Current state and the gap

Layer and track definitions are **flat and global**.  One `LayerStack`
(`def_layer`) and one `RoutingGridStack` (`def_track_pattern`) serve every
bundle at every depth:

| # | Fact | Where |
|---|------|-------|
| F1 | The planner enumerates candidate layers for every segment from the full stack — `get_layer_ids_by_dir(dir)` — with no per-bundle restriction.  TOP/LOW typing is a global per-layer attribute driving trunk preference, `base_cost_non_top`, `kBalance`, `kHeight`, and the escalation ladder. | `src/congestion_planner.cpp:153,1461` (enumeration), `:1522-1546` (TOP cost terms) |
| F2 | The cell-local bottom-up solve constructs its own planner and NUTS engine **from the same global stack**: `CongestionPlanner(fp, self.layers)` / `NUTSEngine(fp, self.layers)`. | `src/buda_session/hier.py:1817,1993` |
| F3 | Unplanned cell-local bundles park **demand reservations on global-TOP bands** inside their instance bbox — including bands on layers the cell will never use under a cap. | `src/congestion_planner.cpp:618-628` (`top_height_rank`, TOP-band collection) |
| F4 | The dead-span escalation moves a dead LOW segment to the **cheapest same-direction global-TOP layer** — under a cap, an illegal move for a leaf-cell segment. | `src/buda_session/nutsflow.py` (`_escalate_dead_low_segments`, `_cheapest_top`) |
| F5 | `run_planner post_nuts` reassigns stub layers over the full stack. | `src/buda_cmds/planner_cmds.py` (post_nuts path) |
| F6 | `align_bottom_up` phase-aligns instances per axis on the LCM of **that direction's full layer-pitch set** — a cap would shrink the LCM (leaf cells need only their own layers' phases to agree). | `src/buda_session/hier.py` (align path); CLAUDE.md `align_bottom_up` row |
| F7 | The `cell` table persists `bottom_up` (v17) but has no layer attribute. | `src/bdb.cpp:390,702` |
| F8 | Dogleg jogs **reuse already-assigned layers** (trunk pieces keep `h_layer`, the jog rides a stub's assigned layer) — no independent layer choice, so doglegs are cap-compliant for free once assignment is. | `src/nuts_dogleg.cpp:204-222` |
| F9 | ripup's width gate takes the **minimum bit-pitch over all same-direction layers** as its best case; under a cap the minimum must be over the *allowed* set or the bound is unsound (an allowed-only-coarse-pitch bundle would be under-estimated). | `src/buda_session/ripup.py` (`_rr_width_infeasible`) |

The tempting shortcut — hand each cell-local solve a **filtered copy** of the
LayerStack (the F2 seam) — is rejected as the primary mechanism.  It covers
only the template solve itself: the *global* planner phases that also touch
bottom-up bundles (the release pass's individual re-solve, class-move
re-plans, `refine_selection`, ripup trials, negotiate's `replan_bundle`,
`post_nuts`) all run against the session stack and would silently escalate a
leaf bundle to M6.  Every one of those paths flows through the planner core's
layer enumeration — so the enforcement belongs **in the core, keyed by the
bundle**.

---

## 2. User-visible design

### Commands

```buda
# Ceiling per cell: this cell's OWN interconnect may use layers with id <= cap.
set_cell_layer_cap <cell>|* <layer_id|layer_name>

# Convenience: assign caps by hierarchy level in one line, deepest first.
# Equivalent to per-cell caps derived from each cell's depth.
set_layer_caps_by_depth <cap_deepest> [<cap_next> ...]

# Clearing: '*  off' removes every cap (byte-identical to no caps).
set_cell_layer_cap * off
```

* `set_cell_layer_cap dnuts1 M3` — cell `dnuts1`'s bundles use M2/M3 only.
* `*` sets the default cap for cells without an explicit one; explicit wins.
* The command **hard-errors** unless the capped set contains at least one H
  and one V routing layer (an unroutable cap must fail LOUD at declaration,
  not surface as BEST_EFFORT commits later).
* Declared any time before `run_planner hier`; like `set_bottom_up`, it is a
  cell-template attribute, so rotation-class clone templates (`<cell>90`)
  **inherit the base cell's cap**.

### Semantics

1. **Cap = id ceiling.**  Metal ids are height-ordered by convention
   (M2=2 … M7=7), so a single integer expresses "this level and below".  An
   explicit allow-list variant is deliberately deferred (§10 Q2).
2. **Which bundles a cap governs — the owning-frame rule.**  A bundle is
   capped by the cell in whose frame it is planned (`cell_context`):
   * cell-local template bundles (and their expanded per-instance wrappers,
     and bottom-up copies) → that cell's cap;
   * cross-level bundles → the cap of the **common-ancestor cell** whose
     frame they are planned in (they may legitimately use the higher layers
     of the level they belong to);
   * top-level bundles → the top cell's cap, or uncapped if none.
   This is the rule that makes "reserve higher layers for higher levels"
   compositional: a level-k bundle sees exactly the union its level is
   entitled to.
3. **Effective-TOP within a cap.**  The planner's cost model and the healers
   need TOP layers to exist *inside the capped view*:
   * if the allowed set contains globally-TOP layers, those are the
     effective-TOP set (unchanged semantics — e.g. a mid-level cap at M5 in
     a stack where M4/M5 are TOP);
   * else the **highest allowed layer per direction is promoted to
     effective-TOP for that bundle** (a leaf capped at M3 in a stack where
     M2/M3 are LOW treats M2/M3 as its trunk layers).
   Promotion is per-bundle scoring context, never a mutation of the global
   `Layer::type` — two cells with different caps coexist in one solve.
4. **No cap anywhere ⇒ byte-identical.**  The mask is absent by default and
   every enforcement site short-circuits on "no mask".  This is the corpus
   guard for the whole feature.

---

## 3. Core mechanism: a per-bundle allowed-layer mask

New field on the wrapper (beside `hier.locked` / `hier` metadata):

```cpp
// BundleWrapper::input (or hier) — empty = uncapped (all layers allowed).
std::vector<int> allowed_layers;   // resolved from the owning cell's cap
int              layer_cap = -1;   // the declared ceiling, for reporting
```

Resolved once, at the same places the bottom-up machinery already resolves
per-cell facts: template creation, `_expand_hier_bundles` (per-instance
wrappers inherit), rotation-class clone creation, and `load_pipeline`
rehydration.  The mask is **data on the bundle**, so every consumer — the
global planner, cell-local planners, healers, ripup trials — sees it without
plumbing a second LayerStack anywhere.

### Enforcement inventory

This is the complete list of places that choose or assume a layer; each gets
a mask check (or an argument why none is needed):

| Site | Change |
|------|--------|
| `CongestionPlanner::optimize_topologies` per-segment layer loop (`:1461`) | skip `lid` not in the wrapper's mask.  STRICT/rip-up/ALLOW_OVERFLOW/BEST_EFFORT ladder then operates on the reduced set automatically — including `replan_bundle`, `replan_bundle_ripup`, and negotiate, which reuse it. |
| TOP-dependent cost terms (`base_cost_non_top`, `kBalance`, `kHeight`, trunk preference, `top_height_rank`) | evaluate against the wrapper's **effective-TOP** set (§2.3) instead of raw `is_top` when a mask is present. |
| Demand reservations (F3) | park the unplanned cell-local width on the cell's **effective-TOP** bands only.  This is a correctness *and* QoR improvement: capped leaves stop reserving M6/M7 room they can never use. |
| `run_planner post_nuts` (F5) | candidate target layers filtered by each bundle's mask. |
| Dead-span escalation (F4) | "cheapest same-direction TOP" becomes "cheapest same-direction **effective-TOP within the bundle's mask**".  If the dead segment already sits on the mask's ceiling, report LOUD and leave it (the cap made it unhealable by layer — that is the declared trade, and it must be visible, not silently violated). |
| ripup width gate (F9) | minimum bit-pitch over the **allowed** same-direction layers. |
| Dogleg jog layer (F8) | no change — inherits assigned layers, compliant by construction. |
| DNUTS / check_design / viz | no change — they consume assigned layers.  `dump_hbundles` gains a `cap=M3` annotation per bundle; `check_design` gains an advisory `LAYER_CAP` violation (a placed segment above its bundle's cap — should be impossible, defense-in-depth like the keepout audit). |
| Cell-local solves (F2) | wrappers carry masks, so the same core enforcement applies.  **Additionally** the cell-local `NUTSEngine`/planner may be handed a filtered stack as an optimization (smaller per-layer solve loop), but that is optional and never the correctness mechanism. |
| `align_bottom_up` (F6) | per-axis phase LCM computed over the **cell's allowed** layer pitches.  Strictly weaker constraint ⇒ strictly smaller (or equal) nudges; existing behavior when uncapped. |
| `check_template_tracks` | compares per-layer pools across instances; restrict the compared layer set to the cell's mask (layers the cell cannot use must not fail the uniformity check). |

---

## 4. Persistence

* Schema: `ALTER TABLE cell ADD COLUMN layer_cap INTEGER NOT NULL DEFAULT -1`
  (v20; precedent: `cell.bottom_up` v17 at `src/bdb.cpp:390,702`).  `-1` =
  uncapped.  The `*` default cap is session state persisted in BDB meta
  (`layer_cap_default`), like other flow knob memos.
* `load_pipeline [expanded]` re-resolves masks from the persisted caps before
  re-validating bundles — a resumed session must plan under the same caps.
* `save_bdb` / fixtures: the column appears in the diffable `.bdb.sql`; the
  fixture regenerator (`test/tests/data/build_fixtures.py`) needs no change
  beyond the version bump.
* Converters: `bdb2buda` (flat export) has no cells — no change.
  `buda2bdb` writes cells — carries the cap column through untouched.

---

## 5. Interaction with the bottom-up machinery

* **Copies and keepouts.**  Copied instance routing already becomes
  layer-tagged keepout zones for higher levels (plan §4.3, gap G3 closed:
  explicitly tagged zones are enforced on any layer).  Under caps the zones
  simply never mention layers above the cell's ceiling, and the parent level
  plans over the instances on its own (higher) layers with **no detour at
  all** where the leaf metal lies below its floor.  That separation is the
  entire point of the BKM, and it should be *visible*: `report_wirelength`'s
  per-layer breakdown is the natural evidence (leaf WL confined to ≤cap).
* **Congruence / rotation classes.**  A cap is a cell attribute ⇒ identical
  across instances by construction; `<cell>90` clones inherit it.  The
  90°-rotation swaps H and V — the H/V-coverage validation of §2 must be
  re-checked against the clone's rotated direction assignment (a cap of
  {M2 H, M3 V} rotated means the clone routes {M2 V?, …} — no: layer
  directions are global; rotation transforms geometry, not the stack.  The
  clone's candidates are generated from the rotated floorplan against the
  same H/V layers, so the same mask applies verbatim.  No special case, but
  a test pins it.)
* **Healer class moves / release pass.**  Both re-plan through the planner
  core with the same wrappers ⇒ mask enforced.  The release pass's
  "individual re-solve" of one instance keeps that instance's mask (release
  breaks uniformity, never the cap).
* **check_template_tracks `independent` mode** — solves misaligned instances
  individually; masks ride along unchanged.

---

## 6. Failure modes (all LOUD, per house style)

1. **Cap without both directions** — hard error at `set_cell_layer_cap`.
2. **Capped bundle infeasible under STRICT** — the existing ladder already
   reports ALLOW_OVERFLOW/BEST_EFFORT commits with WARNINGs; the message
   gains the cap so the user sees *why* the layer set was small.
3. **Dead span at the cap ceiling** — reported, not silently escalated past
   the cap (see §3).  The report names the bundle, segment, cap, and the
   layer escalation would have wanted.
4. **Cap declared for an unknown cell** — hard error (matches
   `set_bottom_up`'s treatment).
5. **Cap tighter than already-persisted routing** (e.g. cap added, then
   `load_pipeline` of a checkpoint routed above it) — validation pass
   reports every violating persisted segment and refuses to continue without
   an explicit `run_planner hier` re-plan; never silently keeps illegal
   metal.

---

## 7. What this deliberately does NOT do (v1 scope cuts)

* **No per-cell track patterns.**  Caps restrict *which* layers a cell uses,
  not the patterns on them.  Per-cell/region patterns already exist via
  `add_grid_override`; composing caps with overrides needs no new mechanism.
* **No via/pin-access modeling.**  BUDA's busterm-face model has no explicit
  via stacks; a parent landing on a capped child's face is unchanged.
* **No automatic cap inference from depth.**  `set_layer_caps_by_depth` is a
  thin convenience mapping depths→caps at declaration time; nothing infers
  caps dynamically.
* **No partial-span caps** (a cell using M4 only in a region).  Region
  scoping stays the keepout/override system's job.

---

## 8. Tests

1. **Unit — mask enforcement**: a two-bundle fixture where bundle A is
   capped at M3 and B is uncapped; assert every A segment's assigned layer
   ≤ 3 across STRICT and the escalation ladder, B unchanged.
2. **Unit — effective-TOP promotion**: cap M3 over an all-LOW {M2,M3};
   assert the trunk lands on the promoted pair and `base_cost_non_top` does
   not tax it.
3. **Unit — declaration validation**: single-direction cap hard-errors;
   unknown cell hard-errors; `* off` restores byte-identity.
4. **Unit — escalation compliance**: a dead LOW segment in a capped bundle
   escalates only within the mask; at the ceiling it reports instead.
5. **Bottom-up integration (mid)**: `mix2_fast_bottomup` variant with leaf
   cells capped at M3: per-layer `report_wirelength` shows zero leaf-template
   WL above M3; endpoint no worse than the uncapped flow's (or the delta
   recorded); rotation-class clone inherits the cap.
6. **Persistence round-trip**: cap set → `save_bdb` → `load_pipeline
   expanded` → masks identical; v19→v20 migration keeps old fixtures loading.
7. **Byte-identity corpus guard**: full `qor_corpus.py --compare` with no
   caps declared — **must be 0 better / 0 worse / all unchanged with WL
   +0.00%** (the F-sites all short-circuit on empty mask).

## 9. Measurement plan

* New QoR vehicles: `flow/rnr/mix2_fast_bottomup_caps.buda` (leaf M3 / mid
  M5 / top M7) and a capped `flow/chip/chip_bottomup_caps.buda` (the
  432-leaf corpus vehicle is where level separation should pay most).
* Metrics: endpoints (overlaps/unplaced/viol_bundles), per-layer WL
  breakdown (the separation evidence), healer iteration counts (expected
  DOWN at upper levels: less contention on high layers), runtime.
* Honest expectations, stated up front: capping *removes freedom*, so leaf
  endpoints may degrade where M2/M3 supply is genuinely tight — the BKM
  trades leaf-level slack for top-level routability and predictability.  The
  study reports both directions; caps stay **opt-in** regardless (the
  no-cap byte-identity guarantee makes that free).

## 10. Open questions before implementation

1. **Cap granularity** — is the id-ceiling sufficient, or do you want an
   explicit allow-list from day one (e.g. a cell allowed {M2,M3,M6} skipping
   the middle)?  Ceiling is simpler and matches the stated BKM; the mask
   mechanism underneath supports lists whenever the command grows.
2. **Cross-level default** — plan says cross-level bundles take the
   common-ancestor's cap.  Alternative: uncapped unless explicitly set.
   Ancestor-cap is the compositional reading of the BKM; confirm.
3. **Floor as well as ceiling?**  The BKM as stated reserves high layers for
   high levels; should upper levels also be pushed OFF the leaf layers (a
   floor, e.g. top-level bundles use ≥M4 except for pin access)?  The mask
   mechanism supports it (`allowed = [floor..cap]`), but it changes stub
   behavior (short stubs love cheap LOW layers) — I would measure ceiling-only
   first and add floors as a follow-up knob if leaf-layer pollution from
   above shows up in the per-layer WL breakdown.
4. **Depth convenience command** — `set_layer_caps_by_depth` maps BDB depth
   to caps.  Depth counting must be pinned down (deepest-first as written,
   or top-first?) — trivially bikesheddable, needs one decision.

## 11. Phasing

* **Phase 1 — core (C++)**: mask field + effective-TOP scoring context;
  enforcement in `optimize_topologies` / ladder / reservations /
  `post_nuts`; `set_cell_layer_cap` command + validation; no-cap
  byte-identity corpus run.  *Deliverable: capped flat-hier flow routes
  under caps; corpus unchanged without caps.*
* **Phase 2 — bottom-up wiring (Python)**: mask resolution at template /
  expansion / clone / load_pipeline; cell-local solves; `align_bottom_up`
  LCM; `check_template_tracks` scoping; persistence v20.  *Deliverable:
  capped `mix2_fast_bottomup` end-to-end with per-layer WL evidence.*
* **Phase 3 — healer compliance**: dead-span escalation, width gate pitch,
  release/class-move verification, `LAYER_CAP` advisory check, cap-aware
  reporting (`dump_hbundles`, ladder warnings).  *Deliverable: healers never
  violate a cap; violations impossible by audit.*
* **Phase 4 — flows, study, docs**: the two capped QoR vehicles, the
  measurement table, CLAUDE.md command rows, BDB_REFERENCE schema,
  HIER_* doc updates, `set_layer_caps_by_depth` once Q4 is settled.

Each phase lands with its tests green and the no-cap corpus byte-identical;
Phases 1–2 are the minimum for a usable capped bottom-up flow.
