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

"""Phase E3b — the TopologyExplorer's TopoEdit mode.

'e'/'E' open an edit session (copy of the shown candidate / empty topology);
while open: 'T'/'Y' add an H/V trunk at the cursor's bundle-grid line (full
span), 'S' stubs the block under the cursor to the selected segment, 'C'/'D'
pair-connect/-disconnect, 'W'/'W' refines the selected segment's slide window
at the cursor ('w' clears; staged windows land on plan.seg_slide_lo/hi at
commit), 'X' removes the selected segment, enter commits the result as a
uid-deduped USER candidate (pinned + sidecar'd), escape aborts.  Navigation
is parked while a session is open.  Driven headlessly through _on_key with
synthesized events.
"""

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test_runtime_analysis.md.
pytestmark = pytest.mark.mid
import os
import sys
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")   # headless; no window

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402
import buda      # noqa: E402


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "add_block b1 0 0 100 100",
        "add_block b2 200 0 300 100",
        # A keepout whose edges give the BUNDLE-scoped Hanan grid (busterm
        # blocks + keepouts — what edit snapping targets) a mid-channel
        # column (x=140/160) for the trunk to snap to.
        "add_keepout 140 200 160 260 4",
        # A non-busterm block: its edges are in the FULL grid but must be
        # absent from the bundle-scoped one (and never a snap target).
        "add_block c1 500 300 520 340",
        "add_bus v[4] b1.o b2.i",
        "run_bundler",
        "generate_topologies",
    ):
        s.do_command(cmd)
    return s


def _key(exp, key, x=None, y=None):
    exp._on_key(SimpleNamespace(key=key, xdata=x, ydata=y))


def _explorer(s, tmp_path):
    return buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sc.json"),
        layer_stack=s.layers)


def test_edit_mode_builds_commits_and_pins(tmp_path):
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        w = s.bundles[0]
        n_before = len(w.input.candidates)

        _key(exp, 'E')                            # open empty session
        assert exp._edit_topo is not None

        _key(exp, 'Y', x=150, y=50)               # V trunk, snaps to x=140
        assert len(exp._edit_topo.segments) == 1
        seg = exp._edit_topo.segments[0]
        # Bundle-grid snap: x=140 is the KEEPOUT's edge (busterm blocks +
        # keepouts are the only snap targets now — c1's edges are not).
        assert seg.start.x == seg.end.x == 140
        # Full span: the Hanan y-extent (0..340 with block c1 present).
        assert (min(seg.start.y, seg.end.y),
                max(seg.start.y, seg.end.y)) == (0, 340)

        _key(exp, 'j')                            # select seg 0 (stub target)
        assert exp.sidx == 0
        _key(exp, 'S', x=50, y=50)                # stub from b1 (under cursor)
        _key(exp, 'S', x=250, y=50)               # stub from b2
        assert len(exp._edit_topo.segments) == 3
        assert "clean" in exp._edit_msg, exp._edit_msg

        # Navigation is parked while editing.
        _key(exp, ']')
        assert exp.bidx == 0 and "finish the session" in exp._edit_msg

        _key(exp, 'enter')                        # commit + pin
        assert exp._edit_topo is None
        assert len(w.input.candidates) == n_before + 1
        assert w.input.candidates[-1].type == "USER"
        assert w.input.topology_pinned
        assert w.plan.selected_topology_index == n_before
        assert exp._current_is_selected()         # explorer focused on it, pinned
        assert os.path.exists(str(tmp_path / "sc.json"))
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_mode_abort_discards(tmp_path):
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        w = s.bundles[0]
        base_uids = [buda.topo_uid(c) for c in w.input.candidates]

        _key(exp, 'e')                            # copy of the shown candidate
        assert exp._edit_topo is not None
        _key(exp, 'j')
        _key(exp, 'X')                            # remove a segment in the copy
        _key(exp, 'escape')                       # abort
        assert exp._edit_topo is None
        assert [buda.topo_uid(c) for c in w.input.candidates] == base_uids
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_mode_pair_connect(tmp_path):
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'E')
        _key(exp, 'T', x=150, y=200)              # H trunk at a Hanan row
        _key(exp, 'Y', x=150, y=50)               # V trunk at x=140
        topo = exp._edit_topo
        assert len(topo.segments) == 2
        # Two-step connect: mark seg 0, move to seg 1, press again.
        _key(exp, 'j')                            # sidx 0
        _key(exp, 'C')
        assert "marked" in exp._edit_msg
        _key(exp, 'j')                            # sidx 1
        _key(exp, 'C')
        assert topo.seg_conns, "connect left no junction record"
        _key(exp, 'escape')
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_mode_ui_banner_hanan_and_thin_segments(tmp_path):
    """The edit-session UI affordances:
    - the banner is a boxed chip INSIDE the axes (it used to sit just above
      them at y=1.01 and overlap the centered title);
    - the Hanan grid is forced visible while a session is open (T/Y place
      trunks on Hanan lines) without touching the shared ui_state, and hides
      again when the session closes;
    - segments thin out while editing (<= 4.5pt) so the slide bands and
      dotted nominals stay readable under a wide bus's line."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        assert not exp.ui_state.hanan_grid        # off by default

        def hanan_lines():
            return [a for a in exp.ax.lines
                    if a.get_linestyle() == '--' and a.get_alpha() == 0.6]

        def seg_lws():
            # Colored segment lines only (>= 3pt); the white halo under each
            # (lw + 4) is excluded — it scales with the same viz_lw anyway.
            return [a.get_linewidth() for a in exp.ax.lines
                    if a.get_linewidth() >= 3.0 and a.get_color() != 'white']

        exp._draw()
        assert hanan_lines() and not any(a.get_visible() for a in hanan_lines())
        lw_before = max(seg_lws())

        _key(exp, 'e')                            # open session (copy)
        assert any(a.get_visible() for a in hanan_lines()), \
            "hanan grid must show while editing"
        assert not exp.ui_state.hanan_grid        # shared state untouched
        # The grid shown is the BUNDLE-scoped one generation uses: edges of
        # the bundle's busterm blocks (b1/b2) AND of every keepout — NOT the
        # full-design grid: c1 (x=500/520) is no bundle busterm, so its
        # lines must be absent.
        xs = {a.get_xdata()[0] for a in hanan_lines()
              if a.get_xdata()[0] == a.get_xdata()[1]}
        assert {0, 100, 200, 300} <= xs, xs       # b1/b2 edges present
        assert {140, 160} <= xs, xs               # keepout edges present
        assert not ({500, 520} & xs), xs          # non-busterm c1 excluded
        banners = [t for t in exp.ax.texts
                   if t.get_text().startswith("EDIT") and t.get_bbox_patch()]
        assert banners, "edit banner chip missing"
        x, y = banners[0].get_position()
        assert y <= 1.0, "banner must sit inside the axes, not over the title"
        assert max(seg_lws()) <= 4.5 + 1e-6, "segments must thin while editing"

        _key(exp, 'escape')                       # close session
        exp._draw()
        assert not any(a.get_visible() for a in hanan_lines())
        assert max(seg_lws()) == lw_before        # width restored
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_mode_slide_window_refine(tmp_path):
    """'W' two-step slide-window refine: with a segment selected, the first W
    marks one perpendicular bound at the cursor, the second applies
    [min,max] ∩ the segment's structural slide range; 'w' clears.  Staged
    windows live-update the drawn slide band and land on plan.seg_slide_lo/hi
    at commit (the dogleg NUTS-override hatch)."""
    import math
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        w = s.bundles[0]
        _key(exp, 'E')
        _key(exp, 'Y', x=150, y=50)               # V trunk at x=140
        _key(exp, 'j')                            # select seg 0
        _key(exp, 'S', x=50, y=50)                # stub b1
        _key(exp, 'S', x=250, y=50)               # stub b2

        # A window DISJOINT from the structural slide range is rejected: the
        # stubbed trunk can only slide within the b1..b2 channel, nowhere
        # near x=1050 (a bare unstubbed trunk would legally accept anything —
        # its slide range is unconstrained).
        exp.sidx = 0
        _key(exp, 'W', x=1050, y=50)
        _key(exp, 'W', x=1090, y=50)
        assert "rejected" in exp._edit_msg and 0 not in exp._edit_slide

        # Refine the trunk's slide window: V segment → perpendicular is X.
        _key(exp, 'W', x=120, y=50)
        assert "press W at the other bound" in exp._edit_msg
        _key(exp, 'W', x=180, y=50)
        assert exp._edit_slide.get(0), exp._edit_msg
        lo, hi = exp._edit_slide[0]
        assert (lo, hi) == (120, 180) or (lo, hi) == (
            max(120, lo), min(180, hi)), (lo, hi)   # clamped into structure
        assert 120 <= lo < hi <= 180

        # The drawn slide band follows the staged window.
        cs = list(exp._build_conn_topo(exp._edit_topo).segs())[0]
        assert exp._seg_slide(cs, 0) == (lo, hi)

        # 'w' clears; re-stage for the commit check.
        _key(exp, 'w')
        assert 0 not in exp._edit_slide
        _key(exp, 'W', x=120, y=50); _key(exp, 'W', x=180, y=50)
        assert 0 in exp._edit_slide

        _key(exp, 'enter')                        # commit + pin + overrides
        # (_edit_msg is one-shot: the post-commit draw displays then clears
        # it, so assert on the landed plan overrides, not the banner text.)
        assert exp._edit_topo is None and not exp._edit_slide
        n = len(w.input.candidates[w.plan.selected_topology_index].segments)
        slo, shi = list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi)
        assert len(slo) == len(shi) == n
        assert (slo[0], shi[0]) == (lo, hi)
        assert all(math.isnan(v) for v in slo[1:])  # only seg 0 constrained
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
