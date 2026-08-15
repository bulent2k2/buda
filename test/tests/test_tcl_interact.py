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


def test_flat_flow_pin_replan_verdict(tmp_path):
    flow = tmp_path / "mini.buda"
    flow.write_text(_FLAT_FLOW)
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin d1 4\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FLAT flow" in r.stdout
    assert "`replan` replays the flow's own routing tail" in r.stdout
    assert "no file-backed BDB" in r.stdout        # no open_bdb in the flow
    assert "pins changed since the last route" in r.stdout
    assert "topo 4 of" in r.stdout                 # the replay honored the pin
    # The replay is the FLOW's tail: its checks and detailed stage re-ran.
    assert "replay> run_detailed_nuts" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout


def test_hier_flow_is_autodetected_and_replans_hier(tmp_path):
    # The unmodified hbundles cross-level case: 3 levels, cell-local template
    # + cross-level bundles, :memory: BDB, ends in `visualize` (headless note).
    flow = _ROOT / "flow" / "hbundles" / "08_cross_level.buda"
    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="pin b_lohi 2\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "HIER flow" in r.stdout
    assert "starting `run_planner hier 5`" in r.stdout
    assert "expanded instances" in r.stdout        # the template pin fanned out
    assert r.stdout.count("topo 2 of 5") >= 4, "the replay lost the pin"


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
    assert "durable pin restored -> topo 4" in r.stdout
    assert "topo 4 of" in r.stdout and "[pinned]" in r.stdout
    assert "done -- 0 overlaps, 0 unplaced, 0 audit violations" in r.stdout

    r = _run(["tclsh", _DRIVER, flow], tmp_path, stdin="unpin d1\ndone\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Unpinned" in r.stdout

    r = _run(["tclsh", _DRIVER, flow], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "durable pin restored" not in r.stdout          # honestly unpinned


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
    assert "durable pin restored -> topo 4" in r.stdout
    assert "topo 4 of" in r.stdout and "[pinned]" in r.stdout


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
