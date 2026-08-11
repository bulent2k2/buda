# Open items — non-default rules (NDR)

What the NDR feature deliberately does **not** do yet, and why each gap was
left rather than forgotten. The feature itself is documented for three
audiences — [`../NDR_REQUIREMENTS.md`](../NDR_REQUIREMENTS.md) (architects,
the R1–R13 contract), [`../NDR_UI.md`](../NDR_UI.md) (methodology, the
command/GUI surface), [`ndr_architecture.md`](ndr_architecture.md)
(implementers, as-built) — and the user-facing command reference is
[`../script_reference/ndr.md`](../script_reference/ndr.md).

Snapshot index — last verified against `main`: **2026-08-10**, after R1
(absolute width/spacing) landed. Every requirement has a working
implementation and a vehicle, and the feature is usable end to end — **R8 is
the one still met only in part** (the rule-uniform fallback), and R1 and R6
each carry a residual limit below now that their headline gaps are closed.
What follows is that residue.

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

**Residual limit:**

- **Consumers charge the conservative maximum.** The routing stages
  (`congestion_planner.cpp`, `nuts.cpp`, `detailed_nuts.cpp`) price an
  absolute rule at the max over its governed layers rather than resolving
  per layer, so a coarse layer is over-charged (a 3-unit wire at pitch 5
  costs `2 x 5 = 10` units instead of ~3). Safe in direction — over-charge,
  never under — but it is capacity a design does not owe. R3 realizability
  and the declaration reporting DO resolve per layer.

Vehicle: [`flow/ndr_abs_um.buda`](../../flow/ndr_abs_um.buda).

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

### The cull-risk predictor fix has no dedicated test

`_escalate_dead_low_segments(cull_risk=True)` now excludes shield rows from
its placed-bit count (the R13 fix). It has no test: the predicate sits behind
`wmap`, which excludes `hier.locked` bundles, so bottom-up instances never
reach it — observing it needs a governed NON-locked LOW segment whose seat is
starved yet still R3-realizable (a coarse pattern hard-errors at declaration
first). The sibling fix in the same commit *is* test-pinned. See
[`ndr_architecture.md`](ndr_architecture.md) §7.5.

### The dead-span and re-seat HEALS still measure need in member bits

The doomed-seat census and `tools/doomed_seat_forensics.py` now measure a
governed seat against `_seg_admission_need` — the Python mirror of
`bus_seg_min_demand`, which is what the engine actually admits on. The two
HEALS that share the same seat arithmetic (`_escalate_dead_low_segments` and
the TOP re-seat pass in `nutsflow.py`) still compute `need` as
`_seg_member_bits`, so a governed segment whose seat can host its bits but
not its bits-plus-guards-plus-shields is not escalated and strands at DNUTS.

*Why it was left out of the census fix.* The census is report-only; the heals
change layer assignment, so the fix belongs with its own QoR measurement
rather than folded into a diagnostic change. It is corpus-neutral by
construction — no corpus flow declares a rule, and the NDR conversion is the
identity on an inactive spec — but it would move governed vehicles, which is
exactly what wants measuring on its own.

*Where to start:* swap the two `need = self._seg_member_bits(...)` sites in
`_escalate_dead_low_segments` / the re-seat heal for `_seg_admission_need`,
then measure the NDR vehicles.

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
