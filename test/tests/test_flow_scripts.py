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

# ---------------------------------------------------------------------------
# nuts_relax_range_reg.buda — pinned U_VHV detour (select_topology after
# run_planner).  Guards two regressions:
# 1. select_topology must re-plan layer assignments: stale seg_layers from the
#    planner's I_H pick put the left V stub on M4 (an H layer) at coordinates
#    from the wrong axis — and the dnuts connectivity check passed anyway.
#    The planner line must show the pinned U with per-direction layers.
# 2. relax_boundary_intervals edge-at-bound: the H trunk's pull target is the
#    hard slide bound y=-2 (min stub length below the blocks at y=0); the bus
#    EDGE must land at -2.0, not spread past it.
# ---------------------------------------------------------------------------

def test_nuts_relax_range_reg_pinned_u_detour():
    out, rc = run_script("nuts_relax_range_reg.buda")
    assert_clean(out, rc, "nuts_relax_range_reg.buda")
    assert re.search(
        r"\[Planner\] Bundle 1 .*U_VHV@y-100 \[pinned\]\s+\[V→M5 H→M4 V→M5\]", out
    ), "select_topology must re-plan: pinned U with V segs on M5, H trunk on M4"
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 3
    assert viols == 0
    assert ovlps == 0
    # Trunk bus upper edge exactly at the hard slide bound y=-2.
    m = re.search(r"\[NUTS\] M4: total bus width .* interval \[(-?[\d.]+), (-?[\d.]+)\]", out)
    assert m, "M4 NUTS summary line not found"
    assert float(m.group(2)) == -2.0
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 18
    assert int(dm.group(2)) == 0
    assert "disconnected" not in out
    assert "unbuildable" not in out
    assert out.count("Success: no opens found.") == 1


def test_08_cross_level_detour_trunk_connectivity():
    out, rc = run_script("hbundles/08_cross_level.buda")
    assert_clean(out, rc, "hbundles/08_cross_level.buda")
    assert re.search(r"\[Planner\] Bundle 6 .*-> topo \d+ of \d+: I_H\b", out), (
        "Bundle 6 should select the direct I_H topology"
    )
    segs, viols, ovlps = nuts_summary(out)
    # 28 = bundle 6 on I_H (1 seg, not the 3-seg U detour) + bundle 7 on a
    # Z detour (3 segs: with measured bit pitch its direct I_V no longer fits
    # its slide window cleanly) + bundle 3 pinned to Z_HVH@x475@y125 (3 segs)
    # by the 08_cross_level.json sidecar, honored by run_planner hier.
    assert segs  == 28
    assert re.search(r"\[Planner\] Bundle 3 .*Z_HVH@x475@y125 \[pinned\]", out), (
        "sidecar pin for bundle 3 must be honored by the hier planner"
    )
    assert viols == 0
    assert ovlps == 0
    assert "disconnected" not in out
    # topo check is not run in this flow; nuts + dnuts must both be clean
    assert out.count("Success: no opens found.") == 2


def test_sel_topos_typo():
    out, rc = run_script("sel_topos_typo.buda")
    assert rc == 1, f"Expected non-zero exit code 1, got {rc}"
    assert "Error: block 'rcv2' is used as both driver and receiver in bus 'c'" in out


# ---------------------------------------------------------------------------
# planner3.buda — bundles 1 and 2 want the same TRUNK_H@y305 whose trunks
# share the 110-unit slide window [250,360].  With measured bit pitch a
# 16-bit trunk costs 68 units (16 × 34/8 on M6), so two trunks (136) overflow
# the window — the window-aware capacity clamp must make bundle 2's
# evaluation see that and route it via a different candidate.  Previously
# (1.5 units/bit → 51 wide) the planner priced both at overflow=0 and dnuts
# ended with a reservation conflict and 16 unplaced bits (the window has 27
# signal tracks; two 16-bit trunks need 32).
#
# The M6 window contention among B1's trunk and the B2/B3 stubs is resolved
# by the earliest-deadline-first window repack, and B2's two stubs (same
# bundle, same trunk) share one M6 band via the sibling alignment preference
# (positions asserted in-process in test_nuts_alignment.py).
# ---------------------------------------------------------------------------

def test_planner3_window_capacity_avoids_double_booked_trunk():
    out, rc = run_script("planner3.buda")
    assert_clean(out, rc, "planner3.buda")
    sel = dict(re.findall(r"\[Planner\] Bundle (\d+) .*-> topo \d+ of \d+: (\S+)", out))
    assert sel["1"] == "TRUNK_H@y305"
    assert sel["2"] != "TRUNK_H@y305", (
        "bundle 2 must not double-book bundle 1's trunk window; "
        f"planner selected {sel}"
    )
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 8   # was 9: hard overflow gate lets bundle 3 take a 2-seg trunk
    assert viols == 0
    assert ovlps == 0   # was 1: EDF repack packs the shared M6 window cleanly
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 128   # 8 segs × 16 bits
    assert int(dm.group(2)) == 0     # was 16: trunk reservation conflict
    assert "reservation conflict" not in out


# ---------------------------------------------------------------------------
# channel_stress.buda — deliberately over-subscribed channels (M4 at 88%
# overhead) with 62 sidecar-pinned selections saved under the old width
# model.  Regression: pinned bundles whose only candidate is infeasible
# (slide window < effective bus width) made optimize_topologies commit an
# empty per-segment layer vector and index it out of bounds (segfault).
# The planner must instead fall back to a best-effort assignment with an
# explicit warning.  Overlap/violation counts are the flow's stress payload
# and intentionally not pinned here.
# ---------------------------------------------------------------------------

def test_channel_stress_pinned_infeasible_does_not_crash():
    out, rc = run_script("channel_stress.buda")
    assert rc == 0, f"channel_stress.buda crashed (exit {rc})\n{out[-2000:]}"
    assert "Fatal Python error" not in out
    assert "WARNING" in out and "best-effort" in out, (
        "infeasible pinned candidates must be reported, not silently committed"
    )
    # The pipeline must still run to completion (no run_detailed_nuts in
    # this flow; it ends with post_nuts stub reassignment).
    nuts_summary(out)


# ---------------------------------------------------------------------------
# planner4.buda — keepout on M6 blocks bundle 3's preferred trunk band, and
# bundles 1/2 (pinned) hold the alternative bands.  Overflow is a hard
# constraint: the planner must detour bundle 3 to an overflow-free trunk
# instead of committing 16 units of overflow that NUTS cannot place (which
# previously materialised as a B2×B3 track overlap on M6).
# ---------------------------------------------------------------------------

def test_planner4_keepout_overflow_forces_detour():
    out, rc = run_script("planner4.buda")
    assert_clean(out, rc, "planner4.buda")
    ovs = re.findall(r"\[Planner\] Bundle \d+ .*overflow=([\d.]+)", out)
    assert len(ovs) == 3
    assert all(float(o) == 0.0 for o in ovs), f"expected overflow-free plans, got {ovs}"
    segs, viols, ovlps = nuts_summary(out)
    assert viols == 0
    assert ovlps == 0   # was 1: bundle 3 overflowed into bundle 2's trunk band
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# ripup1.buda — bundle 1 (wider, planned first) parks in the only band that
# bundle 2's pinned I_H topology can use; both cannot fit (102+68 > 120) and
# the pin forbids a detour.  The planner must rip up bundle 1 and replan it
# into the free band above so both end up overflow-free.
# ---------------------------------------------------------------------------

def test_ripup1_replans_earlier_bundle_to_free_capacity():
    out, rc = run_script("ripup1.buda")
    assert_clean(out, rc, "ripup1.buda")
    assert "[Planner] Rip-up: replanned bundle 1 to free capacity for bundle 2" in out
    ovs = re.findall(r"\[Planner\] Bundle \d+ .*overflow=([\d.]+)", out)
    assert all(float(o) == 0.0 for o in ovs), f"expected overflow-free plans, got {ovs}"
    segs, viols, ovlps = nuts_summary(out)
    assert viols == 0
    assert ovlps == 0
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 40    # 2 segs × 20 bits avg (24+16 bits)
    assert int(dm.group(2)) == 0
