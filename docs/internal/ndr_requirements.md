# NDR support — requirements (nets/buses/bundles with non-default width, spacing, shield constraints)

Status: **REQUIREMENTS — no design chosen, nothing built** (2026-08-06).
This document states what NDR support must do and what it must not break,
grounded in the as-built stage 8 (routing grid) and stage 9 (detailed NUTS)
model, plus the upstream stages that price width.  Solution *paths* are
sketched at the end only as brainstorm framing — choosing one is the next
conversation, not this document's job.

Tracked as [opens.md](opens.md) "Substantial features" item 15 and on the
[work menu](work_menu_2026-08-06.md).

## 1. What an NDR is here

A **non-default rule**: a named constraint set attachable to nets, buses, or
bundles, carrying any combination of

- **width** — the wire is wider than the default signal track width
  (typically an integer multiple, but the requirement is a value);
- **spacing** — the wire needs more clearance to its neighbors than the
  default track spacing provides;
- **shielding** — the wire (or the bus as a whole) must be flanked by
  grounded shield wires.

The industry precedent is LEF/DEF `NONDEFAULTRULE` (per-layer width/spacing
plus optional hard-spacing, applied per net); shielding in commercial flows
is usually a router directive rather than part of the LEF rule.  BUDA should
treat all three as one attachable rule object, because the *demand*
consequence is the same: an NDR bit consumes more perpendicular room than one
default signal track, and every stage that counts tracks must know it.

Typical users: clocks and strobes (shielded, often double-width), analog /
noise-sensitive nets (spacing, shielding), high-speed buses (spacing or
shield-per-pair), resistance-critical feedlines (width).

## 2. As-built baseline — the assumptions NDR breaks

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
  that **admission arithmetic is single-sourced**.
- **Bundling / hier**: bundler signatures ignore physical constraints;
  `align_bottom_up`'s phase criterion is over *pattern pitches* only (NDR
  changes per-net demand, not the pattern, so phase math should be
  untouched — requirement R13 pins this).

Everything below is stated against this baseline.

## 3. Requirements

### Declaration & attachment

- **R1 — Rule object.**  An NDR is declared once, by name, with per-layer
  or layer-independent values: width (absolute µm or × default), minimum
  spacing to any neighbor (absolute or ×), and a shield spec (none /
  flank-the-bus / flank-every-bit / shield-per-N-bits, with the shield
  net's identity, default GROUND).  A rule may specify any subset; absent
  fields mean default.
- **R2 — Attachment surface.**  Rules attach to (a) nets by longest-prefix
  match (the `set_bundling` / scoped `set_max_bundle_bits` precedent), (b)
  whole buses, (c) whole bundles, and — hier — (d) resolve identically for
  a template and all its replicas/instances (a template's NDR propagates to
  every expansion, like the scoped bit cap's occurrence-group resolution).
  Most-specific wins; conflicts inside one bundle are R8's problem.
- **R3 — Realizability validation, LOUD.**  Declaring a rule that cannot be
  realized on a declared layer's pattern (width exceeding what any
  slot-run/track can host, spacing no window can satisfy, shield spec with
  no shieldable resource) is a **hard error at declaration or first
  resolution**, naming the layer, the rule, and the arithmetic — the
  `def_track_pattern` duplicate/unknown-slot-type precedent (fail loud,
  never silently degrade to default).  A rule that is realizable on some
  layers restricts the NDR net to those layers, visible to the planner
  (R7), not discovered at DNUTS as a strand.

### Demand model (the load-bearing abstraction)

- **R4 — Track-demand units, defined at the GROUP level.**  Every stage
  that today counts "bits vs signal tracks" must count **demand units**,
  and the conversion's domain is a segment's **whole member-bit group plus
  its shield arrangement** — never an independently rounded per-bit cost.
  Shared resources make demand non-additive per bit: a flank-the-bus
  8-bit group needs TWO shields while two 4-bit groups need FOUR;
  shield-per-N amortizes across exactly the bits present; and interior
  spacing is shared between neighbors while the group's outer edges are
  not.  So the one shared function takes (rule, member-bit set, shield
  spec) and returns the group's total demand — a default bit alone costs
  1, and any per-bit figure is a derived VIEW of the group total, not an
  input to it.  Every operation that changes a segment's member-bit set
  (fan-in taper via `bit_list`, bundle splits, ripup re-pins) must
  re-invoke the group conversion, not add/subtract per-bit costs.
  `bus_seg_nbits`-as-track-demand, `eff_bus_width`, the planner's
  `signal_tracks` capacity mode, kPeak's supply floor, dead-span
  escalation, the doomed-seat census, and DNUTS admission must all consume
  the **same** conversion — one function, shared, like
  `count_signal_tracks_in_span` is today.  (This is the #536 lesson
  applied prospectively: if the census and the placer round differently —
  or amortize shared shields differently after a taper or split — we
  manufacture a new class of silent strands.)

### Routing grid (stage 8)

- **R5 — NDR-aware supply queries.**  The grid must answer, for a window
  and a rule: *how many wires of this rule fit* — not just how many SIGNAL
  centres exist.  Whatever the chosen realization (multi-slot consumption,
  wide-slot matching, or continuous packing — §5), the query must stay
  keepout/span-aware (the `signal_tracks_in_span` semantics), must have a
  no-allocation `count_` sibling for the planner hot path, and the vector
  and count views must be structurally lockstep (the
  `for_each_signal_track_in_span` single-walker pattern).
- **R5a — Pattern interplay: credit by NET IDENTITY, not adjacency.**  A
  pattern rail may satisfy a rule's shield requirement (a bit placed
  beside a static GND rail is already shielded on that side, and the model
  must be able to *credit* that rather than emitting a redundant shield
  wire) — but only a rail **electrically identical to the rule's requested
  shield net** qualifies: a POWER/VDD rail can never satisfy a GROUND
  shield spec, and even a `SHIELD`-typed slot counts only once its label
  resolves to the requested net.  `TrackSlot` carries only a type and a
  free-form label today, so crediting requires a label→net-identity
  resolution (compatible GROUND rails — e.g. `GND`/`VSS` aliases —
  qualify for the default GROUND spec); that resolution is part of the
  design, and R9's mis-connected-shield audit must apply the same
  predicate, so credit and audit cannot disagree.

### Detailed NUTS (stage 9)

- **R6 — Placement semantics.**
  - **Width**: an NDR bit's `NetSegment.width` comes from its rule; the
    occupied perpendicular extent (its footprint) blocks every track whose
    centre it covers, for both same-layer neighbors and the sharing
    exemption's bookkeeping.
  - **Spacing**: the rule's clearance is enforced against *everything* —
    other bits (any bundle), pattern rails, pre-routes, and fixed
    bottom-up copies.  A spacing violation is never emitted silently.
  - **Shielding**: emitted shield wires are first-class placed objects with
    net identity (the shield net), drawn from the same supply the demand
    model charged (R4), placed adjacent to what they shield, with vias/
    connectivity per the shield spec.  Whether they are `NetSegment`s with
    a shield flag or a new kind is a design choice; the requirement is
    that check_design, viz, persistence, and WL reporting all see them.
  - **Interplay**: `timing_critical` contiguity composes with NDR (a
    timing-critical NDR bus needs a contiguous *demand-unit* window);
    `bit_order` applies to the signal bits with shields interleaved per
    spec; the keepout cull applies to shield wires too; fan-in taper
    (`bit_list`) charges only member bits' demand.
  - **Admission stays all-or-nothing per segment** (matching today's
    contract), but the count it compares is demand units (R4), so a
    partial-supply NDR segment strands loudly at the same place default
    segments do — and shows up in the doomed-seat census identically.

### Upstream honesty (stages 3–4)

- **R7 — Priced where chosen, not discovered where placed.**  The planner
  must see NDR demand in band capacity (both width mode and
  `signal_tracks` mode), so an NDR bus that cannot fit a band overflows
  *at plan time* (STRICT ladder, replans, negotiate/ripup trials all
  comply — the layer-cap precedent: enforce inside the enumeration, not as
  a post-hoc audit).  Layer restrictions from R3 enter the planner's layer
  enumeration the way `allowed_layers`/cell bands do.  Abstract NUTS
  reserves the NDR footprint (`eff_bus_width` generalized per R4) so
  stage-4 packing and stage-9 reality agree.

### Bundling & hier

- **R8 — Mixed-rule bundles.**  Default position (to be confirmed in
  design): a bundle may carry per-net rules; a segment charges the R4
  GROUP conversion of its member-bit set (shared shields and interior
  spacing counted once at the group level — never a sum of
  independently rounded per-bit costs), and a bundle split must
  re-derive each part's group demand, since splitting a flanked group
  CHANGES the shield count (the split report should surface the demand
  delta).  If the design instead requires
  rule-uniform bundles, the bundler must *split* by rule class LOUDLY (the
  `set_max_bundle_bits` split-report precedent) — never silently drop or
  merge constraints.  Either way `set_bundling`-style scoping must keep an
  NDR net out of merges the user forbids.
- **R13 — Bottom-up composition.**  A template's NDR solves once and
  copies with the reference routing (shield wires included);
  `align_bottom_up`'s phase criterion is **unchanged** (pattern pitches
  only — NDR moves demand, not the grid), and `check_template_tracks`'s
  pool comparison compares NDR-aware pools (R5) so congruence stays
  honest.

### Verification, reporting, persistence

- **R9 — Audit.**  `check_design` gains NDR checks at nuts/dnuts stages:
  under-width wire, spacing violation (the `BIT_SHORT` machinery
  generalized from "same track" to "closer than the rule allows"), missing
  or mis-connected shield.  Report-only, typed violations, like every
  existing check.
- **R10 — Persistence.**  Rules and attachments persist in the BDB (schema
  bump; net-level attachment likely `net_props`, rule table by name);
  `open_bdb` restores them, `load_pipeline` re-resolves onto restored
  wrappers and VOIDS (LOUD) a restored plan a since-tightened rule outlaws
  — the `set_cell_layer_cap` v20 precedent, reused wholesale.
- **R11 — Viz & WL.**  Detailed viz draws NDR bits at their real width and
  shows shield wires (own toggle or the SHIELD type toggle);
  `report_wirelength` counts shield metal separately (it is real metal the
  design pays for, but not signal WL — keep the QoR triple comparable).
- **R12 — No-NDR byte-identity.**  A design declaring no rule is
  byte-identical on the full corpus (the standing guard for every opt-in:
  band_span_charge, caps, shares, prune/dedup all held this bar).  This is
  the acceptance gate for landing any phase.

## 4. Non-goals (first phase)

- **Via NDR** (enlarged/multi-cut vias) — vias are symbolic squares today;
  out of scope until via geometry is real.
- **DRC-true spacing tables** (width-dependent, parallel-run-length) — one
  clearance value per rule per layer is the phase-1 model.
- **Region-scoped NDR** (rule active only inside a rect) — patterns have
  region overrides; rules do not, phase 1.
- **Tapering** (dropping the rule partway along a route) — a net has one
  rule everywhere it routes.
- **Auto-derivation** (inferring NDR from net names/timing) — declaration
  is explicit.

## 5. Solution-path sketches — brainstorm framing only

Four candidate realizations of R4/R5/R6, each with its obvious tension;
none chosen here:

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

The brainstorm should weigh: which path keeps the R4 single-sourced
arithmetic simplest; which keeps R12 byte-identity trivially true; and which
degrades most gracefully when a rule is only *partially* realizable on a
layer.

## 6. Test vehicles & acceptance

- A small flat vehicle (comprehensive_demo-scale) with one shielded
  double-width clock bus among default buses — the demo of all three
  constraint kinds.
- A congested vehicle (bigHalf/mix2 class) where NDR demand forces planner
  overflow — proving R7 (priced at plan time, not stranded at DNUTS).
- A bottom-up vehicle with an NDR bus inside a template — proving R13
  (copies carry shields; pools compare NDR-aware).
- Corpus: **zero rules declared = byte-identical** (R12), the landing gate
  for every phase.
