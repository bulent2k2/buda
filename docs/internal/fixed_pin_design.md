# The fixed-pin primitive: `fix_pin`

*Design proposal, 2026-09-20 — convergence-ladder build item 7.  Not built.
Revised the same day after a review round (see *Corrections after review*).
Published for outside review as the [Fixed Pin Primitive](https://claude.ai/artifact/T75FBqiYjTEax8A5Q5jdLN) page; this
file is its twin in the tree.  The open questions at the end are the ones a
reader with a real pin-assignment flow can answer; the pushbacks are the
places where the first sketch was argued down, kept so the argument is not
re-had.*

## Why now

The [ladder](convergence_ladder.md) records that BUDA has no fixed pin: a
busterm's bbox is the whole component, `PORT` is a label, and
`add_cell_pin`'s `px py` is consumed by nothing in the routing frame.  Every
bus lands wherever the generator likes on the face.  Item 7 wants "a busterm
restricted to a face, then to a window on a face", and the owner's question
(2026-09-20) widened it into a **ladder of constraint strength**: orientation
only; a face direction; a precise face in local coordinates on a
non-rectangular block; a layer; a range along the face; and relative bit
ordering for a bus, with `index` meaning low bits at low coordinates.  The
ask was a command general enough to carry all of that and easy to use for
the simple end.

## What the engine already has (read before designing)

Three facts, each checked in the source, decide the shape of the design.

1. **A pin is a landing on a busterm face, and the face is derived, not
   declared.**  `generate_2pin` lands a stub on the face nearest the partner
   block (`Rect::face_x/face_y(toward)`), at the partner's centre projected
   onto that face, and NUTS then slides it along the face within the
   busterm-face membership window.  `annotate_endpoints` recognises a landing
   geometrically: an endpoint on a rect face, preferring one that abuts from
   outside.
2. **A fixed pin already exists — by representation.**  A DEF die port
   becomes a component whose bbox is the `PORT` rect (`is_port`, v23), and
   generation, NUTS, DNUTS and the audit route to it with no special case.  A
   fixed pin is nothing more than a busterm whose rect is a thin strip on one
   face.
3. **Multi-rect landings are built and audited.**  A block may carry several
   rects; `best_rect` picks the one nearest the spine, taps are per rect, and
   `TEG_OPEN` audits the result under `over`.  A set of admissible face strips
   *is* a multi-rect busterm under `thru` (the strips are joined by the
   block's own metal, which is exactly what `thru` asserts).

So the mechanism is not new generator logic.  A `fix_pin` **rewrites the
bundle's busterm for that block into its admissible strips, each strip
tagged with the ONE edge that is its face**.  The strips reach the generator
at the one place each bundle's busterm is built (`mk_bt`, in both the 2-pin
and n-pin generators), where face choice, per-rect taps and NUTS's slide
window read them unchanged.  Two things do NOT come for free, and the first
cut of this note claimed they did (Codex on #944):

- **A plain `Rect` has four edges.**  `annotate_endpoints` accepts a landing
  on any edge of any busterm rect (`on_face_rect` / `abuts_rect`), so an
  east strip would also admit a stub ending on its INNER edge or on its
  one-unit north and south ends — a vertical stub could satisfy `H`.  The
  face TAG is what those two predicates, and their twin in the audit,
  consult: a landing counts only on the tagged edge.
- **Two consumers read the floorplan, not the busterm.**  The pass-through
  clamp (`tighten_passthrough`, `topology_analysis.cpp`) and the audit
  (`verify.cpp`, a dozen `get_block_rects` sites) fetch a block's rects from
  the `Floorplan`, so a strip installed at `mk_bt` alone would let a trunk
  generated through a point pin slide anywhere across the physical block and
  still pass `BUSTERM_OPEN` — the opposite of "a point pin admits no
  pass-through".  The admissible geometry is therefore SINGLE-SOURCED:
  `Floorplan::landing_rects(block, pin-set)` — the tagged strips, or the
  block's own rects with every edge a face when nothing is fixed — called by
  `mk_bt`, the pass-through clamp and every audit site in place of
  `get_block_rects`, the one-function-three-callers rule the TEG contact
  predicate already follows.

A design with no fix is byte-identical, because the resolver then returns
exactly what `get_block_rects` returns today.

The alternative — an admissibility *predicate* consulted by the generator and
the gate, with the busterm left whole — was considered and is more code for
the same answer: the generator would have to learn to CHOOSE faces, the gate
to test them, and NUTS to narrow windows.  What survives of it is the face
tag: which edge of a strip is the face is a fact a rectangle cannot carry,
and it lives in landing RECOGNITION (the annotator, the clamp, the audit),
not in face choice.

## The proposal

One object with six knobs, each defaulting to "anything".  A fix is the SET
OF LANDINGS it still admits; tighten by adding tokens.

```
fix_pin <target> [north|south|east|west|H|V ...] [rect <n>]
                 [at <lo>..<hi> | at <pos>] [layer <L>[,<L>...]]
                 [order index|rindex]
fix_pin <target> off
fix_pin <target> from_pins        # consume the LEF pin the BDB already holds
dump_pin_fixes
```

**Target.**  Names a pin the way `add_net` does: `<owner>.<pin>`.  An owner
that is a **cell** is a template and governs every instance, transformed by
each instance's orientation; an owner that is an **instance path** or a
**flat block** governs one occurrence.  `cell:` / `inst:` disambiguate only
when a name is both.  The pin part is a pin name, a **bus name** (every bit,
through `bus_names.py`, so declared `d_in_3` and imported `d_in[3]` are one
bus), a glob (`d_*`), or `*`.

**The six levels, in that spelling:**

| level | line | admits |
|---|---|---|
| 1 orientation | `fix_pin core_cell.* H` | horizontal stubs: the east or west faces; a horizontal pass-through |
| 2 face direction | `fix_pin core_cell.d_in east` | every exposed east-facing face of the shape |
| 3 precise face | `fix_pin core_cell.d_in rect 2 east` | the east face of the 2nd declared rect, local coordinates |
| 4 layer | `fix_pin core_cell.d_in east layer M5` | that face, the landing stub on M5 |
| 5 range | `fix_pin core_cell.d_in east at 20..60` | a window along the face, local coordinates |
| 6 order | `fix_pin core_cell.d_in east at 20..60 order index` | bit 0 at the lowest coordinate in the window |
| strongest | `fix_pin core_cell.d_in[3] east at 18 layer M5` | one bit, one point, one layer — a LEF pin |

### What each knob becomes in the pipeline

- **Faces, `rect`, `at`** become the strips.  `H` is sugar for `east west`,
  `V` for `north south`.  With no face token every exposed face of the shape
  is admissible, so `rect 2 east` is one face and a bare `east` is every
  east face; "exposed" excludes a rect edge interior to the union (the
  rectilinear L/C case).  The window is the strip's extent along the face —
  so NUTS's slide window falls out of busterm-face membership with no new
  plumbing, and a single `at <pos>` is a strip one track pitch wide.  A strip
  has a small inward thickness (one unit, or one pitch on the landing layer)
  because `Rect` is integer and a zero-area rect is refused everywhere — and
  its face tag says which of its four edges the wire must END on; the inner
  edge and the two short ends are not faces.
- **Pass-through is automatic.**  A trunk crossing the block is a landing
  today when it crosses the busterm rect; with strips it is a landing when it
  crosses a strip.  So an `H` fix admits a horizontal pass-through and a
  point pin admits none.  Nothing to declare — provided the pass-through
  CLAMP and the audit read the strips too, which today they do not (above);
  that is the single-sourced resolver's job.
- **`layer`** names the APPROACH segment's layer(s) — the stub that lands —
  and must be direction-compatible with the face: an east or west face is
  reached by a horizontal stub, so it takes H layers; a north or south face,
  V layers; a mismatch is a hard error at declaration, since the engine
  requires a segment's direction to match its layer's (`LAYER_DIR`).  A
  pin's own METAL layer is a different thing (Codex on #944): a LEF pin
  drawn on M2, a vertical layer, on an east face is reached by an H wire on
  M1 or M3 plus a via, not by an H wire on M2.  So `from_pins` records the
  pin's metal layer and DERIVES the approach layer — the pin layer itself
  when the directions agree, else the adjacent compatible layer — and DNUTS
  emits one ENDPOINT VIA per bit between the two, riding the `net_via` row
  with a negative `to_seg` that the NDR shield bond straps already use for
  "the far end is not a segment".  The planner enforces the approach set the
  way it enforces `pinned_seg_layers` and `allowed_layers`, inside the STRICT
  enumeration, so a fixed layer steers the choice rather than the ladder
  escalating past it.
- **`order`** is a bit permutation per LANDING, not per segment (Codex on
  #944).  `BusSegment::bit_order` is one value for a whole segment — today
  `run_detailed_nuts [lo_hi|hi_lo]` sets it for the whole run — and a
  straight segment with a fixed pin at each end (an `I` shape, or a
  pass-through trunk touching several fixed pins) has several landings.  A
  straight wire cannot reverse its bits, so two landings on ONE segment must
  agree and a disagreement is refused loudly at declaration; across a BEND
  they may differ, since the per-bit via crossing simply mirrors, so an L
  with `index` at one end and `rindex` at the other is legal and each leg
  takes its landing's order.  `index` sorts the bundle's nets by (bus name,
  bit index) then scalars, and lays bits out low to high along the strip;
  `rindex` reverses.  A per-bit `at` becomes a per-bit track TARGET in
  DNUTS's stub placement (`bit_targets`), the one genuinely new piece in that
  stage.
- **Templates transform.**  A cell fix is stored cell-local and transformed
  per instance by `orient_map` — the path a v30 `cell_rect` footprint already
  takes into every projection.  A window rotates with its face, `H` becomes
  `V` under a quarter turn, and the order direction travels with the strip so
  a mirrored instance keeps bit 0 at the right end.  A `set_bottom_up`
  template solved once and copied is consistent by construction, since the
  fix is a property of the cell.
- **Precedence** follows `set_feedthru`: instance beats cell, a named pin
  beats its bus beats `*`, a later line on the same key replaces.  Declare
  BEFORE bundling, like `set_ndr` (the bundler may split a bundle whose bits
  carry different fixes — open question Q2).
- **Failure is loud and never strands.**  A bundle with no candidate honouring
  its fix keeps its best candidate and reports at generation (a new
  BUDA-192x WARNING naming the pin and the fix), the way `filter_uncovered`
  already keeps an all-broken pool with a WARNING; `check_design` gains a
  `PIN_FIX` kind (defense in depth: a landing off its admissible set, or on a
  layer outside its set).  Refusing would strand the bus; silence would be
  worse.
- **Persistence.**  A v31 `pin_fix` table (owner kind, owner, pin selector,
  faces, rect index, lo, hi, layers CSV, order), written through by the
  command, restored by `open_bdb` (session-declared wins, as every other
  policy), and in the planner's persist fingerprint since a layer fix
  reprices a plan.  The Tcl front end gets `buda::fix_pin` from the registry
  for free; `dump_pin_fixes` prints the inventory the way `dump_pins` and
  `dump_ndr` do.

### How the ladder uses it

Rung 1 (pin **sides**) is level 1 or 2.  Rung 2 (pin **positions**) is level
5 or `from_pins`.  The E3 partner evaluation is `fix_pin cell:* from_pins`
against the partner's LEF, which is why that verb exists: `import_def_lef`
already reads each LEF `PIN` into `cell_pin.px/py`, and this is the command
that finally consumes it.

## Pushbacks (kept, so they are not re-argued)

1. **Orientation is not its own dimension.**  `H` is two faces.  A separate
   concept would be a second code path for the same admissible set.
2. **"Which way the wire leaves" is not a dimension either.**  A face names
   the METAL a wire attaches to.  On a LOW layer the leaf footprint is a
   keepout, so an east pin can only be approached from the east.  On a TOP
   layer a wire may arrive over the cell from the west and END on the east
   strip's tagged face, which is physically legitimate for a macro whose
   `OBS` allows it (the strip's inner edge is not a face, so the wire has to
   reach the pin metal's outer edge).  Wanting outward departure is a
   statement about the layer; face plus layer already expresses it.
3. **Relative order ACROSS buses should be windows, not order lists.**  A
   partner hands over positions, never orderings.  Give bus A `at 0..100` and
   bus B `at 100..200` and the order is implied, per instance, with no new
   solver constraint.  Within a bus, `index` and `rindex` are the two
   anybody has needed; an explicit permutation over an arbitrary net list is
   buildable later on the same DNUTS hook if a real design asks, and is not
   in the first cut.
4. **Local coordinates for flat blocks too.**  `add_block` is absolute, but a
   pin is a property of the block the way `set_cell_rects` is a property of
   the cell, and one spelling should work for a flat block and a cell.
   Origin at the block's lower-left, x along the block's width.  (Q4 asks
   whether flat should stay absolute instead.)
5. **The far-face case is the real gap, and this does not close it.**  A pin
   on the side AWAY from its partner needs the route to wrap the block.  The
   generator has L, Z, U, out-of-bbox detours and MST, but no shape that
   STARTS from a forced face.  The first cut relies on those shapes and
   reports loudly where they fail; whether a partner's pin placement is
   routable at all is E3's own question, so measuring it is the experiment
   rather than something to design around in advance.
6. **`from_pins` needs one importer change.**  `_parse_lef_pins` keeps a
   pin's centroid and direction only (`CellPinRow` has no layer).  It should
   keep the pin as a RECT strip on its LAYER, because a pin has extent and
   the layer is the fourth knob.  Small, and orthogonal to the rest.
7. **Two PRs, not one.**  First: the tagged strips and the single-sourced
   `landing_rects` resolver (generator, pass-through clamp, audit), `rect`,
   `at`, persistence, the template transform — levels 1, 2, 3, 5 — with the
   die-port identity as a test (a DEF `PIN` routed as today must equal the
   same port declared as a full-strength `fix_pin`, byte for byte) and a
   test that a stub ending on a strip's inner edge or short end is NOT a
   landing.  Second: `layer` with the approach/metal split and the endpoint
   via, per-landing `order`, per-bit targets, `from_pins` — levels 4, 6 and
   the LEF change — touching the planner and DNUTS.

## Corrections after review (2026-09-20)

Four findings from the first review round (Codex on #944), each checked
against the source and each right; what they changed is folded into the
text above and listed here so the first cut's claims are not mistaken for
the current ones.

1. **A strip needs a face tag.**  `annotate_endpoints` treats every edge of
   a busterm rect as a face, so plain strips could not enforce levels 1–3:
   an east strip admitted landings on its inner edge and its short ends.
   The strip carries the one edge that is its face, consulted at landing
   recognition and in the audit.
2. **The pass-through clamp and the audit read the floorplan, not the
   busterm.**  `tighten_passthrough` and `verify.cpp` call
   `get_block_rects`, so "everything downstream is unchanged" was false for
   exactly the two consumers that decide whether a point pin admits a
   pass-through.  The admissible geometry is single-sourced through a
   `Floorplan` resolver all three read.
3. **A pin's metal layer is not the approach layer.**  Forcing the landing
   stub onto the LEF pin's own layer is unsatisfiable when that layer runs
   the other way (`LAYER_DIR`).  `layer` names the approach layer and must
   match the face's direction; `from_pins` derives it from the pin's metal
   and DNUTS emits an endpoint via per bit.
4. **Bit order is per landing, not per segment.**  A straight segment with a
   fixed pin at each end has one `bit_order` and two landings.  Orders are
   declared per landing, must agree on one straight segment (refused loudly
   otherwise), and may differ across a bend.

## Open questions

- **Q1 — Where do fixed pins come from in your flow?**  A hard macro's LEF
  (cell-level, every instance the same), a block-level DEF with `PINS` at
  the boundary (instance-level), a pin-assignment spreadsheet or Tcl?  This
  decides whether `inst:` matters in the first PR and what `from_pins`
  should read.
- **Q2 — A bundle whose bits carry different fixes: split or refuse?**  The
  NDR bundler splits a mixed-rule bundle into rule-uniform parts, loudly.
  Same here (lean), or refuse the declaration?
- **Q3 — Is `from_pins` ever a default once positions are present?**  Lean
  no: opt-in and byte-identical without it, like every other policy here.
- **Q4 — Local or absolute coordinates for a flat block's `at`?**  Lean
  local (pushback 4); say if the floorplan-reading habit is stronger.
- **Q5 — Do you constrain a pin's LAYER, or only its side and position?**  If
  a set of layers is the norm rather than one, the `layer` token is already
  a set; if it is never constrained, level 4 can wait for the second PR
  without anyone missing it.
- **Q6 — May a pin be reached OVER the cell on an upper layer?**  Pushback 2
  says yes where the block's obstruction model allows it.  If your
  methodology forbids it regardless of `OBS`, the primitive needs an
  `outward` token after all.
- **Q7 — How common is the wrap-around pin** (a pin on the face away from
  its partner)?  If it is a floorplan defect nobody routes around, pushback
  5 is fine; if it is routine, the generator needs a forced-face shape
  family before the primitive is useful to you.
- **Q8 — Bit order: LSB at the low coordinate, or by side?**  `index` assumes
  bit 0 at the lowest coordinate along the face.  If your convention flips
  with the face (LSB nearest a corner, say), `order` needs a per-face form.

## Not in scope

Pin *assignment* — choosing positions for a block's pins — is not this
primitive.  `fix_pin` consumes a placement; the ladder's Q2 decision (no
credible in-house baseline for pin assignment) stands.  A derived pin
placement from the top's routed result — the `derive_top_plan` idea one level
down — would be a separate item, and it would emit `fix_pin` lines.
