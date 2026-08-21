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

"""`set_placed_endpoints`: a junction NUTS contracted a span onto IS that end.

`is_endpoint` is derived once, at generation, from nominal coordinates
(topology_analysis.cpp: at_pos == along_lo || along_hi).  NUTS then moves the
ends -- tighten_spans_to_reach contracts a span back to its outermost junction
-- and nothing re-derives the label.  DetailedNUTS reads it as the ONLY gate on
per-bit snapping: an endpoint conn pulls each bit onto its own partner bit's
track, a mid-span conn merely asks the span to keep covering.  So a junction
that was interior at nominal and is the placed end leaves every bit stretched
to one shared abstract end.

Vehicle: bundle 67 of the bigHalf 5x design, pinned to candidate 18
(TRUNK_H+MST@y1700).  seg2 runs y[500,2770] nominally with junctions only at
1700 (seg0) and 500 (seg3); NUTS contracts its placed span to [428,1754] and
seg0's placed track IS 1754 -- yet the label still says mid.

See docs/internal/hybrid_leg_overhang.md.
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda
import buda_cli

_ROOT = _FLOWDIR = Path(__file__).parents[2]
_TRACKS = _ROOT / "flow/tracks/tracks4top.buda"
_DESIGN = _ROOT / "flow/big_data_test/tc3a_flat_5x.buda"

_BUNDLE = 67
_CAND = 18          # 1-based, TRUNK_H+MST@y1700
_STALE_SEG = 2      # the segment whose hi end NUTS contracts onto seg0

pytestmark = pytest.mark.mid


@contextlib.contextmanager
def _placed_endpoints(on):
    """Set the knob and always put it back -- it is process-global (a module
    flag, so every make_bus_segments caller agrees), so a leak would silently
    change every later test in the session."""
    prev = buda.dnuts_placed_endpoints()
    buda.dnuts_set_placed_endpoints(on)
    try:
        yield
    finally:
        buda.dnuts_set_placed_endpoints(prev)


def _solve(on):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        f"source {_TRACKS}",
        f"source {_DESIGN}",
        "run_bundler",
        "generate_topologies_for_bundle bus_005",
        "run_planner signal_tracks",
        f"select_topology bus_005 {_CAND}",
        "run_nuts",
        "run_detailed_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        with _placed_endpoints(on):
            for c in cmds:
                s.do_command(c)
    return s


@pytest.fixture(scope="module")
def off():
    return _solve(False)


@pytest.fixture(scope="module")
def on():
    return _solve(True)


def _antennas(s, seg_idx=None):
    """The ENGINE's verdict -- the same check_dnuts `check_design` runs, so a
    test cannot pass against a number the flow disagrees with."""
    w = next(w for w in s.bundles
             if w.input.original_bundle.id == _BUNDLE)
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    res = buda.check_dnuts(ct, s.detailed_result, topo, s.fp, s.layers,
                           _BUNDLE, len(w.input.original_bundle.net_names))
    return [v for v in res.violations
            if v.kind == buda.ViolationKind.ANTENNA
            and (seg_idx is None or v.seg_idx == seg_idx)]


def _bus_seg(s, on, seg_idx):
    """The stage-9 descriptor for one segment -- what DetailedNUTS actually
    read, not the nominal ConnSeg.

    The knob MUST be re-applied around this call: it is a module flag consulted
    inside make_bus_segments, and the solve fixtures restore it when they
    finish.  Rebuilding outside the context silently re-derives with the OTHER
    setting -- which is how the first cut of this file made the promote-only
    assertion below pass vacuously.
    """
    with _placed_endpoints(on):
        bus = buda.make_bus_segments(s.bundles, s.nuts_result, s.fp, "LO_HI",
                                     s.layers)
    return next(b for b in bus
                if b.bundle_id == _BUNDLE and b.seg_idx == seg_idx)


# ── the defect, and that the knob removes it ────────────────────────────────

def test_defect_reproduces_with_knob_off(off):
    """THE regression.  Without the knob, seg2's bits are held out to the
    shared abstract end and the engine reports it per bit."""
    assert len(_antennas(off, _STALE_SEG)) > 0


def test_knob_clears_them(on):
    assert _antennas(on, _STALE_SEG) == []


def test_knob_clears_the_whole_bundle(on):
    """Not just the one segment: turning the label fix on leaves this bundle
    with no dangling-metal finding anywhere."""
    assert _antennas(on) == []


def test_the_junction_really_is_the_placed_end(off):
    """The premise, stated as an assertion rather than assumed: NUTS contracts
    seg2's span onto seg0's placed track, so 'mid' is contradicted by the
    geometry the flow actually built."""
    placed = {t.seg_idx: t for t in off.nuts_result.segments
              if t.bundle_id == _BUNDLE}
    seg2, seg0 = placed[_STALE_SEG], placed[0]
    assert min(seg2.span_lo, seg2.span_hi) <= seg0.track_position
    assert max(seg2.span_lo, seg2.span_hi) == pytest.approx(
        seg0.track_position), "seg2's hi end should be seg0's placed track"


def test_label_flips_only_with_the_knob(off, on):
    """Off: the conn to seg0 reads mid (the stale nominal label).
    On: it reads endpoint."""
    def label(s, knob):
        return next(c.is_endpoint
                    for c in _bus_seg(s, knob, _STALE_SEG).connections
                    if c.seg_idx == 0)
    assert label(off, False) is False
    assert label(on, True) is True


# ── PROMOTE ONLY -- the half that was measured and rejected ─────────────────

def test_never_demotes_a_nominal_endpoint(on):
    """Pins the rule that the first prototype got wrong.

    The symmetric version also DEMOTED a nominal endpoint that placement had
    moved the span end past.  That clears has_ep_*, which makes the end
    eligible for the tapered retraction to pres_*, cutting the wire short of a
    partner it still has to meet -- real SEG_OPENs, and corpus-wide 14 flows
    worse against 1 with detailed WL UP.

    seg0 is exactly such a case here: nominally [700,1050] with its seg1
    junction at its along_hi, but placement stretches it to x=3112 and seats
    seg1 at 996, leaving that junction interior.  It must STAY an endpoint.
    """
    seg0 = _bus_seg(on, True, 0)         # knob ON -- else this is vacuous
    to_seg1 = next(c for c in seg0.connections if c.seg_idx == 1)
    placed = {t.seg_idx: t for t in on.nuts_result.segments
              if t.bundle_id == _BUNDLE}
    # the premise: placement really did move seg0's end past this junction
    assert placed[1].track_position not in (seg0.span_lo, seg0.span_hi)
    assert to_seg1.is_endpoint is True, "a nominal endpoint must never be demoted"


def test_no_new_opens_on_the_flow_the_symmetric_rule_broke(tmp_path):
    """big_3bundles_sel_pure_mst_topo is where the demotion showed itself:
    `Seg 9<->10: 12 bit(s) — segment disconnected`.  Guard the whole flow's
    audit, not just that pair, so any future re-introduction is caught."""
    flow = _ROOT / "flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda"
    kinds = {}
    for on_ in (False, True):
        s = buda_cli.BudaSession()
        s.no_viz = True
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            with _placed_endpoints(on_):
                s.do_command(f"source {flow}")
        found = set()
        for w in s.bundles:
            if not w.plan or not w.input.candidates:
                continue
            topo = w.input.candidates[w.plan.selected_topology_index]
            ct = buda.ConnTopology()
            ct.build(topo, s.fp)
            res = buda.check_dnuts(ct, s.detailed_result, topo, s.fp, s.layers,
                                   w.input.original_bundle.id,
                                   len(w.input.original_bundle.net_names))
            found |= {v.kind for v in res.violations}
        kinds[on_] = found
    assert not (kinds[True] - kinds[False]), (
        f"knob introduced violation kinds: {kinds[True] - kinds[False]}")


# ── default and precedence ─────────────────────────────────────────────────

def test_default_is_off():
    """Off by default = byte-identical, which is what lets this land before the
    flip is argued.  (The env seed is read once at first access, so this asserts
    the compiled default only when BUDA_DNUTS_PLACED_ENDPOINTS is unset.)"""
    import os
    if os.environ.get("BUDA_DNUTS_PLACED_ENDPOINTS") == "1":
        pytest.skip("env override active")
    assert buda.dnuts_placed_endpoints() is False


def test_buda_command_sets_it():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with _placed_endpoints(False):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("set_placed_endpoints on")
        assert buda.dnuts_placed_endpoints() is True
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command("set_placed_endpoints off")
        assert buda.dnuts_placed_endpoints() is False


def test_buda_command_rejects_junk():
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with _placed_endpoints(False):
        with contextlib.redirect_stdout(buf):
            s.do_command("set_placed_endpoints maybe")
        assert "Error" in buf.getvalue()
        assert buda.dnuts_placed_endpoints() is False
