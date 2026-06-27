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
