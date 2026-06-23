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

"""
Regression: NUTS tighten_pulls must pull segments toward their pull target,
including aligned sibling stubs that share a trunk junction.

History (all in src/nuts.cpp tighten_pulls / try_repack):
  - try_repack used to bottom-edge pulled anchors with first_fit;
  - tighten_pulls then slid single segments toward pull, but DEADLOCKED on
    aligned siblings: two V-stubs hanging off one H-trunk's junction bound the
    trunk by min(member positions), so moving either ALONE is wirelength-neutral
    and gets rejected — both stay bottom-edged (e.g. B20: both M7 stubs stuck at
    x=219 vs a free channel at ~1731).

Fix: tighten_pulls now moves an aligned group ATOMICALLY — each member toward its
own best (split) unless a shared track reaches the same trunk bound (collapse).
This breaks the deadlock and recovers the wirelength.

The metric here is `pull_target` (the resolved pull coordinate the code actually
aims at — nominal for unbounded MST trunks, not the raw interval edge), exposed
on TrackSegment so the test measures the code's true objective.
"""
import math
import os
import pytest

pytestmark = pytest.mark.mid

_FLOW = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'flow'))
_BIG_DIR = os.path.join(_FLOW, 'big_data_test')

# Lines of big.buda (== tc3a_flat.buda) to execute.  Stop at run_nuts: the pull
# placement is fixed there, and detailed-NUTS / connectivity add seconds without
# changing what this test measures.  check_connectivity is skipped for speed.
_RUN_THROUGH = "run_nuts"
_SKIP_PREFIXES = ("check_connectivity", "visualize", "run_detailed_nuts")


def _run_through_nuts(script_name):
    """Drive a .buda script in-process up to (and including) run_nuts.  Caller
    must already be cwd'd into the script's directory (so relative `source`
    lines resolve)."""
    from buda_cli import BudaSession
    s = BudaSession()
    s.script_path = script_name
    for line in open(script_name):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_SKIP_PREFIXES):
            continue
        s.do_command(line)
        if line == _RUN_THROUGH:
            break
    return s


@pytest.fixture(scope="module")
def big_nuts_session():
    cwd = os.getcwd()
    os.chdir(_BIG_DIR)            # so `source ../tracks4top.buda` etc. resolve
    try:
        yield _run_through_nuts("tc3a_flat.buda")
    finally:
        os.chdir(cwd)


def _build_big_session():
    """Fresh big-flow session (caller already cwd'd into _BIG_DIR)."""
    return _run_through_nuts("tc3a_flat.buda")


def _pull_deviation(session):
    """Sum over placed, pulled segments of the distance from the resolved
    `pull_target` — the coordinate tighten_pulls actually aims at.  This is the
    wirelength the pass is trying to recover; the group move drives it down."""
    total, n = 0.0, 0
    for ts in session.nuts_result.segments:
        if not ts.placed or math.isnan(ts.pull_target):
            continue
        total += abs(ts.track_position - ts.pull_target)
        n += 1
    return total, n


def test_pulled_anchors_end_near_pull(big_nuts_session):
    """Aggregate distance of pulled segments from their pull_target.  The group
    move drives this well below what per-segment tightening (which deadlocks on
    aligned siblings) leaves behind.  Current ≈ 43.5k; a regression that loses
    the atomic group move (B20-style trunks stuck at the interval floor) spikes
    it back up: per-segment tightening (no group move) leaves ≈ 50.1k, the group
    move ≈ 43.5k — the threshold sits between the two so the guard bites."""
    total, n = _pull_deviation(big_nuts_session)
    # Sanity: the design really does exercise many pulled segments (so this test
    # is meaningful, not vacuously passing on an all-pull_target==NaN result).
    assert n > 100, f"expected many pulled segments, got {n}"
    assert total < 47_000.0, (
        f"pulled segments stray {total:.0f} from their pull_target across {n} "
        f"segments — the aligned-sibling group move is not pulling trunks in "
        f"(expected < 47k; group-move ≈ 43.5k, per-segment-only ≈ 50.1k)"
    )


def _pulled_fraction(ts):
    """How far ts sits toward its pull side of its interval: 0 = at the far
    (non-pull) edge, 1 = at the pull edge.  Bottom-edged anchors read ≈ 0."""
    span = ts.interval_hi - ts.interval_lo
    if span <= 0:
        return 1.0
    f = (ts.track_position - ts.interval_lo) / span   # net_pull > 0 → pull = hi
    return f if ts.net_pull > 0 else 1.0 - f


def test_reported_bundles_reach_their_pull(big_nuts_session):
    """The trunk segments from the bug report must be pulled WELL toward their
    pull side, not bottom-edged.  B20's two M7 stubs are aligned siblings that
    used to deadlock at x=219 (fraction ≈ 0); the group move now lifts them into
    the upper part of their interval.  B9/B28 reach their pull_target tightly."""
    segs = {(ts.bundle_id, ts.seg_idx): ts
            for ts in big_nuts_session.nuts_result.segments}
    for bid, sidx in ((9, 1), (20, 2), (28, 1)):
        ts = segs.get((bid, sidx))
        assert ts is not None and ts.placed, f"B{bid} seg{sidx} missing/unplaced"
        assert ts.net_pull > 0, f"B{bid} seg{sidx} expected an upward pull"
        frac = _pulled_fraction(ts)
        assert frac > 0.6, (
            f"B{bid} seg{sidx} only {frac:.2f} toward its pull side "
            f"(track={ts.track_position:.0f}, interval "
            f"[{ts.interval_lo:.0f},{ts.interval_hi:.0f}]) — it is bottom-edged, "
            f"the aligned-sibling deadlock is back"
        )
    # B9 and B28 are not congestion-bound and should reach pull_target tightly.
    for bid, sidx in ((9, 1), (28, 1)):
        ts = segs[(bid, sidx)]
        assert abs(ts.track_position - ts.pull_target) < 250.0, (
            f"B{bid} seg{sidx} is {abs(ts.track_position - ts.pull_target):.0f} "
            f"from its pull_target {ts.pull_target:.0f}"
        )


def test_tighten_does_not_trade_pull_for_overlaps(big_nuts_session):
    """Shorter wires must not be bought with new shorts: every group move is
    accepted only when it adds no overlap/violation, so the count does not
    regress.  The group-move build clears them entirely (0)."""
    res = big_nuts_session.nuts_result
    assert res.num_violations == 0, f"interval violations: {res.num_violations}"
    # Pre-group-move builds left up to 5-7 abstract overlaps; the group move
    # frees enough congestion to reach 0.  Guard against a regression.
    assert res.num_overlaps <= 2, (
        f"abstract track overlaps regressed to {res.num_overlaps} (expected <= 2)"
    )


def test_group_split_under_asymmetric_congestion():
    """flow/nuts_group_pull.buda is the minimal B20: two aligned M7 V-stubs off
    one H-trunk, with two keepouts that carve DIFFERENT feasible regions.  The
    best COMMON track is ≈ 500, but splitting the siblings onto their own best
    tracks raises the laggard to ≈ 1000 — shorter trunk.  tighten_pulls must
    SPLIT here (not collapse to ~500): seg1 near its pull, seg2 capped just below
    its keepout.  Guards against a naive force-common-track group move."""
    cwd = os.getcwd()
    os.chdir(_FLOW)
    try:
        s = _run_through_nuts("nuts_group_pull.buda")
        segs = {(t.bundle_id, t.seg_idx): t for t in s.nuts_result.segments}
        seg1 = segs[(1, 1)]   # down-stub: feasible [200,500)∪(1600,2300], pull 2300
        seg2 = segs[(1, 2)]   # up-stub:   feasible [200,1000],            pull 2700
        assert seg1.track_position > 2000.0, (
            f"seg1 should pull to its upper feasible region (~2298), got "
            f"{seg1.track_position:.0f}"
        )
        assert 800.0 < seg2.track_position < 1200.0, (
            f"seg2 should sit just below its keepout (~997), got "
            f"{seg2.track_position:.0f} — collapsed to the common ~500?"
        )
        # The whole point: they are SPLIT (different tracks), not co-located.
        assert abs(seg1.track_position - seg2.track_position) > 500.0, (
            "siblings collapsed to one track — split was not taken"
        )
        assert s.nuts_result.num_overlaps == 0
        assert s.nuts_result.num_violations == 0
    finally:
        os.chdir(cwd)


def test_run_nuts_on_layer_keeps_other_layers_and_no_regression():
    """run_nuts_on_layer re-solves and tightens ONLY the named layer:
      (a) every OTHER layer's track positions stay byte-identical (the
          single-layer contract — tighten is only_layer-restricted), and
      (b) the re-solve does not regress overlaps or violations on the design.
    (Exact M7 byte-idempotency is intentionally NOT asserted: tighten's global
    span adjustments couple layers, so a single-layer re-solve legitimately
    reaches a different — equally valid — M7 packing than the full solve.)"""
    cwd = os.getcwd()
    os.chdir(_BIG_DIR)
    try:
        s = _build_big_session()
        m7 = s._layer_name_map["M7"]

        def positions(pred):
            return {(t.bundle_id, t.seg_idx): t.track_position
                    for t in s.nuts_result.segments if t.placed and pred(t)}

        other_before = positions(lambda t: t.layer != m7)
        ov_before    = s.nuts_result.num_overlaps

        s.do_command("run_nuts_on_layer M7")

        other_after = positions(lambda t: t.layer != m7)
        assert other_after == other_before, (
            "run_nuts_on_layer M7 perturbed a non-M7 track position — tighten "
            "must be restricted to the re-solved layer"
        )
        assert s.nuts_result.num_overlaps <= ov_before, (
            f"rerun regressed overlaps {ov_before} -> {s.nuts_result.num_overlaps}"
        )
        assert s.nuts_result.num_violations == 0
    finally:
        os.chdir(cwd)
