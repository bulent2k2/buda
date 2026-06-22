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
Regression: NUTS try_repack must honour each member's pull preference.

Bug (src/nuts.cpp try_repack): when a later segment could not find a gap, the
window-defragmenting repack re-placed EVERY contending member with first_fit —
bottom-edge packing that ignores pull.  A phase-1 pulled anchor that place_seg
had correctly parked at its pull-extreme was thus dragged to the low edge of its
interval, stretching its connected stubs (longer wirelength).  On the big flat
design (flow/big_data_test) this silently lengthened ~40 trunk segments (e.g.
the reported B20: seg2 pulled to x=219 instead of its pull bound 2700; B28: seg1
to 9357 instead of 10950).

Fix: the repack now packs members pull-aware — each pulled member seeks its own
pull target, pull-free members pack low — and only falls back to the dense
first_fit pack when the pull-aware pass cannot fully fit the window (so a genuinely
tight window still resolves its overlap rather than dropping the segment).

This test drives the big flat flow through run_nuts in-process and asserts that
the aggregate distance of pulled segments from their pull bound stays well below
what the first_fit-only repack produced.  A reversion to first_fit packing spikes
that aggregate (≈99.3k vs the fixed ≈66.0k) and trips this test.
"""
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


@pytest.fixture(scope="module")
def big_nuts_session():
    from buda_cli import BudaSession
    cwd = os.getcwd()
    os.chdir(_BIG_DIR)            # so `source ../tracks4top.buda` etc. resolve
    try:
        s = BudaSession()
        s.script_path = "tc3a_flat.buda"
        for line in open("tc3a_flat.buda"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(_SKIP_PREFIXES):
                continue
            s.do_command(line)
            if line == _RUN_THROUGH:
                break
        yield s
    finally:
        os.chdir(cwd)


def _pull_deviation(session):
    """Sum over placed, pulled segments of the distance from the segment's pull
    bound (interval_hi when net_pull>0, interval_lo when net_pull<0).  This is
    exactly the wirelength the repack was needlessly adding by bottom-edging
    pulled anchors."""
    total, n = 0.0, 0
    for ts in session.nuts_result.segments:
        if not ts.placed or ts.net_pull == 0:
            continue
        pull = ts.interval_hi if ts.net_pull > 0 else ts.interval_lo
        total += abs(ts.track_position - pull)
        n += 1
    return total, n


def test_pulled_anchors_end_near_pull(big_nuts_session):
    """Aggregate pull deviation across the design.  Two mechanisms keep it low,
    and removing either trips this:
      - first_fit-only repack (the original bug)  → ≈99,300
      - pull-aware repack, no final tighten pass   → ≈65,970
      - pull-aware repack + tighten_pulls (current) → ≈50,700
    Threshold sits below the no-tighten value so the regression guard covers the
    final wirelength-tightening pass as well as the repack."""
    total, n = _pull_deviation(big_nuts_session)
    # Sanity: the design really does exercise many pulled segments (so this test
    # is meaningful, not vacuously passing on an all-net_pull==0 result).
    assert n > 100, f"expected many pulled segments, got {n}"
    assert total < 60_000.0, (
        f"pulled segments stray {total:.0f} from their pull bound across {n} "
        f"segments — repack is bottom-edging anchors and/or the tighten pass is "
        f"not pulling them in (expected < 60k; no-tighten ≈66k, buggy ≈99k)"
    )


def test_reported_bundles_reach_their_pull(big_nuts_session):
    """The three trunk segments from the bug report must land essentially AT
    their pull bound now that tighten_pulls slides them in.  Pre-fix these were
    bottom-edged far from pull (B9 seg1 at x=4959 vs pull 6820; B28 seg1 at
    x=10268 vs pull 10950)."""
    segs = {(ts.bundle_id, ts.seg_idx): ts
            for ts in big_nuts_session.nuts_result.segments}
    for bid, sidx in ((9, 1), (20, 2), (28, 1)):
        ts = segs.get((bid, sidx))
        assert ts is not None and ts.placed, f"B{bid} seg{sidx} missing/unplaced"
        assert ts.net_pull > 0, f"B{bid} seg{sidx} expected an upward pull"
        gap = ts.interval_hi - ts.track_position   # distance below the pull bound
        assert gap < 300.0, (
            f"B{bid} seg{sidx} sits {gap:.0f} below its pull bound "
            f"{ts.interval_hi:.0f} (track={ts.track_position:.0f}); tighten_pulls "
            f"should have slid it in"
        )


def test_tighten_does_not_trade_pull_for_overlaps(big_nuts_session):
    """Shorter wires must not be bought with new shorts: every tighten move is
    accepted only when it adds no overlap, so the final count stays at or below
    the pre-fix baseline."""
    res = big_nuts_session.nuts_result
    assert res.num_violations == 0, f"interval violations: {res.num_violations}"
    # first_fit-only repack left 7 abstract track overlaps here; pull-aware repack
    # + tighten leaves 4.  Guard against a regression that trades pull for shorts.
    assert res.num_overlaps <= 6, (
        f"abstract track overlaps regressed to {res.num_overlaps} (expected <= 6)"
    )
