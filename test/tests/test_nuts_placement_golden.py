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

"""Phase 0 byte-identity gate (docs/internal/nuts_dnuts_refactor.md §3, §4).

Every corpus flow's FINAL placement — each TrackSegment's track_position,
adjusted span, interval, corner bounds and flags, and each NetSegment /
NetVia of the detailed stage — must match the committed golden byte-for-byte.
Any refactor of nuts.cpp / detailed_nuts.cpp that moves a single wire fails
here with a line-level diff (large flows: a (stage, layer) digest that names
the location; regenerate the full snapshot on the baseline tree for the
reviewable diff).

Deliberate re-baseline: rerun `PYTHONPATH=build:tools python3
tools/nuts_snapshot.py` and review the golden diff in the PR.
"""
import difflib
import os

import pytest

import nuts_snapshot as ns

pytestmark = pytest.mark.mid   # full-pipeline runs are integration tests


def _check_flow(flow):
    golden = ns.golden_path(flow)
    assert os.path.exists(golden), (
        f"missing golden {golden} — run tools/nuts_snapshot.py to create it")
    s = ns.run_flow(flow)
    live = (ns.snapshot_digest(s) if flow in ns.DIGEST_FLOWS
            else ns.snapshot_session(s))
    want = open(golden).read()
    if live == want:
        return
    diff = list(difflib.unified_diff(
        want.splitlines(), live.splitlines(),
        fromfile="golden", tofile="live", lineterm=""))
    head = "\n".join(diff[:40])
    pytest.fail(
        f"{flow}: placement snapshot deviates from golden "
        f"({len(diff)} diff lines).\nIf the change is intentional, re-run "
        f"tools/nuts_snapshot.py and commit the golden diff.\n{head}")


@pytest.mark.parametrize("flow", ns.CORPUS_SMALL)
def test_nuts_placement_matches_golden(flow):
    _check_flow(flow)


@pytest.mark.slow
@pytest.mark.parametrize("flow", ns.CORPUS_LARGE)
def test_nuts_placement_matches_golden_large(flow):
    _check_flow(flow)
