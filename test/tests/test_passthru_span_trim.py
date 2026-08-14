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

"""Regression: the per-bit span snap must not delete a PASS-THROUGH crossing.

Issue #496, the mirror image of the busterm-face anchor bug next door in
`test_nuts_busterm_face_anchor.py` (big2 bus_077 / blk_12). There, the snap
pulled a face END off its block. Here it is worse: a block a segment covers by
*crossing* it can sit entirely BEYOND the segment's outermost junction, so the
snap does not clip the crossing — it deletes it, and the block opens with no
warning at any earlier stage. Nominal (`check_topo`) and abstract
(`check_nuts`) coverage both pass, because the abstract segment keeps its full
span; only the per-bit stage loses it.

The repro (`flow/rnr/mix.buda`, bundle 90):

    block chip/i_dnuts2_2/u4  = (1680,1530)-(1730,1580)
    seg1 nominal (1035,1555)-(1875,1555)   crosses u4 at x in [1680,1730]
    seg1 junctions at x=1095 and x=1455
    seg1 per-bit spans after the snap: [1085 .. 1479]     <- crossing gone

Fix: `BusSegment::passthru_spans` carries each crossing interval (clipped to
the abstract span at construction, so a repair can never claim metal abstract
NUTS did not reserve) and `adjust_bit_spans` re-extends to it — but only when
the crossing was actually lost, so a bit that still overlaps is untouched.
"""
import os
import subprocess
import sys
from pathlib import Path

import contextlib
import io

import pytest

import buda

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "src"))
import buda_cli  # noqa: E402
from subprocess_env import buda_env

LAYER = 4


# ── fixture: a trunk with two junctions and a block beyond them ──────────────
#
# Mirrors the repro's shape. The trunk spans [0,200]; its two stub junctions
# are at 20 and 60; a pass-through block occupies [150,180]. The snap pulls
# every bit in to the junction extent, stranding the block.

def _stack():
    slots = [buda.TrackSlot(type="SIGNAL", label="sig",
                            width=1.0, space_after=1.0) for _ in range(4)]
    stack = buda.RoutingGridStack()
    stack.define_layer(LAYER, buda.TrackPattern(origin=0.0, slots=slots), True)
    return stack


def _seg(bundle_id, seg_idx, span_lo, span_hi, interval_lo, interval_hi,
         bit_width=2):
    s = buda.BusSegment()
    s.bundle_id, s.seg_idx, s.layer = bundle_id, seg_idx, LAYER
    s.span_lo, s.span_hi = span_lo, span_hi
    s.interval_lo, s.interval_hi = interval_lo, interval_hi
    s.bit_width = bit_width
    return s


def _conn(seg_idx, at_pos, is_endpoint, lo_end):
    c = buda.BusSegmentConn()
    c.seg_idx, c.at_pos = seg_idx, at_pos
    c.is_endpoint, c.lo_end = is_endpoint, lo_end
    return c


def _trunk_with_two_stubs(passthru=None):
    """seg0 = the trunk [0,200]; seg1/seg2 = stubs it meets near 20 and 60.

    Both junctions are ENDPOINT conns, so the snap sets both of the trunk's
    ends to the stubs' placed tracks — the stubs' intervals put those near 20
    and 60, leaving the trunk's bits nowhere near a block at [150,180]. That
    asymmetry (junctions clustered at one end, the crossing beyond the far
    one) is exactly the repro's shape.
    """
    trunk = _seg(1, 0, 0.0, 200.0, 0.0, 8.0)
    trunk.connections = buda.BusSegmentConnList([
        _conn(1, 20.0, True, True),          # lo end snaps to seg1's bit track
        _conn(2, 60.0, True, False),         # hi end snaps to seg2's bit track
    ])
    if passthru is not None:
        trunk.passthru_spans = [passthru]
    s1 = _seg(1, 1, 0.0, 40.0, 18.0, 26.0)   # tracks 18.5, 20.5
    s2 = _seg(1, 2, 0.0, 40.0, 58.0, 66.0)   # tracks 58.5, 60.5
    return [trunk, s1, s2]


def _trunk_spans(result):
    """(lo, hi) extent of every bit of seg 0, ordered."""
    out = []
    for ns in result.net_segments:
        if ns.bundle_id == 1 and ns.seg_idx == 0:
            out.append((min(ns.span_lo, ns.span_hi),
                        max(ns.span_lo, ns.span_hi)))
    return sorted(out)


# ── the engine-level contract ────────────────────────────────────────────────

def test_snap_alone_deletes_a_crossing_beyond_the_last_junction():
    """The defect itself, pinned: with no `passthru_spans` the snap trims every
    bit to its junction extent and a block at [150,180] is left uncovered.

    This is the discriminating half — it is what the fixture would do on a
    build without the repair, so the test below cannot pass vacuously."""
    res = buda.DetailedNUTSEngine(_stack()).run(_trunk_with_two_stubs())
    spans = _trunk_spans(res)
    assert spans, "fixture placed no trunk bits"
    for lo, hi in spans:
        assert not (lo <= 180.0 and hi >= 150.0), (
            f"bit span [{lo},{hi}] still reaches the block — fixture no longer "
            "reproduces the trim")


def test_passthru_crossing_survives_the_snap():
    """With the crossing declared, every bit reaches the block again."""
    res = buda.DetailedNUTSEngine(_stack()).run(
        _trunk_with_two_stubs(passthru=(150.0, 180.0)))
    spans = _trunk_spans(res)
    assert spans, "fixture placed no trunk bits"
    for lo, hi in spans:
        assert lo <= 180.0 and hi >= 150.0, (
            f"bit span [{lo},{hi}] does not cover the crossing [150,180]")


def test_repair_never_exceeds_the_abstract_span():
    """The safety property the clipping buys: a restored span stays inside the
    abstract segment's own extent, so it can only give back wire abstract NUTS
    already reserved — never claim new metal that could collide."""
    res = buda.DetailedNUTSEngine(_stack()).run(
        _trunk_with_two_stubs(passthru=(150.0, 180.0)))
    for lo, hi in _trunk_spans(res):
        assert lo >= 0.0 and hi <= 200.0, (
            f"bit span [{lo},{hi}] escaped the abstract span [0,200]")


def test_no_change_when_the_crossing_survives():
    """A bit that still overlaps the block is left alone — the repair is
    conditional, so every flow whose crossings survive the snap is
    byte-identical.  Block [30,50] sits between the two junctions."""
    base = _trunk_spans(buda.DetailedNUTSEngine(_stack())
                        .run(_trunk_with_two_stubs()))
    with_pt = _trunk_spans(buda.DetailedNUTSEngine(_stack())
                           .run(_trunk_with_two_stubs(passthru=(30.0, 50.0))))
    assert base == with_pt, f"spans moved for a surviving crossing: {base} -> {with_pt}"


# ── end to end ───────────────────────────────────────────────────────────────

def _run_buda(script):
    env = buda_env(_ROOT)
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz", str(script)],
        capture_output=True, text=True, env=env, cwd=str(_ROOT / "flow" / "rnr"),
    )
    return r.returncode, r.stdout + r.stderr


@pytest.mark.mid
def test_mix_flow_has_no_passthrough_open():
    """The reported repro end to end: `mix.buda` used to finish with bundle
    90's `chip/i_dnuts2_2/u4` open at DetailedNUTS (10 bits) while every
    earlier stage reported the route clean.  The healers never cleared it —
    BUSTERM_OPEN is in no healer's metric — so it survived to the final
    check_design."""
    rc, out = _run_buda(_ROOT / "flow" / "rnr" / "mix.buda")
    assert rc == 0, f"non-zero exit {rc}\n{out[-4000:]}"
    assert "no pass-through segment" not in out, out[-4000:]
    assert "no pass-through/busterm connection" not in out, out[-4000:]


# ── the anchor invariant (Codex P1, second review on #499) ───────────────────

def _untapped_crossings(s, w):
    """Per untapped connected block, the segments that PLACEABLY cross it.

    Placeable = the robust-cover gate check_topo applies (issue #438): the
    segment's slide window must be able to reach the block's perp extent, so a
    boundary graze pushed off the face is not counted as a cover.
    """
    sel = w.plan.selected_topology_index
    if sel is None or sel < 0 or sel >= len(w.input.candidates):
        return {}
    topo = w.input.candidates[sel]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    tapped = {c.block_name for cs in ct.segs() for c in cs.conns
              if c.kind == buda.SegConnKind.BUSTERM}
    out = {}
    for bname in topo.connected_block_names:
        if bname in tapped:
            continue
        rects = s.fp.get_block_rects(bname) or [s.fp.get_block_bounds(bname)]
        crossers = []
        for i, cs in enumerate(ct.segs()):
            lo, hi = min(cs.along_lo, cs.along_hi), max(cs.along_lo, cs.along_hi)
            for r in rects:
                plo, phi = (r.y1, r.y2) if cs.horiz else (r.x1, r.x2)
                alo, ahi = (r.x1, r.x2) if cs.horiz else (r.y1, r.y2)
                if cs.perp_pos < plo or cs.perp_pos > phi:
                    continue
                if lo > ahi or hi < alo:
                    continue
                if cs.perp_hi < plo or cs.perp_lo > phi:
                    continue                       # not placeable
                crossers.append(i)
                break
        if crossers:
            out[bname] = crossers
    return out


@pytest.mark.mid
@pytest.mark.parametrize("flow", ["flow/rnr/mix.buda",
                                  "flow/big_data_test/big.buda",
                                  "demo/comprehensive_demo.buda"])
def test_every_placeable_crossing_block_keeps_an_anchor(flow):
    """Every untapped block with a placeable crossing must have AT LEAST ONE
    anchored segment holding it.

    The first version of the fix anchored a crossing only when it was the
    block's *sole* nominal cover, on the reasoning that a block several
    segments cross stays covered when any one retracts.  That does not follow:
    nominal multiplicity is not placement redundancy, and several crossings can
    ALL be contracted past the block, which reopens exactly the `BUSTERM_OPEN`
    the anchor exists to prevent (Codex, second review on #499).

    The hole was not hypothetical — on `main` at 18b631e these three flows had
    10, 34 and 4 multi-crosser blocks respectively, every one of them
    unanchored.  The rule is now "exactly one placeable crossing per block":
    never all of them (b44 realizes +30% that way), and never none.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    os.chdir(str(_ROOT / os.path.dirname(flow)))
    try:
        with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
            for line in open(os.path.basename(flow)):
                c = line.strip()
                if not c or c.startswith("#"):
                    continue
                if c.split()[0] in {"visualize", "exit", "check_design",
                                    "report_wirelength"}:
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)

    placed = {(t.bundle_id, t.seg_idx): t for t in s.nuts_result.segments}
    unanchored, multi = [], 0
    for w in s.bundles:
        bid = w.input.original_bundle.id
        for bname, crossers in _untapped_crossings(s, w).items():
            if len(crossers) > 1:
                multi += 1
            if not any(placed.get((bid, i)) is not None
                       and len(placed[(bid, i)].passthru_spans) > 0
                       for i in crossers):
                unanchored.append((bid, bname, crossers))
    assert not unanchored, (
        f"{len(unanchored)} block(s) with a placeable crossing carry no anchor: "
        f"{unanchored[:5]}")
    # Non-vacuity: the flow must actually contain the multi-crosser case the
    # rule is about, or this proves nothing.
    assert multi > 0, f"{flow} has no multi-crosser block — fixture is vacuous"


@pytest.mark.mid
@pytest.mark.parametrize("flow", ["flow/rnr/mix.buda",
                                  "flow/big_data_test/big2/big2.buda"])
def test_elected_anchor_is_seated_inside_its_block(flow):
    """Every honored anchor must belong to a segment actually seated inside the
    block it protects — and each block must have exactly one.

    An anchor holds a segment's ALONG span; it does not constrain
    `track_position`. So a crossing elected on nominal geometry can be placed
    perpendicular-OUTSIDE the block it was chosen for, leaving the sibling that
    did land inside unanchored and free to be contracted away — the same
    `BUSTERM_OPEN` the anchor exists to prevent (Codex, third review on
    #499/#505).

    Measured on `big2` before the fix: 7 blocks whose nominal-elected anchor
    sat outside, each covered only by an unanchored sibling. The election
    therefore happens in `tighten_spans_to_reach`, where placement is final.

    `TrackSegment.passthru_spans` is the post-tighten (honored) set, so this
    reads the real decision rather than re-deriving it.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    os.chdir(str(_ROOT / os.path.dirname(flow)))
    try:
        with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
            for line in open(os.path.basename(flow)):
                c = line.strip()
                if not c or c.startswith("#"):
                    continue
                if c.split()[0] in {"visualize", "exit", "check_design",
                                    "report_wirelength"}:
                    continue
                s.do_command(c)
    finally:
        os.chdir(cwd)

    outside, per_block = [], {}
    for t in s.nuts_result.segments:
        if not t.placed:
            continue
        for pc in t.passthru_spans:
            per_block.setdefault((t.bundle_id, pc.block), []).append(t.seg_idx)
            if not (pc.perp_lo <= t.track_position <= pc.perp_hi):
                outside.append((t.bundle_id, t.seg_idx, pc.block,
                                t.track_position, (pc.perp_lo, pc.perp_hi)))
    assert not outside, (
        f"{len(outside)} anchor(s) held by a segment seated outside the block "
        f"they protect: {outside[:5]}")
    dupes = {k: v for k, v in per_block.items() if len(v) > 1}
    assert not dupes, f"block(s) with more than one anchor: {list(dupes)[:5]}"
    assert per_block, f"{flow} elected no anchors — fixture is vacuous"
