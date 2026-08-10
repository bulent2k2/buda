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

"""
End-to-end smoke tests for .buda flow scripts.

Each test runs buda_cli.py --no-viz on a known-good script and asserts
on key output: bundle count, NUTS placement stats, zero track overlaps.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Full-pipeline integration smoke tests: each spawns buda_cli.py on a .buda
# script (~1s apiece, ~19s total).  They form the "mid" tier — deselected from
# the default fast run, included by `bb -m`/`bb -s` (see pytest.ini, bb).
pytestmark = pytest.mark.mid

_ROOT   = Path(__file__).parents[2]
FLOW    = _ROOT / "flow"
DEMO    = _ROOT / "demo"          # user/designer-facing demo scripts
CLI     = _ROOT / "src" / "buda_cli.py"
SRC_DIR = CLI.parent


def _flow_log_text(script: Path) -> str:
    """Detailed per-command output now lives in <dir>/log/<stem>_flow.log; the
    terminal only carries an abstract per-command summary.  Return the log text
    so assertions can still inspect NUTS/planner/etc. detail."""
    log_path = Path(script).parent / "log" / f"{Path(script).stem}_flow.log"
    return log_path.read_text() if log_path.exists() else ""


def run_script(name: str) -> tuple[str, int]:
    """Run a .buda script with --no-viz; return (combined output, returncode).

    Combined output = terminal (summary) + the flow log (full detail)."""
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
    # Detail (NUTS/planner/…) now lives once in the flow log; the terminal only
    # carries an abstract per-command summary, whose one-line headlines would
    # otherwise double-count detail lines.  Parse the log (+ stderr for crashes)
    # so occurrence counts match the pre-summary behaviour.
    return r.stderr + "\n" + _flow_log_text(FLOW / name), r.returncode


def assert_clean(out: str, rc: int, name: str) -> None:
    assert rc == 0, f"{name}: non-zero exit {rc}\n{out}"
    bad = [l for l in out.splitlines() if l.startswith("Error:")]
    assert not bad, f"{name}: unexpected error lines:\n" + "\n".join(bad)


#: The flow QoR ratchet's expected counts, as a REGENERABLE golden.
#:
#: This used to be numbers frozen in the test body, calibrated under
#: -march=native on a host that no longer exists.  No tool could rebaseline it,
#: so the honest options were to guess or to leave it enforced NOWHERE — which
#: is what happened (docs/internal/opens_ci.md).  Regenerating it is now one
#: command on the reference host, so CI can own it the way it owns the
#: placement goldens.
FLOW_QOR_GOLDEN = Path(__file__).parent / "data" / "flow_qor_golden.json"


def load_flow_qor_golden():
    """The committed expectations, or None when absent/malformed."""
    try:
        with open(FLOW_QOR_GOLDEN) as fh:
            d = json.load(fh)
        return d["counts"] if isinstance(d.get("counts"), dict) else None
    except Exception:                              # noqa: BLE001
        return None


def save_flow_qor_golden(counts):
    """Rebaseline. Run on the REFERENCE HOST (pinned ISA — docs/internal/ci.md):

        BUDA_FLOW_QOR_REGEN=1 pytest test/tests/test_flow_scripts.py \
            -k four_level_scale_one_bundle

    then commit the file.  The `arch` field records what it was measured under,
    so a mismatch is diagnosable rather than mysterious.
    """
    FLOW_QOR_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    with open(FLOW_QOR_GOLDEN, "w") as fh:
        json.dump({"flow": "flow/hbundles/10_chip_units_blocks_leaf.buda",
                   "arch": os.environ.get("BUDA_ARCH", "unknown"),
                   "counts": counts}, fh, indent=1)
        fh.write("\n")


def nuts_summary(out: str):
    """Return (segments, interval_violations, track_overlaps) from NUTS summary line."""
    m = re.search(
        r"\[NUTS\] (\d+) segments placed.*"
        r"Track overlaps: (\d+), Interval violations: (\d+)", out
    )
    assert m, "NUTS summary line not found in output"
    # Return (segments, interval_violations, track_overlaps) — the line now
    # prints overlaps before violations, so map the groups back.
    return int(m.group(1)), int(m.group(3)), int(m.group(2))


# ---------------------------------------------------------------------------
# two.buda — 13 bundles, 21 segments, 0 violations, 0 overlaps, clean
# connectivity at topo/nuts/dnuts levels (PSI: perp slide interval case)
# ---------------------------------------------------------------------------

def test_tc3a_flat_no_perp_range_inversion():
    """Regression: ConnTopology::build asserted (perp_lo > perp_hi) on the big
    flat tc3a design.  A min-stub-length push-out (compute_slide_ranges pass 2)
    collided with a busterm bound (pass 1) for a spine sitting on a shared block
    edge, emptying the perpendicular slide window.  With asserts on this aborted
    the process; the flow must now complete without the SIGABRT.

    The regression is a *topo-stage* issue, so the CPU-invariant guards are that
    the flow completes (rc == 0) and topo-level connectivity verifies clean.
    NUTS-stage cleanliness is FP/CPU-sensitive under -march=native (a bundle can
    tip into a NUTS open on some hosts), so we don't require every stage to be
    clean.  See test_planner_signal_tracks / test_ripup_reroute for the same
    -ffp-contract=off portability story.

    DetailedNUTS unplaced == 0 IS asserted, though: this design used to strand ~32
    bits at DetailedNUTS because a collinear MST relay (two bus stubs entering
    opposite faces of a pass-through block on the SAME row) was bridged by a
    trivial 2-unit jog that the planner offloaded to a zero-signal-track layer.
    complete_relay_junctions now MERGES such collinear stubs into one straight
    pass-through wire (topology-stage, deterministic — no FP), so the strand is
    gone on every host."""
    out, rc = run_script("big_data_test/big.buda")
    assert rc == 0, f"big/tc3a aborted (rc={rc}) — perp-range inversion?\n{out[-3000:]}"
    # Anchor to the TOPO-stage check section specifically — a bare
    # `"Success..." in out` could be satisfied by a later clean NUTS/DNUTS stage
    # even if the topo check regressed.  (Not `>= 2` Success lines: that also
    # required NUTS-stage cleanliness, which is CPU-sensitive here — bundle 48
    # tips into a NUTS open on some hosts under -march=native.)
    topo = re.search(
        r"━━━ check_design topo all ━━━\n(.*?)(?=\n━━━|\Z)", out, re.S)
    assert topo, f"topo-stage connectivity section not found\n{out[-2000:]}"
    assert "Success: no violations found." in topo.group(1), \
        f"topo-stage connectivity not clean (perp-range inversion?):\n{topo.group(1)}"
    # The collinear-relay merge closes the DetailedNUTS strand deterministically.
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, f"DetailedNUTS summary not found\n{out[-2000:]}"
    assert int(dm.group(2)) == 0, \
        f"expected 0 unplaced after collinear-relay merge, got {dm.group(2)}"


def test_pinless_buses_stay_separate():
    """Issue #16: two distinct shorthand (no-dot) buses must not merge.

    `extract_instance` used to map every pinless endpoint to the sentinel "top",
    so `a left right` and `b up down` shared the STRICT signature top->[top],
    collapsed into one bundle, and b_* silently routed left->right (the first
    net's endpoints).  Now the bare token is the block name, so they stay in two
    bundles routed left->right and up->down.
    """
    out, rc = run_script("no_pin_suffix.buda")
    assert_clean(out, rc, "no_pin_suffix.buda")
    assert "Bundler created 2 hbundles." in out, out
    assert "(left->right)" in out, out
    assert "(up->down)" in out, out


def test_two():
    out, rc = run_script("two.buda")
    assert_clean(out, rc, "two.buda")
    assert "Bundler created 13 hbundles." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 21
    assert viols == 0
    assert ovlps == 0
    assert out.count("Success: no violations found.") == 3
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
# comprehensive_regression.buda — 5 bundles, 26 segments, 0 track overlaps
#
# A FROZEN snapshot of demo/comprehensive_demo.buda (in flow/, not demo/) so
# the live demo stays free to tweak for demonstrations without disturbing these
# pinned counts.  Bundle 5 USED to commit with a planner overflow WARNING while
# NUTS and DetailedNUTS still ended fully clean — that warning was #518's
# phantom overflow (a bus charged entirely into one Hanan band narrower than
# itself).  Since band_span_charge defaulted to 1 the warning is gone and the
# route is shorter: 26 -> 24 segments, 153 -> 121 bit-wires, still 0 overlaps
# and 0 unplaced.
# ---------------------------------------------------------------------------

def test_comprehensive_regression():
    out, rc = run_script("comprehensive_regression.buda")
    assert_clean(out, rc, "comprehensive_regression.buda")
    assert "Bundler created 5 hbundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    assert segs  == 24
    assert ovlps == 0
    # The phantom-overflow WARNING this flow used to emit must stay gone.
    assert "no overflow-free candidate" not in out
    assert "overflows and cannot be rerouted" not in out
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out
    )
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(1)) == 121
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# four_blocks_3_bundles.buda — 6 bundles (b1..b6), 8 segments, 0 track overlaps
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
    assert "Bundler created 6 hbundles." in out
    segs, _viols, ovlps = nuts_summary(out)
    # b1=I_H(1), b2=I_V(1), b3=L_HV(2), b4=I_H(1), b5=I_V(1), b6=L_HV(2)
    assert segs  == 8
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
    assert out.count("Success: no violations found.") == 1


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
    assert out.count("Success: no violations found.") == 2


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
# channel_stress.buda — stress-tests NUTS channel packing (62 bundles, 200 nets
# in a 340-unit channel).  The pre-redistribution run_nuts packs CLEANLY (0);
# the post_nuts stub-length spread across only three vertical layers (M3/M5/M7)
# then leaves a single corner overlap (B48×B53 on M7).  Under the corrected
# congestion accounting (Issue #22: closed-interval for_each_band) this flow
# sits right at its packing limit — one residual corner touch that no
# threshold/pin re-tune fully removes — and there are no interval violations.
# (Before the fix the buggy under-count let it tune to exactly 0; that was a
# knife-edge artifact.)  DetailedNUTS reports 3 keepout-crossing bits: this
# flow has always routed exactly 3 bits straight through a keepout (the old
# midpoint-sampled track query couldn't see a keepout that missed the span
# midpoint — keepout-model audit); the post-placement crossing cull now
# removes them and counts them as unplaced instead of emitting illegal wires.
# ---------------------------------------------------------------------------

def test_channel_stress_packs_clean():
    out, rc = run_script("channel_stress.buda")
    assert rc == 0, f"channel_stress.buda crashed (exit {rc})\n{out[-2000:]}"
    assert "Fatal Python error" not in out
    # Pin the end state.  The full "[NUTS] N segments placed ... Track overlaps:"
    # summaries appear once per run_nuts; the FIRST (pre-redistribution) packs
    # clean, the LAST (post_nuts spread) leaves a single residual corner overlap.
    full = re.findall(
        r"\[NUTS\] \d+ segments placed[^\n]*Track overlaps: (\d+), "
        r"Interval violations: (\d+)", out)
    assert full, "no full NUTS summary found"
    first_ovlp, first_viol = full[0]
    assert int(first_ovlp) == 0, \
        f"pre-redistribution run_nuts not clean: overlaps {first_ovlp}"
    last_ovlp, last_viol = full[-1]
    # Channel is at its packing limit on three V layers; a small residual corner
    # overlap is expected, never an interval violation.  The threshold rose from
    # <=1 to <=6 with the big2-b25 interior-side face-tap fix
    # (docs/internal/big2_b25_abutment_tap_dnuts_2026-07.md): correcting the
    # crossed-vs-abutting tap unlocks tighter pass-through trunk candidates that
    # pack closer to the channel limit, so the raw (no-healer) residue here rises.
    # This is the documented raw-packing trade-off of that fix; healer-equipped
    # flows recover (e.g. big2.buda stays 0/0/0).
    assert int(last_ovlp) <= 6, f"channel_stress final overlaps {last_ovlp} (expected <=6)"
    assert int(last_viol) == 0

    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    # The unplaced bits are the real keepout crossings this flow always had
    # (previously emitted as illegal wires through the keepout, now culled and
    # reported).  Exactly which bits cross is FP/ISA-sensitive under
    # -march=native (the generation host packs 3; this host 7), so bound the
    # count rather than pin it — the invariant that stays host-invariant is
    # that EVERY unplaced bit is an accounted-for keepout cull (unplaced ==
    # removed), so nothing is silently dropped, and the total is a small
    # handful, never a routing blow-up.
    unplaced = int(dm.group(2))
    km = re.search(r"\[DetailedNUTS\] WARNING: (\d+) bit\(s\) removed", out)
    removed = int(km.group(1)) if km else 0   # a host that packs 0 culls is fine
    assert unplaced == removed, \
        f"unplaced {unplaced} != keepout-culled {removed} (a bit was dropped)"
    assert unplaced <= 8, \
        f"expected only the flow's handful of keepout crossings, got {unplaced}"


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
    assert out.count("Success: no violations found.") == 2
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
#
# Keepout-model audit: this flow has always routed exactly 7 bits (bundles
# 1 and 3, seg 1, on M7 — a non-TOP layer where leaf footprints are
# keepouts) straight through leaf-cell keepouts, invisibly (measured on
# main with the same crossing predicate the cull now applies).  Those 7
# are now culled and counted, and 2 abstract segments report their forced
# keepout commits — expected keepout WARNINGs, not planner regressions.
# check_design mirrors the same two events (KEEPOUT_CROSS): the nuts
# stage reports the 2 committed segments and the dnuts stage the 7 culled
# bits as unplaced, so only the topo stage stays fully clean.
# ---------------------------------------------------------------------------

def test_10_four_level_scale_one_bundle_per_bus():
    out, rc = run_script("hbundles/10_chip_units_blocks_leaf.buda")
    assert_clean(out, rc, "hbundles/10_chip_units_blocks_leaf.buda")
    # Regression: check_design over hbundles verifies every candidate —
    # cell-level templates carry cell-local block names (e.g. lo/hi) absent from
    # the chip floorplan.  The check must resolve each hbundle's own generation
    # floorplan instead of crashing with `map::at: key not found`.
    assert "Verifying topology-level design (all candidates)..." in out
    assert "map::at" not in out
    assert "Traceback" not in out
    # topo clean; hier planning defaults to one refinement pass (refine_passes,
    # the level-ordering synthesis) whose strictly-better replans move bundles
    # off the keepout bands.  On the generation host this clears the flow's 2
    # historical keepout-committed segments (KEEPOUT_CROSS) entirely; whether a
    # given replan clears is FP/ISA-sensitive under -march=native (a
    # marginally-better alternative here is marginally-worse there), so assert
    # the pass RAN and left at most that historical residual, not zero exactly.
    assert out.count("Success: no violations found.") >= 1
    assert "Refine pass 1" in out
    assert out.count("placed ON keepout") <= 2
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
    # The only WARNINGs are the keepout report channels (see header comment):
    # every other WARNING — planner overflow/best-effort etc. — stays fatal.
    other_warn = [l for l in out.splitlines()
                  if "WARNING" in l and "keepout" not in l]
    assert not other_warn, f"unexpected WARNINGs:\n" + "\n".join(other_warn)
    assert "Success: no violations found." in out      # nuts connectivity
    # QoR ratchet — exact counts calibrated on the golden-generation host.
    # Whether each refine replan clears its keepout band is FP/ISA-sensitive
    # under -march=native: on the generation host the flow lands 209 segments /
    # 0 overlaps and refinement clears every keepout crossing (0 unplaced); on
    # another host a marginally-better alternative is marginally-worse, so the 2
    # keepout-committed M7 segments survive and DNUTS culls their ~22 crossing
    # bits (1 residual overlap, ~1194 net segments).  Enforce the exact ratchet
    # on the calibration host, tolerant bounds off it.
    #
    # Gated on its OWN flag.  This used to read BUDA_NUTS_GOLDEN_STRICT, which
    # made "enforce the NUTS placement goldens" silently also mean "enforce this
    # hand-calibrated QoR ratchet" — two unrelated concerns on one variable.
    # That coupling bites: CI can legitimately want strict goldens (it has a
    # pinned ISA and image, and `tools/nuts_snapshot.py` can rebaseline them)
    # while NOT wanting this ratchet, whose numbers are hardcoded here, cannot
    # be regenerated by any tool, and were calibrated under -march=native — a
    # different ISA from CI's.  With them coupled, the documented and ACCEPTABLE
    # host-sensitive alternative below would fail the whole run for a reason
    # unrelated to golden enforcement (review on PR #562).
    strict = bool(os.environ.get("BUDA_FLOW_QOR_STRICT"))
    segs, viols, ovlps = nuts_summary(out)
    assert viols == 0
    dm = re.search(r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    net_segs, unplaced = int(dm.group(1)), int(dm.group(2))
    km = re.search(r"\[DetailedNUTS\] WARNING: (\d+) bit\(s\) removed", out)
    measured = {"segs": segs, "overlaps": ovlps, "net_segs": net_segs,
                "unplaced": unplaced, "culls": int(km.group(1)) if km else 0}
    if os.environ.get("BUDA_FLOW_QOR_REGEN"):
        save_flow_qor_golden(measured)     # rebaseline; see the helper's docstring
        pytest.skip(f"regenerated {FLOW_QOR_GOLDEN} — rerun without BUDA_FLOW_QOR_REGEN")
    if strict:
        # Compare against the REGENERABLE golden, not numbers frozen in this
        # file.  The old form hardcoded 209/1220/0 calibrated under
        # -march=native on a host that no longer exists, which is precisely why
        # this ratchet ended up enforced nowhere (docs/internal/opens_ci.md).
        want = load_flow_qor_golden()
        assert want is not None, (
            f"{FLOW_QOR_GOLDEN} missing — regenerate with "
            f"BUDA_FLOW_QOR_REGEN=1 on the reference host")
        assert measured == want, (
            f"QoR ratchet moved:\n  measured {measured}\n  golden   {want}\n"
            f"If this is an intended improvement, rebaseline on the reference "
            f"host (pinned ISA, see docs/internal/ci.md) with "
            f"BUDA_FLOW_QOR_REGEN=1 and commit {FLOW_QOR_GOLDEN}.")
    else:
        assert 204 <= segs <= 209         # host-sensitive topology near-ties
        assert ovlps <= 1                 # a residual corner overlap when a replan doesn't clear here
        assert net_segs >= 1180
        # Off the generation host, any unplaced bits must all be accounted-for
        # keepout culls (unplaced == removed) — nothing silently dropped — AND
        # capped: the host-sensitive residual is the 2 historical
        # keepout-committed M7 segments' ~22 crossing bits, so tolerate that
        # plus headroom but not a routing regression that lands MANY more bits
        # on keepouts (all culled would still satisfy the equality alone;
        # Codex #281).
        removed = measured["culls"]
        assert unplaced == removed, \
            f"unplaced {unplaced} != keepout-culled {removed} (a bit was dropped)"
        assert unplaced <= 30, \
            f"expected at most the ~22 historical keepout-crossing bits, got {unplaced}"


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


# ---------------------------------------------------------------------------
# nuts_corner_touch.buda — a CROSS-trunk-layer corner overlap.  Bundle 1's trunk
# is pinned to M4 (sidecar seg_layers [5,4,5]) and bundle 2's to M6; both land at
# y=125, so the two M5 stubs meet end-to-end (a touch, not a strict overlap).
# find_overlaps flags the end-to-end touch; the corner pass can't track-order an
# M4 trunk against an M6 trunk, so it nudges each within its own layer to opposite
# sides of a split coordinate.  Resolves the overlap (and the bits it blocked).
# ---------------------------------------------------------------------------

def test_nuts_corner_touch_xlayer():
    out, rc = run_script("nuts_corner_touch.buda")
    assert_clean(out, rc, "nuts_corner_touch.buda")
    # Trunks split across two layers (M4 and M6).
    assert re.search(r"Bundle 1 .*\[V→M5 H→M4 V→M5\]", out)
    assert re.search(r"Bundle 2 .*\[V→M5 H→M6 V→M5\]", out)
    assert "[NUTS] corner-overlap pass: overlaps 1 -> 0." in out
    segs, viols, ovlps = nuts_summary(out)
    assert segs  == 6
    assert viols == 0
    assert ovlps == 0
    dm = re.search(
        r"\[DetailedNUTS\] (\d+) net segments placed, (\d+) bits unplaced", out)
    assert dm, "DetailedNUTS summary not found"
    assert int(dm.group(2)) == 0


# ---------------------------------------------------------------------------
# Hardening: a missing `source` must fail fast, and an empty layer stack must
# warn (regression guards for the "planner layer-assignment instability" false
# alarm — wishlist #3 — where an unresolvable relative source left no def_layers
# and run_planner silently fell back to M4/M5).
# ---------------------------------------------------------------------------

def _run_path(path: Path) -> tuple[str, int]:
    build_dir = _ROOT / "build"
    tools_dir = _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    new_ppath = f"{build_dir}:{tools_dir}:{ppath}" if ppath else f"{build_dir}:{tools_dir}"
    env = {**os.environ, "PYTHONPATH": new_ppath}
    r = subprocess.run([sys.executable, str(CLI), "--no-viz", str(path)],
                       capture_output=True, text=True, env=env)
    return r.stdout + r.stderr + "\n" + _flow_log_text(path), r.returncode


def test_source_missing_file_fails_fast(tmp_path):
    """`source` on a non-existent file aborts (exit 1) instead of silently
    continuing with the rest of the script (which would route on the wrong
    metal because no def_layers loaded)."""
    script = tmp_path / "broken.buda"
    script.write_text(
        "source ./does_not_exist.buda\n"
        "add_block a 0 0 100 100\n"
        "add_block b 400 0 500 100\n"
        "add_bus x[4] a b\n"
        "run_bundler\n"
        "generate_topologies\n"
        "run_planner\n"
    )
    out, rc = _run_path(script)
    assert rc == 1, f"expected exit 1 on missing source, got {rc}\n{out}"
    assert "sourced file not found" in out, out
    # It must have aborted at the source line — not reached the planner.
    assert "[Planner]" not in out, f"ran past the failed source:\n{out}"


def test_empty_layer_stack_warns(tmp_path):
    """A flow with no def_layer still runs (M4/M5 fallback) but emits a one-shot
    planner warning so the misconfiguration is visible."""
    script = tmp_path / "nolayers.buda"
    script.write_text(
        "add_block a 0 0 100 100\n"
        "add_block b 400 0 500 100\n"
        "add_bus x[4] a b\n"
        "run_bundler\n"
        "generate_topologies\n"
        "run_planner\n"
    )
    out, rc = _run_path(script)
    assert rc == 0, f"flow should still complete, got {rc}\n{out}"
    assert "WARNING: no" in out and "layers defined" in out, out


# ---------------------------------------------------------------------------
# dsl/comments.buda — inline `#` comments: everything from the first token-
# starting `#` to end of line is stripped, so a command can be commented out
# partially (`run_bundler # strict` runs `run_bundler`).
# ---------------------------------------------------------------------------

def test_dsl_inline_comments_repro():
    """The checked-in repro runs clean: inline comments after commands and args
    are stripped, so run_bundler/run_planner run with default (uncommented)
    behaviour rather than choking on the `#` token."""
    out, rc = run_script("dsl/comments.buda")
    assert_clean(out, rc, "dsl/comments.buda")
    # The comment `# strict` after run_bundler must have been dropped, not
    # parsed as a strategy — the pre-fix failure printed this error.
    assert "strategy must be" not in out, out
    # The pipeline actually ran end-to-end with the comments removed.
    assert "Bundler created 1 hbundles" in out, out
    segs, viol, over = nuts_summary(out)
    assert (viol, over) == (0, 0), f"expected clean NUTS, got viol={viol} over={over}\n{out}"


def test_inline_comment_boundary_rules(tmp_path):
    """A `#` only starts a comment at a token boundary (start of line or after
    whitespace). A `#` embedded in a token is preserved, so it can't silently
    swallow real arguments; a full-line comment and a trailing comment both
    strip cleanly without dropping the command's earlier args."""
    script = tmp_path / "inline.buda"
    script.write_text(
        "# a full-line comment\n"
        "def_layer 4 M4 H TOP 0.0   # trailing comment after all args\n"
        "add_block a 0 0 100 100     # place block a\n"
        "add_block b 400 0 500 100\n"
        "add_bus x[4] a b\n"
        "run_bundler # strict\n"
        "generate_topologies\n"
        "run_planner # signal_tracks\n"
        "run_nuts\n"
    )
    out, rc = _run_path(script)
    assert rc == 0, f"inline comments should not break the flow, got {rc}\n{out}"
    bad = [l for l in out.splitlines() if l.startswith("Error:")]
    assert not bad, "unexpected errors:\n" + "\n".join(bad)
    # def_layer kept its 5 real args despite the trailing comment.
    assert "strategy must be" not in out, out
    segs, viol, over = nuts_summary(out)
    assert (viol, over) == (0, 0), f"expected clean NUTS, got viol={viol} over={over}\n{out}"


@pytest.mark.slow
def test_bighalf_rr_reaches_clean_endpoint(tmp_path):
    """bigHalf reaches the clean 0 overlaps / 0 opens endpoint (ReadMe_bigHalf
    row 6).  Since opens #10 the checked-in bigHalf.buda ENABLES both
    `ripup_reroute 30` lines, so this test runs the checked-in flow AS-IS
    (only the relative `source` paths are absolutised for tmp_path) — the
    committed max_iter is what gets guarded, not a rewritten one, so a
    host that needs >10 iterations to converge fails here exactly as it
    would for a user running the flow.  The ENDPOINT is asserted, never
    intermediate counts or wall time (tc3a NUTS-stage counts are FP/CPU-
    sensitive under -march=native); the trial-budget bound guards against a
    trial-count blowup regression.  If the source is ever reverted to the
    bare/commented fast config, the normalise below re-injects the 30 budget
    so the endpoint stays CI-guarded either way."""
    src = FLOW / "big_data_test" / "bigHalf.buda"
    text = src.read_text()
    # Guard the COMMITTED budget: run as-is when the flow already carries a
    # numeric max_iter; only inject 30 if the source was reverted to bare or
    # commented form (so the test never silently guards a budget the flow
    # doesn't have).
    text = text.replace("# ripup_reroute", "ripup_reroute")     # legacy commented form
    text = re.sub(r"(?m)^ripup_reroute\s*$", "ripup_reroute 30", text)  # bare -> budgeted
    text = text.replace("source ../tracks/",
                        f"source {FLOW / 'tracks'}/")
    text = text.replace("source tc3a_flat_5x.buda",
                        f"source {FLOW / 'big_data_test' / 'tc3a_flat_5x.buda'}")
    script = tmp_path / "bigHalf_rr.buda"
    script.write_text(text)
    out, rc = _run_path(script)
    assert rc == 0, f"bigHalf rr-enabled aborted (rc={rc})\n{out[-3000:]}"
    # run output = terminal summary + flow log, so the done lines can appear
    # twice (and the terminal copy may be ellipsis-truncated) — take the
    # LAST two, which are the flow log's full stage-a / stage-b lines.
    done = re.findall(r"\[ripup_reroute\] done: metric .*?->(\S+(?: \(ovl \d+\))?) "
                      r"after \d+ move\(s\), (\d+) trial\(s\)", out)
    assert len(done) >= 2, f"expected two ripup runs\n{out[-3000:]}"
    st_a, st_b = done[-2], done[-1]
    # Stage a ends at 0 overlaps; stage b at 0 opens / 0 collateral overlaps.
    assert st_a[0] == "0", f"stage a not clean: {st_a}\n{out[-3000:]}"
    assert st_b[0] == "0 (ovl 0)", f"stage b not clean: {st_b}\n{out[-3000:]}"
    assert int(st_a[1]) + int(st_b[1]) < 600, \
        f"trial-count blowup: {done}"


def test_ndr_shield_flat_multi_rule():
    """flow/ndr_shield_flat.buda: three rules spanning the multiplier range
    (x1.5 / x2 / x2.5 width and spacing) and all three shield arrangements
    (bus / bit / per:N) + rail crediting, governing three bundles beside
    ungoverned traffic — clean endpoint, shields reported separately."""
    out, rc = run_script("ndr_shield_flat.buda")
    assert_clean(out, rc, "ndr_shield_flat.buda")
    # The three declared rules quantized as designed.
    assert "width x1.5 -> 2 slot(s)/bit" in out, out
    assert "spacing x2.5 -> 2 guard slot(s)/gap" in out, out
    assert "shield per:2" in out, out
    # STRICT merged s15+data on shared endpoints; the rule-class split
    # separated them LOUDLY.
    assert "rule-uniform part(s)" in out, out
    assert "[NDR] 3 bundle(s) governed by declared rules" in out, out
    # Clean endpoint: every bit placed, no NDR (or other) violations, and
    # the shield metal reported on its own line (R11).
    assert "0 bits unplaced" in out, out
    assert "no violations" in out and "NDR_" not in out, out
    assert "NDR shield metal:" in out, out


def test_ndr_shield_hier_multi_rule():
    """flow/ndr_shield_hier.buda: the R2d twin — the same three rule shapes
    governing a REAL sub_cell-level template class (one template + three
    lockstep replicas) and top-level buses beside ungoverned traffic."""
    out, rc = run_script("ndr_shield_hier.buda")
    assert_clean(out, rc, "ndr_shield_hier.buda")
    # 4 template-class occurrences + 2 governed top-level buses.
    assert "[NDR] 6 bundle(s) governed by declared rules" in out, out
    assert "0 bits unplaced" in out, out
    assert "no violations" in out and "NDR_" not in out, out
    assert "NDR shield metal:" in out, out


def test_ndr_bond_shield_bonding():
    """flow/ndr_bond.buda (requirement R6, the `bond` token): two shielded
    rules opt into bonding — one matching the grid rails by LABEL, one
    through the supply-family predicate (VSS shields on GND rails).  Every
    emitted shield must come out strapped to the grid, so the NDR_BOND
    floating-shield audit stays silent."""
    out, rc = run_script("ndr_bond.buda")
    assert_clean(out, rc, "ndr_bond.buda")
    assert "shield bus (net GND) bond" in out, out
    assert "shield bit (net VSS) bond" in out, out
    # Straps emitted and reported; no shield left floating.
    assert "NDR shield bond via(s) strapped" in out, out
    assert "0 bits unplaced" in out, out
    assert "no violations" in out and "NDR_" not in out, out


def test_ndr_bottom_up_composition():
    """flow/ndr_bottom_up.buda (requirement R13): a governed cell template
    marked set_bottom_up is solved once and COPIED to every instance,
    shield wires included, with an honest unplaced count.  Running this
    vehicle is what exposed the copy path's shield-inflated accounting
    (-6 unplaced before the fix)."""
    out, rc = run_script("ndr_bottom_up.buda")
    assert_clean(out, rc, "ndr_bottom_up.buda")
    # The class aligned and the template was solved once, then copied.
    assert "ALIGNED" in out, out
    assert "reference bit(s) solved once" in out, out
    assert "copied to 3 sibling instance(s)" in out, out
    # Honest accounting: no negative/masked unplaced count.
    assert "0 bits unplaced" in out, out
    assert "no violations" in out and "NDR_" not in out, out
    assert "NDR shield metal:" in out, out
