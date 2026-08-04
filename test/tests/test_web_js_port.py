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

"""EXECUTE the JS front end's display geometry and diff it against the authority.

`test_web_displaygeom.py` pins the authoritative *behaviour*
(`viz_common.snap_endpoint_extents`, what draw.py Pass B uses) but never runs a
line of JavaScript — so the browser port was kept in step by hand and by review.
That is not a hypothetical gap: issue #554 is the demonstration.  The fix landed
in the Python renderer, BOTH mirrors stayed broken, and the parity suite stayed
green throughout.

This test closes it for the JS half: it extracts `computeDisplay` from
src/web/static/index.html, runs it under node over the SAME b44 fixtures the
Python test builds, and asserts the outputs agree elementwise.  A JS-only edit
that changes the rule now fails here with numbers.

The Scala port (web/.../render/DisplayGeom.scala) is still unexecuted — it needs
a JDK+sbt toolchain, tracked in docs/internal/opens_ci.md.

Deliberately NOT a browser test: `computeDisplay` is pure over the serialized
analysis, so node alone covers the logic that has actually drifted.  Rendering
(SVG element creation) is a separate concern and is not what #554 was about.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_web_displaygeom import (  # noqa: E402  — one fixture, one authority
    _b44_candidates, _draw_pass_b, _perp)

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_INDEX_HTML = os.path.join(_ROOT, "src", "web", "static", "index.html")

# The skip reason CI greps for.  A missing node must NOT quietly shrink the
# suite the way the absent src/web/requirements.txt once removed 28 tests while
# the gate still reported success (see docs/internal/ci.md).
_NO_NODE = "node unavailable - JS port unexecuted"

_node = shutil.which("node") or shutil.which("nodejs")


def extract_js_function(name, source):
    """Return the source text of a top-level `function <name>(...) {...}`.

    Brace-matched from the declaration to its closing brace, so it does not
    depend on the function staying last in the file or on any marker comment
    that a future edit could silently drop.
    """
    m = re.search(r"^function\s+%s\s*\(" % re.escape(name), source, re.M)
    assert m, f"{name}() not found in {_INDEX_HTML} — was it renamed?"
    i = source.index("{", m.start())
    depth, j = 0, i
    while j < len(source):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start():j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces while extracting {name}()")


def _run_js(analyses):
    """Run the extracted computeDisplay over each analysis under node."""
    with open(_INDEX_HTML) as fh:
        fn_src = extract_js_function("computeDisplay", fh.read())
    driver = fn_src + """
const input = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify(input.map(computeDisplay)));
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        js = os.path.join(td, "driver.js")
        data = os.path.join(td, "in.json")
        with open(js, "w") as fh:
            fh.write(driver)
        with open(data, "w") as fh:
            json.dump(analyses, fh)
        p = subprocess.run([_node, js, data], capture_output=True, text=True)
    assert p.returncode == 0, f"node failed:\n{p.stderr}"
    return json.loads(p.stdout)


@pytest.mark.skipif(_node is None, reason=_NO_NODE)
def test_js_computedisplay_matches_the_authority():
    """The browser's computeDisplay must agree with snap_endpoint_extents on
    every b44 candidate — the exact fixtures that caught #554."""
    cands = _b44_candidates()
    analyses = [c["analysis"] for c in cands]
    js = _run_js(analyses)
    assert len(js) == len(analyses)
    for ci, (an, got) in enumerate(zip(analyses, js), start=1):
        want_lo, want_hi = _draw_pass_b(an)
        want_perp = _perp(an)
        assert got["perp"] == pytest.approx(want_perp), (ci, "perp")
        assert got["alo"] == pytest.approx(want_lo), (
            ci, "along_lo", got["alo"], want_lo)
        assert got["ahi"] == pytest.approx(want_hi), (
            ci, "along_hi", got["ahi"], want_hi)


@pytest.mark.skipif(_node is None, reason=_NO_NODE)
def test_js_port_pins_the_known_b44_extents():
    """The same concrete numbers test_web_displaygeom pins for the Python
    authority, asserted against the JS itself.  (18,1) and (6,2) pin
    'snap to the endpoint-incident partner'; (22,0) is the only b44 segment with
    TWO partners on one endpoint, so it alone pins 'take the extreme' — a JS
    revert to last-match-wins draws its hi end at 2960 instead of 3810."""
    cands = _b44_candidates()
    js = _run_js([c["analysis"] for c in cands])
    for (ci, si), (elo, ehi) in {(18, 1): (700, 1570),
                                 (6, 2): (1200, 1960),
                                 (22, 0): (2700, 3810)}.items():
        got = js[ci - 1]
        assert abs(got["alo"][si] - elo) <= 1 and abs(got["ahi"][si] - ehi) <= 1, (
            ci, si, (got["alo"][si], got["ahi"][si]), (elo, ehi))


@pytest.mark.skipif(_node is None, reason=_NO_NODE)
def test_extractor_fails_loudly_on_a_rename():
    """The extractor must not silently pass when the function is gone: a rename
    that made this a no-op would leave the port unguarded while staying green."""
    with pytest.raises(AssertionError, match="not found"):
        extract_js_function("noSuchFunction", "function other(){}\n")
    # ...and it must brace-match, not stop at the first '}'.
    src = "function f(a) {\n  if (a) { return 1; }\n  return 2;\n}\n"
    assert extract_js_function("f", src).endswith("return 2;\n}")
