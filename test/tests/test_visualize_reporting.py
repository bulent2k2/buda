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

"""`visualize` must never open no window in silence.

There were three ways to ask for a viewer and get nothing back, and all three
looked identical from the outside — the command returned, printed nothing,
and the flow carried on as though a window were up:

  1. `--no-viz` (asked for, but not acknowledged anywhere in the log);
  2. the command server forced it off, so a Tcl flow could NEVER get a
     window, on any host;
  3. matplotlib's backend cannot show anything (a headless machine), where
     `plt.show()` is a silent no-op — matplotlib 3.11 does not even warn.

(2) is now a client choice; (1) and (3) say so, at severities that
distinguish "you asked" from "nobody asked for this".
"""
import io
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import buda_cli
import buda_diag
from buda_cmds import verify_viz_cmds as vv

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _ROOT / "tools"


def _session(no_viz):
    s = buda_cli.BudaSession()
    s.no_viz = no_viz
    s.run_command("add_block a 0 0 100 100")
    s.run_command("add_block b 200 0 300 100")
    return s


def _say(session, line):
    buf = io.StringIO()
    with redirect_stdout(buf):
        session.run_command(line)
    return buf.getvalue()


@pytest.mark.parametrize("cmd", ["visualize", "visualize_topologies"])
def test_a_suppressed_viewer_is_reported_as_asked_for(cmd):
    """`--no-viz` was requested, so it is an INFO — but still said, because a
    log read against its script should show where the viewer was skipped."""
    out = _say(_session(no_viz=True), cmd)
    assert "BUDA-1903" in out, out
    assert buda_diag.severity_of_line(out.strip().splitlines()[0]) == "INFO"
    assert cmd in out


@pytest.mark.parametrize("cmd", ["visualize", "visualize_topologies"])
def test_a_viewer_nobody_can_see_is_a_warning(cmd, monkeypatch):
    """Nobody asked for a headless host, so this one is a WARNING — and it is
    the case that had no voice at all: the CLI over ssh drew nothing and said
    nothing."""
    monkeypatch.setattr(vv, "_backend_cannot_show", lambda: True)
    out = _say(_session(no_viz=False), cmd)
    assert "BUDA-1903" in out, out
    assert buda_diag.severity_of_line(out.strip().splitlines()[0]) == "WARNING"


def test_the_viewer_is_reached_when_a_display_is_available(monkeypatch):
    """The other half of the claim: with a backend that CAN show, the command
    goes on and builds the viewer rather than reporting itself skipped.
    (`show()` is still a no-op under the test host's Agg, which is why this
    can run in CI at all — what is being pinned is that nothing short-circuits
    ahead of it.)"""
    monkeypatch.setattr(vv, "_backend_cannot_show", lambda: False)
    built = []
    import buda_viz
    monkeypatch.setattr(buda_viz.BudaVisualizer, "show",
                        lambda self: built.append(self))
    out = _say(_session(no_viz=False), "visualize")
    assert built, "the viewer was never constructed: " + out
    assert "BUDA-1903" not in out, out


def test_the_note_reaches_the_flow_log_too(tmp_path):
    """`visualize` is a PASSTHROUGH command — its output deliberately skips
    run_command's capture, so the note would have gone to the terminal only.
    The flow log is exactly where someone asks afterwards why they never saw
    a window."""
    s = _session(no_viz=True)
    log = tmp_path / "f_flow.log"
    s._flow_log = open(log, "w")
    s._flow_log_path = str(log)
    try:
        _say(s, "visualize")
    finally:
        s._flow_log.close()
    assert "BUDA-1903" in log.read_text()


def test_a_host_without_matplotlib_is_told_which_command_wanted_it(monkeypatch):
    """A farm image built without the plotting stack: the ImportError from
    `from buda_viz import …` names `matplotlib` and neither the command that
    wanted it nor the fact that the rest of the run is unaffected."""
    import builtins
    real = builtins.__import__

    def no_mpl(name, *a, **k):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError("No module named 'matplotlib'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_mpl)
    out = _say(_session(no_viz=False), "visualize")
    assert "BUDA-1903" in out and "matplotlib is not available" in out, out


def test_an_unrecognised_backend_is_tried_rather_than_refused(monkeypatch):
    """The verdict is "is it KNOWN to be unable to show", never "is it known
    to be able to": a third-party backend (`module://…`) is in neither
    builtin list, and refusing a window we could have opened is the worse of
    the two mistakes — `show()` on a backend that cannot show is a no-op."""
    import matplotlib
    monkeypatch.setattr(matplotlib, "get_backend",
                        lambda: "module://some_vendor_backend")
    assert vv._backend_cannot_show() is False
    monkeypatch.setattr(matplotlib, "get_backend", lambda: "Agg")
    assert vv._backend_cannot_show() is True


# ── the command server ─────────────────────────────────────────────────────

def _server():
    sys.path.insert(0, str(_TOOLS))
    import buda_server
    return buda_server


def test_the_server_no_longer_forces_visualization_off():
    """It did, unconditionally — which made `visualize` the one command that
    does not mean from Tcl what it means in a script."""
    srv = _server().Server(out=io.StringIO())
    assert srv.session.no_viz is False


def test_a_client_can_turn_visualization_off_and_ask_what_it_is():
    """What `buda::start -viz 0` sends: a batch driver declares itself,
    exactly as a batch CLI run passes `--no-viz`."""
    out = io.StringIO()
    srv = _server().Server(out=out)
    assert srv.handle("__viz") is True
    assert out.getvalue().endswith("on")
    srv.handle("__viz off")
    assert srv.session.no_viz is True
    out.truncate(0), out.seek(0)
    srv.handle("__viz")
    assert out.getvalue().endswith("off")
    out.truncate(0), out.seek(0)
    srv.handle("__viz sideways")
    assert out.getvalue().startswith("ERR ")
    # A near-miss must not be answered as the no-argument form: `__vizz`
    # would have reported a plausible setting instead of not being
    # understood.  It falls through to the command dispatcher, where an
    # unknown command is BUDA's own fail-fast — which is the right answer.
    out.truncate(0), out.seek(0)
    srv.handle("__vizz")
    assert not out.getvalue().startswith("OK "), out.getvalue()
    assert "unknown command" in out.getvalue(), out.getvalue()


# ── the Tcl bridge ─────────────────────────────────────────────────────────

def test_every_bridge_proc_is_reserved_against_the_registry():
    """`buda::_define` mints one proc per engine command.  A registry name
    colliding with one of the bridge's own — `viz`, say, beside the existing
    `visualize` — would replace it with a wrapper that sends it to the engine
    as a script command, and the flow would fail far from the cause.  The
    reserved list is written out rather than snapshotted, so this is what
    keeps it complete."""
    text = (_TOOLS / "buda.tcl").read_text()
    defined = {n for n in re.findall(r"^proc ::buda::(\w+)", text, re.M)
               if not n.startswith("_")}
    listed = set(re.search(r"variable reserved \{([^}]*)\}", text).group(1).split())
    assert defined == listed, (f"not reserved: {sorted(defined - listed)}; "
                              f"reserved but not defined: {sorted(listed - defined)}")
    # ...and nothing in the engine's registry collides today, so no start
    # prints the refusal.  A future command that does will fail here first.
    from buda_cmds import COMMANDS
    assert not (listed & set(COMMANDS)), sorted(listed & set(COMMANDS))


def test_the_corpus_harness_declares_itself_a_batch_run():
    """31 of the 41 corpus flows end in `visualize`, and a window BLOCKS the
    run until it is closed.  Without `-viz 0` a sweep on a machine with a
    display would stop dead at the first flow's viewer."""
    harness = (_ROOT / "flow" / "tcl" / "corpus" / "harness.tcl").read_text()
    assert "buda::start -echo 0 -viz 0" in harness


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_visualize_from_tcl_says_what_it_did(tmp_path):
    """The whole point, end to end: `buda::visualize` from a real tclsh used
    to return an empty string having done nothing at all."""
    script = tmp_path / "v.tcl"
    script.write_text(f"""
        source {_TOOLS / 'buda.tcl'}
        buda::start -python {sys.executable}
        buda::add_block a 0 0 100 100
        puts "setting=[buda::viz]"
        buda::viz off
        puts "setting=[buda::viz]"
        buda::visualize
        buda::stop
    """)
    r = subprocess.run(["tclsh", str(script)], capture_output=True,
                       encoding="utf-8", cwd=str(tmp_path), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "setting=on" in r.stdout, r.stdout       # the engine's own default
    assert "setting=off" in r.stdout, r.stdout
    assert "BUDA-1903" in r.stdout, r.stdout


@pytest.mark.skipif(shutil.which("tclsh") is None, reason="no tclsh on this host")
def test_start_viz_0_reaches_the_engine(tmp_path):
    """The harness declares itself batch at START, and on a HEADLESS host a
    setting that never arrived looks exactly like one that did — both end in
    no window.  The severity is what tells them apart, so that is what this
    reads: INFO means the engine was told, WARNING means it worked it out."""
    script = tmp_path / "s.tcl"
    script.write_text(f"""
        source {_TOOLS / 'buda.tcl'}
        buda::start -echo 0 -viz 0 -python {sys.executable}
        puts "setting=[buda::viz]"
        buda::add_block a 0 0 100 100
        buda::visualize
        puts "said=[string trim [buda::output]]"
        buda::stop
    """)
    r = subprocess.run(["tclsh", str(script)], capture_output=True,
                       encoding="utf-8", cwd=str(tmp_path), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "setting=off" in r.stdout, r.stdout
    assert "said=BUDA-1903: INFO:" in r.stdout, r.stdout
