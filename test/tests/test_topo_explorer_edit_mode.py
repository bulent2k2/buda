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

        _trunk(exp, 'Y', 150, 50)               # V trunk, snaps to x=150
        assert len(exp._edit_topo.segments) == 1
        seg = exp._edit_topo.segments[0]
        # CENTERLINE snap: x=150 is the middle of the bundle-grid cell between
        # the keepout's edges (140..160) — trunks land mid-channel, and the
        # cell's bounding lines seed the initial slide window.
        assert seg.start.x == seg.end.x == 150
        assert exp._edit_slide[0] == (140.0, 160.0)
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
        _trunk(exp, 'Y', 150, 50)               # V trunk at x=150 (centerline)
        _key(exp, 'j')                            # select seg 0
        _key(exp, 'S', x=50, y=50)                # stub b1
        _key(exp, 'S', x=250, y=50)               # stub b2

        # A window DISJOINT from the structural slide range is rejected: the
        # stubbed trunk can only slide within the b1..b2 channel, nowhere
        # near x=1050 (a bare unstubbed trunk would legally accept anything —
        # its slide range is unconstrained).  The rejection leaves the
        # placement-seeded window (the Hanan cell 140..160) untouched.
        exp.sidx = 0
        _key(exp, 'W', x=1050, y=50)
        _key(exp, 'W', x=1090, y=50)
        assert "rejected" in exp._edit_msg
        assert exp._edit_slide.get(0) == (140.0, 160.0)

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

        # A motion event previews the snapped CELL CENTERLINE (y=190 lies in
        # the 100..200 cell -> centerline 150) without placing.
        exp._on_trunk_motion(SimpleNamespace(inaxes=exp.ax, xdata=150, ydata=190))
        assert exp._trunk_hover == 150
        assert len(exp._edit_topo.segments) == 0

        # Second 'T' PLACES at the cursor and leaves trunk mode.
        _key(exp, 'T', x=150, y=190)
        assert exp._trunk_mode is None
        assert len(exp._edit_topo.segments) == 1
        seg = exp._edit_topo.segments[0]
        assert min(seg.start.y, seg.end.y) == max(seg.start.y, seg.end.y) == 150
        # The cell's bounding lines seed the trunk's slide window, and the new
        # segment is auto-selected (live info banner + direct S target).
        assert exp._edit_slide[0] == (100.0, 200.0)
        assert exp.sidx == 0

        # esc cancels an armed trunk without touching the session or topology.
        _key(exp, 'Y', x=150, y=50)
        assert exp._trunk_mode is False
        _key(exp, 'escape')
        assert exp._trunk_mode is None
        assert exp._edit_topo is not None            # session still open
        assert len(exp._edit_topo.segments) == 1     # no V trunk added

        # A left-click also places (switch back to a fresh arm first).
        _key(exp, 'Y', x=150, y=150)                 # arm V (snaps x -> 150)
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
        cx = exp._snap_cell_center(col, xs)            # OOB band's centerline

        # V trunk (the C spine) lands mid-detour-band; default span is the
        # busterm extent (50..2050) — both blocks touch the band's inner line.
        _trunk(exp, 'Y', col, 1000)
        assert exp._edit_topo.segments[0].start.x == cx
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
        assert exp._edit_topo.segments[0].start.x == cx    # perp preserved

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


# ── dd-detour gestures: segment anchors, single-anchor re-span, dup trunks ────

def _dd_base_session():
    """The c_double_detour geometry with its committed C topo — the base the
    dd-detour session edits (clone, add right V trunks, re-span)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 44.44",
        "def_layer 5 M5 V TOP 50.00",
        "add_block lo 0    0 400  400",
        "add_block up 0 1000 400 1400",
        "add_bus w[4] lo.o up.i",
        "run_bundler",
        "generate_topologies",
        "edit_topology 1 new",
        "edit_add_trunk H  1700 -700 400",
        "edit_add_trunk V  -700 -300 1700",
        "edit_add_trunk H  -300 -700 400",
        "edit_add_stub up 0",
        "edit_add_stub lo 2",
        "edit_set_span 0 -700 200",
        "edit_set_span 2 -700 200",
        "edit_commit pin",
    ):
        s.do_command(cmd)
    return s


def _click(exp, x, y):
    exp._on_trunk_click(SimpleNamespace(button=1, inaxes=exp.ax,
                                        xdata=x, ydata=y))


def _span(exp, i, horiz):
    sg = exp._edit_topo.segments[i]
    return ((min(sg.start.x, sg.end.x), max(sg.start.x, sg.end.x)) if horiz
            else (min(sg.start.y, sg.end.y), max(sg.start.y, sg.end.y)))


def test_edit_pin_segment_anchor_and_single_anchor_respan(tmp_path):
    """The dd-detour gestures: (a) a click near a PERPENDICULAR segment anchors
    the span at ITS perp coordinate (even off-grid), so 'up block + the top H
    trunk' spans (1200, 1700) and the junction lands exactly; (b) a SINGLE
    anchor moves only the NEAREST endpoint — re-spanning the H trunk's right
    end onto the new V trunk while its left end stays on the spine.  This is
    the workflow whose click-beside-the-block miss used to be rejected as a
    'degenerate span' (a lone grid anchor)."""
    s = _dd_base_session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'e')                        # clone the C (5 segments)
        _trunk(exp, 'Y', 500, 700)            # right V trunk (idx 5), OOB x=500
        exp.sidx = 5
        _key(exp, 'P')
        _click(exp, 200, 1200)                # inside `up` → block anchor (1200)
        _click(exp, 0, 1700)                  # ON the top H trunk → segment
        assert exp._trunk_pin_set == {'up'}   #   anchor at ITS y=1700 (off-grid)
        assert exp._trunk_pin_grid == {1700}
        _key(exp, 'enter')
        assert _span(exp, 5, horiz=False) == (1200, 1700)

        # Single segment-anchor: re-span the top H trunk's right end onto the
        # new V trunk (at the detour band's centerline x=450); the left end
        # (on the spine at -700) must not move.
        exp.sidx = 0
        _key(exp, 'P')
        _click(exp, 500, 1600)                # near the V trunk line (x=450)
        assert exp._trunk_pin_grid == {450}
        _key(exp, 'enter')
        assert _span(exp, 0, horiz=True) == (-700, 450)
        assert 'rejected' not in exp._edit_msg
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_pin_to_trunk_stretches_and_connects(tmp_path):
    """P with a segment anchor doesn't just land the pinned trunk's end ON
    the partner's line — it CONNECTS the pair (edit_connect), stretching the
    PARTNER to the crossing when its span falls short, so the two trunks end
    junctioned instead of merely sharing a coordinate."""
    s = _dd_base_session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'e')                        # clone the C (5 segments)
        _trunk(exp, 'Y', 500, 700)            # right V trunk (idx 5) at x=450
        exp.sidx = 5
        _key(exp, 'P')
        _click(exp, 200, 1200)                # block anchor: up (centre 1200)
        _click(exp, 0, 1700)                  # segment anchor: top H trunk (0)
        _key(exp, 'enter')
        assert _span(exp, 5, horiz=False) == (1200, 1700)
        # The PARTNER stretched to the crossing: the top H trunk's right end
        # reaches the V trunk's line (was -700..200, V trunk at x=450).
        assert _span(exp, 0, horiz=True) == (-700, 450)
        # And the pair is junctioned — ConnTopology sees the SEG conn.
        ct = exp._build_conn_topo(exp._edit_topo)
        conns5 = [c.seg_idx for c in list(ct.segs())[5].conns
                  if c.kind == buda.SegConnKind.SEG]
        assert 0 in conns5
        assert "connected seg 0" in exp._edit_msg
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_pin_interior_trunk_anchor_preserves_span(tmp_path):
    """Codex #322 P2: a segment anchor BETWEEN outer anchors must not shrink
    the just-applied span — the connect reverses (the PARTNER's endpoint
    lands on the pinned trunk, extended to the crossing), instead of moving
    the pinned trunk's nearest end inward to the anchor."""
    s = _dd_base_session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'e')                        # clone the C (5 segments)
        _trunk(exp, 'Y', 500, 700)            # V trunk (idx 5) at x=450,
        exp.sidx = 0                          #   spanning y 200..1200
        _key(exp, 'P')                        # pin the TOP H trunk (y=1700)
        _click(exp, 200, 1200)                # block anchor: up (centre 200)
        _click(exp, 500, 1650)                # grid anchor: OOB line x=500
                                              #   (above the V trunk's span)
        _click(exp, 450, 700)                 # trunk anchor x=450 — INTERIOR
        assert exp._trunk_pin_grid == {500, 450}
        _key(exp, 'enter')
        # Span = [min,max] over anchors {200, 500, 450} — the interior 450
        # did NOT pull the right end in (the old forward connect gave 450).
        assert _span(exp, 0, horiz=True) == (200, 500)
        # The PARTNER junctioned onto the pinned trunk instead: extended from
        # y 1200 up to the crossing at 1700.
        assert _span(exp, 5, horiz=False) == (200, 1700)
        ct = exp._build_conn_topo(exp._edit_topo)
        conns0 = [c.seg_idx for c in list(ct.segs())[0].conns
                  if c.kind == buda.SegConnKind.SEG]
        assert 5 in conns0
        assert "connected seg 5" in exp._edit_msg
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_duplicate_trunk_rejected(tmp_path):
    """Repeated Y at the same spot must NOT stack identical trunks: the second,
    geometrically identical placement is rejected loud (same line + span);
    disjoint spans on one line remain allowed (the dd-detour pattern)."""
    s = _dd_base_session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'e')
        _trunk(exp, 'Y', 500, 700)
        n = len(exp._edit_topo.segments)
        _trunk(exp, 'Y', 500, 700)            # identical → rejected
        assert len(exp._edit_topo.segments) == n
        assert 'identical trunk' in exp._edit_msg
        # Disjoint spans on the same line are the legitimate pattern.
        exp.sidx = n - 1
        _key(exp, 'P'); _click(exp, 200, 1200); _click(exp, 0, 1700)
        _key(exp, 'enter')                    # re-span to (1200, 1700)
        _trunk(exp, 'Y', 500, 700)            # default span (200,1200) ≠ dup
        assert len(exp._edit_topo.segments) == n + 1
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_disconnected_commit_flagged(tmp_path):
    """The user's escape recipe — stub `lo` onto a floating right trunk, then
    remove lo's original V stub — must be flagged: the live banner shows
    DISCONNECTED, and check_topo on the committed candidate reports it (the
    audit that used to pass a two-island topology silently)."""
    s = _dd_base_session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'e')
        _trunk(exp, 'Y', 500, 700)            # floating right trunk (idx 5)
        exp.sidx = 5
        _key(exp, 'S', x=200, y=200)          # H stub from lo onto it
        exp.sidx = 4                          # lo's original V stub
        _key(exp, 'X')                        # → two islands
        assert 'DISCONNECTED' in exp._edit_msg
        _key(exp, 'enter')                    # commit (never strands — allowed)
        w = s.bundles[0]
        topo = w.input.candidates[w.plan.selected_topology_index]
        ct = buda.ConnTopology(); ct.build(topo, s.fp)
        kinds = {str(v.kind).split('.')[-1]
                 for v in buda.check_topo(ct, topo, s.fp, 1).violations}
        assert 'DISCONNECTED' in kinds
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_seg_info_lines_on_unpinned_topo(tmp_path):
    """j/k works on an UN-pinned candidate too, and the info box carries the
    second line: perp position + slide range on the perpendicular axis."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'j')                        # no pin, no edit session
        assert exp.sidx == 0
        infos = [t.get_text() for t in exp.ax.texts
                 if 'segment 0' in t.get_text()]
        assert infos, "info box missing on un-pinned topo"
        lines = infos[0].splitlines()
        assert lines[0].startswith('Selected')
        assert '-slide=[' in lines[1]         # "y=.. V-slide=[..]" / "x=.. H-.."
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


@pytest.mark.parametrize("pinned", (False, True))
@pytest.mark.parametrize("edit", (False, True))
def test_seg_selection_emphasis_consistent(tmp_path, pinned, edit):
    """Stepping with j/k highlights the segment AND its slide band in every
    state of the 2x2 matrix (pinned x edit-mode).  The band gate used to
    require edit-or-pinned while the segment halo worked on any candidate,
    so an un-pinned j left the halo without the band emphasis."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        exp._step_topo(+1)              # a multi-segment candidate
        if pinned:
            _key(exp, 's')
        if edit:
            _key(exp, 'e')              # session on the current candidate
        _key(exp, 'j')
        assert exp.sidx != -1
        halo = any(ln.get_zorder() == 9 and ln.get_color() == 'white'
                   for ln in exp.ax.lines)
        bands = sorted({round(p.get_alpha(), 2) for p in exp.ax.patches
                        if p.get_zorder() == 3})
        assert halo, "selected-segment halo missing"
        assert bands == [0.04, 0.3], (
            f"slide bands not emphasized/dimmed: {bands}")
        # No selection: every band back at the uniform resting alpha.
        exp.sidx = -1
        exp._draw()
        bands = {round(p.get_alpha(), 2) for p in exp.ax.patches
                 if p.get_zorder() == 3}
        assert bands == {0.10}
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_abort_drops_stale_segment_selection(tmp_path):
    """Codex #314 P2: select a segment ADDED during an edit session, then
    abort — the restored candidate has fewer segments, so the stale sidx
    must clear (else every band draws dimmed with nothing selected, and
    +/- would index past the restored topology's segments)."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        n0 = len(exp._shown_topo().segments)
        _key(exp, 'e')                      # session: copy of the candidate
        _trunk(exp, 'T', 150, 150)          # new segment beyond the copy
        assert len(exp._edit_topo.segments) == n0 + 1
        while exp.sidx != n0:               # select the ADDED segment
            _key(exp, 'j')
        _key(exp, 'escape')                 # abort: restore the original
        assert exp.sidx == -1               # stale index dropped
        bands = {round(p.get_alpha(), 2) for p in exp.ax.patches
                 if p.get_zorder() == 3}
        assert bands == {0.10}              # uniform resting alpha
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_zoom_toggle_bundle_vs_segment(tmp_path):
    """cmd/ctrl-z zooms to the bundle bbox; with a segment selected, repeated
    presses TOGGLE bundle <-> segment.  The segment view frames the SPAN +
    SLIDE box — centered, covering at most 1/9 of the canvas (1/3 per
    dimension) — so a slide-displaced drawn wire is never shifted out of
    frame (the nominal-endpoint zoom used to drift off the band)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
                # Nearly-aligned tall pair: the L candidates carry a ~10-unit
                # H jog (tiny vs the ~2100-tall bundle bbox) and a ~2000 V run.
                "add_block b1 0 0 100 100", "add_block b2 10 2000 110 2100",
                "add_bus v[4] b1.o b2.i", "run_bundler",
                "generate_topologies"):
        s.do_command(cmd)
    exp = _explorer(s, tmp_path)
    try:
        # A multi-segment candidate (candidate 0 can be a straight 1-seg shot
        # whose bbox IS the bundle bbox — no jog to zoom to).
        while len(exp._shown_topo().segments) < 2:
            exp._step_topo(+1)
        exp._zoom_to_bundle()
        assert exp._zoom_sel_mode == 'bundle'
        bx = exp.ax.get_xlim(); by = exp.ax.get_ylim()
        bw, bh = bx[1] - bx[0], by[1] - by[0]

        for si in range(len(exp._shown_topo().segments)):
            exp.sidx = si
            exp._zoom_sel_mode = 'bundle'
            exp._zoom_to_bundle()
            assert exp._zoom_sel_mode == 'seg'
            x0b, x1b, y0b, y1b = exp._seg_zoom_box()
            tx = exp.ax.get_xlim(); ty = exp.ax.get_ylim()
            # Centered on the span+slide box...
            assert abs((tx[0] + tx[1]) / 2 - (x0b + x1b) / 2) < 1
            assert abs((ty[0] + ty[1]) / 2 - (y0b + y1b) / 2) < 1
            # ...and the box covers at most 1/3 per dimension (1/9 of canvas);
            # the window filler only ever EXPANDS an axis, never shrinks it.
            assert (x1b - x0b) <= (tx[1] - tx[0]) / 3 + 1e-6
            assert (y1b - y0b) <= (ty[1] - ty[0]) / 3 + 1e-6
            # The whole box (wire + slide band) is inside the view.
            assert tx[0] <= x0b and x1b <= tx[1]
            assert ty[0] <= y0b and y1b <= ty[1]

        exp._zoom_to_bundle()                                 # toggle back out
        assert exp._zoom_sel_mode == 'bundle'
        bx2 = exp.ax.get_xlim(); by2 = exp.ax.get_ylim()
        assert abs((bx2[1] - bx2[0]) - bw) < 1
        assert abs((by2[1] - by2[0]) - bh) < 1
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_seg_info_banner_conn_line(tmp_path):
    """Line 3 of the segment info banner: net-pull always, plus busterm taps /
    pass-through blocks / connected segs when present."""
    s = _session()
    exp = _explorer(s, tmp_path)
    try:
        # A multi-segment candidate: seg 0 taps its block and joins another.
        while len(exp._shown_topo().segments) < 2:
            exp._step_topo(+1)
        _key(exp, 'j')
        infos = [t.get_text() for t in exp.ax.texts
                 if 'segment 0' in t.get_text()]
        assert infos
        lines = infos[0].splitlines()
        assert len(lines) >= 3 and lines[2].startswith('pull=')
        assert 'busterms:' in lines[2]
        assert 'segs:' in lines[2]

        # A hand-placed trunk CROSSING blocks reports them as passthru: T at
        # y=50 (centerline of the 0..100 cell) spans b1..b2's centres and
        # crosses both footprints without tapping.
        _key(exp, 'E')
        _trunk(exp, 'T', 150, 60)
        assert exp.sidx == 0                  # auto-selected on placement
        infos = [t.get_text() for t in exp.ax.texts
                 if 'segment 0' in t.get_text()]
        assert infos
        line3 = infos[0].splitlines()[2]
        assert 'passthru: b1,b2' in line3
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_trunk_centerline_slice_busterms_and_live_info(tmp_path):
    """T/Y hover snaps to the Hanan-cell CENTERLINE, the armed banner
    live-reports the prospective trunk (coordinate, slide, touching
    busterms), and placement seeds the slide window from the cell's bounding
    lines with the along span from the busterms touching the slice."""
    s = _session_col()          # lo (0..100 y), up (2000..2100 y), aligned
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'E')
        _key(exp, 'T', 50, 1080)              # arm over the lo..up channel
        assert exp._trunk_hover == 1050       # centerline of the 100..2000 cell
        assert 'y=1050' in exp._edit_msg
        assert 'slide=[100,2000]' in exp._edit_msg
        assert 'busterms: lo,up' in exp._edit_msg   # both touch the slice
        _key(exp, 'T', 50, 1080)              # place
        sg = exp._edit_topo.segments[0]
        assert sg.start.y == sg.end.y == 1050
        # Along span: lo/up share x-centre 50, so the touching-busterm span
        # falls back to their x-EXTENT (0..100).
        assert (min(sg.start.x, sg.end.x), max(sg.start.x, sg.end.x)) == (0, 100)
        assert exp._edit_slide[0] == (100.0, 2000.0)
        assert exp.sidx == 0                  # auto-selected
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_ops_logged_and_user_topo_survives_rerun(tmp_path, capsys):
    """Items 5+8: every applied GUI op prints its `.buda` equivalent
    ([edit-cmd] …), the commit stores the op-log in the sidecar, and a FRESH
    session re-running the same flow rebuilds the USER candidate (same uid)
    and resolves the pin — no 'could not be resolved' warning."""
    flow = tmp_path / "f.buda"
    flow.write_text("\n".join((
        "def_layer 4 M4 H TOP 10",
        "def_layer 5 M5 V TOP 10",
        "add_block lo 0    0 400  400",
        "add_block up 0 1000 400 1400",
        "add_bus w[4] lo.o up.i",
        "run_bundler",
        "generate_topologies",
    )) + "\n")

    def run_flow(sess):
        for line in flow.read_text().splitlines():
            if line.strip():
                sess.do_command(line.strip())

    # Session 1: run the flow, edit in the GUI, commit.
    s1 = buda_cli.BudaSession(); s1.no_viz = True
    s1.script_path = str(flow)
    run_flow(s1)
    exp = buda_viz.TopologyExplorer(
        s1.fp, s1.bundles, sidecar_path=str(tmp_path / "f.json"),
        layer_stack=s1.layers)                     # the session's sidecar
    try:
        _key(exp, 'E')
        _trunk(exp, 'Y', 500, 700)
        exp.sidx = 0
        _key(exp, 'S', x=200, y=1200)
        _key(exp, 'S', x=200, y=200)
        exp.sidx = 0
        _key(exp, 'W', x=420, y=700)          # slide window bound 1
        _key(exp, 'W', x=460, y=700)          # bound 2 → staged [420,460]
        _key(exp, 'enter')
        out = capsys.readouterr().out
        # Y at x=500 (the OOB line) snaps to the detour band's CENTERLINE
        # (400..500 → 450), which also seeds the initial slide window; the W
        # refinement then replaces it.
        assert '[edit-cmd] edit_add_trunk V 450' in out
        assert '[edit-cmd] edit_set_slide 0 400 500' in out   # placement seed
        assert '[edit-cmd] edit_add_stub up 0 layer 4' in out
        assert '[edit-cmd] edit_set_slide 0 420 460' in out
        uid1 = None
        sel = exp._find_selection()
        assert sel and sel.get('user_topo', {}).get('ops'), \
            "sidecar entry must carry the op-log"
        assert 'edit_set_slide 0 420 460' in sel['user_topo']['ops']
        uid1 = sel['topo_uid']
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')

    # Session 2: fresh run of the SAME flow — the op-log replays and the pin
    # resolves to the rebuilt USER candidate, W window included.
    import math
    s2 = buda_cli.BudaSession(); s2.no_viz = True
    s2.script_path = str(flow)
    run_flow(s2)
    out2 = capsys.readouterr().out
    assert 'rebuilding USER candidate' in out2
    assert 'could not be resolved' not in out2
    w2 = s2.bundles[0]
    idx = w2.plan.selected_topology_index
    assert w2.input.topology_pinned
    assert w2.input.candidates[idx].type == 'USER'
    assert buda.topo_uid(w2.input.candidates[idx]) == uid1
    # The staged W window survived the round trip onto the plan.
    assert (w2.plan.seg_slide_lo[0], w2.plan.seg_slide_hi[0]) == (420.0, 460.0)
    assert all(math.isnan(v) for v in list(w2.plan.seg_slide_lo)[1:])


def test_cli_edit_set_slide_stages_clamps_and_rekeys(tmp_path):
    """The scriptable W: edit_set_slide stages a window (clamped to the
    structural slide range), 'clear' unstages, edit_remove_segment re-keys,
    and edit_commit lands the survivors on plan.seg_slide_lo/hi."""
    import math
    s = _dd_base_session()                    # C committed+pinned (5 segments)
    for cmd in (
        "edit_topology 1 new",
        "edit_add_trunk V 500 200 1200",      # seg 0: OOB → range [400, inf)
        "edit_add_stub up 0",                 # seg 1: slides in up's face
        "edit_add_stub lo 0",                 # seg 2
        "edit_set_slide 0 0 460",             # lo clamps to the range floor
                                              # (400 face + 20 min-stub push-out)
        "edit_set_slide 1 1000 1200",         # valid window on the stub
        "edit_set_slide 1 clear",             # ...then unstaged
        "edit_remove_segment 2",              # re-key check (no window ≥ 2)
        "edit_commit pin",
    ):
        s.do_command(cmd)
    w = s.bundles[0]
    slo, shi = list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi)
    assert (slo[0], shi[0]) == (420.0, 460.0)   # lo clamped 0 → 420
    assert all(math.isnan(v) for v in slo[1:])  # seg 1 cleared, seg 2 gone


# ── Codex #305 review fixes ───────────────────────────────────────────────────

def test_cli_commit_without_pin_discards_overrides(capsys):
    """Codex #305: an un-pinned edit_commit appends the candidate but does NOT
    select it — per-segment overrides (staged slide windows, edit_set_layer
    pins) are indexed by the committed topology and attach to the SELECTION,
    so writing them would misapply to whatever is selected (the length guard
    can coincide).  They must be discarded LOUD, and the live plan/input state
    left untouched."""
    import math
    s = _dd_base_session()                    # C committed+pinned via the flow
    w = s.bundles[0]
    sel_before = w.plan.selected_topology_index
    slide_before = list(w.plan.seg_slide_lo)
    pins_before = list(w.input.pinned_seg_layers)
    for cmd in (
        "edit_topology 1 new",
        "edit_add_trunk V 500 200 1200",
        "edit_add_stub up 0",
        "edit_add_stub lo 0",
        "edit_set_slide 0 420 460",
        "edit_set_layer 1 5",                 # H stub → V layer (just a change)
        "edit_commit",                        # NO pin
    ):
        s.do_command(cmd)
    out = capsys.readouterr().out
    assert 'slide window(s) discarded' in out
    assert 'NOT pinned' in out
    assert w.plan.selected_topology_index == sel_before   # selection untouched
    assert list(w.plan.seg_slide_lo) == slide_before      # no plan pollution
    assert list(w.input.pinned_seg_layers) == pins_before


def test_cli_edit_set_layer_pins_on_commit():
    """Codex #305: edit_set_layer must survive planning — layer_hint alone is
    a suggestion; a pinning commit rebuilds wrapper.input.pinned_seg_layers
    from the working copy (GUI parity)."""
    s = _dd_base_session()
    for cmd in (
        "edit_topology 1 new",
        "edit_add_trunk V 500 200 1200",      # seg 0, default V layer 5
        "edit_add_stub up 0",                 # seg 1, H layer 4
        "edit_add_stub lo 0",                 # seg 2, H layer 4
        "edit_set_layer 0 5",                 # explicit (same value, still a pin)
        "edit_commit pin",
    ):
        s.do_command(cmd)
    w = s.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    assert list(w.input.pinned_seg_layers) == \
        [sg.layer_hint for sg in topo.segments]


def test_sidecar_load_preserves_user_topo(tmp_path, capsys):
    """Codex #305: _load_sidecar must carry user_topo through — a designer
    opening an explorer on an existing sidecar and re-saving any selection
    must not strand the USER candidate's replay log.  Re-pinning the SAME
    USER candidate keeps it too."""
    s = _dd_base_session()
    sc = str(tmp_path / "sc.json")
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, sidecar_path=sc,
                                    layer_stack=s.layers)
    try:
        _key(exp, 'E')
        _trunk(exp, 'Y', 500, 700)
        exp.sidx = 0
        _key(exp, 'S', x=200, y=1200)
        _key(exp, 'S', x=200, y=200)
        _key(exp, 'enter')                    # commit + pin → sidecar user_topo
        assert 'user_topo' in exp._find_selection()
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')

    # A SECOND explorer loads the same sidecar, then re-saves: the replay log
    # must survive the load→save cycle.
    exp2 = buda_viz.TopologyExplorer(s.fp, s.bundles, sidecar_path=sc,
                                     layer_stack=s.layers)
    try:
        sel = exp2._find_selection()
        assert sel is not None and 'user_topo' in sel, \
            "_load_sidecar dropped the replay log"
        exp2._save_sidecar()
        import json
        on_disk = json.load(open(sc))['selections'][0]
        assert 'user_topo' in on_disk
        # Re-pinning the same USER candidate (s on the shown selection) keeps it.
        exp2._select_current()
        sel2 = exp2._find_selection()
        assert sel2 is not None and 'user_topo' in sel2
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_edit_temp_grid_line_places_trunk_mid_channel(tmp_path):
    """'G' while a trunk is armed drops a TEMPORARY Hanan line at the cursor
    on the armed axis and pins the placement EXACTLY there — the escape for an
    OFF-CENTER coordinate (hover now snaps to cell centerlines, so the channel
    centerline needs no G; an upper-half trunk like c_ddd's y=850 does).  The
    line joins the grid for the session (gone after commit/abort)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
                "add_block lo 0 0 400 400", "add_block up 0 1000 400 1400",
                "add_bus w[4] lo.o up.i", "run_bundler",
                "generate_topologies"):
        s.do_command(cmd)
    exp = _explorer(s, tmp_path)
    try:
        _key(exp, 'E')
        _key(exp, 'T', 200, 860)              # arm: hover snaps to the CHANNEL
        assert exp._trunk_hover == 700        #   centerline (400..1000 cell)
        _key(exp, 'G', 200, 850)              # temp line at the cursor's row
        assert exp._trunk_hover == 850
        assert 850 in exp._bundle_hanan_grid()[1]
        _key(exp, 'T', 200, 852)              # place: the G pin wins verbatim
        sg = exp._edit_topo.segments[0]
        assert sg.start.y == sg.end.y == 850  # off-center, exactly as dropped
        _key(exp, 'escape')                   # abort → temp lines cleared
        assert 850 not in exp._bundle_hanan_grid()[1]
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
