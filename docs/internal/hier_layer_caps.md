# Per-Cell Layer Caps and Fractional Layer Shares — Design Plan

Status: **PROPOSED PLAN** — awaiting review of the open questions in §10.
Scope: reserve higher routing layers for higher levels of the hierarchy in
bottom-up flows.  Each cell gets a **per-layer policy**: for every layer,
FULL use (1.0), NO use (0), or — the practice-rooted generalization — a
**fractional share** of that layer's tracks (e.g. a cell capped at M3 may
additionally use 30% of M4's tracks and 10% of M5's inside its footprint).
The simple **layer cap** (a ceiling in the metal stack) is the shorthand:
share 1.0 at and below the cap, 0 above.  Leaf cells route in e.g. M2/M3
only; the next level up adds M4/M5; the top adds M6/M7 — and where the
design is wiring-limited, a level may lease a measured slice of the layers
above it instead of hitting a hard wall.  This is the standard custom-layout
BKM: lower levels must not consume the routing resource the levels above
them will need — but the resource wall is a budget, not a cliff.

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

# Fractional share: this cell may additionally use PCT percent of the given
# layer's signal tracks within its footprint.  Overrides the cap for that
# layer (a cap is just shorthand for shares 1.0 / 0).
set_cell_layer_share <cell> <layer_id|layer_name> <pct>

# Convenience: assign caps by hierarchy level in one line, deepest first.
# Equivalent to per-cell caps derived from each cell's depth.
set_layer_caps_by_depth <cap_deepest> [<cap_next> ...]

# Clearing: '*  off' removes every cap/share (byte-identical to no caps).
set_cell_layer_cap * off
```

* `set_cell_layer_cap dnuts1 M3` — cell `dnuts1`'s bundles use M2/M3 only.
* `set_cell_layer_cap dnuts1 M3` + `set_cell_layer_share dnuts1 M4 30` +
  `set_cell_layer_share dnuts1 M5 10` — the wiring-limited form: full M2/M3,
  plus a 30% slice of M4 and a 10% slice of M5 inside the cell's footprint.
  The parent level keeps everything the child does not consume — the share
  is a **budget on the child**, not a reservation grant (see §3b).
* `*` sets the default cap for cells without an explicit one; explicit wins.
* The command **hard-errors** unless the resulting policy grants at least one
  H and one V routing layer with share > 0 (an unroutable policy must fail
  LOUD at declaration, not surface as BEST_EFFORT commits later).  A share
  that rounds to **zero whole tracks per pattern period** on its layer is
  likewise a declaration-time error, not a silent no-op (§6.6).
* Declared any time before `run_planner hier`; like `set_bottom_up`, it is a
  cell-template attribute, so rotation-class clone templates (`<cell>90`)
  **inherit the base cell's policy**.

### Policy model

The per-cell policy is a vector `share(layer) ∈ [0,1]` with `1` at and below
the cap, `0` above, and explicit fractions where declared.  The **binary
subset** (`share ∈ {0,1}`) is the mask design of §3 — Phase 1 — and every
mechanism below degrades to it exactly when no fractional share exists.

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

## 3b. Fractional shares — the thinned-pattern realization

The load-bearing observation: **every stage already reads track supply
through the effective `TrackPattern`** — the planner's `signal_tracks`
capacity (`count_signal_tracks_in` at `congestion_planner.cpp:550`), NUTS's
per-bit width (`LayerStack::eff_bus_width` from the pattern-derived
`bit_pitch`) and dilution fallback, DetailedNUTS's placement
(`signal_tracks_in`), and the dead-span/supply tests
(`count_signal_tracks_in_span`).  And the slot model already has a
non-routable type: only `SIGNAL` is routable; `CUSTOM` is a first-class
blocked slot (`routing_grid.h:32`).

So a fractional share needs **no new capacity arithmetic anywhere**: realize
`share(L) = s` for a cell as a **derived thinned pattern** — the layer's
global pattern with only `ceil(s × n_signal)` of each period's SIGNAL slots
kept SIGNAL, the rest re-typed CUSTOM — installed as the view the cell-local
solve sees on that layer.  Every consumer then prices and places against the
thinned supply automatically, at every stage, with one mechanism:

* planner `signal_tracks` capacity counts only the kept slots;
* NUTS `bit_pitch` = `unit_pitch / n_kept` — the abstract width honestly
  reflects the share (a 30% share makes each bit ~3.3× wider in channel
  terms, which is exactly the right pressure to keep the cell mostly on its
  own layers and spill upward only when genuinely wiring-limited);
* DNUTS can only land bits on kept slots;
* the dead-span discriminator and `check_template_tracks` pool counts see
  the thinned supply with no code change beyond using the view.

### Slot allocation policy (which tracks the child gets)

Deterministic and pattern-periodic: within each repeating unit, keep the
**first contiguous run** of `ceil(s × n_signal)` SIGNAL slots (per power
rail group), re-type the rest.  Contiguous-per-period rather than an
interleaved comb because the parent's leftover then also stays contiguous
per period — an interleaved comb would fragment the parent's supply and
break `timing_critical`'s contiguous-window requirement for wide parent
buses.  Periodicity preserves `unit_pitch`, so **instance phase congruence
and `align_bottom_up` are untouched** (the thinned pattern has the same
period and origin as the global one).

### Two enforcement tiers, by where the bundle is planned

* **Tier 1 — cell-local solves (the main consumer).**  The bottom-up
  template solve already constructs its own planner and NUTS engine
  (`hier.py:1817,1993`); it additionally receives a **derived
  `RoutingGridStack` view**: the global stack with the cell's thinned
  patterns substituted on shared layers.  Class-move re-plans re-run the
  cell-local solve and inherit the view.  This is exact, DNUTS-real, and
  cheap — and it is safe *here* precisely because the cell-local solve is a
  closed context (the objection that killed view-based enforcement for the
  binary mask in §1 does not apply: the mask must hold in the GLOBAL solve
  too, where no per-solve view exists; a fractional share below applies
  there differently).
* **Tier 2 — the global solve** (top-down-planned bundles of a shared-layer
  cell, cross-level bundles, and a RELEASE-pass instance re-solved
  individually).  No per-solve grid view exists, so the share is enforced as
  a **capacity scale in the planner**: for a wrapper with `share(L)=s`, the
  slide-clamped band capacity on layer L inside the instance bbox is
  `s × capacity` (one multiply in `CongestionPlanner::capacity()`'s
  track-mode branch, keyed by the wrapper under evaluation).  This is a
  *per-bundle* approximation of the collective budget — several bundles of
  one cell could together exceed `s` — documented as such; the exact
  collective form (per-band usage split by budget group) is deliberately
  deferred until a measured flow needs it (§10 Q6).  For a released
  instance, Tier 2 plus the copied siblings' keepouts in practice bounds it
  tightly.

### The share is a budget, not a reservation

The parent is **not** restricted to the complement: it sees the full
pattern minus the child's *actual placed routing* (the existing copied-
routing keepouts).  A child that uses 12% of its 30% M4 slice leaves 88% of
M4 to the parent.  This is the wiring-limited-design semantics the BKM
wants — the guarantee the parent needs is an upper bound on child
consumption, which the thinned pattern enforces physically.  (If a hard
parent-side reservation is ever wanted, it is the same mechanism pointed
the other way — a parent-view thinning — and becomes a floor knob, §10 Q3.)

---

## 4. Persistence

* Schema (v20): a `cell_layer_share` table — `(cell_id, layer_id, share
  REAL)` — rather than a single column, since the policy is a vector.  A
  plain cap stores only the rows it implies differ from default (share 0
  above the cap is representable implicitly via a `cell.layer_cap INTEGER
  DEFAULT -1` column for the common case + share rows for the fractional
  exceptions; precedent for the column: `cell.bottom_up` v17 at
  `src/bdb.cpp:390,702`).  The `*` default cap is session state persisted in
  BDB meta (`layer_cap_default`), like other flow knob memos.
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
6. **Share that rounds to zero kept tracks per period** (e.g. 5% of a
   4-signal-slot pattern) — declaration-time hard error naming the layer,
   the period's slot count, and the minimum meaningful share.  A share the
   pattern cannot express must not silently become a cap.
7. **Tier-2 over-consumption** (several global-solve bundles of one cell
   collectively exceeding the per-bundle-scaled share) — an advisory audit
   after `run_planner hier` sums each capped cell's committed usage per
   shared layer against `share × supply` in its bbox and WARNs on excess,
   so the per-bundle approximation's slack is visible, never silent.

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
8. **Unit — thinned pattern**: `share 0.3` on an 8-signal-slot period keeps
   `ceil(2.4)=3` contiguous SIGNAL slots per period, same `unit_pitch` and
   origin; `count_signal_tracks_in` and `eff_bus_width` reflect it;
   `share 1.0` is the identity pattern (bit-identical view).
9. **Bottom-up integration — shared layer (mid)**: leaf capped at M3 with
   `share M4 30`; assert (a) every leaf-template bit on M4 lands on a kept
   slot, (b) leaf M4 usage ≤ 30% of M4's tracks in the instance bbox,
   (c) the parent uses M4 tracks the child left free (the budget-not-
   reservation semantics), (d) phase congruence across instances still
   holds (`check_template_tracks` clean on the thinned view).
10. **Tier-2 audit fires**: a constructed over-consumption WARNs.

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
5. **Slot allocation comb** — contiguous-per-period is the plan (parent
   contiguity, `timing_critical` safety).  Alternative: an offset parameter
   so *sibling cell types* sharing the same parent layer get disjoint runs
   (cell A the first 30%, cell B the next 20%) instead of all children
   competing for the same low slots.  Worth deciding before Phase 3; the
   pattern derivation supports an offset trivially.
6. **Tier-2 exactness** — the global-solve share is enforced per-bundle
   (capacity scale), which several bundles of one cell can collectively
   exceed; the audit (§6.7) makes the slack visible.  Exact collective
   budgeting needs per-band usage split by budget group inside `GlobalCut`
   — real complexity.  Proposal: ship the approximation + audit, revisit
   only if a measured flow shows the audit warning with real DNUTS damage.
7. **Do shares imply demand-reservation changes?**  An unplanned capped
   cell's reservation (§3, F3) should park `share-weighted` width on shared
   layers — proposal: reserve `s × eff_width` on a shared layer's bands,
   full width on fully-owned layers.  Confirm the weighting.

## 11. Phasing

* **Phase 1 — binary core (C++)**: mask field + effective-TOP scoring
  context; enforcement in `optimize_topologies` / ladder / reservations /
  `post_nuts`; `set_cell_layer_cap` command + validation; no-cap
  byte-identity corpus run.  *Deliverable: capped flat-hier flow routes
  under caps; corpus unchanged without caps.*
* **Phase 2 — bottom-up wiring (Python)**: mask resolution at template /
  expansion / clone / load_pipeline; cell-local solves; `align_bottom_up`
  LCM; `check_template_tracks` scoping; persistence v20.  *Deliverable:
  capped `mix2_fast_bottomup` end-to-end with per-layer WL evidence.*
* **Phase 3 — fractional shares**: thinned-pattern derivation +
  `set_cell_layer_share`; the Tier-1 grid view for cell-local solves; the
  Tier-2 capacity scale + over-consumption audit; share-aware reservations
  (Q7).  *Deliverable: the wiring-limited leaf (M3 cap + 30% M4 + 10% M5)
  routes end to end; budget-not-reservation semantics tested.*
* **Phase 4 — healer compliance**: dead-span escalation, width gate pitch,
  release/class-move verification, `LAYER_CAP` advisory check, cap-aware
  reporting (`dump_hbundles`, ladder warnings) — all over the full policy
  vector, not just the binary mask.  *Deliverable: healers never violate a
  cap or exceed a share; violations impossible by audit.*
* **Phase 5 — flows, study, docs**: capped AND shared QoR vehicles (the
  share study wants a deliberately wiring-limited leaf so the M4 spill is
  exercised), the measurement table, CLAUDE.md command rows, BDB_REFERENCE
  schema, HIER_* doc updates, `set_layer_caps_by_depth` once Q4 is settled.

Each phase lands with its tests green and the no-cap corpus byte-identical;
Phases 1–2 are the minimum usable capped flow, Phase 3 adds the shared form.
