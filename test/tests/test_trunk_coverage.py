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

"""A trunk must reach every segment connected to it (coverage guarantee).

NUTS' span-adjustment splits a trunk's connections into lo-end / hi-end groups
using NOMINAL geometry.  When placement moves a tap across the trunk — e.g. a
keepout (or congestion) pushes a stub past the far end — that stale label routes
the stub to the wrong end and the trunk stops short of it, producing a real open.

flow/stub_order_swap.buda is the minimal reproducer (3 blocks, one bus, with a
keepout inserted between run_planner and run_nuts): the M7 stub to blk_06 is
forced left of the keepout, outside the planned trunk span.  With the coverage
pass the trunk extends to reach it; without it, Seg 0 and Seg 2 are open.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_FLOW = os.path.join(_ROOT, "flow", "stub_order_swap.buda")


def test_trunk_reaches_stub_moved_across_far_end():
    import buda
    import buda_cli

    sess = buda_cli.BudaSession()
    sess.no_viz = True
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    os.dup2(devnull, 1)
    try:
        sess.do_command(f"source {_FLOW}")
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)

    assert sess.nuts_result is not None and sess.detailed_result is not None

    # The single bundle's trunk must reach both vias at both stages.
    opens = []
    for w in sess.bundles:
        if not w.input.candidates or w.plan.selected_topology_index < 0:
            continue
        bid = w.input.original_bundle.id
        topo = w.input.candidates[w.plan.selected_topology_index]
        ct = buda.ConnTopology()
        ct.build(topo, sess.fp)
        num_bits = len(w.input.original_bundle.get_net_names())
        for res in (buda.check_nuts(ct, sess.nuts_result, topo, sess.fp, sess.layers, bid),
                    buda.check_dnuts(ct, sess.detailed_result, topo, sess.fp,
                                     sess.layers, bid, num_bits)):
            opens += [v.message for v in res.violations
                      if v.kind == buda.ViolationKind.SEG_OPEN]

    assert not opens, f"trunk under-extension opens: {opens}"
