"""
Phase B3 tests: BustermRow.rects encode/decode and HierBusterm.rects roundtrip.

Tests cover:
  - BustermRow.rects persists through BDB add_busterm / all_busterms
  - HierBusterm.rects decoded from stored JSON
  - derive() stores empty rects for single-rect components
  - Manual busterm with multi-rect geometry survives roundtrip
  - PORT resolution assignment for leaf cells with declared pins
  - refine() re-derives same state as derive()
"""
import math
import pytest
import buda


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_simple_db():
    """Two placed leaf cells at depth 0, one with cell_pins."""
    db = buda.BDB(":memory:")
    db.add_cell("cell_a", 100, 100)
    db.add_cell("cell_b",  80,  80)
    db.add_cell_pin("cell_a", "out", "OUTPUT", 100.0, 50.0)
    db.add_inst("a_i", "cell_a", "",   0, 0)
    db.add_inst("b_i", "cell_b", "", 200, 0)
    return db


# ── BustermRow.rects roundtrip ─────────────────────────────────────────────────

def test_busterm_row_rects_empty_default():
    """BustermRow.rects is empty string by default."""
    row = buda.BustermRow()
    assert row.rects == ""


def test_busterm_row_rects_persists_via_bdb():
    """A BustermRow with rects string persists through add_busterm / all_busterms."""
    db = buda.BDB(":memory:")
    db.add_cell("c", 50, 50)
    db.add_inst("ci", "c", "", 0, 0)

    row = buda.BustermRow()
    row.id        = "bt:ci"
    row.comp_id   = db.all_components()[0].id
    row.hier_path = "ci"
    row.depth     = 0
    row.x1 = 0.0; row.y1 = 0.0
    row.x2 = 50.0; row.y2 = 50.0
    row.resolution = "BLOCK"
    row.rects = "[[0,0,20,50],[30,0,50,50]]"

    db.add_busterm(row)
    stored = db.all_busterms()

    assert len(stored) == 1
    assert stored[0].rects == "[[0,0,20,50],[30,0,50,50]]"


def test_busterm_row_empty_rects_stored_and_retrieved():
    """A BustermRow with empty rects string round-trips correctly."""
    db = buda.BDB(":memory:")
    db.add_cell("c", 50, 50)
    db.add_inst("ci", "c", "", 0, 0)

    row = buda.BustermRow()
    row.id        = "bt:ci"
    row.comp_id   = db.all_components()[0].id
    row.hier_path = "ci"
    row.depth     = 0
    row.x1 = row.y1 = 0.0; row.x2 = row.y2 = 50.0
    row.resolution = "SPATIAL_CLUSTER"
    row.rects = ""

    db.add_busterm(row)
    stored = db.all_busterms()
    assert stored[0].rects == ""


# ── HierBusterm.rects roundtrip via BustermGen ────────────────────────────────

def test_hierbusterm_rects_empty_after_derive():
    """derive() produces HierBusterms with empty rects (single-rect components)."""
    db = _make_simple_db()
    gen = buda.BustermGen(db)
    gen.derive(0)
    hbts = gen.all()
    assert len(hbts) == 2
    for hbt in hbts:
        assert hbt.rects == [], f"Expected empty rects for single-rect comp, got {hbt.rects}"


def test_hierbusterm_rects_roundtrip_manual():
    """Manually storing a multi-rect busterm and reading via BustermGen.all()."""
    db = buda.BDB(":memory:")
    db.add_cell("teg", 200, 100)
    db.add_inst("teg_i", "teg", "", 0, 0)

    # Manually insert a multi-rect busterm (simulating TEG geometry)
    row = buda.BustermRow()
    row.id        = "bt:teg_i"
    row.comp_id   = db.all_components()[0].id
    row.hier_path = "teg_i"
    row.depth     = 0
    row.x1 = 0.0; row.y1 = 0.0
    row.x2 = 200.0; row.y2 = 100.0
    row.resolution = "BLOCK"
    row.rects = "[[0,0,80,100],[120,0,200,100]]"

    db.add_busterm(row)
    gen = buda.BustermGen(db)
    hbts = gen.all()

    assert len(hbts) == 1
    hbt = hbts[0]
    assert len(hbt.rects) == 2, f"Expected 2 rects, got {len(hbt.rects)}"
    r0 = hbt.rects[0]
    r1 = hbt.rects[1]
    assert r0 == (0.0, 0.0, 80.0, 100.0), f"rect0: {r0}"
    assert r1 == (120.0, 0.0, 200.0, 100.0), f"rect1: {r1}"


def test_hierbusterm_rects_float_precision():
    """Rects with fractional coordinates survive encode/decode."""
    db = buda.BDB(":memory:")
    db.add_cell("c", 10, 10)
    db.add_inst("ci", "c", "", 0, 0)

    row = buda.BustermRow()
    row.id = "bt:ci"; row.comp_id = db.all_components()[0].id
    row.hier_path = "ci"; row.depth = 0
    row.x1 = row.y1 = 0.0; row.x2 = row.y2 = 10.0
    row.resolution = "BLOCK"
    row.rects = "[[1.5,2.5,3.75,4.25]]"

    db.add_busterm(row)
    gen = buda.BustermGen(db)
    hbts = gen.all()
    assert len(hbts[0].rects) == 1
    x1, y1, x2, y2 = hbts[0].rects[0]
    assert math.isclose(x1, 1.5) and math.isclose(y1, 2.5)
    assert math.isclose(x2, 3.75) and math.isclose(y2, 4.25)


def test_hierbusterm_rects_four_rects():
    """A busterm with four rects decodes to all four tuples."""
    db = buda.BDB(":memory:")
    db.add_cell("c", 100, 100)
    db.add_inst("ci", "c", "", 0, 0)

    row = buda.BustermRow()
    row.id = "bt:ci"; row.comp_id = db.all_components()[0].id
    row.hier_path = "ci"; row.depth = 0
    row.x1 = row.y1 = 0.0; row.x2 = row.y2 = 100.0
    row.resolution = "BLOCK"
    row.rects = "[[0,0,20,20],[30,0,50,20],[60,0,80,20],[90,0,100,20]]"

    db.add_busterm(row)
    gen = buda.BustermGen(db)
    hbt = gen.all()[0]
    assert len(hbt.rects) == 4
    assert hbt.rects[0] == (0.0, 0.0, 20.0, 20.0)
    assert hbt.rects[3] == (90.0, 0.0, 100.0, 20.0)


# ── PORT resolution assignment ─────────────────────────────────────────────────

def test_derive_assigns_port_resolution_for_leaf_with_cell_pins():
    """derive() assigns PORT resolution to leaf components whose cell has cell_pins."""
    db = _make_simple_db()  # cell_a has an "out" pin declared
    gen = buda.BustermGen(db)
    gen.derive(0)
    hbts = {h.hier_path: h for h in gen.all()}

    # a_i uses cell_a which has a declared cell_pin → should be PORT
    assert hbts["a_i"].resolution == buda.BustermResolution.PORT, (
        f"Expected PORT for a_i (cell_a has cell_pins), got {hbts['a_i'].resolution}"
    )
    # b_i uses cell_b which has no cell_pins → should be SPATIAL_CLUSTER
    assert hbts["b_i"].resolution == buda.BustermResolution.SPATIAL_CLUSTER, (
        f"Expected SPATIAL_CLUSTER for b_i (cell_b has no cell_pins), got {hbts['b_i'].resolution}"
    )


def test_derive_assigns_block_resolution_for_non_leaf():
    """derive() assigns BLOCK resolution to non-leaf hierarchical components."""
    db = buda.BDB(":memory:")
    db.add_cell("top_cell", 500, 500)
    db.add_cell("child_cell", 100, 100)
    db.add_inst_to_cell("top_cell", "ch_i", "child_cell", 50, 50)
    db.add_inst("top_i", "top_cell", "", 0, 0)

    gen = buda.BustermGen(db)
    gen.derive(1)
    hbts = {h.hier_path: h for h in gen.all()}

    assert hbts["top_i"].resolution == buda.BustermResolution.BLOCK
    assert hbts["top_i/ch_i"].resolution == buda.BustermResolution.SPATIAL_CLUSTER


# ── refine() consistency ───────────────────────────────────────────────────────

def test_refine_reproduces_derive_state():
    """refine() produces the same busterm set as a fresh derive()."""
    db = _make_simple_db()
    gen = buda.BustermGen(db)
    gen.derive(0)
    first = {h.hier_path: h.resolution for h in gen.all()}

    gen.refine()
    second = {h.hier_path: h.resolution for h in gen.all()}

    assert first == second, f"refine() diverged: {first} vs {second}"


def test_refine_clears_and_rewrites():
    """refine() clears the busterm table before re-deriving (no duplicates)."""
    db = _make_simple_db()
    gen = buda.BustermGen(db)
    gen.derive(0)
    count_after_derive = len(gen.all())

    gen.refine()
    count_after_refine = len(gen.all())

    assert count_after_refine == count_after_derive, (
        f"refine() duplicated entries: {count_after_refine} vs {count_after_derive}"
    )


def test_derive_max_depth_stored_for_refine():
    """refine() uses the max_depth from the last derive() call."""
    db = buda.BDB(":memory:")
    db.add_cell("top_cell", 500, 500)
    db.add_cell("child_cell", 100, 100)
    db.add_inst_to_cell("top_cell", "ch_i", "child_cell", 50, 50)
    db.add_inst("top_i", "top_cell", "", 0, 0)

    gen = buda.BustermGen(db)
    gen.derive(1)
    after_d1 = len(gen.all())   # should include top_i and top_i/ch_i

    gen.refine()
    after_refine = len(gen.all())

    assert after_refine == after_d1, (
        f"refine() did not use stored max_depth=1: {after_refine} vs {after_d1}"
    )
