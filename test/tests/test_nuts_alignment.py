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
Abstract NUTS same-bundle alignment, EDF window repack, and rerun idempotency
on flow/planner3.buda (driven in-process for direct access to placements).

Mechanisms under test (src/nuts.cpp):
- Same-bundle segments never conflict in packing or metrics: per-bit they are
  the same nets and may share tracks (mirrors the DetailedNUTS same-bundle
  reservation exemption).
- Alignment siblings (same bundle, connected to the same perpendicular
  segment, same layer) prefer the sibling's track position — a multicast
  trunk's two stubs collapse onto one band, saving a track.
- try_repack orders the contended window's members earliest-deadline-first
  (interval_hi ascending) and includes ALL placed interval-overlapping
  segments, not just sweep-active ones.
"""
import os
import pytest
import buda

_FLOW = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'flow'))


@pytest.fixture(scope="module")
def planner3_session():
    from buda_cli import BudaSession
    cwd = os.getcwd()
    os.chdir(_FLOW)
    try:
        s = BudaSession()
        s.script_path = "planner3.buda"
        for line in open("planner3.buda"):
            line = line.strip()
            if not line or line.startswith("#") or line == "visualize":
                continue
            s.do_command(line)
        yield s
    finally:
        os.chdir(cwd)


def _m6_segs(session):
    return {(ts.bundle_id, ts.seg_idx): ts
            for ts in session.nuts_result.segments if ts.layer == 6}


def test_same_trunk_stubs_share_one_band(planner3_session):
    """B2's two M6 stubs hang off the same V trunk: aligned → one band."""
    m6 = _m6_segs(planner3_session)
    s1, s2 = m6[(2, 1)], m6[(2, 2)]
    assert s1.track_position == pytest.approx(s2.track_position), (
        f"same-bundle stubs off one trunk must align: "
        f"{s1.track_position} vs {s2.track_position}"
    )


def test_planner3_m6_window_packed_cleanly(planner3_session):
    res = planner3_session.nuts_result
    assert res.num_overlaps   == 0
    assert res.num_violations == 0
    # Distinct M6 bands: alignment saves one (4 buses of demand on 4 bands,
    # with B2's pair counted once).
    m6 = _m6_segs(planner3_session)
    positions = {ts.track_position for ts in m6.values()}
    assert len(positions) == len(m6) - 1, (
        f"expected exactly one shared band among {len(m6)} M6 segments, "
        f"got positions {sorted(positions)}"
    )


def test_rerun_layer_is_idempotent(planner3_session):
    """A second NUTS pass on M6 must keep the clean packing (deterministic
    inputs → deterministic result, no new overlaps)."""
    s = planner3_session
    before = {(ts.bundle_id, ts.seg_idx): ts.track_position
              for ts in s.nuts_result.segments if ts.layer == 6}
    s.do_command("run_nuts_on_layer M6")
    after = {(ts.bundle_id, ts.seg_idx): ts.track_position
             for ts in s.nuts_result.segments if ts.layer == 6}
    assert s.nuts_result.num_overlaps == 0
    assert after == before


def test_rerun_layer_keeps_single_layer_contract(planner3_session):
    """run_nuts_on_layer re-solves and tightens ONLY the named layer: every other
    layer's track positions must be byte-identical afterwards.  Guards the
    tighten_pulls(only_layer=...) restriction added for rerun_layer."""
    s = planner3_session
    other_before = {(ts.bundle_id, ts.seg_idx): ts.track_position
                    for ts in s.nuts_result.segments if ts.layer != 6}
    s.do_command("run_nuts_on_layer M6")
    other_after = {(ts.bundle_id, ts.seg_idx): ts.track_position
                   for ts in s.nuts_result.segments if ts.layer != 6}
    assert other_after == other_before, (
        "run_nuts_on_layer M6 perturbed a non-M6 track position — tighten must "
        "only slide segments on the re-solved layer"
    )
    assert s.nuts_result.num_overlaps == 0
    assert s.nuts_result.num_violations == 0
