# NDR Support — Requirements

**Audience: chip architects.**  This document states what BUDA's non-default
rule (NDR) support will do, what it guarantees, and what is out of scope —
in design terms, for review and feedback.  Two companion documents cover the
other audiences: [NDR_UI.md](NDR_UI.md) (command/GUI surface options, for
design methodology) and
[internal/ndr_architecture.md](internal/ndr_architecture.md) (implementation
architecture).  Status: **requirements under review — no design frozen,
nothing built** (2026-08-06).

## 1. What an NDR is

A **non-default rule**: a named constraint set attachable to nets, buses, or
bundles, carrying any combination of

- **width** — the wire is wider than the default signal track width
  (typically an integer multiple, but any value may be requested);
- **spacing** — the wire needs more clearance to its neighbors than the
  default track spacing provides;
- **shielding** — the wire (or the bus as a whole) must be flanked by
  grounded shield wires.

The industry precedent is LEF/DEF `NONDEFAULTRULE` (per-layer width/spacing,
applied per net); shielding in commercial flows is usually a router
directive rather than part of the LEF rule.  BUDA treats all three as one
attachable rule object because the *resource* consequence is the same: an
NDR wire consumes more routing room than one default signal track, and every
planning stage that counts tracks must know it.

Typical users: clocks and strobes (shielded, often double-width), analog /
noise-sensitive nets (spacing, shielding), high-speed buses (spacing or
shield-per-pair), resistance-critical feedlines (width).

## 2. Requirements

### Declaration & attachment

- **R1 — Rule object.**  An NDR is declared once, by name, with per-layer
  or layer-independent values: width (absolute µm or × default), minimum
  spacing to any neighbor (absolute or ×), and a shield spec (none /
  flank-the-bus / flank-every-bit / shield-per-N-bits, with the shield
  net's identity, default GROUND).  A rule may specify any subset; absent
  fields mean default behavior.
- **R2 — Attachment.**  Rules attach to (a) nets by name prefix (longest
  prefix wins), (b) whole buses, (c) whole bundles, and — in hierarchical
  designs — (d) resolve identically for a cell template and every instance
  of it, so a rule declared once applies uniformly to all occurrences.
  Most-specific attachment wins.  *Status:* (a)/(b) and (d) landed —
  the hier bundler splits template classes into rule-uniform parts before
  per-instance expansion and hard-errors on a scope that governs a class
  inconsistently across its occurrences (a template is solved once and
  copied, so every occurrence must resolve to the same rules on the same
  bits).
- **R3 — Realizability is validated LOUDLY, up front.**  Declaring a rule
  that cannot be realized on a layer's track pattern (a width no track
  arrangement can host, a spacing no window can satisfy, a shield spec
  with no shieldable resource) is a **hard error at declaration or first
  resolution**, naming the layer, the rule, and the arithmetic.  The tool
  never silently degrades an NDR to default.  A rule realizable on only
  some layers restricts the net to those layers — and the restriction is
  known to the planner up front (R7), not discovered as a failed route at
  the end.

### The demand model (the load-bearing abstraction)

- **R4 — Track demand is computed at the GROUP level, by one shared
  conversion.**  Every stage that counts "bits versus available tracks"
  counts **demand units**, and the conversion's input is a whole routed
  group (its member bits plus its shield arrangement) — never an
  independently rounded per-bit cost.  Shared resources make demand
  non-additive per bit: a flank-the-bus 8-bit group needs TWO shields,
  while the same bus split into two 4-bit groups needs FOUR; interior
  spacing is shared between neighbors while the group's outer edges are
  not.  Any per-bit figure is a derived view of the group total, and any
  operation that changes a group's membership (tapering, bundle splits,
  re-routing) recomputes the group demand.  Critically, **every consumer —
  the global planner's capacity model, the congestion estimators, the
  feasibility censuses, and the final track-assignment admission — uses
  the same conversion function**, so no two stages can ever disagree about
  whether a group fits.  (This is a lesson already learned in BUDA once:
  when two stages round differently, designs strand silently.)

### Supply & placement

- **R5 — Rule-aware supply queries.**  The routing grid answers, for any
  window and rule: *how many wires of this rule fit here* — not merely how
  many default tracks exist.  Supply queries remain aware of keepouts and
  of the full span a wire will occupy.
- **R5a — Existing power-grid rails may be credited as shields — by NET
  IDENTITY, never by mere adjacency.**  A wire placed beside a static rail
  that is electrically identical to the rule's requested shield net is
  already shielded on that side, and the tool may credit that instead of
  emitting a redundant shield wire.  A POWER/VDD rail can never satisfy a
  GROUND shield spec; a shield-typed rail counts only once its identity
  resolves to the requested net (GND/VSS aliases qualify for the default
  GROUND spec).  The audit (R9) applies the same identity test, so credit
  and audit cannot disagree.  *Phasing:* crediting may land after an
  initial always-emit phase — deferring it is conservative (redundant
  shields cost extra capacity but are never illegal) — but R5a is
  mandatory for the feature to be complete.  *Status:* landed as the
  opt-in `credit` token on `def_ndr` — end shields credit against an
  immediately adjacent identity-matching rail whose metal spans the run;
  a rule without the token always emits.
- **R6 — Placement semantics.**  An NDR wire is placed at its true width;
  its footprint blocks every track it covers.  Spacing is enforced against
  everything — other signals, power-grid rails, pre-routes, and fixed
  routing from lower hierarchy levels — and a spacing violation is never
  silent.  Emitted shield wires are first-class routed objects with net
  identity, visible to verification, visualization, reporting, and the
  saved design.  Existing per-bus properties (timing-critical contiguity,
  bit ordering) compose with NDR.  Admission remains all-or-nothing per
  routed segment: a group that cannot be fully placed strands loudly, in
  the same reports that catch default-rule shortfalls today.

### Planning honesty

- **R7 — Priced where chosen, not discovered where placed.**  The global
  planner sees NDR demand in its capacity model, so an NDR bus that cannot
  fit a region shows up as planner overflow *at planning time* — in the
  main flow and in every re-plan and repair pass — rather than as a failed
  detailed route at the end.  Layer restrictions from R3 constrain the
  planner's layer choices the same way existing layer policies do.  The
  abstract (bus-level) placement stage reserves the NDR footprint, so
  bus-level packing and bit-level reality agree.

### Bundling & hierarchy

- **R8 — Mixed-rule bundles.**  Default position (to be confirmed in
  design): a bundle may carry per-net rules, and each routed segment
  charges the R4 group conversion of its member bits — shared shields and
  interior spacing counted once at the group level.  A bundle split
  re-derives each part's group demand (splitting a flanked group *changes
  the shield count*), and the split report surfaces the demand change.
  If the design instead requires rule-uniform bundles, the bundler splits
  by rule class loudly — constraints are never silently dropped or merged.
- **R13 — Bottom-up composition.**  A cell template's NDR interconnect is
  solved once and copied to every instance, shield wires included.  The
  instance-alignment machinery is unchanged — NDR moves demand, not the
  track grid — and template-vs-instance verification compares rule-aware
  track supplies, so uniformity claims stay honest.

### Verification, reporting, persistence

- **R9 — Audit.**  The design checker gains NDR checks: under-width wire,
  spacing violation, missing or mis-connected shield.  Report-only, typed
  violations, consistent with every existing check.
- **R10 — Persistence.**  Rules and attachments persist with the design
  database; reopening a saved design restores them, and a saved routing
  plan that a since-tightened rule now outlaws is voided LOUDLY (re-plan
  required) rather than silently kept.
- **R11 — Visualization & reporting.**  Detailed visualization draws NDR
  wires at their real width and shows shield wires.  Wirelength reporting
  counts shield metal separately — it is real metal the design pays for,
  but not signal wirelength, so quality metrics stay comparable across
  designs with and without NDRs.
- **R12 — No-NDR designs are untouched.**  A design declaring no rule
  routes **byte-identically** to today, verified across the full
  regression corpus.  This is the acceptance gate for every landing phase.

## 3. Non-goals (first phase)

- **Via NDR** (enlarged / multi-cut vias) — vias are symbolic today; out of
  scope until via geometry is real.
- **DRC-true spacing tables** (width-dependent, parallel-run-length) — one
  clearance value per rule per layer is the phase-1 model.
- **Region-scoped NDR** (a rule active only inside a region) — not in
  phase 1.
- **Tapering** (dropping the rule partway along a route) — a net has one
  rule everywhere it routes.
- **Auto-derivation** (inferring NDR from net names or timing) —
  declaration is explicit.

## 4. Acceptance & demonstration vehicles

- A small flat design with one shielded double-width clock bus among
  default buses — demonstrating all three constraint kinds end to end.
- A congested design where NDR demand forces planner overflow — proving R7
  (priced at plan time, not stranded at detailed routing).
- A hierarchical bottom-up design with an NDR bus inside a cell template —
  proving R13 (copies carry shields; supplies compare rule-aware).
- The full corpus with **zero rules declared = byte-identical** (R12).

## 5. Feedback wanted

From architecture review, the questions that most shape the design:

1. Are width/spacing/shield the right phase-1 constraint set, or does your
   methodology need any phase-2 item (via rules, region scoping, tapering)
   promoted?
2. Is quantizing width/spacing up to whole track pitches acceptable
   (conservative, never illegal — a 1.5× wire pays 2 tracks), or do you
   have layers where exact sub-pitch widths matter enough to justify the
   added complexity?
3. Shield-per-N-bits: what N values do your flows actually use, and is
   flank-the-bus (N = bus width) the common case?
