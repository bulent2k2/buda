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

"""The anchor-interval pull model (#523/#539) surfaced in the two inspection
displays: `dump_topologies --conn` and the Topology Explorer segment info bar.

The model computes a per-segment flat-optimum interval `[pull_lo, pull_hi]`
(connected wirelength is minimized over it) plus the |slopes| just outside it
(`pull_f_lo`/`pull_f_hi`).  `net_pull` stays the DERIVED scalar; the displays
kept showing only that scalar (`pull=none(0)`), hiding the interval entirely
even when it is informative.  Both now append `opt=[lo..hi] f=lo/hi`.
"""
import contextlib
import io
import os
import sys

import matplotlib
matplotlib.use("Agg")

import buda
import buda_cli
import buda_viz


# A symmetric multi-tap star: every stub sits at a WL-flat position, so the
# DERIVED scalar reads none(0) everywhere while the flat-optimum intervals are
# real and wide — exactly the case where showing only the scalar loses the
# information.  The H trunk gets a bounded optimum with a >1 slope (several
# anchors), the V stubs get wide optima with unit slopes.
_CMDS = [
    "def_layer 4 M4 H TOP 44.44",
    "def_layer 5 M5 V TOP 50.00",
    "corner_margin dx 1 dy 2",
    "add_block u1 100 500 200 700",
    "add_block l1 150 100 250 300",
    "add_block u2 300 500 500 700",
    "add_block l2 350 100 450 300",
    "add_block u3 600 500 700 700",
    "add_block l3 550 100 650 300",
    "add_bus bus1[8] u1 u2,u3,l1,l2,l3",
    "run_bundler",
    "generate_topologies",
    "select_topology 1 15",         # a TRUNK_H in the mid channel
]


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        for c in _CMDS:
            s.do_command(c)
    return s


def _fmt_reports():
    from buda_session.reports import _fmt_pull_opt
    return _fmt_pull_opt


def _fmt_explorer():
    from viz_explorer.analysis import _fmt_pull_opt
    return _fmt_pull_opt


def test_conn_dump_shows_pull_optimum_and_slopes():
    """`dump_topologies --conn` appends `opt=[lo..hi] f=lo/hi` to every
    segment's pull field.  The selected TRUNK_H's trunk has a bounded
    optimum with matched slopes > 1 (several anchors pull it)."""
    s = _session()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command("dump_topologies --conn")
    out = buf.getvalue()
    assert "opt=[" in out, out
    assert "anchors=" in out, out
    # The trunk seg0's flat optimum [300..500] with a 6-anchor same-side count.
    assert "opt=[300..500] anchors=6/6" in out, out
    # Every conn-detail seg line that shows a pull also shows an optimum
    # (none of these segments is flat-everywhere).
    for line in out.splitlines():
        if "pull=" in line and "seg" in line:
            assert "opt=[" in line, f"pull line without optimum: {line}"


def test_explorer_info_bar_shows_pull_optimum():
    """The Topology Explorer segment banner (`_seg_conn_line`) appends the
    same `opt=[lo..hi] f=lo/hi` to its pull field."""
    s = _session()
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers)
    exp.idx = s.bundles[0].plan.selected_topology_index
    topo = exp._shown_topo()
    cs_list = list(exp._build_conn_topo(topo).segs())
    lines = []
    with contextlib.redirect_stdout(io.StringIO()):
        for si, cs in enumerate(cs_list):
            lines.append(exp._seg_conn_line(cs, si))
    joined = "\n".join(lines)
    assert "opt=[" in joined, joined
    assert any("opt=[300..500] anchors=6/6" in l for l in lines), joined


def test_both_formatters_agree_on_a_real_segment():
    """The two display layers keep independent copies of `_fmt_pull_opt`
    (no viz->session coupling); they must format identically."""
    s = _session()
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    cs0 = list(ct.segs())[0]          # the trunk, a bounded optimum
    fr, fe = _fmt_reports(), _fmt_explorer()
    assert fr(cs0) == fe(cs0)
    assert fr(cs0) == "  opt=[300..500] anchors=6/6"


def test_conn_omits_optimum_for_dogleg_overridden_segment():
    """When a segment carries a dogleg pull/slide override on the SELECTED
    topology, its ConnSeg optimum is recomputed from split geometry and is
    NOT what NUTS used (`build_nuts_maps` suppresses the interval), so
    `--conn` must omit `opt=` for it — Codex P2 on #555.  Simulate the
    override by pinning `plan.seg_net_pull` for one segment."""
    s = _session()
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    nseg = len(w.input.candidates[sel].segments)
    # Pin seg0's net_pull (INT_MIN = "unset" for the rest) — the dogleg
    # override shape build_nuts_maps keys on.
    w.plan.seg_net_pull = [1 if i == 0 else -2147483648 for i in range(nseg)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s._dump_conn_detail(w, sel)
    lines = [l for l in buf.getvalue().splitlines()
             if l.strip().startswith("seg")]
    assert lines
    # seg0 (overridden) has no opt=; a non-overridden seg still shows it.
    seg0 = next(l for l in lines if l.strip().startswith("seg0"))
    assert "opt=[" not in seg0, seg0
    assert any("opt=[" in l for l in lines if not l.strip().startswith("seg0"))
    # An UNSELECTED candidate ignores the override array (indexed by the
    # selected topology), so its segments keep showing opt=.
    other = next(i for i in range(len(w.input.candidates)) if i != sel)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2), buda.ostream_redirect():
        s._dump_conn_detail(w, other)
    assert "opt=[" in buf2.getvalue()


def test_flat_everywhere_optimum_is_omitted():
    """When the optimum is flat everywhere (the INT_MIN..INT_MAX sentinel —
    no anchor constrains the slide), both formatters emit nothing, so an
    unconstrained segment's line stays as terse as before."""
    fr, fe = _fmt_reports(), _fmt_explorer()

    class _Flat:
        pull_lo = -(2 ** 31)
        pull_hi = 2 ** 31 - 1
        pull_f_lo = 0
        pull_f_hi = 0

    assert fr(_Flat()) == ""
    assert fe(_Flat()) == ""


def test_one_sided_infinite_optimum_is_shown():
    """A half-bounded optimum (anchors on one side only) is informative and
    must be shown, with the infinite end rendered `-inf` / `+inf`."""
    fr = _fmt_reports()

    class _HalfLo:
        pull_lo = 300
        pull_hi = 2 ** 31 - 1
        pull_f_lo = 6
        pull_f_hi = 0

    class _HalfHi:
        pull_lo = -(2 ** 31)
        pull_hi = 500
        pull_f_lo = 0
        pull_f_hi = 6

    assert fr(_HalfLo()) == "  opt=[300..+inf] anchors=6/0"
    assert fr(_HalfHi()) == "  opt=[-inf..500] anchors=0/6"
