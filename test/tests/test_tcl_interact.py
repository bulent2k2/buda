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
# The launcher, platform-selected (bash wrapper on POSIX, the PowerShell
# twin on native Windows — and on any box with pwsh when BUDA_WRAPPER_PS=1
# forces it), so the wrapper-level tests below exercise whichever btcl this
# platform actually runs: the -b/-r/-s forwarding is wrapper code, and the
# .ps1 twin's copy was only hand-validated until these went through
# wrapper_select (validated here under BUDA_WRAPPER_PS=1 with pwsh).
from wrapper_select import wrapper_command, wrapper_missing_reason
_BTCL_CMD = wrapper_command(_ROOT, "btcl")

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host"),
              pytest.mark.skipif(_BTCL_CMD is None,
                                 reason=wrapper_missing_reason("btcl"))]

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

    r = _run([*_BTCL_CMD, "-i", flow, tmp_path / "armed.bdb"], tmp_path,
             stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checkpoint" in r.stdout and "no file-backed BDB" not in r.stdout

    r = _run([*_BTCL_CMD, "-i", flow, tmp_path / "armed.bdb"], tmp_path)
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
    r = _run([*_BTCL_CMD, "-i", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout

    tclflow = tmp_path / "flow.tcl"
    tclflow.write_text("puts hi\n")
    r = _run([*_BTCL_CMD, "-i", tclflow], tmp_path)
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
    r = _run([*_BTCL_CMD, flow], tmp_path)
    assert r.returncode == 2
    assert "is a BUDA flow" in r.stderr
    assert "btcl -i" in r.stderr and "buda " in r.stderr   # both remedies
    assert "invalid command name" not in r.stderr          # tclsh never saw it

    # A .tcl operand is untouched — the wrapper refuses ONE named mistake, it
    # does not audit what a Tcl script may be called.
    tclflow = tmp_path / "hi.tcl"
    tclflow.write_text("puts hi\n")
    r = _run([*_BTCL_CMD, tclflow], tmp_path)
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
    r = _run([*_BTCL_CMD, "-i", flow], tmp_path)
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
    r = _run([*_BTCL_CMD, "-i", flow], tmp_path,
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
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path)
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
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path)
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

    r = _run([*_BTCL_CMD, "-s", "plan", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESUMING at `plan`" in r.stdout
    assert "replay> RUN_PLANNER 5" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_build_flag_refuses_a_nondurable_checkpoint_before_running(tmp_path):
    # The non-durable shapes -b still refuses at t=0 — before an engine is
    # spawned — naming the file and line of the open.  The two SINGLE-open
    # shapes are no longer among them, because both REDIRECT: a read-only
    # `.sql` input (test_build_redirects_a_readonly_sql_input) and
    # `:memory:` (test_build_redirects_a_memory_flow_into_the_checkpoint).
    # What is left, and tested here: a `.sql` input that does not exist (the
    # build could only fail at the open, a route later), and a MULTI-open
    # flow ending non-durable — where a redirect could land on the wrong
    # open, so the refusal stands (the `:memory:` twin of that case is
    # test_a_multi_open_memory_flow_is_still_refused).
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

    # A single-open `:memory:` flow used to be refused here too.  It now
    # redirects instead — the change this case was updated for, not a
    # weakened assertion: the refusal it made was the one being removed.
    flow.write_text(_FLAT_FLOW + "open_bdb :memory:\n")
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "building the flow's `:memory:` BDB in the checkpoint" in r.stdout
    assert (tmp_path / "mini.ckpt.bdb").is_file()


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

    # A checkpoint path that names an existing DIRECTORY is refused before
    # anything is deleted: the fresh-rebuild path removes a stale target,
    # and a recursive delete on a mistyped directory would erase it
    # wholesale (Codex #796 P1).
    trap = tmp_path / "trap"
    (trap / "precious").mkdir(parents=True)
    (trap / "precious" / "keep.txt").write_text("data\n")
    r = _run(["tclsh", _DRIVER, "--build", flow, trap], tmp_path)
    assert r.returncode == 2
    assert "is not a regular file" in r.stderr
    assert (trap / "precious" / "keep.txt").read_text() == "data\n"


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


def test_the_build_resume_demo_vehicles(tmp_path):
    # docs/BUILD_RESUME.md's two walkthroughs, pinned so the doc's recipes
    # cannot rot: the FLAT demo (no open_bdb -> auto checkpoint) and the
    # HIER demo (read-only .bdb.sql input -> materialized checkpoint;
    # dnuts resume = inspection, plan resume takes a template pin).
    # Copied to tmp_path with the ../flow/tracks fixture their `source`
    # expects, so nothing dirties demo/ and the flow logs cannot race
    # other tests running the demos in place.
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "flow" / "tracks")
    for f in ("resume_flat.buda", "resume_hier.buda",
              "resume_hier_input.bdb.sql"):
        shutil.copy(_ROOT / "demo" / f, demo / f)

    r = _run([*_BTCL_CMD, "-b", demo / "resume_flat.buda"], tmp_path,
             stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-b arming checkpoint" in r.stdout and "FLAT flow" in r.stdout
    assert (demo / "resume_flat.ckpt.bdb").exists()
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    r = _run([*_BTCL_CMD, "-r", demo / "resume_flat.buda"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "deepest recorded stage `dnuts`" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    inp = (demo / "resume_hier_input.bdb.sql").read_bytes()
    r = _run([*_BTCL_CMD, "-b", demo / "resume_hier.buda"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "materializing the read-only input" in r.stdout
    assert "HIER flow" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert (demo / "resume_hier_input.bdb.sql").read_bytes() == inp

    # dnuts resume = the guarded inspection session; plan resume holds the
    # construction and takes a template pin that fans to both instances.
    r = _run([*_BTCL_CMD, "-r", demo / "resume_hier.buda"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INSPECTION session" in r.stdout

    r = _run([*_BTCL_CMD, "-r", "-s", "plan", demo / "resume_hier.buda"],
             tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "held by the checkpoint" in r.stdout
    assert "2 expanded instances" in r.stdout      # the template pin fanned out
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert (demo / "resume_hier_input.bdb.sql").read_bytes() == inp


# ── explorer (GUI) pins: previews the session must not lose silently ──────
# An explorer pin's durable form is the sidecar `.json`, applied only where
# the PLANNER runs; the visualizer's replan deliberately never writes the
# checkpoint.  Two honesty pieces guard the seams: the prompt watches the
# sidecar's content and says PREVIEW (with the cost of committing: `replan`
# replays the flow's tail, healers included — never spent silently), and a
# below-plan resume NOTEs a selections sidecar with the `-s plan` remedy.

_SIDECAR = ('{"selections": [{"bundle_hint": "irq", "bundle_id": 6, '
            '"topo_type": "Z_HVH@x290@y115", "topo_wl": 320, '
            '"topo_index_hint": 2, "note": "", "seg_layers": [6, 5, 6]}]}')


class _Interactive:
    """A btcl session driven one command at a time — what the piped-stdin
    `_run` cannot do: the sidecar must change WHILE the prompt waits, the
    way the explorer changes it, and with everything piped up front the
    write would race the session's own entry stamp."""

    def __init__(self, cmd, cwd):
        import os
        import queue
        import threading
        self.p = subprocess.Popen([*map(str, cmd)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, cwd=cwd,
                                  env={**os.environ})
        # A reader THREAD, not `select`: on Windows `select` accepts only
        # SOCKETS, and a pipe fd raises `WinError 10038: an operation was
        # attempted on something that is not a socket` -- measured on all
        # four of this class's users (windows-validate run 37, mingw).  A
        # blocking read on its own thread is the portable shape and needs no
        # platform branch: the queue below gives the timeout `select` gave.
        self._q = queue.Queue()
        self._buf = ""
        def _pump(fh, q):
            # `os.read`, not `fh.read(n)`: the buffered form blocks until it
            # has n bytes or EOF, which would hide a prompt that carries no
            # trailing newline -- the very thing this class waits for.
            fd = fh.fileno()
            try:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    q.put(chunk)
            except OSError:
                pass                            # pipe closed under us
            finally:
                q.put(None)                     # EOF sentinel
        self._reader = threading.Thread(
            target=_pump, args=(self.p.stdout, self._q), daemon=True)
        self._reader.start()

    def read_until(self, marker, deadline=300):
        import queue
        import time
        end = time.time() + deadline
        while time.time() < end:
            try:
                chunk = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if chunk is None:                   # the session ended
                break
            self._buf += chunk.decode("utf-8", "replace")
            if marker in self._buf:
                out, self._buf = self._buf, ""
                return out
        raise AssertionError(f"never saw {marker!r} in:\n{self._buf[-2000:]}")

    def send(self, line):
        self.p.stdin.write((line + "\n").encode())
        self.p.stdin.flush()

    def finish(self, deadline=300):
        import queue
        self.p.stdin.close()
        self.p.wait(timeout=deadline)
        self._reader.join(timeout=deadline)
        # Drain what the pump collected after the last `read_until`; the
        # thread owns the pipe now, so reading it here would race the pump
        # and usually come back empty.
        rest = []
        while True:
            try:
                chunk = self._q.get_nowait()
            except queue.Empty:
                break
            if chunk is None:
                break
            rest.append(chunk)
        out = self._buf + b"".join(rest).decode("utf-8", "replace")
        self._buf = ""
        return out


def _flat_demo(tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "flow" / "tracks")
    shutil.copy(_ROOT / "demo" / "resume_flat.buda", demo / "resume_flat.buda")
    return demo


def test_a_gui_pin_is_reported_as_a_preview_and_never_auto_healed(tmp_path):
    demo = _flat_demo(tmp_path)
    s = _Interactive([*_BTCL_CMD, "-b", demo / "resume_flat.buda"], tmp_path)
    s.read_until("resume_flat> ")
    s.send("topos d0")                      # the entry stamp is now taken
    s.read_until("resume_flat> ")
    (demo / "resume_flat.json").write_text(_SIDECAR)   # "the explorer"
    s.send("topos d0")                      # any command re-stamps
    out = s.read_until("resume_flat> ")
    assert "the explorer saved selection(s)" in out
    assert "PREVIEW" in out and "HEALERS included" in out
    s.send("done")
    tail = s.finish()
    # done must NOT silently spend the flow's tail (healers included) on a
    # pin the user made in a preview tool — it says what was not committed
    # and where the pins still apply.
    assert "were NOT committed to the checkpoint" in tail
    assert "pins changed since the last route" not in out + tail
    assert "replay>" not in tail
    assert s.p.returncode == 0, tail[-2000:]


def test_replan_commits_gui_pins_and_a_below_plan_resume_notes_them(tmp_path):
    demo = _flat_demo(tmp_path)
    flow = demo / "resume_flat.buda"
    s = _Interactive([*_BTCL_CMD, "-b", flow], tmp_path)
    s.read_until("resume_flat> ")
    s.send("topos d0")
    s.read_until("resume_flat> ")
    (demo / "resume_flat.json").write_text(_SIDECAR)
    s.send("topos d0")
    s.read_until("PREVIEW")
    s.send("replan")                        # the explicit commit
    s.send("done")
    tail = s.finish()
    assert s.p.returncode == 0, tail[-2000:]
    assert "47 net segments placed" in tail      # the pinned route (46 + 1)
    assert "were NOT committed" not in tail      # reminder cleared by replan

    # The commit persisted: a plain -r restores the pinned route...
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "47 net segments placed" in r.stdout
    # ...and the below-plan NOTE names the sidecar with the -s plan remedy,
    # worded to stay true whether or not the checkpoint already carries it.
    assert "sidecar pins are applied at the PLANNER" in r.stdout
    r = _run(["tclsh", _DRIVER, "--resume", "--stage", "plan", flow],
             tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sidecar pins are applied at the PLANNER" not in r.stdout


def test_an_uncommitted_sidecar_applies_at_a_plan_resume(tmp_path):
    # The user-report shape end-to-end (no GUI needed): a sidecar written
    # AFTER the build (so the checkpoint holds the pre-pin route) is
    # invisible to a dnuts resume — NOTEd, not silent — and a `-s plan`
    # resume applies AND persists it, so every later plain -r carries it.
    demo = _flat_demo(tmp_path)
    flow = demo / "resume_flat.buda"
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    (demo / "resume_flat.json").write_text(_SIDECAR)

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "46 net segments placed" in r.stdout      # pre-pin, restored as-is
    assert "sidecar pins are applied at the PLANNER" in r.stdout

    r = _run(["tclsh", _DRIVER, "--resume", "--stage", "plan", flow],
             tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "47 net segments placed" in r.stdout      # applied...

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "47 net segments placed" in r.stdout      # ...and persisted


def test_an_inspection_session_cannot_lose_the_preview_reminder(tmp_path):
    # Codex #804 P2: in a hier nuts/dnuts (inspection) resume the route
    # callback REFUSES replan — and a refusal that returned normally read
    # as a run, clearing the GUI-pin reminder with no planner anywhere.
    # The refusal raises now, and the PREVIEW message in a guarded session
    # names the one open door (`-s plan`) instead of the closed one.
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "flow" / "tracks")
    for f in ("resume_hier.buda", "resume_hier_input.bdb.sql"):
        shutil.copy(_ROOT / "demo" / f, demo / f)
    flow = demo / "resume_hier.buda"
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    s = _Interactive(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    out = s.read_until("resume_hier> ")
    assert "INSPECTION session" in out
    s.send("topos")
    s.read_until("resume_hier> ")
    (demo / "resume_hier.json").write_text(
        '{"selections": [{"bundle_hint": "b_lohi_t0_0", "bundle_id": 2, '
        '"topo_type": "X", "topo_wl": 1, "topo_index_hint": 1, '
        '"seg_layers": []}]}')
    s.send("topos")
    out = s.read_until("resume_hier> ")
    assert "cannot commit (replan is disabled here)" in out
    assert "HEALERS included" not in out       # the closed door, not offered
    s.send("replan")                           # refused — must not clear
    s.send("done")
    tail = s.finish()
    assert s.p.returncode == 0, tail[-2000:]
    assert "resume at `plan` to re-plan" in tail          # the refusal, said
    assert "were NOT committed to the checkpoint" in tail  # reminder survives


def test_a_typed_pin_replan_also_commits_the_gui_preview(tmp_path):
    # Codex #804 P2, the second half: with an explorer preview pending AND a
    # typed pin, the dirty-pin re-plan (at `save`, or the caller's at exit)
    # IS a planner run — it applies the sidecar and persists — so the
    # messages must not keep calling the preview uncommitted.  Before the
    # fix, `save` claimed the snapshot held the PRE-pin route right after
    # the pin went in, and the exit note said "NOT committed" seconds
    # before the caller's re-plan committed it.
    demo = _flat_demo(tmp_path)
    s = _Interactive([*_BTCL_CMD, "-b", demo / "resume_flat.buda"], tmp_path)
    s.read_until("resume_flat> ")
    s.send("topos d0")
    s.read_until("resume_flat> ")
    (demo / "resume_flat.json").write_text(_SIDECAR)   # "the explorer"
    s.send("topos d0")
    out = s.read_until("resume_flat> ")
    assert "PREVIEW" in out

    s.send("pin d1 4")                       # typed pin: pins_dirty
    s.read_until("resume_flat> ")
    s.send("save")                           # dirty re-plan runs first...
    out = s.read_until("resume_flat> ")
    assert "wrote snapshot" in out
    assert "PRE-pin" not in out              # ...so this claim would be false

    # A SECOND explorer save after that commit, plus another typed pin: the
    # exit note must say the preview rides the pending re-plan, not that it
    # was dropped.
    (demo / "resume_flat.json").write_text(
        _SIDECAR.replace('"note": ""', '"note": "x"'))
    s.send("pin d0 2")
    s.read_until("resume_flat> ")
    s.send("done")
    tail = s.finish()
    assert "NOT committed" not in tail, tail
    assert "ride" in tail and "applies the" in tail

    # The commit is real: a plain -r restores the sidecar's choice as the
    # PINNED selection.
    r = _run([*_BTCL_CMD, "-r", demo / "resume_flat.buda"], tmp_path,
             stdin="topos irq\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PINNED" in r.stdout and "Z_HVH" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_the_buda_suffix_is_inferred_and_question_mark_is_help(tmp_path):
    # `btcl -r resume_flat` refused while resume_flat.buda sat right there —
    # the launcher seam not applying the suffix rule the engine's own
    # `source` has (cmd_source's .buda fallback).  Inference is the
    # FALLBACK direction only: a bare name passes exactly when nothing is
    # literally so named and `<name>.buda` exists, so a Tcl flow or a typo
    # keeps the refusal.  And `?` at the prompt is `help`.
    demo = _flat_demo(tmp_path)
    r = _run([*_BTCL_CMD, "-b", demo / "resume_flat"], tmp_path,
             stdin="?\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-b arming checkpoint" in r.stdout
    assert "this list (also: ?)" in r.stdout           # `?` reached help
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    r = _run([*_BTCL_CMD, "-r", demo / "resume_flat"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "deepest recorded stage `dnuts`" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    # Neither spelling exists -> the wrapper's refusal, unchanged.
    r = _run([*_BTCL_CMD, "-r", demo / "nothere"], tmp_path)
    assert r.returncode == 2
    assert "take a .buda flow" in r.stderr
    # BOTH spellings exist -> refusal too: inference is the fallback
    # direction only, and passing the bare name through would make the
    # driver run the suffixless FILE as the flow (Codex #805 P1).
    (demo / "resume_flat").write_text('puts "not a flow"\n')
    r = _run([*_BTCL_CMD, "-r", demo / "resume_flat"], tmp_path)
    assert r.returncode == 2
    assert "take a .buda flow" in r.stderr
    (demo / "resume_flat").unlink()
    # A Tcl flow is still not an interact operand.
    (demo / "x.tcl").write_text("puts hi\n")
    r = _run([*_BTCL_CMD, "-i", demo / "x.tcl"], tmp_path)
    assert r.returncode == 2
    assert "take a .buda flow" in r.stderr


def test_pins_verb_and_the_resume_pin_banner(tmp_path):
    # `pins` lists the live inventory (the new dump_pins), and a `-r` resume
    # prints the SAME inventory right after RESUMED — the choices the
    # checkpoint carries, said up front instead of rediscovered at `topos`.
    demo = _flat_demo(tmp_path)
    flow = demo / "resume_flat.buda"
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path,
             stdin="pins\npin d1 4\npins\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "dump_pins: no pinned bundles." in r.stdout   # before the pin
    assert "dump_pins: 1 pinned bundle(s):" in r.stdout  # after it
    assert "(d1_0) -> topo 4" in r.stdout

    r = _run([*_BTCL_CMD, "-r", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    resumed = r.stdout.index("RESUMED")
    banner = r.stdout.index("dump_pins: 1 pinned bundle(s):")
    assert banner > resumed                              # inventory ON resume
    assert "(d1_0) -> topo 4" in r.stdout


def test_save_takes_a_snapshot_path(tmp_path):
    # `save <path>` snapshots to the NAMED file (binary or .sql by suffix);
    # bare `save` keeps the historical default beside the checkpoint.
    demo = _flat_demo(tmp_path)
    flow = demo / "resume_flat.buda"
    snap = tmp_path / "handpicked.bdb.sql"
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path,
             stdin=f"pin d1 4\nsave {snap}\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert snap.exists(), "save <path> wrote nothing"
    assert "CREATE TABLE" in snap.read_text()[:2000]     # a real .sql dump
    # The snapshot carries the pin: rebuild it and ask.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript(snap.read_text())
    assert con.execute(
        "SELECT COUNT(*) FROM topology WHERE is_pinned=1").fetchone()[0] == 1
    con.close()


def test_hier_build_rerun_reuses_the_checkpoint(tmp_path):
    # The field-reported FK failure: a second `-b` on the hier demo REUSES
    # the materialized checkpoint, which still holds the previous session's
    # candidate pool — so derive_busterms' clear_busterms hit the
    # topology_seg_busterm -> busterm(id) FK ('tb:' rows) and the rebuild
    # died with a bare "FOREIGN KEY constraint failed".  The clear now keeps
    # exactly the still-referenced 'tb:' rows; a pin from the first build
    # re-attaches to the rebuilt pool like the flat reuse path.
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "flow" / "tracks")
    for f in ("resume_hier.buda", "resume_hier_input.bdb.sql"):
        shutil.copy(_ROOT / "demo" / f, demo / f)
    flow = demo / "resume_hier.buda"

    r = _run([*_BTCL_CMD, "-b", flow], tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run([*_BTCL_CMD, "-b", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reusing the checkpoint" in r.stdout
    assert "FOREIGN KEY" not in r.stdout + r.stderr
    assert "[pinned]" in _log(flow), "the template pin fell out of the rerun"
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_replan_retires_the_absorbed_sidecar_and_layers_survive_rebuild(tmp_path):
    # The GUI-pin lifecycle, end to end: a sidecar pin WITH forced layers is
    # committed by `replan` -> the entry is RETIRED from the json (it is now
    # durable in the checkpoint), while an entry the BDB cannot replay on a
    # rebuild (a group pin) is KEPT; then a `-b` rerun with the json gone
    # restores the pin AND the forced layers from the checkpoint alone --
    # the rebuild door of the #815 restore (_apply_bdb_pins used to re-attach
    # the pin only, and the persist right after ERASED the layer meta).
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copytree(_ROOT / "flow" / "tracks", tmp_path / "flow" / "tracks")
    for f in ("resume_hier.buda", "resume_hier_input.bdb.sql"):
        shutil.copy(_ROOT / "demo" / f, demo / f)
    flow = demo / "resume_hier.buda"

    # Probe the design in-process for the real content uid of the candidate
    # the sidecar pins (hardcoding it would rot with any generator change).
    import json as _json
    import subprocess as _sp
    import sys as _sys
    probe = _sp.run(
        [_sys.executable, "-c", (
            "import sys, os, contextlib, io, json\n"
            f"sys.path[:0] = [{str(_ROOT / 'build')!r}, {str(_ROOT / 'src')!r}]\n"
            "import matplotlib; matplotlib.use('Agg')\n"
            f"os.environ['BUDA_BDB_MATERIALIZE_TO'] = {str(tmp_path / 'probe.bdb')!r}\n"
            "import buda, buda_cli\n"
            "s = buda_cli.BudaSession()\n"
            f"s.script_path = {str(flow)!r}\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            f"    for c in ['source ' + {str(_ROOT / 'flow' / 'tracks' / 'tracks.buda')!r},\n"
            f"              'open_bdb ' + {str(demo / 'resume_hier_input.bdb.sql')!r},\n"
            "              'corner_margin dx 5 dy 5', 'set_min_stub_length 2',\n"
            "              'derive_busterms 1', 'add_blocks_from_bdb 0',\n"
            "              'add_blocks_from_bdb 1 skip', 'run_hier_bundler',\n"
            "              'generate_hier_topologies']:\n"
            "        s.do_command(c)\n"
            "w = {x.input.original_bundle.id: x for x in s.bundles}[1]\n"
            "t = w.input.candidates[2]\n"
            "print(json.dumps({'uid': buda.topo_uid(t), 'type': t.type,\n"
            "                  'wl': t.estimated_wirelength,\n"
            "                  'hint': w.input.original_bundle.get_net_names()[0]}))\n"
        )], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    cand = _json.loads(probe.stdout.strip().splitlines()[-1])
    (tmp_path / "probe.bdb").unlink(missing_ok=True)

    side = demo / "resume_hier.json"
    side.write_text(_json.dumps({"selections": [
        {"bundle_hint": cand["hint"], "bundle_id": 1,
         "topo_type": cand["type"], "topo_wl": cand["wl"],
         "topo_uid": cand["uid"], "topo_index_hint": 2, "note": "",
         "selected_at": "now", "seg_layers": [3, 6, 7]},
        {"bundle_hint": "nonexistent_bus_0", "bundle_id": 99,
         "topo_type": "X", "topo_wl": 1, "topo_uid": "deadbeefdeadbeef",
         "topo_index_hint": 0, "note": "", "selected_at": "now",
         "group_uids": ["deadbeefdeadbeef"]},
    ]}))

    r = _run([*_BTCL_CMD, "-b", flow], tmp_path, stdin="replan\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "retire_sidecar: 1 selection(s) now durable" in r.stdout
    assert "1 kept" in r.stdout
    kept = _json.loads(side.read_text())["selections"]
    assert len(kept) == 1 and kept[0].get("group_uids"), \
        "the group entry must survive retirement"

    # The json GONE: the checkpoint alone must restore pin + layers on the
    # -b rerun (the rebuild path, not load_pipeline).
    side.unlink()
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reusing the checkpoint" in r.stdout
    assert "with 3 forced layer(s)" in _log(flow) + r.stdout, \
        "rebuild lost the forced layers"
    import sqlite3
    con = sqlite3.connect(str(demo / "resume_hier.ckpt.bdb"))
    meta = con.execute(
        "SELECT value FROM meta WHERE key='pinned_layers:1'").fetchone()
    layers = con.execute(
        "SELECT assigned_layer FROM topology_segment WHERE bundle_id='1' "
        "AND cand_index=(SELECT cand_index FROM topology WHERE "
        "bundle_id='1' AND is_selected=1) ORDER BY seg_index").fetchall()
    con.close()
    assert meta and _json.loads(meta[0]) == [3, 6, 7], \
        "the layer meta was erased by the rebuild"
    assert [l[0] for l in layers] == [3, 6, 7], "the route lost the layers"


def test_an_ephemeral_session_never_retires_the_sidecar(tmp_path):
    # `btcl -i` with no checkpoint: the json is the ONLY persistence, so a
    # replan must not retire it (the engine gates on a DURABLE BDB).
    demo = _flat_demo(tmp_path)
    flow = demo / "resume_flat.buda"
    side = demo / "resume_flat.json"
    side.write_text(_SIDECAR)
    r = _run([*_BTCL_CMD, "-i", flow], tmp_path, stdin="replan\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "retire_sidecar" not in r.stdout
    assert side.exists(), "an ephemeral session deleted the only persistence"


def test_a_user_pin_survives_the_b_rerun(tmp_path):
    # The custom-topologies guide's worked example, through the door that
    # used to drop it (#831 review): build, hand-edit + `edit_commit pin`
    # at the prompt, then a `-b` RERUN — the USER candidate re-injects from
    # the checkpoint and the pin + forced layers re-attach.
    demo = tmp_path / "demo"
    demo.mkdir()
    shutil.copy(_ROOT / "demo" / "custom_topo.buda", demo)
    flow = demo / "custom_topo.buda"
    r = _run([*_BTCL_CMD, "-b", flow], tmp_path, stdin=(
        "pin a_0 4\nreplan\nedit_topology 1\nedit_set_layer 0 3\n"
        "edit_set_layer 2 3\nedit_commit pin\nreplan\ndone\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Pinned 3 segment layer(s)" in r.stdout

    r = _run([*_BTCL_CMD, "-b", flow], tmp_path, stdin="pins\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "topo 9 (USER) layers[M3 M4 M3]" in r.stdout, \
        "the -b rerun lost the hand-edited pin"
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_alias_typed_at_the_prompt_reaches_the_engine(tmp_path):
    # The alias command's interactive half: an alias defined mid-session is
    # session state, absent from the registry the prompt cached at start —
    # so without the gate consulting `buda::aliases` it would be refused
    # locally as "unknown command".  Define one at the prompt and invoke it.
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path,
             stdin="alias rd report_wl\nrd\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unknown command 'rd'" not in r.stdout, r.stdout
    # The alias reached the engine: report_wl ran on the routed design.
    assert "detailed WL" in r.stdout, r.stdout


# ── the `:memory:` flow: -b redirect ───────────────────────────────────────
# The commonest shape in the tree (30 checked-in flows): a hier design the
# flow CONSTRUCTS, in a database it never meant to keep.  -b used to refuse
# it outright — correctly, since the run would discard everything it routed
# — which asked the user to edit a working flow to get a checkpoint.  Now the
# same fresh database is built in the checkpoint instead
# (BUDA_BDB_MEMORY_TO), and the flow's text is untouched.
#
# The sibling `.sql` redirect above REUSES its materialization; this one
# cannot, and the difference is the point: a `.sql` open READS a design, so
# reopening the copy resumes it, while `:memory:` BUILDS one — the flow's own
# add_cell/add_inst lines run again on the next build.

_MEMORY_FLOW = "open_bdb :memory:\nset_die 800 600\n" + _FLAT_FLOW


def test_build_redirects_a_memory_flow_into_the_checkpoint(tmp_path):
    flow = tmp_path / "mem.buda"
    flow.write_text(_MEMORY_FLOW)

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "building the flow's `:memory:` BDB in the checkpoint" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    ckpt = tmp_path / "mem.ckpt.bdb"
    assert ckpt.exists() and ckpt.stat().st_size > 0
    assert (tmp_path / "mem.ckpt.bdb.trace").exists()
    # The flow is UNCHANGED — the redirect is the launcher's, not an edit.
    assert flow.read_text() == _MEMORY_FLOW

    # And it resumes: the recorded `open_bdb :memory:` is rewritten onto the
    # checkpoint (replaying it verbatim would open a fresh in-memory DB and
    # restore nothing).  Same rewrite the .sql redirect uses — `:memory:` is
    # stamped as the trace's `# input:`, which is what reaches it.
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    replayed = [ln for ln in r.stdout.splitlines()
                if ln.startswith("replay> open_bdb")]
    assert replayed and all("mem.ckpt.bdb" in ln for ln in replayed), replayed
    assert ":memory:" not in " ".join(replayed)
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_a_memory_rebuild_is_fresh_and_says_the_pins_go(tmp_path):
    """The honest half: a -b rerun REBUILDS, so pins do not survive it.

    Reusing the checkpoint would re-run the flow's construction onto rows
    already there, so the rebuild is not a choice — and a message promising
    the .sql redirect's pin-reuse here would be simply false."""
    flow = tmp_path / "mem.buda"
    flow.write_text(_MEMORY_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path,
             stdin="pin d1 3\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    # The banner must NOT make the .sql promise.
    assert "rerun the flow (or\n" not in r.stdout
    assert "A `btcl -b` rerun rebuilds this checkpoint fresh" in r.stdout
    assert "run it without -b and its BDB is `:memory:` again" in r.stdout

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rebuilding the checkpoint" in r.stdout
    assert "cannot be reused" in r.stdout
    # The rebuild really is clean — a reused checkpoint would duplicate-error.
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
    assert "duplicate" not in r.stdout.lower()


def test_a_multi_open_memory_flow_is_still_refused(tmp_path):
    """Only the SINGLE-open shape redirects: the request names one file, so
    with several opens it could land on the wrong one."""
    flow = tmp_path / "multi.buda"
    flow.write_text("open_bdb first.bdb\n" + _MEMORY_FLOW)
    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 2
    assert "last open_bdb is not durable" in r.stderr
    # and it says WHY this one is not redirected
    assert "opens several BDBs" in r.stderr
    assert not (tmp_path / "multi.ckpt.bdb").exists()


# ── a checkpoint that NAMES a flow source (Codex #868 P1) ─────────────────
# Both redirects clear a stale checkpoint before building.  That delete is
# safe for a checkpoint and catastrophic for a script, and `-b <flow> <ckpt>`
# is one fat-fingered argument away from making them the same file: it erased
# the flow and then failed with "sourced file not found", the run's own input
# gone.  PRE-DATES the `:memory:` redirect — reproduced on the read-only-.sql
# door too — so the guard sits where both pass through.

def test_a_checkpoint_naming_the_flow_is_refused_not_deleted(tmp_path):
    flow = tmp_path / "mem.buda"
    flow.write_text(_MEMORY_FLOW)
    before = flow.read_bytes()
    r = _run(["tclsh", _DRIVER, "--build", flow, flow], tmp_path)
    assert r.returncode == 2
    assert "is the flow's own script" in r.stderr
    assert "would DELETE it" in r.stderr
    assert flow.read_bytes() == before          # THE property


def test_a_checkpoint_naming_a_sourced_file_is_refused_too(tmp_path):
    """`_pf_files` is the whole source tree, so a checkpoint aliasing an
    INCLUDED fixture is caught as well — the entry file is not the only
    thing a build reads."""
    inc = tmp_path / "inc.buda"
    inc.write_text("def_layer 4 M4 H TOP 0.0\n")
    flow = tmp_path / "s.buda"
    flow.write_text("source inc.buda\n" + _MEMORY_FLOW)
    before = inc.read_bytes()
    r = _run(["tclsh", _DRIVER, "--build", flow, inc], tmp_path)
    assert r.returncode == 2
    assert "is the flow's own sourced file" in r.stderr
    assert inc.read_bytes() == before


def test_the_readonly_sql_door_refuses_the_alias_too(tmp_path):
    """The same guard on the OTHER redirect — this door had the bug first."""
    flow, _inp = _readonly_input_flow(tmp_path)
    before = flow.read_bytes()
    r = _run(["tclsh", _DRIVER, "--build", flow, flow], tmp_path)
    assert r.returncode == 2
    assert "is the flow's own script" in r.stderr
    assert flow.read_bytes() == before


def test_a_replay_resolves_relative_paths_against_the_flow_not_the_cwd(tmp_path):
    """A replayed setup command means what it meant during the build.

    The engine resolves a relative path against the RUNNING SCRIPT's
    directory, and only against the CWD when no script is running.  A resume
    replays the recorded lines one at a time, so it runs no script -- and
    every relative path in them was silently re-rooted at the CWD.  A
    `require_file tpu.def` that passed during the build then reported the
    file missing on the resume, with the remedy for regenerating an input
    the resume does not even read.

    Matching the build's CWD was never a fix, which is what makes this a
    resolution bug rather than an invocation one: the flow lives in `sub/`
    and the build ran from `tmp_path`, so the file resolves under NEITHER
    CWD.  The build and both resumes run from `tmp_path` here, and the
    pre-fix failure reproduces exactly that way.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "beside.txt").write_text("an input that lives beside the flow\n")
    flow = sub / "mini.buda"
    # `require_file` is a session verb, so it replays in setup -- and it is
    # the loud one: a mis-rooted path here is FATAL rather than silent.
    flow.write_text("open_bdb ckpt.bdb\n"
                    "require_file beside.txt\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUDA-1905" not in r.stdout                  # the build's own root

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUDA-1905" not in r.stdout, "the replay re-rooted a relative path"
    assert "required input file(s) not found" not in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_a_replay_root_is_dropped_for_what_you_type_at_the_prompt(tmp_path):
    """The root is armed for the REPLAY, not for the session.

    A command typed at the prompt is not part of any script, so a relative
    path in it means what the shell would mean by it.  Leaving the flow's
    directory armed would quietly redirect a typed path to somewhere the
    user is not standing -- so this pins the boundary, not just the fix.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "beside.txt").write_text("beside the flow, not the cwd\n")
    flow = sub / "mini.buda"
    flow.write_text("open_bdb ckpt.bdb\n"
                    "require_file beside.txt\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    # Typed from tmp_path, where `beside.txt` is NOT: the CWD is the root,
    # so this must fail -- while the identical token replayed above passed.
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path,
             stdin="require_file beside.txt\ndone\n")
    assert "BUDA-1905" in r.stdout, "a typed path was resolved against the flow"
    # ...and the CWD-relative spelling of the very same file passes.
    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path,
             stdin="require_file sub/beside.txt\ndone\n")
    assert "BUDA-1905" not in r.stdout, r.stdout


def test_a_replay_roots_a_sourced_files_lines_in_ITS_directory(tmp_path):
    """The trace FLATTENS the source tree, so one root is not enough.

    A relative path in a sourced file was resolved against THAT file's
    directory at build time, and nothing in a flattened line says where it
    came from.  Arming the entry flow for every line fixes the common case
    and breaks exactly this one -- which the pre-fix CWD fallback happened
    to get right whenever the resume ran from the sourced file's directory,
    so a blanket root was a REGRESSION there (Codex #874 P2).

    So the recorder writes `# origin:` markers and the replay arms per
    line.  Checked from BOTH directories, since each is what the other
    approach got wrong: the entry flow's (where the CWD fallback failed)
    and the sourced file's (where it accidentally worked).
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "data.txt").write_text("beside the SOURCED file\n")
    (sub / "inc.buda").write_text("require_file data.txt\n"
                                  "def_layer 4 M4 H TOP 30\n"
                                  "def_layer 5 M5 V TOP 30\n")
    flow = tmp_path / "top.buda"
    flow.write_text("open_bdb ck.bdb\nsource sub/inc.buda\n"
                    + "\n".join(_FLAT_FLOW.splitlines()[6:]) + "\n")

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    trace = (tmp_path / "ck.bdb.trace").read_text()
    assert f"# origin: {sub / 'inc.buda'}" in trace, trace

    for cwd, where in ((tmp_path, "the entry flow's directory"),
                       (sub, "the sourced file's directory")):
        r = _run(["tclsh", _DRIVER, "--resume", flow], cwd)
        assert "BUDA-1905" not in r.stdout, f"resumed from {where}:\n{r.stdout}"
        assert "done --" in r.stdout, f"resumed from {where}:\n{r.stdout}"


def test_a_trace_without_origin_markers_still_resumes(tmp_path):
    """Backward compatibility: a checkpoint built before the markers.

    Its lines carry no recorded root, so each falls back to the entry
    flow -- which is right for any flow that sources nothing, i.e. every
    trace that could exist and every checked-in flow.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "beside.txt").write_text("x\n")
    flow = sub / "mini.buda"
    flow.write_text("open_bdb ckpt.bdb\nrequire_file beside.txt\n" + _FLAT_FLOW)

    r = _run(["tclsh", _DRIVER, "--build", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    tf = sub / "ckpt.bdb.trace"
    tf.write_text("".join(ln for ln in tf.read_text().splitlines(keepends=True)
                          if not ln.startswith("# origin:")))
    assert "# origin:" not in tf.read_text()

    r = _run(["tclsh", _DRIVER, "--resume", flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUDA-1905" not in r.stdout, r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout
