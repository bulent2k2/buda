# Healers × bottom-up templates: class-level moves (investigation)

**Status: DESIGN — investigation of what it takes; nothing implemented.**

The healers (`negotiate_congestion`, `ripup_reroute`) currently refuse
bottom-up (`hier.locked`) wrappers as movers and victims, so on a bottom-up
flow the residual they cannot clear sits exactly on the frozen template
copies.  This note measures that gap on
`flow/rnr/mix2_fast_bottomup.buda`, maps the machinery a fix must touch,
and specifies the missing move type: the **template-class move** — re-pin a
bottom-up template to an alternate candidate and propagate it to every
instance of its rotation class as ONE measured, accept-on-strict-improvement
healing move.

## 1. The gap, measured (mix2_fast_bottomup vs its top-down twin)

Same design, same knobs (`run_planner hier 5 signal_tracks`), healers
enabled after `run_nuts` and after `run_detailed_nuts`:

| | bottom-up (4 cells marked) | top-down twin |
|---|---|---|
| plan-time | 37 locked wrappers committed **with overflow** ("pinned topology overflows and cannot be rerouted") | no such warnings |
| abstract overlaps | 17 → negotiate 15 → ripup **2 (stuck)** + 1 interval violation | 16 → 8 → **0** |
| DNUTS opens | 88 → ripup **16 (stuck)** | 137 → 73 → **0** |
| final `check_design` | 16 violations in 2 groups | **Success: no violations found** |

The two stuck groups are bundle **166** (locked instance, pinned candidate
1 of **8**) and bundle **177** (locked instance, pinned candidate 1 of
**28**) — templates with plenty of alternates the healers can see but are
forbidden to try.  The frozen-template cost on this design is therefore the
whole residual: 2 overlaps + 16 open bits that full freedom heals to zero.

Without healers the bottom-up flow ends at 17 overlaps / 196 unplaced bits
(the flow ships with both healers commented out).

## 2. Where the healers refuse locked wrappers today

All refusals date from stage (a) of the bottom-up plan
(`hier_bottom_up_planning.md` §3.3, "skip `locked` wrappers as victims
*and* as movers") and are deliberate: a locked wrapper's assignment is a
uniform copy shared by every sibling instance, so moving ONE instance would
break the solve-once-copy invariant.  Sites:

- `ripup.py::_rr_contenders` (~:303): a locked wrapper is never a
  contender, so ripup enumerates no moves for it.
- `ripup.py::_rr_global_pass` (~:881): locked occupants are skipped in the
  global-occupant stage.
- `ripup.py::_negotiate_iteration` (~:1354): negotiation never unpins a
  locked wrapper; its overlap partner (if unlocked) replans around it.
- C++ `replan_bundle_ripup`: locked blockers are skipped as victims
  (planner-side mirror of the same rule).
- Planner ladder: a locked wrapper that overflows is committed with a
  WARNING ("cannot be rerouted") — 37 of them on this flow.

The refusals are correct *per instance*.  What's missing is the legal move
at the granularity the invariant allows: **the whole class at once**.

## 3. How template state flows (what a class move must touch)

The machinery is already centralized; the crucial facts:

1. **Template wrappers** (`session._hier_bundles_orig`, cell-local coords)
   carry the authoritative pin: `_plan_bottom_up_templates` runs a
   dedicated cell-local `CongestionPlanner` per marked cell (WIDTH model,
   deepest first, per rotation class — 90° families split into clone
   templates via `_split_bottom_up_rotation_classes`), then sets
   `selected_topology_index`, `seg_layers`, `seg_perp`,
   `topology_pinned=True`, `pinned_seg_layers` on the template.
2. **Expansion** (`_expand_hier_bundles`) translates candidates per
   instance via `offset_topology` (candidate ORDER preserved — index k on
   an instance is the translate of template index k), copies the pin, and
   marks instances `hier.locked`.  `_hier_expansion_map`: template id →
   [instance wrappers].
3. **NUTS fixed copies** (`_bottom_up_fixed_segments_compute`) re-derive
   ON DEMAND from the TEMPLATE wrappers' pins: cell-local `NUTSEngine`
   solve (with the local planner's candidate-extended grid,
   `_bu_planner_grids[cell]`), translated per instance, cached in
   `_bu_fixed_cache`.  The cache is injected into **every** engine site —
   `run_nuts`, `post_nuts`, `run_nuts_on_layer`, and the healers' internal
   re-runs — plus the DNUTS copy path (`nutsflow.py` `add_fixed_bits`) and
   `check_template_tracks` (`_template_track_verdict`,
   `_bu_dnuts_plan_cache`).
4. **Invalidation already exists**: `_plan_bottom_up_templates` starts by
   clearing `_bu_fixed_cache`, `_template_track_verdict`,
   `_bu_dnuts_plan_cache`, and the adopted template doglegs.  A class move
   needs exactly this invalidation, scoped or global.

So a class move is, mechanically:

    re-pin template wrapper W_t to k' (+ fresh seg_layers/seg_perp for k')
    re-pin every instance wrapper in _hier_expansion_map[W_t.id] to k'
    invalidate _bu_fixed_cache (+ verdict + dnuts plan cache + template doglegs)
    re-run the pipeline stage (_rr_rerun full) → measure → accept or restore

Everything downstream (fixed copies, blockage, DNUTS copy, verdict)
re-derives automatically from the template pin — that is the payoff of the
existing template-authoritative design.

## 4. What it takes — itemized

### A. Ripup class moves (the core; v1)

New move kind in `ripup_reroute`, enumerated when a contender is locked
(today: silently dropped in `_rr_contenders`):

- **Contender mapping.** Keep `_rr_contenders` as is for free bundles; add
  a parallel list of *locked* contenders mapped to their template id
  (instance wrapper → `_hier_expansion_map` reverse index; build once).
  De-dup by template: one class entry regardless of how many of its
  instances are contended.
- **Move enumeration.** Alternates = template candidate indices ≠ current.
  The existing farness ranking (`_rr_candidate_order`) works unchanged on
  the *contended instance's* wrapper (its candidates are translates in
  instance coords, and the contention sites are global) — rank there, use
  the index against the class.  Cap like index moves (top-8).
- **Trial body.** For candidate k':
  1. Run the cell-local planner for that cell with the template pinned to
     k' (reuse `_plan_bottom_up_templates` factored to a per-cell helper
     with a forced pin) — this yields correct `seg_layers`/`seg_perp` for
     k' instead of guessing, refreshes `_bu_planner_grids[cell]`, and
     costs ~10 ms (measured: the 4 local solves on mix2 are 6–11 segments
     each).
  2. Propagate pin + layers to all class instances.
  3. Invalidate the bottom-up caches (v1: global invalidation — the full
     recompute measured ~50 ms for all 4 cells; scoped invalidation is a
     later optimization).
  4. `_rr_rerun(stage, full=True)` — class trials must be FULL trials:
     they change many instances at once, so the incremental
     `replan_bundle` path and the fixed-context screen don't apply (v1;
     see Costs).
  5. Metric strictly better → commit (re-run full pipeline as commits
     already do, persist below); else restore.
- **Ordering.** Try class moves AFTER per-bundle index moves stall (same
  slot as the global-occupant pass, before or after it — measure; class
  moves are strictly more expensive, and a free-bundle fix should win when
  one exists).

### B. Snapshot/restore extension (correctness-critical)

`_rr_snapshot` captures ONLY `self.bundles` (the expanded list).  A class
trial additionally mutates state that a rejected trial must restore:

- template wrapper pin state (`_hier_bundles_orig`: selection, pin,
  seg_layers/seg_perp/pinned_seg_layers, assigned_*),
- `_bu_fixed_cache`, `_bu_planner_grids`, `_template_track_verdict`,
  `_bu_dnuts_plan_cache`, `_bu_fixed_from_resume` (a class move ends the
  resumed-routing preference — deliberate, but must restore on reject),
- adopted template doglegs (`_reset_bottom_up_doglegs` state — template
  dogleg slots and their instance copies).

Cheapest correct v1: snapshot these as opaque blobs (the cache is a list
of TrackSegments; copy it), restore wholesale on reject.  This extension
is only taken when a class trial actually runs, so free-bundle healing
keeps today's snapshot cost.

### C. Invariants the move must keep (and gets for free)

- **Uniformity**: all instances re-pin to the same k' — the move is
  class-atomic by construction; congruence untouched (geometry is
  translated per instance exactly as expansion did).
- **`check_template_tracks`**: verdict invalidated with the caches; the
  post-heal `check_template_tracks` / DNUTS-copy path recomputes.  A class
  move can change the verdict (ALIGNED ↔ mismatch) — that is a real
  outcome, reported by the existing machinery; the `on_mismatch` policy
  applies unchanged.
- **User pins**: a template the USER pinned pre-plan keeps its pin in
  `_plan_bottom_up_templates`; class moves must skip user-pinned templates
  (same respect the local solve shows).  Distinguish via the existing
  sidecar/user-pin provenance rather than `topology_pinned` (which the
  local solve sets on every template).
- **Rotation classes**: a 90° clone template is its own class with its own
  candidates — classes move independently (nothing new needed; the clone
  IS a separate template wrapper).
- **Persistence on commit**: re-persist template `is_selected` + assigned
  layers + expanded instance rows — the persist paths already run from the
  commit's full pipeline re-run.

### D. Does this apply to `negotiate_congestion`?  v1: NO — v2: yes, with a price-translation layer

**The class move above is a `ripup_reroute` mechanism only.  In v1,
`negotiate_congestion` keeps refusing locked wrappers exactly as today.**
The reason is structural, not a scoping shortcut — the two healers move
bundles through different mechanisms, and only ripup's fits a class
natively:

- **Ripup is trial-based**: "mutate → re-run → measure the design metric →
  keep iff strictly better, else restore."  That loop is agnostic to WHAT
  was mutated, so a class re-pin (template + all instances + cache
  invalidation) slots straight into the existing trial/snapshot machinery.
- **Negotiate is price-based**: one `replan_bundle` pass per affected
  bundle, UNPINNED, where the corrected (injected + history) band prices —
  not a per-candidate trial — choose the topology.  There is no "try k'"
  loop to reuse.  For a template, the injected prices live on GLOBAL
  Hanan bands, but the template plans in the CELL-LOCAL frame; letting the
  local planner feel measured congestion means translating each
  instance's injected band demand into cell coordinates and SUMMING
  across instances, then replanning the template under that aggregated
  field and propagating.  That price-translation layer into
  `_plan_bottom_up_templates` is the v2 work — the principled
  multi-context version of the local solve (which today prices intra-cell
  congestion only, blind to every instance's surroundings).

Negotiate still participates in v1, in three unchanged-but-real ways:
its replans of FREE bundles around locked blockers keep working; its
stage-b dead-span preconditioning and injections keep feeding the loop;
and once a ripup class move COMMITS, subsequent negotiate iterations run
against the new template placement.  Recommend deferring v2 until ripup
class moves are measured — they already cover the endpoint (the stuck
residual heals or it doesn't), and the measurement will show whether
price-guided class selection adds anything over farness-ranked trials.

### E. Adjacent, out of scope here

The 37 plan-time "pinned topology overflows and cannot be rerouted"
warnings are the same rigidity one stage earlier: the planner ladder could
consider a class re-pin before ALLOW_OVERFLOW.  Same primitives (per-cell
local replan + uniform propagate), different driver; worth a follow-up
after the healer version proves the trial machinery.

## 5. Costs

- Class trial ≈ local planner (~10 ms) + fixed-cache recompute (~50 ms)
  + full `_rr_rerun` (NUTS ~50 ms + DNUTS ~40 ms on mix2) ≈ **one full
  trial** — the cost ripup already pays in its stall sweep.
- Move count is small: (#classes with a contended instance) × (top-K
  alternates), e.g. ≤ 4 × 8 on mix2 — dozens, not the 250 screened index
  trials.  No screen needed in v1 (screen assumes single-bundle placement
  against frozen context; a class changes the context itself).
- Non-bottom-up flows: zero cost, zero behavior change (no locked
  wrappers → no class contenders → new code never runs).  This is the
  byte-identity gate for the whole corpus minus bottom-up flows.

## 6. Validation plan

1. **Endpoint**: mix2_fast_bottomup + healers with class moves should
   close toward the top-down bound (0 overlaps / 0 opens; accept a small
   residual if uniformity genuinely cannot fit — but bundle 177 alone has
   27 untried alternates, so expect 0/0).
2. **Byte-identity**: wl_corpus + flow goldens identical on every
   non-bottom-up flow (structural: the move kind only fires on locked
   contenders).  `mix2_fast_topdown` identical.
3. **Uniformity audit**: after healing, every class still uniform (all
   instances same index) — assert in a regression; `check_template_tracks`
   re-verdict exercised both ways (stays ALIGNED on one fixture, flips on
   a crafted one under `independent`).
4. **Reject path**: a forced-reject class trial restores template pins,
   caches, verdict, doglegs — snapshot regression test.
5. **Resume**: class-move commit then checkpoint/resume round-trips (the
   template pin is persisted; `_bu_fixed_from_resume` interaction).
6. Tiers: fast/mid green; `test_hier_bottom_up.py` suite extended.

## 7. Files touched (estimate)

- `src/buda_session/ripup.py` — class-contender mapping, move kind, trial
  body, snapshot extension (~the bulk).
- `src/buda_session/hier.py` — factor `_plan_bottom_up_templates` into a
  per-cell helper callable with a forced pin; scoped invalidation hook.
- `src/buda_session/reports.py` / none in C++ for v1 (the C++ locked
  skips stay — they guard per-instance moves, which remain forbidden).
- `test/tests/test_hier_bottom_up.py` + a new healer-class-move test file;
  `flow/rnr/mix2_fast_bottomup.buda` un-comment the healers once green.
- Docs: this note → as-built update; `hier_bottom_up_planning.md` §3.3
  cross-reference; script reference note under `ripup_reroute`.
