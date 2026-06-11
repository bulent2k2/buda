"""
Phase C tests: HierarchicalBundler on the pipeline test vehicle.

BDB setup mirrors _build_pipeline_bdb() from test_hier_testcase.py but
also calls add_net_pins for the four buses (8 bits each).
"""
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

scenarios('features/hier_bundler.feature')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_pipeline_bdb():
    db = buda.BDB(":memory:")
    db.add_cell("src_cell",  200, 200)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("snk_cell",  200, 200)
    db.add_cell("gen_cell",   80,  80)
    db.add_cell("buf_cell",   80,  80)
    db.add_cell("pipe_cell", 110,  80)
    db.add_cell("rcv_cell",   80,  80)
    db.add_cell("store_cell", 80,  80)
    db.add_inst_to_cell("src_cell",  "gen_i",   "gen_cell",    20,  60)
    db.add_inst_to_cell("src_cell",  "buf_i",   "buf_cell",   100,  60)
    db.add_inst_to_cell("proc_cell", "pa_i",    "pipe_cell",   20,  60)
    db.add_inst_to_cell("proc_cell", "pb_i",    "pipe_cell",  155,  60)
    db.add_inst_to_cell("proc_cell", "pc_i",    "pipe_cell",  290,  60)
    db.add_inst_to_cell("snk_cell",  "rcv_i",   "rcv_cell",    20,  60)
    db.add_inst_to_cell("snk_cell",  "store_i", "store_cell", 100,  60)
    db.add_inst("src_i",  "src_cell",  "",  50,  50)
    db.add_inst("proc_i", "proc_cell", "", 350,  50)
    db.add_inst("snk_i",  "snk_cell",  "", 870,  50)
    return db


def _add_pipeline_nets(db):
    for i in range(8):
        db.add_net_pins(f"s2p_{i}",   "src_i/buf_i.out",  ["proc_i/pa_i.in"])
        db.add_net_pins(f"pa_pb_{i}", "proc_i/pa_i.out",  ["proc_i/pb_i.in"])
        db.add_net_pins(f"pb_pc_{i}", "proc_i/pb_i.out",  ["proc_i/pc_i.in"])
        db.add_net_pins(f"p2s_{i}",   "proc_i/pc_i.out",  ["snk_i/rcv_i.in"])


def _run_hier_bundler(db, max_depth):
    gen = buda.BustermGen(db)
    gen.derive(max_depth)
    hb = buda.HierarchicalBundler(db)
    return hb.run(max_depth)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def context():
    return {}


# ── Background ─────────────────────────────────────────────────────────────────

@given("a BDB with the pipeline hierarchy and nets")
def load_pipeline_with_nets(context):
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    context['db'] = db


# ── When ───────────────────────────────────────────────────────────────────────

@when(parsers.re(r'run_hier_bundler is called with max_depth (?P<d>\d+)'))
def step_run_hier_bundler(context, d):
    max_depth = int(d)
    bundles = _run_hier_bundler(context['db'], max_depth)
    context['bundles'] = bundles
    context['by_net'] = {}
    for b in bundles:
        for n in b.net_names:
            context['by_net'][n] = b


# ── Bundle count steps ─────────────────────────────────────────────────────────

@then(parsers.re(r'there are (?P<n>\d+) hbundles$'))
def check_total_hbundles(context, n):
    got = len(context['bundles'])
    assert got == int(n), (
        f"Expected {n} hbundles, got {got}: "
        f"{[(b.id, b.level, b.reason[:40]) for b in context['bundles']]}"
    )


@then(parsers.re(r'there are (?P<n>\d+) hbundles at level (?P<l>\d+)$'))
def check_hbundles_at_level(context, n, l):
    level = int(l)
    found = [b for b in context['bundles'] if b.level == level]
    assert len(found) == int(n), (
        f"Expected {n} hbundles at level {level}, got {len(found)}: "
        f"{[(b.id, b.reason[:40]) for b in found]}"
    )


@then(parsers.re(r'an hbundle with (?P<n>\d+) nets exists at level (?P<l>\d+) with reason containing "(?P<fragment>[^"]+)"'))
def check_hbundle_exists_with_reason(context, n, l, fragment):
    level = int(l); count = int(n)
    matches = [b for b in context['bundles']
               if b.level == level and fragment in b.reason and len(b.net_names) == count]
    assert matches, (
        f"No level-{level} hbundle with {count} nets and reason containing {fragment!r}; "
        f"present: {[(b.reason, len(b.net_names)) for b in context['bundles'] if b.level == level]}"
    )


# ── Cell context steps ─────────────────────────────────────────────────────────

@then(parsers.re(r'there are (?P<n>\d+) hbundles with cell_context "(?P<ctx>[^"]+)"'))
def check_hbundles_with_cell_context(context, n, ctx):
    found = [b for b in context['bundles'] if b.cell_context == ctx]
    assert len(found) == int(n), (
        f"Expected {n} hbundles with cell_context={ctx!r}, got {len(found)}: "
        f"{[(b.id, b.reason[:40]) for b in found]}"
    )


@then(parsers.re(r'there are (?P<n>\d+) hbundles at level (?P<l>\d+) with empty cell_context'))
def check_hbundles_at_level_no_context(context, n, l):
    level = int(l)
    found = [b for b in context['bundles'] if b.level == level and b.cell_context == ""]
    assert len(found) == int(n), (
        f"Expected {n} level-{level} hbundles with empty cell_context, got {len(found)}"
    )


@then(parsers.re(r'every hbundle with cell_context "(?P<ctx>[^"]+)" has instance "(?P<inst>[^"]+)"'))
def check_cell_context_instances(context, ctx, inst):
    for b in context['bundles']:
        if b.cell_context == ctx:
            assert inst in b.instances, (
                f"hbundle {b.id} (reason={b.reason!r}) has cell_context={ctx!r} "
                f"but instances={b.instances!r}, expected {inst!r} in it"
            )


# ── Depth linkage steps ────────────────────────────────────────────────────────

@then(parsers.re(r'the depth-1 bundle for "(?P<net>[^"]+)" has a depth-0 parent'))
def check_depth1_has_parent(context, net):
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle found containing net {net!r}"
    assert b.level == 1, f"Bundle for {net!r} is at level {b.level}, expected 1"
    assert b.parent_id != -1, (
        f"Bundle {b.id} for net {net!r} has no parent (parent_id=-1); "
        f"reason={b.reason!r}"
    )
    parent = next((p for p in context['bundles'] if p.id == b.parent_id), None)
    assert parent is not None, f"parent_id={b.parent_id} not found in bundles"
    assert parent.level == 0, f"parent bundle level={parent.level}, expected 0"


@then(parsers.re(r'the depth-1 bundle for "(?P<net>[^"]+)" has no parent bundle'))
def check_depth1_no_parent(context, net):
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle found containing net {net!r}"
    assert b.level == 1, f"Bundle for {net!r} is at level {b.level}, expected 1"
    assert b.parent_id == -1, (
        f"Bundle {b.id} for net {net!r} unexpectedly has parent_id={b.parent_id}"
    )


# ── Busterm id steps ───────────────────────────────────────────────────────────

@then(parsers.re(r'the bundle for "(?P<net>[^"]+)" has entry_busterm "(?P<bt>[^"]+)"'))
def check_entry_busterm(context, net, bt):
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle found containing net {net!r}"
    assert bt in b.entry_busterm_ids, (
        f"Bundle {b.id}: entry_busterm_ids={b.entry_busterm_ids!r}, expected {bt!r}"
    )


@then(parsers.re(r'the bundle for "(?P<net>[^"]+)" has exit_busterm "(?P<bt>[^"]+)"'))
def check_exit_busterm(context, net, bt):
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle found containing net {net!r}"
    assert bt in b.exit_busterm_ids, (
        f"Bundle {b.id}: exit_busterm_ids={b.exit_busterm_ids!r}, expected {bt!r}"
    )


# ── Standalone unit tests ──────────────────────────────────────────────────────

def test_depth0_only_cross_block():
    """At depth 0, only cross-block nets produce bundles (intra-proc nets invisible)."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 0)
    assert len(bundles) == 2, f"Expected 2, got {len(bundles)}: {[b.reason for b in bundles]}"
    levels = {b.level for b in bundles}
    assert levels == {0}
    reasons = [b.reason for b in bundles]
    assert any("DRV:src_i"  in r for r in reasons)
    assert any("DRV:proc_i" in r for r in reasons)


def test_depth1_four_bundles():
    """At depth 1, all four buses produce bundles; two are intra-proc."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    assert len(bundles) == 6, f"Expected 6, got {len(bundles)}"
    d0 = [b for b in bundles if b.level == 0]
    d1 = [b for b in bundles if b.level == 1]
    assert len(d0) == 2
    assert len(d1) == 4


def test_intra_proc_cell_context():
    """pa_pb and pb_pc bundles have cell_context=proc_cell, instances=[proc_i]."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    intra = [b for b in bundles if b.cell_context == "proc_cell"]
    assert len(intra) == 2, f"Expected 2 intra-proc bundles, got {len(intra)}"
    for b in intra:
        assert b.instances == ["proc_i"], f"instances={b.instances!r}"
        assert b.level == 1


def test_cross_block_depth_linkage():
    """Depth-1 s2p/p2s bundles are linked to their depth-0 parents."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    by_net = {n: b for b in bundles for n in b.net_names}
    by_id  = {b.id: b for b in bundles}

    for net in ("s2p_0", "p2s_0"):
        b = by_net[net]
        assert b.parent_id != -1, f"{net}: expected parent, got parent_id=-1"
        parent = by_id[b.parent_id]
        assert parent.level == 0, f"{net}: parent level={parent.level}"
        assert b.id in parent.child_ids


def test_intra_proc_no_depth_linkage():
    """Intra-proc bundles (pa_pb, pb_pc) have no cross-depth parent."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    by_net = {n: b for b in bundles for n in b.net_names}
    for net in ("pa_pb_0", "pb_pc_0"):
        b = by_net[net]
        assert b.parent_id == -1, f"{net}: expected no parent, got parent_id={b.parent_id}"


def test_busterm_ids():
    """Entry/exit busterm ids are set for intra-proc bundles."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    by_net = {n: b for b in bundles for n in b.net_names}
    pa_pb = by_net["pa_pb_0"]
    assert "bt:proc_i/pa_i" in pa_pb.entry_busterm_ids
    assert "bt:proc_i/pb_i" in pa_pb.exit_busterm_ids
    pb_pc = by_net["pb_pc_0"]
    assert "bt:proc_i/pb_i" in pb_pc.entry_busterm_ids
    assert "bt:proc_i/pc_i" in pb_pc.exit_busterm_ids


def test_net_names_per_bundle():
    """Each depth-1 bundle contains exactly 8 nets."""
    db = _build_pipeline_bdb(); _add_pipeline_nets(db)
    bundles = _run_hier_bundler(db, 1)
    for b in bundles:
        assert len(b.net_names) == 8, (
            f"Bundle {b.id} level={b.level} reason={b.reason!r}: "
            f"expected 8 nets, got {len(b.net_names)}: {b.net_names}"
        )


def test_multiple_occurrence_two_proc_instances():
    """When two proc_cell instances exist, matching bundles share a cell_local_sig."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110,  80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell",  20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    # Two instances of proc_cell
    db.add_inst("proc_i1", "proc_cell", "",   0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 0)
    # Nets for each proc instance
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])

    bundles = _run_hier_bundler(db, 1)

    # Expect: 2 depth-1 bundles (one per proc instance at depth-1 before merging)
    # After multiple-occurrence merging: template has instances=[proc_i1, proc_i2]
    d1 = [b for b in bundles if b.level == 1]
    assert len(d1) == 2, f"Expected 2 depth-1 bundles, got {len(d1)}"

    # The template bundle should have both instances
    template = next((b for b in d1 if len(b.instances) == 2), None)
    assert template is not None, (
        f"No template bundle with 2 instances; d1 instances: "
        f"{[b.instances for b in d1]}"
    )
    assert set(template.instances) == {"proc_i1", "proc_i2"}

    # The replica bundle should reference the template
    replica = next((b for b in d1 if b.id != template.id), None)
    assert replica is not None
    assert replica.parent_id == template.id
    assert replica.id in template.child_ids


# ── UNKNOWN direction tests ────────────────────────────────────────────────────

def _build_two_block_bdb():
    """Minimal BDB: two top-level blocks A and B, no sub-hierarchy."""
    db = buda.BDB(":memory:")
    db.add_cell("cell_a", 100, 100)
    db.add_cell("cell_b", 100, 100)
    db.add_inst("a_i", "cell_a", "",   0, 0)
    db.add_inst("b_i", "cell_b", "", 200, 0)
    return db


def test_add_net_pins_undirected_stores_unknown_dir():
    """add_net_pins_undirected stores all pins with dir=UNKNOWN."""
    db = _build_two_block_bdb()
    db.add_net_pins_undirected("clk", ["a_i.clk", "b_i.clk"])
    pins = db.all_pins()
    clk_pins = [p for p in pins if p.pin_name == "clk"]
    assert len(clk_pins) == 2, f"Expected 2 clk pins, got {len(clk_pins)}"
    for p in clk_pins:
        assert p.dir == "UNKNOWN", f"Expected UNKNOWN, got {p.dir!r}"


def test_hier_bundler_bundles_unknown_direction_net():
    """HierarchicalBundler creates a bundle for a net with all-UNKNOWN pins."""
    db = _build_two_block_bdb()
    db.add_net_pins_undirected("clk", ["a_i.clk", "b_i.clk"])
    gen = buda.BustermGen(db)
    gen.derive(0)
    hb = buda.HierarchicalBundler(db)
    bundles = hb.run(0)
    net_names = [n for b in bundles for n in b.net_names]
    assert "clk" in net_names, (
        f"Net 'clk' with UNKNOWN direction was not bundled; bundles: "
        f"{[(b.reason, b.net_names) for b in bundles]}"
    )


def test_hier_bundler_unknown_pins_positional_driver_is_first():
    """With UNKNOWN pins, the first-encountered pin becomes the driver (positional)."""
    db = _build_two_block_bdb()
    # Insert undirected: a_i is listed first → should be treated as driver
    db.add_net_pins_undirected("sig", ["a_i.out", "b_i.in"])
    gen = buda.BustermGen(db)
    gen.derive(0)
    hb = buda.HierarchicalBundler(db)
    bundles = hb.run(0)
    sig_bundle = next((b for b in bundles if "sig" in b.net_names), None)
    assert sig_bundle is not None, "Net 'sig' was not bundled"
    # Driver should be a_i (depth-0 comp), receiver b_i
    assert "DRV:a_i" in sig_bundle.reason, (
        f"Expected DRV:a_i in reason, got {sig_bundle.reason!r}"
    )


def test_hier_bundler_mixed_directed_and_unknown_nets():
    """Directed and undirected nets co-exist and both get bundled."""
    db = _build_two_block_bdb()
    db.add_net_pins("data", "a_i.out", ["b_i.in"])         # directed
    db.add_net_pins_undirected("clk", ["a_i.clk", "b_i.clk"])  # undirected
    gen = buda.BustermGen(db)
    gen.derive(0)
    hb = buda.HierarchicalBundler(db)
    bundles = hb.run(0)
    all_nets = {n for b in bundles for n in b.net_names}
    assert "data" in all_nets, "Directed net 'data' was not bundled"
    assert "clk"  in all_nets, "Undirected net 'clk' was not bundled"


def test_add_net_pins_undirected_single_pin_produces_no_bundle():
    """A net with only one UNKNOWN pin has no receiver — should not be bundled."""
    db = _build_two_block_bdb()
    db.add_net_pins_undirected("lonely", ["a_i.out"])
    gen = buda.BustermGen(db)
    gen.derive(0)
    hb = buda.HierarchicalBundler(db)
    bundles = hb.run(0)
    net_names = {n for b in bundles for n in b.net_names}
    assert "lonely" not in net_names, (
        "A single-pin undirected net should not form a bundle (no receiver)"
    )
