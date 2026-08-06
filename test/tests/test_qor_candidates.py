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

"""`qor_corpus.py --candidates` — corpus membership evidence, not corpus edits.

The tool reports full-pipeline flows outside the corpus, ranked by feature
coverage no member exercises, so a decision to add or drop one is made against
its coverage AND its runtime.  The decision itself stays with the owner, which
is the property `test_candidates_never_mutates_the_corpus` pins.

Coverage is measured in FEATURE TOKENS: the command, plus the argument where
the argument selects the path (`run_bundler:combined`).  The tokenizer's job is
mostly to NOT report noise — an iteration count, a bus-name hint, or a viz
command the headless sweep no-ops would each produce a fake 'new feature'.
"""
import io
import contextlib

import pytest

from tools import qor_corpus as qc


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# --------------------------------------------------------------- tokenizing

def test_command_names_become_tokens(tmp_path):
    f = _write(tmp_path, "a.buda", "run_bundler STRICT\ngenerate_topologies\n")
    assert {"run_bundler", "generate_topologies"} <= qc.flow_tokens(f)


def test_path_selecting_argument_is_part_of_the_token(tmp_path):
    """`run_bundler STRICT` and `run_bundler COMBINED` are different coverage."""
    strict = _write(tmp_path, "s.buda", "run_bundler STRICT\n")
    combined = _write(tmp_path, "c.buda", "run_bundler COMBINED\n")
    assert "run_bundler:strict" in qc.flow_tokens(strict)
    assert "run_bundler:combined" in qc.flow_tokens(combined)
    assert "run_bundler:combined" not in qc.flow_tokens(strict)


def test_numeric_argument_is_a_count_not_a_feature(tmp_path):
    """`run_planner 1` vs `run_planner 200` is one feature, not two.  Without
    this the report is dominated by iteration counts."""
    a = _write(tmp_path, "a.buda", "run_planner 1\n")
    b = _write(tmp_path, "b.buda", "run_planner 200\n")
    assert qc.flow_tokens(a) == qc.flow_tokens(b) == {"run_planner"}


def test_mode_argument_still_counts(tmp_path):
    """The numeric guard must not swallow the real modes."""
    f = _write(tmp_path, "a.buda", "run_planner hier 5\nrun_planner post_nuts\n")
    t = qc.flow_tokens(f)
    assert "run_planner:hier" in t and "run_planner:post_nuts" in t


def test_generator_flags_are_features(tmp_path):
    f = _write(tmp_path, "a.buda", "generate_topologies multi_trunk spine_relays\n")
    t = qc.flow_tokens(f)
    assert "generate_topologies:multi_trunk" in t
    assert "generate_topologies:spine_relays" in t


def test_report_command_arguments_are_not_features(tmp_path):
    """`dump_topologies bus_007` names a bus.  Treating it as a feature would
    make every flow look unique — the failure mode that motivated the ignore
    list."""
    f = _write(tmp_path, "a.buda", "dump_topologies bus_007 --problems\n")
    assert not any(t.startswith("dump_topologies") for t in qc.flow_tokens(f))


@pytest.mark.parametrize("cmd", ["visualize", "visualize_topologies",
                                 "report_wl", "check_design", "exit"])
def test_commands_that_cannot_change_routing_are_ignored(cmd, tmp_path):
    """The sweep runs headless and calls check_design itself, so these drive
    nothing the harness would not drive anyway."""
    f = _write(tmp_path, "a.buda", f"{cmd}\n")
    assert qc.flow_tokens(f) == set()


def test_comments_and_blank_lines_are_stripped(tmp_path):
    f = _write(tmp_path, "a.buda", "# run_bundler STRICT\n\n   \nrun_nuts  # trailing\n")
    assert qc.flow_tokens(f) == {"run_nuts"}


# ------------------------------------------------------------------ source

def test_source_is_followed_transitively(tmp_path):
    _write(tmp_path, "leaf.buda", "def_track_pattern 2 0 SIGNAL 1 1\n")
    _write(tmp_path, "mid.buda", "source leaf.buda\nrun_nuts\n")
    top = _write(tmp_path, "top.buda", "source mid.buda\nrun_planner hier\n")
    t = qc.flow_tokens(top)
    assert {"def_track_pattern", "run_nuts", "run_planner:hier"} <= t
    assert "source" not in t, "source is plumbing, not a feature"


def test_source_cycle_terminates(tmp_path):
    """A flow pair that sources each other must not hang the report."""
    _write(tmp_path, "a.buda", "source b.buda\nrun_nuts\n")
    _write(tmp_path, "b.buda", "source a.buda\nrun_bundler STRICT\n")
    t = qc.flow_tokens(str(tmp_path / "a.buda"))
    assert {"run_nuts", "run_bundler"} <= t


def test_missing_source_target_is_not_fatal(tmp_path):
    """Discovery reads flows it never runs; a stale path must not crash it."""
    f = _write(tmp_path, "a.buda", "source nope.buda\nrun_nuts\n")
    assert qc.flow_tokens(f) == {"run_nuts"}


# --------------------------------------------------------------- discovery

def test_only_full_pipeline_flows_are_candidates(tmp_path):
    """Sourced fragments (track fixtures) never reach run_detailed_nuts, which
    is what keeps the report from listing every .buda in the tree."""
    (tmp_path / "flow").mkdir()
    _write(tmp_path, "flow/whole.buda", "run_nuts\nrun_detailed_nuts\n")
    _write(tmp_path, "flow/fragment.buda", "def_track_pattern 2 0 SIGNAL 1 1\n")
    found = qc.discover_candidates(roots=(str(tmp_path / "flow"),), corpus=[])
    names = [f.split("/")[-1] for f in found]
    assert "whole.buda" in names and "fragment.buda" not in names


def test_corpus_members_are_not_candidates():
    """Every real corpus row must be excluded from its own candidate list."""
    found = set(qc.discover_candidates())
    assert not (found & set(qc.CORPUS))


def test_discovery_is_sorted_and_stable():
    assert qc.discover_candidates() == sorted(qc.discover_candidates())


# ------------------------------------------------------------- the contract

def test_candidates_never_mutates_the_corpus():
    """THE contract: the tool supplies evidence, the owner decides membership.
    A report that quietly grew CORPUS would make the benchmark's composition an
    accident of whatever happened to be lying in flow/."""
    before = list(qc.CORPUS)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        qc.cmd_candidates(quantify=False)
    assert qc.CORPUS == before
    assert "ADVISORY" in buf.getvalue()


def test_report_separates_new_coverage_from_the_rest():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rows = qc.cmd_candidates(quantify=False)
    out = buf.getvalue()
    assert "NEW COVERAGE" in out and "NO NEW TOKENS" in out
    assert any(r["new_tokens"] for r in rows), "expected some uncovered feature"
    # The no-new-tokens list must not be presented as proof of redundancy.
    assert "Not proof of redundancy" in out


def test_corpus_coverage_is_the_union_of_its_flows():
    cov = qc.corpus_coverage()
    assert "run_detailed_nuts" in cov and "run_nuts" in cov
    # A token from exactly one member still counts as covered.
    assert "reserve_top_layers" in cov or "set_cell_layer_cap" in cov
