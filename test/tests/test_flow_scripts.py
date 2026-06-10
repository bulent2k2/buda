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
FLOW    = _ROOT / "flow"
CLI     = _ROOT / "src" / "buda_cli.py"
SRC_DIR = CLI.parent


def run_script(name: str) -> tuple[str, int]:
    """Run a .buda script with --no-viz; return (combined output, returncode)."""
    # Ensure build (where buda.so lives) and tools are in PYTHONPATH
    build_dir = _ROOT / "build"
    tools_dir = _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    new_ppath = f"{build_dir}:{tools_dir}:{ppath}" if ppath else f"{build_dir}:{tools_dir}"
    env = {**os.environ, "PYTHONPATH": new_ppath}
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
# two.buda — 13 bundles, 21 segments, 0 violations, 0 overlaps, clean
# connectivity at topo/nuts/dnuts levels (PSI: perp slide interval case)
# ---------------------------------------------------------------------------

def test_two():
    out, rc = run_script("two.buda")
    assert_clean(out, rc, "two.buda")
    assert "Bundler created 13 hbundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 21
    assert viols == 0
    assert ovlps == 0
    assert out.count("Success: no opens found.") == 3
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# two_rotated.buda — same topology counts + detailed NUTS (38 segs, 0 unplaced)
# ---------------------------------------------------------------------------

def test_two_rotated():
    out, rc = run_script("two_rotated.buda")
    assert_clean(out, rc, "two_rotated.buda")
    assert "Bundler created 13 hbundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 19
    assert viols == 0
    assert ovlps == 0
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out
    )
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 19
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# comprehensive_demo.buda — 3 bundles, 4 segments, 0 track overlaps
# (1 interval violation is a known fixture; not checked here)
# ---------------------------------------------------------------------------

def test_comprehensive_demo():
    out, rc = run_script("comprehensive_demo.buda")
    assert_clean(out, rc, "comprehensive_demo.buda")
    assert "Bundler created 3 hbundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    assert segs  == 4
    assert ovlps == 0


# ---------------------------------------------------------------------------
# four_blocks_3_bundles.buda — 3 bundles, 6 segments, 0 track overlaps
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# two_rotated_buses.buda — same geometry as two_rotated but 8-bit buses.
# 2 abstract-NUTS M5 overlaps (B7×B9, B8×B9): three 8-bit buses share a
# ~30-unit corridor that has ~12 signal tracks (< 8+8+8 needed).
# Option B places buses in abstract_pos order; the third cannot find a
# non-conflicting 8-track window → 3 segments × 8 bits = 24 unplaced.
# ---------------------------------------------------------------------------

def test_two_rotated_buses():
    out, rc = run_script("two_rotated_buses.buda")
    assert_clean(out, rc, "two_rotated_buses.buda")
    assert "Bundler created 13 hbundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 19
    assert viols == 0
    assert ovlps == 0   # Resolved: previously 2
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out
    )
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 152   # 19 segs × 8 bits = 152
    assert int(dm.group(2)) == 0     # Resolved: previously 24


def test_four_blocks_3_bundles():
    out, rc = run_script("four_blocks_3_bundles.buda")
    assert_clean(out, rc, "four_blocks_3_bundles.buda")
    assert "Bundler created 3 hbundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    assert segs  == 4   # b1=I_H(1), b2=I_V(1), b3=L_HV(2)
    assert ovlps == 0


# ---------------------------------------------------------------------------
# hbundles/08_cross_level.buda — bundle 6 (12 wide, left→right chip) must take
# the direct I_H (1 segment, WL 50), not the U_VHV south detour (3 segments,
# WL 310).  The centre-only band lookup used to price I_H as congested because
# its nominal y=250 sat on a 20-cap Hanan sliver; the slide-aware lookup sees
# the 130-cap bands inside its slide interval, and the kWL term breaks the
# resulting zero-congestion tie toward the shorter topology.
# ---------------------------------------------------------------------------

def test_08_cross_level_detour_trunk_connectivity():
    out, rc = run_script("hbundles/08_cross_level.buda")
    assert_clean(out, rc, "hbundles/08_cross_level.buda")
    assert re.search(r"\[Planner\] Bundle 6 .*-> topo \d+ of \d+: I_H\b", out), (
        "Bundle 6 should select the direct I_H topology"
    )
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 24   # bundle 6: I_H (1 seg) instead of U_VHV (3 segs)
    assert viols == 0
    assert ovlps == 0
    assert "disconnected" not in out
    # topo check is not run in this flow; nuts + dnuts must both be clean
    assert out.count("Success: no opens found.") == 2
