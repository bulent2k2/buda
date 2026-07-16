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
while open: 'T'/'Y' arm an H/V trunk (two-step: press to arm + preview the
hovered cell, press again / enter / click to place at the busterm extent),
'S' stubs the block under the cursor to the selected segment, 'C'/'D'
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


def _trunk(exp, key, x, y):
    """Place a trunk via the two-step T/Y flow: arm, then place (same key)."""
    _key(exp, key, x, y)   # arm
    _key(exp, key, x, y)   # place


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

        _trunk(exp, 'Y', 150, 50)               # V trunk, snaps to x=140
        assert len(exp._edit_topo.segments) == 1
        seg = exp._edit_topo.segments[0]
        # Bundle-grid snap: x=140 is the KEEPOUT's edge (busterm blocks +
        # keepouts are the only snap targets now — c1's edges are not).
        assert seg.start.x == seg.end.x == 140
        # No overshoot: the trunk spans the BUSTERM extent along its axis, not
        # the whole-design Hanan extent. b1/b2 share a y-centre (50), so the
        # span falls back to their y-EXTENT (0..100) — NOT (0..340) past c1.
        assert (min(seg.start.y, seg.end.y),
                max(seg.start.y, seg.end.y)) == (0, 100)

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
        _trunk(exp, 'T', 150, 200)              # H trunk at a Hanan row
        _trunk(exp, 'Y', 150, 50)               # V trunk at x=140
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
        _trunk(exp, 'Y', 150, 50)               # V trunk at x=140
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


def test_edit_mode_slide_window_revalidated_at_commit(tmp_path):
    """A staged 'W' window is validated against the ConnTopology AT STAGING
    time; later geometry edits (stubs, connect/disconnect) can narrow the
    segment's structural slide range, and NUTS honors any non-NaN override
    verbatim.  Commit must therefore revalidate: a shrunken window clamps to
    the current range, a now-disjoint one is dropped (never written stale) —
    Codex #294."""
    import math
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        w = s.bundles[0]

        # Disjoint case: stage far away on a BARE trunk (unconstrained slide
        # range accepts anything), then stubs narrow the range to the b1..b2
        # channel — commit must drop the stale window, not write it.
        _key(exp, 'E')
        _trunk(exp, 'Y', 150, 50)               # bare V trunk at x=140
        exp.sidx = 0
        _key(exp, 'W', x=1050, y=50)
        _key(exp, 'W', x=1090, y=50)
        assert exp._edit_slide.get(0) == (1050, 1090)   # accepted: bare trunk
        _key(exp, 'j')                            # select seg 0 for stubbing
        _key(exp, 'S', x=50, y=50)                # stub b1
        _key(exp, 'S', x=250, y=50)               # stub b2 → range ~[100,200]
        # Plant matching-length overrides from "an earlier commit / dogleg":
        # the dropped-only commit must CLEAR them, not leave them to leak
        # onto the newly pinned topology (Codex #295).
        w.plan.seg_slide_lo = [50.0, 50.0, 50.0]
        w.plan.seg_slide_hi = [60.0, 60.0, 60.0]
        _key(exp, 'enter')
        slo = list(w.plan.seg_slide_lo)
        assert slo and all(math.isnan(v) for v in slo), \
            f"stale overrides must be cleared by the dropped-only commit: {slo}"

        # Clamp case: stage [120, 1090] on the bare trunk, then stub — commit
        # clamps the surviving overlap into the current structural range.
        _key(exp, 'E')
        _trunk(exp, 'Y', 150, 50)
        exp.sidx = 0
        _key(exp, 'W', x=120, y=50)
        _key(exp, 'W', x=1090, y=50)
        assert exp._edit_slide.get(0) == (120, 1090)
        _key(exp, 'j')
        _key(exp, 'S', x=50, y=50)
        _key(exp, 'S', x=250, y=50)
        cs = list(exp._build_conn_topo(exp._edit_topo).segs())[0]
        s_lo, s_hi = float(cs.perp_lo), float(cs.perp_hi)
        _key(exp, 'enter')
        slo, shi = list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi)
        assert not math.isnan(slo[0]), "clamped window should survive commit"
        assert slo[0] == max(120.0, s_lo) and shi[0] == min(1090.0, s_hi)
        assert s_lo <= slo[0] < shi[0] <= s_hi   # inside the CURRENT range
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_trunk_spans_busterm_extent_and_stub_auto_selects(tmp_path):
    """#8: a trunk spans the busterm extent (endpoints at the extreme busterm
    centres), not the whole-design Hanan grid — no overshoot. #7: with only the
    trunk present, 'S' auto-selects it as the stub target."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'E')
        _trunk(exp, 'T', 150, 190)         # H trunk, snaps to y=200 (clear of faces)
        seg = exp._edit_topo.segments[0]
        # b1 x-centre 50, b2 x-centre 250 differ -> span = [50, 250], bounded.
        assert (min(seg.start.x, seg.end.x),
                max(seg.start.x, seg.end.x)) == (50, 250)

        exp.sidx = -1                        # nothing selected...
        _key(exp, 'S', x=50, y=50)           # ...'S' auto-selects the lone trunk
        assert exp.sidx == 0                 # (#7) auto-selected the trunk
        assert len(exp._edit_topo.segments) == 2   # stub landed on it
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_bundle_grid_has_oob_detour_lines(tmp_path):
    """#6: the bundle grid (the T/Y snap targets) includes an OOB detour line a
    margin beyond each extreme, so an out-of-bounds detour trunk is placeable —
    a cursor in the margin snaps to the OOB line, not back to a busterm edge."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        xs, _ = exp._bundle_hanan_grid()
        assert min(xs) < 0 and max(xs) > 300      # beyond the [0,300] busterm span
        far = max(xs)
        assert exp._snap(far + 5, xs) == far      # margin cursor snaps OOB
        assert far not in (0, 100, 140, 160, 200, 300)   # a genuine detour target
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_trunk_two_step_arm_preview_place(tmp_path):
    """#5: T/Y ARM 'add trunk' mode (no immediate placement); a motion event
    previews the snapped target line; a second T/Y (or enter/click) places it;
    esc cancels."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'E')
        # First 'T' ARMS — it must NOT place a segment yet.
        _key(exp, 'T', x=150, y=190)
        assert exp._trunk_mode is True
        assert len(exp._edit_topo.segments) == 0
        assert "ADD H TRUNK" in exp._edit_msg

        # A motion event previews the snapped row (y=190 -> 200) without placing.
        exp._on_trunk_motion(SimpleNamespace(inaxes=exp.ax, xdata=150, ydata=190))
        assert exp._trunk_hover == 200
        assert len(exp._edit_topo.segments) == 0

        # Second 'T' PLACES at the cursor and leaves trunk mode.
        _key(exp, 'T', x=150, y=190)
        assert exp._trunk_mode is None
        assert len(exp._edit_topo.segments) == 1
        seg = exp._edit_topo.segments[0]
        assert min(seg.start.y, seg.end.y) == max(seg.start.y, seg.end.y) == 200

        # esc cancels an armed trunk without touching the session or topology.
        _key(exp, 'Y', x=150, y=50)
        assert exp._trunk_mode is False
        _key(exp, 'escape')
        assert exp._trunk_mode is None
        assert exp._edit_topo is not None            # session still open
        assert len(exp._edit_topo.segments) == 1     # no V trunk added

        # A left-click also places (switch back to a fresh arm first).
        _key(exp, 'Y', x=150, y=150)                 # arm V (snaps x -> 140)
        exp._on_trunk_click(SimpleNamespace(button=1, inaxes=exp.ax,
                                            xdata=150, ydata=150))
        assert exp._trunk_mode is None
        assert len(exp._edit_topo.segments) == 2
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def _session_row():
    """Two pairs of horizontally-aligned blocks with a big gap between them —
    the scenario for two H trunks on one row, each spanning only its pair."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "add_block a1 0 0 100 100",
        "add_block a2 300 0 400 100",       # pair A (x-centres 50, 350)
        "add_block b3 2000 0 2100 100",
        "add_block b4 2300 0 2400 100",     # pair B (x-centres 2050, 2350)
        "add_bus w[4] a1.o a2.i,b3.i,b4.i",
        "run_bundler",
        "generate_topologies",
    ):
        s.do_command(cmd)
    return s


def test_edit_trunk_pin_span_to_busterm_subset(tmp_path):
    """Follow-up to #8: 'P' refines a trunk's span to a SUBSET of busterms by
    clicking them, so two H trunks can share one Hanan row while each covers
    only its pair (no reach across the big gap)."""
    s = _session_row()
    exp = _explorer(s, tmp_path)

    def click(x, y):
        exp._on_trunk_click(SimpleNamespace(button=1, inaxes=exp.ax,
                                            xdata=x, ydata=y))
    def xspan(i):
        seg = exp._edit_topo.segments[i]
        return (min(seg.start.x, seg.end.x), max(seg.start.x, seg.end.x))
    try:
        _key(exp, 'E')
        # Trunk 0: default spans ALL busterms (a1 centre 50 .. b4 centre 2350).
        _trunk(exp, 'T', 200, 190)
        assert xspan(0) == (50, 2350)

        # Pin trunk 0 to pair A (click a1, a2) -> span shrinks to [50, 350].
        exp.sidx = 0
        _key(exp, 'P')
        assert exp._trunk_pin_set == set()
        click(50, 50); click(350, 50)                 # a1, a2
        assert exp._trunk_pin_set == {'a1', 'a2'}
        _key(exp, 'enter')
        assert exp._trunk_pin_set is None
        assert xspan(0) == (50, 350)                  # limited to pair A

        # Trunk 1 on the SAME row, pinned to pair B -> [2050, 2350].
        _trunk(exp, 'T', 200, 190)
        exp.sidx = 1
        _key(exp, 'P')
        click(2050, 50); click(2350, 50)              # b3, b4
        assert exp._trunk_pin_set == {'b3', 'b4'}
        _key(exp, 'enter')
        assert xspan(1) == (2050, 2350)               # limited to pair B
        assert xspan(0) == (50, 350)                  # trunk 0 unchanged

        # esc cancels pin mode without changing the span; a click toggles off.
        exp.sidx = 0
        _key(exp, 'P')
        click(50, 50); click(50, 50)                  # toggle a1 on then off
        assert exp._trunk_pin_set == set()
        click(50, 50)
        _key(exp, 'escape')
        assert exp._trunk_pin_set is None
        assert xspan(0) == (50, 350)                  # unchanged by the cancel
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def _session_col():
    """Two blocks aligned VERTICALLY — the C-detour scenario: a V spine runs
    beside them and its span must reach a Hanan line below the lower block and
    above the upper block (both BEYOND the busterms)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "add_block up 0 2000 100 2100",     # upper block (y 2000..2100)
        "add_block lo 0 0 100 100",         # lower block (y 0..100)
        "add_bus w[4] lo.o up.i",
        "run_bundler",
        "generate_topologies",
    ):
        s.do_command(cmd)
    return s


def test_edit_trunk_pin_span_to_grid_lines_beyond_busterms(tmp_path):
    """The designer's C-detour: 'P' pins a V trunk's endpoints to Hanan grid
    lines (incl. the OOB detour lines) BELOW the lower and ABOVE the upper
    block, so the span reaches beyond both busterms — hovering a block pins the
    block, hovering elsewhere pins the nearest along-axis grid line."""
    s = _session_col()
    exp = _explorer(s, tmp_path)

    def click(x, y):
        exp._on_trunk_click(SimpleNamespace(button=1, inaxes=exp.ax,
                                            xdata=x, ydata=y))
    def yspan(i):
        seg = exp._edit_topo.segments[i]
        return (min(seg.start.y, seg.end.y), max(seg.start.y, seg.end.y))
    try:
        _key(exp, 'E')
        xs, ys = exp._bundle_hanan_grid()
        below, above = min(ys), max(ys)               # OOB lines beyond blocks
        assert below < 0 and above > 2100
        col = min(xs)                                  # V spine sits OOB, beside

        # V trunk (the C spine); default span is the busterm extent (50..2050).
        _trunk(exp, 'Y', col, 1000)
        assert yspan(0) == (50, 2050)

        # Pin the two endpoints to grid lines beyond both blocks.
        exp.sidx = 0
        _key(exp, 'P')
        click(col, below)                             # empty space -> low grid
        click(col, above)                             # ... -> high grid
        assert exp._trunk_pin_set == set()            # no busterm blocks picked
        assert exp._trunk_pin_grid == {below, above}
        _key(exp, 'enter')
        assert yspan(0) == (below, above)             # reaches beyond both
        assert exp._edit_topo.segments[0].start.x == col   # perp preserved

        # A grid pick toggles off; a mixed block+grid anchor also works.
        _key(exp, 'P')
        click(col, above); click(col, above)          # toggle high line on/off
        assert exp._trunk_pin_grid == set()
        click(50, 50)                                 # lower block centre (y=50)
        click(col, above)                             # up to the high grid line
        assert exp._trunk_pin_set == {'lo'}
        assert exp._trunk_pin_grid == {above}
        _key(exp, 'enter')
        assert yspan(0) == (50, above)                # lower centre .. high line
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def _session_multilayer():
    """Two layers per direction so +/- actually cycles a segment's layer."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 10",
        "def_layer 6 M6 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "def_layer 7 M7 V TOP 10",
        "add_block b1 0 0 100 100",
        "add_block b2 200 0 300 100",
        "add_bus v[4] b1.o b2.i",
        "run_bundler",
        "generate_topologies",
    ):
        s.do_command(cmd)
    return s


def test_edit_layer_change_survives_commit(tmp_path):
    """Codex #302: a +/- layer edit in a TopoEdit session must survive commit.
    A stale wrapper.input.pinned_seg_layers carried over from the source
    candidate would, if its length matched, be re-pinned as seg_layers by
    _select_current and override the edit — the committed USER candidate would
    route on the OLD layer.  Commit must rebuild the pins from the working copy."""
    s = _session_multilayer()
    exp = _explorer(s, tmp_path)
    w = s.bundles[0]
    try:
        topo0 = w.input.candidates[exp.idx]
        # Simulate a prior non-edit layer pin: the source candidate pinned to its
        # own current layers (a full snapshot, as _cycle_layer would leave it).
        stale = [sg.layer_hint for sg in topo0.segments]
        w.input.pinned_seg_layers = list(stale)

        _key(exp, 'e')                                # copy of the shown candidate
        exp.sidx = 0
        _key(exp, '+')                                # cycle segment 0's layer
        after = exp._edit_topo.segments[0].layer_hint
        assert after != stale[0]                       # the edit moved the layer
        assert exp._edit_layers_changed
        _key(exp, 'enter')                            # commit

        committed = w.input.candidates[w.plan.selected_topology_index]
        pins = list(w.input.pinned_seg_layers)
        assert len(pins) == len(committed.segments)
        assert pins[0] == after                        # the EDIT, not the stale layer
        # The sidecar selection carries the rebuilt pins, so a re-plan honors them.
        sel = exp._find_selection()
        assert sel and sel.get('seg_layers', [])[0] == after
        assert not exp._edit_layers_changed            # reset after commit

    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_commit_without_layer_change_drops_stale_pins(tmp_path):
    """The complement: a session that makes NO layer decision must not inherit
    the source candidate's stale per-segment pins (they are indexed to the old
    topology).  Commit drops them so the planner assigns layers freely."""
    s = _session_multilayer()
    exp = _explorer(s, tmp_path)
    w = s.bundles[0]
    try:
        topo0 = w.input.candidates[exp.idx]
        w.input.pinned_seg_layers = [
            5 if sg.start.x == sg.end.x else 4 for sg in topo0.segments]

        _key(exp, 'e')                                # copy; no layer edits
        _key(exp, 'j')
        _key(exp, 'enter')                            # commit as-is

        assert list(w.input.pinned_seg_layers) == []   # stale pins dropped
        sel = exp._find_selection()
        assert sel is not None and 'seg_layers' not in sel
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
