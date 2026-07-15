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
while open: 'T'/'Y' add an H/V trunk at the cursor's Hanan line (full span),
'S' stubs the block under the cursor to the selected segment, 'C'/'D'
pair-connect/-disconnect, 'X' removes the selected segment, enter commits the
result as a uid-deduped USER candidate (pinned + sidecar'd), escape aborts.
Navigation is parked while a session is open.  Driven headlessly through
_on_key with synthesized events.
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
        # A third block whose edges give the Hanan grid a mid-channel column
        # (x=140/160) for the trunk to snap to.
        "add_block c1 140 300 160 340",
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
        assert seg.start.x == seg.end.x == 140    # Hanan-snapped column
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
