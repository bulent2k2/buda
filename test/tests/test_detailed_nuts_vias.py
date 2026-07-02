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

"""Per-bit vias (NetVia) emitted by Detailed NUTS.

Each bundle-level symbolic bus-via (a layer transition between two connected
segments) fans out to `bit_width` per-bit NetVias: bit i of one segment meets
bit i of the connected segment at the crossing of their individual placed
tracks. Emitted by the DNUTS engine (step 5) into DetailedNUTSResult.net_vias,
keyed (bundle_id, from_seg, to_seg, bit_index) with from_seg < to_seg —
the same (from_seg, to_seg) pair key as the symbolic bus-via.
"""

import contextlib
import io

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _routed(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, "source flow/rnr/mix_tracks.buda", *cmds)
    assert s.detailed_result.num_unplaced == 0    # clean placement is the premise
    return s


def _expected_cross_layer_pairs(s, w):
    """Cross-layer SEG-connected (min,max) seg pairs per the canonical
    ConnTopology connection model + NUTS layer assignment — the same derivation
    the symbolic bus-via uses."""
    topo = w.input.candidates[w.plan.selected_topology_index]
    bid = w.input.original_bundle.id
    ts_map = {ts.seg_idx: ts for ts in s.nuts_result.segments
              if ts.bundle_id == bid and ts.placed}
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    pairs = set()
    for a, cs in enumerate(ct.segs()):
        ta = ts_map.get(a)
        if ta is None:
            continue
        for conn in cs.conns:
            if conn.kind != buda.SegConnKind.SEG:
                continue
            tb = ts_map.get(conn.seg_idx)
            if tb is not None and ta.layer != tb.layer:
                pairs.add((min(a, conn.seg_idx), max(a, conn.seg_idx)))
    return pairs


def _assert_via_coords(s, vias):
    """Every via sits at the crossing of the two bits' placed tracks, within
    both bit-wires' spans (the check_dnuts reach criterion)."""
    ns_map = {(n.bundle_id, n.seg_idx, n.bit_index): n
              for n in s.detailed_result.net_segments}
    for v in vias:
        a = ns_map[(v.bundle_id, v.from_seg, v.bit_index)]
        b = ns_map[(v.bundle_id, v.to_seg, v.bit_index)]
        assert v.from_layer == a.layer and v.to_layer == b.layer
        assert v.from_layer != v.to_layer
        a_h = s.layers.get_layer_dir(a.layer) == buda.LayerDir.HORIZONTAL
        b_h = s.layers.get_layer_dir(b.layer) == buda.LayerDir.HORIZONTAL
        if a_h != b_h:                                # H<->V bend or T-junction
            h, vv = (a, b) if a_h else (b, a)
            assert (v.x, v.y) == (vv.track_position, h.track_position)
            # The crossing lies within both bit-wires' spans (reach test).
            assert min(h.span_lo, h.span_hi) <= v.x <= max(h.span_lo, h.span_hi)
            assert min(vv.span_lo, vv.span_hi) <= v.y <= max(vv.span_lo, vv.span_hi)


def test_l_bend_fans_out_per_bit(tmp_path):
    s = _routed("add_block A 0 0 200 400",
                "add_block B 600 800 800 1200",
                "add_bus d[8] A.p B.p",
                "run_bundler", "generate_topologies", "run_planner 3",
                "run_nuts", "run_detailed_nuts")
    w = s.bundles[0]
    pairs = _expected_cross_layer_pairs(s, w)
    assert pairs                                       # the L-bend transitions
    vias = s.detailed_result.net_vias
    got = {(v.from_seg, v.to_seg, v.bit_index) for v in vias}
    want = {(a, b, bit) for (a, b) in pairs for bit in range(8)}
    assert got == want                                 # pair count x bit_width
    assert len(vias) == len(got)                       # no duplicate rows
    assert all(v.from_seg < v.to_seg for v in vias)
    _assert_via_coords(s, vias)


def test_bits_get_distinct_via_positions(tmp_path):
    # The whole point of the fan-out: each bit crosses at its OWN track pair,
    # so per-pair via positions are all distinct.
    s = _routed("add_block A 0 0 200 400",
                "add_block B 600 800 800 1200",
                "add_bus d[8] A.p B.p",
                "run_bundler", "generate_topologies", "run_planner 3",
                "run_nuts", "run_detailed_nuts")
    by_pair = {}
    for v in s.detailed_result.net_vias:
        by_pair.setdefault((v.from_seg, v.to_seg), []).append((v.x, v.y))
    for pts in by_pair.values():
        assert len(set(pts)) == len(pts) == 8


def test_t_junction_multicast_per_bit(tmp_path):
    # 3-way multicast routes as a trunk with stubs landing on its INTERIOR
    # (T-junctions) — per-bit vias must cover those too, mirroring the
    # symbolic-via ConnTopology derivation.
    s = _routed("add_block A 0 550 200 650",
                "add_block B 900 100 1100 200",
                "add_block C 900 550 1100 650",
                "add_block D 900 1000 1100 1100",
                "add_bus bus[8] A.p B.p,C.p,D.p",
                "run_bundler", "generate_topologies", "run_planner 5",
                "run_nuts", "run_detailed_nuts")
    w = s.bundles[0]
    pairs = _expected_cross_layer_pairs(s, w)
    got = {(v.from_seg, v.to_seg, v.bit_index) for v in s.detailed_result.net_vias}
    want = {(a, b, bit) for (a, b) in pairs for bit in range(8)}
    assert got == want and want
    _assert_via_coords(s, s.detailed_result.net_vias)


def test_single_bit_bus(tmp_path):
    # bit_width=1: per-bit via count equals the symbolic pair count.
    s = _routed("add_block A 0 0 200 400",
                "add_block B 600 800 800 1200",
                "add_net solo A.p B.p",
                "run_bundler", "generate_topologies", "run_planner 3",
                "run_nuts", "run_detailed_nuts")
    pairs = _expected_cross_layer_pairs(s, s.bundles[0])
    vias = s.detailed_result.net_vias
    assert {(v.from_seg, v.to_seg) for v in vias} == pairs
    assert all(v.bit_index == 0 for v in vias)
    _assert_via_coords(s, vias)


def test_hi_lo_order_keeps_logical_bits(tmp_path):
    # bit_index is the LOGICAL bit (bit_order only moves which physical track a
    # bit lands on), so HI_LO emits the same via key set as LO_HI — only the
    # positions differ.
    def run(order):
        s = _routed("add_block A 0 0 200 400",
                    "add_block B 600 800 800 1200",
                    "add_bus d[8] A.p B.p",
                    "run_bundler", "generate_topologies", "run_planner 3",
                    "run_nuts", f"run_detailed_nuts {order}")
        return s
    lo = run("lo_hi")
    hi = run("hi_lo")
    keys = lambda s: {(v.from_seg, v.to_seg, v.bit_index)
                      for v in s.detailed_result.net_vias}
    assert keys(lo) == keys(hi) and keys(lo)
    _assert_via_coords(hi, hi.detailed_result.net_vias)
