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

"""BIT_SHORT across bundles (issue #948).

`check_dnuts` is handed ONE bundle and filters the detailed result to it, so
two wires of two different bundles overlapping on one track were outside
every call `check_design` made: the judge (`tools/independent_audit.py`)
counted 4 shorts on `converge.tcl soc 4` and 7 on a `soc.tcl 8` run that
ended "clean", and the violation list named none of them.  The kind existed;
its SCOPE was one bundle.

`check_dnuts_cross_shorts` is the other half, run once per design: every
pair of placed wires from two DIFFERENT bundles whose metal overlaps on one
layer while they carry different nets.  The two halves partition the pairs,
and the tests below keep the two shapes apart the way the issue asks —
two bits of one bundle (check_dnuts, as before) and two bits of two bundles
(the new pass) — and check that neither half reports the other's pair.

The predicate is the judge's: the rectangle persistence writes, overlapping
by a positive AREA, keyed on the net NAME.  So one net carried by two
bundles is shared metal, abutting wires are not a short, a wire whose name
cannot be resolved is its own net rather than one of a crowd, and a wide
wire overlapping a neighbour on a DIFFERENT track position is caught.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import buda

_ROOT = Path(__file__).resolve().parents[2]
M4 = 4
M5 = 5


def _wire(bundle, seg, bit, pos, lo, hi, layer=M4, width=1.0, shield=False,
          placed=True):
    ns = buda.NetSegment()
    ns.bundle_id, ns.seg_idx, ns.bit_index = bundle, seg, bit
    ns.layer, ns.track_position, ns.width = layer, pos, width
    ns.span_lo, ns.span_hi = lo, hi
    ns.is_shield = shield
    ns.placed = placed
    return ns


def _dnuts(*wires):
    res = buda.DetailedNUTSResult()
    res.net_segments = list(wires)
    return res


def _shorts(wires, bit_nets=None, shield_nets=None):
    if bit_nets is None:
        # every bit its own net, named after its bundle and bit
        bit_nets = {}
        for w in wires:
            if w.bit_index >= 0:
                names = bit_nets.setdefault(w.bundle_id, [])
                while len(names) <= w.bit_index:
                    names.append(f"n{w.bundle_id}_{len(names)}")
    res = buda.check_dnuts_cross_shorts(_dnuts(*wires), bit_nets,
                                        shield_nets or {})
    assert all(v.kind == buda.ViolationKind.BIT_SHORT for v in res.violations)
    return list(res.violations)


# ── the predicate ──────────────────────────────────────────────────────────

def test_two_bundles_on_one_track_is_a_short_with_both_wires_named():
    # The issue's first shape: two nets of two bundles, one M6 track,
    # overlapping along it (864..880 of 854.5..1080).
    v, = _shorts([_wire(176, 0, 12, 2700.0, 864.0, 880.0),
                  _wire(1, 0, 0, 2700.0, 854.5, 1080.0)])
    # oriented: the smaller (bundle, seg, bit) is the primary wire
    assert (v.bundle_id, v.seg_idx, v.bit_index) == (1, 0, 0)
    assert (v.bundle_id2, v.seg_idx2, v.bit_index2) == (176, 0, 12)
    assert "cross-bundle" in v.message
    assert "n1_0" in v.message and "n176_12" in v.message
    assert "[864, 880] along it" in v.message, v.message


def test_collinear_ends_running_past_each_other_is_a_short():
    # The issue comment's shape: two bundles collinear on shared tracks,
    # each bit's end overrunning the other's start by a shrinking amount —
    # 12 units down to 1.  The bus segments need not overlap at all.
    wires, overrun = [], [12, 11, 10, 8, 5, 3, 1]
    for k, d in enumerate(overrun):
        pos = 158.0 + 3 * k
        wires.append(_wire(434, 2, 2 + k, pos, 766.0, 910.0 + k, layer=M5))
        wires.append(_wire(133, 1, 15 + k, pos, 910.0 + k - d, 1500.0,
                           layer=M5))
    got = _shorts(wires)
    assert len(got) == len(overrun)
    assert {(v.bundle_id, v.bundle_id2) for v in got} == {(133, 434)}
    assert [v.bit_index for v in got] == list(range(15, 22))


def test_one_net_carried_by_two_bundles_is_shared_metal():
    # Identity is the NAME, not the bundle: the same net on two bundles
    # meeting on one track is one conductor.
    wires = [_wire(1, 0, 0, 10.0, 0.0, 100.0), _wire(2, 0, 3, 10.0, 50.0, 150.0)]
    assert _shorts(wires, bit_nets={1: ["clk"], 2: ["a", "b", "c", "clk"]}) == []
    # ... and two different names at the same place are a short
    assert len(_shorts(wires, bit_nets={1: ["clk"], 2: ["a", "b", "c", "d"]})) == 1


def test_abutting_wires_share_no_metal():
    # End to end on one track (a zero-length touch) and side by side at
    # zero spacing: a boundary, no area — the judge's rule and the
    # same-bundle half's strict test.
    assert _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0),
                    _wire(2, 0, 0, 10.0, 100.0, 200.0)]) == []
    assert _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0),
                    _wire(2, 0, 0, 11.0, 0.0, 100.0)]) == []     # width 1


def test_different_layers_never_short():
    assert _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0, layer=M4),
                    _wire(2, 0, 0, 10.0, 0.0, 100.0, layer=M5)]) == []


def test_the_pair_inside_one_bundle_is_not_this_passes_to_report():
    # Two different bits of ONE bundle co-located is check_dnuts's half;
    # reporting it here too would count one short twice.
    assert _shorts([_wire(7, 0, 0, 10.0, 0.0, 100.0),
                    _wire(7, 1, 1, 10.0, 50.0, 150.0)]) == []


def test_metal_not_track_position_decides():
    # A 3-slot-wide wire centred at 10 covers 8.5..11.5; a plain wire of
    # another bundle at 11 lies inside it — a different track POSITION, the
    # same metal.  A (layer, track) bucket would miss it.
    got = _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0, width=3.0),
                   _wire(2, 0, 0, 11.0, 0.0, 100.0)])
    assert len(got) == 1
    assert _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0, width=3.0),
                    _wire(2, 0, 0, 12.0, 0.0, 100.0)]) == []    # 11.5 | 11.5


def test_a_reversed_span_is_read_as_its_extent():
    # A placed span may be stored reversed (span_lo > span_hi keeps the
    # endpoint identity); the raw order would read this pair as disjoint.
    assert len(_shorts([_wire(1, 0, 0, 10.0, 110.0, 60.0),
                        _wire(2, 0, 0, 10.0, 70.0, 90.0)])) == 1


def test_an_unresolvable_wire_is_its_own_net():
    # No name for either wire: they are NOT folded onto one shared
    # "unknown" net — that is exactly how a real short would hide.
    got = _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0),
                   _wire(2, 0, 5, 10.0, 0.0, 100.0)],
                  bit_nets={1: [""], 2: []})
    assert len(got) == 1 and "net unresolved" in got[0].message


def test_shields_short_by_their_net():
    # Two bundles' shields on one net: shared metal.  A shield over another
    # bundle's signal bit: a short, named as a shield.
    sh_a = _wire(1, 0, -1, 10.0, 0.0, 100.0, shield=True)
    sh_b = _wire(2, 0, -1, 10.0, 50.0, 150.0, shield=True)
    assert _shorts([sh_a, sh_b], shield_nets={1: "VSS", 2: "VSS"}) == []
    assert len(_shorts([sh_a, sh_b], shield_nets={1: "VSS", 2: "VDD"})) == 1
    v, = _shorts([sh_a, _wire(2, 0, 0, 10.0, 50.0, 150.0)],
                 shield_nets={1: "VSS"})
    assert v.bit_index == -1 and "shield 1 (VSS)" in v.message


def test_an_unplaced_row_carries_no_metal():
    assert _shorts([_wire(1, 0, 0, 10.0, 0.0, 100.0),
                    _wire(2, 0, 0, 10.0, 0.0, 100.0, placed=False)]) == []


def test_a_pair_sharing_two_bins_is_reported_once():
    # Both wires straddle the same bin boundary across the layer (bins are
    # as wide as the widest wire, 2.0 here, so 4.0 is a boundary): the
    # sweep meets the pair in both bins and must count it once.
    got = _shorts([_wire(1, 0, 0, 4.0, 0.0, 100.0, width=2.0),
                   _wire(2, 0, 0, 4.0, 0.0, 100.0, width=2.0)])
    assert len(got) == 1


def test_many_wires_on_one_track_report_every_overlapping_pair():
    # A chain along one track: each wire overlaps only its neighbours.
    wires = [_wire(b, 0, 0, 10.0, 100.0 * b, 100.0 * b + 150.0)
             for b in range(1, 6)]
    got = _shorts(wires)
    assert [(v.bundle_id, v.bundle_id2) for v in got] == [
        (1, 2), (2, 3), (3, 4), (4, 5)]


# ── check_design, on a routed design ───────────────────────────────────────

def _quickstart():
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    cwd = os.getcwd()
    try:
        os.chdir(_ROOT / "demo")
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("source quickstart.buda")
    finally:
        os.chdir(cwd)
    return s


def _audit(s):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design("dnuts")
    return verdict, buf.getvalue()


_FIELDS = ("bundle_id", "seg_idx", "bit_index", "layer", "track_position",
           "width", "span_lo", "span_hi", "is_shield", "placed")


def _clone(ns):
    # A real copy: reading `net_segments` hands back references into the
    # engine's vector, which a later assignment to it would overwrite.
    c = buda.NetSegment()
    for f in _FIELDS:
        setattr(c, f, getattr(ns, f))
    return c


def _move(s, key, **fields):
    """Rewrite one placed bit-wire of the session's detailed result."""
    segs = [_clone(ns) for ns in s.detailed_result.net_segments]
    hit = [ns for ns in segs
           if (ns.bundle_id, ns.seg_idx, ns.bit_index) == key]
    assert len(hit) == 1, key
    for k, val in fields.items():
        setattr(hit[0], k, val)
    s.detailed_result.net_segments = segs


@pytest.fixture(scope="module")
def routed():
    s = _quickstart()
    return s, [_clone(ns) for ns in s.detailed_result.net_segments]


@pytest.fixture
def quickstart(routed):
    s, pristine = routed
    s.detailed_result.net_segments = [_clone(ns) for ns in pristine]
    yield s
    s.detailed_result.net_segments = [_clone(ns) for ns in pristine]


def test_the_routed_design_starts_clean(quickstart):
    verdict, out = _audit(quickstart)
    assert verdict["violations"] == 0, out


def test_a_short_between_two_bundles_is_now_a_violation(quickstart):
    # Bundle 5 seg 1 bit 0 moved onto bundle 2 seg 3 bit 0's M4 track: the
    # spans overlap over 600..631.5.  Before #948 no audit saw this pair.
    s = quickstart
    track = next(ns.track_position for ns in s.detailed_result.net_segments
                 if (ns.bundle_id, ns.seg_idx, ns.bit_index) == (2, 3, 0))
    _move(s, (5, 1, 0), track_position=track)
    verdict, out = _audit(s)
    assert verdict["by_kind"].get("BIT_SHORT") == 1, out
    # listed once, under the lower bundle, naming the other in its locus
    assert re.search(r"Bundle 2: Seg 3<->Bundle 5 Seg 1: 1 bit\(s\) — "
                     r"different nets' metal overlaps", out), out


def test_a_short_inside_one_bundle_is_still_check_dnuts_s_and_counted_once(
        quickstart):
    # The other shape: bundle 2 seg 2 bit 1 moved onto seg 1 bit 0's track,
    # its span stretched to overlap — two bits of ONE bundle.  check_dnuts
    # reports it as it always has (no second bundle in the locus), and the
    # cross-bundle pass must not report it again.
    s = quickstart
    track = next(ns.track_position for ns in s.detailed_result.net_segments
                 if (ns.bundle_id, ns.seg_idx, ns.bit_index) == (2, 1, 0))
    _move(s, (2, 2, 1), track_position=track, span_lo=400.0, span_hi=700.0)
    verdict, out = _audit(s)
    assert verdict["by_kind"].get("BIT_SHORT") == 1, out
    assert re.search(r"Bundle 2: Seg (1<->2|2<->1): 1 bit\(s\) — "
                     r"different nets'", out), out
    assert "<->Bundle" not in out, out


def test_both_bundles_of_a_short_count_as_dirty(quickstart):
    # One violation, but two bundles whose nets are shorted: the summary's
    # "across N bundle(s)" — what the QoR corpus reads as viol_bundles —
    # counts both.
    s = quickstart
    v, = _shorts([_wire(2, 3, 0, 10.0, 0.0, 100.0),
                  _wire(5, 1, 0, 10.0, 50.0, 150.0)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._report_violations_summary([("Bundle 2", v)])
    assert "Total: 1 violation(s) in 1 group(s) across 2 bundle(s)" in \
        buf.getvalue(), buf.getvalue()


def test_one_wire_shorting_two_is_counted_as_two(quickstart):
    # A wide wire of bundle 2 across two narrow, mutually disjoint bits of
    # bundle 5's segment: two violations, one group, and the summary must
    # say two — it counted distinct bit_index alone and said one (Codex P2
    # on #963), which is the number the judge-agreement test sums.
    s = quickstart
    vs = _shorts([_wire(2, 3, 0, 10.0, 0.0, 100.0, width=6.0),
                  _wire(5, 1, 0, 8.5, 50.0, 150.0),
                  _wire(5, 1, 1, 11.5, 50.0, 150.0)])
    assert len(vs) == 2
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._report_violations_summary([("Bundle 2", v) for v in vs])
    out = buf.getvalue()
    assert "Seg 3<->Bundle 5 Seg 1: 2 wire pair(s) — different nets'" in out, out
    assert "Total: 2 violation(s) in 1 group(s) across 2 bundle(s)" in out, out


def test_shield_shorts_are_counted_per_pair_too(quickstart):
    # A shield's bit_index is its negative ordinal: a wide shield of bundle 2
    # across two disjoint bits of bundle 5 is two shorts with NO signal bit
    # on bundle 2's side.  The pair count must not depend on the bit's sign,
    # or the group reads as one verbatim message and Total 1 (second Codex
    # P2 on #963).
    s = quickstart
    vs = _shorts([_wire(2, 3, -1, 10.0, 0.0, 100.0, width=6.0, shield=True),
                  _wire(5, 1, 0, 8.5, 50.0, 150.0),
                  _wire(5, 1, 1, 11.5, 50.0, 150.0)],
                 bit_nets={5: ["n5_0", "n5_1"]}, shield_nets={2: "GND"})
    assert len(vs) == 2
    assert all(v.bit_index < 0 for v in vs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._report_violations_summary([("Bundle 2", v) for v in vs])
    out = buf.getvalue()
    assert "Seg 3<->Bundle 5 Seg 1: 2 wire pair(s) — different nets'" in out, out
    assert "Total: 2 violation(s) in 1 group(s) across 2 bundle(s)" in out, out


def test_persistence_and_the_audit_name_a_wire_the_same_way(quickstart):
    # `_wire_nets` is the one statement of identity both read; the judge
    # reads what persistence stores, so this is what keeps it and the
    # engine audit agreeing about "one net".
    s = quickstart
    bit_nets, shield_nets = s._wire_nets()
    for w in s.bundles:
        bid = w.input.original_bundle.id
        assert bit_nets[bid] == list(w.input.original_bundle.get_net_names())
        assert shield_nets[bid] == "GND"          # no NDR rule in this flow


# ── against the judge, on the issue's own repro ────────────────────────────

@pytest.mark.mid
@pytest.mark.skipif(shutil.which("tclsh") is None,
                    reason="no tclsh on this host")
def test_check_design_and_the_judge_count_the_same_shorts(tmp_path):
    """The issue's repro, healerless, one round: `-judge` checkpoints the
    round and runs `tools/independent_audit.py` on it.  The judge's SHORT
    count and check_design's BIT_SHORT count must agree — they read the same
    rectangles and the same net names — and the vehicle must still HAVE a
    short, or the agreement is vacuous: if a routing change removes it,
    this test needs another vehicle that shorts, not a relaxed assertion."""
    out = tmp_path / "j"
    r = subprocess.run(
        ["tclsh", str(_ROOT / "flow" / "tcl" / "converge.tcl"), "soc", "4",
         "-arms", "blind", "-maxreserve", "0", "-informed", "0", "-judge",
         "-out", str(out)],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=tmp_path, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    stem = "soc4_healerless_step1_judged_blind_r1"
    judged = json.loads((out / f"{stem}.judge.json").read_text())
    judge_shorts = judged["counts"]["SHORT"]
    log = (out / f"{stem}.log").read_text(errors="replace")
    # the dnuts-stage audit's short lines: "Bundle a: Seg i<->Bundle b Seg
    # j: N bit(s) — ... (a short)" (cross-bundle) and "Bundle a: Seg
    # i<->j: N bit(s) — ... (a short)" (inside one bundle); "N wire
    # pair(s)" when one wire shorts several or a shield is in the pair
    engine = sum(int(m) for m in re.findall(
        r"Bundle \d+: Seg \d+<->(?:Bundle \d+ Seg )?\d+: (\d+) "
        r"(?:bit|wire pair)\(s\) — different nets' metal overlaps", log))
    assert judge_shorts > 0, (
        "the vehicle no longer produces a short: the agreement below would "
        "be vacuous — find a vehicle that shorts (see the docstring)")
    assert engine == judge_shorts, (engine, judge_shorts)
