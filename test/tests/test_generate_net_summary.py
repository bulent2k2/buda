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

"""generate_topologies / generate_hier_topologies append a `<n> nets: <summary>`
suffix to each per-bundle log line, mapping a bundle to its constituent buses/nets
so a reader of the flow log can tie a bundle id (used by ripup_reroute,
select_topology, dump_topologies) back to the buses it carries — e.g.
`Generated 5 topologies for bundle 3 (u0->v0) 16 nets: bv1[0:15]`."""
import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402


# --- unit: the formatting helpers -------------------------------------------

def test_fmt_index_ranges_compresses_runs():
    f = buda_cli.BudaSession._fmt_index_ranges
    assert f([0, 1, 2, 3]) == "0:3"
    assert f([0, 1, 2, 3, 5, 6]) == "0:3,5:6"
    assert f([4]) == "4"
    assert f([3, 1, 2, 0]) == "0:3"          # unsorted input
    assert f([0, 0, 1, 1, 2]) == "0:2"       # de-dups
    assert f([]) == ""


def test_bundle_net_summary_groups_buses_and_singletons():
    s = buda_cli.BudaSession()
    # default add_bus naming: <bus>_<idx>
    assert s._bundle_net_summary([f"bus_007_{i}" for i in range(28)]) == "bus_007[0:27]"
    # explicit add_net bit-tag naming: <bus>_b<idx>
    assert s._bundle_net_summary(["bus_077_b00", "bus_077_b01", "bus_077_b02"]) \
        == "bus_077[0:2]"
    # a net with no numeric suffix is listed verbatim
    assert s._bundle_net_summary(["clk"]) == "clk"
    # mixed buses + singletons, first-seen order preserved
    assert s._bundle_net_summary(["ctrl_0", "ctrl_1", "reset", "ctrl_2"]) \
        == "ctrl[0:2], reset"


def test_bundle_net_summary_truncates_long_lines():
    s = buda_cli.BudaSession()
    many = [f"net_{i}_x{i}" for i in range(200)]   # 200 distinct singleton-ish names
    out = s._bundle_net_summary(many, max_len=40)
    assert len(out) <= 41 and out.endswith("…")


# --- integration: the log line appears with the right content ---------------

def _run(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return buf.getvalue()


def test_generate_topologies_logs_bundle_net_correspondence():
    out = _run([
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_layer 7 M7 V TOP 50",
        "add_block A 0 0 200 200",
        "add_block B 1000 0 1200 200",
        "add_bus data[8] A.p B.p",
        "run_bundler",
        "generate_topologies",
    ])
    assert "for bundle 1 (A->B) 8 nets: data[0:7]" in out
