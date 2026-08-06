# NDR Support — Internal Architecture

**Audience: implementers.**  The as-built baseline NDR must generalize, the
candidate solution paths with their assessment, the shield-materialization
and persistence decisions, and the per-requirement implementation mapping.
Requirements (R1–R13) live in [../NDR_REQUIREMENTS.md](../NDR_REQUIREMENTS.md);
the user-surface options in [../NDR_UI.md](../NDR_UI.md).  Tracked as
[opens.md](opens.md) "Substantial features" item 15 and on the
[work menu](work_menu_2026-08-06.md).  Status: **path leaning recorded, not
frozen; nothing built** (2026-08-06).

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
| R2 | prefix resolution beside `set_bundling`'s (longest-prefix, `*` default); hier occurrence-group resolution reuses the scoped-bit-cap union machinery |
| R4 | ONE new function (working name `group_track_demand(members, layer)`, where `members` carries each bit's RESOLVED rule — a mixed-rule bundle (R8) means the group has no single rule, so a one-rule signature would either collapse members to the wrong rule or let planner and placement demand disagree; the shield arrangement is derived per rule-class within the group, and the layer/pattern context prices the quantization) in `buda_core`, consumed by `eff_bus_width`, planner `signal_tracks` capacity, kPeak floor, dead-span escalation, doomed-seat census, `_seg_admission_pool`, DNUTS admission |
| R5 | generalize `for_each_signal_track_in_span` to run-of-k enumeration behind a rule-aware wrapper; `count_` sibling stays no-allocation, vector/count lockstep preserved |
| R5a | phase 2: label→net-identity predicate shared between credit and the R9 audit |
| R6 | `place_by_layer`: k-slot claims, guard slots, shield emission (`NetSegment` + shield flag + shield-net id); footprint-aware occupancy for the sharing exemption; keepout cull covers shields |
| R7 | demand units through `BundleWrapper` width plumbing; layer restriction enters the planner's layer enumeration like `allowed_layers` |
| R8 | bundler split pass re-derives group demand per part; split report gains the demand delta |
| R9 | `check_design` nuts/dnuts stages: NDR_WIDTH / NDR_SPACING / NDR_SHIELD typed violations (BIT_SHORT machinery generalized to clearance) |
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
