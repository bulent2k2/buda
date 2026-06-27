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
    """A segment through a multi-rect block's notch/gap must NOT be reported as a
    pass-through (its solid geometry is not crossed), but a segment through one of
    its rectangles must be."""
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
    pass_lines = [ln for ln in buf.getvalue().splitlines() if "passthru:" in ln]
    # The trunk runs at y≈1050, inside the gap → notch not crossed.
    assert pass_lines, buf.getvalue()
    assert not any("notch" in ln for ln in pass_lines), pass_lines

    # Now route at a y that lands inside the lower rect → notch IS crossed.
    s2 = _run([
        "def_layer 4 M4 H TOP 0.0", "def_layer 5 M5 V TOP 0.0",
        "add_block src 0 400 100 500", "add_block dst 3000 400 3100 500",
        "add_block notch rect 1000 0 1200 900 rect 1000 1200 1200 2000",
        "add_bus a[4] src dst", "run_bundler", "generate_topologies", "run_planner",
    ])
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        s2.do_command("dump_topologies --conn")
    pass_lines2 = [ln for ln in buf2.getvalue().splitlines() if "passthru:" in ln]
    assert any("notch" in ln for ln in pass_lines2), buf2.getvalue()
