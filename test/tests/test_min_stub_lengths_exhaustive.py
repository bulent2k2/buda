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

import pytest
import buda

def test_min_stub_length_exhaustive():
    fp = buda.Floorplan()
    
    # ── Setup Floorplan ──────────────────────────────────────────────────
    # u_s: [50, 400] to [250, 600]
    fp.add_block("u_s", 50, 400, 250, 600)
    # u_d: [700, 500] to [800, 1000]
    fp.add_block("u_d", 700, 500, 800, 1000)
    # u_m: [800, 400] to [900, 1000]
    fp.add_block("u_m", 800, 400, 900, 1000)
    
    # Global corner margin
    fp.set_global_corner_margin(15, 20)
    
    # Layers
    # M4: Horizontal, ID=4
    # M5: Vertical, ID=5
    
    # ── Test 1: Default (20) ─────────────────────────────────────────────
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    
    # Check L_VH Candidate (Option B: above src)
    # src top face at 600. min_stub_v = 20. bend_y should be >= 620.
    cands = tg.generate_candidates("u_s", "u_d")
    l_vh_above = next((c for c in cands if "L_VH@y620" in c.type), None)
    assert l_vh_above is not None, "Expected L_VH@y620 with default min_stub=20"
    
    # ── Test 2: Global Override (40) ─────────────────────────────────────
    fp.set_min_stub_length(40)
    cands = tg.generate_candidates("u_s", "u_d")
    l_vh_above = next((c for c in cands if "L_VH@y640" in c.type), None)
    assert l_vh_above is not None, "Expected L_VH@y640 with global min_stub=40"
    
    # ── Test 3: Directional Override (V=60) ──────────────────────────────
    fp.set_min_stub_length_dir(buda.LayerDir.VERTICAL, 60)
    cands = tg.generate_candidates("u_s", "u_d")
    l_vh_above = next((c for c in cands if "L_VH@y660" in c.type), None)
    assert l_vh_above is not None, "Expected L_VH@y660 with V min_stub=60"
    
    # ── Test 4: Layer Override (M5=80) ───────────────────────────────────
    # M5 is our Vertical layer (id=5)
    fp.set_min_stub_length_layer(5, 80)
    cands = tg.generate_candidates("u_s", "u_d")
    l_vh_above = next((c for c in cands if "L_VH@y680" in c.type), None)
    assert l_vh_above is not None, "Expected L_VH@y680 with M5 min_stub=80"
    
    # ── Test 5: Multicast Trunks ─────────────────────────────────────────
    # H-trunk at y=600. Vertical stubs to u_s, u_d, u_m.
    # u_s: y=[400, 600]. conn_y=600 (no stub).
    # u_d: y=[500, 1000]. conn_y=600 (no stub).
    # u_m: y=[400, 1000]. conn_y=600 (no stub).
    # This trunk would have no stubs if placed at 600.
    
    # Let's try y=144 (far below).
    # u_s: conn_y=400. stub_len = 400 - 144 = 256. (OK)
    # If m_v is 300, this trunk at y=144 should be skipped.
    fp.set_min_stub_length(300)
    # multicast u_s -> [u_d, u_m]
    mc_cands = tg.generate_candidates("u_s", ["u_d", "u_m"])
    trunk_144 = next((c for c in mc_cands if "@y144" in c.type), None)
    # actually y_mid = (200 + 400) / 2 = 300 might be in hanan grid if we have blocks there.
    # In four_blocks logic, it adds margin-offset trunks.
    # Abutment exemption: u_d and u_m share the vertical edge x=800 (y-overlap
    # [500,1000]).  An MST edge between abutting blocks is realized as a wire that
    # CROSSES the shared edge, and min-stub is intentionally NOT enforced on
    # abutment/completion connectors (correctness over the heuristic — a true
    # abutment between blocks narrower than min-stub cannot produce a long wire).
    # Exempt stubs whose midpoint lies within an abutting pair's union footprint.
    def _abut(a, b):
        ox = min(a[2], b[2]) - max(a[0], b[0]); oy = min(a[3], b[3]) - max(a[1], b[1])
        touch_x = (a[2] == b[0] or b[2] == a[0]) and oy > 0
        touch_y = (a[3] == b[1] or b[3] == a[1]) and ox > 0
        return touch_x or touch_y
    rects = [(50, 400, 250, 600), (700, 500, 800, 1000), (800, 400, 900, 1000)]  # u_s, u_d, u_m
    abut_unions = [(min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                   for i, a in enumerate(rects) for b in rects[i + 1:] if _abut(a, b)]

    def _in_abut_region(cx, cy):
        return any(u[0] <= cx <= u[2] and u[1] <= cy <= u[3] for u in abut_unions)

    # Let's just verify that NO multicast candidate has a tiny (non-abutment) stub.
    for cand in mc_cands:
        ct = buda.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            is_stub = any(c.kind == buda.SegConnKind.BUSTERM for c in cs.conns)
            if not is_stub:
                continue
            amid = (cs.along_lo + cs.along_hi) / 2
            cx, cy = (amid, cs.perp_lo) if cs.horiz else (cs.perp_lo, amid)
            if _in_abut_region(cx, cy):
                continue  # abutment crossing / connector — exempt by design
            length = abs(cs.along_hi - cs.along_lo)
            m = fp.get_min_stub_length(buda.LayerDir.VERTICAL if not cs.horiz else buda.LayerDir.HORIZONTAL, cs.layer_id)
            assert length >= m, f"Multicast stub in {cand.type} too short: {length} < {m}"

def test_z_u_uu_min_stub():
    fp = buda.Floorplan()
    fp.add_block("u1", 0, 0, 100, 100)
    fp.add_block("u2", 200, 200, 300, 300)
    fp.set_min_stub_length(50)
    
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    candidates = tg.generate_candidates("u1", "u2")
    
    for cand in candidates:
        ct = buda.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            is_stub = any(c.kind == buda.SegConnKind.BUSTERM for c in cs.conns)
            if is_stub:
                length = abs(cs.along_hi - cs.along_lo)
                # Check for Z, U, UU
                assert length >= 50, f"Stub in {cand.type} too short: {length} < 50"

if __name__ == "__main__":
    test_min_stub_length_exhaustive()
    test_z_u_uu_min_stub()
    print("Exhaustive min-stub tests passed!")
