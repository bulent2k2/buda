# NDR Support — UI Options (CLI, script commands, GUI)

**Audience: design methodology.**  This document lays out the user-level
surface options for NDR support — new `.buda` commands, topology-editor and
layer-assignment behavior, and what persists where — for methodology review.
Requirements live in [NDR_REQUIREMENTS.md](NDR_REQUIREMENTS.md) (R-numbers
below refer to it); the implementation architecture, including the internal
persistence design, lives in
[internal/ndr_architecture.md](internal/ndr_architecture.md).  Status:
**options under review — nothing frozen** (2026-08-06).

## How persistence frames every option below

BUDA has two persistence worlds, and the UI options inherit their split:

- **Flat flow**: *declarations* persist in the `.buda` script itself
  (re-executed every run, like `def_layer` / `def_track_pattern`);
  *decisions* (topology selections, pins, pinned layers, USER candidates)
  persist in the per-flow JSON **sidecar**, keyed by bundle hint (first net
  name) + candidate content identity (`topo_uid`).
- **Hier flow**: the **BDB** holds both — policy tables restored by
  `open_bdb`, decisions re-resolved by `load_pipeline`, with the
  established VOID-on-tightening semantics (a restored plan that a
  since-tightened policy outlaws is voided LOUDLY, re-plan required).

The single most consequential internal decision for the UI is that **NDR
must not change candidate topology geometry** (shields materialize at
detailed routing, not as topology segments — see the architecture doc).
That is what keeps `topo_uid` stable, which means: **every existing sidecar
selection, script `select_topology` pin, and edit-session provenance record
survives attaching, editing, or removing a rule.**  No sidecar format
change, no pin remapping, no re-baselining.

## Decision 1 — Rule declaration command

| Option | Shape | Assessment |
|---|---|---|
| **1a. One declaration command** (recommended) | `def_ndr <name> [width <w\|xN>] [spacing <s\|xN>] [shield bus\|bit\|per:<N> [net <GND\|name>]] [credit] [layers <list>]` | Mirrors `def_layer`/`def_track_pattern`: declare-once, duplicate name = hard error, unknown token = hard error.  R3 realizability fires here — the command prints the rule's effective layer set or hard-errors with the arithmetic.  `credit` (built, opt-in) lets an END shield be satisfied by an adjacent identity-matching power rail instead of an emitted wire (R5a); it requires a shield arrangement. |
| 1b. Property-style increments | `ndr <name> width 2x`, then `ndr <name> spacing 2x`, … | Composable, but violates the declare-once-LOUD convention; a half-declared rule is a silent hazard. |
| 1c. Pattern-embedded | NDR classes declared inside `def_track_pattern` | Only meaningful under the pattern-declared solution path, which the architecture assessment relegates to an optimization ingredient. |

## Decision 2 — Attachment surface

Not mutually exclusive; the question is what ships first.

| Option | Shape | Precedent | Persists in |
|---|---|---|---|
| **2a. Net-prefix scope** (ships first) | `set_ndr <prefix>\|* <rule\|off>` | `set_bundling`, scoped `set_max_bundle_bits` — longest prefix wins, `*` = global default | Script (flat) / BDB attachment table (hier) |
| 2b. Bundle-selector override | `set_bundle_ndr <sel> <rule>` using `select_topology`'s selector grammar (`id:N`, `net:PFX`, hints) | `select_topology` | A *decision* — sidecar-adjacent (flat) or bundle column (BDB) |
| 2c. Cell-scoped (hier) | `set_cell_ndr <cell> <prefix> <rule>` | `set_cell_layer_cap` owning-frame rule | BDB policy table |

**Why 2a must come first**: attachment must precede bundling — the bundler
may split by rule class (R8) and the demand model needs rules resolved
before `run_bundler` / `run_hier_bundler`.  A bundle-level attachment is
inherently post-bundling, so it can only *refine*, never *define*.  2c
falls out of 2a for most needs, since prefixes resolve per net and template
attachment propagates to every instance (R2d); it stays phase-2 unless a
per-cell exception surfaces early.

## Decision 3 — Topology editor & layer assignment

NDR is per-net, so the editor gains **awareness, not new geometry
commands**:

- **`edit_set_layer <seg#> <layer_id>`** validates against the rule's
  effective layer set and prints the demand arithmetic in the per-edit
  verdict line — **refusing** a layer outside the effective set (physical
  unrealizability, R3) and **warn-and-allowing** an in-set layer whose
  supply is merely tight (economic override); the two-case split is
  argued in open question 1 below.
- **`edit_status` / `dump_topologies --conn`** show per-segment demand
  (`ndr=clk2x demand=12u/8bits`), the way `dump_hbundles` shows
  `band=[..] shares={..}`.
- **`dump_ndr`** — rules, effective layer sets, attachment scopes, and
  which bundles each rule currently governs.
- **Explorer GUI**: an NDR badge in the bundle header; a per-candidate
  realizability tint in the debug cost view (a candidate whose layers
  cannot host the rule's demand renders flagged); and a **shield ghost
  overlay drawn from the rule**, not from stored geometry — so the overlay
  is always consistent with the declaration and nothing new persists.
- **Detailed viz**: width-proportional bit wires; shield wires under the
  existing SHIELD type toggle (R11).
- **Deliberately deferred**: `edit_shield_side <seg#> <both|lo|hi|none>`
  (per-segment shield-placement override).  It would create stored shield
  *geometry decisions*, dragging shields into sidecar/BDB persistence —
  exactly the coupling the architecture avoids.  Wait for a user who needs
  it.

## The phase-1 strawman — PROTOTYPED (2026-08-06)

The smallest surface that exercises the whole pipe — now built exactly in
this shape (as-built record + prototype limitations in
[internal/ndr_architecture.md](internal/ndr_architecture.md) §7; demo
vehicle `flow/ndr_demo.buda`; the multiplier-only value form and the R3
check firing at `run_detailed_nuts` are the two as-built specifics worth
knowing before methodology review):

```
def_ndr  clk2x  width 2x spacing 2x shield bus net GND
set_ndr  clk_   clk2x
dump_ndr                      # rules + effective layer sets + scopes
check_design                  # gains the R9 NDR advisory
```

- Shields materialize at detailed routing (architecture decision) — so the
  sidecar is untouched and all pins survive rule edits.
- BDB gains rule + scope tables on the established v20 policy-table
  template (restore on `open_bdb`, re-resolve + VOID-on-tightening on
  `load_pipeline`).
- Explorer gains badge + realizability tint + rule-derived ghost shields
  (read-only).  TopoEdit gains only the `edit_set_layer` validation
  verdict.
- Everything in 2b / 2c / `edit_shield_side` waits for a demonstrated
  need.

## Open questions for methodology feedback

1. **`edit_set_layer` validation is TWO cases, not one** (sharpened in
   review): a layer OUTSIDE the rule's effective layer set (R3) is
   *physically unrealizable* — the rule cannot exist there, so the edit is
   **refused** with the arithmetic (unlike a layer-cap exception, which is
   an economic policy override, this pin could only strand at detailed
   placement or silently violate the rule — there is no valid expert
   override to preserve).  A layer IN the set whose current window/supply
   is merely *tight* is an economic judgment — there the edit is
   **warn-and-allow** with the LOUD pinned-exception framing
   `check_design` already uses for layer-cap overrides: the expert keeps
   the last word on economics, physics keeps the last word on
   realizability.  Remaining question for methodology: does the refusal
   need an escape hatch that stores the edit as explicitly-invalid,
   unplannable state (visible, never planned), or is refusal-with-report
   enough?
2. **Is prefix scoping (2a) enough for phase 1**, or do your flows need
   the bundle-level override (2b) from day one?
3. **Should abstract (bus-level) viz show the widened NDR footprint?**  A
   cheap visibility half-step — the abstract view would show honest
   reserved width without shields existing at that stage.
