#!/usr/bin/env python3
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
"""Turn a Tcl flow into the `.buda` script it actually ran — by running it.

The mirror of `tools/buda2tcl.py`, and deliberately NOT built the same way,
because the two directions are not the same problem.

**Why this is not a parser.**  `.buda` is a command list, so translating it
to Tcl is a source-to-source rewrite — `buda2tcl.py` reads the text and
never runs anything.  Tcl is a general programming language, so the reverse
has no source-to-source form: what commands a flow issues is not a property
of its TEXT.

    tclsh flow/tcl/array.tcl 3 2      # 18 bundles
    tclsh flow/tcl/array.tcl 4 5      # 62 bundles, same file

The buses come out of nested loops over `$argv`; the bundle count, the block
coordinates and the net names are all computed.  Add `expr`, `catch`, a
`source` chosen at runtime, a value read from a file, and a static
translator would have to be a Tcl interpreter.  There is already an
excellent one, so this uses it.

**What makes that cheap.**  By the time a command reaches the engine it IS a
flat `.buda` line — `buda::add_bus "bh_${R}_${C}\\[4\\]" $t/l_0_0.out …`
arrives as `add_bus bh_0_1[4] t_0_1/l_0_0.out …`.  The translation has
already happened; it only needs writing down.  `BUDA_RECORD=<path>` does
that at the one choke point every driver passes through
(`BudaSession.do_command`), so the same mechanism records a Tcl flow, a
`.buda` run, or the web server — see `_record` in `src/buda_cli.py`.

**What you get is a FLATTENING, and that is the honest description.**  One
concrete design, straight-line: `array.tcl 4 5` becomes the 4x5 design's
script with the parameterization gone, `source`d files inlined, loops
unrolled, `if` branches resolved to whichever way they went.  That is what
"the `.buda` for this Tcl run" can mean.  What it cannot do is round-trip
back to a program.

Usage:
  tools/tcl2buda.py flow/tcl/array.tcl 3 2 -o /tmp/array_3x2.buda
  tools/tcl2buda.py flow/tcl/array.tcl -o /tmp/a.buda --verify
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# The numbers a replay has to reproduce.  Wirelength is the fingerprint: two
# runs can agree on the small integers by luck and cannot agree on this.
_METRICS = (
    ("detailed WL", re.compile(r"total detailed WL = (\d+)")),
    ("bit-wires", re.compile(r"total detailed WL = \d+ over \d+ bundle\(s\) / (\d+)")),
    ("abstract WL", re.compile(r"total abstract WL = (\d+)")),
    ("overlaps", re.compile(r"Track overlaps: (\d+)")),
)


def _metrics(text):
    """The comparable numbers in a run's output (last occurrence wins — a
    healing flow reports more than once and the LAST one is the result)."""
    out = {}
    for name, rx in _METRICS:
        hits = rx.findall(text)
        if hits:
            out[name] = hits[-1]
    return out


def record(script, args, out_path, tclsh="tclsh"):
    """Run the Tcl flow with the recorder armed.  Returns its CompletedProcess.

    The destination is REMOVED first, so its existence afterwards proves THIS
    run wrote it.  Without that, a flow that dies before `buda::start` records
    nothing, the previous run's file is still sitting there, and the caller
    counts its commands and reports an old design as the new result — a
    routine retry silently answering with stale data (Codex P1 on #701).
    """
    out_path = os.path.abspath(out_path)
    try:
        os.remove(out_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        # Refuse rather than proceed: we could no longer tell a fresh
        # recording from the file already there, which is the whole point.
        raise SystemExit(f"tcl2buda: cannot clear {out_path}: {e}")
    env = dict(os.environ)
    env["BUDA_RECORD"] = out_path
    env["BUDA_RECORD_NOTE"] = " ".join([os.path.relpath(script, _ROOT)] + list(args))
    return subprocess.run([tclsh, script, *args], capture_output=True,
                          encoding="utf-8", errors="replace", env=env)


def replay(buda_path):
    """Run the recorded script through the CLI, as any `.buda` is run.

    Returns (CompletedProcess, text) where `text` is stdout PLUS the run's
    flow log.  The log is not a nicety: the CLI prints a one-line, TRUNCATED
    summary per command to the terminal and sends the full report to the log,
    so scanning stdout alone reads `… / 1` out of a clipped wirelength line
    and calls a byte-identical replay a mismatch.  (It did, before this.)
    """
    p = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "src", "buda_cli.py"),
         "--no-viz", buda_path],
        capture_output=True, encoding="utf-8", errors="replace", cwd=_ROOT,
        env=dict(os.environ, MPLBACKEND="Agg"))
    text = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"Full per-command detail → (\S+)", text)
    if m:
        try:
            with open(m.group(1), encoding="utf-8", errors="replace") as fh:
                text += fh.read()
        except OSError:
            pass
    return p, text


def _recorded_cwd(path):
    """The `# cwd:` the recorder stamped, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if not ln.startswith("#"):
                    break
                if ln.startswith("# cwd: "):
                    return ln[len("# cwd: "):].strip()
    except OSError:
        pass
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="the Tcl flow to run")
    ap.add_argument("args", nargs="*", help="arguments passed to it verbatim")
    ap.add_argument("-o", "--out", required=True, help="the .buda to write")
    ap.add_argument("--verify", action="store_true",
                    help="replay the recording through the CLI and compare "
                         "its measured result against the Tcl run's")
    ap.add_argument("--tclsh", default="tclsh")
    a = ap.parse_args(argv)

    if not shutil.which(a.tclsh):
        print(f"no {a.tclsh} on this host", file=sys.stderr)
        return 2
    if not os.path.exists(a.script):
        print(f"no such script: {a.script}", file=sys.stderr)
        return 2

    # One absolute path from here on.  `record` resolves against the CALLER's
    # directory while `replay` runs with cwd=_ROOT, so a relative `-o` used by
    # both would name two different files (Codex P2 on #701).
    out = os.path.abspath(a.out)
    p = record(a.script, a.args, out, a.tclsh)
    if not os.path.exists(out):
        # No recording at all means the flow never reached the engine — a
        # Tcl error before `buda::start`, most likely.  Its own output is the
        # explanation, so pass it through rather than paraphrasing.
        print(f"tcl2buda: {a.script} recorded nothing (rc={p.returncode})",
              file=sys.stderr)
        sys.stderr.write((p.stderr or p.stdout or "")[-2000:])
        return 1

    n = sum(1 for ln in open(out)
            if ln.strip() and not ln.startswith("#"))
    print(f"tcl2buda: {n} command(s) -> {out}  (tcl exit {p.returncode})")
    # A non-zero exit is NOT an error here: `array.tcl` exits 1 on a dirty
    # design, and the recording of a dirty run is exactly as valid.
    if p.returncode != 0:
        print("  (the Tcl flow exited non-zero — its own verdict, not a "
              "recording failure)")

    if not a.verify:
        return 0

    r, replay_text = replay(out)
    # The replay RAN is checked first and unconditionally.  Comparing metrics
    # only when both sides have them is fine; returning 0 because there was
    # nothing to compare is not — that reported success for a replay which had
    # died on its third command.
    if r.returncode != 0:
        print(f"  verify: the replay FAILED (exit {r.returncode})")
        tail = [ln for ln in replay_text.splitlines()
                if "Error" in ln or "error" in ln][-3:]
        for ln in tail:
            print(f"    {ln.strip()[:160]}")
        cwd = _recorded_cwd(out)
        outdir = os.path.dirname(out)
        if cwd and os.path.abspath(cwd) != outdir:
            print(f"    A `.buda` resolves relative paths against its OWN "
                  f"directory, and this flow ran in\n      {cwd}\n"
                  f"    Write the recording there (-o {os.path.join(cwd, os.path.basename(out))}) "
                  f"so its paths resolve.")
        return 1
    tcl_m = _metrics((p.stdout or "") + (p.stderr or ""))
    buda_m = _metrics(replay_text)
    if not tcl_m:
        # The replay is known good by here; there is simply nothing on the
        # Tcl side to compare it WITH (a `-echo 0` flow keeps its output in
        # `buda::output`).  Say which of the two it is.
        print("  verify: the replay ran clean, but the Tcl run printed no "
              "comparable metric (an `-echo 0` flow keeps its output in "
              "buda::output) — the replay is unchecked against it.\n"
              "          Run the flow with echo on, or give it a "
              "`report_wirelength`, to get a comparison.")
        return 0
    shared = sorted(set(tcl_m) & set(buda_m))
    diff = {k: (tcl_m[k], buda_m[k]) for k in shared if tcl_m[k] != buda_m[k]}
    missing = sorted(set(tcl_m) - set(buda_m))
    for k in shared:
        print(f"  verify: {k:<12} {tcl_m[k]:>12}  (tcl)  "
              f"{buda_m[k]:>12}  (replay)"
              + ("   ← DIFFERS" if k in diff else ""))
    if missing:
        print(f"  verify: the replay never reported: {', '.join(missing)}")
    if diff or missing:
        print("  verify: FAILED — the replay is not the same design")
        return 1
    print(f"  verify: identical on {len(shared)} metric(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
