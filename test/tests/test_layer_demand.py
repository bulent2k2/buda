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

"""Per-instance, per-layer demand (convergence ladder item 3).

`_layer_demand` reads, off the routed result, what the REST of the design
placed over each instance's footprint, in the currency `set_cell_layer_share`
speaks (signal tracks of the layer's pattern inside the instance bbox).  It
is the number a driver hands DOWN as the complement share, so the properties
worth pinning are the ones a share derivation would silently get wrong:

  * an instance's OWN routing is not demand on it — a cell-local bus lives in
    the instance and must not eat the instance's own budget — while the same
    metal IS demand on a sibling it crosses and on nothing it does not;
  * a top-level bus is demand on every instance it reaches over and none it
    does not (the tap that ends ON a face reaches over nothing);
  * `used` counts tracks, `bits` counts traffic, and used <= bits always;
  * a demand that was never computed is None (the Tcl query's -1), not 0.

Two vehicles: a hand-built two-instance design where every number can be
worked by hand, and a top bus placed OVER a sibling instance.
"""
import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


def _quiet(s, *lines):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in lines:
            s.do_command(c)


def _cmd(s, line):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(line)
    return buf.getvalue()


# M6 (H, TOP): unit pitch 34 with 8 signal slots -> a bit pitch of 4.25.
# Two instances of one cell, each holding a cell-local 8-bit bus a->b, and
# an 8-bit top bus from u1/b to u2/a.  Everything routes on M6 at y=150.
_DESIGN = [
    f"source {_ROOT / 'flow' / 'tracks' / 'tracks.buda'}",
    "open_bdb :memory:",
    "add_cell top_cell 600 200",
    "add_cell leaf 80 80",
    "add_inst_to_cell top_cell a leaf 20 60",
    "add_inst_to_cell top_cell b leaf 300 60",
    "add_inst u1 top_cell - 50 50",
    "add_inst u2 top_cell - 900 50",
    "derive_busterms 1",
    "add_blocks_from_bdb 0",
    "add_blocks_from_bdb 1 skip",
    "bdb_net_mode on",
    "add_bus loc[8] u1/a.out u1/b.in",
    "add_bus loc2[8] u2/a.out u2/b.in",
    "add_bus x[8] u1/b.out u2/a.in",
    "run_hier_bundler depth 1",
    "generate_hier_topologies",
    "run_planner hier 3",
]


def _session(*extra):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN, *extra)
    return s


def _row(rows, inst, layer):
    hit = [r for r in rows if r["inst"] == inst and r["layer_name"] == layer]
    assert len(hit) == 1, (inst, layer, rows)
    return hit[0]


def test_never_computed_is_none_not_zero():
    """Before run_nuts there is nothing to read the demand off; the answer
    is None (the Tcl bridge's -1), never an empty or all-zero table."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    assert s._layer_demand() is None
    s = _session()                      # planned, not placed
    assert s._layer_demand() is None
    _quiet(s, "run_nuts")
    assert s._layer_demand() is not None


def test_own_routing_is_not_demand_and_a_top_bus_is():
    """u1's cell-local `loc` bus is u1's own metal: not demand on u1.  The
    top bus `x` reaches over u1 (its tap enters the bbox to land on u1/b's
    face) and over u2, so both see its 8 bits; it reaches nothing else."""
    s = _session("run_nuts")
    rows = s._layer_demand()
    u1 = _row(rows, "u1", "M6")
    assert u1["bits"] == 8 and u1["bundles"] == 1, u1
    # Abstract reading: the bus is 8 pitches of metal centred on its track,
    # which catches 8 or 9 track centres depending on phase — never fewer
    # than the bits, never more than one extra.
    assert 8 <= u1["used"] <= 9, u1
    assert u1["supply"] > u1["used"] and 0 < u1["pct"] < 100
    u2 = _row(rows, "u2", "M6")
    assert u2["bits"] == 8 and u2["bundles"] == 1
    # Every other (instance, layer) is untouched: leaves see nothing (the
    # taps end ON their faces), and no other layer carries metal.
    for r in rows:
        if r["inst"] in ("u1", "u2") and r["layer_name"] == "M6":
            continue
        assert r["bits"] == 0 and r["used"] == 0 and r["pct"] == 0.0, r


def test_used_counts_tracks_bits_counts_traffic():
    """Two foreign buses over one instance on the SAME tracks are 16 bits of
    traffic but only ~8 tracks of footprint: used <= bits, and the union is
    what a uniform share must leave free."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN[:-3],
           # a second top bus, the other way, sharing the same M6 tracks
           "add_bus y[8] u2/a.out u1/b.in",
           "run_hier_bundler depth 1", "generate_hier_topologies",
           "run_planner hier 3", "run_nuts")
    rows = s._layer_demand("u1", "M6")
    u1 = _row(rows, "u1", "M6")
    assert u1["bits"] == 16 and u1["bundles"] == 2, u1
    assert u1["used"] <= u1["bits"]
    for r in rows:
        assert r["used"] <= r["supply"], r


def test_detailed_reading_is_exact():
    """Once DNUTS has placed every bit on its own track the count is exact:
    8 bits on 8 tracks, no phase slack."""
    s = _session("run_nuts", "run_detailed_nuts")
    u1 = _row(s._layer_demand("u1"), "u1", "M6")
    assert u1["bits"] == 8 and u1["used"] == 8, u1
    assert "detailed bit tracks" in _cmd(s, "report_layer_demand u1 M6")


def test_filters_and_the_unknown_layer():
    """An instance filter keeps the instance and its subtree; `cell:` keeps
    every placed instance of a cell; an unknown layer is the caller's
    error rather than an empty table."""
    s = _session("run_nuts")
    subtree = {r["inst"] for r in s._layer_demand("u1")}
    assert subtree == {"u1", "u1/a", "u1/b"}, subtree
    leaves = {r["inst"] for r in s._layer_demand("cell:leaf")}
    assert leaves == {"u1/a", "u1/b", "u2/a", "u2/b"}, leaves
    one = s._layer_demand("u2", "M6")
    assert {r["layer_name"] for r in one} == {"M6"}
    assert s._layer_demand("nosuch") == []
    with pytest.raises(ValueError, match="unknown layer"):
        s._layer_demand("", "M9")
    out = _cmd(s, "report_layer_demand u1 M9")
    assert "Error" in out and "unknown layer" in out, out


def test_the_report_prints_the_table_and_the_worst():
    """`report_layer_demand` is the same rows as a table, closing with the
    per-layer worst instance — where a share derivation starts."""
    s = _session("run_nuts")
    out = _cmd(s, "report_layer_demand")
    assert "=== Layer demand (abstract bus tracks" in out, out
    assert "worst per layer" in out and "M6" in out, out
    # a bare report before NUTS says what it needs rather than printing 0s
    s2 = _session()
    out2 = _cmd(s2, "report_layer_demand")
    assert "Error" in out2 and "run_nuts" in out2, out2


def test_layers_without_a_pattern_are_not_counted():
    """A layer with no def_track_pattern has no track supply to count
    against, so it has no row: the table cannot invent a 0-of-0."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, "def_layer 6 M6 H TOP 30", "def_layer 7 M7 V TOP 30",
           "def_track_pattern 6 0 VDD 2 1 (_ 1 1)x8 GND 2 1",
           *_DESIGN[1:], "run_nuts")
    rows = s._layer_demand()
    assert rows and {r["layer_name"] for r in rows} == {"M6"}


def test_a_reversed_span_is_still_demand():
    """NUTS may store a segment's extent as span_lo > span_hi (endpoint
    identity the corner logic relies on — see
    test_reversed_span_connectivity.py).  A raw-bound overlap test would
    call such a segment disjoint from every instance between its ends;
    the extent is ordered first, so the demand it places is the same
    either way round (Codex P1 on #933)."""
    s = _session("run_nuts")
    before = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    assert before["bits"] == 8
    flipped = 0
    for ts in s.nuts_result.segments:
        if ts.layer == 6 and ts.bundle_id == 2:      # the top bus `x`
            ts.span_lo, ts.span_hi = ts.span_hi, ts.span_lo
            flipped += 1
    assert flipped == 1
    after = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    assert after == before, (before, after)


def test_shield_rows_block_tracks_but_are_not_bits():
    """An NDR shield is a first-class NetSegment (`is_shield`): metal that
    takes a track, not a member bit.  It counts in `used` and not in
    `bits`, so `bits` keeps measuring the traffic (Codex P2 on #933)."""
    s = _session("run_nuts", "run_detailed_nuts")
    before = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    assert before["bits"] == 8 and before["used"] == 8
    marked = 0
    for ns in s.detailed_result.net_segments:
        if ns.layer == 6 and ns.bundle_id == 2 and ns.bit_index == 0:
            ns.is_shield = True
            marked += 1
    assert marked == 1
    after = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    assert after["bits"] == 7 and after["used"] == 8, after


def test_an_empty_detailed_result_is_zero_not_a_fallback():
    """A detailed result whose every bit went unplaced is an authoritative
    answer — no placed metal — and must not resurrect the abstract
    placement it superseded (Codex P2 on #933)."""
    s = _session("run_nuts", "run_detailed_nuts")
    s.detailed_result.net_segments = []
    rows = s._layer_demand()
    assert rows and all(r["bits"] == 0 and r["used"] == 0 for r in rows)
    assert "detailed bit tracks" in _cmd(s, "report_layer_demand u1 M6")


def test_ndr_guard_tracks_are_reserved_demand():
    """An NDR rule with spacing keeps guard slots EMPTY between and beyond
    its wires; detailed NUTS reserves them and emits no row for them, yet
    a cell cannot use them without violating the clearance.  The detailed
    reading counts the run the NDR_SPACING audit reads (one shared
    function), so a governed 8-bit bus with one guard slot per gap is 8
    bits of traffic and 17 tracks of footprint — the same 17 the abstract
    width prices (Codex P2 on #933, second round)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, _DESIGN[0], "def_ndr g spacing x2", "set_ndr x g",
           *_DESIGN[1:], "run_nuts")
    abstract = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    _quiet(s, "run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    detailed = _row(s._layer_demand("u1", "M6"), "u1", "M6")
    assert detailed["bits"] == 8, detailed
    assert detailed["used"] == 17 == abstract["used"], (abstract, detailed)
