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

"""Repros for big2's two residual failure classes (2026-07 baseline, after
the flow moved to `run_planner signal_tracks` + `negotiate_congestion` +
`ripup_reroute`, which clears every NUTS overlap on the full pipeline).

1. DNUTS opens — RESOLVED (was the live failure): the full flow used to end
   with 72 unplaced bits — two single-segment ABUTMENT-CROSSING bundles
   (bus_042/32b, bus_002/40b) whose only wire is a ~20-unit V stub crossing
   a shared block edge, which the planner assigned to LOW layer M3.  On a
   LOW layer the two leaf blocks' footprints are keepouts, and the stub's
   whole slide window lies inside them, so DetailedNUTS finds ZERO unblocked
   signal tracks — a guaranteed open that abstract NUTS reports as clean
   (overlaps=0, violations=0).  The planner-side fix (`low_seg_obstructed`,
   Gap A): an empty open interior between two DISTINCT abutting endpoint
   cells is flagged obstructed, so layer selection routes the crossing
   over-the-cell on TOP — big2's full flow now ends fully clean (0 overlaps,
   0 opens, ~1s).  `test_low_layer_abutment_stub_dnuts_open_repro` still
   reproduces the DNUTS-side mechanism in isolation by PINNING the M3
   assignment; `test_low_layer_abutment_stub_planner_avoids_low` asserts the
   planner-side guarantee.  History: wishlist-planner.md ("LOW-layer
   abutment crossings...", resolved).

2. Spread-fit NUTS overlap residue (the band-repack target,
   docs/internal/nuts_band_repack.md): WITHOUT the negotiation/ripup stages
   the same flow leaves overlaps that are pure placement clustering (the
   shared band fits every member).  This residue is a FULL-DESIGN phenomenon
   — the wedge comes from the other ~70 bundles' occupancy, so no subset
   extraction reproduces it (verified: re-running with only the involved
   buses, even with the full run's candidate/layer/band assignments pinned,
   packs cleanly).  `test_big2_prenegotiation_spreadfit_residue` is therefore
   the gate: big2 to `run_nuts`, negotiation skipped — which is FAST (~1s:
   big2's minutes live in negotiate/ripup/DNUTS, all skipped here).

   With the band-level cluster repack IMPLEMENTED (nuts_band_repack.md), the
   x86-64 measurement is 8 -> 5: the repack separates what pure re-packing
   can separate without drifting members off their planner-charged bands
   (drifting further cleared more abstract overlaps but stranded 16 bits at
   DNUTS on rnr/mix — the charged-band preference is the fix), and the
   remainder is the B79 star (a 292-wide trunk whose window is genuinely
   full — re-pin territory, cleared by the flow's negotiation) plus two
   guard-rejected pairs.  Asserted as host-robust bounds.
"""
import collections
import contextlib
import io
import re
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_BIG2 = _ROOT / "flow" / "big_data_test" / "big2"


def _run_lines(lines, skip=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in lines:
            c = c.strip()
            if not c or c.startswith("#"):
                continue
            if c.split()[0] in skip:
                continue
            s.do_command(c)
    return s


def _filtered_fixture(tmp_path, keep_prefixes):
    """tc3b_flat_x5 with the full floorplan but only the given buses' nets."""
    keep = tuple(p + "_b" for p in keep_prefixes)
    out = []
    for line in open(_BIG2 / "tc3b_flat_x5.buda"):
        c = line.strip()
        if c.startswith("add_net") and not c.split()[1].startswith(keep):
            continue
        out.append(line)
    path = tmp_path / "mini_fixture.buda"
    path.write_text("".join(out))
    return path


def _clusters(nuts_result):
    """Connected components of the overlap graph, with spread-fit tagging."""
    seg = {(t.bundle_id, t.seg_idx): t for t in nuts_result.segments}
    adj = collections.defaultdict(set)
    for od in nuts_result.overlap_details:
        a, b = (od.bid_a, od.seg_a), (od.bid_b, od.seg_b)
        adj[a].add(b)
        adj[b].add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            seen.add(x)
            stack.extend(adj[x] - comp)
        tot = sum(seg[m].width for m in comp)
        lo = min(seg[m].interval_lo for m in comp)
        hi = max(seg[m].interval_hi for m in comp)
        comps.append(dict(members=sorted(comp), layer={seg[m].layer for m in comp},
                          spread_fit=(tot <= hi - lo)))
    return comps


@pytest.mark.mid
def test_low_layer_abutment_stub_dnuts_open_repro(tmp_path, monkeypatch):
    """A LOW-layer abutment-crossing stub is a guaranteed DNUTS open: its
    band's signal tracks are all keepout-blocked by the two leaf blocks it
    crosses between — and abstract NUTS reports the placement as CLEAN.

    The planner no longer makes this assignment (low_seg_obstructed flags
    the abutment case), so the M3 layers are PINNED here to keep exercising
    the DNUTS-side mechanism: the abstract/detailed keepout-model mismatch
    stays observable if anything ever routes into it again.

    Pin data measured from the pre-fix full big2 run (2026-07, x86-64): both
    bundles single-segment, planner-assigned layer 3, band centres
    1770 / 3822."""
    monkeypatch.chdir(_BIG2)
    fixture = _filtered_fixture(tmp_path, ["bus_042", "bus_002"])
    s = _run_lines(["source tracks4top.buda", f"source {fixture}",
                    "run_bundler", "generate_topologies",
                    "run_planner signal_tracks"])
    pin = {("bus_042",): dict(layers=[3], perp=[1770]),
           ("bus_002",): dict(layers=[3], perp=[3822])}
    expected_bits = 0
    for w in s.bundles:
        nets = list(w.input.original_bundle.get_net_names())
        key = tuple(sorted({re.sub(r"_b\d+$", "", n) for n in nets}))
        d = pin[key]
        assert len(w.plan.seg_layers) == 1, "abutment crossing is one segment"
        w.plan.selected_topology_index = 0
        w.plan.seg_layers = d["layers"]
        w.plan.seg_perp = d["perp"]
        w.input.topology_pinned = True
        expected_bits += len(nets)
    assert expected_bits == 72

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_nuts")
        # The final cull heal now engages on ADMISSION strands too (its
        # entry gate was culled-bits-only), and this pinned shape is
        # exactly that class — hold the heal off so the un-healed
        # mechanism stays observable, which is this repro's subject.
        s._final_cull_heal_in_dnuts = False
        s.do_command("run_detailed_nuts")

    # Abstract NUTS thinks everything is fine...
    assert s.nuts_result.num_overlaps == 0
    assert s.nuts_result.num_violations == 0
    # ...and DetailedNUTS strands every bit of both buses (zero unblocked
    # signal tracks inside the leaf footprints on the LOW layer).
    assert s.detailed_result.num_unplaced == expected_bits
    open_bids = {w.input.original_bundle.id for w in s.bundles}
    placed_bids = {n.bundle_id for n in s.detailed_result.net_segments}
    assert not (open_bids & placed_bids), "no bit of either bus should place"

    # ...and the heal, left ON, clears exactly this shape: a 0-track seat
    # is measured-stranded with a span-pool shortfall, so the escalation
    # moves the stubs to TOP under the componentwise accept.
    with contextlib.redirect_stdout(io.StringIO()):
        s._final_cull_heal_in_dnuts = True
        healed = s._final_cull_heal()
    assert healed >= 1
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0


@pytest.mark.mid
def test_low_layer_abutment_stub_planner_avoids_low(tmp_path, monkeypatch):
    """The planner-side guarantee (the wishlist-planner.md fix): an abutment
    crossing — empty open interior between two DISTINCT abutting endpoint
    cells — is unroutable on a LOW layer (its whole slide window lies inside
    the two leaf footprints, zero unblocked signal tracks), so
    low_seg_obstructed flags it and layer selection routes it over-the-cell
    on TOP.  End-to-end: both buses land on a TOP V layer and every bit
    places."""
    monkeypatch.chdir(_BIG2)
    fixture = _filtered_fixture(tmp_path, ["bus_042", "bus_002"])
    s = _run_lines(["source tracks4top.buda", f"source {fixture}",
                    "run_bundler", "generate_topologies",
                    "run_planner signal_tracks",
                    "run_nuts", "run_detailed_nuts"])
    for w in s.bundles:
        assert len(w.plan.seg_layers) == 1, "abutment crossing is one segment"
        assert w.plan.seg_layers[0] in (5, 7), (
            f"abutment crossing assigned unroutable layer "
            f"{w.plan.seg_layers[0]} (expected a TOP V layer)")
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0


@pytest.mark.mid
def test_big2_prenegotiation_spreadfit_residue(monkeypatch):
    """The band-repack gate (docs/internal/nuts_band_repack.md): big2 without
    the negotiation/ripup stages.  Pre-repack this left 8 overlaps in 3
    spread-fit clusters; WITH the cluster repack the x86-64 measurement is 5
    (the B79 star — re-pin territory, negotiation's job — plus two
    guard-rejected pairs).  Bounds are host-robust (PR #203 pattern):
    the repack must keep the residue at/below the measured level, and a
    future improvement clearing it entirely should update
    nuts_band_repack.md and tighten this to zero."""
    monkeypatch.chdir(_BIG2)
    s = _run_lines(open(_BIG2 / "big2.buda").readlines(),
                   skip={"visualize", "check_design", "exit",
                         "negotiate_congestion", "ripup_reroute",
                         "run_detailed_nuts"})
    r = s.nuts_result
    # The repack guarantee: at/below the measured level.  Raised 6 -> 7 with
    # the big2-b25 interior-side face-tap fix
    # (docs/internal/big2_b25_abutment_tap_dnuts_2026-07.md): correcting the
    # crossed-vs-abutting endpoint tap unlocks tighter pass-through trunk
    # candidates that raise the raw (pre-negotiation) residue by one here; the
    # healer-equipped big2.buda still reaches 0/0/0 (and b25's own open heals).
    # Raised 7 -> 9 by the issue-#482 antenna fix, the SAME mechanism: dropping
    # the redundant collinear MST edge leg shortens those candidates, so the
    # planner picks tighter (lower-WL) ones here — big2's abstract WL improves
    # 343,037 -> 340,445 (-0.76%) and the healer-equipped big2.buda still
    # reaches 0/0/0.  This bound tracks the RAW pre-negotiation packing, which
    # every candidate-shape change perturbs; the end-to-end guarantee above
    # (test_big2_reaches_clean_endpoint) is the one that must never move.
    assert r.num_overlaps <= 9, (
        f"cluster repack regressed: {r.num_overlaps} residual overlaps "
        f"(pre-repack baseline was 8; post-#482 measurement 9)")
    if r.num_overlaps == 0:
        return   # even better — a future improvement cleared everything
    comps = _clusters(r)
    spread = [c for c in comps if c["spread_fit"]]
    assert spread, f"residue is not the spread-fit class anymore: {comps}"
