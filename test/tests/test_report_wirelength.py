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

"""The `report_wirelength` (alias `report_wl`) command.

Reports routed wirelength per bundle + a design total so a change to topology
generation / planning / NUTS can be compared for interconnect quality.  The
abstract bus-level WL (one length per placed bus segment — the metric topology
decisions move) is reported after run_nuts; the detailed bit-level WL (every
bit-wire) is added after run_detailed_nuts.  The full per-bundle table is
captured to the flow log by the command wrapper; the terminal shows the total.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402


def _two_bus_session():
    """Two 8-bit buses on a shared H layer (M4), a dense signal pattern so
    DetailedNUTS places every bit — deterministic, no congestion."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_layer 7 M7 V TOP 50",
        "def_track_pattern 4 0 SIGNAL 1 1",
        "def_track_pattern 5 0 SIGNAL 1 1",
        "add_block D1 0 1000 200 1400",
        "add_block D2 400 1000 600 1400",
        "add_block R 2400 1000 2600 1400",
        "add_bus a[8] D1.p R.p",
        "add_bus b[8] D2.p R.p",
        "run_bundler", "generate_topologies", "run_planner", "run_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _run(s, cmd="report_wirelength"):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_no_nuts_result_is_a_clear_error():
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _run(s)
    assert "no NUTS result" in out


def test_abstract_total_matches_hand_sum():
    """The reported abstract total equals the hand-summed placed bus-segment
    lengths (sum |span_hi - span_lo|), and every bundle has its own line."""
    s = _two_bus_session()
    hand = sum(abs(t.span_hi - t.span_lo)
               for t in s.nuts_result.segments if t.placed)
    out = _run(s)
    # The greppable total is the full placed length (topology segments + jogs);
    # the line now also breaks it into (seg + jog) and reports the DOF envelope.
    assert f"total abstract WL = {hand:.0f} (seg " in out, out
    assert "over 2 bundle(s)" in out, out
    assert "DOF envelope" in out, out
    # Per-bundle lines: one per bundle id (sorted), plus a TOTAL row.
    assert "Abstract bus-level wirelength" in out
    assert "TOTAL" in out
    for bid in {t.bundle_id for t in s.nuts_result.segments}:
        assert any(line.split()[:1] == [str(bid)]
                   for line in out.splitlines()), f"bundle {bid} row missing"
    # No detailed section before run_detailed_nuts.
    assert "Detailed bit-level" not in out


def test_per_bundle_wl_sums_to_total():
    """Abstract table columns: bundle | bits | lo | WL | hi | jog | fill.
    The per-bundle WL (topology-segment wire, col 3) sums to the TOTAL row's WL
    (col 2: TOTAL | lo | WL | hi | jog)."""
    s = _two_bus_session()
    out = _run(s)
    per_bundle, total = {}, None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 7 and parts[0].isdigit():          # a bundle data row
            per_bundle[int(parts[0])] = float(parts[3])      # WL column
        if parts[:1] == ["TOTAL"]:
            total = float(parts[2])                          # TOTAL's WL column
    assert per_bundle and total is not None, out
    assert abs(sum(per_bundle.values()) - total) < 1e-6, out


def test_detailed_section_added_after_detailed_nuts():
    """After run_detailed_nuts the report adds a bit-level section whose total
    equals the hand-summed bit-wire lengths; the alias `report_wl` works too."""
    s = _two_bus_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    hand = sum(abs(n.span_hi - n.span_lo)
               for n in s.detailed_result.net_segments)
    n_wires = len(s.detailed_result.net_segments)
    out = _run(s, "report_wl")                       # exercise the alias
    assert "Abstract bus-level wirelength" in out     # still reports abstract
    assert "Detailed bit-level wirelength" in out
    assert (f"total detailed WL = {hand:.0f} over 2 bundle(s) / "
            f"{n_wires} bit-wire(s)") in out, out


def test_per_layer_breakdown_present():
    s = _two_bus_session()
    out = _run(s)
    # Both buses route on M4 here, so the by-layer line names M4 with the total.
    assert "by layer:" in out
    assert "M4=" in out


def _dogleg_session():
    """A bus whose only route is a Z (offset driver/receiver, blocker between) —
    the selected topology has real slide DOF, so its WL interval is non-trivial."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "add_block A 0 0 100 100",
        "add_block B 400 300 500 400",
        "add_bus n[8] A.p B.p",
        "run_bundler", "generate_topologies", "run_planner", "run_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_wl_interval_brackets_nonjog_actual():
    """The [lo, hi] envelope of each selected topology is a valid OUTER bracket:
    the routed abstract WL, minus NUTS-inserted jogs, lands inside it."""
    for s in (_two_bus_session(), _dogleg_session()):
        intervals = s._selected_wl_intervals()
        assert intervals, "no selected topologies to bracket"
        # Per-bundle routed WL split into topology-segment vs jog.
        seg, jog = {}, {}
        for ts in s.nuts_result.segments:
            if getattr(ts, "placed", True) is False:
                continue
            L = abs(ts.span_hi - ts.span_lo)
            d = jog if getattr(ts, "is_jog", False) else seg
            d[ts.bundle_id] = d.get(ts.bundle_id, 0.0) + L
        for bid, (lo, hi) in intervals.items():
            assert lo <= hi, f"bundle {bid}: inverted interval [{lo},{hi}]"
            actual = seg.get(bid, 0.0)          # non-jog topology wire
            assert lo - 0.5 <= actual <= hi + 0.5, (
                f"bundle {bid}: non-jog WL {actual} outside [{lo},{hi}]")


def test_report_shows_dof_envelope_and_all_inside():
    """The abstract report gains lo/hi/jog/fill columns and an inside-count, and
    the deterministic sessions route every bundle inside its envelope."""
    for s in (_two_bus_session(), _dogleg_session()):
        out = _run(s)
        assert "DOF envelope" in out
        assert "fill" in out and "jog" in out
        # "N/M bundle(s) inside" with N == M (all inside), and no '*' flag rows.
        import re
        m = re.search(r"(\d+)/(\d+) bundle\(s\) inside", out)
        assert m, out
        assert m.group(1) == m.group(2), f"some bundle out of envelope: {out}"
        assert " *" not in out, f"a bundle is flagged outside its bracket: {out}"


def _dnuts_open_session():
    """A bus pinned through an all-POWER corridor: DetailedNUTS finds zero
    signal tracks under its trunk, so all 8 bits go unplaced (deterministic)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "def_track_pattern 4 0 SIGNAL 1 4",
        "def_track_pattern 5 0 SIGNAL 1 4",
        "add_grid_override 4 600 900 2600 1550 0 POWER 2 3",
        "add_block D1 0 1000 200 1400",
        "add_block R 2400 1600 2600 2000",
        "add_bus a[8] D1.p R.p",
        "run_bundler", "generate_topologies",
        "select_topology 1 1",                 # pin L_HV through the dead band
        "run_planner", "run_nuts", "run_detailed_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_unplaced_bits_surfaced_in_detailed_total():
    """Codex P2: a WL comparison must not be fooled by incomplete routing.
    When bits are unplaced, the detailed total line carries the unplaced count
    and a NOTE says the WL excludes them — so a lower number is never mistaken
    for a tighter route."""
    s = _dnuts_open_session()
    assert s.detailed_result.num_unplaced == 8
    out = _run(s)
    assert "8 unplaced bit(s)" in out, out
    assert "NOT comparable to a complete route" in out, out


def test_all_ids_listed_even_at_zero_wl():
    """An all-unplaced bundle must still appear (as a 0-WL row), not vanish."""
    s = _dnuts_open_session()
    out = _run(s)
    bid = s.bundles[0].input.original_bundle.id
    # The bundle has zero placed detailed wires but must be in the table.
    assert any(line.split()[:1] == [str(bid)] for line in out.splitlines()), out


def test_clean_route_reports_zero_unplaced():
    """The count is always present (0 when complete), so every run is diffable
    on the same field."""
    s = _two_bus_session()
    out = _run(s)
    assert "0 unplaced segment(s)" in out, out


def _dogleg_adopt_session():
    """flow/dogleg1.buda: three buses in a cycle whose NUTS solve adopts a dogleg,
    pinning per-segment slide overrides that differ from ConnTopology's recomputed
    windows and leaving a jog segment in the topology."""
    from pathlib import Path
    flow = Path(__file__).parents[2] / "flow" / "dogleg1.buda"
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for line in flow.read_text().splitlines():
            c = line.strip()
            if not c or c.startswith("#"):
                continue
            if c.split()[0] in ("visualize", "visualize_topologies", "exit"):
                continue
            s.do_command(c)
    return s


def test_doglegged_bundle_envelope_uses_plan_slide_overrides():
    """Regression (PR #208 Codex P2): when NUTS adopts a dogleg it pins
    plan.seg_slide_lo/hi that it — not ConnTopology — places within, and leaves a
    jog segment in the topology.  The WL envelope must honour those overrides and
    exclude the jog's own span, or the doglegged bundle reads as a false
    out-of-envelope '*'."""
    import math
    s = _dogleg_adopt_session()
    # At least one bundle actually adopted a dogleg (has a non-NaN slide override).
    adopted = [w.input.original_bundle.id for w in s.bundles
               if any(not math.isnan(v) for v in list(w.plan.seg_slide_lo))]
    assert adopted, "scenario did not adopt a dogleg — override path not exercised"

    seg = {}
    for ts in s.nuts_result.segments:
        if getattr(ts, "placed", True) is False or getattr(ts, "is_jog", False):
            continue
        seg[ts.bundle_id] = seg.get(ts.bundle_id, 0.0) + abs(ts.span_hi - ts.span_lo)
    for bid, (lo, hi) in s._selected_wl_intervals().items():
        assert lo - 0.5 <= seg.get(bid, 0.0) <= hi + 0.5, (
            f"bundle {bid} (dogleg-adopted={bid in adopted}) non-jog WL "
            f"{seg.get(bid, 0.0)} outside envelope [{lo},{hi}]")
