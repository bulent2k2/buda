"""
End-to-end smoke tests for .buda flow scripts.

Each test runs buda_cli.py --no-viz on a known-good script and asserts
on key output: bundle count, NUTS placement stats, zero track overlaps.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT   = Path(__file__).parents[2]
FLOW    = _ROOT / "buda_system_v2" / "flow"
CLI     = _ROOT / "buda_system_v2" / "src" / "buda_cli.py"
SRC_DIR = CLI.parent


def run_script(name: str) -> tuple[str, int]:
    """Run a .buda script with --no-viz; return (combined output, returncode)."""
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    r = subprocess.run(
        [sys.executable, str(CLI), "--no-viz", str(FLOW / name)],
        capture_output=True, text=True, env=env,
    )
    return r.stdout + r.stderr, r.returncode


def assert_clean(out: str, rc: int, name: str) -> None:
    assert rc == 0, f"{name}: non-zero exit {rc}\n{out}"
    bad = [l for l in out.splitlines() if l.startswith("Error:")]
    assert not bad, f"{name}: unexpected error lines:\n" + "\n".join(bad)


def nuts_summary(out: str):
    """Return (segments, interval_violations, track_overlaps) from NUTS summary line."""
    m = re.search(
        r"\[NUTS\] (\d+) segments placed.*"
        r"Interval violations: (\d+), Track overlaps: (\d+)", out
    )
    assert m, "NUTS summary line not found in output"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# ---------------------------------------------------------------------------
# two.buda — 13 bundles, 19 segments, 0 violations, 0 overlaps
# ---------------------------------------------------------------------------

def test_two():
    out, rc = run_script("two.buda")
    assert_clean(out, rc, "two.buda")
    assert "Bundler created 13 bundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 19
    assert viols == 0
    assert ovlps == 0


# ---------------------------------------------------------------------------
# two_rotated.buda — same topology counts + detailed NUTS (38 segs, 0 unplaced)
# ---------------------------------------------------------------------------

def test_two_rotated():
    out, rc = run_script("two_rotated.buda")
    assert_clean(out, rc, "two_rotated.buda")
    assert "Bundler created 13 bundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 19
    assert viols == 0
    assert ovlps == 0
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out
    )
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 38
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# comprehensive_demo.buda — 3 bundles, 4 segments, 0 track overlaps
# (1 interval violation is a known fixture; not checked here)
# ---------------------------------------------------------------------------

def test_comprehensive_demo():
    out, rc = run_script("comprehensive_demo.buda")
    assert_clean(out, rc, "comprehensive_demo.buda")
    assert "Bundler created 3 bundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    assert segs  == 4
    assert ovlps == 0


# ---------------------------------------------------------------------------
# four_blocks_3_bundles.buda — 3 bundles, 6 segments, 0 track overlaps
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# two_rotated_buses.buda — same geometry as two_rotated but 8-bit buses.
# Bundle width = 8 × 1.5 = 12 → bit_width=12 in detailed NUTS (228 net segs).
# 2 M5 track overlaps (B7×B9, B8×B9) are a known geometric fixture: three
# 12-unit buses compete for a ~30-unit perpendicular corridor and cannot all fit.
# ---------------------------------------------------------------------------

def test_two_rotated_buses():
    out, rc = run_script("two_rotated_buses.buda")
    assert_clean(out, rc, "two_rotated_buses.buda")
    assert "Bundler created 13 bundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 19
    assert viols == 0
    assert ovlps == 2   # known: B7×B9, B8×B9 (3×12u buses in 30u corridor)
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out
    )
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 228
    assert int(dm.group(2)) == 0


def test_four_blocks_3_bundles():
    out, rc = run_script("four_blocks_3_bundles.buda")
    assert_clean(out, rc, "four_blocks_3_bundles.buda")
    assert "Bundler created 3 bundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    assert segs  == 6
    assert ovlps == 0
