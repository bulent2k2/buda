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

"""Span-ordering invariant for NUTS / detailed-NUTS results.

A bus segment's span is stored as ``[span_lo, span_hi]`` with the convention
``span_lo <= span_hi``.  The follower span-adjustment in both abstract NUTS
(`nuts.cpp`) and detailed NUTS (`detailed_nuts.cpp`) derives a segment's extent
from its endpoint connections, whose ``lo_end``/``hi_end`` labels are *nominal*.
NUTS placement can swap the actual order of the two end connections (e.g. a
Z-trunk whose two via stubs pack in reversed order under congestion), which used
to leave ``span_lo > span_hi`` — a backwards but geometrically valid extent that
the connectivity checks misread as an open (false-positive disconnects, seen on
``tc3a`` bundles 15 & 60).  Both engines now normalise the span.

These tests pin the invariant on self-contained flows that route Z / U / trunk
topologies through detailed NUTS, so a regression that reintroduces reversed
spans is caught here.
"""
import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Self-contained flows (no machine-specific `source` paths) that exercise
# trunk/Z/U shapes plus run_detailed_nuts.
_FLOWS = [
    "flow/dnuts1.buda",          # Z_VHV + U_VHV between offset blocks
    "flow/dnuts2.buda",
    "flow/flip_bit_order.buda",  # U_VHV
    "flow/two_dnuts_b1.buda",    # Z_HVH
    "flow/keepout_demo.buda",    # U_VHV around a keepout
]


def _run(flow_name):
    import buda_cli
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    # Silence the flow's own stdout chatter while sourcing.
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    os.dup2(devnull, 1)
    try:
        sess.do_command(f"source {os.path.join(_ROOT, flow_name)}")
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)
    return sess


@pytest.mark.parametrize("flow", _FLOWS)
def test_nuts_spans_are_ordered(flow):
    sess = _run(flow)
    nr = sess.nuts_result
    assert nr is not None, f"{flow} produced no NUTS result"
    bad = [(t.bundle_id, t.seg_idx, t.span_lo, t.span_hi)
           for t in nr.segments if t.span_lo > t.span_hi]
    assert not bad, f"{flow}: reversed abstract-NUTS spans (span_lo > span_hi): {bad}"


@pytest.mark.parametrize("flow", _FLOWS)
def test_detailed_nuts_spans_are_ordered(flow):
    sess = _run(flow)
    dr = sess.detailed_result
    if dr is None:
        pytest.skip(f"{flow} has no detailed-NUTS result")
    bad = [(t.bundle_id, t.seg_idx, t.bit_index, t.span_lo, t.span_hi)
           for t in dr.net_segments if t.span_lo > t.span_hi]
    assert not bad, f"{flow}: reversed detailed-NUTS spans (span_lo > span_hi): {bad[:8]}"


# --- The original reproducer -------------------------------------------------
# tc3a_flat (40 blocks / 80 buses) is the design that first exposed the bug:
# bundles 15 (blk_05->blk_31) and 60 (blk_31->blk_05) are an opposite-direction
# pair routed as Z_VHV on the same trunk row, and congestion packs their via
# stubs in reversed order — producing span_lo > span_hi and false-positive
# disconnects at both the NUTS and detailed-NUTS stages.  Without the
# normalization this test fails; with it, bundles 15/60 are clean.
#
# It is a full-pipeline integration test (~1s in-process) so it lives in the
# mid tier.  Layer/track definitions are inlined (the original sources them from
# a machine-specific absolute path).

_X10 = os.path.join(_ROOT, "flow", "big_data_test", "tc3a_flat_x10.buda")

_TC3A_TRACKS = """\
def_layer 2 M2 H LOW 55.56
def_layer 3 M3 V LOW 55.56
def_layer 4 M4 H TOP 55.56
def_layer 5 M5 V TOP 55.56
def_layer 6 M6 H TOP 66.67
def_layer 7 M7 V TOP 58.97
def_track_pattern 2 -100  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5
def_track_pattern 3    0  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5
def_track_pattern 4 -200  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5
def_track_pattern 5    0  POWER 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  GROUND 2 1  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5  SIGNAL 1 0.5
def_track_pattern 6 -400  POWER 3 1  SIGNAL 1 1  SIGNAL 1 1 SIGNAL 1 1  SIGNAL 1 1  GROUND 3 1  SIGNAL 1 1 SIGNAL 1 1  SIGNAL 1 1  SIGNAL 1 1
def_track_pattern 7 -600  POWER 6 2  SIGNAL 2 1  SIGNAL 2 1  SIGNAL 2 1 SIGNAL 2 1  GROUND 6 1  SIGNAL 2 1 SIGNAL 2 1  SIGNAL 2 1  SIGNAL 2 1
"""


@pytest.mark.mid
def test_tc3a_flat_no_reversed_span_false_opens(tmp_path):
    if not os.path.exists(_X10):
        pytest.skip("tc3a_flat_x10.buda fixture not present")
    import buda
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

    nr = sess.nuts_result
    dr = sess.detailed_result
    assert nr is not None and dr is not None

    # Global invariant: every placed span is well-ordered.
    assert not [t for t in nr.segments if t.span_lo > t.span_hi], \
        "reversed abstract-NUTS spans present"
    assert not [t for t in dr.net_segments if t.span_lo > t.span_hi], \
        "reversed detailed-NUTS spans present"

    # The specific bundles that regressed must have no false-positive opens at
    # either stage (bundle 20's REAL open is tracked separately and not asserted
    # here, so this stays green when that defect is fixed).
    for bid in (15, 60):
        w = next(w for w in sess.bundles
                 if w.input.original_bundle.id == bid)
        topo = w.input.candidates[w.plan.selected_topology_index]
        ct = buda.ConnTopology()
        ct.build(topo, sess.fp)
        num_bits = len(w.input.original_bundle.get_net_names())
        opens_nuts = [v.message for v in
                      buda.check_nuts(ct, nr, topo, sess.fp, sess.layers, bid).violations
                      if v.kind == buda.ViolationKind.SEG_OPEN]
        opens_dnuts = [v.message for v in
                       buda.check_dnuts(ct, dr, topo, sess.fp, sess.layers, bid, num_bits).violations
                       if v.kind == buda.ViolationKind.SEG_OPEN]
        assert not opens_nuts, f"bundle {bid} false NUTS open(s): {opens_nuts}"
        assert not opens_dnuts, f"bundle {bid} false dnuts open(s): {opens_dnuts}"
