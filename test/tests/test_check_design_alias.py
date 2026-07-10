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

"""`check_design` rename: the command audits connectivity, layer direction,
and keepout crossings — far more than its original name said.  `check_design`
is the primary name; `check_connectivity` stays registered as a legacy alias
so existing user scripts keep working (opens.md quick-win item)."""
import io
import contextlib

import buda_cli


def _mini_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.tx B.rx", "run_bundler strict",
                  "generate_topologies", "run_planner 3", "run_nuts"):
            s.do_command(c)
    return s


def _capture(session, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        session.do_command(cmd)
    return buf.getvalue()


def test_check_connectivity_is_an_alias_of_check_design():
    s = _mini_session()
    new = _capture(s, "check_design nuts")
    old = _capture(s, "check_connectivity nuts")
    # Identical behavior and output — the alias maps to the same handler.
    assert new == old
    assert "Verifying NUTS-level design..." in new
    assert "Success: no violations found." in new
    # Both names are registered commands (an unknown command is a hard error
    # and would have printed one).
    assert "Error" not in new and "Error" not in old
