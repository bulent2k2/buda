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

"""The Tcl front end's GUI face: streaming, async, cancellation.

opens_interchange item 9's second residual said it plainly: one request,
one reply, no cancellation and no progress events — fine for a flow script,
not for a GUI driving it.  These pin the three additions and, just as
deliberately, what did NOT change: a client that never opts in never sees
an OUT frame, `buda::output` is the same whole text in every mode, and a
cancelled command fails like any other failing command with the session
alive.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TCL = _ROOT / "tools" / "buda.tcl"

pytestmark = pytest.mark.skipif(shutil.which("tclsh") is None,
                                reason="no tclsh on this host")


def _tcl(tmp_path, body, expect_rc=0):
    script = tmp_path / "t.tcl"
    script.write_text(f"source {_TCL}\nbuda::start -python {sys.executable}\n"
                      f"{body}\n")
    r = subprocess.run(["tclsh", str(script)], capture_output=True,
                       encoding="utf-8", errors="replace",
                       cwd=tmp_path, timeout=300)
    if expect_rc is not None:
        assert r.returncode == expect_rc, r.stdout + r.stderr
    return r.stdout + r.stderr


def test_streaming_delivers_progress_and_the_same_whole_output(tmp_path):
    """`__stream on`: a chatty command arrives as multiple OUT chunks (the
    progress callback counts them), and buda::output is character-identical
    to the buffered mode's — streaming changes how output travels, never
    what a command returns."""
    out = _tcl(tmp_path, """
        buda::def_layer 4 M4 H TOP 30
        set sync [buda::dump_messages]
        set n 0
        buda::onprogress {apply {{chunk} { incr ::chunks }}}
        set ::chunks 0
        buda::stream 1
        set streamed [buda::dump_messages]
        buda::stream 0
        buda::onprogress ""
        puts "chunks=$::chunks"
        puts "same=[string equal $sync $streamed]"
        buda::stop
    """)
    assert "same=1" in out, out
    chunks = int(out.split("chunks=")[1].split()[0])
    assert chunks >= 2, f"expected multiple OUT frames, got {chunks}"


def test_a_client_that_never_opts_in_never_sees_an_out_frame(tmp_path):
    """The one-frame protocol remains the whole contract for existing
    flows: with no __stream request, the progress callback fires at most
    once per command (the single final frame)."""
    out = _tcl(tmp_path, """
        set ::frames 0
        buda::onprogress {apply {{chunk} { incr ::frames }}}
        buda::dump_messages
        puts "frames=$::frames"
        buda::stop
    """)
    assert "frames=1" in out, out


def test_async_runs_the_command_and_fires_the_done_callback(tmp_path):
    """buda::async returns immediately; the -done callback gets the final
    status and the full output as ARGUMENTS (there is no caller on the
    stack to catch a raise), and buda::wait returns the status."""
    out = _tcl(tmp_path, """
        buda::def_layer 4 M4 H TOP 30
        buda::async dump_messages -done {apply {{status output} {
            puts "done=$status len=[expr {[string length $output] > 0}]"
        }}}
        puts "returned-immediately=[buda::running]"
        set st [buda::wait]
        puts "wait=$st"
        # ...and the session is still alive for sync commands after.
        puts "blocks=[buda::query blocks]"
        buda::stop
    """)
    assert "returned-immediately=1" in out, out
    assert "done=OK len=1" in out, out
    assert "wait=OK" in out, out
    assert "blocks=0" in out, out


def test_a_sync_command_while_async_is_in_flight_is_refused(tmp_path):
    out = _tcl(tmp_path, """
        buda::async dump_messages
        if {[catch {buda::query blocks} err]} { puts "refused=1" }
        buda::wait
        buda::stop
    """)
    assert "refused=1" in out, out


@pytest.mark.skipif(os.name != "posix", reason="cancel needs POSIX kill")
def test_cancel_interrupts_a_long_command_and_the_session_survives(tmp_path):
    """The cancel contract: SIGINT lands at a Python bytecode boundary, the
    command fails as an ordinary ERR (KeyboardInterrupt in its output), and
    the session keeps the state the command had committed — some of the
    blocks, queryable afterwards."""
    # A genuinely long PURE-PYTHON command: source a script of 150k
    # add_block lines (the CLI loops per line, so the interrupt lands
    # between them).
    big = tmp_path / "big.buda"
    with big.open("w") as f:
        for i in range(150000):
            x = (i % 1000) * 20
            y = (i // 1000) * 20
            f.write(f"add_block b{i} {x} {y} {x + 10} {y + 10}\n")
    out = _tcl(tmp_path, """
        buda::async {source big.buda} -done {apply {{status output} {
            puts "done=$status"
            puts "interrupted=[string match *KeyboardInterrupt* $output]"
        }}}
        after 400 { buda::cancel }
        buda::wait
        set n [buda::query blocks]
        puts "alive-with-partial-state=[expr {$n > 0 && $n < 150000}]"
        buda::stop
    """)
    assert "done=ERR" in out, out
    assert "interrupted=1" in out, out
    assert "alive-with-partial-state=1" in out, out
