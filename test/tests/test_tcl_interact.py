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

"""`btcl -i <flow>.buda` — interactive iteration on ANY .buda flow.

design.tcl/hdesign.tcl carry their own design and route recipe; the
interact driver (tools/buda_interact.tcl) carries neither.  It runs an
arbitrary flow verbatim via the engine's `source`, learns what the flow DID
from the recorder (BUDA_RECORD at do_command — loops unrolled, source trees
flattened), and derives from that: hier vs flat, the flow's own routing
tail as the prompt's `replan`, whether it routed at all, and where its
checkpoint lives.  Then the shared prompt (tools/buda_prompt.tcl) takes
over — the same loop the vehicles use, so a pin means the same thing in
all three drivers.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DRIVER = _ROOT / "tools" / "buda_interact.tcl"
_BTCL = _ROOT / "bin" / "btcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]

# flow/tcl/design.tcl's design as a plain .buda — proven clean, with enough
# candidates per bundle for a pin to be a real choice.
_FLAT_FLOW = """\
def_layer 2 M2 H 55.56
def_layer 3 M3 V 55.56
def_layer 4 M4 H 55.56
def_layer 5 M5 V TOP 50
def_layer 6 M6 H TOP 52.94
def_layer 7 M7 V TOP 56.10
def_track_pattern 2 -100 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 3 0 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 4 -200 POWER 2 1 (SIGNAL 1 0.5)x4 GROUND 2 1 (SIGNAL 1 0.5)x4
def_track_pattern 5 0 POWER 3 1 (SIGNAL 2 1)x4 GROUND 3 1 (SIGNAL 2 1)x4
def_track_pattern 6 -400 POWER 4 1 (SIGNAL 2 1)x4 GROUND 4 1 (SIGNAL 2 1)x4
def_track_pattern 7 -600 POWER 6 2 (SIGNAL 3 2)x3 GROUND 2 1 (SIGNAL 3 2)x3
corner_margin dx 5 dy 5
set_min_stub_length 2
set_planner_param healersAhead 1
add_block cpu 50 50 250 250
add_block mem0 550 50 750 250
add_block mem1 550 350 750 550
add_block dsp 50 350 250 550
add_block io 330 20 470 120
add_block noc 330 480 470 580
add_bus d0[8] cpu.d0 mem0.d0
add_bus d1[8] cpu.d1 mem1.d1
add_bus w[4] dsp.w mem1.w
add_bus io_in[4] io.i cpu.i
add_bus io_out[4] cpu.o io.o
add_bus n0[4] noc.a dsp.a
add_bus n1[4] noc.b mem1.b
add_net irq io.irq dsp.irq
run_bundler STRICT
generate_topologies
RUN_PLANNER 5
RUN_NUTS
check_design nuts
run_detailed_nuts
check_design dnuts
"""
# RUN_PLANNER / RUN_NUTS are deliberately uppercase: the engine lowercases the
# command NAME but the recorder writes the line verbatim, so the driver's
# hier/routed/tail detection must case-fold the verb too — spelled that way
# here, the flat test only passes if it does.


def _run(cmd, tmp_path, stdin="done\n"):
    import os
    return subprocess.run([*map(str, cmd)], input=stdin, capture_output=True,
                          encoding="utf-8", errors="replace", cwd=tmp_path,
                          timeout=900, env={**os.environ})


def _log(flow):
    """The flow's per-command detail, where the driver now files it.

    A flow run through this driver is summarized exactly as `bin/buda`
    summarizes it — one line per command on the terminal, the full detail in
    `<flow_dir>/log/<stem>_flow.log` — so a claim about what a COMMAND printed
    (`topo 4 of 8 … [pinned]`, a restored-pin note) is a claim about the log.
    The terminal carries the abstract, and the assertions that read `r.stdout`
    below are the ones about the DRIVER's own messages.
    """
    p = Path(flow).parent / "log" / (Path(flow).stem + "_flow.log")
    return p.read_text(errors="replace") if p.exists() else ""


def test_flat_flow_pin_replan_verdict(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout
    assert "`replan` replays the flow's own routing tail" in r.stdout
    assert "no file-backed BDB" in r.stdout        # no open_bdb in the flow
    assert "pins changed since the last route" in r.stdout
    assert "topo 4 of" in _log(flow)               # the replay honored the pin
    # The replay is the FLOW's tail: its checks and detailed stage re-ran.
    assert "replay> run_detailed_nuts" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_hier_flow_is_autodetected_and_replans_hier(tmp_path):
    # The unmodified hbundles cross-level case: 3 levels, cell-local template
    # + cross-level bundles, :memory: BDB, ends in `visualize` (headless note).
    #
    # COPIED into tmp_path (with the `../tracks` fixture its one `source`
    # needs, so the relative path still resolves) rather than run in place:
    # the flow log is derived from the flow's own directory, and
    # test_flow_scripts.py runs this same flow through `bin/buda`, writing the
    # same file.  Two writers were harmless while nobody read it; now that the
    # engine detail this test asserts on lives there, running in place would
    # be a race under `bb -p`.
    flow = tmp_path / "hbundles" / "08_cross_level.buda"
    flow.parent.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "tracks")
    shutil.copy(_ROOT / "flow" / "hbundles" / "08_cross_level.buda", flow)
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "HIER flow" in r.stdout
    assert "starting `run_planner hier 5`" in r.stdout
    # The fan-out note is the TYPED `pin`'s own output (terminal); the planner
    # lines proving the replay honored it are the flow's, hence the log.
    assert "expanded instances" in r.stdout        # the template pin fanned out
    assert _log(flow).count("topo 2 of 5") >= 4, "the replay lost the pin"


def test_a_failed_replay_fails_the_run(tmp_path):
    # A command that succeeds once and hard-errors on repetition — a duplicate
    # `def_layer` id — planted AFTER the planner puts a deterministic failure
    # inside the routing tail: the original run declares layer 8 once (fine),
    # every replay re-declares it and stops there.  A replay that stops
    # partway leaves the session half-mutated, so the driver must exit
    # non-zero rather than read a verdict off that state.
    flow = tmp_path / "sour.buda"
    flow.write_text(_FLAT_FLOW.replace(
        "RUN_NUTS\n", "RUN_NUTS\ndef_layer 8 M8 H 30\n"))

    # Exit-path replan (pin made pins dirty; `done` triggers the replay).
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "replay stopped at `def_layer 8 M8 H 30`" in r.stdout + r.stderr
    assert "FAILED" in r.stderr

    # Mid-session `replan` with CLEAN pins: the prompt survives the failure
    # (the error is printed, the loop continues to `done`), no exit replan
    # runs — and the sticky flag still fails the run, because the last route
    # attempt never finished.
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="replan\ndone\n")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "replay stopped at `def_layer 8 M8 H 30`" in r.stdout
    assert "stopped partway" in r.stderr


def test_a_flow_that_never_planned_has_no_replan_and_no_verdict(tmp_path):
    flow = tmp_path / "partial.buda"
    flow.write_text("def_layer 4 M4 H TOP 30\n"
                    "add_block A 0 0 100 100\n")
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="replan\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "never ran the planner" in r.stdout
    assert "no replan recipe" in r.stdout          # the verb says so, politely
    assert "did not route" in r.stdout and "no verdict" in r.stdout


def test_pins_survive_back_to_back_sessions(tmp_path):
    # The durable-pin round trip on a flow that opens its own BDB: a pin made
    # at the prompt writes topology.is_pinned through at once; the next
    # session RERUNS the flow (a rebuild, not a load_pipeline resume), and
    # the generation tail re-attaches the pin onto the regenerated pool by
    # content uid (_apply_bdb_pins) — before the re-persist that used to
    # wipe it — so the flow's own run_planner honors it.  Unpin is durable
    # the same way: cleared in session 3, gone in session 4.
    flow = tmp_path / "ckpt.buda"
    flow.write_text("open_bdb ckpt.bdb\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checkpoint" in r.stdout and "pins persist" in r.stdout

    r = _run(["tclsh", _DRIVER, flow], tmp_path)          # just `done`
    assert r.returncode == 0, r.stdout + r.stderr
    log = _log(flow)
    assert "durable pin restored -> topo 4" in log
    assert "topo 4 of" in log and "[pinned]" in log
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # `unpin` is TYPED at the prompt, so its output stays on the terminal:
    # the driver summarizes the flow it runs, never what the user asks for.
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="unpin d1\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Unpinned" in r.stdout

    r = _run(["tclsh", _DRIVER, flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "durable pin restored" not in _log(flow)        # honestly unpinned


def test_in_session_rebundle_and_per_bundle_generation_keep_pins(tmp_path):
    # The two Codex #758 P1s in one round trip.  Session B pins a SECOND
    # bundle at the prompt, then re-bundles raw — fresh unpinned wrappers,
    # so the memo mirror (refreshed by the pin's own persist) is the only
    # surviving copy — and regenerates ONE bundle through the per-bundle
    # additive path, whose persist rewrites the whole table.  At that
    # persist w's pool is still EMPTY: it must ride the mirror's
    # carry-forward (not be warn-dropped), so the bulk generation that
    # follows restores it onto the regenerated pool.  Session C then sees
    # BOTH pins: d1's from session A, w's from session B.
    flow = tmp_path / "ckpt.buda"
    flow.write_text("open_bdb ckpt.bdb\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run(["tclsh", _DRIVER, flow], tmp_path,
             stdin="pin w 2\nrun_bundler STRICT\n"
                   "generate_more_topologies d1\n"
                   "generate_topologies\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    # The re-bundle and the per-bundle regeneration are TYPED here, so their
    # own output is on the terminal; the restore notes belong to the flow's
    # `generate_topologies`, which the driver summarized into the log.
    both = r.stdout + _log(flow)
    assert "durable pin restored -> topo 4" in both        # d1 held throughout
    assert "durable pin restored -> topo 2" in both        # w, via carry-forward
    assert "matches no regenerated candidate" not in both

    r = _run(["tclsh", _DRIVER, flow], tmp_path)           # just `done`
    assert r.returncode == 0, r.stdout + r.stderr
    log = _log(flow)
    assert "durable pin restored -> topo 4" in log
    assert "durable pin restored -> topo 2" in log
    assert log.count("[pinned]") >= 2, "a pin fell out of the planner"


def test_armed_bdb_gives_a_flat_flow_durable_pins(tmp_path):
    # A flow that never opens a BDB has no durable home for a pin — the
    # optional second argument arms one BEFORE the flow runs (the design.tcl
    # pattern without editing the flow), so the whole pipeline persists as
    # it goes and the next session restores the pin onto the rebuilt pool.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)

    r = _run(["bash", _BTCL, "-i", flow, tmp_path / "armed.bdb"], tmp_path,
             stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checkpoint" in r.stdout and "no file-backed BDB" not in r.stdout

    r = _run(["bash", _BTCL, "-i", flow, tmp_path / "armed.bdb"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    log = _log(flow)
    assert "durable pin restored -> topo 4" in log
    assert "topo 4 of" in log and "[pinned]" in log


def test_stage_resume_skips_the_rebuild_and_keeps_pins(tmp_path):
    # The optional third argument is the design.tcl RESUME recipe
    # generalized: a build session writes its recorded trace beside the
    # checkpoint; `btcl -i flow ckpt.bdb plan` then replays only the SETUP
    # portion, calls load_pipeline (which restores bundles, candidates, the
    # plan and the PINS — the machinery built for this), and re-enters at
    # the planner.  No bundler, no generator, and the pin needs no restore
    # hack: load_pipeline's own path carries it.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    bdb = tmp_path / "r.bdb"

    r = _run(["tclsh", _DRIVER, flow, bdb], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resume trace" in r.stdout                  # the build wrote it
    assert (tmp_path / "r.bdb.trace").exists()

    r = _run(["tclsh", _DRIVER, flow, bdb, "plan"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMING at `plan`" in r.stdout
    assert "RESUMED 8 bundles" in r.stdout
    assert "replay> run_bundler" not in r.stdout       # the point of resume
    assert "replay> generate_topologies" not in r.stdout
    log = _log(flow)
    assert "topo 4 of" in log and "[pinned]" in log
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # `nuts` keeps the plan too: the planner is not re-run either.
    r = _run(["tclsh", _DRIVER, flow, bdb, "nuts"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replay> RUN_PLANNER" not in r.stdout       # trace spells it so
    assert "replay> run_detailed_nuts" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # A stage without a build first is refused with the remedy.
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "fresh.bdb", "plan"],
             tmp_path)
    assert r.returncode == 2
    assert "run a build session first" in r.stderr


def test_hier_stage_resume_holds_construction_and_replans(tmp_path):
    # The hdesign.tcl resume rule, generalized: a hier flow's setup mixes
    # session projections (stack, patterns — replayed) with BDB
    # construction (cells, instances, buses, busterms — IN the checkpoint;
    # a replayed add_inst is a duplicate-instance error), so the resume
    # replays the whitelist and HOLDS the construction, then load_pipeline
    # restores the templates and `run_planner hier` re-expands — the pinned
    # template fanning back out to every instance.
    src = (_ROOT / "flow" / "hbundles" / "08_cross_level.buda").read_text()
    src = src.replace("open_bdb :memory:", "open_bdb h.bdb")
    src = src.replace("source ../tracks/tracks.buda",
                      f"source {_ROOT / 'flow' / 'tracks' / 'tracks.buda'}")
    flow = tmp_path / "h.buda"
    flow.write_text(src)

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resume trace" in r.stdout

    r = _run(["tclsh", _DRIVER, flow, tmp_path / "h.bdb", "plan"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "HIER flow" in r.stdout
    assert "held by the checkpoint" in r.stdout
    assert "replay> add_inst" not in r.stdout          # construction held
    assert "replay> derive_busterms" not in r.stdout
    assert _log(flow).count("topo 2 of 5") >= 4, "the template pin fell out"
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # A hier resume BELOW the planner is an INSPECTION session — the quick
    # look at a routed checkpoint: `load_pipeline expanded` restores the
    # post-expansion view, the stage replays (dnuts: detailed NUTS + its
    # check), and the verdict comes out — while pins, their raw-command
    # bypass, and replan are guarded, because their persist would write the
    # expanded view over the checkpoint's template rows.
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "h.bdb", "dnuts"], tmp_path,
             stdin="pin b_lohi 3\nselect_topology b_lohi 3\nreplan\n"
                   "run_planner hier 5\nrun_hier_bundler depth 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INSPECTION session" in r.stdout
    assert "replay> run_detailed_nuts" in r.stdout
    # pin verb + raw select_topology + raw run_planner + raw re-bundle: the
    # guard sees every raw engine command, so the planner/bundler bypass
    # (Codex #763) is blocked the same way the pin family is.
    assert r.stdout.count("disabled in this INSPECTION") == 4
    assert "resume at `plan` to re-plan" in r.stdout           # replan refusal
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # The proof the guard matters: after the inspection session (with its
    # attempted pins), a `plan` resume still restores the ORIGINAL template
    # pin onto every instance — the checkpoint was not clobbered.
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "h.bdb", "plan"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _log(flow).count("topo 2 of 5") >= 4, "the inspection session dirtied the checkpoint"
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_below_plan_resume_holds_healers_the_plan_already_carries(tmp_path):
    # load_pipeline restores the PLAN, not the planner object, so the
    # healers would refuse outright on a below-plan resume — and holding
    # them is also the honest fast path: a healer commit is a full-pipeline
    # state, so the restored plan already carries the healing and re-solving
    # NUTS/DNUTS from it reproduces the healed endpoint without paying for
    # the healers again (the quick-inspection case for long healer flows).
    flow = tmp_path / "healer.buda"
    flow.write_text("open_bdb heal.bdb\n" + _FLAT_FLOW.replace(
        "check_design nuts\n",
        "check_design nuts\nripup_reroute 5\nnegotiate_congestion 3\n"))

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run(["tclsh", _DRIVER, flow, tmp_path / "heal.bdb", "nuts"],
             tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "holding 2 planner-dependent command(s)" in r.stdout
    assert "ripup_reroute" in r.stdout and "negotiate_congestion" in r.stdout
    assert "replay> ripup_reroute" not in r.stdout
    assert "replay> negotiate_congestion" not in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_relative_checkpoint_paths_resolve_against_the_flow(tmp_path):
    # The engine resolves a relative `open_bdb` against the FLOW's directory
    # (the script-dir rule), and the recorder writes the token verbatim —
    # so the driver must resolve it the same way: the trace lands BESIDE
    # the checkpoint the engine actually wrote (not in the invocation CWD),
    # and the resume's raw replay reopens the same file.
    sub = tmp_path / "sub"
    sub.mkdir()
    flow = sub / "mini.buda"
    flow.write_text("open_bdb ckpt.bdb\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (sub / "ckpt.bdb").exists()             # the engine's rule
    assert (sub / "ckpt.bdb.trace").exists()       # the trace beside it
    assert not (tmp_path / "ckpt.bdb.trace").exists()

    r = _run(["tclsh", _DRIVER, flow, sub / "ckpt.bdb", "plan"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMED 8 bundles" in r.stdout
    log = _log(flow)
    assert "topo 4 of" in log and "[pinned]" in log
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_sql_fixture_without_writeback_is_not_a_checkpoint(tmp_path):
    # `open_bdb x.bdb.sql` without `writeback` materializes a THROWAWAY
    # binary — nothing flushes back to the fixture — so the driver must not
    # advertise it as a checkpoint, must not write a resume trace for it,
    # and a stage resume against it is refused.
    seed = tmp_path / "seed.buda"
    seed.write_text("open_bdb seed.bdb\n" + _FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, seed], tmp_path,
             stdin="save_bdb fix.bdb.sql\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "fix.bdb.sql").exists()

    flow = tmp_path / "sqlflow.buda"
    flow.write_text("open_bdb fix.bdb.sql\n" + _FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not a durable checkpoint" in r.stdout
    assert "writeback" in r.stdout                 # the reason is named
    assert not (tmp_path / "fix.bdb.sql.trace").exists()

    r = _run(["tclsh", _DRIVER, flow, tmp_path / "fix.bdb.sql", "plan"],
             tmp_path)
    assert r.returncode == 2                       # no trace, refused


def test_an_exit_ending_flow_with_a_durable_checkpoint_is_resumable(tmp_path):
    # A flow that ends in `exit` has ended the ENGINE session, so there is no
    # live pipeline to drive at a prompt.  But if its checkpoint is durable
    # the routed rows are on disk, so a `<stage>` resume should still work —
    # which needs the build session to have written the trace.  It used to
    # return at the exit check BEFORE the trace was written, so the trace was
    # never written and every later resume refused (the report that cost a
    # 2000s heal).  The trace write now runs on the exit path too.
    flow = tmp_path / "exitflow.buda"
    flow.write_text("open_bdb dur.bdb\n" + _FLAT_FLOW + "exit\n")

    r = _run(["tclsh", _DRIVER, flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resume trace" in r.stdout                       # written despite exit
    assert (tmp_path / "dur.bdb.trace").exists()
    assert "ended in `exit`" in r.stdout
    assert "DISCARDED" not in r.stdout                      # it was NOT discarded

    # The resume the build advertised actually runs.
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "dur.bdb", "dnuts"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run a build session first" not in r.stderr
    assert "RESUMING at `dnuts`" in r.stdout


def test_an_exit_ending_flow_without_a_durable_checkpoint_warns_loudly(tmp_path):
    # The shape that cost the 2000s heal: a flow opens its OWN `.sql` without
    # `writeback` (a throwaway materialized copy) and ends in `exit`.  The
    # routing it did is discarded — and the user must hear THAT at the end of
    # the build, not at a refused resume a long route later.  The armed BDB is
    # replaced by the flow's own open, so that is named too.
    flow = tmp_path / "nondur.buda"
    flow.write_text("open_bdb work.bdb.sql\n" + _FLAT_FLOW + "exit\n")
    (tmp_path / "work.bdb.sql").write_text("")

    r = _run(["tclsh", _DRIVER, flow, tmp_path / "armed.bdb"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING" in r.stdout and "DISCARDED" in r.stdout
    assert "writeback" in r.stdout                          # the cause is named
    assert "replacing it" in r.stdout                       # the armed BDB note
    assert not (tmp_path / "armed.bdb.trace").exists()
    assert not (tmp_path / "work.bdb.sql.trace").exists()

    # The resume refusal names the real cause, not the command that just ran.
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "armed.bdb", "dnuts"], tmp_path)
    assert r.returncode == 2
    assert "exists but" in r.stderr and "non-durable" in r.stderr
    assert "writeback" in r.stderr
    # The old message advised `btcl -i flow armed.bdb` — the build that just
    # produced this state.  It must not, now.
    assert "run a build session first" not in r.stderr


def test_a_durable_checkpoint_whose_trace_cannot_be_written_does_not_promise_resume(tmp_path):
    # The resume claim is gated on the trace ACTUALLY being on disk, not just
    # on the checkpoint being durable (Codex #789 P2).  Block the trace path
    # with a directory of its name so the write fails, and check that neither
    # the exit path nor the prompt path advertises a `<stage>` resume that
    # would immediately refuse.
    exitflow = tmp_path / "tfexit.buda"
    exitflow.write_text("open_bdb tf.bdb\n" + _FLAT_FLOW + "exit\n")
    (tmp_path / "tf.bdb.trace").mkdir()                 # block the write

    r = _run(["tclsh", _DRIVER, exitflow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not write the resume trace" in r.stdout
    assert "resume works" not in r.stdout              # the gated claim
    assert "unavailable until that path can be" in r.stdout

    # The prompt path (no `exit`) is gated the same way: pins still persist
    # across a rerun, but no `<stage>` resume is offered.
    promptflow = tmp_path / "pfprompt.buda"
    promptflow.write_text("open_bdb pf.bdb\n" + _FLAT_FLOW)
    (tmp_path / "pf.bdb.trace").mkdir()
    r = _run(["tclsh", _DRIVER, promptflow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not write the resume trace" in r.stdout
    assert "or resume:" not in r.stdout
    assert "pins persist across a rerun" in r.stdout


def test_a_missing_checkpoint_still_says_run_a_build_first(tmp_path):
    # The other half of the split: when the BDB itself is absent (not just its
    # trace), "run a build session first" is exactly right and must stay.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow, tmp_path / "ghost.bdb", "plan"], tmp_path)
    assert r.returncode == 2
    assert "which\n              does not exist" in r.stderr \
        or "does not exist" in r.stderr
    assert "run a build session first" in r.stderr


def test_concurrent_sessions_fail_loudly_and_do_not_corrupt_the_bdb(tmp_path):
    # SQLite allows one writer, and that is the protection: two sessions on
    # the SAME armed BDB must never corrupt it — the unlucky one fails
    # LOUDLY (at the arming open, or mid-flow as "the flow failed") while
    # the file stays consistent and a follow-up session runs clean.  Which
    # session loses (or whether the writes happen to serialize) is timing;
    # what is asserted is the invariant: no silent failure, no corruption.
    import sqlite3
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    bdb = tmp_path / "conc.bdb"

    import os
    procs = [subprocess.Popen(
                 ["tclsh", str(_DRIVER), str(flow), str(bdb)],
                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, encoding="utf-8",
                 errors="replace", cwd=tmp_path, env={**os.environ})
             for _ in range(2)]
    outs = [p.communicate(input=f"pin d1 {n}\ndone\n", timeout=900)[0]
            for n, p in zip((4, 3), procs)]
    rcs = [p.returncode for p in procs]

    for rc, out in zip(rcs, outs):
        if rc == 0:
            assert "done -- 0 overlaps" in out, out
        else:
            assert ("cannot open the armed BDB" in out
                    or "the flow failed" in out
                    or "FAILED" in out), f"a silent failure (rc {rc}): {out}"

    c = sqlite3.connect(bdb)
    assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    c.close()

    r = _run(["tclsh", _DRIVER, flow, bdb], tmp_path)     # follow-up session
    assert r.returncode == 0, r.stdout + r.stderr
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_btcl_dash_i_wraps_the_driver_and_refuses_tcl(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, "-i", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout

    tclflow = tmp_path / "flow.tcl"
    tclflow.write_text("puts hi\n")
    r = _run(["bash", _BTCL, "-i", tclflow], tmp_path)
    assert r.returncode == 2
    assert "prompt.tcl" in r.stderr           # the message names the path


def test_a_buda_flow_without_dash_i_says_which_two_ways_run_it(tmp_path):
    # The mirror of the case above, and the one tclsh cannot report: it reads
    # the .buda as TCL, so the complaint is about whatever the flow's first
    # line happens to be — `invalid command name "add_block"`, or, for a flow
    # opening with `source`, Tcl's OWN source failing on a path resolved
    # against a different root (measured on flow/big_data_test/bigHalf.buda:
    # `couldn't read file "../tracks/tracks4top.buda"`).  Neither names the
    # mistake, and both arrive after part of the flow has already run.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, flow], tmp_path)
    assert r.returncode == 2
    assert "is a BUDA flow" in r.stderr
    assert "btcl -i" in r.stderr and "buda " in r.stderr   # both remedies
    assert "invalid command name" not in r.stderr          # tclsh never saw it

    # A .tcl operand is untouched — the wrapper refuses ONE named mistake, it
    # does not audit what a Tcl script may be called.
    tclflow = tmp_path / "hi.tcl"
    tclflow.write_text("puts hi\n")
    r = _run(["bash", _BTCL, tclflow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "hi" in r.stdout


def test_a_flow_run_is_summarized_like_the_cli_and_filed_in_the_log(tmp_path):
    # The parity this driver exists to have: `bin/buda` prints ONE line per
    # command and files the detail; run through Tcl the same flow printed
    # every line of every command (bigHalf: 677 lines against the CLI's 51)
    # and left no log at all, because the summarizer is gated on a flow log
    # being open and only the CLI ever opened one.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, "-i", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    # One line per command, in the CLI's own shape (`  <cmd>  <secs>s  <head>`).
    assert re.search(r"^  run_detailed_nuts +\d+\.\d\ds  ", r.stdout, re.M), \
        r.stdout
    assert "═══════ Runtime summary (mini.buda)" in r.stdout
    assert "Full per-command detail →" in r.stdout

    # The detail is not lost — it moved to the file the CLI writes, at the
    # path the CLI derives, and the console no longer carries it.
    log = _log(flow)
    assert (tmp_path / "log" / "mini_flow.log").exists()
    assert "[DetailedNUTS] 46 net segments placed" in log
    # The planner's per-bundle lines are the bulk of a real flow's output and
    # none of them is a headline (`RUN_PLANNER 5` summarizes to a line count),
    # so they are the honest test of what left the console.
    assert "[Planner] Bundle" in log
    assert "[Planner] Bundle" not in r.stdout

    # And the whole point: the terminal is now the CLI's order of magnitude,
    # not the engine's.  (mini is small; bigHalf measured 677 → 58.)
    assert len(r.stdout.splitlines()) < len(log.splitlines())


def test_the_prompt_shows_what_you_type_in_full(tmp_path):
    # The boundary of the summarizing: a command the USER typed is one whose
    # output they asked to read.  `topos` exists to print a candidate table,
    # and summarizing it to "(12 lines)" would answer the question with a
    # line count — so the driver arms the log around the FLOW and its replays
    # and drops it while the prompt waits.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, "-i", flow], tmp_path,
             stdin="topos d1\nreplan\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr

    after = r.stdout.split("pin/edit prompt")[1]
    assert "topo type" in after and "*SEL" in after       # the table, in full
    # …while the replan — the flow's OWN routing tail — is summarized.
    assert re.search(r"^  RUN_NUTS +\d+\.\d\ds  ", after, re.M), after

    # One session is one log: the replan APPENDS rather than rotating the
    # flow's own detail away (two solves, two sections).
    assert _log(flow).count("━━━ RUN_NUTS ━━━") == 2


# ── the -b / -r / -s spellings (btcl forwards them; the driver owns them) ──


def test_build_flag_arms_auto_checkpoint_and_stamps_trace(tmp_path):
    # -b takes the checkpoint filename off the user's plate: the flow opens
    # no BDB, so -b arms <flow_dir>/<stem>.ckpt.bdb before the flow and the
    # trace lands beside it, stamped with the flow text's crc32 so a later
    # resume can notice an edit.  Through the WRAPPER, so the forwarding of
    # the flag to the driver is what this test measures first.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["bash", _BTCL, "-b", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-b arming checkpoint" in r.stdout
    ckpt = tmp_path / "mini.ckpt.bdb"
    assert ckpt.exists()
    trace = tmp_path / "mini.ckpt.bdb.trace"
    assert trace.exists()
    assert re.search(r"^# flow_crc32: \d+$", trace.read_text(), re.M)
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # A rebuild re-arms the SAME checkpoint (pins persisted there re-attach
    # to the rebuilt pool — the _apply_bdb_pins path), and says so.
    r = _run(["bash", _BTCL, "-b", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "re-arming the existing checkpoint" in r.stdout


def test_resume_flag_defaults_to_deepest_stage(tmp_path):
    # -r with no -s: the checkpoint is discovered at the auto name and the
    # stage defaults to the DEEPEST the trace records — dnuts here, since
    # the flow ran run_detailed_nuts — so everything above it restores and
    # only the last leg re-runs.  -s alone implies -r and overrides.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resuming at the deepest recorded stage `dnuts`" in r.stdout
    assert "RESUMING at `dnuts`" in r.stdout
    assert "replay> RUN_PLANNER" not in r.stdout       # restored, not re-run
    assert "replay> run_detailed_nuts" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    r = _run(["bash", _BTCL, "-s", "plan", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMING at `plan`" in r.stdout
    assert "replay> RUN_PLANNER 5" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_build_flag_refuses_a_nondurable_checkpoint_before_running(tmp_path):
    # The non-durable shapes -b still refuses at t=0 — before an engine is
    # spawned — naming the file and line of the open.  (The single-open
    # read-only `.sql` shape is no longer among them: that one REDIRECTS —
    # see test_build_redirects_a_readonly_sql_input.)  Here: a `.sql` input
    # that does not exist (the build could only fail at the open, a route
    # later), a MULTI-open flow ending non-durable (redirect could land on
    # the wrong open, so those keep the refusal), and `:memory:`.
    sub = tmp_path / "ck_open.buda"
    sub.write_text("# checkpoint half\nopen_bdb design.bdb.sql\n")
    flow = tmp_path / "mini.buda"
    flow.write_text("source ck_open.buda\n" + _FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2
    assert "-b refused before running" in r.stderr
    assert "does not exist" in r.stderr                # the missing input
    assert "ck_open.buda line 2" in r.stderr           # the open, located
    assert "Bundler" not in r.stdout                   # nothing was spent
    assert not (tmp_path / "mini.ckpt.bdb").exists()

    # Several opens ending non-durable: the classic refusal, now also
    # naming the redirect boundary.
    (tmp_path / "scratch.bdb.sql").write_text("")
    flow.write_text("open_bdb good.bdb\n" + _FLAT_FLOW
                    + "open_bdb scratch.bdb.sql\n")
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2
    assert "DISCARDED" in r.stderr
    assert "writeback" in r.stderr                     # the remedy
    assert "ONLY open_bdb" in r.stderr                 # the redirect boundary

    # `:memory:` is the other non-durable spelling.
    flow.write_text(_FLAT_FLOW + "open_bdb :memory:\n")
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2
    assert "`:memory:` dies with the process" in r.stderr


def test_build_flag_defers_to_the_flows_own_durable_checkpoint(tmp_path):
    # A flow that already opens a durable BDB owns its checkpoint: arming
    # another would just be replaced by the flow's own open, so -b arms
    # nothing, says so, and the trace lands beside the flow's checkpoint.
    flow = tmp_path / "mini.buda"
    flow.write_text("open_bdb own.bdb\n" + _FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "opens its own durable checkpoint" in r.stdout
    assert not (tmp_path / "mini.ckpt.bdb").exists()
    assert (tmp_path / "own.bdb.trace").exists()

    # ...and -r finds it through the trace's `# flow:` header, no name given.
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-r resuming from" in r.stdout and "own.bdb" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_build_flag_takes_no_stage_and_bad_flags_refuse(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    # A build runs the whole flow, so a stage with -b is a contradiction —
    # refused in both spellings rather than silently reinterpreted.
    r = _run(["tclsh", _DRIVER, "--build", "--stage", "dnuts", flow], tmp_path)
    assert r.returncode == 2 and "-b takes no stage" in r.stderr
    r = _run(["tclsh", _DRIVER, "--build", flow, tmp_path / "c.bdb", "dnuts"],
             tmp_path)
    assert r.returncode == 2 and "-b takes no stage" in r.stderr
    r = _run(["tclsh", _DRIVER, "--build", "--resume", flow], tmp_path)
    assert r.returncode == 2 and "mutually exclusive" in r.stderr
    r = _run(["tclsh", _DRIVER, "--resume", "--stage", "build", flow], tmp_path)
    assert r.returncode == 2 and "use -b" in r.stderr


def test_resume_flag_with_no_checkpoint_names_the_build_remedy(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 2
    assert "found no checkpoint for this flow" in r.stderr
    assert "btcl -b" in r.stderr                       # the remedy, named


def test_resume_notes_a_stale_flow_text(tmp_path):
    # The resume replays the RECORDED build, so an edit to the flow's text
    # since then does not take effect — worth a NOTE with the rebuild
    # remedy, not a refusal (the recorded build is a fine thing to resume).
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "changed since this build" not in r.stdout  # unedited: silent

    flow.write_text(_FLAT_FLOW + "# a post-build edit\n")
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the flow's text (or a sourced file's) changed since this build" \
        in r.stdout
    assert "btcl -b" in r.stdout                       # the rebuild remedy
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_ephemeral_pins_print_their_flow_text_at_exit(tmp_path):
    # A session with no durable checkpoint cannot persist a pin as a BDB
    # row, but the pin has a second durable form: FLOW TEXT.  The exit
    # prints the paste lines, so the experiment's outcome survives even
    # though the checkpoint cannot — and the banner names `btcl -b` as the
    # way to get the durable kind.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "btcl -b" in r.stdout                       # the banner's remedy
    assert "as flow text" in r.stdout
    assert "select_topology d1 2" in r.stdout          # the paste line

    # With a durable checkpoint the rows persist — no paste block.
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path,
             stdin="pin d1 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "as flow text" not in r.stdout


def test_preflight_follows_the_engines_source_rules(tmp_path):
    # Codex #794 P1s: the pre-flight must read `source` the way the ENGINE
    # does.  (a) `source foo` with only foo.buda on disk gets the suffix
    # appended (cmd_source's fallback) — skipping it silently approved a
    # build whose included file ends in a non-durable open; (b) a file
    # sourced TWICE executes twice, so the cycle guard is an active
    # recursion stack, not a visited memo — the memo skipped the second
    # execution and read `include(nondurable); open_bdb good.bdb; include
    # again` as durable while the real run ends on the include's open.
    sub = tmp_path / "ck_open.buda"
    sub.write_text("open_bdb design.bdb.sql\n")
    flow = tmp_path / "mini.buda"
    flow.write_text("source ck_open\n" + _FLAT_FLOW)   # suffixless spelling
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2
    assert "-b refused before running" in r.stderr
    assert "ck_open.buda line 1" in r.stderr

    flow.write_text("source ck_open.buda\nopen_bdb good.bdb\n"
                    "source ck_open.buda\n" + _FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2, "the re-sourced include's open was skipped"
    assert "-b refused before running" in r.stderr

    # A genuine cycle terminates (active-stack guard): the self-source is
    # skipped while on the stack, so the scan reaches the non-durable open
    # and refuses at t=0 — the engine never runs.
    cyc = tmp_path / "cyc.buda"
    cyc.write_text("source cyc.buda\nopen_bdb x.bdb.sql\n")
    r = _run(["tclsh", _DRIVER, "--build", cyc], tmp_path)
    assert r.returncode == 2
    assert "-b refused before running" in r.stderr


def test_sourced_edits_and_nested_checkpoints(tmp_path):
    # Codex #794 P1 + P2, the sourced-file halves: the staleness stamp
    # covers the whole SOURCE TREE (the recorded recipe flattens the
    # sourced files too), and a relative open_bdb in a sourced sub-file
    # resolves where the ENGINE resolves it — the innermost file's
    # directory — so the trace lands beside the real checkpoint and -r
    # discovers it through the source tree, outside the entry flow's dir.
    subdir = tmp_path / "setup"
    subdir.mkdir()
    (subdir / "ck.buda").write_text("open_bdb own.bdb\n")
    flow = tmp_path / "mini.buda"
    flow.write_text("source setup/ck.buda\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "opens its own durable checkpoint" in r.stdout
    assert (subdir / "own.bdb").exists()               # where the engine put it
    assert (subdir / "own.bdb.trace").exists()         # ...and the trace beside it
    assert not (tmp_path / "own.bdb").exists()         # not the entry-dir guess

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-r resuming from" in r.stdout
    assert "RESUMED" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # Edit only the SOURCED file: the entry flow is byte-identical, and the
    # NOTE must still fire.
    (subdir / "ck.buda").write_text("open_bdb own.bdb\n# edited\n")
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "(or a sourced file's) changed since this build" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


# ── the read-only .sql input: -b redirect (phase 2) ────────────────────────
# Many hier flows open an input `.bdb.sql` WITHOUT `writeback` — "read the
# design, never write it back".  The engine materializes such an open into a
# binary copy and persists the whole pipeline into the copy; -b names that
# copy (BUDA_BDB_MATERIALIZE_TO) so it survives as the checkpoint, with the
# input read-only by construction — same code path, no writeback source.


def _readonly_input_flow(tmp_path):
    """A flow opening a real .bdb.sql input read-only, plus the input."""
    prep = tmp_path / "prep.buda"
    prep.write_text("open_bdb :memory:\nset_die 800 600\n"
                    "save_bdb input.bdb.sql\nexit\n")
    r = _run(["tclsh", _DRIVER, prep], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    inp = tmp_path / "input.bdb.sql"
    assert inp.exists()
    flow = tmp_path / "ro.buda"
    flow.write_text("open_bdb input.bdb.sql\n" + _FLAT_FLOW)
    return flow, inp


def test_build_redirects_a_readonly_sql_input(tmp_path):
    flow, inp = _readonly_input_flow(tmp_path)
    before = inp.read_bytes()

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "materializing the read-only input" in r.stdout
    assert "(the input is never written)" in r.stdout
    ckpt = tmp_path / "ro.ckpt.bdb"
    assert ckpt.exists()                                # the durable copy
    trace = tmp_path / "ro.ckpt.bdb.trace"
    assert trace.exists()
    assert re.search(r"^# input_crc32: \d+$", trace.read_text(), re.M)
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert inp.read_bytes() == before                   # THE property

    # -r resumes FROM THE CHECKPOINT: the recorded `open_bdb input.bdb.sql`
    # is rewritten onto it (re-opening the input would materialize a fresh
    # throwaway copy and restore nothing).
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resuming at the deepest recorded stage `dnuts`" in r.stdout
    replayed_opens = [ln for ln in r.stdout.splitlines()
                      if ln.startswith("replay> open_bdb")]
    assert replayed_opens and all("ro.ckpt.bdb" in ln
                                  for ln in replayed_opens), replayed_opens
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert inp.read_bytes() == before


def test_redirect_reuse_keeps_pins_and_a_changed_input_rebuilds(tmp_path):
    flow, inp = _readonly_input_flow(tmp_path)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path,
             stdin="pin d1 3\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr

    # A -b rerun with the input unchanged REUSES the checkpoint, so the
    # pin persisted there re-attaches to the rebuilt pool (_apply_bdb_pins).
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reusing the checkpoint" in r.stdout
    assert "[pinned]" in _log(flow), "the pin fell out of the reuse rebuild"
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # A changed input at -r is a NOTE (the resume opens the checkpoint
    # materialized from the OLD input)...
    inp.write_text(inp.read_text() + "-- trailing comment\n")
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the input BDB" in r.stdout and "changed since this" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # ...and at -b it re-materializes FRESH: the old checkpoint no longer
    # derives from this input, so keeping its pins would pin a different
    # design's candidates.  Loud, and the pin is gone.
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "re-materializing" in r.stdout and "fresh" in r.stdout
    assert "[pinned]" not in _log(flow)
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
