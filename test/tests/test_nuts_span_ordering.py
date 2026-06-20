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
