# Seg–busterm containment: a trunk contained in its endpoint block

Repro: `flow/big_data_test/big2/b34_bus_028.buda` (commit "new bug repro (passthru
trunk is tied to a block edge)"). Three abutted blocks, a bus whose trunk runs
*inside* one of its endpoint blocks.

## The repro

```
add_block blk_00 1250 3780 2230 4775     # receiver
add_block blk_15 2230 3365 3500 4615     # driver
add_block blk_32 2230 4615 3500 5350     # receiver
add_bus bus_028[28] blk_15.p blk_32.p,blk_00.p
```

Candidate `TRUNK_V@x1740` (`dump_topologies --conn`):

```
seg0  V  along[4615,4775] perp=1740  slide=[1250..2210]   busterms: blk_00@face=4775(mid)   segs: seg1@4615, seg2@4615
seg1  H  along[1740,2230] perp=4615                       busterms: blk_15@face=2230(mid)   segs: seg0@1740   passthru: blk_00, blk_32
seg2  H  along[1740,2230] perp=4615                       busterms: blk_32@face=2230(mid)   segs: seg0@1740   passthru: blk_00, blk_15
```

`seg0` (the V trunk at x=1740) is **fully contained in `blk_00`**: x=1740 is interior
to blk_00's x-extent [1250,2230]; its along-span [4615,4775] is inside blk_00's
y-extent [3780,4775]; its **bottom** endpoint — the junction with seg1/seg2 at
y=4615 — is already inside blk_00; its **top** endpoint rides blk_00's top edge
(y=4775).

The candidate routes cleanly (0 unplaced). This is **not a routing failure** — it
is an **over-constraint / wrong abstraction**.

## Root cause

The connectivity model knows exactly two ways to connect a segment to a block
(`SegConn::Kind = {BUSTERM, SEG}`):

- **BUSTERM** — the segment *endpoint lies on a block face* (`annotate_endpoints`,
  `topology.cpp:699`: `P.y == r.y1 || P.y == r.y2`). The face anchors the segment
  and Pass-1 of `compute_slide_ranges` (`conn_topology.cpp:267-276`) bounds the
  segment's perpendicular slide to the block's extent.
- **SEG** — the endpoint meets another segment (T-junction).

There is **no notion of containment** — a segment whose body lies *inside* a
block without its endpoint touching a face. So to connect the trunk to the
receiver `blk_00`, `add_trunk_v` (the coverage-driven span, `topology.cpp:~1500-1530`)
**extends the V-spine up to blk_00's top face (4775)** to manufacture a BUSTERM
tap — even though the junction at y=4615 is already inside blk_00.

Consequences:

1. **The spine is redundant.** Its entire height (y 4615→4775) exists only to reach
   blk_00's face. blk_00 already contains the junction, and seg1/seg2 already pass
   through blk_00 (they list it as `passthru`). The connection is available by
   containment; the spine manufactures a tap instead.
2. **Tied to the edge.** seg0's along-span top endpoint is pinned to y=4775
   (160 units of redundant V wire above the junction).
3. **Containment is invisible.** Because blk_00 is a BUSTERM, it is marked
   `explicitly_connected` (`conn_topology.cpp:373-377`) and excluded from the
   pass-through machinery; the dump's per-segment passthru list also excludes a
   block the segment taps (`buda_cli.py:1453`), so blk_00 never shows as passthru
   for seg0 even though `_seg_spans_block(seg0, blk_00)` is true.

The face-tap is doing **double duty**: it both *connects* blk_00 and *bounds the
perp slide* to blk_00's x-extent ([1250..2210], the "doesn't slide out" guard).
The user's ask — *"don't force it to connect to a block edge, but ensure it
doesn't slide out"* — is exactly: keep the perp-containment, drop the edge tap.

## Target model — containment as a first-class connection

An **endpoint/busterm block that the trunk passes through** should be connected by
**containment** (pass-through), not by extending the spine to a face:

- **Coverage:** the block is covered by the segment that spans it (the existing
  `tighten_passthrough_ranges` + `check_topo` pass-through-coverage path), and
  because it is a bundle busterm it counts as connected.
- **Perp slide:** bounded to the block's perpendicular extent (same as today's
  face-tap Pass-1) → *doesn't slide out*.
- **Along span:** **not** pinned to a face → *not tied to the edge*. The spine is
  not extended past the block interior to reach a face.
- **Degenerate spine collapse:** when the spine's height existed *only* to reach
  the contained endpoint block (b34: all other attachments share y=4615), the
  spine collapses to zero and the block is covered by the perpendicular branches'
  pass-through — the topology becomes the two H branches meeting at a junction
  inside blk_00. (When the spine has height from other attachments, it simply
  stops passing through the contained block instead of extending to its far face.)

## Implementation plan

1. **`topology.cpp` `add_trunk_v` / `add_trunk_h` (generation).** When an
   endpoint block *contains the trunk axis* (`x_trunk ∈ [block.x1, block.x2]` for V),
   treat it as a pass-through cover for span purposes — do **not** push the
   spine's `att_y` to that block's far y-face. If the resulting spine span is
   degenerate (`y_lo == y_hi`), drop the spine segment and let the perpendicular
   branches carry the block by pass-through (re-root like the existing
   trunk-completion paths). Mirror x↔y in `add_trunk_h`.
2. **`annotate_endpoints` (`topology.cpp:699`).** Do not annotate a BUSTERM face
   tap for an endpoint block the segment only passes through (endpoint strictly
   inside, not on a face). Leave it to the pass-through path.
3. **`conn_topology.cpp`.** `tighten_passthrough_ranges` already covers a busterm
   block reached purely by pass-through *if* it is not in `explicitly_connected`.
   Ensure a contained endpoint block is treated as a pass-through cover (perp
   bounded to its extent) and still counts as connected for `check_topo`.
4. **`verify.cpp` `check_topo`.** A contained endpoint block is covered by
   pass-through; confirm no `FEEDTHRU_RELAY` / `BUSTERM_OPEN` is raised for it.
5. **Dump (`buda_cli.py`).** Surface the containment so the contained block shows
   as a pass-through (or a `contains` tag) for the trunk rather than vanishing.

## Tests (pin the desired semantics)

- A V trunk whose axis is contained in an endpoint block: the block is connected
  (no `BUSTERM_OPEN`/`FEEDTHRU_RELAY`), the trunk's perp slide is bounded to the
  block's x-extent (no slide-out), and the trunk's along-span is **not** extended
  to the block's far y-face (no edge pin / no redundant wire).
- `b34_bus_028.buda` routes clean end-to-end (topo/NUTS/dNUTS) with the slimmer
  topology.
- Regression guard: the existing trunk / pass-through / MST-completion suites stay
  green (this changes generation for the contained-endpoint case only).

## Status

Design + failing tests first; generation change is guarded to the
"endpoint block contains the trunk axis" case to keep the blast radius small.
