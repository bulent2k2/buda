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
"""select_topology / select_topologies accept a net-name hint (not just a
numeric bundle ID), stay backward compatible with numeric IDs + ranges, expose
id:/net: disambiguation, and print an error that names the bus and its candidate
count.  Regression for the flow/mst_bad.buda footgun (a bus whose bundle ID is
not its ordinal among the generated bundles)."""
import contextlib
import io

import buda_cli


def _sess(*extra):
    """Two buses (busA 2 bits, busB 3 bits) between three blocks; topologies
    generated for BOTH so each has a non-empty candidate pool."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "add_block A 0 0 100 100",
        "add_block B 400 0 500 100",
        "add_block C 200 300 300 400",
        "add_bus busA[2] A.p B.p",
        "add_bus busB[3] A.p B.p,C.p",
        "run_bundler",
        "generate_topologies",
        *extra,
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def _pinned(s, net_prefix):
    for w in s.bundles:
        names = w.input.original_bundle.get_net_names()
        if names and names[0].startswith(net_prefix):
            return (w.input.topology_pinned, w.plan.selected_topology_index)
    return (None, None)


def test_numeric_bundle_id_still_works():
    s = _sess()
    out = _run(s, "select_topology 1 2")
    assert "Pinned bundle 1" in out
    assert _pinned(s, s.bundles[0].input.original_bundle.get_net_names()[0][:4])[0]


def test_net_name_hint_pins_the_right_bundle():
    s = _sess()
    out = _run(s, "select_topology busB 1")
    assert "Pinned" in out and "busB" in out
    assert _pinned(s, "busB") == (True, 0)
    # a bundle the hint did NOT name stays unpinned
    assert _pinned(s, "busA")[0] in (False, None)


def test_id_and_net_disambiguation_prefixes():
    s = _sess()
    assert "Pinned bundle 1" in _run(s, "select_topology id:1 2")
    assert "busA" in _run(s, "select_topology net:busA 1")


def test_error_names_bus_and_range_when_id_out_of_range():
    s = _sess()
    out = _run(s, "select_topology busA 9999")
    assert "invalid topology id 9999" in out
    assert "busA" in out and "valid range is 1.." in out


def test_error_when_bundle_has_no_candidates():
    # Generate for NO bundle, then pin — the pool is empty.
    s = buda_cli.BudaSession(); s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 400 0 500 100",
                  "add_bus busA[2] A.p B.p", "run_bundler"):
            s.do_command(c)
    out = _run(s, "select_topology busA 1")
    assert "no candidates" in out and "generate_topologies" in out


def test_error_when_hint_matches_nothing():
    s = _sess()
    out = _run(s, "select_topology busZZZ 1")
    assert "no bundle whose first net starts with 'busZZZ'" in out


def test_select_topologies_mixes_hints_and_numeric_ranges():
    s = _sess()
    out = _run(s, "select_topologies busA,busB 1")
    assert out.count("Pinned") == 2
    assert _pinned(s, "busA") == (True, 0)
    assert _pinned(s, "busB") == (True, 0)
