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

## Why a "single straight segment" (approach A) is invalid

A topology segment is a **bus of N bit-wires**, not one wire. N bits need N
tracks → a perpendicular **interval** of non-zero width, so every segment must
have `perp_lo < perp_hi`. Segments are therefore assigned to Hanan **intervals**
(the cells/channels between grid lines), never to Hanan **lines** (block edges,
which have zero width on their perpendicular axis).

The tempting "single H segment at y=4615" sits exactly on a Hanan line
(`blk_15.y2 == blk_32.y1 == 4615`): blk_15 and blk_32 **abut** there, so over
their x-range a segment at y=4615 has `perp_lo == perp_hi == 4615` — zero slide,
nowhere for the 28 bits. It is not merely a narrow option; it assigns a bus to a
zero-width line, which is unroutable in principle.

This is exactly why the bus *must* route through `blk_00`'s interior: blk_00
spans y[3780,4775], straddling the 4615 abutment, so it provides a real interval
for the bits. Today's `seg0` already does the geometrically-right thing — a
vertical bus inside blk_00 with a genuine perp interval `slide=[1250..2210]`. The
defect is purely the **modeling** (edge-tap busterm vs containment), which
over-extends the along-span to blk_00's far face and hides the pass-through. The
containment connection must give the contained segment its slide from the
**block's interior interval** (non-zero, bounded so it cannot exit) — never pin
it to a face/line.

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

## Status — IMPLEMENTED (`add_trunk_v` only)

Landed in `add_trunk_v` (`topology.cpp`), guarded tightly so the blast radius is
exactly the contained-endpoint case (full fast+mid+slow suite green, big2
unchanged at 9 overlaps / 60 unplaced). **The `add_trunk_h` mirror was reverted**:
on b34 it resurrected a degenerate H-trunk (`TRUNK_H@y5062`) whose blk_00 stub
lands on the abutment Hanan line x=2230 — a zero-slide pinch (`interval[2230,2230]`,
28/28 unplaced). Worse, it was the *cheapest* candidate (wl 734) so the planner
auto-selected it. The same-side / ≥2-stub guards don't catch a stub pinned to a
shared block edge, and b34 is fundamentally a V-trunk case, so the H mirror is
net-negative; the H-symmetric containment case is deferred until it has its own
repro and a no-pinch guard. Only `add_trunk_v` is active:

1. **Pull-back.** Before computing the spine span, a no-stub (contained) endpoint
   block whose extent the STUB span already overlaps has its `att_y`/`att_x`
   pulled back into the stub span (instead of being pushed to its face). The
   spine no longer over-extends to manufacture an edge tap; the block is covered
   by the spine/branches passing through it (pass-through). Single-rect only —
   multi-rect/TEG keeps its own per-rect handling.
2. **Degenerate-spine collapse.** When the span collapses (all real attachments
   share one coordinate) AND the perpendicular stubs alone connect every block
   (each contained block holds the junction, **≥2 stubs**, all on the **same
   side** of the trunk so they overlap and stay connected), emit the stubs with
   no spine. Otherwise the candidate is dropped as before. The `≥2 stubs` /
   `same-side` guards keep the collapse from emitting zero-slide single-segment
   "trunks" on an abutment line (the Hanan-line pinch the principle above
   forbids).

Result on `b34_bus_028`: the `TRUNK_V@x1740` candidate drops from 3 segments /
wl 1140 to 2 / wl 980, with **blk_00 connected by containment** (a `passthru` on
both H segments, no BUSTERM edge tap) and non-zero slides (835, 160). Routes
clean end to end.

## Update — containment is GATED ON FEEDTHRU

Connecting a block by containment (the trunk runs over/through its footprint with
**no stub landing on its face**) is electrically valid only if the block relays
the bus across its interior to reach its pin — i.e. the block is a declared
**feedthru** on the trunk layer (`set_feedthru`). Otherwise the receiver never
gets the net: the connection is *open*. It only "passes" NUTS/dNUTS today because
those stages check track overlap, not feedthru legality. **Containment ⇒
feedthru.**

So both halves of the `add_trunk_v` containment machinery are gated on
`floorplan_.get_feedthru(block, v_layer_)`:

1. **Pull-back** (`topology.cpp` ~1583) — a contained endpoint block is pulled
   into the stub span (connected by pass-through) only if it is a feedthru.
   Otherwise it keeps the face `att_y` the push loops gave it, the spine stays
   non-degenerate, and the block is connected by a real **edge tap**.
2. **Degenerate-spine collapse** (~1676) — a no-stub contained block counts toward
   `all_covered` only if it is a feedthru; otherwise the collapse does not fire.

Result on `b34_bus_028` (no feedthru declared): `TRUNK_V@x1740` is **not**
collapsed — it is a 3-segment edge-tap (blk_00 tapped at its top face), routes
clean, and big2 is unchanged at 9 overlaps / 60 unplaced. Declaring
`set_feedthru blk_00` restores the slim 2-segment containment shape. Tests:
`test_contained_endpoint_edge_taps_without_feedthru` (no feedthru → edge tap) and
`test_contained_endpoint_connects_by_containment_with_feedthru` (feedthru →
containment).

### Deferred — OOB / suppression-induced containment (topo 3)

`TRUNK_V_OOB@x1025` connects blk_00 by a **different** containment path: the OOB
trunk's long blk_15/blk_32 stubs cross blk_00, and the existing **stub
suppression** removes blk_00's own stub, leaving it covered only by the crossing
wires (which land at the zero-slide triple corner `2230,4615` — not a real
interval tap). Per the feedthru principle this is also illegal containment.

The natural fix — *don't suppress a non-feedthru bundle endpoint's own stub* —
has **broad blast radius**: stub suppression is the same mechanism that removes
legitimately-redundant stubs across many flows, and big2's routing relies on
suppression-induced pass-through coverage in several bundles. Gating it
generally keeps those stubs, and the extra wire overloads the planner: big2's
DNUTS unplaced regresses **60 → 128** (e.g. bundle 24's 48 bits dump onto M2, a
LOW layer, over a cell). No *connectivity* regressions (verify still reports no
opens) — purely a congestion consequence the current planner cannot absorb.

This reveals that the existing pass-through "coverage" model (`verify.cpp` +
suppression) treats *any* wire crossing a bundle endpoint as connecting it, which
the feedthru principle says requires feedthru. Making that rigorous is a larger,
**planner-aware** change (feedthru-aware coverage in verify + a planner that
absorbs the kept stubs, or auto-detecting when an endpoint genuinely needs its
own tap). Tracked as `test_oob_trunk_edge_taps_without_feedthru` (xfail). The
same applies to the `TRUNK_V_OOB+MST@x1025` hybrid (relay-completion path).

### Deferred refinements

- **Duplicate collinear stubs.** When two same-side stubs share a face point
  (b34: blk_15/blk_32 both at x=2230, y=4615) the collapse emits two *identical*
  collinear segments (one per block). They overlap (physically one wire) and
  route clean, but inflate the candidate's reported wirelength and are a
  redundant representation. A future pass could merge same-line stubs into one
  segment tapping both blocks.
- **First-class `CONTAINMENT` SegConn.** This increment achieves containment via
  the existing pass-through path (no edge tap). A dedicated `SegConn::CONTAINMENT`
  kind (perp slide = block interior, no along anchor) would make the model
  explicit and generalise to the non-collinear / multi-rect cases; tracked as a
  follow-up.
