# Open items — non-default rules (NDR)

What the NDR feature deliberately does **not** do yet, and why each gap was
left rather than forgotten. The feature itself is documented for three
audiences — [`../NDR_REQUIREMENTS.md`](../NDR_REQUIREMENTS.md) (architects,
the R1–R13 contract), [`../NDR_UI.md`](../NDR_UI.md) (methodology, the
command/GUI surface), [`ndr_architecture.md`](ndr_architecture.md)
(implementers, as-built) — and the user-facing command reference is
[`../script_reference/ndr.md`](../script_reference/ndr.md).

Snapshot index — last verified against `main`: **2026-08-08**, after R13
(bottom-up composition) landed. Every requirement has a working implementation
and a vehicle, and the feature is usable end to end — but **R1, R6 and R8 are
met only in part**, by the decisions recorded below. What follows is that
residue.

**None of these is blocking.** Each is a bounded piece of work waiting for a
design that needs it, and the current behaviour in each case is conservative
(costs capacity, never produces an illegal route) or loudly reported.

---

## 1. Shield bonding vias (R6 — LANDED, with two residual limits)

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

**Residual limits:**

- **Every crossing is strapped.** There is no stride or via-count budget, so
  a long shield over a dense grid gets many straps (280 on the small
  `ndr_bond.buda` vehicle). That is the conservative direction — more grid
  ties, never fewer — but a real flow may want a declared stride.
- **Adjacent layers only.** A shield bonds to `layer ± 1` when that layer is
  perpendicular and has a grid. A rail two layers away needs a via stack,
  which needs the intermediate layer's own track reserved — a placement
  decision, not an output one, and out of scope here.

*Where to start on either:* `emit_shield_bond_vias` in `detailed_nuts.cpp`.

## 2. Absolute (µm) width and spacing values (R1 partial)

`def_ndr` accepts the multiplier form only (`width x2`, `spacing x1.5`). R1
allows absolute µm as well.

*Why deferred.* The multiplier form is **pattern-independent**: `x2` means
"two signal slots" on any layer, so one rule is portable across a stack whose
layers have different pitches. An absolute value has to be resolved against
each layer's slot geometry, which means per-layer quantization and a decision
about what to do when the value falls between slot counts. That is a real
design question, not a parsing one.

*Current behaviour:* an absolute value is a hard error naming the multiplier
form — no silent misinterpretation.

*Where to start:* `_parse_x` in `buda_cmds/ndr_cmds.py`, and the quantization
in `_spec_of` (which would become per-layer).

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

### Shield POSITION is only validated for `shield bus`

`check_design`'s `NDR_SHIELD` compares the placed shield **count** against the
rule's layout for every mode, and additionally checks that the two shields are
the run's outermost wires for `shield bus`. Under `shield bit` and
`shield per:N` a same-count shield sitting in the wrong gap is not caught.
(Raised in review of PR #636; the docs state the narrower guarantee rather
than implying a full arrangement audit.) Adding positional validation for
those modes is a contained change to `audit_ndr_dnuts` — walk the credited
layout and compare role-by-role against the sorted placed rows.

### The cull-risk predictor fix has no dedicated test

`_escalate_dead_low_segments(cull_risk=True)` now excludes shield rows from
its placed-bit count (the R13 fix). It has no test: the predicate sits behind
`wmap`, which excludes `hier.locked` bundles, so bottom-up instances never
reach it — observing it needs a governed NON-locked LOW segment whose seat is
starved yet still R3-realizable (a coarse pattern hard-errors at declaration
first). The sibling fix in the same commit *is* test-pinned. See
[`ndr_architecture.md`](ndr_architecture.md) §7.5.

### The doomed-seat census counts member bits, not demand units

`check_design`'s supply-doomed-seat census and `tools/doomed_seat_forensics.py`
count member bits for NDR segments rather than demand units. Harmless while
admission strands loudly — the census under-reports rather than misleads — but
it should adopt `bus_seg_demand` when NDR designs get big enough to census.

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
