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

"""Which bus a net belongs to — both spellings, in one place.

`add_bus p[4]` expands to `p_0 … p_3`; a design read from LEF/DEF/Verilog
keeps `p[0] … p[3]`.  Three call sites each carried a regex knowing only
the first, so on an imported design every bit looked like its own bus.
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda_cli
from bus_names import BRACKET_TAG, bus_key, bus_name, parse_bus_bit

_RV = Path(__file__).resolve().parents[2] / "flow" / "rv"


# ── the rule ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("net,expect", [
    # declared: add_bus / add_net
    ("bus_03_0",           ("bus_03", "", 0)),
    ("bus_077_b00",        ("bus_077", "b", 0)),
    # imported: the netlist's own names, including a hierarchical path
    ("instr[0]",           ("instr", BRACKET_TAG, 0)),
    ("dbg[31]",            ("dbg", BRACKET_TAG, 31)),
    ("u_cl/u_c0/ctrl[3]",  ("u_cl/u_c0/ctrl", BRACKET_TAG, 3)),
])
def test_both_spellings_parse(net, expect):
    assert parse_bus_bit(net) == expect


@pytest.mark.parametrize("net", [
    "mwe",          # a plain scalar
    "u_cl/we1",     # a scalar that happens to end in a digit
    "a[]",          # empty index
    "[0]",          # no base name
    "x[1a]",        # index that is not a number
    "w[1][0]",      # NOT a select — an escaped Verilog identifier's name
])
def test_a_net_that_is_not_a_bus_bit_is_its_own_bus(net):
    """Only the last bracket group is considered and it must be all digits,
    which is `derive_bus_bit`'s rule.  `w[1][0]` parses as bus `w[1]` bit 0
    — the honest reading of the STRING; the netlist reader is what knows it
    came from an escaped identifier, and it is the one that would have to
    say otherwise."""
    parsed = parse_bus_bit(net)
    if net == "w[1][0]":
        assert parsed == ("w[1]", BRACKET_TAG, 0)
        return
    assert parsed is None
    assert bus_key(net) == ("", net)
    assert bus_name(net) == net


def test_the_two_spellings_are_different_buses():
    """`p_0` and `p[0]` share a NAME but are not the same bus, and a key
    that merged them would be a new way to be wrong."""
    assert bus_name("p_0") == bus_name("p[0]") == "p"
    assert bus_key("p_0") != bus_key("p[0]")


def test_a_unicode_digit_is_not_an_index():
    """`str.isdigit()` is true for '٣' and `int()` accepts it, but the C++
    rule (`std::isdigit`) does not — so a name using one must not resolve to
    a bus here while `net_props.bus_name` leaves it alone."""
    assert parse_bus_bit("p[٣]") is None


# ── the three call sites that had their own copy ───────────────────────────

def test_the_design_stats_line_counts_buses_not_bits():
    """The visualizer reported `1230 buses · 1230 nets` for flow/rv — a bus
    count equal to the net count, on a design with 49 buses.  That line
    exists to be read at a glance, so a number that can only ever equal the
    net count is worse than no number."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ["open_bdb :memory:", "set_import_scale dbu",
                  f"import_lef_tech {_RV / 'soc.lef'}",
                  f"import_def_lef {_RV / 'soc.def'} {_RV / 'soc.lef'}",
                  f"import_verilog {_RV / 'soc.v'}",
                  "derive_container_bboxes margin 4000", "derive_busterms 4",
                  "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                  "add_blocks_from_bdb 2 skip", "add_blocks_from_bdb 3 skip",
                  "add_blocks_from_bdb 4 skip", "run_hier_bundler depth 4"]:
            s.do_command(c)

    from viz_main.panels import VizPanelsMixin

    class _Counter(VizPanelsMixin):
        def __init__(self, bundles):
            self.bundles = bundles

    _nb, nbus, nnets = _Counter(s.bundles)._design_counts()
    assert nnets == 1230
    # 49 buses + the 4 genuine scalars, each its own bus by the rule above.
    # Reconciled against the DATABASE, which derives bus_name independently
    # at import: 1226 of the 1230 nets carry one, over 49 distinct names.
    assert nbus == 53, nbus
    assert nbus < nnets / 10          # the property that was violated


def test_the_bundle_log_summary_compresses_an_imported_bus():
    """It exists to turn 32 net names into one range, and on an imported
    design it never did — the case where the line is longest."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    assert s._bundle_net_summary([f"dbg[{i}]" for i in range(32)]) == "dbg[0:31]"
    # …and the declared form still compresses exactly as before.
    assert s._bundle_net_summary([f"bus_07_{i}" for i in range(4)]) == "bus_07[0:3]"


def test_the_bit_cap_splitter_keeps_an_imported_bus_together():
    """`set_max_bundle_bits` promises "bits of one bus kept together".  With
    every bracket-named bit its own group there was nothing to keep
    together, so a bus could be cut down the middle with no diagnostic —
    the one failure in this family that is not cosmetic.

    Two 8-bit buses capped at 8: the split must fall BETWEEN them.
    """
    import buda
    from buda_cmds import bundling_cmds

    b = buda.HBundle()
    b.id = 1
    b.reason = "DRV:a|REC:b"
    b.net_names = ([f"lo[{i}]" for i in range(8)] +
                   [f"hi[{i}]" for i in range(8)])
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._max_bundle_bits = 8
    parts = bundling_cmds._split_hier_bundles(s, [b])

    assert len(parts) == 2, [p.net_names for p in parts]
    for p in parts:
        buses = {bus_name(n) for n in p.get_net_names()}
        assert len(buses) == 1, (buses, p.get_net_names())
