import pytest
import interconnect

def test_min_stub_length_exhaustive():
    fp = interconnect.Floorplan()
    
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
    tg = interconnect.TopologyGenerator(fp)
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
    fp.set_min_stub_length_dir(interconnect.LayerDir.VERTICAL, 60)
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
    # Let's just verify that NO multicast candidate has a tiny stub.
    for cand in mc_cands:
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            is_stub = any(c.kind == interconnect.SegConnKind.BUSTERM for c in cs.conns)
            if is_stub:
                length = abs(cs.along_hi - cs.along_lo)
                m = fp.get_min_stub_length(interconnect.LayerDir.VERTICAL if not cs.horiz else interconnect.LayerDir.HORIZONTAL, cs.layer_id)
                assert length >= m, f"Multicast stub in {cand.type} too short: {length} < {m}"

def test_z_u_uu_min_stub():
    fp = interconnect.Floorplan()
    fp.add_block("u1", 0, 0, 100, 100)
    fp.add_block("u2", 200, 200, 300, 300)
    fp.set_min_stub_length(50)
    
    tg = interconnect.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    candidates = tg.generate_candidates("u1", "u2")
    
    for cand in candidates:
        ct = interconnect.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            is_stub = any(c.kind == interconnect.SegConnKind.BUSTERM for c in cs.conns)
            if is_stub:
                length = abs(cs.along_hi - cs.along_lo)
                # Check for Z, U, UU
                assert length >= 50, f"Stub in {cand.type} too short: {length} < 50"

if __name__ == "__main__":
    test_min_stub_length_exhaustive()
    test_z_u_uu_min_stub()
    print("Exhaustive min-stub tests passed!")
