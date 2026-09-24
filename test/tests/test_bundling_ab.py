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

"""`tools/bundling_ab.py` -- the bundled-vs-unbundled A/B.

The measurement itself takes minutes (the unbundled arm is the slow one BY
DESIGN), so it is not run here; docs/internal/bundling_ab.md records it with
the command that reproduces it.  What is pinned is the part that decides
whether the two arms differ in ONE thing: where the cap is injected, and the
two refusals that stop the table from claiming "one net per bundle" when it
is not true.  The row reader is pinned against the CLI's real summary lines.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bundling_ab as ab  # noqa: E402


FLOW = """\
# a flow
def_layer 3 M3 H TOP 10
open_bdb :memory:
run_hier_bundler depth 4   # the bundler
generate_hier_topologies
run_planner hier
"""


def test_injects_immediately_before_the_first_bundler():
    out = ab.unbundled_text(FLOW).splitlines()
    i = out.index("run_hier_bundler depth 4   # the bundler")
    assert out[i - 1] == ab.INJECTED
    assert out[i - 2].startswith("# bundling_ab:")
    # Nothing else changes.
    assert [ln for ln in out if not ln.startswith("# bundling_ab")
            and ln != ab.INJECTED] == FLOW.splitlines()


def test_flat_bundler_and_first_of_several():
    text = "add_block a 0 0 1 1\nrun_bundler STRICT\nrun_bundler STRICT\n"
    out = ab.unbundled_text(text).splitlines()
    assert out.count(ab.INJECTED) == 1
    assert out.index(ab.INJECTED) == 2


def test_a_commented_out_bundler_is_not_a_bundler():
    text = "# run_bundler STRICT\nrun_hier_bundler\n"
    out = ab.unbundled_text(text).splitlines()
    assert out.index(ab.INJECTED) == out.index("run_hier_bundler") - 1


def test_crlf_is_kept():
    out = ab.unbundled_text("open_bdb x\r\nrun_bundler STRICT\r\n")
    assert f"{ab.INJECTED}\r\n" in out
    assert "\n" not in out.replace("\r\n", "")


def test_refuses_a_flow_with_no_bundler_of_its_own():
    with pytest.raises(ab.Refused, match="source"):
        ab.unbundled_text("source setup.buda\nrun_planner hier\n")


@pytest.mark.parametrize("cap", ["set_max_bundle_bits 8",
                                 "set_max_bundle_bits 4 for pc_"])
def test_refuses_a_flow_with_its_own_cap(cap):
    with pytest.raises(ab.Refused, match="flow:2"):
        ab.unbundled_text(f"open_bdb x\n{cap}\nrun_hier_bundler\n")


# The CLI's real one-line summaries (flow log armed), as soc_small prints them.
STDOUT = """\
  run_hier_bundler depth 4             0.24s  HierBundler: 323 hbundles (D0: 21, D1: 30, D2: 80, D3: 192)
  generate_hier_topologies             0.50s  generate_hier_topologies: 323 bundles, 2023 total candidates
  report_wirelength                    0.08s  [report_wirelength] total detailed WL = 1968672 over 323 bundle(s) …
"""

REPORT = {
    "total_seconds": 4.0,
    "commands": [
        {"command": "run_hier_bundler depth 4", "seconds": 0.24},
        {"command": "generate_hier_topologies", "seconds": 0.5},
        {"command": "run_planner hier", "seconds": 0.5},
        {"command": "run_planner post_nuts", "seconds": 0.3},
        {"command": "run_nuts", "seconds": 0.25},
        {"command": "run_nuts_on_layer M3", "seconds": 9.0},
        {"command": "run_detailed_nuts", "seconds": 0.43},
        {"command": "ripup_reroute 10", "seconds": 1.0},
    ],
    "audits": [{"stage": "nuts", "violations": 3},
               {"stage": "dnuts", "violations": 40},
               {"stage": "dnuts", "violations": 0}],
}


def test_summarize_reads_the_cli_output():
    row = ab.summarize(REPORT, STDOUT)
    assert row["bundles"] == 323
    assert row["candidates"] == 2023
    assert row["detailed_wl"] == 1968672
    assert row["first_nuts_violations"] == 3
    assert row["first_dnuts_violations"] == 40
    assert row["final_violations"] == 0
    st = row["stages"]
    assert st["planner"] == 0.8                  # post_nuts IS the planner
    assert st["track fitting (bus)"] == 0.25     # run_nuts_on_layer is not
    assert st["repair"] == 1.0


def test_summarize_says_unknown_rather_than_zero():
    row = ab.summarize({}, "")
    assert row["bundles"] is None and row["candidates"] is None
    assert row["detailed_wl"] is None and row["final_violations"] is None
    assert "?" in ab.render("f", {"bundled": row, "unbundled": row})


def test_render_shows_wirelength_as_a_percent():
    row = ab.summarize(REPORT, STDOUT)
    other = dict(row, detailed_wl=1887680)
    table = ab.render("f", {"bundled": row, "unbundled": other})
    assert "| detailed wirelength | 1,968,672 | 1,887,680 | -4.1 % |" in table


# ── Codex on #953 ─────────────────────────────────────────────────────────

def test_refuses_a_cap_declared_in_a_sourced_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "caps.buda").write_text(
        "set_max_bundle_bits 4 for pc_\n")
    (tmp_path / "setup.buda").write_text("source sub/caps\n")  # nested, no suffix
    flow = "source setup.buda\nrun_hier_bundler\n"
    with pytest.raises(ab.Refused, match="caps.buda:1"):
        ab.unbundled_text(flow, base_dir=str(tmp_path))


def test_a_sourced_file_without_a_cap_is_fine(tmp_path):
    (tmp_path / "setup.buda").write_text("def_layer 3 M3 H TOP 10\n")
    out = ab.unbundled_text("source setup.buda\nrun_bundler STRICT\n",
                            base_dir=str(tmp_path))
    assert ab.INJECTED in out


def test_an_unreadable_source_refuses(tmp_path):
    with pytest.raises(ab.Refused, match="cannot be read"):
        ab.unbundled_text("source missing.buda\nrun_bundler STRICT\n",
                          base_dir=str(tmp_path))


# A flat flow's generation, as the FLOW LOG has it: one line per bundle
# (the terminal shows only one of them), and generated twice.
FLAT_LOG = """\
━━━ run_bundler strict ━━━
Bundler created 3 hbundles.
━━━ generate_topologies ━━━
Generated 1 topologies for bundle 1 (a->b) 2 nets
Generated 1 topologies for bundle 2 (a->c) 2 nets
Generated 1 topologies for bundle 3 (b->c) 2 nets
━━━ generate_topologies double_detour ━━━
Generated 7 topologies for bundle 1 (a->b) 2 nets
Generated 5 topologies for bundle 2 (a->c) 2 nets
Generated 4 topologies for bundle 3 (b->c) 2 nets
━━━ run_planner 10 ━━━
Generated 99 topologies for bundle 1 (not a generation command)
"""


def test_flat_candidates_are_summed_from_the_log_last_generation_only():
    stdout = ("  generate_topologies   0.02s  Generated 4 topologies for "
              "bundle 3 (b->c) …\n")
    row = ab.summarize({}, stdout, FLAT_LOG)
    assert row["bundles"] == 3
    assert row["candidates"] == 16


def test_flat_candidates_without_a_log_are_unknown_not_one_bundle():
    stdout = ("  generate_topologies   0.02s  Generated 4 topologies for "
              "bundle 3 (b->c) …\n")
    assert ab.summarize({}, stdout)["candidates"] is None


def _fake_run(report):
    def run(argv, **kw):
        path = Path(argv[argv.index("--report-json") + 1])
        path.write_text(ab.json.dumps(report))
        return ab.subprocess.CompletedProcess(argv, 0, "", "")
    return run


def test_a_run_whose_commands_reported_errors_fails(tmp_path, monkeypatch):
    flow = tmp_path / "f.buda"
    flow.write_text("run_bundler STRICT\n")
    monkeypatch.setattr(ab.subprocess, "run", _fake_run(
        {"exit_status": 0, "commands": [
            {"command": "run_bundler STRICT", "seconds": 0.1, "errors": 0},
            {"command": "run_planner bogus", "seconds": 0.1, "errors": 1}]}))
    with pytest.raises(RuntimeError, match="run_planner bogus"):
        ab.run_arm(flow, flow.read_text(), "bundled")
    assert not list(tmp_path.glob(".bundling_ab_*"))   # variant cleaned up


def test_a_clean_run_returns_its_log(tmp_path, monkeypatch):
    flow = tmp_path / "f.buda"
    flow.write_text("run_bundler STRICT\n")
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / ".bundling_ab_f_bundled_flow.log").write_text("LOG")
    monkeypatch.setattr(ab.subprocess, "run", _fake_run(
        {"exit_status": 0, "commands": [
            {"command": "run_bundler STRICT", "seconds": 0.1, "errors": 0}]}))
    report, stdout, log = ab.run_arm(flow, flow.read_text(), "bundled")
    assert log == "LOG" and report["wall_seconds"] >= 0
