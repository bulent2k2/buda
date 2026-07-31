# Per-Cell Layer Caps and Fractional Layer Shares — Design Plan

Status: **PROPOSED PLAN** — awaiting review of the per-phase open questions
in §13.  Doc-only: no code changes ride with this plan.

Companion docs: [hier_bottom_up_planning.md](hier_bottom_up_planning.md) (the
bottom-up template machinery this builds on),
[HIER_PLANNER.md](../HIER_PLANNER.md) (top-down expansion + ladder),
[bottomup_healer_templates.md](bottomup_healer_templates.md) (class/release
healer passes that must stay cap-compliant).

---

## 1. Problem definition

BUDA's layer and track definitions are **flat and global**: one `LayerStack`
(`def_layer`) and one `RoutingGridStack` (`def_track_pattern`) serve every
bundle at every depth of the hierarchy.  A leaf cell's 4-bit local bus and
the top level's 64-bit cross-chip trunk choose from the same layers under
the same TOP/LOW economics.  Nothing expresses "this cell may not use M6",
and nothing stops a cell-local solve — or a healer acting on its bundles
later — from escalating leaf-level wires onto the highest metals.

Concretely, for a stack M2…M7 (M2/M3 LOW, M4–M7 TOP), a bottom-up flow
today plans each marked cell's local interconnect against **all six
layers**.  The copied instance routing then blocks whatever it landed on as
keepouts for the levels above — including any high-layer metal the leaf did
not need but was free to take.

The problem: **provide per-cell control over which layers — and how much of
each layer — a cell's own interconnect may consume**, enforced everywhere a
layer is chosen (planner, healers, escalations), persisted with the design,
and byte-identical to today's behavior when unused.

## 2. Motivation

Reserving higher routing layers for higher levels of the design is a
standard custom-layout best-known method:

* **Resource discipline.**  Lower levels must not consume the routing
  resource the levels above them will need.  A leaf that routes entirely in
  M2/M3 leaves M4+ *completely* clean over every one of its instances —
  the parent plans over them with no detours at all — instead of leaving
  keepout-shaped holes wherever the leaf happened to wander upward.
* **Predictability and reuse.**  A hard per-cell layer budget makes a
  template's resource footprint a *contract*: any parent can instantiate it
  knowing exactly which layers remain free.  That is what makes bottom-up
  solve-once-copy-everywhere composition scale.
* **Level-matched economics.**  Short local nets belong on thin cheap
  metal; long global trunks belong on thick fast metal.  The flat stack
  lets the planner make locally-optimal choices that are globally wrong.
* **The wiring-limited escape valve (the generalization).**  A hard cap is
  a cliff.  In wiring-limited designs the practice is a *budget*: a cell
  capped at M3 may be granted, say, 30% of M4's tracks and 10% of M5's
  within its footprint — layer **sharing** between parent and children,
  bounded so the parent's supply stays predictable.

The target flow, concretely: leaf cells on M2/M3 only (or M2/M3 + a
declared slice of M4/M5), the next level adding M4/M5, the top level adding
M6/M7.

## 3. Current state and the gap

Facts verified in code, with the sites the plan must touch:

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
| F9 | ripup's width gate takes the **minimum bit-pitch over all same-direction layers** as its best case; under a cap the minimum must be over the *allowed* set or the bound is unsound. | `src/buda_session/ripup.py` (`_rr_width_infeasible`) |
| F10 | Every stage reads track supply through the effective `TrackPattern` — planner `signal_tracks` capacity, NUTS `bit_pitch`/dilution, DNUTS placement, dead-span pools, `check_template_tracks` — and `CUSTOM` is a first-class **non-routable** slot type (only `SIGNAL` is routable). | `src/congestion_planner.cpp:550`, `src/routing_grid.h:32`, `src/detailed_nuts.cpp` |

The tempting shortcut — hand each cell-local solve a **filtered copy** of
the LayerStack (the F2 seam) — is rejected as the primary mechanism for the
binary cap.  It covers only the template solve itself: the *global* planner
phases that also touch bottom-up bundles (the release pass's individual
re-solve, class-move re-plans, `refine_selection`, ripup trials, negotiate's
`replan_bundle`, `post_nuts`) all run against the session stack and would
silently escalate a leaf bundle to M6.  Every one of those paths flows
through the planner core's layer enumeration — so cap enforcement belongs
**in the core, keyed by the bundle** (§5).  Fractional shares get a
different, supply-side mechanism (§6) for which the per-solve view *is*
sound.

## 4. User-visible design

### Commands

```buda
# Band per cell: this cell's OWN interconnect defaults to FULL use of layers
# in [floor .. cap] and NO use outside it.  -min omitted = no floor.
set_cell_layer_cap <cell>|* <cap_layer> [-min <floor_layer>]

# Fractional share: override ANY layer's share — thin a layer inside the
# band (keep some for the parent) or grant a slice above/below it.
set_cell_layer_share <cell> <layer_id|layer_name> <pct>

# Convenience: assign caps by hierarchy level in one line, deepest first.
set_layer_caps_by_depth <cap_deepest> [<cap_next> ...]

# Clearing: '* off' removes every cap/share (byte-identical to no caps).
set_cell_layer_cap * off
```

* **Resolution rule (Q1 RESOLVED — band defaults, shares override):**
  `share(L)` = the explicit `set_cell_layer_share` if declared, else `1.0`
  for `L ∈ [floor..cap]`, else `0`.  The cap is NOT a hard bound on shares —
  a hard bound would outlaw the escape-valve case below, the original
  motivation for shares.  The derived ceiling `max(cap, highest shared
  layer)` is surfaced in reporting (`dump_hbundles`:
  `policy=[M3..M5], M4@30%`), so nothing widens silently.
* `set_cell_layer_cap dnuts1 M3` — cell `dnuts1`'s bundles use M2/M3 only.
* `set_cell_layer_cap mid M5 -min M3` — a BAND: M2 forbidden, M3–M5 full
  use (whether globally TOP or LOW — allowance is orthogonal to typing),
  nothing above M5.  The `-min` floor is the former Phase-5 floors question,
  resolved early by this syntax; its stub-economics caveat (short stubs
  favor cheap low metal) moves to the §12 measurement plan.
* `set_cell_layer_cap mid M5 -min M3` + `set_cell_layer_share mid M4 30` —
  thinning INSIDE the band: mid may use only 30% of M4's tracks so the
  levels above can share the layer.
* `set_cell_layer_cap dnuts1 M3` + `set_cell_layer_share dnuts1 M4 30` +
  `set_cell_layer_share dnuts1 M5 10` — the wiring-limited escape valve:
  full M2/M3, plus a 30% slice of M4 and a 10% slice of M5.  The parent
  keeps everything the child does not consume — the share is a **budget on
  the child**, not a reservation grant (§6).
* `*` sets the default cap for cells without an explicit one; explicit wins.
* Declaration **hard-errors** unless the resulting policy grants at least
  one H and one V routing layer with share > 0, and when a share rounds to
  zero whole tracks per pattern period (§9).
* Declared any time before `run_planner hier`; like `set_bottom_up`, a cap
  is a cell-template attribute, so rotation-class clone templates
  (`<cell>90`) **inherit the base cell's policy**.

### Policy model

The per-cell policy is a vector `share(layer) ∈ [0,1]` — `1` at and below
the cap, `0` above, explicit fractions where declared.  The **binary
subset** (`share ∈ {0,1}`) is the mask design of §5 and ships first; every
mechanism degrades to it exactly when no fractional share exists.

### Semantics

1. **Cap = id ceiling.**  Metal ids are height-ordered by convention
   (M2=2 … M7=7), so a single integer expresses "this level and below".
2. **Owning-frame rule.**  A bundle is governed by the policy of the cell
   in whose frame it is planned (`cell_context`): cell-local templates (and
   their expanded wrappers, and bottom-up copies) → that cell; cross-level
   bundles → the common-ancestor cell whose frame plans them; top-level
   bundles → the top cell's policy or unrestricted.  This is what makes
   "reserve higher layers for higher levels" compositional.
3. **Effective-TOP within a policy.**  The cost model and healers need TOP
   layers to exist inside the allowed view: if the allowed set contains
   globally-TOP layers those are effective-TOP; else the highest allowed
   layer per direction is **promoted to effective-TOP for that bundle** —
   a scoring context, never a mutation of the global `Layer::type`.
4. **No policy anywhere ⇒ byte-identical.**  Every enforcement site
   short-circuits on "no policy"; a full-corpus byte-identity run guards it.

## 5. Core mechanism (binary caps): a per-bundle allowed-layer mask

New field on the wrapper (beside `hier.locked`):

```cpp
// BundleWrapper::input (or hier) — empty = uncapped (all layers allowed).
std::vector<int> allowed_layers;   // resolved from the owning cell's policy
int              layer_cap = -1;   // the declared ceiling, for reporting
```

Resolved at template creation, `_expand_hier_bundles` (per-instance wrappers
inherit), rotation-class clone creation, and `load_pipeline` rehydration.
The mask is **data on the bundle**, so every consumer — global planner,
cell-local planners, healers, ripup trials — sees it without plumbing a
second LayerStack anywhere.

### Enforcement inventory

The complete list of places that choose or assume a layer; each gets a mask
check (or an argument why none is needed):

| Site | Change |
|------|--------|
| `CongestionPlanner::optimize_topologies` per-segment layer loop (`:1461`) | skip `lid` not in the wrapper's mask.  The STRICT/rip-up/ALLOW_OVERFLOW/BEST_EFFORT ladder then operates on the reduced set automatically — including `replan_bundle`, `replan_bundle_ripup`, and negotiate, which reuse it. |
| TOP-dependent cost terms (`base_cost_non_top`, `kBalance`, `kHeight`, trunk preference, `top_height_rank`) | evaluate against the wrapper's **effective-TOP** set (§4.3) instead of raw `is_top` when a mask is present. |
| Demand reservations (F3) | park the unplanned cell-local width on the cell's **effective-TOP** bands only — a correctness *and* QoR improvement: capped leaves stop reserving M6/M7 room they can never use. |
| `run_planner post_nuts` (F5) | candidate target layers filtered by each bundle's mask. |
| Dead-span escalation (F4) | "cheapest same-direction TOP" becomes "cheapest same-direction **effective-TOP within the bundle's mask**".  A dead segment already at the mask's ceiling is reported LOUD and left — the cap made it unhealable by layer; that declared trade must be visible, never silently violated.  **Lands in Phase 1, not Phase 4** (Codex P1 on #542): `run_nuts` under `healersAhead` auto-invokes this escalation (`src/buda_cmds/nuts_cmds.py:103-109`) outside any planner-core path, so a "usable capped flow" claim without it is false — a plain capped flow could silently move a governed segment above its cap on its very first `run_nuts`. |
| ripup width gate (F9) | minimum bit-pitch over the **allowed** same-direction layers. |
| Dogleg jog layer (F8) | no change — inherits assigned layers, compliant by construction. |
| DNUTS / check_design / viz | no change — they consume assigned layers.  `dump_hbundles` gains a `cap=M3` annotation; `check_design` gains an advisory `LAYER_CAP` violation (a placed segment above its bundle's cap — defense-in-depth, like the keepout audit). |
| Cell-local solves (F2) | wrappers carry masks, so core enforcement applies.  A filtered stack for the local solve is an optional optimization, never the correctness mechanism. |
| `align_bottom_up` (F6) | per-axis phase LCM over the **cell's allowed** layer pitches — strictly smaller (or equal) nudges; identical when uncapped. |
| `check_template_tracks` | restrict the compared per-layer pools to the cell's mask (layers the cell cannot use must not fail the uniformity check). |

## 6. Fractional shares: the thinned-pattern realization

The load-bearing observation is F10: **every stage already reads track
supply through the effective `TrackPattern`**, and the slot model already
has a first-class non-routable type.  So a fractional share needs **no new
capacity arithmetic anywhere**: realize `share(L) = s` for a cell as a
**derived thinned pattern** — the layer's global pattern with only
`floor(s × n_signal)` of each period's SIGNAL slots kept SIGNAL, the rest
re-typed CUSTOM — installed as the view the cell-local solve sees on that
layer.  **Floor, not ceil** (Codex P1 on #542): the share is a budget, so
rounding must never grant MORE than declared — `ceil` would turn 5% of a
4-slot period into 25% and 30% of 8 into 37.5%, contradicting the ≤ share
guarantee and making the zero-track declaration error unreachable.  With
floor, the granted fraction is always ≤ the declared one, and a share whose
floor is zero kept tracks is exactly the §9.6 declaration-time error.  Every consumer then prices and places against the thinned supply
automatically:

* planner `signal_tracks` capacity counts only the kept slots;
* NUTS `bit_pitch` = `unit_pitch / n_kept` — the abstract width honestly
  reflects the share (a 30%-declared share on an 8-slot period keeps 2
  slots, making each bit 4× wider in channel terms: exactly the economic
  pressure that keeps the cell on its own layers and spills upward only
  when genuinely wiring-limited).  **This requires a derived `LayerStack`,
  not just a derived grid** (Codex P1 on #542): `bit_pitch` is copied into
  the session `LayerStack` once at `def_track_pattern`
  (`src/buda_cmds/grid_cmds.py:110-116`) and `eff_bus_width` reads that
  stored value (`src/layering.cpp:67-71`) — thinning only the
  `RoutingGridStack` would constrain DNUTS placement while leaving
  planner/NUTS demand at the full-pattern width.  The Tier-1 view is
  therefore a PAIR: thinned grid + a derived `LayerStack` with
  `bit_pitch = unit_pitch / n_kept` on shared layers, handed together to
  the cell-local planner and NUTS constructors (`hier.py:1817,1993`);
* DNUTS can only land bits on kept slots;
* the dead-span discriminator and `check_template_tracks` pools see the
  thinned supply with no code change beyond using the view.

### Slot allocation policy

Deterministic and pattern-periodic: within each repeating unit, keep the
**first contiguous run** of `floor(s × n_signal)` SIGNAL slots (per power
rail group; floor per the budget-rounding rule above — this paragraph
predated that resolution), re-type the rest.  Contiguous-per-period rather
than an interleaved comb: the parent's leftover then also stays contiguous
per period — an interleaved comb would fragment the parent's supply and
break `timing_critical`'s contiguous-window requirement for wide parent
buses.  Periodicity preserves `unit_pitch`, so **instance phase congruence
and `align_bottom_up` are untouched**.  RESOLVED (plan owner, 2026-07-31,
Phase 3 Q1): ship contiguous-first-slots for every cell type; the
per-sibling OFFSET (disjoint runs so a track index is leased to at most
one child type) stays unexposed until a measured design shows the
contention — same-index leases maximize the parent's uniformly-clean
corridor, so the default is not just simpler but plausibly better.

### Two enforcement tiers, by where the bundle is planned

* **Tier 1 — cell-local solves** (the main consumer).  The bottom-up
  template solve already constructs its own planner and NUTS engine
  (`hier.py:1817,1993`); it additionally receives a **derived
  `RoutingGridStack` view** with the cell's thinned patterns substituted on
  shared layers.  Class-move re-plans re-run the cell-local solve and
  inherit the view.  Exact, DNUTS-real, cheap — and safe *here* because the
  cell-local solve is a closed context (the objection that killed
  view-based enforcement for the mask does not apply).
* **Tier 2 — the global solve** (top-down-planned bundles of a shared-layer
  cell, cross-level bundles, a RELEASE-pass instance re-solved
  individually).  No per-solve grid view exists, so the share is enforced
  as a **capacity scale in the planner**: for a wrapper with
  `share(L) = s`, the slide-clamped band capacity on layer L inside the
  instance bbox is `s × capacity` (one multiply in
  `CongestionPlanner::capacity()`'s track-mode branch, keyed by the wrapper
  under evaluation).  A *per-bundle* approximation of the collective budget
  — several bundles of one cell could together exceed `s` — closed by the
  **scalar collective budget** (§13 Phase 3 Q3, RESOLVED): one running
  counter per (cell-instance, shared layer) refuses any candidate whose
  commit would push the cell's total past `s × supply(bbox)`, with the
  §9.7 audit kept as defense-in-depth.

### The share is a budget, not a reservation

The parent is **not** restricted to the complement: it sees the full
pattern minus the child's *actual placed routing* (the existing
copied-routing keepouts).  A child using 12% of its 30% M4 slice leaves 88%
of M4 to the parent.  The guarantee the parent needs is an upper bound on
child consumption, and the thinned pattern enforces that physically.  (A
hard parent-side reservation would be the same mechanism pointed the other
way — a parent-view thinning — and becomes a floor knob if ever needed;
§13, Phase 5 Q2.)

## 7. Persistence

* Schema (v20): `cell.layer_cap INTEGER DEFAULT -1` for the common ceiling
  case (precedent: `cell.bottom_up` v17 at `src/bdb.cpp:390,702`) plus a
  `cell_layer_share (cell_id, layer_id, share REAL)` table for the
  fractional exceptions.  `-1` = uncapped.  The `*` default cap persists in
  BDB meta (`layer_cap_default`), like other flow knob memos.
* `load_pipeline [expanded]` re-resolves masks/views from the persisted
  policy before re-validating bundles — a resumed session must plan under
  the same policy.
* Fixtures: the column/table appear in the diffable `.bdb.sql`;
  `build_fixtures.py` needs only the version bump.  Converters: `bdb2buda`
  (flat export, no cells) unchanged; `buda2bdb` carries the columns through.

## 8. Interaction with the bottom-up machinery

* **Copies and keepouts.**  Copied instance routing already becomes
  layer-tagged keepout zones for higher levels.  Under caps the zones never
  mention layers above the ceiling, and the parent plans over the instances
  on its own layers with **no detour at all** where leaf metal lies below
  its floor.  The separation should be *visible*: `report_wirelength`'s
  per-layer breakdown is the evidence (leaf WL confined to ≤ cap + slices).
* **Congruence / rotation classes.**  A policy is a cell attribute ⇒
  identical across instances by construction; `<cell>90` clones inherit it.
  Layer directions are global — rotation transforms geometry, not the stack
  — so the same mask applies verbatim to the clone; a test pins it.
* **Healer class moves / release pass.**  Both re-plan through the planner
  core with the same wrappers ⇒ mask enforced; the released instance keeps
  its mask (release breaks uniformity, never the cap) and its share via
  Tier 2.
* **`check_template_tracks` `independent` mode** — masks/views ride along
  unchanged.

## 9. Failure modes (all LOUD, per house style)

1. **Policy without both directions** — hard error at declaration; likewise
   a floor above the cap (`-min M5` with cap M3).
2. **Capped bundle infeasible under STRICT** — the existing ladder already
   reports ALLOW_OVERFLOW/BEST_EFFORT commits with WARNINGs; the message
   gains the cap so the user sees *why* the layer set was small.
3. **Dead span at the cap ceiling** — reported, never silently escalated
   past the cap; names the bundle, segment, cap, and the layer escalation
   wanted.
4. **Policy for an unknown cell** — hard error (matches `set_bottom_up`).
5. **Cap tighter than already-persisted routing** (cap added, then
   `load_pipeline` of a checkpoint routed above it) — validation reports
   every violating persisted segment and refuses to continue without an
   explicit re-plan; never silently keeps illegal metal.
6. **Share that rounds to zero kept tracks per period** (e.g. 5% of a
   4-signal-slot pattern) — declaration-time hard error naming the layer,
   the period's slot count, and the minimum meaningful share.
7. **Tier-2 over-consumption** — an advisory audit after `run_planner hier`
   sums each policied cell's committed usage per shared layer against
   `share × supply` in its bbox and WARNs on excess, so the per-bundle
   approximation's slack is visible, never silent.

## 10. What this deliberately does NOT do (v1 scope cuts)

* **No per-cell track patterns.**  Policies restrict *which* layers and
  *how many tracks* a cell uses, not the patterns themselves; per-region
  patterns already exist via `add_grid_override` and compose.
* **No via/pin-access modeling.**  The busterm-face model is unchanged.
* **No automatic cap inference.**  `set_layer_caps_by_depth` is a thin
  declaration-time convenience; nothing infers caps dynamically.
* **No partial-span caps** (a cell using M4 only in a region) — region
  scoping stays the keepout/override system's job.

## 11. Tests

1. **Unit — mask enforcement**: bundle A capped at M3, B uncapped; every A
   segment ≤ M3 across STRICT and the ladder, B unchanged.
2. **Unit — effective-TOP promotion**: cap M3 over an all-LOW {M2,M3};
   trunk lands on the promoted pair; `base_cost_non_top` does not tax it.
3. **Unit — declaration validation**: single-direction policy hard-errors;
   unknown cell hard-errors; `* off` restores byte-identity.
4. **Unit — escalation compliance**: a dead LOW segment in a capped bundle
   escalates only within the mask; at the ceiling it reports instead.
5. **Unit — thinned pattern**: `share 0.3` on an 8-signal-slot period keeps
   `floor(2.4)=2` contiguous SIGNAL slots (granted 25% ≤ declared 30%),
   same `unit_pitch`/origin; a 5%-of-4-slots share floors to zero and
   hard-errors at declaration;
   `count_signal_tracks_in` and `eff_bus_width` reflect it; `share 1.0` is
   the identity view.
6. **Bottom-up integration (mid)** — caps: `mix2_fast_bottomup` variant
   with leaf cells capped at M3: per-layer `report_wirelength` shows zero
   leaf-template WL above M3; endpoint delta recorded; rotation clone
   inherits the cap.
7. **Bottom-up integration (mid)** — shares: leaf capped at M3 with
   `share M4 30`: (a) every leaf M4 bit on a kept slot, (b) leaf M4 usage ≤
   30% of supply in the bbox, (c) the parent uses M4 tracks the child left
   free (budget-not-reservation), (d) `check_template_tracks` clean on the
   thinned view.
8. **Tier-2 audit fires** on a constructed over-consumption.
9. **Persistence round-trip**: policy → `save_bdb` → `load_pipeline
   expanded` → identical masks/views; v19→v20 migration keeps old fixtures.
10. **Byte-identity corpus guard**: full `qor_corpus.py --compare` with no
    policy declared — **0 better / 0 worse / all unchanged, WL +0.00%**.

## 12. Measurement plan

* New QoR vehicles: `flow/rnr/mix2_fast_bottomup_caps.buda` (leaf M3 / mid
  M5 / top M7), a **deliberately wiring-limited** shared variant (leaf
  M3 + 30% M4) so the spill mechanism is actually exercised, and a capped
  `flow/chip/chip_bottomup_caps.buda` (the 432-leaf corpus vehicle is where
  level separation should pay most).
* Metrics: endpoints (overlaps/unplaced/viol_bundles), per-layer WL
  breakdown (the separation evidence), healer iteration counts (expected
  DOWN at upper levels), runtime.
* Honest expectations, stated up front: caps *remove freedom*, so leaf
  endpoints may degrade where low-layer supply is genuinely tight — that is
  the BKM's trade (leaf slack for top-level routability and
  predictability), and shares exist precisely to price that trade instead
  of hitting a wall.  The study reports both directions; the feature stays
  **opt-in** regardless (the byte-identity guarantee makes that free).

## 13. Phasing, with open questions per phase

Each phase lands with its tests green and the no-policy corpus
byte-identical.  Phases 1–2 are the minimum usable capped flow; Phase 3
adds the shared form.

### Phase 1 — binary core (C++ + the one healer that fires without healers)

Mask field + effective-TOP scoring context; enforcement in
`optimize_topologies` / ladder / reservations / `post_nuts`; **dead-span
escalation mask compliance** (the `run_nuts healersAhead` auto path invokes
it outside the planner core — without this, a plain capped flow violates
its cap on the first `run_nuts`; Codex P1 on #542);
`set_cell_layer_cap` + validation; byte-identity corpus run.
*Deliverable: capped flat-hier flow routes under caps — including through
`run_nuts` — and the corpus is unchanged without caps.*

Open questions — **both RESOLVED** (plan owner, 2026-07-31):
* **Q1 — RESOLVED: the band form.**  `set_cell_layer_cap <cell> <cap>
  [-min <floor>]` defines the default-full band; explicit shares override
  any layer in either direction (thin inside the band, grant above it) —
  the cap is deliberately NOT a hard bound on shares, which would outlaw
  the escape-valve case.  See the §4 resolution rule.  This subsumes both
  the allow-list question (the band + share overrides express every
  practical set) and the former Phase-5 floors question.
* **Q2 — RESOLVED: ancestor policy.**  Cross-level bundles take the
  common-ancestor cell's policy, per the compositional reading.

### Phase 2 — bottom-up wiring (Python) — LANDED 2026-07-31

Mask resolution at template / expansion / clone / `load_pipeline`;
cell-local solves; `align_bottom_up` LCM; `check_template_tracks` scoping;
persistence v20.  *Deliverable: capped `mix2_fast_bottomup` end-to-end with
per-layer WL evidence.*

Open questions: none beyond Phase 1's — this phase is wiring.

As built:

* **Schema v20** — `cell.layer_cap`/`cell.layer_floor` (upsert-preserved,
  the `bottom_up` v17 pattern) + the `cell_layer_share` table (created
  now, consumed by Phase 3); `set_cell_layer_band` / `cell_layer_band` /
  `layer_capped_cells` accessors + `CellRow` fields; idempotent
  migration; fixtures regenerated.
* **Write-through + restore** — `set_cell_layer_cap` persists to the open
  BDB (`*` default in `meta.layer_cap_default`; `* off` clears both);
  `open_bdb` restores persisted policies with session-typed entries
  winning the merge; `load_pipeline` re-resolves masks onto the restored
  wrappers.
* **§9.5 audit** — a cap tighter than already-persisted routing voids the
  violating bundles' restored plan at `load_pipeline` (LOUD per segment,
  their bus segments excluded from the NUTS rehydration, explicit
  re-plan required).
* **Band-scoped `align_bottom_up`** — per rotation-class group, the
  alignment period is the LCM of the BAND's pitches only, and the mirror
  constant CRT-combines only the band's congruences: fewer pitches →
  finer equivalence → smaller nudges.  The placement-stage
  `check_template_tracks` compares only the band's layers (one criterion
  with the aligner); the routed check was already scoped by construction
  — it walks the copied segments' own layers.
* **Clone ordering** — `_apply_layer_policies` runs AFTER the
  rotation-class split (clone wrappers are fresh `BundleInput`s; the
  clone context resolves to the base cell via `_bu_cell_of`), BEFORE the
  cell-local solves plan under the masks.
* **Vehicle + evidence** — `flow/rnr/mix2_fast_bottomup_caps.buda`
  (dnuts2 at M3; dnuts1/dogleg1/dogleg2 at M5) ends `Success: no
  violations found` / 0 unplaced.  Per-cell per-layer detailed WL:

  | context | M2 | M3 | M4 | M5 | M6 | M7 |
  |---|---|---|---|---|---|---|
  | capped: TOP-LEVEL | 10516 | 5437 | 63466 | 111150 | 167098 | 105531 |
  | capped: dnuts1 (≤M5) | 10728 | 0 | 114228 | 174424 | **0** | **0** |
  | capped: dnuts2 (≤M3) | 3795 | 3786 | **0** | **0** | **0** | **0** |
  | capped: dogleg1 (≤M5) | 0 | 0 | 8890 | 25512 | **0** | **0** |
  | capped: dogleg2 (≤M5) | 0 | 0 | 11790 | 24844 | **0** | **0** |
  | uncapped: dnuts1 | 0 | 0 | 49608 | 130344 | 75924 | 47488 |
  | uncapped: dnuts2 | 0 | 0 | 2595 | 2825 | 1200 | 1200 |

  Every leaf template's WL is confined to its band; M6/M7 become
  exclusively top-level (the uncapped leaves had spread 123k+ units onto
  them); total detailed WL 841195 capped vs ~885k uncapped (−5%).  The
  tighter bands measured while choosing the vehicle are the §12
  honest-trade evidence: all four cells at M3 → dnuts1's 32-bit bus
  commits with planner overflow 13–35.5 and strands all 32 bits at
  DNUTS; the child-dense dogleg cells at M3 (leaf footprints are LOW
  keepouts; TOP flies over) strand 32 dogleg2 bits.  Those are the
  wiring-limited shapes Phase 3's shares price.
* **Byte-identity corpus** (no policy declared): 0 better / 0 worse /
  34 unchanged; abstract + detailed WL both +0.00%.

### Phase 3 — fractional shares

Thinned-pattern derivation + `set_cell_layer_share`; Tier-1 grid view for
cell-local solves; Tier-2 capacity scale + over-consumption audit;
share-aware reservations.  *Deliverable: the wiring-limited leaf (M3 cap +
30% M4 + 10% M5) routes end to end; budget-not-reservation semantics
tested.*

Open questions — **all three RESOLVED** (plan owner, 2026-07-31):
* **Q1 — RESOLVED: contiguous-first, no offset knob yet.**
  Contiguous-per-period with every cell type keeping the FIRST slots of
  each period (parent contiguity, `timing_critical` safety).  The
  per-sibling offset (disjoint runs so a track index is leased to at most
  one child type) stays unexposed until a measured design shows the
  contention: same-index leases concentrate all child consumption on the
  same track indices, which maximizes the parent's uniformly-clean
  corridor over a mixed row of instances — the default is not just
  simpler but plausibly better for long parent trunks.  The derivation
  supports an offset trivially if that call is ever revisited.
* **Q2 — RESOLVED: proportional, never over-reserve.**  An unplanned
  policied cell's demand reservation parks `s × eff_width` on a shared
  layer's bands and full width on fully-owned effective-TOP layers — the
  reservation is a forecast of eventual consumption, and the share is the
  legal upper bound on it.  Implementation nuance pinned with the
  decision: `eff_width` here is the GLOBAL-pattern effective width — the
  thinned-view width is already `~1/s` inflated per bit
  (`unit_pitch / n_kept`), so `s × thinned_width ≈ full width`, silently
  reconstructing the over-reservation this resolution rejects.
* **Q3 — RESOLVED: (b), the scalar collective budget.**  Per-bundle
  capacity scaling alone does NOT uphold the user-visible budget: two 30%
  bundles of one cell can collectively take 60% (Codex P1 on #542).  The
  decided form: one running counter per (cell-instance, shared layer);
  a candidate whose commit would push the cell's total past
  `s × supply(bbox)` is refused and the ladder moves on.  Coarser than
  per-band accounting but it enforces the promise, costs one counter, and
  sequential-commit order makes it exact for the budget scalar; the §9.7
  audit stays as defense-in-depth.  Escalate to (c) exact per-band group
  accounting inside `GlobalCut` only if a measured flow shows per-band
  violations the scalar bound misses (quantity legal, placement crowded
  into one band).  Option (a) audit-only stays rejected.

### Phase 4 — healer compliance

Width-gate pitch, release/class-move verification (dead-span escalation
already landed in Phase 1),
`LAYER_CAP` advisory check, policy-aware reporting (`dump_hbundles`, ladder
warnings) — over the full policy vector, not just the binary mask.
*Deliverable: healers never violate a cap or exceed a share; violations
impossible by audit.*

Open questions: none — this phase closes the inventory of §5/§6.

### Phase 5 — flows, study, docs

Capped and shared QoR vehicles, the measurement table, CLAUDE.md command
rows, BDB_REFERENCE schema, HIER_* doc updates, `set_layer_caps_by_depth`.
*Deliverable: the §12 study, published; docs current.*

Open questions to settle **before** Phase 5:
* **Q1 — depth convenience.**  `set_layer_caps_by_depth` maps BDB depth to
  caps; the counting direction (deepest-first as written, or top-first?)
  needs one decision.
* **Q2 — floors: RESOLVED early**, absorbed into Phase 1 by the `-min`
  band syntax (§4).  The stub-economics caveat (short stubs love cheap LOW
  layers) stays: the §12 study reports per-layer WL with and without
  floors so the cost is measured, not assumed.
