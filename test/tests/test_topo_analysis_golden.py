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
single ordering fails here with a line-level diff.

Large flows (big, rnr/mix) gate on per-bundle sha256 digests instead of
full text (multi-MB); a digest mismatch names the bundle, and the reviewable
diff comes from regenerating the full snapshot on the baseline tree
(tools/topo_snapshot.py <out_dir>) — the wl_corpus comparison workflow.

Deliberate re-baseline: rerun `PYTHONPATH=build:tools python3
tools/topo_snapshot.py` and review the golden diff in the PR.
"""
import difflib
import os

import pytest

import topo_snapshot as ts


def _check_flow(flow):
    golden = ts.golden_path(flow)
    assert os.path.exists(golden), (
        f"missing golden {golden} — run tools/topo_snapshot.py to create it")
    s = ts.run_flow_generation(flow)
    live = (ts.snapshot_digest(s) if flow in ts.DIGEST_FLOWS
            else ts.snapshot_session(s))
    want = open(golden).read()
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


@pytest.mark.mid
@pytest.mark.parametrize("flow", ts.CORPUS_MID)
def test_topo_analysis_matches_golden_mid(flow):
    _check_flow(flow)
