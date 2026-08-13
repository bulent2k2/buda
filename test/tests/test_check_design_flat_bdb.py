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

"""`check_design` on a FLAT flow with an open BDB — the checkpoint pattern.

A flat bundle is also a `buda.HBundle` (the type was renamed), so
`_check_design`'s bare isinstance test sent every flat bundle down the hier
floorplan resolution whenever a BDB was open.  The depth-projection floorplan
it resolved holds the BDB's components — not the session blocks the flat
candidates were generated against — so every busterm face failed: the same
clean design audited Success without `open_bdb` and 16 BUSTERM_FACE
violations with it.  That is exactly the flat checkpoint flow
(`flow/tcl/design.tcl`: open_bdb + run_bundler + … + check_design), which is
how it surfaced.

The fix gates the resolution on the hier bundler's own markers —
`(cell_context and entry_busterm_ids) or drv_spec_depth >= 0` — the same
guard `load_pipeline`'s restore validation and `dump_topologies`' resolver
(Codex #231) already use, so the three sites cannot disagree about which
space a bundle is checked in.
"""
import contextlib
import io

import buda_cli


def _capture(session, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        session.do_command(cmd)
    return buf.getvalue()


def _flat_session(tmp_path, with_bdb):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = []
    if with_bdb:
        cmds.append(f"open_bdb {tmp_path / 'flat.bdb'}")
    cmds += ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
             "add_block A 0 0 100 100", "add_block B 300 0 400 100",
             "add_net n0 A.tx B.rx", "run_bundler strict",
             "generate_topologies", "run_planner 3", "run_nuts")
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_flat_check_design_is_bdb_blind(tmp_path):
    # The audit's verdict must not depend on whether a BDB happens to be
    # open: same design, same route, same (clean) answer.
    without = _capture(_flat_session(tmp_path, with_bdb=False),
                       "check_design nuts")
    with_bdb = _capture(_flat_session(tmp_path, with_bdb=True),
                        "check_design nuts")
    assert "Success: no violations found." in without
    assert "Success: no violations found." in with_bdb, with_bdb
    assert "BUSTERM" not in with_bdb
