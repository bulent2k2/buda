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

"""Issue #31 — deterministic stdout ordering under redirect.

When fd 1 is a file (a `> out.log` redirect), C++ `std::cout` output and Python
prints must interleave in CHRONOLOGICAL order.  The fix line-buffers BOTH streams
so neither strands a partial block in a fully-buffered C buffer until program
exit (the macOS symptom in the issue).  Two guards:

  1. Python side — `buda_cli._enable_line_buffered_stdio()` flips `line_buffering`
     on stdout/stderr and tolerates an exotic (non-`reconfigure`-able) stream.
  2. C side — after `import buda`, a newline-terminated C-stdio write (no explicit
     flush) to a redirected fd 1 lands immediately (the line-buffered contract),
     in order with an interleaved Python print.  Meaningful on macOS, where the
     default is fully buffered; on glibc the default already satisfies it, so this
     encodes the cross-platform contract rather than reproducing the macOS-only
     displacement.
"""
import io
import os
import subprocess
import sys
import textwrap

import pytest

import buda_cli


def test_enable_line_buffered_stdio_flips_line_buffering(monkeypatch):
    """The helper turns on line buffering for both standard streams — the case
    that matters is a redirected (block-buffered) TextIOWrapper."""
    out = io.TextIOWrapper(io.BytesIO(), line_buffering=False)
    err = io.TextIOWrapper(io.BytesIO(), line_buffering=False)
    assert out.line_buffering is False and err.line_buffering is False
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    buda_cli._enable_line_buffered_stdio()
    assert out.line_buffering is True
    assert err.line_buffering is True


def test_enable_line_buffered_stdio_tolerates_streams_without_reconfigure(monkeypatch):
    """A stdout replacement lacking `reconfigure` (e.g. the CLI's per-command
    capture, or a bare StringIO) must not raise — the helper degrades quietly."""
    plain = io.StringIO()               # no reconfigure()
    assert not hasattr(plain, "reconfigure")
    monkeypatch.setattr(sys, "stdout", plain)
    monkeypatch.setattr(sys, "stderr", plain)
    buda_cli._enable_line_buffered_stdio()   # must simply return, no exception


def _env_with_pythonpath():
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pp = os.pathsep.join([os.path.join(root, "build"), os.path.join(root, "src")])
    env = dict(os.environ)
    env["PYTHONPATH"] = pp + os.pathsep + env.get("PYTHONPATH", "")
    env["MPLBACKEND"] = "Agg"
    return env


@pytest.mark.skipif(sys.platform == "win32",
                    reason="the setvbuf line-buffering under test is "
                           "DELIBERATELY absent on Windows -- _IOLBF is "
                           "unsupported there and size 0 is a CRT invalid "
                           "parameter that fast-fails the import (see "
                           "bindings.cpp); the probe's ctypes.CDLL(None) is "
                           "POSIX-only besides")
def test_cpp_stdout_line_buffered_after_import_buda(tmp_path):
    """After `import buda`, a `\\n`-terminated C-stdio write with NO explicit
    flush reaches a redirected fd 1 before program exit, ahead of a later Python
    print — the line-buffered ordering contract the fix guarantees on every
    platform (setvbuf(stdout, …, _IOLBF) in bindings.cpp)."""
    log = tmp_path / "out.log"
    # The child redirects its OWN stdout to `log`, imports buda (→ setvbuf),
    # writes a C-stdio line via libc.printf (no symbol lookup — portable), then a
    # Python print, then reads `log` back MID-RUN (before exit flushes anything)
    # and reports what already landed, in order, on stderr.
    child = textwrap.dedent(
        """
        import ctypes, os, sys
        logpath = sys.argv[1]
        fd = os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(fd, 1)                      # fd 1 -> regular file (block-buffered by default)
        import buda                         # triggers setvbuf(stdout, _IOLBF)
        try:
            libc = ctypes.CDLL(None)
        except OSError:
            sys.stderr.write("SKIP no libc\\n"); sys.exit(0)
        libc.printf(b"CPP_LINE\\n")         # C stdio, NO fflush
        print("PY_LINE", flush=True)        # Python, eager flush
        with open(logpath) as fh:           # read our own redirect MID-RUN
            body = fh.read()
        ci, pi = body.find("CPP_LINE"), body.find("PY_LINE")
        sys.stderr.write("RESULT ci=%d pi=%d\\n" % (ci, pi))
        """
    )
    r = subprocess.run([sys.executable, "-c", child, str(log)],
                       capture_output=True, text=True, env=_env_with_pythonpath())
    assert r.returncode == 0, r.stderr
    if "SKIP" in r.stderr:
        import pytest
        pytest.skip("libc unavailable for the C-stdio ordering probe")
    # Parse "RESULT ci=<> pi=<>": both lines present mid-run, C before Python.
    line = [ln for ln in r.stderr.splitlines() if ln.startswith("RESULT")][0]
    ci = int(line.split("ci=")[1].split()[0])
    pi = int(line.split("pi=")[1].split()[0])
    assert ci >= 0, "C++ stdout line stranded in a block buffer (not line-buffered)"
    assert pi >= 0, "Python print did not reach the redirected file"
    assert ci < pi, "C++ output landed out of chronological order (after Python)"
