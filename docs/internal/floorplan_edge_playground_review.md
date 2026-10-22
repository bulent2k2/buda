# Floorplan Edge Playground — review of the draft proposal

**Status: REVIEW (2026-10-22).  Nothing is built here; this is a reading
of a proposal, with the code it names open beside it.**

What is reviewed is the draft page
[Floorplan Edge Playground](https://claude.ai/artifact/J4nGx6eBDyhDVx3VgJ7SPq)
(the 2026-10-16 version): the real `bigsoc` NQ 8 abstract route with its
overlaps drawn, a draggable five-block toy that shows the demand on every
block side and every facing window, a grow-then-compact fixer, eighteen
option cards (V1–V6 for the Visualizer, E1–E5 for edge demand, A1–A7 for
automation) and a suggested build order.  The code the page cites lives on
the branch `claude/hierarchical-floorplan-buda-sbfy7o` (tip `7822f96f`):
the `landings` and `facing` options of `auto_floorplan`, the
`side_loads` / `side_overload` / `facing_pairs` / `align_facing` family in
`src/hier_floorplan.py`, `tools/dirty_bundles.py`, the "Landings and
facing" section of [AUTO_FLOORPLAN.md](../AUTO_FLOORPLAN.md) and the page's
own row in [artifacts.md](../artifacts.md).  **None of that is on `main`**
(`f22fd507` as this is written), so line numbers below are the branch's
unless the file is unchanged on `main`, and the review is of the proposal
against that branch, not against a shipped feature.

Five angles were asked for and each gets a section: UX design principles
(§1), algorithmic effectiveness (§2), algorithmic efficiency (§3), the data
model and DB interface with proprietary databases in mind (§4), and the fit
in a hierarchical RTL + PNR flow (§5).  §6 turns the findings into a build
order; the appendix grades the eighteen cards one by one.

## 0. The short version

**What the page gets right.**  It starts from a measured failure rather
than a feature list: the L1 tag/bank bus whose facing window is 132 units,
122 after the stack's 5-unit corner margins (`flow/mockpdk/stack.buda`),
which on M5 is 30 signal tracks for a 32-bit bus — the `#536`
supply-doomed-seat class that AUTO_FLOORPLAN.md §5 names as the residual
on both vehicles.  The toy makes the two readings a designer needs —
demand on a SIDE and demand on a WINDOW — visible in one picture, the
option cards name the file each would land in, and the "what can go wrong"
list (growth cascade, track phase, fixed cells, side flips, window-vs-side)
is the right list.  The batch half it builds on is measured, and
measured honestly: `landings 1 facing 3` takes `bigsoc` NQ 8 from 3,296
to 1,709 stranded bits for +5 % die, and NQ 16 to 4 static faults for
**+43 %** die.

**Five findings that change the build order.**

1. **The rule the page wants to show live is not the rule that sizes the
   faces.**  `side_overload` charges one pad per side; `landing_floor`,
   which `landings 1` sizes the leaf with, charges one pad per LANDING
   plus a channel between landings (the branch's own L1-tag example: 280
   against 320).  `side_loads` also splits a k-member net's bits by
   (k − 1) across every other member and reads block CENTRES rather than
   the resolved port offsets the packer's cost already uses, and
   `facing_pairs` ignores every net with more than two blocks.  Shown
   live, E1 would read "fits" on faces `auto_floorplan` has already sized
   as short.  Unify the three readings before any of them goes on a
   canvas (§2.1–2.3).
2. **The toy's economics are the toy's.**  The "+5 % per block, +49 to
   +74 % with instances kept equal" comes from a push-only legalizer that
   never re-packs; the batch `facing` RE-PACKS every container and
   measured +5 % at NQ 8 with instances equal by construction.  So the
   page's build order (grow first, compact later) is backwards against
   its own A6: slide and compact-into-slack are free and go first, growth
   is priced, and the GUI's price must come from the dry re-pack
   `_auto_floorplan(_dry=True)` already performs (§2.4, §3.3).
3. **Growth has to be gated by what a cell IS.**  A hard macro's size is a
   LEF fact; the toy grows every block and the branch's `size_leaf` grows
   a `macro` rule to its heavy face.  The BDB already stores the class
   (`cell.cls`, from LEF `MACRO … CLASS`): a `BLOCK`/`PAD` cell gets a
   REPORT and a bundle-side remedy, never a resize (§5.2).
4. **The kernel is already DB-agnostic; the adapter is not.**  Every
   function the page names takes `(pos, size, nets)` dicts and returns
   tuples — that is the right shape and it should stay.  What is wrong is
   that the only adapter from a database to that shape is ~150 inline
   lines of `_auto_floorplan`, reading `cell_children` through a scratch
   copy of the BDB with `sqlite3` because the Python binding has no
   reader.  Extracting that adapter is the whole proprietary-DB story; a
   second copy already exists in the Floorplanner's `compute_hpwl` (§4).
5. **In the PNR flow, E2 is `emit_block_size` and A7 is a block's
   `DIE_AREA`.**  The face demand read off a route already exists as
   `_face_extent` in `src/buda_session/block_size.py`, per layer, in the
   width model the planner charges with; the page's E2 should read it,
   not re-derive it.  And the loop that reproduces a designer's fix
   closes through the hardened block's LEF, so a persisted `minsize` must
   yield to a LEF-stated size when one arrives (§5.3–5.4).

## 1. UX design principles

Read against the usual list — visibility of status, match to the
designer's vocabulary, control and freedom, consistency, error prevention,
recognition over recall, progressive disclosure, help with diagnosis.

### 1.1 Where it is strong

* **Visibility, at the moment it is cheapest.**  E1 shows demand from the
  netlist and the placement alone, before any route exists.  That is the
  correct moment: AUTO_FLOORPLAN.md §4 measures a floorplan at 3–7 s and a
  route at 40 s to 20 min, so a fault caught at the strip costs seconds
  where the same fault at `check_design` costs a routing run.
* **Match to the real world.**  "Window", "side", "bits", "tracks" are
  the designer's words, and the hover card's sentence — "window 122 units
  holds 30 tracks, bus needs 32" (V4) — is the one a designer acts on.
  The page's own prose already speaks in tracks; the toy's strips speak
  in a `need / len` RATIO.  Make the unit tracks everywhere (§2.3).
* **Control and freedom.**  A5 ("proposal with a preview and a price,
  applied as one undoable command") is the right contract and it fits
  the Floorplanner as built: undo is snapshot-based, 50 deep
  (`tools/bdb_floorplanner.py` `_push_undo`), so one proposal = one
  snapshot.  The page's "the batch version cannot be undone; this one
  must" is the right line.
* **Consistency with what exists.**  The page notices that the overlap
  list row already exists and only glows two bundles (V1 answers WHERE),
  and that `tools/render_design.py` already draws overlaps black-on-yellow
  in colours no layer uses (V6).  Both are extensions, not rewrites.
* **The page as an artefact.**  A draggable toy that reproduces the
  cascade in five blocks explains the +43 % better than the table does.
  Keep it, and keep its constants labelled as the mock stack's (`BP 4,
  PAD 24, GAP 16, MARGIN 16` are `flow/mockpdk`'s numbers, not a rule).

### 1.2 Where it should change

* **Put the verdict where the designer already looks.**  The Floorplanner
  has one place a fault is listed: the Validation panel (`OVERLAP`,
  `OUTSIDE_DIE`, `ERROR`; [user guide](../FLOORPLANNER_USER_GUIDE.md)
  "Validation") with a count in the status bar.  A short side and a short
  window are faults of the same standing and should be two more issue
  kinds there (`SIDE_SHORT`, `WINDOW_SHORT`) with the strips as the
  picture of the same rows — the way the Visualizer's overlap LIST and
  overlap HIGHLIGHT are two views of `overlap_details`.  A separate
  "E-panel" would be a third place to look.
* **Reuse the affordances the GUI already has for A2 and A1.**  "Keep
  instances of a cell equal" IS the GUI's existing rule — resizing a
  shared-cell block resizes every sibling (`sync_cell_to_instances`), and
  **Make Unique** is the existing escape.  A2 should surface the PRICE of
  that rule beside Make Unique, not add a second switch.  Likewise A1 is
  a verb like Align and Distribute: put it in the Align ▾ menu with a
  key, since the GUI's keybinding surface is where every other placement
  verb lives ([reference](../FLOORPLANNER_REFERENCE_GUIDE.md)).
* **The price line has a format already.**  Optimize reports "wire-length
  and area before → after with percent" in the status bar; A5's price
  (die before → after, sides fixed, windows fixed, overlaps left) should
  use that line, not a new dialog.
* **Level of detail.**  836 blocks with four strips each is 3,344 strips;
  at chip zoom none is legible and the page knows it (E5).  State the
  rule: E5 outline tint at chip zoom, E4 roll-up by cell in the sidebar,
  E1 strips only inside the drilled container (the Floorplanner already
  draws one level at a time), V4 hover for the sentence.  That is
  progressive disclosure, and it also bounds the per-draw cost (§3.2).
* **Colour.**  Red (doomed) against orange (contended) against the
  layers' own palette, in which M5 is red, is hard for a deuteranope and
  not easy for anyone; `render_design.py`'s lesson ("two colours no layer
  uses") applies to the cause layer too.  Use hatching or a marker shape
  for the cause and ONE table for the colours: `tools/dirty_bundles.py`
  carries `CAUSE_COLOR` (`#d62728` / `#ff7f0e`) and the page's script its
  own pair (`--over` / `#ff7a00`), so the two copies already disagree —
  the "fourth hand-kept copy of the palette" that
  [wishlist-ux.md](wishlist/wishlist-ux.md) item 1 warns about.  Put it
  in `src/viz_common.py` beside the layer palette and let the page, the
  PNG and the viewer read it.
* **V3 must respect the overlap list's existing click cycle.**  A row
  click today cycles both → A only → B only → clear
  (`src/viz_main/highlight.py` `_set_overlap_highlight`).  "Row to zoom"
  should zoom on the FIRST click of a new row and keep the cycle on
  repeats, or it breaks a gesture that exists.
* **Prediction and measurement must look different.**  E1 is a
  prediction (dominant-axis rule), E2 a measurement (the route).  The
  page says a disagreement "points at the dominant-axis rule being wrong
  for that bus"; the canvas has to make the two distinguishable at a
  glance (dashed strip = predicted, solid = measured), or a green
  predicted strip will be read as a clean face (§2.5).
* **Where decisions land.**  The page's want / later / no marks live in
  `localStorage`, per browser, and "Copy as markdown" is the only way out.
  This repository records decisions in the tree; the appendix below is a
  table with a verdict column the owner can overwrite, so the marks have
  a durable home.

## 2. Algorithmic effectiveness

### 2.1 Three models of "which face", and they disagree

There are now three rules for where a bus lands on a block:

| rule | where | what it reads | when |
|---|---|---|---|
| the FACE rule (`face_pair`, `landing_floor`) | `size_leaf`, `_auto_floorplan` | bits grouped by far sibling + direction (+ driver under `landings`), spread over four faces heaviest-first | sizing, before placement |
| the SIDE rule (`side_loads`) | `hier_floorplan.py:827` | dominant axis of the CENTRE offset, normalised by summed extents | after a packing (`facing`), the page's E1 |
| the ROUTE | generator + planner + NUTS | the busterm face the chosen candidate taps | after routing, the page's E2 |

The `facing` option is the loop that reconciles the first two by growing
whatever the second says the first missed, and the +43 % at NQ 16 is
partly the price of their disagreement rather than of the physics: a face
the spread put on the light side ends up on the heavy side once placed,
so the cell grows a side it did not need to.  The page proposes to show
the second rule live.  Three things make it read wrong today:

* **One pad per side.**  `side_overload` computes `need = ceil(bits ×
  bp) + pad` (line 861); `landing_floor` (line 229) charges `pad × n + gap
  × (n − 1)` for `n` landings.  A side with two 32-bit bundles reads 280
  against the 320 `landings 1` knows it needs — the exact example in
  `face_bits_pair_n`'s docstring.  E1 should count landings per side
  (group the side's bits the way `landings` groups them) and call
  `landing_floor`.
* **Multi-member nets.**  `side_loads` gives each member `w / (k − 1)`
  toward every other member (line 851).  For a 32-bit fan-out D → {R1,
  R2}, D is charged 16 + 16 (right only when both receivers are on one
  side) and each receiver 16 from D plus 16 toward the OTHER receiver —
  but 32 bits land on each receiver's face and no wire runs between the
  receivers in a fan-out tree.  `facing_pairs` (line 806) skips the net
  entirely (`len(names) != 2`).  The face rule charges each receiver the
  full 32.  The consistent reading is per LANDING: a k-member net is one
  landing of `w` bits on each member, on the side toward that landing's
  far end (the driver for a receiver, the receivers' centroid for the
  driver).
* **Centres, not ports.**  Both functions use `pos + size/2`.  The packer
  resolves a container's ports to the child that carries them
  (`resolve_ports`, the `(name, dx, dy)` members `hpwl` reads) precisely
  because centred ports pulled the TPU's accumulators into one clump.  A
  bus leaving a 1616 × 120 row at its east end is classified from the
  row's middle.  Use the member offsets; they are already in `nets`.

### 2.2 The window reading

`facing_pairs` is the better half of the model and the check that would
have caught the 122-against-128 case.  Two corrections:

* **Subtract what the route cannot use.**  The window is the geometric
  overlap today.  The route sees the overlap minus two corner margins and
  then only whole tracks of the layer in force: on this stack
  `corner_margin dx 5 dy 5` and M5's 32-unit period with eight signal
  slots (`POWER 3 1 (SIGNAL 2 1)x4 GROUND 3 1 (SIGNAL 2 1)x4`), so 132 →
  122 → 30 tracks.  The function should return `(have_units, have_tracks,
  need_bits)` with the pitch and margin passed in, and the strip should
  show tracks.
* **The cap hides the unfixable case.**  `need = min(need, cap)` with
  `cap = min(aw, bw)` means a pair whose shorter block's side cannot host
  the bus reads as satisfied once the overlap equals that side.  Report
  the cap separately ("window 100 of 100, bus needs 152: the side is the
  limit, not the window"), because the remedy differs — slide fixes a
  window, only growth or a bundle split fixes a side.

### 2.3 Units and thresholds

The toy's `ratio = need / len` with `tight` at 0.85–1.0 mixes the pad
into the number and picks a threshold from nowhere.  Tracks are the unit:
`tracks(side) − landings(side) × (pad/pitch)` against bits, and "tight"
is "fewer than one track of slack", which is what the healers need
(`align_bottom_up` can nudge by a track, not by 15 %).

### 2.4 The fixer: grow-then-compact versus what the batch does

The page's §3 fixer is `growSides` (grow a short side by its shortfall,
per cell with `lock`) then `legalize` x then y, up to four rounds.  Read
closely:

* `legalize` is a push-only 1-D sweep in centre order: after the x pass
  every pair that overlaps in y is separated in x, pairs that do not
  overlap in y cannot overlap at all, and the y pass separates
  x-overlapping pairs without touching x — so the claim that it never
  recreates an overlap holds, and the result is overlap-free with the gap
  by construction.  Good.
* It is monotone outward: blocks never move toward the origin unless
  `closeWhitespace` is on, so growth only ever adds die.  There is no
  fixpoint argument for the four rounds — a move can flip a side's
  dominant axis and create a new shortfall (the page's "side
  classification flips") — the cap is what terminates it.
* The batch `facing` does something different: it records per CELL the
  worst side over every occurrence (`_note_overloads`, `autofp.py:575`)
  and RE-PACKS every container with those sizes as floors (`minsize`,
  `grow_packing`), up to `facing` rounds, stopping when nothing grew.
  Re-packing absorbs growth into slack; push-only cannot.  That is why
  the batch measures +5 % at NQ 8 with instances equal while the toy
  reads +49 to +74 % with `lock` on.  The two numbers answer different
  questions and the page should say so, or the GUI's expectation will be
  set by the wrong one.

The order the economics dictate is the page's own A6 read strictly:
**slide (A4) → compact into slack (A6, within the container outline) →
grow (A1) → re-legalize (A3)**, with A2's equal-instances rule as the
declared default and Make Unique as the priced exception.  Note that
`align_facing` (the branch's A4) is applied only on the `slice` packing
path (`_pack_children`, `autofp.py:643`): a `grid`, `cols` or `sa` packing
gets no slide-before-grow today, so the batch and the GUI would disagree
on the same floorplan until that is lifted.

### 2.5 What edge demand cannot see

The branch's own table says it: after `landings 1 facing 3` at NQ 8,
"what is left is all *contended*" — windows that could host the bus but
whose tracks were taken by other buses.  Edge demand is a width fact and
fixes the doomed class; contention is a routing fact and is the healers'.
Two consequences for the design: a GREEN predicted strip is necessary,
not sufficient, and the canvas must say so (§1.2); and the second reading
a block needs beside its faces is the CROSSING demand over it per layer
(`report_layer_demand` / `buda::query demand`, the number `bigsoc
-iterate` already acts on), which no E card shows.  A later E6 — "over-
the-block demand per TOP layer, measured" — is cheap because the query
exists.

### 2.6 Side flips, and what to do about them

A move changes which side a bus lands on, so a fixed layout can become
short again.  Mitigation that costs nothing: for pairs whose two
normalised offsets are within a band of each other (the ambiguous
diagonal), charge the bits to BOTH candidate sides as "may land" (an
upper bound, drawn lighter), and resolve to one side only once a route
exists (E2).  A side sized for the bound never flips short.

## 3. Algorithmic efficiency

### 3.1 The kernel

Per container of `n` children, `m` aggregated nets with `k` members each:
`side_loads` is O(Σ k²), `facing_pairs` O(m + pairs), `side_overload`
O(n), `align_facing` O(passes × pairs × n) with `facing_shortfall`
recomputed per candidate move (O(m) each).  A container has at most a few
dozen children (81 at the top of the NQ 71 reticle dial; ≤ 8 per slicing
group), so every one of these is microseconds to milliseconds in Python.
The toy's O(n²) `overlapsOf` and `legalize` are the same story at n = 5.
**Nothing here needs C++.**

### 3.2 Interactive: what a drag has to recompute

The Floorplanner redraws on every motion event (`_on_motion` → `_draw`),
and `_draw` already recomputes HPWL from scratch each time: `compute_hpwl`
walks `bdb.all_pins()` and `bdb.all_components()` through the binding on
every draw (`tools/floorplanner_commands.py:1481`), O(pins) with the
database round trip inside the event handler.  E1 "live while dragging"
must not add a second such walk.  What it needs:

* **A snapshot per drill level**, built once on open/drill and
  invalidated on Write: `items` and the aggregated `nets` of the container
  being edited, in the kernel's shape.  This is the adapter §4 asks for,
  and it would also take `compute_hpwl` off the per-draw path.
* **Incremental recompute.**  Moving block `a` changes the side of every
  net touching `a` and nothing else — the side of a bus between `b` and
  `c` depends only on `b` and `c`.  So a motion event recomputes the
  loads of `a` and its net neighbours, O(deg(a) × k), and the facing
  pairs that include `a`.  Strips for the rest are cached.
* **Draw on release at first.**  The GUI already defers the expensive
  part of a resize of a shared cell to release; strips computed on release
  and redrawn with the block are indistinguishable from "live" at these
  sizes and cost nothing to get right first.

### 3.3 The batch loop, and the preview

`facing K` runs up to `K` full dry `auto_floorplan` passes plus the final
one: at NQ 2 the floorplan is 3–7 s, so `facing 3` is a quarter of a
minute; at NQ 16 it is tens of seconds a round.  Two observations:

* The packers are per cell TYPE and deterministic in `(items, nets,
  floors, snap, options)`.  A round only changes the sizes of the cells
  that grew, so only their ANCESTORS need re-packing; a memo keyed on the
  packer's inputs makes rounds 2..K cost the cascade's ancestors alone.
  This is also what makes A5's preview affordable: the price of a
  proposed growth is one dry pass that re-packs the ancestors.
* A5 has two honest previews, not one: the **re-pack** preview (what the
  batch would do; faithful to the price, loses the designer's positions)
  and the **push** preview (A3; keeps positions, overstates the price).
  Offer both and label them; a single "preview" that silently picks one
  is the kind of thing a designer stops trusting.

### 3.4 A3 does not need a new engine routine

`hier_floorplan.compact_pack` (`main`, line 434) is already a
constraint-graph legalize-and-compact that keeps relative order, grows
by the gap, aligns stacked instances of one cell and snaps to the track
period.  The page's "push-only" variant is that routine with the
compaction step skipped — one flag, not a C++ twin.  `FloorplannerEngine
::validate()` is O(n²) in C++ over every block already; at 800 blocks that
is 320 k rectangle tests and it is not what anyone waits for.  If a
compaction ever runs over a FLAT 800-block view, a sort-and-sweep makes it
O(n log n) in Python before C++ is worth a binding.

### 3.5 Track phase is per axis, and one axis cannot be snapped

The page lists track phase as a risk; the number behind it is in
AUTO_FLOORPLAN.md §4: with V pitches 18 / 32 / 41 the x track period is
**11,808 units** — wider than the NQ 8 die (11,584).  So growth snapped to
the period is possible in y (period 306) and not in x; A1's "snap to the
track period" is a per-axis rule, the x side falls back to
`align_bottom_up` after the fact or the `independent` policy, exactly as
the batch does.  `grow_packing` already snaps per axis; the toy's
`growSides` does not snap at all.

## 4. Data model and DB interface

### 4.1 What the kernel reads, and that it should stay that way

Every function the page names takes plain Python — `pos: {name: (x,
y)}`, `size: {name: (w, h)}`, `nets: [(weight, [(name, dx, dy)])]` — and
returns tuples.  That is the correct boundary for a kernel that three
tools (the batch packer, the Floorplanner, the Visualizer) and any
foreign database will feed: no handle, no transaction, no unit.  Keep it,
and write the contract down in one docstring: names are opaque keys the
adapter chose; coordinates are LAYOUT units of one container's frame;
`dx, dy` are optional pin offsets inside the block; a bus is however many
nets share a member set (`aggregate_nets` folds them — no bus NAME is
needed, which matters for OA, where every bit is its own `oaNet`).

### 4.2 The adapter is the missing piece

Today the only path from a database to that shape is inside
`_auto_floorplan`: `kids`, `insts`, `local` (names by `rsplit("/")`),
`pins_by_comp`, `net_comps`, `at_level`, `nets_among`, `resolve_ports`
(`autofp.py:148–300`), with `cell_children` read by saving a COPY of the
BDB to a temp file and opening it with `sqlite3` (`_cell_children_rows`,
line 592) because the binding exposes `add_inst_to_cell` but no reader.
The Floorplanner builds a second, smaller copy of the same thing in
`compute_hpwl` (pins → nets by `net_id`, origins from the engine).  The
page's E1/E2 would make a third.

Extract it: `hier_netlist.container_view(db, cell) -> (items, nets,
ext_bits)` in a module beside `hier_floorplan.py`, with a `cell_children`
reader added to `bind_db.cpp`, and both the packer and the GUI calling
it.  That is also the point where a proprietary database plugs in: an OA
adapter walks `oaInst` → items and `oaNet`/`oaInstTerm` → nets (the
[OA bridge spec](gds_oa_interchange.md) already maps those onto the same
BDB tables); a DEF/LEF adapter reads instances from `COMPONENTS`, sizes
from LEF `SIZE`, nets from `NETS`, which `import_def_lef` already parses.
The kernel never learns which one fed it.

### 4.3 What the proposals write, and who owns it

The fixer produces two kinds of edit, and they land on different sides of
every database:

| edit | BDB | LEF/DEF | OpenAccess | owner in a company flow |
|---|---|---|---|---|
| move an instance (A3, A4) | `component.x1..y2` via `set_comp_bboxes` (batched) / `move_comp`; `cell_children` offsets must follow | DEF `COMPONENTS` `PLACED` | `oaInst::setOrigin` | the floorplan owner |
| resize a cell (A1, A2) | `cell.width/height` via `resize_cell` — every instance, origin kept | LEF `SIZE` (a library fact) | the cell view's boundary | the BLOCK owner |

The second row is the one to be careful with.  `resize_cell` keeps every
instance's origin and grows `x2, y2` (BDB reference, "Mutations"), so
growing a cell in place overlaps its neighbours in every container at
once and nothing in the BDB re-legalizes them — the user guide says in
so many words that parent containment is not enforced and Validate does
not flag it.  So A1/A2 without A3 per affected container is a tool that
manufactures `OVERLAP` rows.  And in a flow where blocks are hardened by
someone else, a resize is not an edit at all: it is a REQUEST for a
bigger block, which is exactly the artefact `emit_block_size` already
emits (§5.3).  The proposal should be explicit that A1/A2 write the
library side and may therefore be refused by the library.

### 4.4 Persisting A7

`minsize` exists on the branch as an argument `_auto_floorplan` keeps
to itself.  To "store accepted sizes where `auto_floorplan` reads them"
there are three homes, in increasing commitment:

1. **The flow text.**  The Floorplanner's Export Flow writes a `.buda`
   script; `auto_floorplan ... minsize cell=w×h,...` as a documented
   option in that script is the repository's own durable form (a checked-
   in flow's TEXT, the rule the Tcl front end is built on), and it needs
   no schema.
2. **A meta row** (`autofp_minsize`, JSON), restored by `open_bdb` with
   the typed-wins rule — the precedent is `layer_reserves`, `pin_fixes`,
   `layer_caps_by_depth`.  No schema bump.
3. **Typed columns** `cell.min_w`, `cell.min_h` (schema v33,
   `SCHEMA_VERSION` in `src/bdb.h`), if the GUI edits them per cell.  The
   precedent is `cell.bottom_up` / `layer_cap` / `layer_floor`.

Do 1 and 2 first.  Whichever home, two rules: a size the LEF states wins
over a declared minimum (§5.4), and a declared minimum that is BELOW what
the faces now need is reported, not silently exceeded — otherwise the
designer's fix and the engine's drift apart with nothing saying so.

A7 as written also reproduces only the SIZE half of a designer's fix.
Positions placed by hand are still re-packed by the next `auto_floorplan`
unless the cell is in `fixed`, which keeps a cell exactly as the BDB holds
it.  "Write back as declarations" should write both: the minimum sizes
and the `fixed` list, or the designer's A4 slides are undone by the first
re-run.

### 4.5 Units, identity, locks

* **Units.**  The kernel is unit-agnostic by construction; the adapter
  converts at the boundary through `lu_per_um` (`set_import_scale`).
  `block_size.py`'s own history (a 20 µm face emitted as 20,000 at a DBU
  scale, Codex on #887) is the trap.
* **Identity.**  `autofp` derives a child's local name by `rsplit("/")`;
  DEF names are flat and escaped, OA names are hierarchical.  The adapter
  keys the kernel on ids it chooses; the kernel never parses a name.
* **Locks.**  The Floorplanner holds a BDB write lockfile (`tools/
  floorplanner_commands.py` `_try_acquire_write_lock`); a read-only second
  session can still compute and SHOW strips, it just cannot apply.  An OA
  adapter has OA's own checkout; the kernel does not care, which is the
  point of §4.1.

## 5. Fit in a hierarchical RTL design + PNR flow

### 5.1 Where each option sits in the flow

```
  RTL ──import_verilog──► BDB (no geometry)
      ──auto_floorplan [landings, facing]──► first floorplan        E1 E3 E4 E5 A1–A7
      ──derive_busterms · hier bundler · planner · NUTS──► top route   V1 V3 V6 E2
      ──run_detailed_nuts · check_design──► bits, causes             V2 V4 V5
      ──emit_block_size · emit_pin_def · emit_guides──► per-block handoff
      ──harden each block (LibreLane)──► LEF (SIZE is now a fact) + DEF
      ──import_def_lef · auto_floorplan fixed <cells>──► next round
```

The E/A cards belong BEFORE hardening: that is when a face can still
grow.  After hardening a block's size is a LEF fact and the only block-
side levers left are A4 (slide, free), the top's channel, and the
bundle-side levers (`set_max_bundle_bits ... for <prefix>`, a layer
change, the healers).  The page does not place its cards on this axis;
it should, because it decides which cards apply to which cells.

### 5.2 Fixed cells and hard macros

The NPU is written by `tpu_lib.tcl`'s array rule and handed over as
`fixed pe_cell,row_cell,npu_cell`; an SRAM from a real PDK is a macro
with a `SIZE`.  Three rules the toy lacks and the branch half-has:

* **A `fixed` cell is an immovable obstacle** for A3/A4 (the page says so
  in its risk list; the toy has no such block).  `PlacementOptimizer`
  already has a Fixed flag per block; the legalizer should read the same
  one.
* **A hard macro is un-growable.**  `cell.cls` from LEF (`BLOCK`, `PAD`)
  is the authority the BDB stores for exactly this question.  A1 on such
  a cell is a REPORT ("east face needs 152, macro is 100: split the
  bundle or move the landing"), never a resize.  The branch's `size_leaf`
  grows a PDK `macro` rule to its heavy face (`max(rule.w, heavy)`,
  `hier_floorplan.py:308`); in a mock PDK that is a convenience, in a
  real one it is a size the fab will not deliver.
* **A bottom-up template's instances stay congruent.**  Growing a cell
  grows every instance; `cell_children` template rows must be rewritten
  (`_sync_cell_children` in the GUI, the batch's own write) so `add_inst`
  and the Floorplanner read one offset table, and `align_bottom_up` runs
  after any growth that was not period-snapped (§3.5).

### 5.3 E2 already exists, as `emit_block_size`

"Measured side demand after routing: bits that actually landed on each
face against the face's signal tracks" is what `_face_extent`
(`src/buda_session/block_size.py:66`) computes for `emit_block_size`:
per face, per LAYER, `eff_bus_width(bits)` on the layer the bits arrive
on — the planner's own width model, so size and route agree by
construction — read off the same `_plan_block` landings `emit_pin_def`
writes pins at.  E2 should call it, not read `nuts_result` afresh.  Two
things fall out: the strip is naturally per layer (a face carrying bits
on two layers has two independent track supplies and one shared face,
which `_face_extent` sums), and E2's number IS the block's
`FP_SIZING`/`DIE_AREA` fragment, so the Visualizer and the handoff cannot
disagree.

### 5.4 The loop closes through the LEF, not only through `minsize`

A7 keeps the designer's accepted sizes for the next `auto_floorplan`.
The PNR flow has a second, stronger memory: once a block is hardened its
LEF `SIZE` comes back through `import_def_lef` and `auto_floorplan
fixed`.  So the precedence is LEF size > declared minimum > face rule,
stated once, and the GUI's A2 price ("growing this cell re-hardens N
blocks") is the honest price in that flow — a resize after hardening is a
re-run of the block's PNR, which costs hours, not die.

### 5.5 Pins are the edge demand's other face

Every short side the strips show becomes, two stages later, a pin map
`emit_pin_def` cannot place: `GRT-0116` overflow when the bits sit on
consecutive tracks with no reserve ([verify_viz](../script_reference/verify_viz.md),
"the bit PITCH is the plan's question").  The side budget E1 computes in
tracks and the pin pitch the handoff needs are the same quantity, so the
E1 strip should take the pin layer's PITCH (and the router's reserve
fraction) as a parameter rather than the stack's bit pitch, or a face
that reads "fits" at BUDA's pitch overflows at the router's.

### 5.6 `bigsoc -iterate` and the per-axis lever

The vehicle's top-down loop grows a starved cell by a uniform `-grow
1.25` where `buda::query demand` reads a TOP layer past 80 %, and widens
the channel 1.5×.  AUTO_FLOORPLAN.md §4 measures that lever as blunt
(528 → 441 → 302 while the endpoint gets worse).  The edge-demand rule is
the per-AXIS, by-the-shortfall form of the same hand-down and the branch
already has it (`facing`); what the loop lacks is the MEASURED version —
hand `_face_extent`'s per-face numbers from round N down as round N + 1's
`minsize`, which is E2 → A7 in batch.  That is a small change to
`bigsoc::round` and it would turn the proposal's two halves into one
experiment with a table beside the existing one.

## 6. A build order that follows the findings

1. **One demand rule** (§2.1–2.3): make `side_loads` / `side_overload`
   count landings with `landing_floor`, charge a k-member net as one
   landing per member, read member offsets, and return tracks.  Pin it
   against `landings` with a test on the L1-tag shape (280 vs 320).
   Re-measure `facing` at NQ 2/8/16 under the unified rule — the +43 %
   may move.
2. **The adapter** (§4.2): `container_view(db, cell)` out of
   `_auto_floorplan`, a `cell_children` reader in the binding, the
   Floorplanner's `compute_hpwl` on it.  This is the prerequisite for
   every E card and it removes the per-draw database walk.
3. **V1 + V3** as the page has them (small, no new model), V6 as a
   `--dirty` flag that calls `dirty_bundles.draw`, which already exists on
   the branch.
4. **E3 then E1**, as Validation issue kinds plus strips inside the
   drilled container, computed on release, with E5 at chip zoom.  E2 from
   `_face_extent` in the Visualizer, drawn distinguishably.
5. **A4 → A6 → A1** behind A5's preview, re-pack and push both, priced
   through the dry pass; A2 as the existing rule with its price beside
   Make Unique; A1 gated on `cell.cls` and `fixed`; growth snapped per
   axis.
6. **A7** as the exported flow's `minsize` + `fixed` first, a meta row
   second, with LEF precedence; then the `-iterate` hand-down (§5.6).

Deferred, with the reason: a C++ legalizer (§3.4, no measured need); V5
(right, large, and already indexed as the prerequisite in wishlist-ux.md
item 3 — build it when V2/V4 want a second violation kind on the canvas).

## Appendix — the eighteen cards

Effort is the page's (S/M/L).  The verdict column is for the owner to
overwrite; the note is this review's.

| card | what | effort | verdict | note |
|---|---|---|---|---|
| V1 | overlap overlay | S | want | draw `overlap_details` where the list already counts it |
| V2 | cause colouring | M | later | needs the cause as a session method; palette in `viz_common`, hatch not hue |
| V3 | row to zoom | S | want | zoom on the first click, keep the 4-state cycle |
| V4 | hover card | M | want | the sentence in TRACKS; the contended record needs the window's track count |
| V5 | violations as data | L | later | the wishlist-ux item 3 prerequisite, unchanged |
| V6 | annotated PNG | S | want | `--dirty` over the branch's `dirty_bundles.draw` |
| E1 | predicted side demand | M | want, after §2.1 | unified rule, on release, inside the drilled container, dashed |
| E2 | measured side demand | M | want | `_face_extent` per layer, solid |
| E3 | facing windows | S | want | in tracks, margins subtracted, cap reported separately |
| E4 | roll-up by cell | M | want | the Validation panel grouped by cell |
| E5 | heat outline | S | want | the chip-zoom level of E1 |
| A1 | grow short sides | S | want, gated | per axis, by the shortfall, snapped in y, never on `cls` BLOCK/PAD or `fixed` |
| A2 | keep instances equal | S | want | the GUI's existing rule, priced beside Make Unique |
| A3 | 1-D compaction | M | want, in Python | `compact_pack` with the compaction step optional; no C++ |
| A4 | slide before grow | M | want, first | lift `align_facing` off the slice-only path |
| A5 | preview + price | M | want | two previews (re-pack, push), one undo snapshot, the Optimize status line |
| A6 | compact into slack first | M | want, before A1 | the container outline as a constraint, "does not fit" as a result |
| A7 | write back as declarations | M | want | minsize AND `fixed`; flow text, then meta; LEF wins |
