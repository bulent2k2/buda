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
    assert f"total abstract WL = {hand:.0f} over 2 bundle(s)" in out, out
    # Per-bundle lines: one per bundle id (sorted), plus a TOTAL row.
    assert "Abstract bus-level wirelength" in out
    assert "TOTAL" in out
    for bid in {t.bundle_id for t in s.nuts_result.segments}:
        assert any(line.split()[:1] == [str(bid)]
                   for line in out.splitlines()), f"bundle {bid} row missing"
    # No detailed section before run_detailed_nuts.
    assert "Detailed bit-level" not in out


def test_per_bundle_wl_sums_to_total():
    s = _two_bus_session()
    out = _run(s)
    per_bundle, total = {}, None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].isdigit():
            per_bundle[int(parts[0])] = float(parts[2])
        if parts[:1] == ["TOTAL"]:
            total = float(parts[1])
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
