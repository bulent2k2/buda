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

"""Phase 0 byte-identity gate (docs/internal/topo_conn_unification.md §3).

Every corpus flow's generation-stage output — the full candidate list per
bundle AND its complete derived analysis (all ConnSeg fields incl. slide
windows, net_pull, along-flex, and the ORDERED conns list) — must match the
committed golden byte-for-byte.  This is the candidate-list snapshot (item 4)
and the analysis-equivalence gate (item 5) in one: any refactor of
topology.cpp / conn_topology.cpp that changes a single derived value or a
single line ordering WITHIN a candidate fails here with a line-level diff.

ORDER-CANONICAL comparison: candidate RANKING order within a pool is
deliberately excluded from the golden contract — both sides (the live
snapshot AND the loaded golden) are canonicalized by sorting each bundle's
per-candidate blocks by their own content (topo_snapshot.canonicalize)
before comparing.  The ranking is a selection policy (annotate_and_sort's
(wl, nsegs, type) key) with its own dedicated test
(test_topo_structural_tiebreak.py); goldens guard the per-candidate
geometry + derived-analysis CONTENT.  This keeps the on-disk goldens owned
by the reference host: a pure tie-break permutation requires no golden
change at all.  Line order INSIDE a candidate block (segments, the conns
list — the frozen NUTS tie-break contract) is still exact.

Large flows (big, rnr/mix) gate on per-bundle sha256 digests instead of
full text (multi-MB); the digest hashes the CANONICALIZED bundle block, so
it is order-independent by construction.  A digest mismatch names the
bundle, and the reviewable diff comes from regenerating the full snapshot
on the baseline tree (tools/topo_snapshot.py <out_dir>) — the wl_corpus
comparison workflow.

Deliberate re-baseline (reference host only): rerun `PYTHONPATH=build:tools
python3 tools/topo_snapshot.py` and review the golden diff in the PR.
"""
import difflib
import os

import pytest

import topo_snapshot as ts


def _live_and_golden(flow):
    """Run generation and load the golden, raising a HARD failure on a missing
    golden or a generation crash.  Returns (live, want) for comparison so both
    the strict path and the pending-regen path share the same loud checks."""
    golden = ts.golden_path(flow)
    assert os.path.exists(golden), (
        f"missing golden {golden} — run tools/topo_snapshot.py to create it")
    s = ts.run_flow_generation(flow)
    live = (ts.snapshot_digest(s) if flow in ts.DIGEST_FLOWS
            else ts.canonicalize(ts.snapshot_session(s)))
    want = open(golden).read()
    if flow not in ts.DIGEST_FLOWS:
        # Goldens generated before the canonical sort are in ranking order;
        # canonicalizing the loaded text keeps them byte-owned by the
        # reference host while excluding candidate order from the contract.
        want = ts.canonicalize(want)
    return live, want


def _check_flow(flow):
    live, want = _live_and_golden(flow)
    if live == want:
        return
    diff = list(difflib.unified_diff(
        want.splitlines(), live.splitlines(),
        fromfile="golden", tofile="live", lineterm=""))
    head = "\n".join(diff[:40])
    pytest.fail(
        f"{flow}: generation-stage snapshot deviates from golden "
        f"({len(diff)} diff lines).\nIf the change is intentional, re-run "
        f"tools/topo_snapshot.py and commit the golden diff.\n{head}")


@pytest.mark.parametrize("flow", ts.CORPUS_FAST)
def test_topo_analysis_matches_golden(flow):
    _check_flow(flow)


# Flows whose generation-stage goldens shift under the issue-#57 collinear-stub
# MERGE (complete_relay_junctions now combines collinear opposite-face stubs into
# one straight pass-through wire even when a far end taps a block, removing the
# staircase jog -> cleaner candidate geometry -> different per-bundle digest).
# The change is intentional and QoR-neutral-or-better; the digests need a
# REFERENCE-HOST re-baseline (tools/topo_snapshot.py, per the module docstring).
# Remove a flow from this set once its golden is regenerated on the reference host.
_PENDING_REGEN_ISSUE_57 = {
    "flow/big_data_test/big.buda",
    "flow/rnr/mix.buda",
}


@pytest.mark.mid
@pytest.mark.parametrize("flow", ts.CORPUS_MID)
def test_topo_analysis_matches_golden_mid(flow):
    if flow in _PENDING_REGEN_ISSUE_57:
        # Still exercise generation AND assert the golden exists (a crash or a
        # missing/corrupt golden must fail loudly, not hide as an expected
        # xfail); xfail ONLY the known digest mismatch until the reference host
        # regenerates (Codex #406).  If the digests already match (regenerated),
        # the test simply passes and the flow can be dropped from the set.
        live, want = _live_and_golden(flow)
        if live == want:
            return
        pytest.xfail(
            "issue #57 collinear-stub merge changed candidate geometry; golden "
            "digest pending reference-host regen (tools/topo_snapshot.py)")
    _check_flow(flow)
