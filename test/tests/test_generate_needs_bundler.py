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

"""generate_topologies needs bundles (from run_bundler / run_hier_bundler).

If a script defines nets but forgets to bundle them, generate_topologies used to
do nothing silently — the "no bundle with candidates" symptom only surfaced
later (e.g. at visualize_topologies). It now reminds the user to run a bundler.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _session():
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command("def_layer 4 M4 H TOP 1.0")
    sess.do_command("def_layer 5 M5 V TOP 1.0")
    return sess


def test_generate_without_bundler_reminds_to_bundle(capsys):
    sess = _session()
    sess.do_command("add_block A 0 0 100 100")
    sess.do_command("add_block B 200 0 300 100")
    sess.do_command("add_net n1 A.o B.i")
    sess.do_command("generate_topologies")          # forgot run_bundler
    out = capsys.readouterr().out
    assert "run_bundler" in out
    assert "run_hier_bundler" in out               # the hierarchy alternative
    assert not sess.bundles                          # nothing generated


def test_generate_with_no_nets_reminds_to_add_nets(capsys):
    sess = _session()
    sess.do_command("generate_topologies")          # no nets at all
    out = capsys.readouterr().out
    assert "add_net" in out and "run_bundler" in out


def test_generate_after_bundler_works(capsys):
    sess = _session()
    sess.do_command("add_block A 0 0 100 100")
    sess.do_command("add_block B 200 0 300 100")
    sess.do_command("add_net n1 A.o B.i")
    sess.do_command("run_bundler")
    sess.do_command("generate_topologies")
    out = capsys.readouterr().out
    assert "Warning" not in out
    assert sess.bundles and len(sess.bundles[0].input.candidates) > 0
