# The fixed-pin primitive: `fix_pin`

*Design proposal, 2026-09-20 — convergence-ladder build item 7.  Not built.
Revised three times the same day, after three review rounds (see *Corrections
after review*); the behavioural contract is spelled out AHEAD of the code in
`test/tests/features/fixed_pin.feature` (`@future`), written against admitted
and refused landings rather than the token grammar.
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
- **Many consumers read the floorplan, not the busterm — and they ask two
  DIFFERENT questions.**  The pass-through clamp (`tighten_passthrough`,
  `topology_analysis.cpp`), the NUTS pass-through anchor (`nuts.cpp`), the
  edit session's landing candidates (`topo_edit.cpp`, `bt_all_rects`), the
  auto bit cap (`bundling_cmds.py`) and the audit (`verify.cpp`, a dozen
  sites) all fetch a block's rects from the `Floorplan`, so a strip
  installed at `mk_bt` alone would let a trunk generated through a point
  pin slide anywhere across the physical block and still pass
  `BUSTERM_OPEN` — the opposite of "a point pin admits no pass-through".
  But the planner's LOW-layer obstruction cache (`congestion_planner.cpp`,
  `leaf_rects_cache_`) and `Floorplan::low_layer_keepouts` read the SAME
  call for the OPPOSITE question — where a leaf's body BLOCKS the lower
  stack — and a resolver that replaced `get_block_rects` for them too would
  turn a macro into a routable notch on LOW layers the moment its pins were
  fixed (second review round).  So there are TWO geometries, named so they
  cannot be conflated: **`landing_rects(block, bundle)`** — the face-tagged
  strips, or the block's own rects with every edge a face when nothing is
  fixed — read by every consumer asking "may this wire attach here"; and
  **`obstruction_rects(block)`** — the physical footprint, NEVER strips —
  read by every consumer asking "what does this block block".  The census
  says which each site reads:

| asks | sites | reads |
|---|---|---|
| may this wire attach here (generator) | `mk_bt` in `generate_2pin` / the n-pin generator; `annotate_endpoints` through the busterm; the per-bundle Hanan grid (`bt_all_rects`) | `landing_rects` |
| may this wire attach here (analysis, NUTS, edit, bundler) | `tighten_passthrough`; the pass-through anchor in `nuts.cpp`; `topo_edit.cpp`'s `bt_all_rects`; `set_max_bundle_bits auto` in `bundling_cmds.py` | `landing_rects` |
| may this wire attach here (audit) | every `get_block_rects` / `get_block_bounds` site in `verify.cpp` | `landing_rects` |
| what does this block block | `congestion_planner.cpp` `leaf_rects_cache_`; `Floorplan::low_layer_keepouts`; footprint keepouts on the grid | `obstruction_rects` — unchanged by any fix |
| what do I draw or report | `viz_explorer/{analysis,draw,edit}.py`, `viz_common.py`, `web/serialize.py`, `buda_session/util.py`, `buda_cmds/setup_cmds.py` | BOTH: the physical outline as today, the strips drawn over it where a fix exists |

A design with no fix is byte-identical, because `landing_rects` then returns
exactly what `get_block_rects` returns today and `obstruction_rects` never
changes at all.

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
  edge and the two short ends are not faces.  **A fix replaces BOTH
  spellings of the busterm's rects** (second round): `annotate_endpoints`
  unions the margin-inset `rects` with the physical `orig_rects` (PR #835
  P2, so a restored endpoint on a margined block keeps its tap), and a tag
  that narrowed only which edge of a rect counts would leave the physical
  face as a second, untagged landing path — under a nonzero corner margin,
  exactly when `orig_rects` is populated.  Under a fix, `rects` and
  `orig_rects` are the same strips, so there is one spelling to tag.
  **A declared window is physical and is NOT margin-inset** (second round):
  `at 20..60` is stated in the block's own coordinates on the physical face;
  the corner margin is a DEFAULT for faces nobody constrained, and a user
  who named a window has constrained that face — so the strip is built from
  the window as written, the per-rect inset (`shrink_rects`) does not apply
  to it, the `2*margin >= face_extent` guard has no strip to act on, and the
  margin still governs the block's other faces.  `dump_pin_fixes` prints the
  window as declared, so what it reports and what the strip is cannot
  differ.  **The strips are Hanan loci, deliberately and with a bound**
  (second round): the per-bundle grid is built from the busterm rects
  (`bt_all_rects`), so a strip's bounds become loci a trunk can snap to —
  without which a level-5 window could have no legal locus at all.  A strip
  contributes its ALONG-axis edges only (the face line and the window's two
  ends), never its one-unit perpendicular pair, so a `*` fix over many
  blocks does not multiply loci the item-12 way; PR 1b measures the grid
  size on the SoC under `fix_pin cell:* H` against the unfixed grid.
- **`set_max_bundle_bits auto` reads the admissible window** (second round).
  The auto cap bounds a bundle by the bits its endpoint faces can host and
  computes that from the whole block's shortest edge (`get_block_bounds`);
  under `at 20..60` the face is 40 units, so a 32-bit bundle would pass a
  cap sized from a 500-unit edge and then have nowhere to land.  The cap
  reads `landing_rects` — the claim it already makes, now true under a fix.
- **Pass-through is automatic, and it is TWO landings** (third round).  A
  trunk crossing the block is a landing today when it crosses the busterm
  rect.  Under a fix a pass-through is a landing on BOTH faces it crosses —
  it enters on one and leaves on the other — and is admitted only when the
  fix admits both, each at the trunk's perpendicular coordinate.  So an `H`
  fix admits a horizontal pass-through (east and west both admitted); a bare
  `east` admits none, since the exit on the west face is not a landing; and
  a point pin admits none for the same reason — NOT because its strip is
  thin.  The second-round text said "a landing when it crosses a strip", and
  a strip one pitch wide at `y = 18` IS crossed by a horizontal trunk at
  `y = 18`, so that rule admitted exactly the trunk the point-pin contract
  refuses (Codex on #944, third round).  Nothing to declare — provided the
  pass-through readers (the clamp, the NUTS anchor, the audit's coverage
  count) ask `landing_rects` the two-face question rather than "does the
  segment span the block", which today they do not (above).
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
  emits one ENDPOINT VIA per bit between the two, as a `net_via` row of its
  own KIND (third round).  The second-round text rode the negative-`to_seg`
  row the NDR shield bond straps use, and that sign is read as "bond strap"
  by every consumer: `emit_shield_bond_vias` deletes every negative row
  before regenerating straps (`detailed_nuts.cpp`), route persistence
  assigns them to the rule's shield net (`persist.py`), and the bottom-up
  copier skips them (`nutsflow.py`) — so an endpoint via encoded that way
  would be deleted, persisted on GND, or missing from every copied instance.
  `NetVia` gets an explicit `kind` (`SEG`, `BOND`, `ENDPOINT`; persisted as
  a `net_via.kind` column in the same schema bump as `pin_fix`), the three
  consumers filter on the kind rather than the sign, and the strap's negative
  ordinal keeps its meaning.  The approach set is enforced PER LANDING
  SEGMENT and as a SET, which neither existing mechanism carries (third
  round): `BundleInput::allowed_layers` masks EVERY segment of a candidate,
  so an east pin held to horizontal M5 would starve the vertical leg of an
  `L`, and `pinned_seg_layers` holds ONE layer per segment, so it cannot say
  `layer M3,M5`.  The landing stub carries its own allowed set
  (`Segment::layer_set`, empty = unconstrained), stamped by the generator on
  the CANDIDATE — never index-keyed on the input, the `pinned_seg_layers`
  hazard a trial moving a bundle to another shape already taught — and
  intersected with the band mask inside the STRICT enumeration, so a fixed
  layer steers the choice rather than the ladder escalating past it.
- **`order`** is a bit permutation per LANDING, not per segment (Codex on
  #944).  `BusSegment::bit_order` is one value for a whole segment — today
  `run_detailed_nuts [lo_hi|hi_lo]` sets it for the whole run — and a
  straight segment with a fixed pin at each end (an `I` shape, or a
  pass-through trunk touching several fixed pins) has several landings.  A
  straight wire cannot reverse its bits, so two landings on ONE segment must
  agree; across a BEND they may differ, since the per-bit via crossing
  simply mirrors, so an L with `index` at one end and `rindex` at the other
  is legal and each leg takes its landing's order.  The agreement is checked
  PER CANDIDATE at generation, not at declaration (third round): fixes are
  declared before bundling, so no segment exists yet to ask whether two
  landings share one, and the generator may produce both a direct `I` (the
  orders conflict) and an `L` (each leg its own) for the same pair of pins —
  a declaration-time refusal would be undecidable or would discard the
  routable bend.  A candidate whose straight segment joins two landings of
  opposite order is DROPPED with a printed note naming both pins, like the
  coverage gate; when every candidate is dropped the pool is kept with the
  warning and the audit reports `PIN_FIX`, so the bus never strands.  The
  declaration validates only what is decidable there: the token and the
  target.  `index` sorts the bundle's nets by (bus name,
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
  BEFORE bundling, like `set_ndr`.
- **Decision (was Q2): a bundle whose bits carry different fixes is SPLIT
  into fix-uniform parts, loudly**, the way the NDR bundler splits a
  mixed-rule bundle (second round).  That is what makes
  `landing_rects(block, bundle)` well-defined — after the split every
  bundle resolves to ONE fix per block, so the key is `(block, bundle)` and
  not `(block)` alone; the obstruction geometry needs no bundle key at all.
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
7. **Three changes, not one, and Q7 first.**  **PR 1a**: the two-geometry
   split — `landing_rects` and `obstruction_rects` as the two resolvers,
   every site in the census moved onto the one it asks for, NO `fix_pin`
   command at all — a pure refactor, byte-identical, corpus-guarded, so the
   risky half lands where it can be measured against nothing changing.
   **PR 1b**: the tagged strips and the command (faces, `rect`, `at`),
   persistence, the template transform, the auto-cap and Hanan rules —
   levels 1, 2, 3, 5 — with the die-port RELATION as a test (a DEF `PIN` is
   a busterm whose every edge is a face, so a port declared with ALL FOUR
   faces admissible must route byte-identically to today's port, and a
   single-face fix on it must admit a SUBSET of the unfixed port's landings;
   the first cut asked for byte-identity against a FULL-STRENGTH fix, which
   admits one tagged face and cannot equal a four-face port) and a test that
   a stub ending on a strip's inner edge or short end is NOT a landing.
   **PR 2**: `layer` with the approach/metal split, the per-segment layer
   SET on the landing stub and the endpoint via as its own via KIND,
   per-landing `order` with the per-candidate agreement gate, per-bit
   targets, `from_pins` — levels 4, 6 and the LEF change — touching the
   generator, the planner and DNUTS.  And **Q7 is the gate on
   PR 1b**: if the wrap-around pin is routine in the flows this is for, the
   forced-face shape family comes first and the command is premature.
8. **Reserved names, reserved HERE and not in the catalogue.**  The message
   id is **BUDA-1922** (1918–1921 are taken) and the audit kind is
   **`PIN_FIX`**.  Neither is added to `buda_diag.py` or the `ViolationKind`
   enum by this note: `test_every_registered_id_is_emitted_somewhere` makes
   the catalogue a contract that every id it holds is emitted, so an id
   lands WITH its emitter in PR 1b, and this note is where the reservation
   lives until then.

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

### Second round (2026-09-20, the owner's review on #944)

Eight findings, each checked against the source and each right, plus two
housekeeping items.  The first is the serious one: the first correction's
"single-sourced resolver" would have broken the obstruction model.

1. **Two geometries, not one.**  `congestion_planner.cpp`'s
   `leaf_rects_cache_` and `Floorplan::low_layer_keepouts` read
   `get_block_rects` to know where a leaf BLOCKS the lower stack; a resolver
   replacing that call everywhere would make a fixed macro's body a routable
   notch on LOW layers.  `landing_rects` and `obstruction_rects` are now
   distinct, with a census of which site reads which.
2. **`orig_rects` was a second, untagged landing path.**  Under a nonzero
   corner margin the annotator unions the inset rects with the physical
   ones; a fix now replaces both spellings with the same strips.
3. **Margin versus window had no precedence.**  A declared window is
   physical and not inset; the margin governs only the unconstrained faces.
4. **The die-port identity test could not pass as stated.**  A port's every
   edge is a face; a full-strength fix admits one.  Restated as a relation:
   an all-faces fix is byte-identical, a single-face fix admits a subset.
5. **`set_max_bundle_bits auto` would under-cap.**  It sized the cap from
   the whole block's shortest edge; it reads the admissible window now.
6. **The consumer census was short.**  The NUTS pass-through anchor,
   `topo_edit.cpp`, the bundler and seven Python readers are in the table.
7. **Hanan coupling was unstated.**  Strips are loci by design, contributing
   along-axis edges only, with the grid size measured in PR 1b.
8. **Q2 was load-bearing.**  It fixes the resolver's key; promoted to a
   decision (split, like NDR), so the key is `(block, bundle)`.

Housekeeping: the contract is in `test/tests/features/fixed_pin.feature`
(`@future`, listed in the coverage plan), written against outcomes because
`feedthru.feature` is this repository's own record of what happens to a
spec written against a grammar that then moved; and BUDA-1922 / `PIN_FIX`
are reserved in pushback 8 rather than in the catalogue, for the reason
given there.  The sequencing suggestions — Q7 first, PR 1 split into a
byte-identical refactor and the command — are adopted in pushback 7.

### Third round (2026-09-20, Codex on #944 at `cb8a086`)

Four findings, each checked against the source and each right.  Three are
the first round's class again — a mechanism named for reuse that does not
carry what the design asked of it — and the fourth is a check placed where
it cannot be made.

1. **The endpoint via cannot ride the bond strap's row.**  A negative
   `to_seg` means "bond strap" to three consumers: `emit_shield_bond_vias`
   deletes every such row before regenerating straps, route persistence puts
   them on the shield net, and the bottom-up copier skips them.  `NetVia`
   gets an explicit `kind`, and the consumers filter on it.
2. **The approach layer needs a per-segment SET.**  `allowed_layers` masks
   every segment of the candidate (an east pin held to M5 starves the `L`'s
   vertical leg) and `pinned_seg_layers` holds one layer per segment (no
   `M3,M5`).  The landing stub carries its own set, on the candidate.
3. **A point pin was still crossed by an aligned trunk.**  "A landing when
   it crosses a strip" admitted a horizontal trunk at the point's own
   coordinate.  A pass-through is two landings, on the faces it enters and
   leaves, and a fix admitting only one of them admits no pass-through — the
   thinness of the strip was never the argument.
4. **Order agreement cannot be checked at declaration.**  Fixes precede
   bundling, so no segment exists to ask; the check is per candidate at
   generation, dropping the straight candidate and keeping the bend.  The
   first round's reply argued against exactly this option as "later and
   quieter"; that premise assumed a segment to check, and there is none.

## Open questions

- **Q1 — Where do fixed pins come from in your flow?**  A hard macro's LEF
  (cell-level, every instance the same), a block-level DEF with `PINS` at
  the boundary (instance-level), a pin-assignment spreadsheet or Tcl?  This
  decides whether `inst:` matters in the first PR and what `from_pins`
  should read.
- **Q2 — DECIDED (second round): split**, like the NDR bundler, loudly; the
  number is kept so answers by number stay valid.
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
- **Q7 — THE GATE: how common is the wrap-around pin** (a pin on the face
  away from its partner)?  If it is a floorplan defect nobody routes around,
  pushback 5 is fine and PR 1b proceeds; if it is routine, the generator
  needs a forced-face shape family BEFORE the primitive is useful to you,
  and PR 1b waits for it.  This is the one answer that decides whether the
  command gets built next or later.
- **Q8 — Bit order: LSB at the low coordinate, or by side?**  `index` assumes
  bit 0 at the lowest coordinate along the face.  If your convention flips
  with the face (LSB nearest a corner, say), `order` needs a per-face form.

## Not in scope

Pin *assignment* — choosing positions for a block's pins — is not this
primitive.  `fix_pin` consumes a placement; the ladder's Q2 decision (no
credible in-house baseline for pin assignment) stands.  A derived pin
placement from the top's routed result — the `derive_top_plan` idea one level
down — would be a separate item, and it would emit `fix_pin` lines.
