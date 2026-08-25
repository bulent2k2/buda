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

"""Guards for the two TEG visualization demos (demo/teg_hier_hybrid.buda +
demo/teg_two_spellings.buda).

Both demos exist to SHOW the TEG (`teg_mode thru|over`) / multi-rect
mechanisms next to a hierarchy, and both must end clean: check_design
Success at nuts AND dnuts, 0 unplaced, no TEG_OPEN — with the load-bearing
markers present (the OVER blocks' connection metal placed, the THRU block's
BUDA-1907 census INFO).

The pins are TYPE specs (`select_topology d TRUNK_V@x760`), so the asserted
planner lines name shape + locus, not a 1-based index.  Deliberately NOT
asserted: candidate pool sizes and `topo N of M` positions — the in-flight
TEG emission work (PR #841: stubs for a trunk landing inside ONE disjoint
rect / one-sided approaches) re-sorts pools by changing OTHER candidates'
WL.  The pinned shapes here (rectilinear partial-span connector leg,
disjoint-gap stub pair, thru census) already emit their connection metal on
main, so their own geometry — segment and bit-wire counts included — is
stable across that change.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from subprocess_env import buda_env

# Full-pipeline subprocess smoke tests -> the flow-script integration tier.
pytestmark = pytest.mark.mid

_ROOT = Path(__file__).parents[2]
DEMO  = _ROOT / "demo"
CLI   = _ROOT / "src" / "buda_cli.py"


def _run_demo(name: str) -> tuple[str, int]:
    """Run a demo/ script with --no-viz; return (terminal+flow-log, rc)."""
    env = buda_env(_ROOT)
    r = subprocess.run(
        [sys.executable, str(CLI), "--no-viz", str(DEMO / name)],
        capture_output=True, text=True, env=env,
    )
    log = DEMO / "log" / f"{Path(name).stem}_flow.log"
    detail = log.read_text() if log.exists() else ""
    return r.stderr + "\n" + detail, r.returncode


def _assert_clean_endpoint(out: str, rc: int, name: str) -> None:
    assert rc == 0, f"{name}: non-zero exit {rc}\n{out}"
    bad = [l for l in out.splitlines() if l.startswith("Error:")]
    assert not bad, f"{name}: unexpected error lines:\n" + "\n".join(bad)
    # nuts AND dnuts audits both clean.
    assert out.count("Success: no violations found.") == 2, out
    # An OVER rect with no placed metal would be TEG_OPEN — the one failure
    # mode these demos exist to make visible.
    assert "TEG_OPEN" not in out, out
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced",
        out)
    assert dm, f"{name}: DetailedNUTS summary not found\n{out}"
    assert int(dm.group(2)) == 0, f"{name}: unplaced bits\n{out}"


def test_teg_hier_hybrid_demo():
    """demo/teg_hier_hybrid.buda — TEG macros beside a projected BDB
    hierarchy (the hierarchy-fed flat route; the hier flow proper cannot
    hold a multi-rect block — teg_multirect_status.md item 6)."""
    out, rc = _run_demo("teg_hier_hybrid.buda")
    _assert_clean_endpoint(out, rc, "teg_hier_hybrid")

    # The hierarchy really was projected from the BDB.
    assert "Added 2 blocks at depth 0" in out, out
    assert "Added 4 blocks at depth 1" in out, out

    # The three type-spec pins resolve to the demonstration shapes (loci are
    # geometric — Hanan lines / channel midpoints — so stable across pool
    # re-sorts).
    assert re.search(r"\[Planner\] Bundle \d+ .*TRUNK_V@x760 \[pinned\]",
                     out), out   # dsp: notch trunk + connector leg to the arm
    assert re.search(r"\[Planner\] Bundle \d+ .*TRUNK_H@y630 \[pinned\]",
                     out), out   # phy: gap trunk + per-rect gap stubs
    assert re.search(r"\[Planner\] Bundle \d+ .*TRUNK_H@y120 \[pinned\]",
                     out), out   # ioc: near-rect-only thru route

    # trunk+stub+leg (dsp) + trunk+2 gap stubs (phy) + trunk (ioc) = 7 placed
    # segments, no overlaps/violations; 7 x 4 bits = 28 bit-wires.  The OVER
    # audits passing (above) prove the leg and both gap stubs are real placed
    # metal, not annotations.
    m = re.search(r"\[NUTS\] (\d+) segments placed\. Track overlaps: (\d+), "
                  r"Interval violations: (\d+)", out)
    assert m and (m.group(1), m.group(2), m.group(3)) == ("7", "0", "0"), out
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed", out)
    assert dm.group(1) == "28", out

    # The THRU block's reliance is censused, naming the untouched rect.
    census = [l for l in out.splitlines() if "BUDA-1907" in l]
    assert census and any("'ioc'" in l and "rect#1" in l for l in census), out


def test_teg_two_spellings_demo():
    """demo/teg_two_spellings.buda — hierarchy container vs teg_mode over vs
    thru on the same L shape: OVER's connector leg is routed+audited metal,
    THRU's reliance is a census INFO, the container's join is assumed."""
    out, rc = _run_demo("teg_two_spellings.buda")
    _assert_clean_endpoint(out, rc, "teg_two_spellings")

    # The hierarchy spelling routes a straight ribbon into the CHILD; the
    # two TEG spellings drop the same notch trunk.
    assert re.search(r"\[Planner\] Bundle \d+ .*I_V \[pinned\]", out), out
    assert re.search(r"\[Planner\] Bundle \d+ .*TRUNK_V@x750 \[pinned\]",
                     out), out   # l_over: partial-span trunk + connector leg
    assert re.search(r"\[Planner\] Bundle \d+ .*TRUNK_V@x1350 \[pinned\]",
                     out), out   # l_thru: same shape, no leg — census instead

    # I_V (1) + over trunk+leg (2) + thru trunk (1) = 4 segments; 16 bits.
    m = re.search(r"\[NUTS\] (\d+) segments placed\. Track overlaps: (\d+), "
                  r"Interval violations: (\d+)", out)
    assert m and (m.group(1), m.group(2), m.group(3)) == ("4", "0", "0"), out
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed", out)
    assert dm.group(1) == "16", out

    # THRU census names l_thru's untouched arm; the OVER twin must NOT be
    # censused (its arm is reached by the emitted leg).
    census = [l for l in out.splitlines() if "BUDA-1907" in l]
    assert census and any("'l_thru'" in l and "rect#0" in l
                          for l in census), out
    assert not any("'l_over'" in l for l in census), out
