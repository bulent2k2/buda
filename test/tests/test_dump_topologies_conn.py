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

"""Smoke test for `dump_topologies --conn`: the per-segment connectivity detail
(what each seg connects to, its pass-through busterms, slide range, net-pull)."""

import io
import re
from contextlib import redirect_stdout


def _run(lines):
    from buda_cli import BudaSession
    s = BudaSession()
    for line in lines:
        s.do_command(line)
    return s


def test_dump_conn_emits_per_segment_detail():
    # A multicast trunk so the selected topology has a trunk + several stubs:
    # the detail block must list busterms, seg-to-seg joins, slide and pull.
    s = _run([
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block drv  0    0  400 200",
        "add_block r1   1000 0  1400 200",
        "add_block r2   1000 800 1400 1000",
        "add_block r3   1000 1600 1400 1800",
        "add_bus a[4] drv r1,r2,r3",
        "run_bundler",
        "generate_topologies",
        "run_planner",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")
    out = buf.getvalue()

    # Header + the four requested detail facets must all be present.
    assert "conn detail" in out, out
    assert "busterms:" in out, out          # (1) seg -> busterm connections
    assert "segs:" in out, out              # (1) seg -> seg connections
    assert "passthru:" in out, out          # (2) pass-through busterms
    assert "slide=" in out, out             # (3) slide range
    assert "pull=" in out, out              # (4) net-pull preference


def test_dump_without_conn_has_no_detail():
    """Plain dump_topologies stays terse — the detail block is opt-in."""
    s = _run([
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block l 0 0 100 100",
        "add_block r 300 0 400 100",
        "add_bus a[4] l r",
        "run_bundler",
        "generate_topologies",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies")
    out = buf.getvalue()
    assert "conn detail" not in out
    assert "passthru:" not in out


def _seg_lines(s):
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")
    return [ln.strip() for ln in buf.getvalue().splitlines()
            if ln.strip().startswith("seg") and " M" in ln]


def test_dump_reports_planned_layer_not_conntopology_hint():
    """The seg layer must come from the planner's seg_layers (the layer NUTS
    routes on), not ConnTopology's normalized hint.  Before planning the detail
    falls back to the hint and marks it `·hint`; after planning it reports the
    effective layer with no suffix, matching w.plan.seg_layers exactly."""
    setup = [
        "def_layer 4 M4 H TOP 0.0", "def_layer 6 M6 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0", "def_layer 7 M7 V TOP 0.0",
        "add_block l 0 0 100 100", "add_block r 400 0 500 100",
        "add_bus a[4] l r", "run_bundler", "generate_topologies",
    ]
    s = _run(setup)
    # Pre-plan: hint, explicitly suffixed.
    assert all("·hint" in ln for ln in _seg_lines(s)), _seg_lines(s)

    s.do_command("run_planner")
    lines = _seg_lines(s)
    assert lines and all("·hint" not in ln for ln in lines), lines
    # Each reported layer equals the planner's effective seg_layers.
    w = s.bundles[0]
    sel = w.plan.selected_topology_index
    planned = [f"M{lyr}" for lyr in list(w.plan.seg_layers)]
    reported = [ln.split()[2] for ln in lines]   # "seg0 H M6 ..." -> "M6"
    assert reported == planned, (reported, planned)


def test_dump_passthrough_respects_multirect_notch():
    """A segment through a multi-rect block's notch/gap must NOT be reported as
    crossed (its solid geometry is untouched), but a segment through one of its
    rectangles must be.  `notch` is not a bundle block, so a genuine crossing
    shows under `otc-over:` (over-the-cell flyover), never under `passthru:`
    (bundle-block coverage)."""
    # `notch` is two rects: below y=900 and above y=1200 — a gap spans y∈(900,1200).
    base = [
        "def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
        "add_block src 0 1000 100 1100", "add_block dst 3000 1000 3100 1100",
        "add_block notch rect 1000 0 1200 900 rect 1000 1200 1200 2000",
        "add_bus a[4] src dst", "run_bundler", "generate_topologies", "run_planner",
    ]
    s = _run(base)
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")
    out = buf.getvalue()
    # The trunk runs at y≈1050, inside the gap → notch not crossed anywhere.
    assert "passthru:" in out, out
    assert not any("notch" in ln for ln in out.splitlines()
                   if "passthru:" in ln or "otc-over:" in ln), out

    # Now route at a y that lands inside the lower rect → notch IS crossed —
    # reported as otc-over (not a bundle block), and never as passthru.
    s2 = _run([
        "def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
        "add_block src 0 400 100 500", "add_block dst 3000 400 3100 500",
        "add_block notch rect 1000 0 1200 900 rect 1000 1200 1200 2000",
        "add_bus a[4] src dst", "run_bundler", "generate_topologies", "run_planner",
    ])
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        s2.do_command("dump_topologies --conn")
    lines2 = buf2.getvalue().splitlines()
    assert any("otc-over:" in ln and "notch" in ln for ln in lines2), buf2.getvalue()
    assert not any("passthru:" in ln and "notch" in ln for ln in lines2), lines2


def test_dump_passthru_scoped_to_bundle_blocks():
    """The big2 b61 report bug: an unrelated block filling the corridor between
    the endpoints is crossed by every direct route — normal over-the-cell
    routing, NOT a pass-through.  It must be listed under `otc-over:` and the
    `passthru:` line must stay empty, consistent with the candidate table's
    `pass 0` (the bundle-scoped C++ count)."""
    s = _run([
        "def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
        "add_block A 0 0 100 300", "add_block B 400 0 500 300",
        "add_block obs 100 0 400 300",    # fills the corridor, abuts both faces
        "add_bus a[4] A.p B.p", "run_bundler", "generate_topologies", "run_planner",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")
    lines = buf.getvalue().splitlines()
    assert any("otc-over:" in ln and "obs" in ln for ln in lines), buf.getvalue()
    assert not any("passthru:" in ln and "obs" in ln for ln in lines), lines


def test_crossing_predicate_requires_interior_overlap():
    """The report's crossing test needs POSITIVE-length overlap: a block the
    segment merely abuts at a single point (big2 b61: the trunk's endpoints
    land on blk_37/blk_39's faces at x=1250/4870) or whose face line the wire
    rides is not crossed.  Exact coordinates from the b61 repro."""
    from types import SimpleNamespace
    from buda_cli import BudaSession
    s = BudaSession()
    trunk = SimpleNamespace(horiz=True, perp_pos=3505, along_lo=1250, along_hi=4870)
    assert not s._seg_crosses_rect(trunk, 0, 3310, 1250, 3895)      # blk_37: point-touch
    assert not s._seg_crosses_rect(trunk, 4870, 3365, 6100, 4425)   # blk_39: point-touch
    assert s._seg_crosses_rect(trunk, 1250, 3225, 2230, 3780)       # blk_05: real crossing
    assert s._seg_crosses_rect(trunk, 2230, 3365, 3500, 4615)       # blk_15: real crossing
    # Riding a block's face line (perp == edge) is boundary, not interior.
    edge_rider = SimpleNamespace(horiz=True, perp_pos=3780,
                                 along_lo=1250, along_hi=4870)
    assert not s._seg_crosses_rect(edge_rider, 1250, 3225, 2230, 3780)
    # Same rules for a vertical segment.
    vseg = SimpleNamespace(horiz=False, perp_pos=150, along_lo=0, along_hi=100)
    assert s._seg_crosses_rect(vseg, 100, 20, 200, 80)     # interior crossing
    assert not s._seg_crosses_rect(vseg, 100, 100, 200, 300)  # endpoint touch y=100
    assert not s._seg_crosses_rect(vseg, 150, 20, 300, 80)    # rides x=150 face


def _re_env(line):
    """Parse the `[lo..hi]` WL envelope out of a dump_topologies candidate row."""
    import re
    m = re.search(r"\[(\d+)\.\.(\d+)\]", line)
    return (int(m.group(1)), int(m.group(2))) if m else None


def test_dump_topologies_shows_wl_envelope_per_candidate():
    """Every candidate row carries a `wl[lo..hi]` DOF envelope: lo (tight joint
    slide minimum) <= hi (loose outer bound), and lo <= the nominal estimate."""
    import buda
    s = _run([
        "def_layer 4 M4 H TOP 0.0",
        "def_layer 5 M5 V TOP 0.0",
        "add_block A 0 0 100 100",
        "add_block B 400 300 500 400",     # offset -> Z/U candidates with real slide
        "add_bus a[4] A.p B.p",
        "run_bundler", "generate_topologies", "run_planner",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies")
    out = buf.getvalue()
    assert "wl[lo..hi]" in out, out
    envs = [_re_env(ln) for ln in out.splitlines() if _re_env(ln)]
    assert envs, out
    for lo, hi in envs:
        assert 0 <= lo <= hi, (lo, hi)

    # Cross-check the metric directly: lo is a real lower bound (<= the nominal
    # as-generated length of the same ConnTopology), and lo <= hi.
    w = s.bundles[0]
    for c in w.input.candidates:
        lo, hi = s._topology_wl_interval(c)
        ct = buda.ConnTopology(); ct.build(c, s.fp)
        nominal = sum(abs(cs.along_hi - cs.along_lo) for cs in ct.segs())
        assert 0 <= lo <= hi, (c.type, lo, hi)
        assert lo <= nominal + 0.5, (c.type, lo, nominal)   # tight floor <= as-generated


def test_flat_dump_with_open_bdb_stays_on_session_fp():
    """Codex #231: a FLAT flow's bundles are also `buda.HBundle`s, so with any
    BDB open (e.g. a checkpoint) the resolver must NOT route them through the
    BDB depth floorplan — their candidates were generated against `session.fp`
    (hand-added blocks the BDB has no components for).  The dump must show the
    same finite slides as the no-BDB flat flow, never `free`."""
    s = _run([
        "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
        "open_bdb :memory:",                       # empty checkpoint BDB
        "add_block A 0 0 100 100", "add_block B 300 20 400 120",
        "add_bus ab[8] A.tx B.rx",
        "run_bundler strict", "generate_topologies",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies")
    out = buf.getvalue()
    assert "mslide" in out, out
    assert "free" not in out, out   # flat candidates resolve against self.fp
    # The direct I_H's slide is the blocks' 80-unit shared y-overlap [20..100].
    assert re.search(r"\bI_H\s+\d+\s+\[\d+\.\.\d+\]\s+1\s+0\s+80\b", out), out


def test_mslide_resolves_cell_local_before_planner():
    """A cell-level HBundle template is still in cell-local coordinates before
    `run_planner hier`.  dump_topologies resolves each hier bundle's
    ConnTopology against the floorplan its candidates were GENERATED in (the
    cell-local one, via `_make_topo_fp_resolver` — the same resolution
    check_design uses), so `mslide`, `wl[lo..hi]`, and the `--conn`
    slide column show real finite numbers pre-planner — matching the flat
    flow — instead of the unbounded-sentinel `free` (which remains the
    display for a genuinely unresolvable candidate; PR #215)."""
    s = _run([
        "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
        "open_bdb :memory:",
        "add_cell proc_cell 420 200", "add_cell pipe_cell 110 80",
        "add_inst_to_cell proc_cell pa_i pipe_cell 20 60",
        "add_inst_to_cell proc_cell pb_i pipe_cell 155 60",
        "add_inst proc_i proc_cell - 350 50",
        "derive_busterms 1",
        "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
        "bdb_net_mode on",
        "add_bus pa_pb[8] proc_i/pa_i.out proc_i/pb_i.in",
        "run_hier_bundler depth 1", "generate_hier_topologies",
    ])
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")   # BEFORE run_planner hier
    out = buf.getvalue()
    assert "mslide" in out, out
    # Every slide resolves against the cell-local floorplan: no `free` left.
    assert "free" not in out, out
    # The I_H candidate's slide is the two children's shared face overlap in
    # cell-local coords (pipe cells at y 60..140 inside the 420x200 cell -> 80),
    # and its envelope is the fixed direct wire [25..25].
    assert re.search(r"\bI_H\s+25\s+\[25\.\.25\]\s+1\s+0\s+80\b", out), out
    # --conn prints the finite cell-local window, not `free`.
    assert "slide=[60..140] = 80" in out, out
    # And the raw sentinel integer never leaks into any column.
    sess = s.__class__
    sentinel = sess._SLIDE_SENTINEL
    assert not any(tok.lstrip("-").isdigit() and abs(int(tok)) >= sentinel
                   for tok in out.split()), out

    # AFTER run_planner hier the expanded per-instance wrapper reports the
    # SAME slide magnitudes in absolute coordinates (cell window offset by
    # the instance position) — the pre-planner dump now matches it.
    with redirect_stdout(io.StringIO()):
        s.do_command("run_planner hier")
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.do_command("dump_topologies --conn")
    out2 = buf.getvalue()
    assert re.search(r"\bI_H\s+25\s+\[25\.\.25\]\s+1\s+0\s+80\b", out2), out2
    assert "slide=[110..190] = 80" in out2, out2
