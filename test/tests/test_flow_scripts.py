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
    # Each net is bundled exactly once at its most specific endpoints:
    # 15 buses − 1 degenerate (xl_l2c) = 14 HBundles = 14 wrappers (the
    # b_lohi blk_cell template expands to its 4 instances; replicas are
    # skipped).  Previously the same-level buses were also bundled at every
    # ancestor depth and routed once per depth (x_top got 3 parallel copies).
    assert "HierBundler: 14 hbundles (D0: 7, D1: 3, D2: 4)" in out
    assert "run_planner hier: 14 wrappers after expansion" in out
    # x_top / x_bot (leaf-precision level-0 bundles) route direct I_H.
    assert re.search(r"\[Planner\] Bundle 8 .*-> topo \d+ of \d+: I_H\b", out)
    assert re.search(r"\[Planner\] Bundle 10 .*-> topo \d+ of \d+: I_H\b", out)
    assert re.search(r"\[Planner\] Bundle 3 .*Z_HVH@x475@y125 \[pinned\]", out), (
        "sidecar pin for bundle 3 (xl_c2l, by net name) must be honored by "
        "the hier planner"
    )
    segs, viols, ovlps = nuts_summary(out)
    # 19 = 10 single-seg I_H/I_V + 2 L_HV (2 segs each) + pinned Z_HVH
    # (3 segs) + 1 L_VH (2 segs) across the 14 wrappers.
    assert segs  == 19
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
# channel_stress.buda — stress-tests NUTS channel packing.
# Regression: verifying that the stress flow runs to completion successfully.
# Overlap/violation counts are the flow's stress payload and intentionally
# not pinned here.
# ---------------------------------------------------------------------------

def test_channel_stress_runs_to_completion():
    out, rc = run_script("channel_stress.buda")
    assert rc == 0, f"channel_stress.buda crashed (exit {rc})\n{out[-2000:]}"
    assert "Fatal Python error" not in out
    # The pipeline must still run to completion
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


# ---------------------------------------------------------------------------
# hbundles/09_local_global_compete.buda — local/global band competition.
# A wide cell-local bus (D1, eff 136) fits only the band over its cell
# interior (cap 170); the global D0 bus (eff 102) is planned first and its
# window centre lands in that same band.  The demand reservation parked by
# run_planner hier must steer the global to a free band on the FIRST pass:
# no rip-up, every bundle overflow-free, both levels strict in the summary.
# ---------------------------------------------------------------------------

def test_09_local_global_compete_reservation_avoids_ripup():
    out, rc = run_script("hbundles/09_local_global_compete.buda")
    assert_clean(out, rc, "hbundles/09_local_global_compete.buda")
    assert "Rip-up" not in out, "reservation should pre-empt the conflict, not repair it"
    ovs = re.findall(r"\[Planner\] Bundle \d+ .*overflow=([\d.]+)", out)
    assert len(ovs) == 2
    assert all(float(o) == 0.0 for o in ovs), f"expected overflow-free plans, got {ovs}"
    assert re.search(r"D0: 1 bundles\s+strict:1", out)
    assert re.search(r"D1: 1 bundles\s+strict:1", out)
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 2
    assert viols == 0
    assert ovlps == 0
    assert out.count("Success: no opens found.") == 2
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 56    # 24 + 32 bits, one segment each
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# planner5_span_drop.buda — span-scaled non-TOP penalty.  With the TOP band
# saturated by the filler, the short bundle chooses between dropping its
# I_H to M4 (penalty scaled by span) and detouring on M6 (extra wirelength).
# Run 1 (base_span_ref 2000): the drop is cheap → I_H on M4.
# Run 2 (base_span_ref 1: effectively the legacy flat penalty): the TOP
# detour is cheaper → multi-segment U via M6.
# ---------------------------------------------------------------------------

def test_planner5_span_scaled_penalty_drops_short_stub():
    out, rc = run_script("planner5_span_drop.buda")
    assert_clean(out, rc, "planner5_span_drop.buda")
    picks = re.findall(r"\[Planner\] Bundle 1 .*?:\s+(\S+)\s+\[([^\]]+)\]", out)
    assert len(picks) == 2, f"expected two planner runs for bundle 1, got {picks}"
    assert picks[0][0] == "I_H" and picks[0][1] == "H→M4", (
        f"scaled penalty must drop the short stub to M4, got {picks[0]}"
    )
    assert picks[1][1] != "H→M4" and "M6" in picks[1][1], (
        f"flat penalty must prefer the TOP-layer detour, got {picks[1]}"
    )
    segs, viols, ovlps = nuts_summary(out)
    assert viols == 0
    assert ovlps == 0


# ---------------------------------------------------------------------------
# ripup2.buda — rip-up victim TARGETING.  Same a/b conflict as ripup1 plus
# an unrelated bus i (disjoint channel) committed between them.  Victims are
# ranked by contended-band overlap: bundle 1 (the blocker) is ripped
# directly; bundle 3 (zero overlap) must never be replanned.
# ---------------------------------------------------------------------------

def test_ripup2_targets_actual_blocker():
    out, rc = run_script("ripup2.buda")
    assert_clean(out, rc, "ripup2.buda")
    assert "[Planner] Rip-up: replanned bundle 1 to free capacity for bundle 2" in out
    assert "replanned bundle 3" not in out, "zero-overlap victim must be skipped"
    ovs = re.findall(r"\[Planner\] Bundle \d+ .*overflow=([\d.]+)", out)
    assert all(float(o) == 0.0 for o in ovs), f"expected overflow-free plans, got {ovs}"
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 3
    assert viols == 0
    assert ovlps == 0
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 60    # 24 + 16 + 20 bits, one segment each
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# hbundles/10_chip_units_blocks_leaf.buda — 4-level hierarchy at scale:
# 2 chips × 10 units × 2 blks × 2 leaves, 176 buses / 968 bits across all
# depth pairings.  Guards the single-bundle-per-net semantics (a bus is
# never routed at more than one hierarchy depth) and the planner's clean
# strict pass at this size.  Residual NUTS overlaps / unplaced dnuts bits
# are known stage-4 packing gaps (docs/future/nuts_packing_gaps.md) and
# are ratcheted, not accepted silently.
# ---------------------------------------------------------------------------

def test_10_four_level_scale_one_bundle_per_bus():
    out, rc = run_script("hbundles/10_chip_units_blocks_leaf.buda")
    assert_clean(out, rc, "hbundles/10_chip_units_blocks_leaf.buda")
    # Regression: check_connectivity over hbundles verifies every candidate —
    # cell-level templates carry cell-local block names (e.g. lo/hi) absent from
    # the chip floorplan.  The check must resolve each hbundle's own generation
    # floorplan instead of crashing with `map::at: key not found`.
    assert "Verifying topology-level connectivity (all candidates)..." in out
    assert "map::at" not in out
    assert "Traceback" not in out
    assert out.count("Success: no opens found.") >= 2   # topo + nuts both clean
    # 176 buses → exactly one HBundle per bus, at its routing-context level.
    assert "HierBundler: 176 hbundles (D0: 26, D1: 30, D2: 40, D3: 80)" in out
    # The two D3 blk_cell templates expand over their 40 instances;
    # replicas are skipped → one wrapper per physical bus.
    assert "run_planner hier: 176 wrappers after expansion" in out
    # Every bundle plans STRICT: no rip-ups, no overflow/best-effort commits.
    assert re.search(r"D0: 26 bundles\s+strict:26", out)
    assert re.search(r"D1: 30 bundles\s+strict:30", out)
    assert re.search(r"D2: 40 bundles\s+strict:40", out)
    assert re.search(r"D3: 80 bundles\s+strict:80", out)
    assert "Rip-up" not in out
    assert "WARNING" not in out
    assert "Success: no opens found." in out      # nuts connectivity
    segs, viols, ovlps = nuts_summary(out)
    # Segment count rose 196→212: the corrected LOW-layer congestion model
    # (Gap 2 — leaf cells block LOW, containers stay transparent) and inter-bus
    # pitch reservation (Gap 1) steer the planner to different topologies.
    assert segs == 212
    assert viols == 0
    assert ovlps <= 4                             # ratchet (was 10; Gap 1+2 dropped it to 4)
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) >= 1224               # ratchet (was 1112; now 1224)
    assert int(dm.group(2)) <= 8                  # ratchet (currently 8; Gap 3 still open)


# ---------------------------------------------------------------------------
# nuts_corner_overlap.buda — two buses pinned to Z-topos whose V-stubs share a
# column.  After the H trunks are placed and the V-stub spans are stretched to
# reach them, the stubs collide (a "corner overlap") — fixable only by ordering
# the trunks (H1 below H2).  The corner-overlap pass derives that vertical
# constraint from the stubs' anchored ends and re-solves the H layer; the
# overlap (and the 4 detailed-NUTS unplaced bits it caused) disappears.
# ---------------------------------------------------------------------------

def test_nuts_corner_overlap_vertical_constraint():
    out, rc = run_script("nuts_corner_overlap.buda")
    assert_clean(out, rc, "nuts_corner_overlap.buda")
    assert "[NUTS] corner-overlap pass: overlaps 1 -> 0." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 6
    assert viols == 0
    assert ovlps == 0          # corner overlap resolved by trunk reordering
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(2)) == 0   # all bits place once the overlap is gone


# ---------------------------------------------------------------------------
# nuts_corner_overlap_3layer.buda — the corner-overlap pass in a 3-TOP-layer
# stack (M4/M5/M6).  An M6 keepout forces both Z-topo H-trunks onto M4, the
# FIRST layer NUTS solves, so the pass must derive its vertical constraint on
# the first-solved layer (the 2-layer test exercises the last-solved M6).
# Confirms the pass generalizes across three layers solved in sequence.
# ---------------------------------------------------------------------------

def test_nuts_corner_overlap_3layer():
    out, rc = run_script("nuts_corner_overlap_3layer.buda")
    assert_clean(out, rc, "nuts_corner_overlap_3layer.buda")
    # Trunks forced onto M4 (the first-solved layer) by the M6 keepout.
    assert re.search(r"Bundle 1 .*\[V→M5 H→M4 V→M5\]", out)
    assert "[NUTS] corner-overlap pass: overlaps 1 -> 0." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 6
    assert viols == 0
    assert ovlps == 0
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(2)) == 0
