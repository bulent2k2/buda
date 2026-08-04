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

"""EXECUTE the Scala front end's display geometry and diff it against the
authority — the Scala twin of test_web_js_port.py.

`web/src/main/scala/buda/web/render/DisplayGeom.scala` was not compiled, let
alone run, so it was kept in step with `viz_common.snap_endpoint_extents` by
review alone.  Issue #554 is why that is not good enough: the fix landed in the
Python renderer, BOTH mirrors stayed broken, and the parity suite stayed green.

The REAL DisplayGeom.scala is compiled UNMODIFIED against a small JVM stand-in
for the sliver of scala.scalajs it uses (`web/src/test/jvm/JsShim.scala`) and
run over the same b44 fixtures the Python and JS tests use.

WHAT THIS DOES NOT COVER, stated plainly: the linked Scala.js artifact.  A
divergence arising from Scala.js semantics rather than from the source — JS
numerics being uniformly Double, say — is invisible to a JVM run.  Covering that
needs sbt + the scalajs plugin + a JS runtime, i.e. a whole toolchain for one
72-line file.  This buys the logic (the part that has actually drifted) cheaply;
it does not claim to buy the link step.

OPTIONAL BY DESIGN — `BUDA_SCALA_PORT_TEST`:

    unset / "auto"   run when a Scala compiler is available, else SKIP.
    "1" / "on"       REQUIRED: a missing toolchain is a failure, not a skip.
    "0" / "off"      skip unconditionally, whatever is installed.

The auto skip reason is deliberately distinct from the CI workflow's
dependency-skip patterns ("could not import", "JS port unexecuted"), so a gate
with no Scala toolchain stays green rather than failing on an optional test.
Turn it into a hard requirement with BUDA_SCALA_PORT_TEST=1 on a host that has
one.

PROVISIONING a toolchain (no sbt needed — the compiler jars are enough):

    mvn dependency:get -Dartifact=org.scala-lang:scala3-compiler_3:3.3.4

which populates ~/.m2 where this test looks.  Or point BUDA_SCALA_CP at a
classpath containing scala3-compiler_3 and its dependencies.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_web_displaygeom import (  # noqa: E402  — one fixture, one authority
    _b44_candidates, _draw_pass_b, _perp)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SOURCES = [
    os.path.join(_ROOT, "web", "src", "test", "jvm", "JsShim.scala"),
    os.path.join(_ROOT, "web", "src", "main", "scala", "buda", "web", "render",
                 "DisplayGeom.scala"),
    os.path.join(_ROOT, "web", "src", "test", "jvm", "Harness.scala"),
]

_MODE = os.environ.get("BUDA_SCALA_PORT_TEST", "auto").strip().lower()
_REQUIRED = _MODE in ("1", "on", "yes", "true", "require")
_DISABLED = _MODE in ("0", "off", "no", "false")

# Must NOT match the CI workflow's dependency-skip grep: an absent Scala
# toolchain is an accepted configuration, unlike an absent pip package.
_NO_SCALA = ("Scala toolchain absent (optional) — "
             "set BUDA_SCALA_PORT_TEST=1 to require it")


def scala_version():
    """The Scala version the PROJECT builds with, read from web/build.sbt.

    Single source of truth on purpose: if the port is moved to a new Scala and
    the cache still holds only the old one, this test must skip (or fail in
    required mode) rather than quietly compile the port under a compiler the
    project does not use.
    """
    sbt = os.path.join(_ROOT, "web", "build.sbt")
    with open(sbt) as fh:
        m = re.search(r'scalaVersion\s*:=\s*"([^"]+)"', fh.read())
    assert m, f"scalaVersion not found in {sbt}"
    return m.group(1)


# Exactly the toolchain scala3-compiler needs — NOT "every jar in the cache".
# Globbing the whole cache put ~70 unrelated jars (maven's own doxia/velocity/
# plexus/commons tree) on the compiler classpath; it happened to work on a
# single-version machine and would pick an arbitrary dotty.tools.dotc.Main on a
# developer cache holding several Scala versions (review #578).
_PINNED = ("scala3-compiler_3", "scala3-library_3", "scala3-interfaces",
           "tasty-core_3")                      # must match scala_version()
_SUPPORT = ("scala-library-2.13", "scala-asm-", "compiler-interface-",
            "util-interface-")                  # version-independent


def _scala_classpath():
    """A classpath holding ONE coherent Scala toolchain, or None.

    BUDA_SCALA_CP wins verbatim; otherwise assemble it from the pinned version's
    jars wherever `mvn dependency:get` or coursier cached them.  Returns None
    rather than raising, so the caller decides whether absence is a skip or a
    failure.
    """
    explicit = os.environ.get("BUDA_SCALA_CP")
    if explicit:
        return explicit
    ver = scala_version()
    roots = [os.path.expanduser("~/.m2/repository"),
             os.path.expanduser("~/.cache/coursier")]
    jars = []
    for root in roots:
        if os.path.isdir(root):
            jars += glob.glob(os.path.join(root, "**", "*.jar"), recursive=True)
    picked, seen = [], set()

    def take(basename_prefix):
        for j in sorted(jars):
            base = os.path.basename(j)
            if base.startswith(basename_prefix) and base not in seen:
                seen.add(base)
                picked.append(j)
                return True
        return False

    for art in _PINNED:                     # exact version — no substitutions
        if not take(f"{art}-{ver}.jar"):
            return None
    for art in _SUPPORT:                    # any version of the support jars
        take(art)
    return os.pathsep.join(picked)


_CP = None if _DISABLED else _scala_classpath()
_JAVA = shutil.which("java")


def _skip_reason():
    if _DISABLED:
        return "BUDA_SCALA_PORT_TEST=off"
    if _JAVA is None or _CP is None:
        return _NO_SCALA
    return None


def _compiled(tmp_path_factory):
    """Compile the shim + the real DisplayGeom + the harness. Cached per run."""
    out = tmp_path_factory.mktemp("scala_out")
    cmd = [_JAVA, "-cp", _CP, "dotty.tools.dotc.Main",
           "-classpath", _CP, "-d", str(out)] + _SOURCES
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, (
        "scalac failed — the Scala port does not COMPILE:\n"
        + p.stdout + p.stderr)
    return str(out)


@pytest.fixture(scope="module")
def scala_classes(tmp_path_factory):
    reason = _skip_reason()
    if reason and not _REQUIRED:
        pytest.skip(reason)
    if reason:                       # required mode: absence is a failure
        raise AssertionError(
            f"BUDA_SCALA_PORT_TEST requires the Scala port to run, but: {reason}")
    return _compiled(tmp_path_factory)


def _fixture_text(analyses):
    """The harness's line protocol (see web/src/test/jvm/Harness.scala)."""
    def cell(v):
        return "NULL" if v is None else repr(float(v))
    out = []
    for an in analyses:
        out.append("CAND %d" % len(an))
        for a in an:
            conns = [c for c in a["conns"] if c["kind"] == "SEG"]
            out.append("SEG %s %s %s %s %s %d" % (
                cell(a["perp_lo"]), cell(a["perp_hi"]), cell(a["perp_pos"]),
                cell(a["along_lo"]), cell(a["along_hi"]), len(conns)))
            for c in conns:
                out.append("CONN SEG %d %s" % (c["seg_idx"], cell(c["at_pos"])))
    return "\n".join(out) + "\n"


def _run_scala(classes, analyses, tmp_path):
    fx = tmp_path / "fixtures.txt"
    fx.write_text(_fixture_text(analyses))
    p = subprocess.run([_JAVA, "-cp", _CP + os.pathsep + classes,
                        "buda.web.test.Harness", str(fx)],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"harness failed:\n{p.stdout}\n{p.stderr}"
    got = {}
    for ln in p.stdout.splitlines():
        if not ln.startswith("OUT "):
            continue
        _, ci, si, perp, alo, ahi = ln.split()
        got[(int(ci), int(si))] = (float(perp), float(alo), float(ahi))
    return got


@pytest.mark.mid
def test_scala_displaygeom_matches_the_authority(scala_classes, tmp_path):
    """DisplayGeom.compute must agree with snap_endpoint_extents on every b44
    candidate — the exact fixtures that caught #554."""
    cands = _b44_candidates()
    analyses = [c["analysis"] for c in cands]
    got = _run_scala(scala_classes, analyses, tmp_path)
    assert got, "harness produced no output"
    for ci, an in enumerate(analyses):
        want_lo, want_hi = _draw_pass_b(an)
        want_perp = _perp(an)
        for si in range(len(an)):
            g = got.get((ci, si))
            assert g is not None, (ci, si, "missing from harness output")
            assert g[0] == pytest.approx(want_perp[si]), (ci, si, "perp")
            assert g[1] == pytest.approx(want_lo[si]), (ci, si, "along_lo")
            assert g[2] == pytest.approx(want_hi[si]), (ci, si, "along_hi")


@pytest.mark.mid
def test_scala_port_pins_the_known_b44_extents(scala_classes, tmp_path):
    """The same concrete numbers the Python and JS suites pin, asserted against
    the Scala.  (22,0) is the only b44 segment with TWO partners incident to one
    endpoint, so it alone pins 'take the extreme' (issue #554) — a revert to
    last-match-wins draws its hi end at 2960 instead of 3810."""
    cands = _b44_candidates()
    got = _run_scala(scala_classes, [c["analysis"] for c in cands], tmp_path)
    for (ci, si), (elo, ehi) in {(18, 1): (700, 1570),
                                 (6, 2): (1200, 1960),
                                 (22, 0): (2700, 3810)}.items():
        _, alo, ahi = got[(ci - 1, si)]
        assert abs(alo - elo) <= 1 and abs(ahi - ehi) <= 1, (
            ci, si, (alo, ahi), (elo, ehi))


def test_scala_port_mode_switch_is_explicit():
    """The three modes must stay distinguishable, and the auto-skip reason must
    not collide with CI's dependency-skip grep — otherwise a gate without a
    Scala toolchain fails on an OPTIONAL test."""
    assert "could not import" not in _NO_SCALA
    assert "JS port unexecuted" not in _NO_SCALA
    for v, req, dis in [("1", True, False), ("on", True, False),
                        ("0", False, True), ("off", False, True),
                        ("auto", False, False), ("", False, False)]:
        m = v.strip().lower()
        assert (m in ("1", "on", "yes", "true", "require")) is req, v
        assert (m in ("0", "off", "no", "false")) is dis, v
