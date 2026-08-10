# NDR Support — Internal Architecture

**Audience: implementers.**  The as-built baseline NDR must generalize, the
candidate solution paths with their assessment, the shield-materialization
and persistence decisions, and the per-requirement implementation mapping.
Requirements (R1–R13) live in [../NDR_REQUIREMENTS.md](../NDR_REQUIREMENTS.md);
the user-surface options in [../NDR_UI.md](../NDR_UI.md).  Tracked as
[opens.md](opens.md) "Substantial features" item 15 and on the
[work menu](work_menu_2026-08-06.md).  Status: **phase-1 prototype BUILT on
path A** (2026-08-06) — see §7 for the as-built record and its documented
prototype limitations.

## 1. As-built baseline — the assumptions NDR breaks

The whole pipeline currently assumes **one bit = one default SIGNAL slot**:

- **Stage 8** (`routing_grid.h`): a layer is a repeating `TrackPattern` of
  `TrackSlot`s (`type`, `label`, `width`, `space_after`).  Only SIGNAL slots
  are routable; `SHIELD`/`POWER`/… slots are *static pattern rails*
  (materialized as `PreRoutedSegment`s, never owned by a net).  Spacing
  exists only implicitly as `space_after` in the pattern.  All supply
  queries — `signal_tracks_in`, `signal_tracks_in_span`, and their `count_`
  hot-path siblings — enumerate SIGNAL slot centres with no notion of a
  consumer needing more than one slot or more than the slot's width.
- **Stage 9** (`detailed_nuts.h/cpp`): `place_by_layer` claims one signal
  track per member bit (`bus_seg_nbits`), emits `NetSegment.width` **from
  the slot** (not from the net), and the admission arithmetic is
  all-or-nothing per segment (`n_sig < bus_seg_nbits` strands every bit).
  The only per-bus constraint precedent is `BusSegment::timing_critical`
  (contiguous-window search) — note it has no `.buda` declaration surface
  today (API-only), so NDR's CLI surface is genuinely new ground.
  Same-bundle bits may share tracks (the sharing exemption); the
  post-placement keepout cull re-checks final spans; vias fan out per bit.
- **Stage 4 / stage 3**: abstract width is `LayerStack::eff_bus_width`
  (`bits × unit_pitch / n_signal_slots` when a pattern exists — the measured
  per-bit channel cost — else `width × dilution_factor`), and the planner
  charges that width (or, in `signal_tracks` mode, the discrete track count
  with `bit_pitch`) against band capacity.  The kPeak supply floor, the
  dead-span escalation, the doomed-seat census, and `_seg_admission_pool`
  all reuse the same count functions — the #536 arc's hard-won invariant is
  that **admission arithmetic is single-sourced** (the R4 requirement is
  this invariant applied prospectively).
- **Bundling / hier**: bundler signatures ignore physical constraints;
  `align_bottom_up`'s phase criterion is over *pattern pitches* only (NDR
  changes per-net demand, not the pattern, so phase math is untouched —
  R13 pins this).

## 2. Solution paths

Four candidate realizations of R4/R5/R6:

- **(A) Slot-quantized consumption.**  An NDR wire consumes k adjacent
  SIGNAL slots (width), plus guard slots left empty (spacing), plus
  flanking slots converted to emitted shield wires.  Pros: every supply
  query stays slot-counting; demand units are integers; admission
  arithmetic generalizes mechanically.  Cons: width realization is
  quantized to slot pitch (a 1.5× wire costs 2 slots); the placed wire's
  centre sits between slot centres (via crossings, pair-align, and the
  abstract_pos anchor all assume slot centres).
- **(B) Pattern-declared NDR classes.**  Patterns declare wide/special
  slots (new slot types or labeled SIGNAL variants); an NDR net may only
  land on slots matching its rule.  Pros: supply is static and visible
  (the pattern IS the contract); placement stays one-bit-one-slot; R3
  validation is trivial.  Cons: burden shifts to the pattern author;
  supply is fixed at pattern-design time (no demand-driven conversion);
  every layer needing NDR needs a pattern edit — and mixed default/NDR
  demand per band becomes two parallel supplies the planner must track.
- **(C) Continuous packing for NDR nets.**  NDR wires bypass slot
  quantization: placed by real geometry (width + clearance) in
  keepout-clear intervals, coexisting with slot-placed default bits.
  Pros: exact realization, no quantization loss.  Cons: two placement
  geometries in one solver; every supply/count query needs a continuous
  twin; phase congruence (bottom-up copies) and the sharing exemption get
  materially harder — the biggest departure from the as-built model.
- **(D) Hybrid A+B.**  Quantized consumption (A) as the general mechanism,
  with pattern-declared classes (B) *credited* where the pattern already
  provides matching resources (incl. R5a shield crediting).  Pros: works
  on unmodified patterns, exploits tuned ones.  Cons: two code paths to
  keep lockstep.

The choice should weigh: which path keeps the R4 single-sourced arithmetic
simplest; which keeps R12 byte-identity trivially true; and which degrades
most gracefully when a rule is only *partially* realizable on a layer.

### 2.1 Path comparison (2026-08-06, pre-brainstorm assessment)

Cells graded ●●● (best) to ● (worst) against the as-built constraints;
reasoning below the table.

| | (A) Slot-quantized consumption | (B) Pattern-declared classes | (C) Continuous packing | (D) Hybrid A+B |
|---|---|---|---|---|
| **Ease of use** | ●●● declare + attach; works on any existing pattern | ● every NDR layer needs a pattern redesign; supply fixed at pattern-design time | ●●● declare + attach; exact realization | ●●● works everywhere, exploits tuned patterns where present |
| **Runtime impact** | ●● supply queries become run-of-k counting (slightly heavier walker); demand still integer | ●●● essentially today's cost — one bit one slot, filter by class; but planner tracks parallel supplies per band | ● continuous twin needed for every count/supply query; planner capacity goes real-valued | ●● A's cost + a credit check per shield requirement |
| **Code complexity** | ●● every supply query + admission generalizes, but stays in the slot paradigm | ●● solver nearly untouched; complexity moves to planner bookkeeping (two supplies per band) and pattern validation | ● two placement geometries in one solver; sharing exemption, bottom-up copies, congruence all reworked | ● A's changes PLUS the credit path kept lockstep with it — two code paths, one arithmetic |
| **R4 group-demand fit** | ●●● group = a window of consumed slots; integer, naturally group-scoped | ●● trivial per class, but default + NDR classes = parallel supplies the charging must not mix | ● real-valued footprints; the single-sourced rounding discipline gets delicate | ●● A's, with credit entering the same group conversion everywhere (R5a already forces the shared predicate) |
| **Realization fidelity** | ●● quantized: 1.5× width pays 2 slots; spacing rounds up to slot pitch (conservative, never illegal) | ●●● exact — the pattern IS the contract | ●●● exact | ●● A's quantization, minus redundant shields where the pattern provides them |
| **Invariant risk (R12/R13)** | ●● no pattern change → phase math + byte-identity easy; wrinkle: a k-slot wire's centre sits between slot centres (abstract_pos anchor, pair-align, via crossings) | ●● byte-identity holds only if patterns are untouched — declaring NDR slots changes DEFAULT supply too, and unused NDR slots are stranded capacity | ● touches the most assumptions (anchors, congruence, sharing exemption) — hardest to prove unchanged | ●● A's risks + credit/audit consistency (mitigated by R5a's shared-predicate requirement) |
| **Partial-realizability degradation (R3)** | ●●● per-layer run availability is computable arithmetic — LOUD and precise | ●● trivially LOUD (no matching slots = cannot host) but static: no demand-driven flexibility | ●● flexible in principle, hard to state the failure arithmetic crisply | ●●● best: falls back to pure A wherever the pattern offers nothing |

Reading the table:

- **The real contest is A vs D.**  C buys exact fidelity at the cost of the
  most invariants — it is the only path that breaks the slot paradigm the
  entire supply/admission arithmetic is built on, exactly where R4 says not
  to take risk.  B alone fails the ease-of-use bar (every layer needs
  pattern surgery) and quietly taxes default nets: an NDR slot reserved in
  the pattern is stranded supply whenever no NDR net uses that band.
- **D is A plus an optimization, not a different architecture.**  Its extra
  cost is precisely the "two paths kept lockstep" burden — but R5a's
  net-identity predicate must exist for the audit anyway, so the credit
  path's hardest piece is already mandatory.  D's marginal complexity over
  A is smaller than the row suggests *if* credit is implemented as a term
  inside the one group-conversion function, never a second conversion.
- **A's sharpest hidden cost is the off-centre wire**: a 2-slot wire's
  centre sits between track centres, which brushes `abstract_pos`
  anchoring, pair-align partnering, and per-bit via crossings.  None are
  correctness walls, but each is a place where "one bit = one slot centre"
  is baked in and needs a footprint-aware generalization.
- **B survives as D's ingredient**: pattern-declared resources get
  *credited*, never *required*.

**Leaning (to be confirmed or overturned): start from A, and shape the
group-conversion API so D's crediting is a later additive phase** — phase 1
ships A pure (byte-identity trivial, shields always emitted), phase 2 adds
R5a crediting once the audit predicate exists.  This phasing **explicitly
defers R5a out of phase 1** (the deferral form R5a itself permits): a
phase-1 NDR near a matching GND rail emits a redundant shield and charges
its capacity — conservative and legal, never a silent violation — so R3
realizability and R7 planner pricing in phase 1 use the uncredited
worst-case demand, and a rule that is only realizable WITH crediting simply
fails LOUD until phase 2 lands.

## 3. Shield materialization — the persistence-critical decision

Where shields become geometry determines the persistence blast radius:

| Option | Shields exist at | Consequence |
|---|---|---|
| **3a. DNUTS emission** (strong lean) | Stage 9 only — topology and planner see *demand units*; shields become placed wires at detailed NUTS | **`topo_uid` untouched.**  Candidate geometry never changes, so every sidecar selection, script `select_topology` pin, `user_ops` op-log, and prune/dedup identity survives rule attachment/edit/removal unchanged.  BDB: shield rows in `net_segment` (+ marker + shield-net identity).  Sidecar: zero impact. |
| 3b. Abstract-NUTS emission | Stage 4 — shields as `bus_segment`-level objects | Visible in abstract viz/WL, but `run_nuts` persistence and `load_pipeline` grow a shield concept, and the planner must carry shields through replans and ripup trials.  Middle cost, middling benefit. |
| 3c. Topology segments | Stage 2 — shields as candidate segments | **Breaks everything keyed by candidate identity**: `topo_uid` shifts when a rule changes, invalidating sidecar selections, script pins, dominance/dedup groups, and op-log provenance — the #517 pin-fragility lesson in new clothes.  Only path that lets TopoEdit hand-edit shields; not worth it. |

3a also keeps R12 trivially provable: no rule ⇒ stage 9 emits nothing
extra ⇒ byte-identical.  The UI consequence (rule-derived ghost shields in
the explorer, drawn from the declaration rather than stored geometry) is
recorded in [../NDR_UI.md](../NDR_UI.md).

## 4. Persistence design

- **Hier/BDB** (schema v21): an `ndr_rule` table (name → values; per-layer
  overrides as JSON, the busterm multi-rect precedent) plus an `ndr_scope`
  attachment table (prefix → rule; `*` default in meta) — the
  `cell_layer_share`/v20 policy-table pattern reused wholesale:
  session-typed entries win over restored ones, `open_bdb` restores,
  `set_ndr * off` clears, and **`load_pipeline` re-resolves demand onto
  restored wrappers and VOIDs (LOUD, re-plan required) any restored plan a
  since-tightened rule outlaws** — semantics already designed and tested
  for layer caps.
- **Flat/script**: declarations live in the script (like `def_layer`);
  nothing new to build.
- **Sidecar**: with 3a, *no format change* — `topo_uid`-keyed selections
  stay valid across rule edits.  (The sidecar stores decisions, not
  declarations: per-bundle selections keyed by bundle hint + `topo_uid`,
  with pinned layers — `src/viz_explorer/sidecar.py`.)  The only
  sidecar-adjacent additions would come from the deferred bundle-level
  attachment override or `edit_shield_side` — which is exactly why both
  are deferred.
- **Provenance**: an `edit_commit` in a session with rules in effect
  stamps the resolved rule name into the existing `user_ops` meta — one
  line, keeps the how-it-was-built record honest.

## 5. Per-requirement implementation mapping (path A phase 1)

| Req | Where it lands |
|---|---|
| R1/R3 | `def_ndr` handler (new `buda_cmds` module) + a C++ `NdrRule` in `buda_core`; R3 arithmetic against each declared layer's pattern at declaration/first-resolution |
| R2 | **LANDED incl. R2d (§7.4)** — prefix resolution beside `set_bundling`'s (longest-prefix, `*` default); hier: rule-class split on TEMPLATE bundles before expansion, replicas in lockstep, class-congruence hard error |
| R4 | ONE new function (working name `group_track_demand(members, layer)`, where `members` carries each bit's RESOLVED rule — a mixed-rule bundle (R8) means the group has no single rule, so a one-rule signature would either collapse members to the wrong rule or let planner and placement demand disagree; the shield arrangement is derived per rule-class within the group, and the layer/pattern context prices the quantization) in `buda_core`, consumed by `eff_bus_width`, planner `signal_tracks` capacity, kPeak floor, dead-span escalation, doomed-seat census, `_seg_admission_pool`, DNUTS admission |
| R5 | generalize `for_each_signal_track_in_span` to run-of-k enumeration behind a rule-aware wrapper; `count_` sibling stays no-allocation, vector/count lockstep preserved |
| R5a | **LANDED (§7.3)** — opt-in `credit`: end-shield rail crediting inside the credited demand/layout pair, seat-decided at DNUTS, audited by the SAME predicate (`ndr_shield_net_matches` / `ndr_rail_credits`) |
| R6 | `place_by_layer`: k-slot claims, guard slots, shield emission (`NetSegment` + shield flag + shield-net id); footprint-aware occupancy for the sharing exemption; keepout cull covers shields |
| R7 | demand units through `BundleWrapper` width plumbing; layer restriction enters the planner's layer enumeration like `allowed_layers` |
| R8 | bundler split pass re-derives group demand per part; split report gains the demand delta |
| R9 | **LANDED (§7.2)** — `check_design` dnuts stage: NDR_WIDTH / NDR_SPACING / NDR_SHIELD typed violations |
| R10 | v21 tables per §4; `load_pipeline` VOID path |
| R11 | detailed viz width-proportional wires + SHIELD toggle; `report_wirelength` shield-metal line |
| R12 | corpus byte-identity run per landing phase (the standing opt-in guard) |
| R13 | bottom-up copy path carries shield rows; `check_template_tracks` pools via the rule-aware R5 queries |

## 6. Phasing

1. **Phase 1 — path A pure**: `def_ndr` + `set_ndr` + `dump_ndr`, group
   conversion single-sourced, DNUTS emission (3a), always-emit shields,
   v21 persistence, R9 audit, explorer badge/tint/ghosts, R12 corpus gate.
2. **Phase 2 — R5a crediting** (the D upgrade): the label→net-identity
   predicate (audit first, credit second), credit as a term inside the
   group conversion.
3. **Later, demand-gated**: bundle-selector attachment override, cell
   -scoped attachment, `edit_shield_side`, abstract-viz footprint display,
   any §3-non-goal promotion that architect feedback requests.

## 7. Phase-1 prototype — AS BUILT (2026-08-06)

The phase-1 slice landed on path A exactly as §5/§6 mapped it, with the
following as-built specifics and honest deviations:

- **`src/ndr.h`**: `NdrSpec` (width_slots / guard_slots / shield_mode /
  shield_per_n / shield_net / rule_name; `active()` = the R12 gate) + the
  single-sourced `ndr_group_demand(spec, nbits)` and its placement-side
  rendering `ndr_run_layout` (test-pinned lockstep: `len(layout) == demand`
  for every shape).  **Uniform-rule signature, not per-member**: phase 1
  adopts R8's rule-uniform-splitting fallback (the bundler splits a
  mixed-rule bundle by rule class LOUDLY, `|NDR:<rule>` reason suffix), so
  (spec, nbits) IS the member list; the per-member `group_track_demand
  (members, layer)` signature remains the target if mixed-rule bundles are
  ever kept whole.
- **Charging (R4/R7)**: `ndr_units()` in `congestion_planner.cpp` routes
  every bundle-scoped `eff_bus_width` site through the group demand —
  `score_candidate_`'s seg_n/seg_w lambdas (the live scoring path; NOTE
  `plan_bundle` carries an identical pre-existing DEAD copy), band charging,
  share usage, demand reservations, commit_plan, and the track-mode ntrk
  handoff — plus the abstract-NUTS `ts.width` extraction in `nuts.cpp`.
- **DNUTS (R6)**: an `bs.ndr.active()` branch in `place_by_layer` claims a
  run of demand consecutive pool slots nearest the anchor, walking the
  layout: bits at `width_slots` (physical contiguity required only WITHIN a
  bit's slots — a guard/shield gap may straddle a pattern rail, which only
  adds clearance; the first whole-run-contiguous draft measurably
  over-refused), guards reserved-but-empty (sentinel bit id), shields
  emitted as `NetSegment{is_shield=true, bit_index=-1,-2,…}`.  Admission
  compares the pool against `bus_seg_demand()` (the same conversion), and
  same-bundle sharing is disabled for NDR groups (hazard tracks reserved).
- **CLI**: `def_ndr <name> [width xN] [spacing xN] [shield bus|bit|per:N
  [net <label>]] [layers <csv>]` (declare-once LOUD; multiplier form only
  in phase 1 — quantization is pattern-independent), `set_ndr <prefix>|*
  <rule|off>` (longest prefix wins), `dump_ndr` (rules, scopes, per-bundle
  demand + layout).  R3 realizability fires at `run_detailed_nuts` (first
  resolution with the grid known): a rule needing more contiguous SIGNAL
  slots per bit than any run on a governed layer's pattern hard-errors
  with the arithmetic.  `run_hier_bundler` refuses when scopes exist.
- **Guarded consumers**: shields excluded from span-follow, via emission,
  and the pair-misalign predictor (negative ordinals are NOT net ids);
  BDB `net_segment` persistence writes the shield net's name (and the
  negative-index wraparound into `net_names[-1]` is fixed); detailed-WL
  reporting and `qor_corpus` count shield metal separately (R11).
- **Vehicle + tests**: `flow/ndr_demo.buda` (the §4-vehicle-1 shape: the
  STRICT bundler merges clk+data on identical endpoints and the rule-class
  split separates them — a nice accidental demonstration), `test_ndr.py`
  (20 tests: lockstep, group-not-per-bit demand, validation LOUDness,
  longest-prefix resolution, end-to-end shields/widths/spacing, planner
  demand honesty, R3 refusal, R12 inertness of unattached rules, shield-WL
  separation, clean check_design).

### 7.1 v21 BDB rule persistence — LANDED (2026-08-08)

R10 shipped on the §4 design, the v20 policy-table pattern verbatim:

- **Schema v21**: the `ndr_rule` table stores declared rules with their RAW
  multiplier values (quantization to slots stays session-side, so a stored
  rule survives a quantizer policy change), `ndr_scope` stores prefix→rule
  attachments (`*` = the global default; the rule FK made LOUD —
  `set_ndr_scope` refuses an undeclared rule), and `bundle.ndr_rule` stamps
  each persisted bundle's governing rule.  Migration is one idempotent
  ALTER (the tables ride the fresh-DB CREATE IF NOT EXISTS); pre-v21
  fixtures open and migrate cleanly (test-pinned on the committed v20
  fixture).
- **Write-through + restore**: `def_ndr`/`set_ndr` write through when a BDB
  is open; `open_bdb`/`load_pipeline` restore with session-typed entries
  winning and a previous BDB's restored entries dropped (the Codex #546
  BDB-switch rule); declare-then-open converges the BDB to the session's
  typed entries.
- **VOID-on-change**: `bundle.ndr_rule` stamps each persisted bundle's
  governing rule as its **pricing fingerprint** — the rule name plus the
  QUANTIZED spec and layer restriction (`name|wN|gN|sN|pN|nNET|Lcsv`) —
  so `load_pipeline` re-resolves rules onto restored wrappers (EVERY
  member net, not just the first: a scope matching only a non-leading net
  makes the bundle MIXED, which VOIDs with a re-bundle notice — the split
  itself is stale) and VOIDs (LOUD, re-plan required) any bundle whose
  fresh resolution fingerprints differently from the stamp.  The
  fingerprint carries the pricing basis inside the bundle row, so a
  same-name redeclare in a LATER session voids correctly even though the
  stored rule definition was overwritten (the cross-session hazard a
  content-snapshot design could not survive), and a content change that
  quantizes identically (x1.8 → x2.0) correctly keeps the plan.  Voided
  bundles' restored routing is excluded from the rehydrated NUTS result,
  exactly like the v20 cap audit.

**Prototype limitations (deliberate, each with its phase):**
- ~~Flat flow only~~ — R2d hier template propagation LANDED (§7.4).
- ~~No R5a crediting~~ — LANDED, opt-in (§7.3).
- **No shield vias/connectivity** (R6 partial): an EMITTED shield is still
  a floating rail (see §7.3 for the decided disposition — a CREDITED end
  needs no bonding at all, the rail is already grid-connected).
- ~~Explorer badge/tint/ghosts not built~~ — LANDED: header badge
  (rule + group demand, `+credit`), debug-view realizability flag (a
  candidate whose slide windows cannot host the demand on any of its
  layers renders red with the arithmetic; unbounded windows and missing
  grids are conservative no-flags), and the rule-derived shield ghost
  overlay (dashed envelope edges at the run's outer-slot offsets —
  declaration-derived, read-only, nothing persists).  All gated on an
  active spec: ungoverned designs render byte-identically (test-pinned,
  `test_topo_explorer_ndr_surface.py`).
- The doomed-seat census/forensics still count member bits, not demand
  units, for NDR segments — harmless while admission strands LOUDLY, but
  the census should adopt `bus_seg_demand` when NDR designs get big
  enough to census.

### 7.5 R13 bottom-up composition — VERIFIED (2026-08-08)

R13 was the last requirement never exercised: nothing combined `def_ndr`
with `set_bottom_up`.  `flow/ndr_bottom_up.buda` closes that — a
4-instance `sub_cell` template governed by a flank-the-bus rule, marked
bottom-up and aligned, with a governed top-level bus planned around the
frozen copies.

**The mechanism holds by construction.**  `transform_net_segment` copies
the whole `NetSegment` (`out = ns`) before transforming its geometry, so
`is_shield` and the shield's negative bit ordinal ride along; the copy
loop copies every reference row.  Measured: all four occurrences carry
their 2 shields, at their own instance coordinates.  `align_bottom_up`
and `check_template_tracks` need no NDR awareness — they compare raw
per-instance signal-track POOLS, which is strictly stronger than a
demand comparison (identical pools ⇒ identical NDR seating), and the
class reports ALIGNED on the vehicle.

**Two shield-counting faults surfaced and were fixed** — the #616 class
(shield rows counted as signal bits) in paths that fix never reached:

1. **Copy-path unplaced accounting** (`nutsflow.py`, the bottom-up copy
   loop): `exp_bits` summed `bit_width` (member bits) but `placed_bits`
   counted EVERY reference row including shields, so
   `extra_unplaced += exp − placed` went NEGATIVE for a governed cell —
   the vehicle reported **−6 bits unplaced**, a count that masks real
   opens design-wide.  Now skips `is_shield` rows; the vehicle reports 0.
2. **Cull-risk survival predictor** (`_escalate_dead_low_segments`,
   `cull_risk=True`): the same inflated count fed `placed >= need`, so a
   governed segment with bits genuinely stranded could read as fully
   placed and skip its escalation.  Fixed by the same rule.  No dedicated
   test: the predicate sits behind `wmap`, which excludes `hier.locked`
   bundles, so bottom-up instances never reach it — observing it needs a
   governed NON-locked LOW segment whose seat is starved yet still R3-
   realizable (a coarse pattern hard-errors at declaration instead).

Both fixes are `is_shield`-conditional, so no-NDR designs are untouched
(corpus-guarded); an ungoverned run of the same vehicle is test-pinned.

### 7.2 R9 typed audit — LANDED (2026-08-08)

The dedicated audit shipped as three typed violations in `check_design`'s
detailed stage, built on the Python-side duck-typed-violation precedent
(`NET_DRIVER_OPEN`): `audit_ndr_dnuts(session, wrapper)` in
`buda_cmds/ndr_cmds.py` runs per governed bundle (gated on
`input.ndr.active()`, so an ungoverned design's report is byte-identical)
and its `_NdrViolation` objects flow through the existing summary/reason
machinery beside the C++ `ConnViolation`s:

- **NDR_SHIELD** — per governed segment, the placed shield-row count must
  equal the rule's `ndr_run_layout` `'S'` count for the segment's
  **intended membership** (`_seg_member_bits`, the same accounting DNUTS
  admission uses — a keepout-culled signal bit is already UNPLACED and
  must not re-shape the expected layout into a spurious mismatch), and in
  flank-the-bus mode the shields must actually be the run's outermost rows
  (a shield inside the bits, or a bit outside the shields, is misplacement
  even at the right count).
- **NDR_WIDTH** — each governed bit's placed extent must cover its
  `width_slots` SIGNAL slot centres (`signal_tracks_in` at the span
  midpoint over `track ± width/2`), so a bit that lost its continuation
  slots is LOUD even though it placed.
- **NDR_SPACING** — the rule's run is EXCLUSIVE: any foreign bundle's wire
  whose track centre lands strictly inside the run's extent with
  overlapping span violates the reserved clearance (guard slots are
  reserved-empty, so any occupant is foreign by construction).  For an
  UNSHIELDED rule the audited extent extends over the **end guard slots**
  beyond the outermost placed wires (the layout reserves them; the placed
  rows alone stop at the outer bit edges), and **aggressor pre-routes**
  are audited too: a CLOCK (or unknown-identity CUSTOM) pattern rail
  running inside the reserved window is fixed metal `net_segments` can
  never show — the pre-routes-vs-spacing case of R6 — while static
  supply/shield rails (POWER/GROUND/SHIELD) stay exempt per the
  documented straddle-neutral phase-1 model (a run's gaps may cross
  rails, which only add isolation).

The audit is indexed, not scanned: `build_ndr_audit_index` materializes
`dr.net_segments` ONCE per `check_design` pass (the pybind vector converts
to a fresh list on every property access) and groups rows by bundle and by
layer sorted on track position, so each segment's foreign-wire lookup is a
bisect over the run window instead of a full pass — linear-ish where the
naive form was quadratic in placed rows.

Beside the audit, the **R5a/R9 shared net-identity predicate** shipped in
`src/ndr.h` as `ndr_shield_net_matches(requested, label)` (bound to
Python): case-insensitive label equality, or same-supply-family membership
(GND/VSS/GROUND one family, VDD/VCC/POWER the other; a POWER rail can
never satisfy a GROUND spec).  The audit is its contract-definition site;
its credited-rail consumer — pricing a pattern rail as the rule's shield —
arrives with phase-2 R5a crediting, and sharing the ONE predicate is what
keeps credit and audit from ever disagreeing (the review-pinned
requirement).  Phase-1 emitted shields carry the rule's own `shield_net`,
so the label check is definitionally clean today; the predicate starts
mattering the moment crediting substitutes a rail.

### 7.3 R5a end-shield rail crediting — LANDED (2026-08-08)

The phase-2 crediting increment, **opt-in per rule** (`def_ndr … shield …
credit` → `NdrSpec.credit_shields`; no token anywhere = byte-identical,
including every phase-1 governed flow):

- **The credited pair** (`ndr.h`): `ndr_group_demand_credited(spec, nbits,
  c_lo, c_hi)` and `ndr_run_layout_credited(…)` — the base pair with a
  credited END's `'S'` neither charged nor emitted, lockstep for every
  credit combination (test-pinned) and reducing exactly to the base pair
  for unshielded/uncredited shapes.  Interior shields are NEVER credited —
  phase-2 scope is the parked-against-a-rail case.  `ndr_rail_credits(
  spec, label, type)` is the ONE credit predicate (label first, slot type
  fallback, `ndr_shield_net_matches` underneath), shared verbatim by the
  seat search and the audit.
- **Seat-decided at DNUTS**: the seat search evaluates each candidate
  start with its own credit pair — the low end credits when the pattern
  slot immediately below the run's first signal slot is a matching rail
  (no empty SIGNAL slot between), the high end symmetrically at the
  credited run's last slot — and the rail must actually RUN THROUGH the
  segment's span (`preroutes_in` coverage merge: a keepout-broken rail is
  absent metal, no credit).  A credited seat consumes 1–2 fewer SIGNAL
  slots; the early admission gate uses the optimistic
  `bus_seg_min_demand` (both ends credited) so a pool only the credited
  form fits is not refused before the search runs.
- **Planner stays on the uncredited worst case** (R7): band charging,
  abstract width, and R3 realizability all price `ndr_group_demand` —
  conservative by design, since whether a seat credits is unknowable at
  planning time.  The credit is pure DNUTS-side relief.
- **The audit is the crediting consumer** (the R5a/R9 agreement): per
  governed segment the R9 shield check derives each end's credit from the
  PLACED GEOMETRY — outermost row a signal bit + adjacent matching rail
  (same predicate, same no-signal-slot-between and span-coverage rules,
  `_ndr_end_credit`) — and expects the credited layout, so a bit-at-the-
  end run with NO matching rail keeps the uncredited expectation and
  fails LOUD, while a legitimately credited end audits clean.  The audit
  checks the property, not the provenance.
- **v22**: `ndr_rule.credit` column (one idempotent ALTER; pre-v22 rules
  default 0 — correct, they never credited), write-through/restore, and
  the pricing fingerprint gains `|c1` ONLY when set, so v21 stamps of
  non-credit rules still compare equal (a resumed v21 checkpoint must not
  VOID on a fingerprint-format change).
- **Shield connectivity disposition (R6)**: a CREDITED end needs no
  bonding — the rail is the power grid's own metal.  An EMITTED shield
  remains a floating rail; physical bonding vias to the supply grid are
  deferred until export flows (GDS/DEF) need them, as they are power vias
  with different table semantics than the per-bit `net_via` rows.
- **Tests**: credited-pair lockstep across credit combos, the credit
  predicate families, deterministic direct-engine seats (both-ends
  credit → zero emitted shields; POWER rail against a GND spec never
  credits; one-end credit), the e2e credit flow whose R9 audit agrees
  wherever the seats land, fingerprint suffix back-compat, v22
  round-trip + genuine-v21 migration.

### 7.4 R2d hier template propagation — LANDED (2026-08-08)

`run_hier_bundler` now PROPAGATES declared rules instead of refusing —
the last flow-scope limitation lifted, on the `set_max_bundle_bits` hier
pattern throughout:

- **Rule-class split before expansion** (`split_mixed_ndr_hbundles`): a
  mixed-rule TEMPLATE bundle splits into rule-uniform parts (every
  HBundle hier field preserved via `_clone_hbundle_with_id`, per-net
  fan-in arrays split in the same partition with the fan-in
  reason/busterm re-scope, `|NDR:<rule>` suffix), and a cell-local
  template's REPLICAS split in LOCKSTEP with `parent_id` rewired
  part-for-part — so the split propagates identically to every occurrence
  through the template↔replica donor keying.  Runs AFTER the bit-cap
  split (the flat order).
- **Class congruence is a hard error**: occurrences of one cell-local
  class carry instance-specific net names, so the class is well-governed
  only when every occurrence's nets partition into the SAME (rule,
  bit-index) classes — a template is solved once and copied, so
  per-occurrence rule differences cannot ride it.  A scope naming only
  one occurrence's prefix (`set_ndr is_lr_u1s2_ r` on vehicle 05's
  24-occurrence class) refuses LOUDLY naming both partitions; the
  congruent forms — one class-wide prefix, or per-occurrence scopes on
  the same bits — govern and split cleanly (test-pinned on the real
  vehicle).
- **Specs ride expansion**: `apply_ndr_specs` stamps templates AND
  replicas at bundler time (before the persist fingerprints);
  `_expand_hier_bundles` copies `input.ndr` onto every per-instance
  wrapper (donor nets resolve to the same rule by the congruence
  guarantee), and a governed template's 90° rotation-class clone keeps
  the rule too.
- **Layer restrictions vs the cell-band resolver**:
  `_apply_layer_policies` OWNS `allowed_layers` on hier wrappers
  (clears/rewrites at every wrapper-set transition), so the rule's layer
  restriction is RE-APPLIED after each resolution
  (`reapply_ndr_layer_restrictions`, called on both resolver paths): the
  effective mask is the band ∩ the rule's layers, and an empty
  intersection hard-errors naming both constraints.
- **Everything downstream is flow-agnostic already**: planner charging,
  abstract width, DNUTS placement/crediting, the R9 audit, and the
  v21/v22 persistence all read `input.ndr` off whatever wrappers exist —
  measured on vehicle 06 (6 governed occurrences, shields at every
  instance) and 05 (24-occurrence template class).  No scopes = the
  split/stamp/re-apply hooks all no-op (corpus byte-identity).

### 7.6 R6 shield bonding — LANDED (2026-08-10)

Closes [`opens_ndr.md`](opens_ndr.md) §1's headline gap. An emitted shield
was labeled metal: a first-class routed wire with the rule's shield-net
identity, reserving its track, connected to nothing. `bond` (opt-in, per
rule, requires a shield arrangement) straps each emitted shield to the
power grid after placement.

**Where it runs.** `emit_shield_bond_vias`, a free function called from
`run()` inside the `if (emit_vias)` block — bonding is pure output, exactly
like the per-bit via emission, so a ripup fast trial that skips vias skips
the straps too and no metric moves.

It is **idempotent and free-standing** on purpose: it reads only placed
shield geometry against a real grid, dropping any straps already present
before recomputing. That is what lets the bottom-up path re-derive each
COPIED instance's straps from ITS OWN coordinates rather than transforming
the reference's. The distinction matters (Codex on #663): a strap's validity
depends on the **adjacent perpendicular** layer's rails, while
`check_template_tracks` establishes copy eligibility from track pools on the
**routed** layer alone — so a sibling under a different grid override or
keepout there could inherit a strap sitting over the wrong supply, and the
`NDR_BOND` audit would read the row as proof of bonding. The merge therefore
skips negative-`to_seg` vias in the copy loop and calls the pass once over
the merged result, so reference, copies and independently-solved instances
are all bonded by what actually crosses them.

**What counts as a bondable crossing.** For a shield on `layer`, the
candidates are `layer ± 1` — present in the stack AND **perpendicular**.
The orientation is *tested*, never assumed: `def_layer` takes an explicit
direction, so BUDA does not force the stack to alternate, and two parallel
layers have no crossing to via. The rails are found with one
`RoutingGridStack::preroutes` query per adjacent layer, with the shield's
span as the adjacent layer's PERP range and the shield's track position as a
point on its ALONG axis — by construction exactly the rails that cross this
shield. Identity is `ndr_shield_net_matches` (label, falling back to slot
type), THE shared predicate: a POWER rail can never bond a ground shield,
and a VSS-net rule bonds to GND rails by supply family. A **credited** end
emits no shield, so it never reaches this pass.

**The via-table decision.** A strap is a `net_via` row, not a new power-via
table, keyed by a NEGATIVE `to_seg` — the strap ordinal `-(k+1)`. A real
segment index is `>= 0`, so the `(bundle_id, from_seg, to_seg, bit_index)`
primary key stays unique across a shield's straps, and the sign itself
encodes "the far end is a grid rail, not a routed segment". Consumers that
read vias geometrically — GDS export, the detailed viz — use
`from_layer`/`to_layer`/`x`/`y` only, so they were unaffected; the BDB
persist path names a strap with the rule's shield net (`bit_net` would return
"" for a negative bit index). Schema v25 adds `ndr_rule.bond`, and it is
deliberately absent from the `bundle.ndr_rule` PRICING fingerprint: bonding
moves no demand, so toggling it must not VOID a restored plan.

**Audit.** `check_design` raises `NDR_BOND` for an emitted shield with zero
straps — floating metal. That is a grid problem (no matching rail crosses
it on an adjacent perpendicular layer), not a routing one, and the message
says so. The strap counts ride `build_ndr_audit_index` as one more pass over
the via vector, skipped when nothing was emitted.

**Residual limits** (recorded in `opens_ndr.md` §1): every crossing is
strapped, with no stride or via budget (280 straps on the small
`ndr_bond.buda` vehicle — conservative, but a real flow may want a declared
stride); and only `layer ± 1` bonds, since a rail two layers away needs a via
stack, whose intermediate track is a placement decision rather than an output
one.

Vehicle: [`flow/ndr_bond.buda`](../../flow/ndr_bond.buda) — one rule matching
the rails by label, one through the supply family, both clean.
