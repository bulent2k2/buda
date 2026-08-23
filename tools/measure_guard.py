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
"""Guards against a measurement that silently measured the wrong thing.

Every failure this exists to catch has ONE shape: the comparison degenerated,
and the tool reported success.  There is no exception, no traceback and no
missing output — just a number that is wrong, and wrong in a way that reads as
a finding.  A stale baseline reports "no difference"; so does a mis-run A/B;
so does a build that no longer matches its source.  "No difference" is exactly
what a null result looks like, which is why these get believed.

Four instances, all measured on this repo (docs/internal/measurement_hazards.md):

  1. `--vs main` resolves the LOCAL ref.  A topic-branch workflow never checks
     main out, so it sat 44 commits stale and re-measured the old base while
     both sweeps succeeded.  `qor_corpus.py` already advises on this one.
  2. A regenerated `qor/qor_table.md` whose checked-in stamp was two merges
     behind: 27 of 49 rows moved and the header went `39 clean -> 37 clean`,
     which reads as the branch causing regressions.  3 rows were the branch's.
  3. A rebase that picked up C++ changes with no rebuild, so the sweep measured
     a binary that did not match its source.  Nothing warned; two unrelated
     tests failed and looked like a real regression on main.
  4. An env A/B run as `=1` versus unset AFTER the default flipped, i.e. two
     identical sides, reporting that the change does nothing.

(1) had a guard and it worked.  The rest had none.  This module adds them.
"""
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Set by a harness's --allow-stale-build.  Module-level so the gate can live at
# the single choke point (`sweep`) rather than at each CLI branch: --candidates
# --quantify also sweeps, and a per-branch gate had already missed it.
ALLOW_STALE = False
_CHECKED = False

# Only these need a rebuild.  The Python layer is imported from source, so a
# .py edit is live and must NOT trip the guard -- a guard that cries wolf on
# every Python edit is one people learn to pass `--allow-stale-build` to.
_NATIVE_SUFFIXES = {".cpp", ".h", ".hpp", ".c", ".cc", ".cxx"}
_EXTRA_BUILD_INPUTS = ("CMakeLists.txt",)


def _newest_native_source():
    """(mtime, path) of the most recently touched compiled input."""
    newest = (0.0, None)
    for base in (_ROOT / "src",):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in _NATIVE_SUFFIXES and p.is_file():
                m = p.stat().st_mtime
                if m > newest[0]:
                    newest = (m, p)
    for name in _EXTRA_BUILD_INPUTS:
        p = _ROOT / name
        if p.is_file() and p.stat().st_mtime > newest[0]:
            newest = (p.stat().st_mtime, p)
    return newest


# Every extension layout this repo supports.  A glob that misses one does not
# degrade gracefully -- it reports "no build" on a machine that has a perfectly
# good one, and with a fail-closed gate that is a CI outage on a platform the
# author cannot run (Codex #829 P1: MSVC emits build/Release/*.pyd and the
# Cygwin build names its extensions *.dll, so a build/*.so glob found nothing
# after a SUCCESSFUL build, and every test calling qor_corpus.sweep would have
# exited 2).  Kept in one list because bin/activate.ps1 and bin/bb.ps1 already
# know these paths and drifting from them is how the next platform breaks.
_BUILD_DIRS = ("build", "build/Release", "build/Debug", "build/RelWithDebInfo")
_EXT_SUFFIXES = ("*.so", "*.pyd", "*.dll")


def _extension_candidates():
    for d in _BUILD_DIRS:
        base = _ROOT / d
        if not base.is_dir():
            continue
        for pat in _EXT_SUFFIXES:
            for p in base.glob(pat):
                # buda_core is a shared LIBRARY, not a Python extension, but it
                # is a build product on the same cadence, so it answers the
                # "did a build run" question just as well.
                if p.is_file():
                    yield p


def _newest_extension():
    """(mtime, path) of the most recently linked build artifact.

    NEWEST, not oldest.  The question is "did a build run after the last source
    edit", and only the newest artifact answers it: `buda` and `buda_db` are
    separate targets over different sources, so an incremental build relinks
    just the one that needed it and legitimately leaves the other untouched.
    Judging by the OLDEST made every ordinary incremental build look stale
    (measured: `buda_db.so` reported 97.8 min behind a `detailed_nuts.cpp` it
    does not depend on, immediately after a successful `bin/bb`) -- and a guard
    that cries wolf is one people learn to pass --allow-stale-build to, which
    would leave the real hazard unguarded.
    """
    mods = list(_extension_candidates())
    if not mods:
        return (None, None)
    newest = max(mods, key=lambda p: p.stat().st_mtime)
    return (newest.stat().st_mtime, newest)


def gate(stream=sys.stderr):
    """The sweep-time gate: check once per process, honouring ALLOW_STALE."""
    global _CHECKED
    if _CHECKED:
        return True
    _CHECKED = True
    return check_build_fresh(strict=not ALLOW_STALE, stream=stream)


def check_build_fresh(strict=True, stream=sys.stderr):
    """Refuse (or warn) when the built engine predates its C++ sources.

    Returns True when the build is usable.  With `strict`, a stale build exits
    non-zero instead: a measurement harness that reports numbers from a binary
    which does not match the source produces a WRONG ANSWER, not an error, and
    the repo's rule for that is to fail loudly rather than carry on.

    Deliberately mtime-based rather than a hash of the tree: this must be cheap
    enough to run before every sweep, and the failure it guards (a rebase or a
    branch switch that touched C++, then no rebuild) always moves mtimes.
    """
    src_m, src_p = _newest_native_source()
    so_m, so_p = _newest_extension()
    if so_m is None:
        # FAIL OPEN, deliberately, and never fatally.  "I found no build" has
        # two causes that look identical from here: there really is none (the
        # flows will then fail loudly on their own, which is a better error
        # than this one), or the layout is one this list does not know about.
        # The second is the author's bug, and making it fatal turns that bug
        # into a CI outage on a platform the author cannot run -- exactly what
        # #829 P1 caught before it shipped.  A guard must degrade to SILENCE
        # when it cannot tell, and refuse only when it has actually measured a
        # problem.
        print("[measure] note: no build artifact found under "
              f"{', '.join(_BUILD_DIRS)} — skipping the freshness check.",
              file=stream)
        return True
    if src_m <= so_m:
        return True

    rel_src = src_p.relative_to(_ROOT) if src_p else "?"
    behind = src_m - so_m
    print(f"\n[measure] STALE BUILD: {so_p.name} is {behind/60:.1f} min older "
          f"than {rel_src}.", file=stream)
    print("[measure] The sweep would measure a binary that does not match the "
          "source —", file=stream)
    print("[measure] a wrong number, not an error.  Run `bin/bb` first.",
          file=stream)
    print("[measure] (A rebase or branch switch that touched C++ is the usual "
          "cause; nothing", file=stream)
    print("[measure]  else in the toolchain notices.)  Override with "
          "--allow-stale-build.\n", file=stream)
    if strict:
        sys.exit(2)
    return False


def git_out(*args):
    try:
        r = subprocess.run(("git",) + args, cwd=_ROOT,
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def describe_drift(stamp_commit, stream=sys.stderr):
    """Advise when a regenerated artifact's diff will carry OTHER people's work.

    A checked-in snapshot records the commit it was generated at.  Regenerating
    it on a branch whose base has moved makes the diff (their movement + yours),
    and nothing in the diff says which is which — hazard (2) above.  Naming the
    span is enough: a reader who knows 24 of 27 changed rows are not the
    branch's will not read them as the branch's.
    """
    if not stamp_commit:
        return
    head = git_out("rev-parse", "--short", "HEAD")
    if not head or head.startswith(stamp_commit) or stamp_commit.startswith(head):
        return
    span = git_out("rev-list", "--count", f"{stamp_commit}..HEAD")
    if not span or span == "0":
        return
    print(f"\n[measure] NOTE: the snapshot being replaced was generated at "
          f"{stamp_commit}; HEAD is", file=stream)
    print(f"[measure] {head}, {span} commit(s) later.  The diff therefore "
          f"carries every change in", file=stream)
    print("[measure] that span, not only yours.  To attribute your own effect, "
          "regenerate once", file=stream)
    print("[measure] with your change disabled and diff the two runs against "
          "each other.\n", file=stream)


def warn_if_identical(base_rows, branch_rows, keys, stream=sys.stderr):
    """Advise when an A/B's two sides are indistinguishable.

    This is the catch-all: whatever made the comparison degenerate — the same
    commit twice, a no-op env override, a stale build shared by both sides —
    lands here as "every metric identical on every flow".  That is a legitimate
    outcome for a byte-identical change, so it is an ADVISORY, not an error;
    the point is that it should never pass unremarked, because the reading
    "my change does nothing" and "my experiment did nothing" look the same.
    """
    if not base_rows or not branch_rows:
        return
    b = {r.get("flow"): r for r in base_rows}
    n = {r.get("flow"): r for r in branch_rows}
    common = set(b) & set(n)
    if not common:
        return
    for f in common:
        for k in keys:
            if b[f].get(k) != n[f].get(k):
                return
    print(f"\n[measure] NOTE: the two runs are IDENTICAL on every metric across "
          f"all {len(common)} flows.", file=stream)
    print("[measure] That is the right answer for a change that is byte-"
          "identical by design.  If you", file=stream)
    print("[measure] expected a difference, the comparison itself is the "
          "suspect: the same commit", file=stream)
    print("[measure] measured twice, an env override that no longer changes "
          "anything (check it", file=stream)
    print("[measure] against the CURRENT default), or one build serving both "
          "sides.\n", file=stream)


def add_build_flag(ap):
    """Register --allow-stale-build on a harness's parser."""
    ap.add_argument("--allow-stale-build", action="store_true",
                    help="measure even when the built engine predates its C++ "
                         "sources (default: refuse — a binary that does not "
                         "match the source yields a wrong number, not an error)")
