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

"""Keepout-model audit regressions (docs/internal/keepout_model_audit.md).

The abstract and detailed stages used to disagree about what a keepout
blocks:

* DetailedNUTS sampled the track pattern (and keepouts) at ONE point — the
  span midpoint — so a keepout overlapping the wire's span but missing the
  midpoint was invisible, and every bit routed straight through it while
  every metric stayed clean (0 overlaps / 0 violations / 0 unplaced).
* Abstract NUTS avoids keepout-occupied intervals during placement, but its
  exhausted-window fallback commits the interval CENTRE with no metric — a
  segment sitting ON a keepout reported as clean.

Fixes under test:

* `RoutingGrid::signal_tracks_in_span` — track blocked when a keepout's perp
  extent covers the centre AND its along extent overlaps the WHOLE span;
  DetailedNUTS prefers this span-clear pool and falls back to the classic
  midpoint pool only when the clear pool is short (bit spans get
  junction-adjusted after placement and usually stop before the keepout, so
  hard-filtering on the abstract span would strand ~495 corpus bits that
  never actually reach it).
* `DetailedNUTSEngine::cull_keepout_crossers` — after `adjust_bit_spans`,
  a bit whose FINAL span still crosses a keepout is removed and counted
  (`num_unplaced` + `num_keepout_bits`) instead of being emitted as an
  illegal wire.  Zero false positives by construction: it judges the real
  adjusted geometry.
* `NUTSResult::num_keepout_conflicts` — abstract segments whose physical
  extent lands on a keepout overlapping their span (the exhausted-window
  fallback) are now counted and warned about.
"""
import io
import contextlib

import buda_cli

_PATN = ("POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 "
         "GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5")


def _run(session, cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _bits_crossing(result, layer, ko_a1, ko_a2, ko_p1, ko_p2):
    """Placed bits on `layer` whose span strictly overlaps the keepout's
    along extent while sitting on a keepout-covered track."""
    return [ns for ns in result.net_segments
            if ns.layer == layer
            and min(ns.span_lo, ns.span_hi) < ko_a2
            and max(ns.span_lo, ns.span_hi) > ko_a1
            and ko_p1 <= ns.track_position <= ko_p2]


# ---------------------------------------------------------------------------
# Compound repro: keepout x[350,450] y[0,100] on M4 covers the A→B wire's
# WHOLE slide window but only part of its span — and NOT the span midpoint
# (x=300).  Pinned to M4 there is nowhere legal to go:
#   before: NUTS 0 overlaps / 0 violations ("clean"), DNUTS 0 unplaced,
#           all 8 bits physically routed THROUGH the keepout.
#   after:  NUTS num_keepout_conflicts == 1 (the abstract segment had to be
#           committed on the keepout), DNUTS culls all 8 bits
#           (num_unplaced == num_keepout_bits == 8), zero illegal wires.
# ---------------------------------------------------------------------------

def test_pinned_keepout_window_is_loud_not_silent():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ("def_layer 4 M4 H TOP 50", "def_layer 6 M6 H TOP 50",
             "def_layer 5 M5 V TOP 50",
             f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 6 0 {_PATN}",
             f"def_track_pattern 5 0 {_PATN}",
             "add_block A 0 0 100 100", "add_block B 500 0 600 100",
             "add_keepout 350 0 450 100 M4",
             "add_bus n[8] A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3"))
    # Force the keepout layer (the planner would legitimately dodge to M6;
    # the audit is about what happens when there is no dodge).
    w = s.bundles[0]
    w.plan.seg_layers = [4]
    w.input.topology_pinned = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    out = buf.getvalue()

    # Abstract NUTS: the forced commit onto the keepout is counted + warned.
    assert s.nuts_result.num_keepout_conflicts == 1
    assert "placed ON a keepout" in out

    # DetailedNUTS: all 8 bits culled (no track clears the span, and the
    # junction-adjusted spans still cross the keepout), loudly.
    assert s.detailed_result.num_unplaced == 8
    assert s.detailed_result.num_keepout_bits == 8
    assert "final span crosses a keepout" in out

    # And — the point of the audit — no wire is emitted through the keepout.
    assert _bits_crossing(s.detailed_result, 4, 350, 450, 0, 100) == []


# ---------------------------------------------------------------------------
# Graze case: same shape, but the keepout covers only the bottom fifth of the
# window (y[0,40] of [0,200]).  The span-clear preferred pool still holds >= 8
# tracks, so every bit places legally above the keepout — the fix must NOT
# turn a dodgeable keepout into unplaced bits (the naive span-hard filter
# stranded ~495 corpus bits exactly this way).
# ---------------------------------------------------------------------------

def test_partial_keepout_is_dodged_not_culled():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
             f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 5 0 {_PATN}",
             "add_block A 0 0 100 200", "add_block B 500 0 600 200",
             "add_keepout 350 0 450 40 M4",   # bottom fifth of the window
             "add_bus n[8] A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3", "run_nuts",
             "run_detailed_nuts"))

    assert s.detailed_result.num_unplaced == 0
    assert s.detailed_result.num_keepout_bits == 0
    assert s.nuts_result.num_keepout_conflicts == 0
    # No bit landed on a keepout-covered track while spanning the keepout.
    assert _bits_crossing(s.detailed_result, 4, 350, 450, 0, 40) == []


# ---------------------------------------------------------------------------
# Empty-layer_ids zone = blocks EVERY layer (audit class 3).  Only reachable
# via the Python Floorplan API (the CLI requires explicit layers); topology
# predicates always honoured it, but abstract NUTS's keepout_occupied, the
# planner's band capacity, and the grid-installation paths all silently
# skipped such zones — so DetailedNUTS routed through them.  Same graze
# shape as above, zone declared with NO layers: every stage must now treat
# it exactly like the explicit-M4 zone (dodge legally, all counters 0).
# ---------------------------------------------------------------------------

def test_all_layer_zone_blocks_every_stage():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
             f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 5 0 {_PATN}",
             "add_block A 0 0 100 200", "add_block B 500 0 600 200"))
    s.fp.add_keepout_zone(350, 0, 450, 40, [])   # NO layers = all layers
    _run(s, ("add_bus n[8] A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3", "run_nuts",
             "run_detailed_nuts"))

    assert s.detailed_result.num_unplaced == 0
    assert s.detailed_result.num_keepout_bits == 0
    assert s.nuts_result.num_keepout_conflicts == 0
    # The zone reached the M4 grid (via _install_leaf_keepouts' all-layer
    # sync): no bit crosses it on a covered track.
    assert _bits_crossing(s.detailed_result, 4, 350, 450, 0, 40) == []
    # And abstract NUTS dodged it too (keepout_occupied saw the zone): the
    # bus segment's physical extent sits clear of y[0,40] over x[350,450].
    for ts in s.nuts_result.segments:
        if ts.layer == 4 and min(ts.span_lo, ts.span_hi) < 450 \
                and max(ts.span_lo, ts.span_hi) > 350:
            assert ts.track_position - ts.width / 2.0 >= 40


# ---------------------------------------------------------------------------
# KEEPOUT_CROSS (audit class 4 — verify keepout-blindness).  check_nuts /
# check_dnuts used to have no keepout test at all: the pinned repro's forced
# commit ON the keepout passed `check_connectivity nuts` with "Success: no
# opens found."  Now:
#   nuts  — a placed bus segment whose extent [pos ± w/2] lies on a keepout
#           overlapping its span is a KEEPOUT_CROSS violation (the live gap:
#           the exhausted-window fallback commit).
#   dnuts — a bit whose track centre sits inside a keepout overlapping its
#           final span is flagged with the cull's own predicate.  Pure
#           defense-in-depth: cull_keepout_crossers removes such bits from
#           every production result, so this only fires if a stage regresses
#           — which is why the test hand-builds the crossing wire.
# ---------------------------------------------------------------------------

def _pinned_keepout_session():
    """The compound repro, pinned to the keepout layer (see above)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ("def_layer 4 M4 H TOP 50", "def_layer 6 M6 H TOP 50",
             "def_layer 5 M5 V TOP 50",
             f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 6 0 {_PATN}",
             f"def_track_pattern 5 0 {_PATN}",
             "add_block A 0 0 100 100", "add_block B 500 0 600 100",
             "add_keepout 350 0 450 100 M4",
             "add_bus n[8] A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3"))
    w = s.bundles[0]
    w.plan.seg_layers = [4]
    w.input.topology_pinned = True
    return s


def test_check_nuts_flags_keepout_commit():
    import buda
    s = _pinned_keepout_session()
    _run(s, ("run_nuts",))
    assert s.nuts_result.num_keepout_conflicts == 1   # the engine counts it...

    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    res = buda.check_nuts(ct, s.nuts_result, topo, s.fp, s.layers, 1)
    kinds = [v.kind.name for v in res.violations]
    # ...and the audit now sees the same event instead of blessing it.
    assert kinds.count("KEEPOUT_CROSS") == 1, kinds
    v = next(v for v in res.violations if v.kind.name == "KEEPOUT_CROSS")
    assert v.seg_idx == 0 and "placed ON keepout" in v.message

    # CLI surface: the nuts-stage check reports it, no false Success.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("check_connectivity nuts")
    out = buf.getvalue()
    assert "Success" not in out and "keepout" in out.lower()


def test_check_dnuts_flags_crossing_bit_defense_in_depth():
    import buda
    s = _pinned_keepout_session()
    _run(s, ("run_nuts", "run_detailed_nuts"))
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)

    # Production result: the cull already removed the crossing bits, so the
    # audit reports them UNPLACED — and no KEEPOUT_CROSS false positive.
    res = buda.check_dnuts(ct, s.detailed_result, topo, s.fp, s.layers, 1, 8)
    kinds = [v.kind.name for v in res.violations]
    assert "KEEPOUT_CROSS" not in kinds
    assert kinds.count("UNPLACED") == 8

    # Synthetic regression wire (a stage that stopped culling): one bit
    # through the keepout must be flagged.
    fake = buda.DetailedNUTSResult()
    ns = buda.NetSegment()
    ns.bundle_id, ns.seg_idx, ns.bit_index = 1, 0, 0
    ns.layer, ns.width = 4, 1.0
    ns.track_position = 50.0                 # inside keepout y[0,100]
    ns.span_lo, ns.span_hi = 100.0, 500.0    # crosses keepout x[350,450]
    fake.net_segments = [ns]
    res = buda.check_dnuts(ct, fake, topo, s.fp, s.layers, 1, 1)
    kinds = [v.kind.name for v in res.violations]
    assert kinds.count("KEEPOUT_CROSS") == 1, kinds
    v = next(v for v in res.violations if v.kind.name == "KEEPOUT_CROSS")
    assert v.seg_idx == 0 and v.bit_index == 0 and "crosses keepout" in v.message


def test_check_keepout_cross_no_false_positive_on_clean_flow():
    import buda
    # The graze case places everything legally — the new checks must stay
    # silent at both stages.
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
             f"def_track_pattern 4 0 {_PATN}", f"def_track_pattern 5 0 {_PATN}",
             "add_block A 0 0 100 200", "add_block B 500 0 600 200",
             "add_keepout 350 0 450 40 M4",
             "add_bus n[8] A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3", "run_nuts",
             "run_detailed_nuts"))
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    res_n = buda.check_nuts(ct, s.nuts_result, topo, s.fp, s.layers, 1)
    res_d = buda.check_dnuts(ct, s.detailed_result, topo, s.fp, s.layers, 1, 8)
    assert [v.kind.name for v in res_n.violations] == []
    assert [v.kind.name for v in res_d.violations] == []
