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


def _fake_run(report, log=None):
    def run(argv, **kw):
        if "--report-json" not in argv:      # the judge, on a checkpoint
            return ab.subprocess.CompletedProcess(argv, 2, "", "")
        path = Path(argv[argv.index("--report-json") + 1])
        path.write_text(ab.json.dumps(report))
        if log is not None:
            variant = Path(argv[-1])
            (variant.parent / "log").mkdir(exist_ok=True)
            (variant.parent / "log" /
             f"{variant.stem}_flow.log").write_text(log)
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
    monkeypatch.setattr(ab.subprocess, "run", _fake_run(
        {"exit_status": 0, "commands": [
            {"command": "run_bundler STRICT", "seconds": 0.1, "errors": 0}]},
        log="LOG"))
    report, stdout, log = ab.run_arm(flow, flow.read_text(), "bundled")
    assert log == "LOG" and report["wall_seconds"] >= 0


# ── Codex, second round on #953 ───────────────────────────────────────────

@pytest.mark.parametrize("flow", [
    "SET_MAX_BUNDLE_BITS 4 for pc_\nrun_hier_bundler\n",
    "alias cap set_max_bundle_bits\ncap 4 for pc_\nrun_hier_bundler\n",
    # an alias of an alias is stored resolved, as cmd_alias does
    "alias c1 set_max_bundle_bits\nalias c2 c1\nc2 8\nrun_bundler STRICT\n",
])
def test_a_cap_in_any_spelling_the_engine_runs_refuses(flow):
    with pytest.raises(ab.Refused, match="set_max_bundle_bits"):
        ab.unbundled_text(flow)


def test_an_alias_defined_in_a_sourced_file_is_resolved(tmp_path):
    (tmp_path / "defs.buda").write_text("alias cap set_max_bundle_bits\n")
    flow = "source defs.buda\ncap 4 for pc_\nrun_hier_bundler\n"
    with pytest.raises(ab.Refused, match="flow:2"):
        ab.unbundled_text(flow, base_dir=str(tmp_path))


def test_unalias_is_honoured():
    flow = ("alias cap set_max_bundle_bits\nunalias cap\n"
            "cap 4\nrun_bundler STRICT\n")
    out = ab.unbundled_text(flow)            # `cap` is no command any more
    assert ab.INJECTED in out


def test_an_upper_case_or_aliased_bundler_is_found():
    for flow, bundler in (("open_bdb x\nRUN_HIER_BUNDLER depth 4\n",
                           "RUN_HIER_BUNDLER depth 4"),
                          ("alias hb run_hier_bundler\nhb\n", "hb")):
        out = ab.unbundled_text(flow).splitlines()
        assert out.index(ab.INJECTED) == out.index(bundler) - 1


def test_a_first_bundler_reached_through_source_refuses(tmp_path):
    (tmp_path / "b.buda").write_text("run_bundler STRICT\n")
    flow = "source b.buda\nrun_bundler STRICT\n"
    with pytest.raises(ab.Refused, match="b.buda:1"):
        ab.unbundled_text(flow, base_dir=str(tmp_path))


def test_each_run_owns_its_files(tmp_path, monkeypatch):
    """Fixed names let two runs on one flow truncate and delete each
    other's files, or a stranger's that shared the name."""
    flow = tmp_path / "f.buda"
    flow.write_text("run_bundler STRICT\n")
    bystanders = [tmp_path / ".bundling_ab_f_bundled.buda",
                  tmp_path / ".bundling_ab_f_bundled.json"]
    for b in bystanders:
        b.write_text("NOT OURS")
    seen = []

    def run(argv, **kw):
        seen.append(Path(argv[-1]))
        return _fake_run({"exit_status": 0, "commands": []})(argv, **kw)
    monkeypatch.setattr(ab.subprocess, "run", run)
    ab.run_arm(flow, flow.read_text(), "bundled")
    ab.run_arm(flow, flow.read_text(), "bundled")
    assert seen[0] != seen[1]
    assert all(b.read_text() == "NOT OURS" for b in bystanders)
    assert sorted(p.name for p in tmp_path.iterdir()
                  if p.is_file()) == sorted(
        [b.name for b in bystanders] + ["f.buda"])


# ── Codex, third round on #953 ────────────────────────────────────────────

def test_a_file_sourced_twice_is_walked_twice(tmp_path):
    """The engine runs every `source`; an alias redefined between two runs
    of one file can make only the SECOND declare a cap."""
    (tmp_path / "knobs.buda").write_text("knob 4 for pc_\n")
    flow = ("alias knob report_overhead\n"
            "source knobs.buda\n"
            "alias knob set_max_bundle_bits\n"
            "source knobs.buda\n"
            "run_hier_bundler\n")
    with pytest.raises(ab.Refused, match="knobs.buda:1"):
        ab.unbundled_text(flow, base_dir=str(tmp_path))


def test_a_flow_that_sources_itself_refuses(tmp_path):
    (tmp_path / "a.buda").write_text("source b.buda\n")
    (tmp_path / "b.buda").write_text("source a.buda\n")
    with pytest.raises(ab.Refused, match="already being sourced"):
        ab.unbundled_text("source a.buda\nrun_bundler STRICT\n",
                          base_dir=str(tmp_path))
    top = tmp_path / "top.buda"
    top.write_text("source top.buda\nrun_bundler STRICT\n")
    with pytest.raises(ab.Refused, match="already being sourced"):
        ab.unbundled_text(top.read_text(), base_dir=str(tmp_path),
                          name=str(top))


def test_stage_times_resolve_case_and_aliases():
    """The report records a command as TYPED."""
    report = {"commands": [
        {"command": "alias plan run_planner", "seconds": 0.0},
        {"command": "GENERATE_HIER_TOPOLOGIES", "seconds": 2.0},
        {"command": "plan hier", "seconds": 3.0},
        {"command": "unalias plan", "seconds": 0.0},
        {"command": "Run_NUTS", "seconds": 5.0},
    ]}
    st = ab.summarize(report, "")["stages"]
    assert st["path generation"] == 2.0
    assert st["planner"] == 3.0
    assert st["track fitting (bus)"] == 5.0


def test_an_aliased_generator_is_found_in_the_log():
    log = ("━━━ alias gen generate_topologies ━━━\n"
           "━━━ gen double_detour ━━━\n"
           "Generated 2 topologies for bundle 1 (a->b) 2 nets\n"
           "Generated 3 topologies for bundle 2 (a->c) 2 nets\n"
           "━━━ run_planner 10 ━━━\n")
    assert ab.summarize({}, "", log)["candidates"] == 5


# ── The owner's review on #953: each arm its own files ────────────────────
#
# The arms run one after the other, so every file the flow opens or writes
# was shared: a flow BUILDING its design into a named `open_bdb` target
# died in the second arm on the first arm's rows, and a flow's `save_bdb`
# left its checked-in fixture holding the one-net arm's route.

def test_a_durable_open_is_renamed_into_the_arm(tmp_path):
    text = "open_bdb ckpt.bdb   # the design\nrun_hier_bundler\n"
    iso = ab.isolate(text, str(tmp_path), "f.buda", str(tmp_path / "arm"))
    first = iso.text.splitlines()[0]
    assert first.startswith("open_bdb " + str(tmp_path / "arm" / "0_ckpt.bdb"))
    assert first.endswith("# the design")
    assert iso.copies == []


def test_an_existing_database_is_copied_not_written(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "ckpt.bdb"
    with sqlite3.connect(db) as c:
        c.execute("create table t(x)")
        c.execute("insert into t values (7)")
    before = db.read_bytes()
    flow = tmp_path / "f.buda"
    flow.write_text("open_bdb ckpt.bdb\nrun_bundler STRICT\n")
    arm = tmp_path / "arm"
    arm.mkdir()
    iso = ab.isolate(flow.read_text(), str(tmp_path), str(flow), str(arm))
    monkeypatch.setattr(ab.subprocess, "run", _fake_run(
        {"exit_status": 0, "commands": []}))
    ab.run_arm(flow, flow.read_text(), "bundled", iso=iso)
    assert iso.copies == [(str(db), str(arm / "0_ckpt.bdb"))]
    with sqlite3.connect(iso.copies[0][1]) as c:
        assert c.execute("select x from t").fetchall() == [(7,)]
    assert db.read_bytes() == before


def test_the_judge_snapshot_follows_the_flow_and_precedes_each_exit(tmp_path):
    """Not a redirect of the open: a `:memory:` database moved onto disk
    commits every write to disk, which added ~9 s of setup to soc_small's
    3.7 s bundled arm -- the very column the table compares."""
    arm = tmp_path / "arm"
    text = ("exit 3\nopen_bdb :memory:\nrun_hier_bundler\nexit\n"
            "run_nuts")                      # no final newline
    iso = ab.isolate(text, str(tmp_path), "f.buda", str(arm))
    snap = f"save_bdb {arm / 'checkpoint.bdb'}"
    assert iso.snapshot == snap and iso.checkpoint == str(arm / "checkpoint.bdb")
    assert iso.text.splitlines() == [
        "exit 3",                     # before any open: no snapshot needed
        "open_bdb :memory:", "run_hier_bundler",
        ab.SNAPSHOT_TAG, snap, "exit",
        "run_nuts", ab.SNAPSHOT_TAG, snap]


def test_every_open_is_judged_and_no_open_is_not(tmp_path):
    iso = ab.isolate("open_bdb a.bdb.sql\nopen_bdb :memory:\n"
                     "run_bundler STRICT\n", str(tmp_path), "f.buda",
                     str(tmp_path / "arm"))
    assert iso.checkpoint and iso.no_judge is None
    assert iso.text.startswith("open_bdb a.bdb.sql\nopen_bdb :memory:\n")
    iso = ab.isolate("run_bundler STRICT\n", str(tmp_path), "f.buda",
                     str(tmp_path / "arm"))
    assert iso.checkpoint is None and "no BDB" in iso.no_judge
    assert iso.text == "run_bundler STRICT\n"


def test_writeback_and_a_sourced_durable_open_refuse(tmp_path):
    with pytest.raises(ab.Refused, match="writeback"):
        ab.isolate("open_bdb x.bdb.sql writeback\nrun_bundler STRICT\n",
                   str(tmp_path), "f.buda", str(tmp_path / "arm"))
    (tmp_path / "setup.buda").write_text("open_bdb ckpt.bdb\n")
    with pytest.raises(ab.Refused, match="setup.buda:1 opens the database"):
        ab.isolate("source setup.buda\nrun_bundler STRICT\n",
                   str(tmp_path), "f.buda", str(tmp_path / "arm"))
    (tmp_path / "out.buda").write_text("save_bdb snap.bdb.sql\n")
    with pytest.raises(ab.Refused, match="out.buda:1 `save_bdb` writes"):
        ab.isolate("run_bundler STRICT\nsource out.buda\n",
                   str(tmp_path), "f.buda", str(tmp_path / "arm"))


def test_every_file_the_flow_writes_is_renamed_into_the_arm(tmp_path):
    arm = str(tmp_path / "arm dir")
    text = ("open_bdb :memory:\nrun_hier_bundler\n"
            "save_bdb my snap.bdb.sql\n"
            "emit_guides out/g.json margin 20 csv out/g.csv tcl out/g.tcl\n"
            "EXPORT_GDS out/c.gds outline 10   # upper case runs too\n"
            "derive_top_plan cells a file out/plan.buda\n"
            "save_bdb\n")
    out = ab.isolate(text, str(tmp_path), "f.buda", arm).text.splitlines()
    assert out[2] == f'save_bdb "{arm}/0_my snap.bdb.sql"'
    assert out[3] == (f'emit_guides "{arm}/1_g.json" margin 20 '
                      f'csv "{arm}/2_g.csv" tcl "{arm}/3_g.tcl"')
    assert out[4] == (f'EXPORT_GDS "{arm}/4_c.gds" outline 10   '
                      f'# upper case runs too')
    assert out[5] == f'derive_top_plan cells a file "{arm}/5_plan.buda"'
    assert out[6] == "save_bdb"          # no path: nothing of the user's


def test_an_open_of_a_file_the_arm_wrote_follows_it(tmp_path):
    text = ("open_bdb :memory:\nrun_bundler STRICT\nsave_bdb s.bdb.sql\n"
            "open_bdb s.bdb.sql\n")
    iso = ab.isolate(text, str(tmp_path), "f.buda", str(tmp_path / "arm"))
    out = iso.text.splitlines()
    assert out[3] == f"open_bdb {tmp_path}/arm/0_s.bdb.sql"


def test_a_refusal_comes_before_either_arm_runs(tmp_path, monkeypatch):
    (tmp_path / "setup.buda").write_text("open_bdb ckpt.bdb\n")
    flow = tmp_path / "f.buda"
    flow.write_text("source setup.buda\nrun_bundler STRICT\n")
    monkeypatch.setattr(ab, "WORK_ROOT", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()

    def never(*a, **k):
        raise AssertionError("an arm ran")
    monkeypatch.setattr(ab.subprocess, "run", never)
    assert ab.main([str(flow)]) == 2
    assert list((tmp_path / "work").iterdir()) == []


def test_the_snapshot_is_not_timed_and_no_shell_redirect_leaks(
        tmp_path, monkeypatch):
    flow = tmp_path / "f.buda"
    flow.write_text("open_bdb :memory:\nrun_bundler STRICT\n")
    iso = ab.isolate(flow.read_text(), str(tmp_path), str(flow),
                     str(tmp_path))
    monkeypatch.setenv("BUDA_BDB_MEMORY_TO", "stale")
    seen = {}

    def run(argv, **kw):
        seen.update(kw["env"])
        return _fake_run({"exit_status": 0, "total_seconds": 5.0, "commands": [
            {"command": "run_bundler STRICT", "seconds": 3.0, "errors": 0},
            {"command": iso.snapshot, "seconds": 2.0, "errors": 0}]})(
                argv, **kw)
    monkeypatch.setattr(ab.subprocess, "run", run)
    report, _, _ = ab.run_arm(flow, flow.read_text(), "bundled", iso=iso)
    assert "BUDA_BDB_MEMORY_TO" not in seen
    assert report["total_seconds"] == 3.0
    assert [c["command"] for c in report["commands"]] == ["run_bundler STRICT"]


def test_a_flow_building_into_a_named_database_runs_both_arms(
        tmp_path, monkeypatch):
    """The owner's case, run for real: a self-contained hier flow whose
    `open_bdb` names a durable file.  The second arm used to die on the
    first arm's rows (`add_inst: insert failed (name exists?)`), leaving
    the flow's file holding the bundled route."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "flow" / "ndr_shield_hier.buda").read_text()
    assert "open_bdb :memory:" in text
    flow = tmp_path / "durable.buda"
    flow.write_text(text.replace("open_bdb :memory:", "open_bdb ckpt.bdb"))
    monkeypatch.setattr(ab, "WORK_ROOT", str(tmp_path))
    out = tmp_path / "rows.json"
    assert ab.main([str(flow), "--json", str(out)]) == 0
    rows = ab.json.loads(out.read_text())
    assert rows["bundled"]["bundles"] < rows["unbundled"]["bundles"]
    assert rows["bundled"]["judge"] and rows["unbundled"]["judge"]
    assert not (tmp_path / "ckpt.bdb").exists()


def test_a_named_database_that_is_not_one_fails_the_run(tmp_path):
    (tmp_path / "ckpt.bdb").write_text("not a database")
    flow = tmp_path / "f.buda"
    flow.write_text("open_bdb ckpt.bdb\nrun_bundler STRICT\n")
    arm = tmp_path / "arm"
    arm.mkdir()
    iso = ab.isolate(flow.read_text(), str(tmp_path), str(flow), str(arm))
    with pytest.raises(RuntimeError, match="could not copy"):
        ab.run_arm(flow, flow.read_text(), "bundled", iso=iso)
