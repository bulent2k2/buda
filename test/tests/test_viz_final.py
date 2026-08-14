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

"""`buda -v/--visualize`: open a viewer on the FINISHED design even when the
script has no `visualize`, so a test vehicle can be eyeballed without editing
it — unless the flow already ends by visualizing."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import buda_cli

from wrapper_select import wrapper_command, wrapper_missing_reason

_ROOT = Path(__file__).parents[2]
_CLI = _ROOT / "src" / "buda_cli.py"
_BTCL = _ROOT / "bin" / "btcl"
_BUDA_TCL = _ROOT / "tools" / "buda.tcl"

_FLOW = """\
def_layer 4 M4 H TOP 50
def_layer 5 M5 V TOP 50
add_block A 0 0 100 100
add_block B 400 0 500 100
add_bus x[4] A.p B.p
run_bundler strict
generate_topologies
run_planner
run_nuts
"""


# ── unit: the "does the flow already end by visualizing?" decision ──────────

@pytest.mark.parametrize("verbs, expected", [
    (["run_nuts"], False),                              # no viewer at all
    (["run_nuts", "visualize"], True),                  # last is visualize
    (["run_nuts", "visualize_topologies"], True),       # …or the explorer
    (["visualize", "exit"], True),                      # visualize just before exit
    (["visualize_topologies", "exit"], True),
    (["visualize", "run_nuts"], False),                 # viewer is mid-flow, more after
    (["visualize", "run_nuts", "exit"], False),         # exit NOT preceded by a viewer
    (["run_nuts", "exit"], False),
    ([], False),
])
def test_flow_ends_by_visualizing(verbs, expected):
    s = buda_cli.BudaSession()
    s._recent_verbs = list(verbs)
    assert s._flow_ends_by_visualizing() is expected


# ── integration: run the CLI headless (Agg → `visualize` self-skips with
#    BUDA-1903, so each attempt is countable and nothing blocks) ─────────────

def _run(tmp_path, body, *flags):
    script = tmp_path / "veh.buda"
    script.write_text(body)
    env = {**os.environ, "MPLBACKEND": "Agg",
           "PYTHONPATH": f"{_ROOT/'build'}:{_ROOT/'tools'}"}
    r = subprocess.run([sys.executable, str(_CLI), str(script), *flags],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def _viewer_attempts(out):
    # Headless, every `visualize` reports BUDA-1903 ("no window opened") once.
    return out.count("BUDA-1903")


@pytest.mark.mid
def test_no_v_flag_no_viewer(tmp_path):
    rc, out = _run(tmp_path, _FLOW)
    assert rc == 0
    assert _viewer_attempts(out) == 0


@pytest.mark.mid
def test_v_appends_viewer(tmp_path):
    rc, out = _run(tmp_path, _FLOW, "-v")
    assert rc == 0
    assert _viewer_attempts(out) == 1


@pytest.mark.mid
def test_v_no_double_when_flow_ends_with_visualize(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "visualize\n", "-v")
    assert rc == 0
    assert _viewer_attempts(out) == 1          # the script's own, not doubled


@pytest.mark.mid
def test_v_no_double_when_visualize_just_before_exit(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "visualize\nexit\n", "-v")
    assert _viewer_attempts(out) == 1


@pytest.mark.mid
def test_v_adds_final_viewer_when_visualize_is_mid_flow(tmp_path):
    body = _FLOW.replace("run_planner\n", "visualize\nrun_planner\n")
    rc, out = _run(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 2          # the mid-flow one + the appended


@pytest.mark.mid
def test_v_preserves_a_nonzero_exit_code(tmp_path):
    rc, out = _run(tmp_path, _FLOW + "exit 3\n", "-v")
    assert _viewer_attempts(out) == 1          # viewer still opened…
    assert rc == 3                             # …and the exit code is preserved


@pytest.mark.mid
def test_v_and_no_viz_are_mutually_exclusive(tmp_path):
    rc, out = _run(tmp_path, _FLOW, "-v", "--no-viz")
    assert rc != 0
    assert "not allowed with" in out or "mutually exclusive" in out


# ── Tcl twin: `btcl -v flow.tcl` opens a viewer at buda::stop ───────────────

_tcl_required = pytest.mark.skipif(shutil.which("tclsh") is None,
                                   reason="no tclsh on this host")

# The btcl wrapper exists twice — bin/btcl (bash) and bin/btcl.ps1
# (PowerShell) — honouring the same contract (BUDA_VIZ_FINAL env signal, `--`
# terminator, dash-named-script normalisation).  wrapper_select picks the one
# this platform can run: bash on POSIX/Cygwin; PowerShell on native Windows,
# where `bash` is the unrunnable WSL stub (measured, windows-validate runs 18
# and 23 — every assertion matched the stub's banner instead of flow output).
_BTCL_CMD = wrapper_command(Path(__file__).parents[2], "btcl")

# Everything that goes through the wrapper needs BOTH a launcher and tclsh.
# One marker rather than two stacked decorators, so the skip REASON names the
# prerequisite that is actually missing.
def _btcl_missing():
    if shutil.which("tclsh") is None:
        return "no tclsh on this host"
    if _BTCL_CMD is None:
        return wrapper_missing_reason("btcl")
    return ""


_BTCL_MISSING = _btcl_missing()
_btcl_required = pytest.mark.skipif(bool(_BTCL_MISSING),
                                    reason=_BTCL_MISSING or "btcl is runnable")

# `-python $sys.executable` is not decoration: `buda::start`'s default is
# `python3`, and native Windows exposes `python`/`py` instead (WINDOWS_REQ.md
# calls that lookup a portability hazard, and windows-validate measures it in
# its "python3 lookup reality" step).  Without this the engine never starts,
# the flow produces no viewer note at all, and the test reads that as "the
# viewer did not open" — which is how `test_buda_viz_final_env_var_is_the_signal`
# failed on all three native jobs in run 23.  test_tcl_front_end.py's `_tcl`
# helper passes it for the same reason.
# Paths reach Tcl as FORWARD-slashed, which Tcl accepts on every platform
# including Windows.  A native Windows path interpolated raw would be read as
# backslash ESCAPES by the Tcl parser — `D:\a\buda\...` becomes BEL + `buda`,
# and `source` then fails on a filename that never existed.  `.as_posix()` /
# the replace below cost nothing on POSIX, where the paths are already `/`.
#
# BRACED as well as forward-slashed, and the two solve different problems.
# Forward slashes stop the Tcl parser eating `\a`/`\b`/`\t` as escapes; braces
# stop it WORD-SPLITTING a path that contains spaces — `C:/Program Files/…`
# would otherwise hand `buda::start` a `-python` of `C:/Program` plus a stray
# `Files/…` option and fail before the engine starts.  Tcl's own `{…}` quoting
# is the right tool: it suppresses substitution and splitting together.
_TCL_PATH = "{" + _BUDA_TCL.as_posix() + "}"
_PY_PATH = "{" + sys.executable.replace("\\", "/") + "}"

_TCL_FLOW = f"""\
source {_TCL_PATH}
buda::start -python {_PY_PATH}
buda::def_layer 4 M4 H TOP 50
buda::def_layer 5 M5 V TOP 50
buda::add_block A 0 0 100 100
buda::add_block B 400 0 500 100
buda::add_bus {{x[4]}} A.p B.p
buda::run_bundler strict
buda::generate_topologies
buda::run_planner
buda::run_nuts
"""


def _run_btcl(tmp_path, tcl_body, *flags):
    script = tmp_path / "flow.tcl"
    script.write_text(tcl_body)
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run([*_BTCL_CMD, *flags, str(script)],
                       capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.mid
@_btcl_required
def test_btcl_no_v_no_viewer(tmp_path):
    _, out = _run_btcl(tmp_path, _TCL_FLOW + "buda::stop\n")
    assert _viewer_attempts(out) == 0


@pytest.mark.mid
@_btcl_required
def test_btcl_v_appends_viewer_at_stop(tmp_path):
    _, out = _run_btcl(tmp_path, _TCL_FLOW + "buda::stop\n", "-v")
    assert _viewer_attempts(out) == 1          # appended at buda::stop


@pytest.mark.mid
@_btcl_required
def test_btcl_v_no_double_when_flow_visualizes(tmp_path):
    body = _TCL_FLOW + "buda::visualize\nbuda::stop\n"
    _, out = _run_btcl(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 1          # the flow's own, not doubled


# ── the three review findings on #712, each with the shape that reproduced ──

@pytest.mark.mid
@_btcl_required
def test_btcl_v_appends_viewer_when_the_flow_ends_with_the_engine_exit(tmp_path):
    """THE regression.  `buda::exit` — the engine's own exit command, which the
    bridge defines from the registry like any other — raises SystemExit inside
    the server's ordinary-command path, which replies BYE and terminates the
    session there.  `buda::stop` afterwards finds the pipe closed and never
    sends `__exit`, where the only viz-final implementation lived.

    Measured before the fix: `buda::stop` printed BUDA-1903 and `buda::exit`
    printed nothing — so `btcl -v` silently did nothing for every flow ending
    the documented way.  Both shutdown paths now go through one method."""
    body = _TCL_FLOW + "buda::exit\nbuda::stop\n"
    _, out = _run_btcl(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 1


@pytest.mark.mid
@_btcl_required
def test_btcl_v_no_double_when_the_flow_visualizes_then_exits(tmp_path):
    """The skip rule has to hold on the exit path too, or the fix above just
    moves the doubling to the other shutdown."""
    body = _TCL_FLOW + "buda::visualize\nbuda::exit\n"
    _, out = _run_btcl(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 1          # the flow's own, not doubled


@pytest.mark.mid
@_btcl_required
def test_btcl_v_keeps_a_nonzero_exit_code_from_the_flow(tmp_path):
    """`buda::exit 3` is the fail-fast path: it must still fail, and loudly,
    with the viewer opened.  The viewer's note is appended AFTER the status is
    decided precisely so it cannot turn a FATAL into anything else."""
    # `puts $err` would re-print the whole caught payload — `_request` already
    # echoed it — and double the BUDA-1903 count for reasons that have nothing
    # to do with the viewer.  Report the verdict, not the text.
    body = _TCL_FLOW + ("catch {buda::exit 3} err\n"
                        "puts \"SAW_CODE_3: [string match {*exit code 3*} $err]\"\n")
    _, out = _run_btcl(tmp_path, body, "-v")
    assert _viewer_attempts(out) == 1
    assert "SAW_CODE_3: 1" in out, out[-500:]


@pytest.mark.mid
@_btcl_required
def test_an_unhandled_engine_exit_code_does_not_reach_the_shell(tmp_path):
    """What the Tcl side actually does with `buda::exit 3`, pinned so the doc
    cannot drift back to claiming the CLI's guarantee.

    `_request` turns a FATAL into a Tcl error, and an unhandled Tcl error exits
    1 — the engine's code survives in the error TEXT, not in the process
    status.  `buda -v` really does exit 3, because the CLI owns its own
    process; the bridge does not own tclsh's.  Documented in TCL_FRONT_END.md
    with the two-line pattern for a flow that wants to carry the code."""
    # Streams kept APART here.  An unhandled FATAL is reported twice by
    # construction — `_request` echoes the payload through `_deliver`, then
    # tclsh prints the uncaught error, which carries the same text — so a
    # combined count says 2 without a second viewer existing.  stdout is the
    # echo; one there is the viewer having run once.
    script = tmp_path / "flow.tcl"
    script.write_text(_TCL_FLOW + "buda::exit 3\n")
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run([*_BTCL_CMD, "-v", str(script)],
                       capture_output=True, text=True, env=env, timeout=300)
    # (r.stdout or "") despite capture_output=True: windows-validate on #736
    # measured ONE run of this test with a stream attribute None — a state
    # CompletedProcess should not be able to reach — and the bare
    # concatenation turned that anomaly into a TypeError that hid the repr.
    # The or-guards keep the assertions running and the message below prints
    # the full CompletedProcess as evidence if it ever recurs.
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 1, (r.returncode, (r.stderr or "")[-400:])  # Tcl's, not 3
    assert "exit code 3" in out, r                              # …not lost
    assert _viewer_attempts(r.stdout or "") == 1, r             # opened once


@pytest.mark.mid
@_btcl_required
def test_btcl_runs_a_script_whose_name_starts_with_a_dash(tmp_path):
    """`--` ends btcl's own options, but tclsh does not honour a `--` of its
    own: measured on 8.6.14, both `tclsh -v.tcl` and `tclsh -- -v.tcl` read the
    name as an OPTION, run nothing, drain stdin and exit 0 — a silent no-op
    reporting success.  So the operand is normalised to `./-v.tcl`."""
    script = tmp_path / "-v.tcl"
    script.write_text('puts "ARGV=[list {*}$argv]"\n')
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run([*_BTCL_CMD, "--", script.name, "a", "b"],
                       capture_output=True, text=True, env=env,
                       cwd=str(tmp_path), timeout=300)
    assert "ARGV=a b" in r.stdout, (r.returncode, r.stdout, r.stderr)


def test_a_failing_viewer_is_reported_not_swallowed():
    """The appended viewer used to be wrapped in `except BaseException: pass`,
    so a backend or render failure gave the user neither the window they asked
    for nor a reason — an empty, clean BYE.  The CLI's `-v` prints a warning;
    this is the one place the two paths could disagree about a failure."""
    sys.path.insert(0, str(_ROOT / "tools"))
    import buda_server

    class _Boom:
        no_viz = False
        def _flow_ends_by_visualizing(self): return False
        def run_command(self, cmd): raise RuntimeError("backend exploded")

    srv = buda_server.Server.__new__(buda_server.Server)
    srv.session, srv.viz_final = _Boom(), True
    out = srv._viz_final_output()
    assert "Warning: -v visualize failed" in out
    assert "RuntimeError: backend exploded" in out

    srv.viz_final = False                       # off  -> nothing at all
    assert srv._viz_final_output() == ""
    srv.viz_final, srv.session.no_viz = True, True   # headless by request
    assert srv._viz_final_output() == ""


@pytest.mark.mid
@_btcl_required
@pytest.mark.parametrize("flags, passed", [
    ([],            ["a", "b"]),
    (["-v"],        ["a", "b"]),
    ([],            ["-v"]),            # the FLOW's own -v, in its own position
    (["-v"],        ["-v", "x"]),       # …alongside the wrapper's
    ([],            ["--visualize"]),   # not re-spelled, not moved
    ([],            ["-v", "-v"]),      # not collapsed
    ([],            ["--", "-v"]),
])
def test_btcl_passes_the_flows_arguments_through_untouched(tmp_path, flags, passed):
    """Wrapper options are read only BEFORE the script.  Reading `-v` anywhere
    on the line stole the flow's own argument, re-spelled `--visualize` as
    `-v`, collapsed two occurrences into one and moved it to the end."""
    script = tmp_path / "argv.tcl"
    script.write_text('puts "ARGV=[list {*}$argv]"\n')
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run([*_BTCL_CMD, *flags, str(script), *passed],
                       capture_output=True, text=True, env=env, timeout=300)
    assert f"ARGV={' '.join(passed)}" in r.stdout, r.stdout + r.stderr


@pytest.mark.mid
@_btcl_required
def test_a_flows_own_v_argument_does_not_open_a_viewer(tmp_path):
    """The half of that defect the wrapper alone cannot fix: `buda::start` used
    to scan `$argv` for a bare `-v`, which cannot tell a launcher's request
    from an argument the flow wants for itself — so `btcl flow.tcl -v` opened a
    viewer nobody had asked this wrapper for.  The signal is an env var now."""
    _, out = _run_btcl(tmp_path, _TCL_FLOW + "buda::stop\n")   # no wrapper -v…
    assert _viewer_attempts(out) == 0
    script = tmp_path / "flow.tcl"
    env = {**os.environ, "MPLBACKEND": "Agg"}
    r = subprocess.run([*_BTCL_CMD, str(script), "-v"],   # …flow's own
                       capture_output=True, text=True, env=env, timeout=300)
    assert _viewer_attempts(r.stdout + r.stderr) == 0


@pytest.mark.mid
@_tcl_required
def test_buda_viz_final_env_var_is_the_signal(tmp_path):
    """What `tclsh flow.tcl` uses with no wrapper in the picture, and what
    `btcl -v` sets.  Explicitly off must stay off."""
    script = tmp_path / "flow.tcl"
    script.write_text(_TCL_FLOW + "buda::stop\n")
    def run(val):
        env = {**os.environ, "MPLBACKEND": "Agg", "BUDA_VIZ_FINAL": val}
        r = subprocess.run(["tclsh", str(script)], capture_output=True,
                           text=True, env=env, timeout=300)
        return _viewer_attempts(r.stdout + r.stderr)
    assert run("1") == 1
    assert run("0") == 0
    assert run("off") == 0
