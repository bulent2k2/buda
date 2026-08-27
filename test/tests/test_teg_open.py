# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TEG_OPEN — the missing-bridge audit (teg_multirect_status.md open 1(b))
and its 1(a) resolution.

A `teg_mode over` multi-rect block declares its rects NOT internally
connected, so every rect needs external attachment by the bundle's PLACED
metal.  Historically the connection metal was a generation-only "bridge"
(Topology::bridge_segments) consumed by NOTHING downstream, so a selected
bridged candidate routed WITHOUT it and check_design reported Success on an
electrically open net (the §1.1 repro).  Open 1(a) resolved that at the
GENERATION side: the bridge is gone, replaced by ordinary segments — a
perpendicular connector leg per un-spanned rect of a rectilinear block, and
per-rect gap stubs joined through the trunk — which the planner, NUTS,
DetailedNUTS, the audits and report_wl all consume.  The §1.1 vehicle
therefore now audits CLEAN (test below: the flip is the fix's proof), while
the audit still fires wherever placed metal genuinely misses a rect —
including a candidate restored from a pre-change checkpoint, whose bridge is
still unrealized.

These tests drive the full flat pipeline through BudaSession and read
check_design's structured verdict, so they pin audit + emission end to end at
both placed stages.  check_topo deliberately does NOT carry the kind (it
feeds generation gates, dogleg trials and healer metrics — a reporting audit
must not start dropping candidates), so there is no topo-stage assertion
here.
"""
import contextlib
import io

import buda
import buda_cli


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


_TRACKS = [
    "def_track_pattern 4 0 (SIGNAL 2 2)x8",
    "def_track_pattern 5 0 (SIGNAL 2 2)x8",
]


def _lshape_cmds(teg):
    return [
        "add_block src 500 150 600 250",
        f"add_block L rect 0 0 100 400 rect 0 0 400 100{teg}",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS


def _pin_notch_trunk(s):
    """Pin the §1.1 shape: TRUNK_V@x250 crosses the base but not the tall arm
    (1-based index).  Pre-1(a) this candidate relied on a bridge nothing
    placed; now it carries a REAL H connector leg from the arm's right face
    (x=100) to the trunk."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_V@x250"):
            with contextlib.redirect_stdout(io.StringIO()):
                s.do_command(f"select_topology 1 {i + 1}")
            return c
    raise AssertionError("no TRUNK_V@x250 candidate generated for the L-shape")


def test_bridge_reliant_shape_now_routes_real_leg_and_audits_clean():
    # The §1.1 repro, FLIPPED by open 1(a) — the flip is the fix's proof.
    # The notch trunk used to emit a floating union-face bridge that nothing
    # placed (TEG_OPEN at both stages); generation now emits a real connector
    # leg instead, so the routed result reaches every rect of L and the audit
    # is clean — with the leg's metal visible in nuts_result/detailed_result
    # and counted by report_wl.
    s = _session(_lshape_cmds(" teg_mode over"))
    cand = _pin_notch_trunk(s)
    assert not dict(cand.bridge_segments), \
        "generation must no longer emit bridge_segments"
    # The connector leg: an H segment tapping L from the arm's right face
    # (x=100) to the trunk (x=250) at the arm's along-centre (y=200).
    legs = [seg for i, seg in enumerate(cand.segments)
            if seg.start.y == seg.end.y == 200
            and {seg.start.x, seg.end.x} == {100, 250}
            and any(bt is not None and bt.block_name == "L"
                    for bt in cand.seg_busterms.get(i, (None, None)))]
    assert legs, "expected the H connector leg from the tall arm to the trunk"

    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    n_segs = len(cand.segments)
    placed = [t for t in s.nuts_result.segments if t.bundle_id == 1]
    assert len(placed) == n_segs, "every segment incl. the leg must be placed"
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert len(s.detailed_result.net_segments) == 4 * n_segs, \
        "each of the 4 bits must realize every segment incl. the leg"
    assert s.detailed_result.num_unplaced == 0
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out

    # report_wl counts the leg's metal: the detailed total covers 4 bits x
    # (trunk + src stub + leg), so it must exceed 4x the leg-less span sum.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("report_wl")
    out = buf.getvalue()
    assert "total detailed WL" in out and "12 bit-wire(s)" in out, out


def test_trunk_inside_one_disjoint_rect_now_stubs_the_other_and_audits_clean():
    # Open 1 residual (i), FLIPPED — the flip is the fix's proof.  A trunk
    # INSIDE one disjoint rect connects Direct, and used to emit no metal at
    # all for the other rects ("OVER activates only for gap/partial-span
    # trunks"), so the routed result fired TEG_OPEN at both placed stages.
    # The Direct branch now covers disjoint blocks too: each rect outside
    # the landing contiguity component gets a stub from its locus-facing
    # perp face to the trunk (face → trunk, at the rect's along-centre), the
    # spine pre-extended to cover the junction — so the route reaches every
    # rect and the audit is clean.
    s = _session([
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 0 500 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:          # inside the lower rect: Direct
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no trunk-inside-lower-rect candidate found"
    trunk_y = int(pinned.type.split("@y")[1].split("+")[0])
    # The upper rect's stub: a V segment at the rect's along-centre (x=100)
    # from its bottom face (y=300) down to the trunk, tapping T.
    stubs = [seg for i, seg in enumerate(pinned.segments)
             if seg.start.x == seg.end.x == 100
             and {seg.start.y, seg.end.y} == {300, trunk_y}
             and any(bt is not None and bt.block_name == "T"
                     for bt in pinned.seg_busterms.get(i, (None, None)))]
    assert stubs, "expected the V stub from the upper rect down to the trunk"
    n_segs = len(pinned.segments)
    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    placed = [t for t in s.nuts_result.segments if t.bundle_id == 1]
    assert len(placed) == n_segs, "every segment incl. the stub must place"
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_one_sided_trunk_now_stubs_every_rect_and_audits_clean():
    # Open 1 residual (ii), FLIPPED: a trunk with ALL rects on the same side
    # used to fall back to the single best-rect stub, leaving every other
    # rect untouched (TEG_OPEN at the placed stages).  The gap branch now
    # covers the one-sided approach too: every rect gets its own stub from
    # its locus-facing perp face to the trunk, joined through the trunk.
    s = _session([
        "add_block T rect 0 200 200 300 rect 0 0 200 100 teg_mode over",
        "add_block src 400 500 500 600",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 300 < y < 500:        # above BOTH rects: one-sided
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no one-sided trunk candidate found"
    trunk_y = int(pinned.type.split("@y")[1].split("+")[0])
    # One stub per rect, each from that rect's locus-facing (top) face down
    # to the trunk.
    t_faces = sorted(
        min(seg.start.y, seg.end.y)
        for i, seg in enumerate(pinned.segments)
        if seg.start.x == seg.end.x
        and max(seg.start.y, seg.end.y) == trunk_y
        and any(bt is not None and bt.block_name == "T"
                for bt in pinned.seg_busterms.get(i, (None, None))))
    assert t_faces == [100, 300], (
        "expected one stub per rect (faces y=100 and y=300 to the trunk), "
        f"got {t_faces}")
    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def _chain_stub_faces(margin):
    """B-stub far-face y-coords for a trunk inside the chain's bottom rect:
    3-rect touching chain (bottom = landing) + a separated 4th rect."""
    fp = buda.Floorplan()
    fp.add_block_rects("B", [(200, 0, 300, 100), (200, 100, 300, 200),
                             (200, 200, 300, 300), (200, 500, 300, 600)])
    fp.set_block_teg_mode("B", buda.TegMode.OVER)
    fp.add_block("A", 0, 0, 100, 100)
    if margin:
        fp.set_global_corner_margin(*margin)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    cand = next(c for c in g.generate_candidates("A", ["B"])
                if c.type.startswith("TRUNK_H@y50"))
    return sorted(
        y
        for i, seg in enumerate(cand.segments)
        if seg.start.x == seg.end.x
        and any(bt is not None and bt.block_name == "B"
                for bt in cand.seg_busterms.get(i, (None, None)))
        for y in (seg.start.y, seg.end.y) if y != 50)


def test_adjacent_chain_gets_a_stub_per_rect_the_trunk_misses():
    # Limitation 2 RESOLVED (2026-08-27) — this assertion is the flip, and
    # the flip is the fix's proof.  It used to read `== [500]`: a rect merely
    # ABUTTING the trunk's landing rect was suppressed as "physically
    # contiguous", transitively along the chain, so only the genuinely
    # separated 4th rect got metal — while the placed TEG_OPEN audit reads
    # contact PER RECT and reported the three silent ones.  `teg_mode over`
    # declares the block's ROUTING does not join its rects; a shared edge is
    # a fact about its FOOTPRINT, and a footprint is not a wire (two macros
    # placed edge to edge are a contiguous footprint and separate metal).  So
    # every rect the trunk misses now gets its own stub — faces y=100, 200
    # (the touching chain) and y=500 (the separated rect) — and generation
    # and the audit read ONE predicate.
    assert _chain_stub_faces(None) == [100, 200, 500]


def test_margined_adjacent_chain_taps_inset_faces():
    # The #835 tap semantic survives the limitation-2 resolution: `corner_
    # margin` insets each rect INDEPENDENTLY, and a stub taps the INSET face
    # (y=110/210/510, not the physical 100/200/500) — tappable = inset
    # geometry.  What is GONE is the other half of the old pair, "touching =
    # physical geometry": that spelling existed only to keep a margin from
    # re-classifying an abutting pair as needing a connector (Codex P2 on
    # #841), and with the adjacency suppression itself removed there is no
    # touch graph left to read in either spelling.  The margined chain now
    # emits exactly what the margin-0 chain emits, one face inset further in.
    assert _chain_stub_faces((10, 10)) == [110, 210, 510]


def _pin_same_band_trunk(s, prefix="TRUNK_H@y", band=(0, 100)):
    """Pin the in-band trunk candidate (trunk inside both rects' shared perp
    band) and return it (1-based select)."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith(prefix):
            v = int(c.type.split("@")[1][1:].split("+")[0])
            if band[0] < v < band[1]:
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                return c
    raise AssertionError("no same-band trunk candidate found: "
                         + str([c.type for c in s.bundles[0].input.candidates]))


def _route_and_check_clean(s):
    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert not verdict["by_kind"], out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert not verdict["by_kind"], out
    assert s.detailed_result.num_unplaced == 0


def test_same_band_disjoint_sibling_routes_clean_via_spine_anchoring():
    # Final-state item 3, RESOLVED: a DISJOINT sibling sharing the trunk's
    # perp band (two rects side by side along the spine, trunk inside one) is
    # reached by SPINE-END ANCHORING — the spine's span is extended to LAND
    # exactly on the sibling's facing along-face, a real BUSTERM landing
    # annotate_endpoints tags, so NUTS holds the end via busterm_faces
    # span_cover the way it holds every face landing (the withdrawn bare
    # extension had no landing and was retracted; the withdrawn over-the-cell
    # anchoring stub tripped the #514 tap-overhang ANTENNA rule — this route
    # carries neither, and both audits are clean of every kind incl. ANTENNA).
    s = _session([
        "add_block T rect 300 0 400 100 rect 0 0 100 100 teg_mode over",
        "add_block src 600 0 700 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    # The spine itself is the connection metal: one segment landing on the
    # sibling's facing face (x=100) at one end and src's face at the other.
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert min(spine.start.x, spine.end.x) == 100
    _route_and_check_clean(s)


def test_same_band_sibling_on_far_side_survives_the_per_block_dedup():
    # The #823 shadow trap, measured while building the fix: with the sibling
    # BEYOND src the spine lands on block T at BOTH ends (landing rect's face
    # x=400 + sibling face x=800), and derive_conn_segs' per-BLOCK BUSTERM
    # dedup dropped the second landing — NUTS then had no face anchor at the
    # sibling end and RETRACTED the span to src's face ([400,650], TEG_OPEN
    # at both stages).  The dedup is now per FACE COORD for multi-rect blocks
    # (single-rect blocks keep the per-block rule byte-identically), so both
    # anchors hold and the route audits clean.
    s = _session([
        "add_block T rect 300 0 400 100 rect 800 0 900 100 teg_mode over",
        "add_block src 550 0 650 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    xs = sorted((spine.start.x, spine.end.x))
    assert xs == [400, 800], xs      # landing-rect face .. sibling face
    _route_and_check_clean(s)


def test_same_band_sibling_v_trunk_twin_routes_clean():
    # The V-trunk twin (x-band siblings): add_trunk is axis-parameterized, so
    # the anchoring covers both orientations — pinned here, not assumed.
    s = _session([
        "add_block T rect 0 300 100 400 rect 0 0 100 100 teg_mode over",
        "add_block src 0 600 100 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s, prefix="TRUNK_V@x")
    spine = next(seg for seg in c.segments if seg.start.x == seg.end.x)
    assert min(spine.start.y, spine.end.y) == 100
    _route_and_check_clean(s)


def test_same_band_three_rect_chain_taps_far_sibling_and_crosses_middle():
    # Three same-band disjoint rects, trunk inside the nearest-to-src one:
    # the spine extends to the FARTHEST sibling's facing face (x=100) and
    # crosses the middle sibling on the way (pass-through contact) — one
    # extension reaches the whole chain, every rect audits reached.
    s = _session([
        "add_block T rect 300 0 400 100 rect 150 0 250 100 rect 0 0 100 100"
        " teg_mode over",
        "add_block src 600 0 700 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert min(spine.start.x, spine.end.x) == 100
    _route_and_check_clean(s)


def test_same_band_extension_is_spine_metal_not_a_stub_under_min_stub_floor():
    # The min-stub floor governs perpendicular stubs (a too-short stub SKIPS
    # the trunk).  The same-band connection is collinear SPINE metal — no
    # stub exists — so a floor larger than the sibling gap (250 > 200) must
    # not reject the trunk, and the route still lands on the sibling's face.
    s = _session([
        "add_block T rect 300 0 400 100 rect 0 0 100 100 teg_mode over",
        "add_block src 600 0 700 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "set_min_stub_length 250",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert min(spine.start.x, spine.end.x) == 100
    _route_and_check_clean(s)


def test_same_band_margined_lands_on_inset_face_suppression_stays_physical():
    # The established margin semantic (#835/#841): tappable = INSET geometry,
    # touching = PHYSICAL geometry.  Under corner_margin dx 10 dy 10 the
    # spine lands on the sibling's INSET facing face (x=90), inside the
    # physical rect (0..100) — the audit's contact predicate reads placed
    # metal against the physical extent, so the inset landing is contact —
    # and the anchoring's reached-component still reads the physical touch
    # graph, so a margin cannot re-classify an adjacent pair as needing an
    # extension.
    s = _session([
        "corner_margin dx 10 dy 10",
        "add_block T rect 300 0 400 100 rect 0 0 100 100 teg_mode over",
        "add_block src 600 0 700 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert min(spine.start.x, spine.end.x) == 90    # inset face of the sibling
    _route_and_check_clean(s)


def test_mixed_rect_set_same_band_sibling_anchors_despite_overlap():
    # Codex P2 on #845, measured before the fix: a MIXED rect set — an
    # OVERLAPPING pair (base + arm, one rectilinear polygon) PLUS
    # a DISJOINT rect sharing the trunk's perp band — skipped the anchoring
    # pass entirely (it was gated on the block-level classification), and the
    # rectilinear branch emits legs only for CROSS-band rects, so the
    # separated sibling stayed unreached (spine [500,700], TEG_OPEN 1 at
    # nuts / 4 at dnuts).  The pass now runs on EVERY OVER multi-rect block
    # and decides reached/contiguous PER RECT (abutment OR strict overlap =
    # connected), so the sibling anchors while the overlapping component —
    # one connected piece of metal — never pulls the spine (a FULLY
    # connected rectilinear block remains a no-op by construction).
    s = _session([
        "add_block T rect 300 0 500 100 rect 300 0 350 300 rect 0 0 100 100"
        " teg_mode over",
        "add_block src 700 0 800 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert min(spine.start.x, spine.end.x) == 100   # lands on the sibling's face
    _route_and_check_clean(s)


def test_mixed_rect_set_cross_band_rect_keeps_the_rectilinear_leg():
    # The interaction control beside the mixed-set fix: the same overlapping
    # pair with the separated rect CROSS-band (above the trunk) is served by
    # the rectilinear branch's perpendicular connector leg, exactly as
    # before — one V leg tapping the rect's near face (y=200), NO same-band
    # anchor extension, no double emission for one rect.
    s = _session([
        "add_block T rect 300 0 500 100 rect 300 0 350 300 rect 0 200 100 300"
        " teg_mode over",
        "add_block src 700 0 800 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    legs = [seg for seg in c.segments if seg.start.x == seg.end.x]
    assert len(legs) == 1, [(s_.start.x, s_.start.y, s_.end.x, s_.end.y)
                            for s_ in c.segments]
    ys = sorted((legs[0].start.y, legs[0].end.y))
    assert ys[1] == 200, ys                          # taps the rect's near face
    _route_and_check_clean(s)


def test_same_band_adjacent_pair_anchors_the_spine_and_audits_clean():
    # Limitation 2's SAME-BAND form, FLIPPED (2026-08-27).  Two ADJACENT rects
    # side by side along the spine, trunk Direct inside the near one: the
    # anchoring pass used to treat the far rect as "reached" because it abuts
    # the landing rect (its reached-component expanded over the abutment
    # graph), so the spine stopped at x=400 and the per-rect TEG_OPEN audit
    # reported the untouched rect.  The reached set is now CONTACT and nothing
    # else — the same `axis_touches_rect` the audit reads — so the spine is
    # extended to LAND on the sibling's facing face at x=300 and the route is
    # clean at both placed stages.  Used to assert [400, 600] + TEG_OPEN >= 1.
    s = _session([
        "add_block T rect 300 0 400 100 rect 200 0 300 100 teg_mode over",
        "add_block src 600 0 700 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)
    spine = next(seg for seg in c.segments if seg.start.y == seg.end.y)
    assert sorted((spine.start.x, spine.end.x)) == [300, 600]   # anchored
    _route_and_check_clean(s)


def test_cross_band_adjacent_pair_stubs_the_second_rect_and_audits_clean():
    # Limitation 2's CROSS-BAND form, the shape the two dirty vehicles used to
    # sit on (test_teg_resume / test_teg_thru_census): two STACKED adjacent
    # rects, an H trunk Direct inside the lower one.  Generation emits a V stub
    # from the upper rect's bottom face (y=100) down to the trunk — metal that
    # necessarily runs over the LOWER rect, which is exactly what routing OVER
    # a block means — and both placed audits are clean where they reported
    # TEG_OPEN (1 at nuts, 4 at dnuts).
    #
    # It also pins the ANTENNA companion: that stub overhangs its junction
    # entirely over block 'r2', and the #514 tap-overhang rule asks whether
    # "the block stays covered without it" — a BLOCK-level question `teg_mode
    # over` revokes.  Judged per RECT (through the same contact predicate) the
    # piece is load-bearing, so it is not an antenna.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r2 rect 200 0 300 100 rect 200 100 300 200 teg_mode over",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r2.b",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_same_band_trunk(s)          # TRUNK_H@y50: Direct inside rect#0
    stubs = [seg for i, seg in enumerate(c.segments)
             if seg.start.x == seg.end.x
             and sorted((seg.start.y, seg.end.y)) == [50, 100]
             and any(bt is not None and bt.block_name == "r2"
                     for bt in c.seg_busterms.get(i, (None, None)))]
    assert stubs, [(g.start.x, g.start.y, g.end.x, g.end.y) for g in c.segments]
    _route_and_check_clean(s)


_MST_OVER_CMDS = [
    "add_block src 0 0 100 100",
    "add_block r1 300 300 400 400",
    "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
    "add_block r3 300 600 400 700",
    "def_layer 4 M4 H TOP 0",
    "def_layer 5 M5 V TOP 0",
    "add_bus d[4] src.tx r1.a,r2.b,r3.c",
    "run_bundler STRICT",
    "generate_topologies",
]


def test_mst_on_over_block_now_attaches_every_rect_and_audits_clean():
    # Final-state limitation 1, FLIPPED — the flip is the fix's proof.  An
    # MST edge lands on the closest rect pair only, so the OVER receiver's
    # far rect (rect#1) used to go unreached — TEG_OPEN at both placed
    # stages.  `add_mst_teg_attachments` now runs on the FINISHED tree
    # (after complete_relay_junctions) and attaches every still-unreached
    # rect with real metal: here a single H T-stub from rect#1's left face
    # (x=900, tapping r2) onto the r1→r2 edge's V leg — so the routed
    # result reaches every rect and both audits are clean.
    s = _session(_MST_OVER_CMDS + _TRACKS)
    w = s.bundles[0]
    pin, cand = next((i, c) for i, c in enumerate(w.input.candidates)
                     if c.type.startswith("MST_"))
    # The attachment stub: taps r2 from rect#1's face (x=900), edge_id -1 so
    # ripup's per-edge flips never mistake it for an MST leg.
    stubs = [seg for i, seg in enumerate(cand.segments)
             if seg.start.x == 900 and seg.edge_id == -1
             and any(bt is not None and bt.block_name == "r2"
                     for bt in cand.seg_busterms.get(i, (None, None)))]
    assert stubs, "expected the attachment stub from rect#1's face at x=900"
    n_segs = len(cand.segments)
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    placed = [t for t in s.nuts_result.segments if t.bundle_id == 1]
    assert len(placed) == n_segs, "every segment incl. the stub must place"
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_mst_edge_spanning_the_far_rect_gets_no_redundant_stub():
    # The control that must NOT change: with a fourth receiver r4 past the
    # OVER block, the r2→r4 MST edge SPANS rect#1 — pass-through contact IS
    # attachment — so the attachment pass must emit nothing (a spanned rect
    # is already reached) and the route still audits clean at both stages.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "add_block r4 1200 0 1300 100",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c,r4.d",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pin, cand = next((i, c) for i, c in enumerate(w.input.candidates)
                     if c.type.startswith("MST_"))
    # No attachment metal: every segment belongs to an MST edge or the relay
    # completion — nothing taps r2 off an edge_id -1 stub at rect#1's faces.
    extra = [seg for i, seg in enumerate(cand.segments)
             if seg.edge_id == -1
             and any(bt is not None and bt.block_name == "r2"
                     for bt in cand.seg_busterms.get(i, (None, None)))]
    assert not extra, "spanned rect#1 is already reached — no stub may be added"
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts",
                "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_mst_on_adjacent_rect_over_block_now_attaches_and_audits_clean():
    # Limitation 2's MST form, FLIPPED (2026-08-27).  The attachment pass's
    # "reached" set used to expand transitively over the abutment graph, so a
    # rect merely touching a reached one got no attachment while the placed
    # TEG_OPEN audit — reading contact per rect — reported it.  "Reached" is
    # now CONTACT alone (`seg_touches_rect`, the audit's own predicate), so
    # rect#1 gets a real H T-stub from its left face (x=600) onto the r1->r2
    # edge's V leg and the route is clean at both stages.  Used to assert "no
    # attachment metal" + TEG_OPEN >= 1 naming rect#1.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 600 0 700 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pin, cand = next((i, c) for i, c in enumerate(w.input.candidates)
                     if c.type.startswith("MST_"))
    stubs = [seg for i, seg in enumerate(cand.segments)
             if seg.edge_id == -1 and seg.start.x == 600
             and any(bt is not None and bt.block_name == "r2"
                     for bt in cand.seg_busterms.get(i, (None, None)))]
    assert stubs, "expected the attachment stub from rect#1's face at x=600"
    n_segs = len(cand.segments)
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    placed = [t for t in s.nuts_result.segments if t.bundle_id == 1]
    assert len(placed) == n_segs, "every segment incl. the stub must place"
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_thru_block_is_exempt_by_design():
    # Same geometry, default thru: internal equivalence is the declared
    # meaning, so the audit must stay silent whatever candidate wins.
    s = _session(_lshape_cmds(""))
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_gap_stub_places_clean_after_retraction_fix():
    # The disjoint-gap OVER branch stubs BOTH rects to the trunk, so when the
    # stubs place as generated, connectivity holds through the trunk and the
    # audit passes (its union-face bridge is redundant metal, not a missing
    # link — teg_multirect_status.md §1.3, last paragraph).
    #
    # They used to NOT place as generated: the far gap-stub was emitted
    # trunk → face, so emit_tap_segment seeded its busterm on the TRUNK end;
    # derive_conn_segs' per-block BUSTERM dedup let that bogus tap shadow the
    # real face-end annotation, and NUTS — left with no face anchor —
    # retracted the stub to the trunk (span [150,158] against a face at 300,
    # both orientations), firing BUSTERM_FACE + ANTENNA + TEG_OPEN here.
    # FIXED (claude/teg-gap-stub-fix): the far stub is emitted face → trunk
    # like every other stub, so this test now pins the clean expectation —
    # the flip its earlier comment promised as the fix's proof.  The direct
    # annotation/placement regression is
    # test_teg_gap_stub_annotation.py (both orientations).
    s = _session([
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 150 500 250",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # Pin an H trunk in the gap (y 100..300): the gap-stub pair shape (which,
    # since open 1(a), carries no bridge — the stubs ARE the connection).
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y"):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 100 < y < 300:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no gap-trunk candidate found"
    assert not dict(pinned.bridge_segments), \
        "generation must no longer emit bridge_segments"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    assert verdict["by_kind"].get("BUSTERM_FACE", 0) == 0, out


def test_all_span_trunk_touches_every_rect_no_teg_open():
    # A trunk crossing EVERY rect of the OVER block (all_span at generation:
    # no bridge emitted, none needed) must audit clean: placed contact per
    # rect is the predicate, not bridge presence.
    s = _session([
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "add_block src 500 20 600 80",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # An H trunk at y<100 crosses BOTH rects (base y 0..100, arm y 0..400).
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y") and not dict(c.bridge_segments):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no bridgeless low trunk candidate found"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_check_topo_does_not_carry_teg_open():
    # The kind is placed-stage only: check_topo feeds generation gates and
    # healer metrics, so it must not report TEG_OPEN even on a candidate that
    # genuinely leaves a rect unreached (an MST candidate on an ADJACENT-rect
    # OVER block — the attachment pass suppresses contiguous rects, so the
    # far rect stays untouched and the placed stages fire, see the loud test
    # above); the pool must not shrink over a reporting audit.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 600 0 700 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    cand = next(c for c in s.bundles[0].input.candidates
                if c.type.startswith("MST_"))
    ct = buda.ConnTopology()
    ct.build(cand, s.fp)
    res = buda.check_topo(ct, cand, s.fp, 1)
    kinds = {v.kind for v in res.violations}
    assert buda.ViolationKind.TEG_OPEN not in kinds


# ── the two Codex P1 refinements (#821 review) ────────────────────────────────

def _lshape_fp_over():
    fp = buda.Floorplan()
    fp.add_block_rects("L", [(0, 0, 100, 400),      # tall arm
                             (0, 0, 400, 100)])     # wide base
    fp.set_block_teg_mode("L", buda.TegMode.OVER)
    fp.add_block("src", 500, 150, 600, 250)
    return fp


def _lshape_candidate(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    cands = g.generate_candidates("src", ["L"])
    topo = cands[0]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return topo, ct


def _layers():
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    return ls


def _h_seg_indices(ct):
    return [i for i, cs in enumerate(ct.segs()) if cs.horiz]


def _wire(si, bit, y, x_lo, x_hi):
    ns = buda.NetSegment()
    ns.bundle_id = 1
    ns.seg_idx = si
    ns.bit_index = bit
    ns.layer = 4
    ns.track_position = float(y)
    ns.span_lo, ns.span_hi = float(x_lo), float(x_hi)
    ns.width = 1.0
    return ns


def test_per_bit_metal_is_not_pooled():
    # Codex P1 (#821): bit 0's wire touches only the base (y=50, x from 150 —
    # right of the arm) and bit 1's only the arm (y=150 — above the base).
    # Pooled metal touches both rects and would audit clean; per-bit each net
    # misses a rect, so BOTH bits must fire.
    fp = _lshape_fp_over()
    topo, ct = _lshape_candidate(fp)
    h = _h_seg_indices(ct)
    assert h, "need an H segment to host the fabricated wires"
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [
        _wire(h[0], 0, 50, 150, 500),    # base only (arm is x<=100)
        _wire(h[0], 1, 150, 50, 500),    # arm only (base is y<=100)
    ]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 2)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert {v.bit_index for v in teg} == {0, 1}, [v.message for v in teg]


def test_touched_but_split_islands_fire_teg_open():
    # Codex P1 (#821): one bit, two wires on segments that are NOT joined by
    # any placed metal of the bit (their structural junction runs through a
    # third segment whose wire is absent) — every rect is touched, but the
    # contacts sit in different metal islands, so joining them would rely on
    # the block interior OVER revokes.  island_roots cannot see this (it
    # unions same-BLOCK taps); the audit must.
    fp = _lshape_fp_over()
    # Search the pool for a candidate holding two H segments with NO direct
    # SEG conn (a Z / V-trunk shape: two H stubs joined only through the V
    # trunk) — the trunk's wire is then left out of the group, so the two
    # stub wires are genuine islands.
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    topo = ct = None
    h = []
    for cand in g.generate_candidates("src", ["L"]):
        c = buda.ConnTopology()
        c.build(cand, fp)
        hh = [i for i, cs in enumerate(c.segs()) if cs.horiz]
        for a in range(len(hh)):
            for b in range(a + 1, len(hh)):
                direct = any(cn.kind == buda.SegConnKind.SEG and
                             cn.seg_idx == hh[b]
                             for cn in c.segs()[hh[a]].conns)
                if not direct:
                    topo, ct, h = cand, c, [hh[a], hh[b]]
                    break
            if topo is not None:
                break
        if topo is not None:
            break
    assert topo is not None, "no candidate with two unjoined H segments"
    si2 = h[1]
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [
        _wire(h[0], 0, 150, 50, 300),    # island A: arm only
        _wire(si2, 0, 50, 150, 500),     # island B: base only
    ]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 1)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert teg and any("islands" in v.message for v in teg), \
        [v.message for v in teg]


# ── the two Codex P1/P2 findings on the 1(a) emission (#828 review) ───────────

def test_spine_slide_stays_inside_crossed_rect_despite_leg_tap():
    # Codex P1 (#828): the connector leg's BUSTERM tap on the un-spanned rect
    # used to mark the whole OVER block explicitly connected, and
    # tighten_passthrough skips explicitly-connected blocks — so the spine
    # crossing the OTHER rect purely by pass-through lost its clamp and NUTS
    # could slide it beyond that rect's far face (measured slide [240,380]
    # against a base ending at x=300): a routed TEG_OPEN under congestion.
    # OVER blocks are now explicitly connected PER RECT: the tapped arm frees
    # nothing about the crossed base, whose coverer stays clamped to its
    # extent.  Vehicle: an L shifted off the die edge so the V spine crosses
    # the base FULLY (both spine endpoints beyond it — no face tap on the
    # base, unlike the §1.1 vehicle where the spine endpoint taps the base
    # top face and masks the skip).
    fp = buda.Floorplan()
    fp.add_block_rects("L", [(0, 200, 100, 700),      # tall arm (tapped by leg)
                             (0, 200, 300, 300)])     # base band (crossed only)
    fp.set_block_teg_mode("L", buda.TegMode.OVER)
    fp.add_block("src", 400, 500, 500, 600)
    fp.add_block("dn", 120, 0, 220, 60)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    cand = next(c for c in g.generate_candidates("src", ["L", "dn"])
                if c.type.startswith("TRUNK_V@x260"))
    ct = buda.ConnTopology()
    ct.build(cand, fp)
    spine = ct.segs()[0]
    assert not spine.horiz and spine.perp_pos == 260
    # The base's x-extent is [0, 300]; unclamped the window reached 380.
    assert spine.perp_hi <= 300, (spine.perp_lo, spine.perp_hi)
    # The leg keeps its own freedom (clamping is per untapped rect, not per
    # block): it slides along the arm's y-extent through its tap window.
    leg = next(cs for i, cs in enumerate(ct.segs())
               if cs.horiz and any(bt is not None and bt.block_name == "L"
                                   for bt in cand.seg_busterms.get(i, (None, None))))
    assert leg.perp_hi > 300, (leg.perp_lo, leg.perp_hi)


def test_spine_seat_interval_clamped_at_placement():
    # Placement-level half of the P1 pin: route the same through-crossing
    # shape and assert the spine's NUTS seat INTERVAL — the hard bound the
    # placer obeys under ANY congestion (placing outside it is counted as a
    # violation) — ends at the crossed base's far face, and the audit is
    # clean.  Before the per-rect fix the seat window extended to x=380.
    s = _session([
        "add_block L rect 0 200 100 700 rect 0 200 300 300 teg_mode over",
        "add_block src 400 500 500 600",
        "add_block dn 120 0 220 60",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx,dn.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pin = next(i for i, c in enumerate(w.input.candidates)
               if c.type.startswith("TRUNK_V@x260"))
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    spine = next(t for t in s.nuts_result.segments
                 if t.bundle_id == 1 and t.seg_idx == 0)
    assert not spine.horiz
    assert spine.interval_hi <= 300, (spine.interval_lo, spine.interval_hi)
    assert 0 <= spine.track_position <= 300
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_connector_leg_respects_min_stub_length():
    # Codex P2 (#828): the min-stub-length floor was only checked for
    # has_stub entries, so the direct/partial-span connector-leg path
    # bypassed it — a locus lying less than the floor outside an un-spanned
    # rect emitted an illegally short leg no ordinary stub may have.  The
    # floor now applies to each leg with the ordinary stub path's exact
    # convention: a too-short leg SKIPS the trunk locus.  On the §1.1
    # L-shape the notch trunk's leg is 150 units (x=100 arm face -> x=250
    # trunk), so a floor of 100 keeps the candidate and a floor of 200
    # rejects it (while looser trunks survive the floor on their own stubs).
    def types_at(min_stub):
        fp = buda.Floorplan()
        fp.add_block_rects("L", [(0, 0, 100, 400), (0, 0, 400, 100)])
        fp.set_block_teg_mode("L", buda.TegMode.OVER)
        fp.add_block("src", 500, 150, 600, 250)
        if min_stub is not None:
            fp.set_min_stub_length(min_stub)
        g = buda.TopologyGenerator(fp)
        g.set_layer_ids(4, 5)
        return [c.type for c in g.generate_candidates("src", ["L"])]

    assert any(t.startswith("TRUNK_V@x250") for t in types_at(None))   # 150 >= 20
    assert any(t.startswith("TRUNK_V@x250") for t in types_at(100))    # 150 >= 100
    assert not any(t.startswith("TRUNK_V@x250") for t in types_at(200))  # 150 < 200


def test_joined_contacts_audit_clean():
    # Control for the island verdict: ONE wire touching both rects (an H wire
    # at y=50 from x=50 crosses arm and base alike) is one island — no
    # TEG_OPEN, whatever else the fabricated placement violates.
    fp = _lshape_fp_over()
    topo, ct = _lshape_candidate(fp)
    h = _h_seg_indices(ct)
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [_wire(h[0], 0, 50, 50, 500)]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 1)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert not teg, [v.message for v in teg]


# ── BITRUNK per-rect selection + TEG connection metal ─────────────────────────
#    (teg_multirect_status.md Final-state limitation 4, RESOLVED)
#
# The datapath trees used to work entirely on `orig_bbox`: no rect selection —
# a "tap" could land on the UNION bbox face over a GAP, on no metal at all —
# and no TEG connection metal, so an OVER rect the tree missed was left
# electrically open (LOUD via TEG_OPEN, which is what made the scoping
# acceptable).  Both families now read the trunk generator's own two rules
# through the shared predicates: `best_rect` picks the tap rect, a rect sharing
# the spine's perp band is reached by GROWING the spine over its along-centre,
# and every other rect gets one perpendicular stub from its facing face
# (`plan_teg_attachments` — one rule for the legacy rungs and, axis-transposed,
# the two-level branches).

_BITRUNK_CMDS = [
    "add_block src 0 0 100 100",
    "add_block r1 300 300 400 400",
    "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
    "add_block r3 300 600 400 700",
    "def_layer 4 M4 H TOP 0",
    "def_layer 5 M5 V TOP 0",
    "add_bus d[4] src.tx r1.a,r2.b,r3.c",
    "run_bundler STRICT",
]


def _pin_type(s, want):
    """Pin bundle 1's candidate whose type equals or starts with `want`."""
    for i, c in enumerate(s.bundles[0].input.candidates):
        if c.type == want or c.type.startswith(want):
            with contextlib.redirect_stdout(io.StringIO()):
                s.do_command(f"select_topology 1 {i + 1}")
            return c
    raise AssertionError(f"no {want} candidate: "
                         + str([c.type for c in s.bundles[0].input.candidates]))


def _run_cmds(s, *cmds):
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)


def _seg_touches(g, r):
    """topology.h's seg_touches_rect, in Python (the audit's own rule)."""
    horiz = (g.start.y == g.end.y)
    perp = g.start.y if horiz else g.start.x
    a_lo = min(g.start.x, g.end.x) if horiz else min(g.start.y, g.end.y)
    a_hi = max(g.start.x, g.end.x) if horiz else max(g.start.y, g.end.y)
    p1, p2 = (r[1], r[3]) if horiz else (r[0], r[2])
    a1, a2 = (r[0], r[2]) if horiz else (r[1], r[3])
    return p1 <= perp <= p2 and a_lo <= a2 and a_hi >= a1


def test_bitrunk_on_over_block_routes_clean_via_per_rect_metal():
    # WAS test_bitrunk_on_over_block_fires_teg_open_end_to_end, which asserted
    #     verdict["by_kind"].get("TEG_OPEN", 0) >= 1
    #     and "rect#1 (900,0)-(1000,100)" in out
    # at BOTH placed stages — the guarantee that made the bbox-only scoping
    # acceptable (loud, not silent).  Limitation 4 is resolved, so the same
    # geometry ROUTES the rect instead of reporting it: r2's rects both share
    # the y=50 rung's perp band, so the rung itself IS the connection metal and
    # its span grows from x 750 (the union-bbox pin) to x 950 (rect#1's
    # along-centre), CROSSING both rects.
    s = _session(_BITRUNK_CMDS + ["generate_topologies"] + _TRACKS)
    c = _pin_type(s, "BITRUNK_H")
    rung = next(g for g in c.segments if g.start.y == g.end.y == 50)
    assert (min(rung.start.x, rung.end.x),
            max(rung.start.x, rung.end.x)) == (50, 950), (
        (rung.start.x, rung.start.y), (rung.end.x, rung.end.y))
    _run_cmds(s, "run_planner", "run_nuts")
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    # ...and the PLACED span keeps the reach (the NUTS half of the fix — see
    # test_over_block_crossed_twice_keeps_both_pass_through_anchors).
    placed = next(t for t in s.nuts_result.segments
                  if t.bundle_id == 1 and t.horiz and t.track_position <= 100)
    assert max(placed.span_lo, placed.span_hi) >= 900, (placed.span_lo,
                                                        placed.span_hi)
    _run_cmds(s, "run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    assert s.detailed_result.num_unplaced == 0


def test_bitrunk_off_band_rect_gets_its_own_perpendicular_stub():
    # The other half of the rule: a rect the rung's band does NOT cover cannot
    # be reached by growing the rung, so it gets ONE perpendicular stub from
    # its rung-facing perp face at its along-centre — FACE → rung, the #823
    # tap-seed orientation.  Pre-fix the block got a single union-bbox stub and
    # rect#1 was untouched.
    cmds = list(_BITRUNK_CMDS)
    cmds[2] = ("add_block r2 rect 500 0 600 100 "
               "rect 900 200 1000 300 teg_mode over")
    s = _session(cmds + ["generate_topologies"] + _TRACKS)
    c = _pin_type(s, "BITRUNK_H")
    stubs = [g for g in c.segments if g.start.x == g.end.x == 950]
    assert len(stubs) == 1, [((g.start.x, g.start.y), (g.end.x, g.end.y))
                             for g in c.segments]
    # it STARTS on rect#1's rung-facing perp face (y=200) and ends on a rung
    rungs = {g.start.y for g in c.segments if g.start.y == g.end.y}
    assert stubs[0].start.y == 200, (stubs[0].start.y, stubs[0].end.y)
    assert stubs[0].end.y in rungs, (stubs[0].end.y, rungs)
    # the busterm seed sits on the FACE end, never the rung end
    si = c.segments.index(stubs[0])
    bt = c.seg_busterms.get(si, (None, None))
    assert bt[0] is not None and bt[0].block_name == "r2", bt
    assert bt[1] is None, bt
    _run_cmds(s, "run_planner", "run_nuts", "run_detailed_nuts")
    for stage in ("nuts", "dnuts"):
        verdict, out = _check(s, stage)
        assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_bitrunk_v_mirror_takes_the_same_treatment():
    # The rule is written once against Axis, so the opt-in BITRUNK_V mirror
    # (V rungs + an H backbone) gets it too — test-pinned, not assumed.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 0 500 100 600 rect 0 900 100 1000 teg_mode over",
        "add_block r3 600 300 700 400",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies multi_trunk",
    ] + _TRACKS)
    c = _pin_type(s, "BITRUNK_V")
    for r in ((0, 500, 100, 600), (0, 900, 100, 1000)):
        assert any(_seg_touches(g, r) for g in c.segments), (
            r, [((g.start.x, g.start.y), (g.end.x, g.end.y))
                for g in c.segments])
    _run_cmds(s, "run_planner", "run_nuts", "run_detailed_nuts")
    for stage in ("nuts", "dnuts"):
        verdict, out = _check(s, stage)
        assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_two_level_bitrunk_over_block_gets_per_rect_metal():
    # The two-level trees share the SAME rule through the transposed axis: a
    # leaf stub runs along the ROOT axis at the rect's perp centre off a
    # branch, which is the legacy rung shape read through Axis{!root_horiz}.
    # Pre-fix the leaf stub started at the union-bbox face at the union perp
    # centre — a coordinate that can sit in a gap — and no other rect got metal.
    s = _session([
        "add_block src 0 400 80 480",
        "add_block a 200 400 280 480",
        "add_block b 600 400 680 480",
        "add_block c 1000 400 1080 480",
        "add_block d rect 1400 400 1480 480 rect 1400 700 1480 780 "
        "teg_mode over",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus q[4] src.tx a.rx,b.rx,c.rx,d.rx",
        "run_bundler STRICT",
        "generate_topologies multi_trunk",
    ] + _TRACKS)
    cands = [c for c in s.bundles[0].input.candidates
             if c.type.startswith("BITRUNK_HVH")
             or c.type.startswith("BITRUNK_VHV")]
    assert cands, [c.type for c in s.bundles[0].input.candidates]
    # every emitted two-level tree carries metal touching BOTH of d's rects
    for cand in cands:
        for r in ((1400, 400, 1480, 480), (1400, 700, 1480, 780)):
            assert any(_seg_touches(g, r) for g in cand.segments), (
                cand.type, r,
                [((g.start.x, g.start.y), (g.end.x, g.end.y))
                 for g in cand.segments])


def test_bitrunk_thru_multirect_block_gets_one_best_rect_stub():
    # CONTROL: `thru` declares the block's own routing joins its rects, so it
    # gets exactly ONE stub — but to the BEST RECT (the rung-facing one), not
    # to the union bbox face over the gap.  Per-rect SELECTION applies to THRU
    # blocks too; the TEG connection metal does not.
    #
    # What the selection half actually COST is measured here: pre-fix the
    # union-bbox tap landed on no rect at all, so the generation coverage gate
    # dropped the whole candidate — `[TopoGen] dropped 1 candidate(s) (0
    # feedthru-relay, first open: BITRUNK_H missing block 'r2')` — and a
    # multi-rect design silently lost the datapath shape.  This test fails
    # pre-fix with "no BITRUNK_H candidate".
    cmds = list(_BITRUNK_CMDS)
    cmds[2] = "add_block r2 rect 500 0 600 100 rect 900 200 1000 300"
    s = _session(cmds + ["generate_topologies"] + _TRACKS)
    c = _pin_type(s, "BITRUNK_H")
    r2_stubs = [i for i in range(len(c.segments))
                if any(bt is not None and bt.block_name == "r2"
                       for bt in c.seg_busterms.get(i, (None, None)))]
    assert len(r2_stubs) <= 1, r2_stubs
    # no TEG metal was invented for the thru block
    assert not any(g.start.x == g.end.x == 950 for g in c.segments), [
        ((g.start.x, g.start.y), (g.end.x, g.end.y)) for g in c.segments]


def test_bitrunk_single_rect_design_is_byte_identical():
    # CONTROL / scope proof: the change is reachable ONLY through
    # `Busterm::rects`, so a single-rect design generates the historical
    # geometry exactly (best_rect over bt_all_rects returns {orig_bbox}, and
    # `rects.empty()` keeps the pin's along-coordinate).
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 500 0 600 100",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    c = _pin_type(s, "BITRUNK_H")
    assert [((g.start.x, g.start.y), (g.end.x, g.end.y))
            for g in c.segments] == [((50, 50), (550, 50)),
                                     ((50, 650), (550, 650)),
                                     ((300, 50), (300, 650)),
                                     ((350, 300), (350, 50))]


def test_bitrunk_per_rect_stubs_never_duplicate_collinearly():
    # Two rects sharing an along-centre on the SAME side of the rung: the
    # farther stub crosses the nearer rect, so planning FARTHEST-face-first and
    # skipping a rect a planned stub already TOUCHES (`seg_touches_rect`, the
    # audit's own predicate) yields ONE stub, not the collinear containing pair
    # the trim knobs exist to remove.
    # Geometry: r2's rung is r1's (its own pin sits at p_mid), so BOTH rects
    # lie above it — the containment case.
    cmds = list(_BITRUNK_CMDS)
    cmds[2] = ("add_block r2 rect 900 500 1000 600 "
               "rect 900 800 1000 900 teg_mode over")
    cmds[3] = "add_block r3 300 1500 400 1600"
    s = _session(cmds + ["generate_topologies"] + _TRACKS)
    c = _pin_type(s, "BITRUNK_H")
    stubs = [g for g in c.segments if g.start.x == g.end.x == 950]
    assert len(stubs) == 1, [((g.start.x, g.start.y), (g.end.x, g.end.y))
                             for g in c.segments]
    # the ONE stub reaches the FAR rect and CROSSES the near one on the way
    for r in ((900, 500, 1000, 600), (900, 800, 1000, 900)):
        assert _seg_touches(stubs[0], r), (
            r, ((stubs[0].start.x, stubs[0].start.y),
                (stubs[0].end.x, stubs[0].end.y)))
    _run_cmds(s, "run_planner", "run_nuts", "run_detailed_nuts")
    for stage in ("nuts", "dnuts"):
        verdict, out = _check(s, stage)
        assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_trunk_mst_hybrid_on_over_block_still_fires_teg_open():
    # THE REMAINING LOUD SHAPE (teg_multirect_status.md Final-state
    # limitation 8, opened by this work): a `TRUNK_*+MST` hybrid re-derives its
    # spine from the SURVIVING branch blocks and drops the seed trunk's TEG
    # connection metal with it — measured on this very geometry, the seed
    # `TRUNK_H@y100` spans x 100..900 (spine-end anchored onto rect#1's face)
    # while `TRUNK_H+MST@y100` spans x 100..500, and `TRUNK_V@x600`'s per-rect
    # stub (900,50)-(600,50) is simply absent from `TRUNK_V+MST@x600`.  The
    # route is LOUD, not silent — the same guarantee that made BITRUNK's
    # bbox-only scoping acceptable — and this is where the two dirty vehicles
    # (test_teg_resume, test_teg_thru_census) now sit.
    s = _session(_BITRUNK_CMDS + ["generate_topologies"] + _TRACKS)
    _pin_type(s, "TRUNK_H+MST@y100")
    _run_cmds(s, "run_planner", "run_nuts")
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "rect#1 (900,0)-(1000,100)" in out, out
    _run_cmds(s, "run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out


def test_over_block_crossed_twice_keeps_both_pass_through_anchors():
    # The NUTS half, isolated: `tighten_spans_to_reach` elects exactly ONE
    # pass-through anchor per (bundle, BLOCK) — anchoring every crossing keeps
    # phantom span for nothing (b44).  That rests on a block's rects being
    # interchangeable covers, which is exactly what `teg_mode over` REVOKES,
    # so an OVER multi-rect block's crossings carry their own anchor
    # (`PassthruCrossing::own_anchor`).  Without it the generated rung reached
    # x=950 and the PLACED span came back clipped to 600 — rect#0's far face,
    # i.e. the one elected crossing — so TEG_OPEN fired on placed geometry
    # while the candidate was correct.
    s = _session(_BITRUNK_CMDS + ["generate_topologies"] + _TRACKS)
    _pin_type(s, "BITRUNK_H")
    _run_cmds(s, "run_planner", "run_nuts")
    rung = next(t for t in s.nuts_result.segments
                if t.bundle_id == 1 and t.horiz and t.track_position <= 100)
    r2 = [p for p in rung.passthru_spans if p.block == "r2"]
    assert len(r2) == 2, [(p.block, p.rect, p.along_lo, p.along_hi)
                          for p in rung.passthru_spans]
    assert {p.rect for p in r2} == {0, 1}
    # ...while an ordinary (single-rect) pass-through keeps ONE anchor
    assert len([p for p in rung.passthru_spans if p.block == "src"]) == 1
