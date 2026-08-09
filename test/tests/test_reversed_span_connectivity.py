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

"""Connectivity is robust to nominally-reversed segment spans.

A bus segment stores its extent as ``[span_lo, span_hi]`` where ``span_lo`` is
the *lo_end* coordinate and ``span_hi`` the *hi_end* — an endpoint identity that
corner/dogleg logic relies on (it derives the fixed anchor as
``lo_end ? span_hi : span_lo``).  NUTS placement can swap the actual order of a
segment's two end connections (e.g. a Z-trunk whose via stubs pack in reversed
order under congestion), leaving ``span_lo > span_hi`` — a backwards but
geometrically valid extent.

The connectivity checks therefore must NOT assume ``span_lo <= span_hi``; they
compare against the ordered ``[min, max]`` extent.  Before that fix, the reversed
spans produced false-positive disconnects on ``tc3a`` bundles 15 & 60 at both
the NUTS and detailed-NUTS stages.  This test reproduces that design through the
full pipeline and asserts those bundles report no opens.
"""
import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The original reproducer: tc3a_flat (40 blocks / 80 buses).  Bundles 15
# (blk_05->blk_31) and 60 (blk_31->blk_05) are an opposite-direction pair routed
# as Z_VHV on the same trunk row; congestion packs their via stubs in reversed
# order, so the H-trunk's span is stored span_lo > span_hi.  The flow is a
# full-pipeline integration test (~1s in-process) -> mid tier.  Layer/track defs
# are inlined (the committed flow sources them from a separate file).
_X10 = os.path.join(_ROOT, "flow", "big_data_test", "tc3a_flat_x10.buda")

_TC3A_TRACKS = """\
def_layer 2 M2 H LOW 55.56
def_layer 3 M3 V LOW 55.56
def_layer 4 M4 H TOP 55.56
def_layer 5 M5 V TOP 55.56
def_layer 6 M6 H TOP 66.67
def_layer 7 M7 V TOP 58.97
def_track_pattern 2 -100  POWER 2 1  (SIGNAL 1 0.5)x4  GROUND 2 1  (SIGNAL 1 0.5)x4
def_track_pattern 3    0  POWER 2 1  (SIGNAL 1 0.5)x4  GROUND 2 1  (SIGNAL 1 0.5)x4
def_track_pattern 4 -200  POWER 2 1  (SIGNAL 1 0.5)x4  GROUND 2 1  (SIGNAL 1 0.5)x4
def_track_pattern 5    0  POWER 2 1  (SIGNAL 1 0.5)x4  GROUND 2 1  (SIGNAL 1 0.5)x4
def_track_pattern 6 -400  POWER 3 1  (SIGNAL 1 1)x4  GROUND 3 1  (SIGNAL 1 1)x4
def_track_pattern 7 -600  POWER 6 2  (SIGNAL 2 1)x4  GROUND 6 1  (SIGNAL 2 1)x4
"""


def _run_tc3a_flat(tmp_path):
    import buda_cli
    flow = tmp_path / "tc3a_flat_repro.buda"
    flow.write_text(
        _TC3A_TRACKS
        + f"source {_X10}\n"
        + "run_bundler\ngenerate_topologies\nrun_planner\n"
        + "run_nuts\nrun_detailed_nuts\n"
    )
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    os.dup2(devnull, 1)
    try:
        sess.do_command(f"source {flow}")
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)
    return sess


def _seg_opens(sess, bid):
    """SEG_OPEN violations for one bundle at the NUTS and detailed-NUTS stages."""
    import buda
    w = next(w for w in sess.bundles if w.input.original_bundle.id == bid)
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, sess.fp)
    num_bits = len(w.input.original_bundle.get_net_names())
    nuts = [v.message for v in
            buda.check_nuts(ct, sess.nuts_result, topo, sess.fp, sess.layers, bid).violations
            if v.kind == buda.ViolationKind.SEG_OPEN]
    dnuts = [v.message for v in
             buda.check_dnuts(ct, sess.detailed_result, topo, sess.fp,
                              sess.layers, bid, num_bits).violations
             if v.kind == buda.ViolationKind.SEG_OPEN]
    return nuts, dnuts


@pytest.mark.mid
def test_tc3a_flat_reversed_span_bundles_have_no_false_opens(tmp_path):
    if not os.path.exists(_X10):
        pytest.skip("tc3a_flat_x10.buda fixture not present")
    sess = _run_tc3a_flat(tmp_path)
    assert sess.nuts_result is not None and sess.detailed_result is not None

    # Sanity: the scenario is actually exercised — at least one placed segment is
    # stored with span_lo > span_hi (reversed endpoint identity).  If this stops
    # holding, the assertions below no longer guard the reversed-span path.
    reversed_present = any(t.span_lo > t.span_hi for t in sess.nuts_result.segments)
    assert reversed_present, "expected at least one reversed-span segment in tc3a_flat"

    # The bundles that regressed must report no opens at either stage.  (Bundle
    # 20's REAL open is tracked separately and is intentionally not asserted, so
    # this stays green when that defect is fixed.)
    for bid in (15, 60):
        nuts, dnuts = _seg_opens(sess, bid)
        assert not nuts, f"bundle {bid} false NUTS open(s): {nuts}"
        assert not dnuts, f"bundle {bid} false dnuts open(s): {dnuts}"
