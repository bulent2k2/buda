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

"""fix_pin PR 1a: block geometry is read through TWO named resolvers.

`Floorplan::landing_rects/landing_bounds` answer "may a wire attach here" and
`Floorplan::obstruction_rects` answers "what does this block block".  With no
pin fixed they are the raw store, so nothing routes differently; the point of
the split is that PR 1b can narrow ONE of them (landings) without a macro's
body becoming routable on the LOW layers (obstruction).  These tests pin the
two halves of that: the identity today, and that no consumer bypasses the
resolvers, so a site the census names cannot be missed later.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from subprocess_env import buda_env  # noqa: E402

import buda  # noqa: E402

SRC = _ROOT / "src"

# Files whose geometry reads are "may a wire attach here" questions.
LANDING_CONSUMERS = [
    "topology.cpp", "topology_analysis.cpp", "nuts.cpp", "topo_edit.cpp",
    "verify.cpp",
    "buda_cmds/bundling_cmds.py", "buda_session/util.py",
    "viz_explorer/analysis.py",
]
# Files whose reads are "what does this block block / draw".
OBSTRUCTION_CONSUMERS = ["congestion_planner.cpp", "viz_common.py",
                         "web/serialize.py"]

RAW = re.compile(r"\bget_block_(rects|bounds)\(")


def _code_lines(path):
    """Source lines with comments dropped (a comment may name the raw reader)."""
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if "raw-store: resolver" in line:      # the resolvers' own reads
            continue
        code = re.sub(r"//.*|#.*", "", line)
        yield n, code


@pytest.mark.parametrize("rel", LANDING_CONSUMERS)
def test_landing_consumers_do_not_read_the_raw_store(rel):
    bad = [(n, c.strip()) for n, c in _code_lines(SRC / rel)
           if RAW.search(c) and "Floorplan::" not in c
           and not c.strip().startswith("return get_block_")]  # the resolvers
    assert not bad, f"{rel} reads the raw block store, not landing_*: {bad}"


@pytest.mark.parametrize("rel", OBSTRUCTION_CONSUMERS)
def test_obstruction_consumers_never_read_landing_geometry(rel):
    bad = [(n, c.strip()) for n, c in _code_lines(SRC / rel)
           if re.search(r"\blanding_(rects|bounds)\(", c) or RAW.search(c)]
    assert not bad, f"{rel} must read obstruction_rects only: {bad}"


def test_unfixed_blocks_resolve_to_the_raw_store():
    fp = buda.Floorplan()
    fp.add_block("solid", 0, 0, 100, 50)
    fp.add_block_rects("tegb", [(300, 0, 400, 100),
                                (0, 0, 100, 100)])
    for name in ("solid", "tegb"):
        raw = fp.get_block_rects(name)
        assert fp.landing_rects(name) == raw
        assert fp.obstruction_rects(name) == raw
        b, lb = fp.get_block_bounds(name), fp.landing_bounds(name)
        assert (lb.x1, lb.y1, lb.x2, lb.y2) == (b.x1, b.y1, b.x2, b.y2)
    assert fp.landing_rects("solid") == []          # single-rect convention
    assert len(fp.landing_rects("tegb")) == 2


def _run(flow):
    p = subprocess.run([sys.executable, str(SRC / "buda_cli.py"), "--no-viz",
                        str(_ROOT / "flow" / "pin_fix" / flow)],
                       capture_output=True, text=True, env=buda_env(_ROOT),
                       timeout=300)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.parametrize("flow", ["baseline_macro.buda", "baseline_teg.buda"])
def test_baseline_flows_route_clean(flow):
    rc, out = _run(flow)
    assert rc == 0, out[-800:]
    assert "Error:" not in out, out[-800:]
    assert out.count("Success") >= 2, out[-1200:]


def test_multi_rect_classification_reads_the_physical_block():
    """Whether a block IS multi-rect is a fact about its body, not its landings
    (Codex on #978): a fix narrowing `landing_rects` must not flip the
    FEEDTHRU_RELAY exemption, the TEG_OPEN gate or the 2-pin/n-pin choice."""
    v = (SRC / "verify.cpp").read_text()
    assert "fp.obstruction_rects(bname).size() > 1) continue;" in v
    assert "fp.obstruction_rects(bname).size() < 2) continue;" in v
    t = (SRC / "topology.cpp").read_text()
    assert "!floorplan_.obstruction_rects(src_name).empty()" in t
