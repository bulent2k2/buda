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

import pytest

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


def _bundled_session():
    sess = _session()
    sess.do_command("add_block A 0 0 100 100")
    sess.do_command("add_block B 200 0 300 100")
    sess.do_command("add_net n1 A.o B.i")
    sess.do_command("run_bundler")
    return sess


def test_generate_rejects_unknown_option(capsys):
    """An unknown option is a hard error (exit non-zero), not a silent skip —
    the token was previously ignored, running with the feature the user thought
    they enabled quietly off."""
    sess = _bundled_session()
    with pytest.raises(SystemExit) as ei:
        sess.do_command("generate_topologies foo")
    assert ei.value.code != 0
    out = capsys.readouterr().out
    assert "unknown option" in out and "'foo'" in out
    assert "Valid options:" in out
    # Names a real option so the user can correct the typo.
    assert "multi_trunk" in out and "no_hanan_loci" in out
    # Nothing was generated — the command aborted before doing work.
    assert not sess.bundles[0].input.candidates


def test_generate_rejects_multiple_unknown_options(capsys):
    sess = _bundled_session()
    with pytest.raises(SystemExit):
        sess.do_command("generate_topologies foo bar")
    out = capsys.readouterr().out
    assert "unknown options" in out          # plural
    assert "'foo'" in out and "'bar'" in out


def test_generate_unknown_option_suggests_close_match(capsys):
    sess = _bundled_session()
    with pytest.raises(SystemExit):
        sess.do_command("generate_topologies multi_trunc")
    out = capsys.readouterr().out
    assert "Did you mean 'multi_trunk'?" in out


def test_generate_accepts_valid_option(capsys):
    """A real flag still works — the guard must not reject valid options."""
    sess = _bundled_session()
    sess.do_command("generate_topologies multi_trunk")   # no raise
    out = capsys.readouterr().out
    assert "unknown option" not in out
    assert sess.bundles[0].input.candidates


def test_unknown_option_rejected_even_before_bundler(capsys):
    """Option validation is state-independent: a malformed command is a hard
    error even when the prerequisite (run_bundler) hasn't run — it must not slip
    past the missing-bundler warning and continue (Codex #466)."""
    sess = _session()
    sess.do_command("add_block A 0 0 100 100")
    sess.do_command("add_block B 200 0 300 100")
    sess.do_command("add_net n1 A.o B.i")
    # NOTE: no run_bundler.
    with pytest.raises(SystemExit) as ei:
        sess.do_command("generate_topologies multi_trunc")
    assert ei.value.code != 0
    out = capsys.readouterr().out
    assert "unknown option" in out               # the hard error, not the warning
    assert "no bundles to generate" not in out   # the prereq warning did NOT win
