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

"""NDR × hierarchical flows (the real hbundles vehicles, 06-10).

NDR phase 1 is FLAT-FLOW ONLY, and the v21 rule persistence must coexist
with fully-populated hierarchical BDBs.  These tests clone the canonical
hier vehicles (flow/hbundles/06..10) and pin both boundaries on them:

1. the phase-1 refusal is LOUD on every real hier vehicle — declaring NDR
   scopes before run_hier_bundler hard-errors instead of silently routing
   governed nets at default width/spacing/no-shield (the bare-session unit
   test's contract, proven against real cell hierarchies);
2. v21 rules/scopes persist into and restore from a HIER design BDB — the
   new tables ride beside the full bundle/topology/routing row population
   (write-through after the flow, restore in a fresh session), and a hier
   flow leaves the tables empty (no silent rule creation);
3. the flows themselves still run to completion under the v21 schema.

Mid tier, like the other full-flow integration tests (test_flow_scripts).
"""
import contextlib
import io
import pathlib

import pytest

import buda
import buda_cli
from buda_cmds import ndr_cmds

pytestmark = pytest.mark.mid

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FLOW = _ROOT / "flow" / "hbundles"

_HIER_FLOWS = [
    "06_multipin_stress.buda",
    "07_wide_fan_stress.buda",
    "08_cross_level.buda",
    "09_local_global_compete.buda",
    "10_chip_units_blocks_leaf.buda",
]


def _flow_lines(name, bdb_path=None):
    """The flow's command lines, cleaned for session-level replay: comments
    dropped, `source ../tracks/...` made absolute (replay has no script CWD),
    `visualize` dropped (headless), and — when bdb_path is given — the
    `:memory:` BDB redirected to a real file so persistence is observable
    across sessions."""
    lines = []
    for raw in open(_FLOW / name):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("visualize"):
            continue
        if line.startswith("source "):
            rel = line.split(None, 1)[1]
            line = f"source {(_FLOW / rel).resolve()}"
        if bdb_path is not None and line.startswith("open_bdb"):
            line = f"open_bdb {bdb_path}"
        lines.append(line)
    return lines


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


@pytest.mark.parametrize("flow", _HIER_FLOWS)
def test_hier_bundler_refuses_ndr_scopes_on_real_vehicle(flow):
    # Inject a rule + scope right before run_hier_bundler: the phase-1
    # flat-only contract must refuse LOUDLY on the real hier vehicle, not
    # silently ignore the declared constraints.
    s = _session()
    lines = _flow_lines(flow)
    with pytest.raises(SystemExit):
        for line in lines:
            if line.startswith("run_hier_bundler"):
                _run(s, "def_ndr clk2x width x2 shield bus")
                _run(s, "set_ndr net_ clk2x")
            _run(s, line)


@pytest.mark.parametrize("flow", _HIER_FLOWS)
def test_v21_rules_persist_beside_a_hier_design(flow, tmp_path):
    # Run the full hier flow against a DISK BDB (the flows use :memory:),
    # then persist rules/scopes into the populated design DB and restore
    # them in a fresh session — the v21 tables coexist with the whole
    # bundle/topology/routing row population.
    bdb_path = tmp_path / f"{flow}.bdb"
    s = _session()
    for line in _flow_lines(flow, bdb_path=bdb_path):
        _run(s, line)
    # The hier flow itself created no rules (no silent rule creation).
    assert list(s.bdb.ndr_rules()) == []
    assert list(s.bdb.ndr_scopes()) == []
    # Declare AFTER the hier pipeline ran (scopes now would not re-enter
    # run_hier_bundler): write-through lands in the hier BDB.
    _run(s, "def_ndr shielded width x2 spacing x2 shield bus net GND")
    _run(s, "set_ndr fut_ shielded")
    assert [r.name for r in s.bdb.ndr_rules()] == ["shielded"]
    del s
    s2 = _session()
    out = _run(s2, f"open_bdb {bdb_path}")
    assert "restored 1 rule(s) and 1 scope(s)" in out
    assert ndr_cmds.ndr_rule_for_net(s2, "fut_x") == "shielded"
    r = s2._ndr_rules["shielded"]
    assert r["width_x"] == 2.0 and r["shield_mode"] == 1


def test_hier_flow_completes_clean_under_v21(tmp_path):
    # One representative end-to-end sanity: the deepest vehicle (08, the
    # cross-level design) runs to a detailed result against a disk BDB at
    # schema v21, and the DB reports the current version.
    bdb_path = tmp_path / "cross_level.bdb"
    s = _session()
    for line in _flow_lines("08_cross_level.buda", bdb_path=bdb_path):
        _run(s, line)
    assert s.detailed_result is not None
    assert s.bdb.schema_version() == buda.BDB.SCHEMA_VERSION
