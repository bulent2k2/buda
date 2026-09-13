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

"""`tools/experiment/e2_abstract_precision.py` -- the E2 driver of the
convergence ladder (docs/internal/convergence_e2.md).

What is guarded is the EXPERIMENT'S CONSTRUCTION, not its verdict: that the
four arms really are one flow differing only in keepout precision, that the
abstractions nest the way the write-up says (a blob covers its bbox covers its
segments; blob and bbox have one keepout per instance-layer, exact has one per
segment), and that the source's block-internal nets are the ones removed.  The
routing numbers are what the doc reports; pinning them here would make an
engine improvement fail this test, so only the ORDERING the claim rests on is
asserted -- none <= exact <= bbox <= blob unplaced -- and it is asserted at
the smallest size the vehicle has, where every arm routes in well under a
second.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOL = _ROOT / "tools/experiment/e2_abstract_precision.py"

pytestmark = pytest.mark.mid


def _cmds(path):
    return [l.strip() for l in Path(path).read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("e2")
    r = subprocess.run([sys.executable, str(_TOOL), "--nq", "2", "--out", str(out), "--dpi", "50"],
                       capture_output=True, text=True, timeout=900, cwd=str(_ROOT))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return out, json.load(open(out / "e2_summary.json")), r.stdout


def test_the_four_arms_are_one_flow_apart_from_the_keepouts(run):
    out, _, _ = run
    arms = {a: _cmds(out / f"soc2_{a}.buda") for a in ("none", "blob", "bbox", "exact")}
    strip = {a: [c for c in cmds if not c.startswith("add_keepout")] for a, cmds in arms.items()}
    for a in ("blob", "bbox", "exact"):
        assert strip[a] == strip["none"], f"arm {a} differs from 'none' beyond its keepouts"
    # the tail is fixed and healerless
    tail = strip["none"][-8:]
    assert tail[0].startswith("run_hier_bundler") and tail[-1] == "report_wirelength"
    assert not any(c.split()[0] in ("negotiate_congestion", "ripup_reroute") for c in strip["none"])
    # the marks are cleared right after the alignment nudge, never before
    cmds = strip["none"]
    i = cmds.index("align_bottom_up")
    assert cmds[i + 1] == "set_bottom_up * off"
    assert "set_bottom_up *" in cmds[:i]
    assert not any(c.startswith("check_template_tracks") for c in cmds)


def test_the_block_internal_buses_are_the_ones_removed(run):
    out, _, stdout = run
    src = _cmds(out / "soc2_source.buda")
    arm = _cmds(out / "soc2_none.buda")
    src_buses = [c for c in src if c.startswith("add_bus")]
    arm_buses = [c for c in arm if c.startswith("add_bus")]
    m = re.search(r"NQ=2: (\d+) block-internal nets in (\d+) locked bundles", stdout)
    assert m, stdout[-2000:]
    n_nets = int(m.group(1))
    removed = set(src_buses) - set(arm_buses)
    assert removed and set(arm_buses) <= set(src_buses)
    # every removed bus is block-internal (its endpoints share one cluster
    # subtree), and the bits removed account for the locked nets
    bits = 0
    for line in removed:
        tok = line.split()
        drv, rcv = tok[2], tok[3]
        assert drv.split("/")[:3] == rcv.split("/")[:3] or drv.split("/")[0] == rcv.split("/")[0], line
        bits += int(re.search(r"\[(\d+)\]", tok[1]).group(1))
    assert bits == n_nets, (bits, n_nets)


def test_the_abstractions_nest(run):
    out, summary, _ = run
    rows = {r["arm"]: r for r in summary["rows"] if r["nq"] == 2}
    assert rows["none"]["keepouts"] == 0 and rows["none"]["keepout_area"] == 0
    # one keepout per (instance, used layer) for blob and bbox; one per segment for exact
    assert rows["blob"]["keepouts"] == rows["bbox"]["keepouts"] > 0
    assert rows["exact"]["keepouts"] >= rows["bbox"]["keepouts"]
    assert rows["blob"]["keepout_area"] > rows["bbox"]["keepout_area"] > rows["exact"]["keepout_area"] > 0
    # geometric containment, keepout by keepout: bbox inside blob, exact inside bbox
    def keeps(a):
        return [tuple(int(x) for x in c.split()[1:6]) for c in _cmds(out / f"soc2_{a}.buda")
                if c.startswith("add_keepout")]
    def covered(inner, outer):
        return all(any(o[4] == k[4] and o[0] <= k[0] and o[1] <= k[1] and o[2] >= k[2] and o[3] >= k[3]
                       for o in outer) for k in inner)
    assert covered(keeps("exact"), keeps("bbox"))
    assert covered(keeps("bbox"), keeps("blob"))


def test_precision_orders_the_outcome(run):
    _, summary, _ = run
    rows = {r["arm"]: r for r in summary["rows"] if r["nq"] == 2}
    assert rows["none"]["unplaced"] == 0
    assert rows["none"]["unplaced"] <= rows["exact"]["unplaced"] <= rows["bbox"]["unplaced"] <= rows["blob"]["unplaced"]
    assert rows["blob"]["unplaced"] > rows["exact"]["unplaced"], "the blob must cost something"
    # every arm routed the same top-level bundles
    assert len({rows[a]["bundles"] for a in ("none", "blob", "bbox", "exact")}) == 1
    # the source row carries both audits (it heals), the arms carry theirs
    src = next(r for r in summary["rows"] if r["arm"].startswith("source"))
    assert src["first_violations"] is not None and src["violations"] == 0
    for a in ("none", "blob", "bbox", "exact"):
        assert rows[a]["violations"] is not None, a


def test_every_arm_was_rendered(run):
    out, _, _ = run
    for a in ("none", "blob", "bbox", "exact"):
        for suffix in ("_fp.png", "_nuts.png", "_dnuts.png", "_meta.json"):
            assert (out / f"soc2_{a}{suffix}").exists(), a + suffix
