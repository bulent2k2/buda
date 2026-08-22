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

"""TEG_OPEN — the missing-bridge audit (teg_multirect_status.md open 1(b)).

A `teg_mode over` multi-rect block declares its rects NOT internally
connected, so every rect needs external attachment by the bundle's PLACED
metal.  Bridges are generation-only today (nothing downstream of generation
consumes Topology::bridge_segments), so a selected bridged candidate routes
WITHOUT the bridge — and before this audit, check_design reported Success on
that electrically open net (measured: the §1.1 repro in
docs/internal/teg_multirect_status.md).

These tests drive the full flat pipeline through BudaSession and read
check_design's structured verdict, so they pin the audit end to end at both
placed stages.  check_topo deliberately does NOT carry the kind (it feeds
generation gates, dogleg trials and healer metrics — a reporting audit must
not start dropping candidates), so there is no topo-stage assertion here.
"""
import contextlib
import io

import buda
import buda_cli


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


_TRACKS = [
    "def_track_pattern 4 0 (SIGNAL 2 2)x8",
    "def_track_pattern 5 0 (SIGNAL 2 2)x8",
]


def _lshape_cmds(teg):
    return [
        "add_block src 500 150 600 250",
        f"add_block L rect 0 0 100 400 rect 0 0 400 100{teg}",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS


def _pin_bridged_candidate(s):
    """Pin the first candidate carrying a bridge over L (1-based index)."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if dict(c.bridge_segments):
            with contextlib.redirect_stdout(io.StringIO()):
                s.do_command(f"select_topology 1 {i + 1}")
            return c
    raise AssertionError("no bridged candidate generated for the L-shape")


def test_missing_bridge_is_teg_open_at_nuts_and_dnuts():
    # The §1.1 repro: pin a bridged candidate, route, audit.  The bridge is
    # placed by nothing, so the rect it was meant to connect is untouched.
    s = _session(_lshape_cmds(" teg_mode over"))
    cand = _pin_bridged_candidate(s)
    assert dict(cand.bridge_segments), "vehicle must rely on a bridge"

    for cmd in ("run_planner", "run_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "nuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) >= 1, out
    assert "OVER block 'L'" in out and "(nuts)" in out

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_detailed_nuts")
    verdict, out = _check(s, "dnuts")
    # Per-bit audit (Codex P1 on #821): each of the 4 bits is its own net and
    # each individually fails to reach the tall arm, so 4 violations, which
    # the summary collapses into one per-block group line.
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 4, out
    assert "Block 'L'" in out


def test_thru_block_is_exempt_by_design():
    # Same geometry, default thru: internal equivalence is the declared
    # meaning, so the audit must stay silent whatever candidate wins.
    s = _session(_lshape_cmds(""))
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_gap_stub_places_clean_after_retraction_fix():
    # The disjoint-gap OVER branch stubs BOTH rects to the trunk, so when the
    # stubs place as generated, connectivity holds through the trunk and the
    # audit passes (its union-face bridge is redundant metal, not a missing
    # link — teg_multirect_status.md §1.3, last paragraph).
    #
    # They used to NOT place as generated: the far gap-stub was emitted
    # trunk → face, so emit_tap_segment seeded its busterm on the TRUNK end;
    # derive_conn_segs' per-block BUSTERM dedup let that bogus tap shadow the
    # real face-end annotation, and NUTS — left with no face anchor —
    # retracted the stub to the trunk (span [150,158] against a face at 300,
    # both orientations), firing BUSTERM_FACE + ANTENNA + TEG_OPEN here.
    # FIXED (claude/teg-gap-stub-fix): the far stub is emitted face → trunk
    # like every other stub, so this test now pins the clean expectation —
    # the flip its earlier comment promised as the fix's proof.  The direct
    # annotation/placement regression is
    # test_teg_gap_stub_annotation.py (both orientations).
    s = _session([
        "add_block T rect 0 300 200 400 rect 0 0 200 100 teg_mode over",
        "add_block src 400 150 500 250",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx T.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # Pin an H trunk in the gap (y 100..300): the gap-stub pair + bridge shape.
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y") and dict(c.bridge_segments):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 100 < y < 300:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no gap-trunk bridged candidate found"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out
    assert verdict["by_kind"].get("BUSTERM_FACE", 0) == 0, out


def test_all_span_trunk_touches_every_rect_no_teg_open():
    # A trunk crossing EVERY rect of the OVER block (all_span at generation:
    # no bridge emitted, none needed) must audit clean: placed contact per
    # rect is the predicate, not bridge presence.
    s = _session([
        "add_block L rect 0 0 100 400 rect 0 0 400 100 teg_mode over",
        "add_block src 500 20 600 80",
        "def_layer 4 M4 H TOP 0",
        "def_layer 5 M5 V TOP 0",
        "add_bus d[4] src.tx L.rx",
        "run_bundler STRICT",
        "generate_topologies",
    ] + _TRACKS)
    w = s.bundles[0]
    # An H trunk at y<100 crosses BOTH rects (base y 0..100, arm y 0..400).
    pinned = None
    for i, c in enumerate(w.input.candidates):
        if c.type.startswith("TRUNK_H@y") and not dict(c.bridge_segments):
            y = int(c.type.split("@y")[1].split("+")[0])
            if 0 < y < 100:
                pinned = c
                with contextlib.redirect_stdout(io.StringIO()):
                    s.do_command(f"select_topology 1 {i + 1}")
                break
    assert pinned is not None, "no bridgeless low trunk candidate found"
    for cmd in ("run_planner", "run_nuts", "run_detailed_nuts"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(cmd)
    verdict, out = _check(s, "dnuts")
    assert verdict["by_kind"].get("TEG_OPEN", 0) == 0, out


def test_check_topo_does_not_carry_teg_open():
    # The kind is placed-stage only: check_topo feeds generation gates and
    # healer metrics, so it must not report TEG_OPEN even on the repro
    # candidate (the pool must not shrink over a reporting audit).
    s = _session(_lshape_cmds(" teg_mode over"))
    cand = _pin_bridged_candidate(s)
    ct = buda.ConnTopology()
    ct.build(cand, s.fp)
    res = buda.check_topo(ct, cand, s.fp, 1)
    kinds = {v.kind for v in res.violations}
    assert buda.ViolationKind.TEG_OPEN not in kinds


# ── the two Codex P1 refinements (#821 review) ────────────────────────────────

def _lshape_fp_over():
    fp = buda.Floorplan()
    fp.add_block_rects("L", [(0, 0, 100, 400),      # tall arm
                             (0, 0, 400, 100)])     # wide base
    fp.set_block_teg_mode("L", buda.TegMode.OVER)
    fp.add_block("src", 500, 150, 600, 250)
    return fp


def _lshape_candidate(fp):
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    cands = g.generate_candidates("src", ["L"])
    topo = cands[0]
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return topo, ct


def _layers():
    ls = buda.LayerStack()
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    return ls


def _h_seg_indices(ct):
    return [i for i, cs in enumerate(ct.segs()) if cs.horiz]


def _wire(si, bit, y, x_lo, x_hi):
    ns = buda.NetSegment()
    ns.bundle_id = 1
    ns.seg_idx = si
    ns.bit_index = bit
    ns.layer = 4
    ns.track_position = float(y)
    ns.span_lo, ns.span_hi = float(x_lo), float(x_hi)
    ns.width = 1.0
    return ns


def test_per_bit_metal_is_not_pooled():
    # Codex P1 (#821): bit 0's wire touches only the base (y=50, x from 150 —
    # right of the arm) and bit 1's only the arm (y=150 — above the base).
    # Pooled metal touches both rects and would audit clean; per-bit each net
    # misses a rect, so BOTH bits must fire.
    fp = _lshape_fp_over()
    topo, ct = _lshape_candidate(fp)
    h = _h_seg_indices(ct)
    assert h, "need an H segment to host the fabricated wires"
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [
        _wire(h[0], 0, 50, 150, 500),    # base only (arm is x<=100)
        _wire(h[0], 1, 150, 50, 500),    # arm only (base is y<=100)
    ]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 2)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert {v.bit_index for v in teg} == {0, 1}, [v.message for v in teg]


def test_touched_but_split_islands_fire_teg_open():
    # Codex P1 (#821): one bit, two wires on segments that are NOT joined by
    # any placed metal of the bit (their structural junction runs through a
    # third segment whose wire is absent) — every rect is touched, but the
    # contacts sit in different metal islands, so joining them would rely on
    # the block interior OVER revokes.  island_roots cannot see this (it
    # unions same-BLOCK taps); the audit must.
    fp = _lshape_fp_over()
    # Search the pool for a candidate holding two H segments with NO direct
    # SEG conn (a Z / V-trunk shape: two H stubs joined only through the V
    # trunk) — the trunk's wire is then left out of the group, so the two
    # stub wires are genuine islands.
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    topo = ct = None
    h = []
    for cand in g.generate_candidates("src", ["L"]):
        c = buda.ConnTopology()
        c.build(cand, fp)
        hh = [i for i, cs in enumerate(c.segs()) if cs.horiz]
        for a in range(len(hh)):
            for b in range(a + 1, len(hh)):
                direct = any(cn.kind == buda.SegConnKind.SEG and
                             cn.seg_idx == hh[b]
                             for cn in c.segs()[hh[a]].conns)
                if not direct:
                    topo, ct, h = cand, c, [hh[a], hh[b]]
                    break
            if topo is not None:
                break
        if topo is not None:
            break
    assert topo is not None, "no candidate with two unjoined H segments"
    si2 = h[1]
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [
        _wire(h[0], 0, 150, 50, 300),    # island A: arm only
        _wire(si2, 0, 50, 150, 500),     # island B: base only
    ]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 1)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert teg and any("islands" in v.message for v in teg), \
        [v.message for v in teg]


def test_joined_contacts_audit_clean():
    # Control for the island verdict: ONE wire touching both rects (an H wire
    # at y=50 from x=50 crosses arm and base alike) is one island — no
    # TEG_OPEN, whatever else the fabricated placement violates.
    fp = _lshape_fp_over()
    topo, ct = _lshape_candidate(fp)
    h = _h_seg_indices(ct)
    dnuts = buda.DetailedNUTSResult()
    dnuts.net_segments = [_wire(h[0], 0, 50, 50, 500)]
    res = buda.check_dnuts(ct, dnuts, topo, fp, _layers(), 1, 1)
    teg = [v for v in res.violations if v.kind == buda.ViolationKind.TEG_OPEN]
    assert not teg, [v.message for v in teg]
