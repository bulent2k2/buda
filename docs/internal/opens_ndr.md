# Open items — non-default rules (NDR)

What the NDR feature deliberately does **not** do yet, and why each gap was
left rather than forgotten. The feature itself is documented for three
audiences — [`../NDR_REQUIREMENTS.md`](../NDR_REQUIREMENTS.md) (architects,
the R1–R13 contract), [`../NDR_UI.md`](../NDR_UI.md) (methodology, the
command/GUI surface), [`ndr_architecture.md`](ndr_architecture.md)
(implementers, as-built) — and the user-facing command reference is
[`../script_reference/ndr.md`](../script_reference/ndr.md).

Snapshot index — last verified against `main`: **2026-08-12**, after R1
(absolute width/spacing) landed in full — declaration, persistence AND
per-layer resolution at every routing consumer — after the doomed-seat
HEALS moved from member bits to group demand (including validating that the
escalation TARGET can host the run it is handed, resolved on that layer),
and after the cull-risk shield-counting fix acquired the repro it lacked. Every requirement has a
working implementation and a vehicle, and the feature is usable end to end —
**R8 is the one still met only in part** (the rule-uniform fallback), and R1
and R6 each carry a residual limit below now that their headline gaps are
closed. What follows is that residue.

**None of these is blocking.** Each is a bounded piece of work waiting for a
design that needs it, and the current behaviour in each case is conservative
(costs capacity, never produces an illegal route) or loudly reported.

---

## 1. Shield bonding vias (R6 — LANDED, with one residual limit)

**Implemented** (the opt-in `bond` token). After placement, every EMITTED
shield is strapped to the power grid with a via wherever an identity-matching
rail crosses it on an **adjacent perpendicular** layer — the same
`ndr_shield_net_matches` predicate crediting and the R9 audit use, so a POWER
rail can never bond a ground shield. `check_design` raises **`NDR_BOND`** for
an emitted shield with zero straps: that is a grid problem (no matching rail
crosses it), not a routing one. Vehicle: [`flow/ndr_bond.buda`](../../flow/ndr_bond.buda).

*The via-table decision.* A strap reuses the per-bit `net_via` row rather
than introducing a power-via table, keyed by a **negative** `to_seg` (the
strap ordinal — a real segment index is `>= 0`, so the
`(bundle_id, from_seg, to_seg, bit_index)` primary key stays unique). The far
end is a grid rail, not a routed segment, which is exactly what the negative
ordinal encodes. Consumers that read vias geometrically — GDS export, the viz
— use `from_layer`/`to_layer`/`x`/`y` only and were unaffected.

The pass is **idempotent and free-standing**, reading placed shields against
a real grid, so the bottom-up path re-derives each copied instance's straps
from its own coordinates rather than transforming the reference's: copy
eligibility is proved on the ROUTED layer's track pools, which says nothing
about the ADJACENT layer's rails, and a sibling under a different override
there must get the honest answer (possibly `NDR_BOND`).

*Note the asymmetry:* a **credited** end (R5a, the `credit` token) needs no
bonding at all — the rail is the power grid's own metal, so it never reaches
the strap pass.

*Strap density is declared.* `bond stride <N>` straps every Nth crossing
instead of all of them — every crossing was the original behaviour and is
still the `bond` default (stride 1), but it is hundreds of vias on a long
shield over a dense grid. Both **extremes are always anchored** whatever N
divides out to: an unbonded tail hanging off the last strap is exactly the
floating metal bonding exists to prevent. The stride rides the existing
`ndr_rule.bond` column (0 = off, N = stride), so it needed no schema bump
and a stored 1 still means what it always meant.

**Residual limit:**

- **Adjacent layers only.** A shield bonds to `layer ± 1` when that layer is
  perpendicular and has a grid. A rail two layers away needs a via stack,
  which needs the intermediate layer's own track reserved — a placement
  decision, not an output one, and out of scope here.

*Where to start:* `emit_shield_bond_vias` in `detailed_nuts.cpp`.

## 2. Absolute (µm) width and spacing values (R1 — LANDED)

**Implemented.** `def_ndr` takes either form: `width x2` (multiplier,
pattern-independent) or `width 3` / `width 0.2um` (absolute, through the
shared `require_distance` parser). An absolute value resolves **per layer**
against the layer's per-signal-slot channel cost, so one declaration means
different slot counts on layers of different pitch — what the multiplier
form structurally cannot express.

*The design question this settled.* A value falling between slot counts
**rounds up**, the convention multipliers already use (`x1.5` pays 2 slots —
conservative, never illegal). Refusing non-boundary values was the
alternative, and it would have made a rule un-portable across exactly the
mixed-pitch stacks the form exists for.

*The divisor* is the per-signal-slot channel cost
(`unit_pitch / n_signal_slots`), not the raw slot pitch: it amortizes the
power rails across the signal slots and is what `eff_bus_width` already
charges, so a rule and the width model agree by construction rather than by
coincidence.

*Ordering is a real precondition, enforced LOUDLY.* Every layer the rule can
reach must already carry a `def_track_pattern`. Requiring all of them rather
than one is what keeps the stored maximum conservative — a maximum over a
subset is not, since a pattern declared later on an omitted layer could need
more slots.

Persisted at BDB **v26** (`ndr_rule.width_abs` / `spacing_abs`). The
QUANTIZATION is deliberately not stored — it is a function of the current
grid, so restore re-derives it and warns when the governed layers have no
pattern yet.

*Every consumer resolves per layer*, not just the declaration. The planner
(`ndr_units`, threaded through `score_candidate_`'s `seg_n`/`seg_w` with the
layer being priced), abstract NUTS (the segment's assigned layer), DNUTS
(pre-resolved ONCE in `make_bus_segments`, where the rule and the placed
layer are both in hand, so every stage-9 consumer reads an already-per-layer
spec), the seat census, R3 realizability and the R9 audit all go through the
same two conversions — `ndr_group_demand_on` in C++, `ndr_spec_for_layer` in
Python. Auditing against the stored maximum instead reported a correctly
placed bit on a coarse layer as `NDR_WIDTH`, which is what a design measured
in the wrong units looks like from the outside.

*Why one change and not three.* Over-charging is safe in direction, so a
partial conversion is not dangerous — but it leaves the stages disagreeing
about a governed group's demand, which is the single-sourcing invariant R4
exists to protect.

*Two settled design points.* **No double-count:** the charged width is
`slots x bit_pitch(layer)`, so per-layer slots make that product ≈ the
declared width on every layer, which is what an absolute value should mean.
**Global pitch, not the override-aware effective pattern:** more accurate
locally, but the planner would then price against a different basis than
DNUTS admits on — the same two-stages-disagree failure.

A layerless caller (the `make_bus_segments` overload without a stack, a
census with no seat) keeps the stored quantization, which for an absolute
rule is the conservative MAXIMUM: the fallback over-charges, never under.

**Residual limits:**

- **The divisor is a CHANNEL cost, so a layer's METAL can fall short of
  the declared width.** `ndr_resolve_for_pitch` divides by `bit_pitch`
  (`unit_pitch / n_signal_slots`) — how much *channel* the width consumes,
  rails amortized in. A k-slot bit's emitted metal is a different
  quantity, `k·w + (k−1)·sp` over the layer's own slots, which includes
  neither the rails nor the gaps outside the run. Nothing forces the two
  to agree.

  **Repro: [`flow/ndr_abs_divisor.buda`](../../flow/ndr_abs_divisor.buda)**,
  which produces all four cases in one run. DENSE = `VDD 2 1 (_ 1 1)x12
  GND 2 1` (pitch 2.5, k slots give 2k−1 metal); SPARSE = `VDD 10 2
  (_ 2 2)x4 GND 10 2` (pitch 10.0, k slots give 4k−2):

  | rule | layer | k = ceil(W/pitch) | metal | vs declared |
  |---|---|---|---|---|
  | `width 3` | M5 dense | 2 | 3 | **exact** |
  | `width 3` | M6 sparse | 1 | 2 | short by 1 |
  | `width 8` | M5 dense | 4 | 7 | short by 1 |
  | `width 8` | M6 sparse | 1 | 2 | **4× under** |

  The first row is the one to internalize: it is exact *by coincidence of
  the declared value*, and the third row is the same layer failing. So a
  stack where it happens to work is not evidence the readings agree. The
  last row is the headline — `ceil(8/10) = 1` means `width_slots 1` and
  `guard_slots 0`, an INACTIVE spec, so the bus routes as an ordinary one
  and gets 2 units of metal for a declared 8. Spacing has the same shape
  (a gap of g guards clears `(g+1)·sp + g·w`, not `g·bit_pitch`).

  `check_design` is **clean** on that vehicle, correctly: the R9
  `NDR_WIDTH` check counts covered SIGNAL slot *centres*, so it measures
  the same channel-shaped quantity the quantization used and agrees with
  it by construction. `dump_ndr` after `run_detailed_nuts` reports the
  delivered metal beside the declared width wherever they differ — the
  only place the gap surfaces, and only because this open was written
  down.

  This is inherited from R1 part 1, not introduced by the per-layer
  resolution — that only made it *visible*, since the conservative
  maximum used to over-deliver on a coarse layer by accident. It is a
  genuine semantic choice, not a bug to patch quietly: `bit_pitch` is the
  right divisor for CHARGING (it is what `eff_bus_width` books, which is
  the no-double-count property) and the wrong one for REALIZING a
  physical width.

  **Landed as opt-in** (`def_ndr ... metal`), together with the per-layer
  values below — see `flow/ndr_per_layer_em.buda`.  It is opt-in rather
  than the default because the honest reading is **stricter**, not merely
  different: a layer whose signal slots sit isolated between rails delivers
  only one slot's metal, so `width 4` there — which the channel reading
  accepts by silently delivering 2 — becomes an R3 refusal.  That rejects
  designs which route today, so the default flip owes its own measured
  study, the same path `kSegsRel` / `spine_relays` / `band_span_charge`
  took.  Two residuals of the opt-in landing: per-layer values are NOT yet
  persisted to the BDB (BUDA-1911 says so LOUDLY rather than restoring a
  rule missing half its declaration), and the R9 audit still measures the
  channel-shaped quantity, so it agrees with a channel rule by construction
  and does not yet check metal against a `metal` rule.

  *The original note, kept because it is the reasoning*: quantize width by the smallest k
  with `k·w + (k−1)·sp ≥ width_abs` and spacing by the smallest g with
  `(g+1)·sp + g·w ≥ spacing_abs`, both read off the layer's pattern. That
  makes `ndr_resolve_for_pitch` need the SLOT GEOMETRY rather than one
  scalar, and the CHARGING conversion must keep using `bit_pitch` so the
  planner's books stay consistent — i.e. the code would carry both
  numbers at once, which is the real cost and the reason this is a
  decision rather than a patch. The R9 audit would have to move with it:
  a metal-shaped rule needs a metal-shaped check, or the two disagree
  again one level down. Decide before a design leans on absolute widths
  for EM or resistance; the channel-shaped reading is fine for congestion
  planning, which is what every current consumer of the number does.

- **A rule that resolves to one slot resolves to NO rule.** On a layer
  whose single signal slot already covers the declared width, the spec
  quantizes to `width_slots 1 / guard_slots 0`; with no shield declared
  that is an INACTIVE spec, so the bundle routes as an ordinary bus there.
  Consistent with the charging reading above, and it means a future
  per-net or reporting hook keyed on "is this segment governed" sees an
  ungoverned one. A shielded rule is unaffected (`shield_mode` keeps it
  active at any pitch).

Vehicle: [`flow/ndr_abs_um.buda`](../../flow/ndr_abs_um.buda) — its `vert_`
bus exists for exactly this: a governed bus routing VERTICALLY, so its
segment lands on the coarse pair and `fine3`'s width 3 costs 2 slots/bit on
M5 and 1 on M6. Without it the vehicle only ever placed governed metal where
the conservative maximum happened to be the right answer.

### R1's per-layer half — LANDED

`def_ndr_layer <rule> <layer> [width ...] [spacing ...]` overrides one
layer's values; layers with no entry inherit.  R1 always asked for
"per-layer or layer-independent values" and its cited precedent (LEF/DEF
`NONDEFAULTRULE`) is per-layer, but phase 1 shipped only the
layer-independent half — and per-layer was **not** among the declared
non-goals, so it was dropped rather than deferred.

It matters for exactly the rules the absolute form exists to serve: EM
limits, sheet resistance and RC all differ per layer, so one width applied
to the whole stack is over-wide on the top metal or under-wide at the
bottom.  Per-layer values apply under BOTH readings — the two questions are
orthogonal.

Residual: not yet persisted (BUDA-1911).

## 3. Per-net mixed-rule bundles (R8, the richer half)

R8 offers two positions. We ship the **fallback**: a bundle must be
rule-uniform, and the bundler splits a mixed bundle into rule-uniform parts,
loudly, in both the flat and hierarchical flows. The richer position — one
bundle carrying per-net rules, with each segment charging the group
conversion of *its* member bits — is unbuilt.

*Why deferred.* The split is correct and loud; the cost is that a bus whose
bits genuinely deserve different treatment routes as several bundles instead
of one, which can cost wirelength and a shared shield. Building the richer
form means `ndr_group_demand` takes a per-member rule list rather than one
spec, and every consumer follows — a real generalization of the load-bearing
arithmetic, worth doing only for a design that demonstrably needs it.

*Where to start:* `ndr_group_demand` / `ndr_run_layout` in `src/ndr.h`
(signature change to a member list), then the split pass in
`split_mixed_ndr_bundles` / `split_mixed_ndr_hbundles` becomes optional.

---

## Smaller residuals

### The cull-risk predictor fix is test-pinned (LANDED)

`_escalate_dead_low_segments(cull_risk=True)` excludes shield rows from its
placed-bit count (the R13 fix). It now has a repro and a test:
[`flow/ndr_cull_shield_count.buda`](../../flow/ndr_cull_shield_count.buda).

Reaching it needed more than the earlier note implied. The predicate does sit
behind `wmap` (no `hier.locked` bundles), but the binding constraint was one
step further out: `_final_cull_heal` returns at its `num_keepout_bits <= 0`
entry guard, and **every** other NDR vehicle is clean, so none of them got
within reach of the predicate at all. Measured, not inferred — instrumenting
the tier showed 0 of 11 vehicles entering the heal.

The shape needs a governed run seated LOW whose MEMBER bits are keepout-culled
while its SHIELDS survive, so the inflated count reads 4 of 4 against a truth
of 2 of 4. Three constructions were tried and rejected first, each for its own
reason, and they are recorded in the vehicle's header because they are the
non-obvious part:

- a rule layer MASK (`layers M3,M4`) seats the run LOW but removes the
  escalation TARGET — with no TOP in the allowed set the tier falls back to
  the highest allowed layer, which is the seat itself, and refuses;
- a `span_max` penalty on the TOP layer re-shapes the chosen TOPOLOGY, so the
  keepout no longer meets the run;
- `base_cost_non_top 0` does not seat a trunk LOW: a trunk prefers TOP
  whatever that penalty says.

The layer is therefore FORCED (`edit_set_layer` + `edit_commit pin`), which is
the honest instrument rather than a workaround: the vehicle is about what the
tier does with a given seat, not about which seat the planner picks.

A/B on the vehicle — with the shield exclusion removed: **4 net segments
placed, 2 bits unplaced, 2 violations, and no CULL-HEAL line**. With it: 6
placed, 0 unplaced, clean, `CULL-HEAL … opens 2->0`. See
[`ndr_architecture.md`](ndr_architecture.md) §7.5.

### The heals measure a governed seat in DEMAND (LANDED)

The census, `tools/doomed_seat_forensics.py` and now the HEALS all measure a
governed seat against `_seg_admission_need` — the Python mirror of
`bus_seg_min_demand`, which is what the engine admits on. Measuring in member
BITS meant a seat with room for the bits but not the
bits-plus-guards-plus-shields read as healthy, was never escalated, and
stranded in full at DNUTS.

Repro: [`flow/ndr_heal_demand_seat.buda`](../../flow/ndr_heal_demand_seat.buda)
— `shield bus` on 4 bits (demand 6) seated where the real pool is 5, so
`5 >= 4` looked fine and `5 < 6` is the truth. Before: **0 net segments
placed, 4 bits unplaced, 4 violations, detailed WL 0**. After: the re-seat
heal moves it (`opens 4->0`) and the design is clean.

*Three sites, not two.* The TOP re-seat pass has both a mover
(`_reseat_doomed_top_segments`) and a ranking helper
(`_final_reseat_heal._stranded_doomed_top`); they have to move together or
the ranking disagrees with the mover about which seats are doomed.

*Two units, kept distinct.* Each site mixes a BITS quantity (placed rows
against member bits — the cull predictor, the `miss` ranking) with a SLOT
quantity (pool against demand). They had been one variable. The pool is
still selected on the FULL demand and the doom test made on the CREDITED
minimum, which is the engine's own split (`detailed_nuts.cpp:470` vs `:497`).

*And the re-seat target resolves on the TARGET layer.* An R1 absolute rule
costs a different number of slots per layer, so asking "can layer L host
this" with the current layer's answer is the wrong question.

*The LOW→TOP escalation validates its target too.* It had always taken the
cheapest same-direction TOP unconditionally, which is sound under its own
premise — a dead LOW seat makes any TOP a better bet. Measuring the seat in
demand widened which segments reach it, and for a governed one the cheapest
TOP can be short as well, or an absolute rule can cost MORE slots there than
on the layer it left. It now searches cheapest-first for a target whose
bounded pool hosts the demand resolved on that layer, keeping the
unconditional choice as the fallback (refusing to move would forfeit the
escape) and gated on `ndr.active()` so the corpus-tuned ungoverned path stays
byte-identical.

*Found on the way:* both ranking helpers counted NDR shield rows as placed
bits, so a governed segment read as better placed than it is (4 bits + 2
shields as 6 of 4) and `miss` could go negative — the same shield-inflated
accounting as the bottom-up copy path (§7.5), here hiding stranded seats
from the heal meant to move them.

---

## Explicit non-goals (not planned)

From [`../NDR_REQUIREMENTS.md`](../NDR_REQUIREMENTS.md) §3 — listed so they are
not mistaken for oversights:

- **Via NDR** (enlarged / multi-cut vias) — vias are symbolic today; out of
  scope until via geometry is real.
- **DRC-true spacing tables** (width-dependent, parallel-run-length) — one
  spacing value per rule is the deliberate simplification.
- **Region-scoped NDR** (a rule active only inside a region) — the attachment
  model is by net name, not geometry.
- **`edit_shield_side`** (per-segment shield-placement override) — it would
  create stored shield *geometry decisions*, dragging shields into
  sidecar/BDB persistence, which is exactly the coupling the architecture
  avoids by materializing shields at DNUTS. Waits for a user who needs it.
