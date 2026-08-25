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


def test_adjacent_chain_is_suppressed_and_separated_rect_still_stubbed():
    # The Direct-branch suppression rules, at generation: a rect ADJACENT to
    # the trunk's landing rect (touching edges, positive shared edge — the
    # feature's adjacency rule) is physically continuous with it and gets no
    # connection metal, TRANSITIVELY along a chain of touching rects — while
    # a genuinely separated rect of the same block still gets its stub.
    # Exactly one stub: from the separated rect's bottom face (y=500) down to
    # the trunk; the touching chain (faces 100/200/300) emits nothing.
    assert _chain_stub_faces(None) == [500]


def test_margined_adjacent_chain_keeps_physical_suppression_inset_taps():
    # Codex P2 on #841, measured before the fix: `corner_margin` insets each
    # rect INDEPENDENTLY (#835), so the chain's physically-abutting rects no
    # longer touched in the suppression component's geometry and each grew a
    # connector to metal it is already continuous with (the margined pair
    # emitted a stub from the lower rect's inset face).  The touch graph now
    # reads the PHYSICAL rects (Busterm::orig_rects) — a margin marks faces
    # unusable for TAPS, it does not physically separate the rects — while
    # the emitted stub still taps the separated rect's INSET face (y=510),
    # the #835 semantic for tap coordinates.  The two semantics divide as:
    # touching = physical geometry, tappable = inset geometry.
    assert _chain_stub_faces((10, 10)) == [510]


def test_same_band_disjoint_sibling_stays_loud_via_teg_open():
    # The one Direct-branch corner residual (i)'s fix deliberately leaves on
    # the LOUD path: a DISJOINT sibling sharing the trunk's perp band (two
    # rects side by side along the spine, trunk inside one).  A perpendicular
    # stub has no gap to bridge, a bare spine extension is retracted by NUTS
    # span adjustment (an end with no junction has nothing holding it —
    # measured), and an over-the-cell anchoring stub trips the #514
    # tap-overhang ANTENNA rule — so nothing is emitted and the routed
    # result reports TEG_OPEN, exactly as before the fix (documented in
    # teg_multirect_status.md open 1).
    s = _session([
        "add_block T rect 300 0 400 100 rect 0 0 100 100 teg_mode over",
        "add_block src 600 0 700 100",
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
            if 0 < y < 100:          # inside both rects' shared perp band
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no same-band trunk candidate found"
    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "OVER block 'T'" in out, out


def test_mst_on_over_block_fires_teg_open_end_to_end():
    # Open 1 residual (iii), still open BY SCOPING: MST candidates connect
    # each edge at the closest rect pair and emit no TEG connection metal
    # for an OVER block's other rects (per-rect attachment into an arbitrary
    # tree segment would have to compose with relay completion, the
    # shared-leg trims and ripup's edge flips — a redesign, documented in
    # teg_multirect_status.md open 1).  This pins the guarantee that makes
    # the scoping safe: the routed result is LOUD — TEG_OPEN at both placed
    # stages naming the unreached rect — never silent.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pin = next(i for i, c in enumerate(w.input.candidates)
               if c.type.startswith("MST_"))
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "OVER block 'r2'" in out and "rect#1 (900,0)-(1000,100)" in out, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out


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
    # genuinely leaves a rect unreached (an MST candidate on an OVER block —
    # the end-to-end firing shape above, residual (iii)); the pool must not
    # shrink over a reporting audit.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
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


def test_bitrunk_on_over_block_fires_teg_open_end_to_end():
    # BITRUNK is bbox-only BY SCOPING (teg_multirect_status.md open 8): the
    # datapath trees work entirely on orig_bbox — no rect selection, no TEG
    # connection metal.  This pins the guarantee that makes the scoping safe
    # to accept: where the bbox-only shape actually bites — an OVER block
    # whose rect the BITRUNK's union-bbox metal never reaches — the routed
    # result is LOUD, not silent: TEG_OPEN fires at both placed stages.
    # Geometry: 4 endpoint blocks (the legacy BITRUNK_H floor), the OVER
    # receiver's second rect at x 900..1000 lies beyond the rungs' along-span
    # (pin centers reach x=750) and its union-center stub lands in the gap,
    # so no placed metal of the bundle touches rect#1.
    s = _session([
        "add_block src 0 0 100 100",
        "add_block r1 300 300 400 400",
        "add_block r2 rect 500 0 600 100 rect 900 0 1000 100 teg_mode over",
        "add_block r3 300 600 400 700",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx r1.a,r2.b,r3.c",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    pin = next(i for i, c in enumerate(w.input.candidates)
               if c.type.startswith("BITRUNK_H"))
    for cmd in (f"select_topology 1 {pin + 1}", "run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "rect#1 (900,0)-(1000,100)" in out, out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out


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
