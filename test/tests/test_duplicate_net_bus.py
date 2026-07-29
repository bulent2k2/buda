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

"""A net name must be unique.  `Netlist::add_net` only appends, so a redefined
net/bus name would silently create a second net (double-counted bits, or — with
different endpoints — two same-named bundles plus a clobbered endpoint map).
`add_net` / `add_bus` reject a duplicate name with a flow-stopping error."""
import contextlib
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402

_BASE = ("def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
         "add_block A 0 0 100 100", "add_block B 500 0 600 100",
         "add_block C 0 500 100 600")


def _run(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            for c in _BASE + cmds:
                s.do_command(c)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def _err(out):
    return next((l for l in out.splitlines() if l.startswith("Error:")), "")


def test_duplicate_add_net_same_endpoints_errors():
    code, out = _run("add_net foo A.p B.p", "add_net foo A.p B.p")
    assert code == 1
    assert "net 'foo' is already defined" in _err(out)


def test_duplicate_add_net_different_endpoints_errors():
    # The most dangerous case: two distinct nets sharing a name.
    code, out = _run("add_net foo A.p B.p", "add_net foo A.p C.p")
    assert code == 1
    assert "already defined" in _err(out)


def test_duplicate_add_bus_errors_and_names_bits():
    code, out = _run("add_bus bus_03[4] A.p B.p", "add_bus bus_03[4] A.p B.p")
    assert code == 1
    assert "bus_03" in _err(out) and "bus_03_0" in _err(out)


def test_bus_over_net_collision_errors():
    code, out = _run("add_net bus_03_0 A.p B.p", "add_bus bus_03[4] A.p B.p")
    assert code == 1
    assert "bus_03_0" in _err(out)


def test_net_over_bus_collision_errors():
    code, out = _run("add_bus bus_03[4] A.p B.p", "add_net bus_03_0 A.p C.p")
    assert code == 1
    assert "bus_03_0" in _err(out)


def test_distinct_names_ok():
    code, out = _run("add_net foo A.p B.p", "add_net bar A.p C.p",
                     "add_bus d[4] A.p B.p", "add_bus e[2] A.p C.p")
    assert code is None and not _err(out)


def test_piecewise_bus_ranges_ok():
    # Building one bus in disjoint index ranges is legitimate (no overlap).
    code, out = _run("add_bus b[0:1] A.p B.p", "add_bus b[2:3] A.p C.p")
    assert code is None and not _err(out)
