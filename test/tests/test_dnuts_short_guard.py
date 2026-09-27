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

"""DetailedNUTS's cross-bundle short guard (issue #962).

A bit's track is reserved against other bundles over its bus's ABSTRACT
span, and the span-follow (`adjust_bit_spans`) then stretches the bit to its
junction partner's track — past that span, onto a track another bundle may
hold.  The two buses' abstract spans never met, so neither reserved against
the other, and the audit (`check_dnuts_cross_shorts`, #948) finds the short
after the router is done with it.

The engine now counts those shorts itself, with the audit's own predicate
(`find_cross_bundle_overlaps`, one statement of the rule), and has two
levers, both off by default:

  cull   remove the stretched side of each short left after the span-follow
         and count it unplaced — an open the healers can act on;
  reach  reserve against the span each segment will REACH: its span widened
         to its partners' placed bit tracks (their abstract footprint when
         they are not placed yet), so the short never happens.

The scenario every test below builds is the issue's mechanism in miniature:
bundle 1's H trunk on M6 spans [100, 200] and ends at a junction with its V
stub, whose bit lands on x = 206.5; bundle 2's H trunk spans [203, 300] on
the same tracks.  The spans do not meet, both trunks want track 3.5, and
bundle 1's bit is stretched to 206.5 — through bundle 2's first 3.5 units.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import buda

_ROOT = Path(__file__).resolve().parents[2]

M5, M6, M7 = 5, 6, 7


def _pattern():
    """unit pitch 14; signal centres 3.5, 5.5, 10.5, 12.5 (+14k)."""
    def slot(t, w, sp):
        return buda.TrackSlot(type=t, label=t.lower(), width=w,
                              space_after=sp)
    return buda.TrackPattern(origin=0.0, slots=[
        slot("POWER", 2.0, 1.0), slot("SIGNAL", 1.0, 1.0),
        slot("SIGNAL", 1.0, 1.0), slot("GROUND", 2.0, 1.0),
        slot("SIGNAL", 1.0, 1.0), slot("SIGNAL", 1.0, 1.0)])


def _stack():
    stack = buda.RoutingGridStack()
    stack.define_layer(M5, _pattern(), False)   # V stubs
    stack.define_layer(M6, _pattern(), True)    # H trunks
    stack.define_layer(M7, _pattern(), False)   # V stubs, placed after M6
    return stack


def _seg(bundle, seg, layer, lo, hi, ilo, ihi, anchor=None, width=0.0):
    bs = buda.BusSegment()
    bs.bundle_id, bs.seg_idx, bs.layer = bundle, seg, layer
    bs.span_lo, bs.span_hi = lo, hi
    bs.interval_lo, bs.interval_hi = ilo, ihi
    bs.bit_width = 1
    if anchor is not None:
        bs.abstract_pos = anchor
    bs.abstract_width = width
    return bs


def _conn(seg, at, lo_end):
    c = buda.BusSegmentConn()
    c.seg_idx, c.at_pos, c.is_endpoint, c.lo_end = seg, at, True, lo_end
    return c


def _scenario(stub_layer=M5, stub_anchor=None, stub_width=0.0):
    """Bundle 1: trunk [100, 200] on M6, its hi end at a junction with a V
    stub whose one track in [206, 208] is 206.5.  Bundle 2: trunk [203, 300]
    on M6.  The anchors put bundle 1's trunk first on M6 and both trunks
    nearest track 3.5."""
    t1 = _seg(1, 0, M6, 100.0, 200.0, 0.0, 14.0, anchor=3.0, width=1.0)
    t1.connections.append(_conn(1, 200.0, lo_end=False))
    stub = _seg(1, 1, stub_layer, 3.0, 60.0, 206.0, 208.0,
                anchor=stub_anchor, width=stub_width)
    t2 = _seg(2, 0, M6, 203.0, 300.0, 0.0, 14.0, anchor=4.0, width=1.0)
    return [t1, stub, t2]


def _run(segs, reach=False, cull=False, fixed=None, stack=None):
    stack = stack or _stack()
    eng = buda.DetailedNUTSEngine(stack)
    eng.set_short_guard(reach, cull)
    if fixed:
        eng.add_fixed_bits(fixed)
    with buda.ostream_redirect():
        res = eng.run(segs)
    return res


def _wire(res, bundle, seg):
    got = [ns for ns in res.net_segments
           if (ns.bundle_id, ns.seg_idx) == (bundle, seg)]
    return got[0] if got else None


def _audit(res):
    nets = {}
    for ns in res.net_segments:
        names = nets.setdefault(ns.bundle_id, [])
        while len(names) <= ns.bit_index:
            names.append(f"n{ns.bundle_id}_{len(names)}")
    return list(buda.check_dnuts_cross_shorts(res, nets, {}).violations)


# ── the mechanism, and the count ───────────────────────────────────────────

def test_guard_off_counts_the_stretch_short_the_audit_reports():
    res = _run(_scenario())
    t1, t2 = _wire(res, 1, 0), _wire(res, 2, 0)
    # The mechanism: both trunks on 3.5, bundle 1 stretched past its span
    # to its stub's bit at 206.5, into bundle 2's [203, 300].
    assert t1.track_position == pytest.approx(3.5)
    assert t2.track_position == pytest.approx(3.5)
    assert max(t1.span_lo, t1.span_hi) == pytest.approx(206.5)
    # Counted by the engine, and exactly the pair the audit reports: the two
    # read one predicate.
    assert res.num_cross_shorts == 1
    v, = _audit(res)
    assert {v.bundle_id, v.bundle_id2} == {1, 2}
    # Observation only: nothing removed, nothing unplaced.
    assert res.num_short_bits == 0
    assert res.num_unplaced == 0
    assert len(res.net_segments) == 3


def test_guard_off_is_placement_identical_to_no_guard_at_all():
    # set_short_guard(False, False) is the default; the placement a run
    # produces must not depend on having been asked explicitly.
    segs = _scenario()
    eng = buda.DetailedNUTSEngine(_stack())
    with buda.ostream_redirect():
        a = eng.run(segs)
    b = _run(segs)
    key = lambda r: sorted((n.bundle_id, n.seg_idx, n.bit_index,
                            n.track_position, n.span_lo, n.span_hi)
                           for n in r.net_segments)
    assert key(a) == key(b)


def test_the_short_is_named_by_its_wires():
    # cross_shorts is what the healers act on (BUDA_HEAL_SHORTS): the pair
    # by (bundle, seg, bit), the smaller first as the audit orders it, and
    # the shared metal — bundle 1's stretch into bundle 2's [203, 300].
    res = _run(_scenario())
    cs, = res.cross_shorts
    assert (cs.bundle_a, cs.seg_a, cs.bit_a) == (1, 0, 0)
    assert (cs.bundle_b, cs.seg_b, cs.bit_b) == (2, 0, 0)
    assert cs.layer == M6
    assert (cs.s_lo, cs.s_hi) == pytest.approx((203.0, 206.5))
    assert (cs.p_lo, cs.p_hi) == pytest.approx((3.0, 4.0))


# ── cull: remove the stretched side ────────────────────────────────────────

def test_cull_removes_the_stretched_side_and_counts_it_unplaced():
    res = _run(_scenario(), cull=True)
    assert res.num_cross_shorts == 1        # counted before the cull
    assert res.num_short_bits == 1
    assert res.num_unplaced == 1
    # Bundle 1's trunk holds shared metal its reservation never covered
    # ([203, 206.5] lies past its span [100, 200]); bundle 2's lies inside
    # its own span.  The stretched side goes.
    assert _wire(res, 1, 0) is None
    assert _wire(res, 2, 0) is not None
    assert _wire(res, 1, 1) is not None     # the stub is not in the short
    assert not _audit(res)
    # Counted before the cull, gone from what is left: the healers must not
    # charge a short the cull already charged as an unplaced bit.
    assert list(res.cross_shorts) == []


def test_cull_never_removes_a_fixed_copy():
    # A bottom-up copy (fixed bit) is reserved against over its FINAL span,
    # [203, 300], which bundle 1's abstract [100, 200] does not meet; the
    # stretch then shorts into it.  The copy is a template's frozen routing,
    # so the run's wire is the one removed.
    t1, stub, _ = _scenario()
    copy = buda.NetSegment()
    copy.bundle_id, copy.seg_idx, copy.bit_index = 9, 0, 0
    copy.layer, copy.track_position, copy.width = M6, 3.5, 1.0
    copy.span_lo, copy.span_hi = 203.0, 300.0
    res = _run([t1, stub], cull=True, fixed=[copy])
    assert res.num_cross_shorts == 1
    assert res.num_short_bits == 1
    assert _wire(res, 1, 0) is None
    # With the cull off the pair stays, the copy named as its other side.
    cs, = _run([t1, stub], fixed=[copy]).cross_shorts
    assert (cs.bundle_a, cs.bundle_b) == (1, 9)


def test_a_short_between_two_fixed_bits_is_not_this_runs():
    a, b = buda.NetSegment(), buda.NetSegment()
    for ns, bid, lo, hi in ((a, 8, 100.0, 210.0), (b, 9, 203.0, 300.0)):
        ns.bundle_id, ns.seg_idx, ns.bit_index = bid, 0, 0
        ns.layer, ns.track_position, ns.width = M6, 3.5, 1.0
        ns.span_lo, ns.span_hi = lo, hi
    res = _run([_seg(3, 0, M6, 500.0, 600.0, 0.0, 14.0, anchor=3.0)],
               cull=True, fixed=[a, b])
    assert res.num_cross_shorts == 0
    assert res.num_short_bits == 0


# ── reach: reserve against where the bits will go ─────────────────────────

def test_reach_reserves_against_a_placed_partners_bits():
    # The stub is on M5, placed before M6: bundle 1's reach is exact,
    # [100, 206.5], which meets bundle 2's [203, 300], so bundle 2 reserves
    # against track 3.5 and takes the next one.
    res = _run(_scenario(), reach=True)
    assert res.num_cross_shorts == 0
    assert res.num_unplaced == 0
    assert _wire(res, 1, 0).track_position == pytest.approx(3.5)
    assert _wire(res, 2, 0).track_position == pytest.approx(5.5)
    assert not _audit(res)


def test_reach_bounds_an_unplaced_partner_by_its_abstract_footprint():
    # The stub on M7 is placed AFTER M6, so its bit is not known when the
    # trunks are; its abstract footprint [206, 208] bounds the stretch.
    res = _run(_scenario(stub_layer=M7, stub_anchor=207.0, stub_width=2.0),
               reach=True)
    assert res.num_cross_shorts == 0
    assert _wire(res, 2, 0).track_position == pytest.approx(5.5)


def test_reach_without_a_footprint_leaves_the_short_to_the_cull():
    # No footprint (width 0: a hand-built segment, or a partner NUTS never
    # seated) bounds nothing, so the reach is the span and the short
    # happens; the cull behind the lever is what catches it.
    segs = _scenario(stub_layer=M7)
    res = _run(segs, reach=True)
    assert res.num_cross_shorts == 1
    both = _run(segs, reach=True, cull=True)
    assert both.num_short_bits == 1
    assert not _audit(both)


def test_a_partner_that_placed_no_bits_stretches_nothing():
    # A stub window with no signal track in it ([207, 208]; M5's nearest
    # are 206.5 and 208.5): the stub is unplaced, the trunk keeps its
    # abstract span, and reach adds nothing — bundle 2 is free to share
    # track 3.5, since the two wires never meet.
    t1, stub, t2 = _scenario()
    stub.interval_lo, stub.interval_hi = 207.0, 208.0
    res = _run([t1, stub, t2], reach=True)
    assert _wire(res, 1, 1) is None
    assert res.num_cross_shorts == 0
    assert _wire(res, 2, 0).track_position == pytest.approx(3.5)


# ── the merged bottom-up route ─────────────────────────────────────────────

def _copy(res, dy, bundle_map):
    """The bottom-up copy of `res`'s wires at an instance `dy` above the
    reference, re-keyed to the sibling's bundles (offset_net_segment, what
    stage c does for an upright instance)."""
    return [buda.offset_net_segment(ns, 0, dy, bundle_map[ns.bundle_id],
                                    ns.layer == M6)
            for ns in res.net_segments]


def test_a_template_short_is_counted_at_every_copy_of_the_template():
    # The bottom-up merge in miniature: the reference solve's bundles 1 and
    # 2 short (the scenario), and the template's copy at another instance
    # carries the same two wires as bundles 11 and 12.  The rest solve is
    # handed reference and copy as fixed bits and drops every pair of two
    # fixed bits — not its run's — so summing the two solves' shorts counted
    # the reference's and missed the copy's: the healers were blind to
    # exactly the shorts a template move fixes (Codex P1 on #966).  The
    # merged route is recounted instead, and has both.
    segs = _scenario()
    ref = _run(segs)
    copy = _copy(ref, 1000, {1: 11, 2: 12})
    rest_segs = [_seg(3, 0, M6, 500.0, 600.0, 0.0, 14.0, anchor=3.0)]
    rest = _run(rest_segs, fixed=list(ref.net_segments) + copy)
    solves = list(ref.cross_shorts) + list(rest.cross_shorts)
    assert [(c.bundle_a, c.bundle_b) for c in solves] == [(1, 2)]
    route = list(ref.net_segments) + copy + list(rest.net_segments)
    merged = buda.cross_shorts_in(route, segs + rest_segs)
    assert [(c.bundle_a, c.bundle_b) for c in merged] == [(1, 2), (11, 12)]
    # Named like the solve names its own: the copy's pair is the
    # reference's, translated.
    ours, theirs = merged
    assert (ours.seg_a, ours.bit_a, ours.seg_b, ours.bit_b) == \
        (theirs.seg_a, theirs.bit_a, theirs.seg_b, theirs.bit_b)
    assert (theirs.s_lo, theirs.s_hi) == pytest.approx((203.0, 206.5))
    assert (theirs.p_lo, theirs.p_hi) == pytest.approx((1003.0, 1004.0))
    # And it is the audit's count of the same route.
    res = buda.DetailedNUTSResult()
    res.net_segments = route
    assert len(_audit(res)) == len(merged)


def _shield(bundle, track):
    ns = buda.NetSegment()
    ns.bundle_id, ns.seg_idx, ns.bit_index = bundle, 0, -1
    ns.layer, ns.track_position, ns.width = M6, track, 1.0
    ns.span_lo, ns.span_hi = 100.0, 300.0
    ns.is_shield = True
    return ns


def _governed(bundle, shield_net):
    bs = _seg(bundle, 0, M6, 100.0, 300.0, 0.0, 14.0)
    spec = buda.NdrSpec()
    spec.shield_mode, spec.shield_net = 1, shield_net
    bs.ndr = spec
    return bs


@pytest.mark.parametrize("nets, shorts", [
    (("GND", "GND"), 0),     # one net: shared shield metal is no short
    (("GND", "VSS"), 1),     # two names are two nets, as the audit reads
    (("VDD", "GND"), 1),     # them off the names persistence writes
])
def test_the_recount_names_a_shield_by_its_rules_net(nets, shorts):
    # Two bundles' shields on one track: whether that is a short is a
    # question of the NETS their rules name, which the recount reads off
    # the bus segments of the whole route — copies' bundles included.
    wires = [_shield(1, 3.5), _shield(2, 3.5)]
    bus = [_governed(1, nets[0]), _governed(2, nets[1])]
    assert len(buda.cross_shorts_in(wires, bus)) == shorts


# ── the env seed ───────────────────────────────────────────────────────────

def _seeded(value):
    code = ("import buda\n"
            "e = buda.DetailedNUTSEngine(buda.RoutingGridStack())\n"
            "print(int(e.short_reach), int(e.short_cull))\n")
    env = dict(os.environ)
    if value is None:
        env.pop("BUDA_DNUTS_SHORT_GUARD", None)
    else:
        env["BUDA_DNUTS_SHORT_GUARD"] = value
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT / "build"), str(_ROOT / "src")])
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.split(), out.stderr


@pytest.mark.parametrize("value, want", [
    (None, ["0", "0"]), ("", ["0", "0"]), ("off", ["0", "0"]),
    ("cull", ["0", "1"]), ("reach", ["1", "0"]),
    ("reach,cull", ["1", "1"]), ("cull+reach", ["1", "1"]),
    ("reach cull", ["1", "1"]),
])
def test_env_seeds_every_engine(value, want):
    got, err = _seeded(value)
    assert got == want
    assert "BUDA_DNUTS_SHORT_GUARD" not in err


def test_an_unknown_env_word_is_reported_not_read_as_off():
    got, err = _seeded("reach,cul")
    assert got == ["1", "0"]
    assert "'cul'" in err
