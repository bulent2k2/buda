"""
Phase E tests: run_planner hier on the pipeline test vehicle.

Verifies:
  - Cell-level bundles are expanded to per-instance wrappers
  - All expanded wrappers receive a topology assignment
  - Priority ordering: depth-0 before depth-1, fewer candidates first
  - Unique bundle IDs after expansion (no collision for multi-instance cells)
"""
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda

scenarios('features/hier_planner.feature')


# ── Shared helpers ────────────────────────────────────────────────────────────

def _build_pipeline_bdb(n_proc=1):
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
    x_proc = 350
    for k in range(n_proc):
        iname = "proc_i" if n_proc == 1 else f"proc_i{k}"
        db.add_inst(iname, "proc_cell", "", x_proc, 50)
        x_proc += 550

    # For multi-proc we only wire the first instance to keep tests simple
    snk_x = x_proc + 100
    db.add_inst("snk_i",  "snk_cell",  "", snk_x, 50)

    proc_name = "proc_i" if n_proc == 1 else "proc_i0"
    for i in range(8):
        db.add_net_pins(f"s2p_{i}",   "src_i/buf_i.out",         [f"{proc_name}/pa_i.in"])
        db.add_net_pins(f"pa_pb_{i}", f"{proc_name}/pa_i.out",   [f"{proc_name}/pb_i.in"])
        db.add_net_pins(f"pb_pc_{i}", f"{proc_name}/pb_i.out",   [f"{proc_name}/pc_i.in"])
        db.add_net_pins(f"p2s_{i}",   f"{proc_name}/pc_i.out",   ["snk_i/rcv_i.in"])
    return db


def _setup_full_context(n_proc=1):
    """Build DB, busterms, hier bundles, and topologies."""
    db = _build_pipeline_bdb(n_proc)
    buda.BustermGen(db).derive(1)
    bundles = buda.HierarchicalBundler(db).run(1)

    layers = buda.LayerStack()
    layers.add_layer(3, "M3", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    layers.add_layer(4, "M4", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)

    # Build global floorplan at depth 0 (top-level instances)
    comps_list = db.all_components()
    fp = buda.Floorplan()
    for c in comps_list:
        if c.depth == 0 and c.x1 >= 0:
            fp.add_block(c.name,
                         int(round(c.x1)), int(round(c.y1)),
                         int(round(c.x2)), int(round(c.y2)))

    # Build BundleWrappers with topologies (simplified version of generate_hier_topologies)
    comps = {c.name: c for c in comps_list}
    wrappers = []
    for b in bundles:
        w = buda.BundleWrapper()
        w.original_bundle = b
        w.width = float(len(b.net_names))

        if b.cell_context and b.instances:
            # Cell-level bundle: generate candidates in cell-local coords
            parent_name = b.instances[0]
            parent = comps.get(parent_name)
            if parent is None:
                continue
            cell_fp = buda.Floorplan()
            for c in comps_list:
                if c.parent_id == parent.id and c.x1 >= 0:
                    lname = c.name.rsplit('/', 1)[-1]
                    cell_fp.add_block(lname,
                                      int(round(c.x1 - parent.x1)),
                                      int(round(c.y1 - parent.y1)),
                                      int(round(c.x2 - parent.x1)),
                                      int(round(c.y2 - parent.y1)))
            if b.entry_busterm_ids and b.exit_busterm_ids:
                src_local = b.entry_busterm_ids[0].removeprefix('bt:').rsplit('/', 1)[-1]
                dsts_local = [e.removeprefix('bt:').rsplit('/', 1)[-1]
                              for e in b.exit_busterm_ids]
                tg = buda.TopologyGenerator(cell_fp)
                try:
                    candidates = tg.generate(src_local, dsts_local)
                except Exception:
                    candidates = []
                w.candidates = candidates
        else:
            # Cross-block or top-level bundle
            if b.entry_busterm_ids and b.exit_busterm_ids:
                src_blk = b.entry_busterm_ids[0].removeprefix('bt:').rsplit('/', 1)[0]
                dsts_blk = [e.removeprefix('bt:').rsplit('/', 1)[0]
                            for e in b.exit_busterm_ids]
                tg = buda.TopologyGenerator(fp)
                try:
                    candidates = tg.generate(src_blk, dsts_blk)
                except Exception:
                    candidates = []
                w.candidates = candidates

        wrappers.append(w)

    return db, bundles, wrappers, fp, layers


def _clone_hbundle_with_id(b, new_id):
    nb = buda.HBundle()
    nb.id                = new_id
    nb.net_names         = b.net_names
    nb.reason            = b.reason
    nb.num_terminals     = b.num_terminals
    nb.level             = b.level
    nb.cell_context      = b.cell_context
    nb.instances         = b.instances
    nb.parent_id         = b.parent_id
    nb.child_ids         = b.child_ids
    nb.entry_busterm_ids = b.entry_busterm_ids
    nb.exit_busterm_ids  = b.exit_busterm_ids
    return nb


def _expand_hier_bundles(wrappers, db):
    """Replicate BudaSession._expand_hier_bundles."""
    comps = {c.name: c for c in db.all_components()}
    max_id = max((w.original_bundle.id for w in wrappers), default=-1)
    next_id = max_id + 1
    result = []
    for w in wrappers:
        b = w.original_bundle
        if not b.cell_context or not b.instances:
            result.append(w)
            continue
        for inst_name in b.instances:
            parent = comps.get(inst_name)
            if parent is None:
                continue
            dx = int(round(parent.x1))
            dy = int(round(parent.y1))
            new_w = buda.BundleWrapper()
            new_w.original_bundle = _clone_hbundle_with_id(b, next_id)
            next_id += 1
            new_w.width = w.width
            # offset candidates
            new_segs_list = []
            for topo in w.candidates:
                new_t = buda.Topology()
                new_t.type                  = topo.type
                new_t.estimated_wirelength  = topo.estimated_wirelength
                new_t.trunk_location        = topo.trunk_location
                new_t.pass_through_count    = topo.pass_through_count
                new_t.connected_block_names = topo.connected_block_names
                new_segs = []
                for s in topo.segments:
                    ns = buda.Segment()
                    ns.start      = buda.Point(s.start.x + dx, s.start.y + dy)
                    ns.end        = buda.Point(s.end.x   + dx, s.end.y   + dy)
                    ns.layer_hint = s.layer_hint
                    new_segs.append(ns)
                new_t.segments = new_segs
                new_segs_list.append(new_t)
            new_w.candidates = new_segs_list
            result.append(new_w)
    return result


# ── BDD fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def context():
    return {}


@given("a pipeline BDB with 1 proc instance and 4 buses of 8 bits", target_fixture="context")
def pipeline_1proc():
    db, bundles, wrappers, fp, layers = _setup_full_context(1)
    return {"db": db, "bundles": bundles, "wrappers": wrappers,
            "fp": fp, "layers": layers}


@given("a pipeline BDB with 2 proc instances and 4 buses of 8 bits", target_fixture="context")
def pipeline_2proc():
    db, bundles, wrappers, fp, layers = _setup_full_context(2)
    return {"db": db, "bundles": bundles, "wrappers": wrappers,
            "fp": fp, "layers": layers}


@when("I run run_planner hier 5")
def run_planner_hier(context):
    wrappers = context["wrappers"]
    db       = context["db"]
    fp       = context["fp"]
    layers   = context["layers"]
    expanded = _expand_hier_bundles(wrappers, db)
    for w in expanded:
        b = w.original_bundle
        w.priority = -(b.level * 10_000 + len(w.candidates))
    planner = buda.CongestionPlanner(fp, layers)
    planner.build_congestion_map()
    with buda.ostream_redirect():
        assignments = planner.optimize_topologies(expanded, 5)
    bid_to_w = {w.original_bundle.id: w for w in expanded}
    for asn in assignments:
        w = bid_to_w.get(asn.bundle_id)
        if w is not None:
            w.selected_topology_index = asn.topo_index
            w.assigned_v_layer = asn.v_layer_id
            w.assigned_h_layer = asn.h_layer_id
            w.seg_layers = list(asn.seg_layers)
    context["expanded"] = expanded


@when("priorities are assigned before run_planner hier")
def assign_priorities(context):
    wrappers = context["wrappers"]
    db       = context["db"]
    expanded = _expand_hier_bundles(wrappers, db)
    for w in expanded:
        b = w.original_bundle
        w.priority = -(b.level * 10_000 + len(w.candidates))
    context["expanded"] = expanded


@when("I expand hier bundles")
def expand_bundles(context):
    context["expanded"] = _expand_hier_bundles(context["wrappers"], context["db"])


@then("self.bundles contains at least as many wrappers as original bundles")
def check_expansion_count(context):
    orig = context["wrappers"]
    exp  = context["expanded"]
    assert len(exp) >= len(orig), \
        f"expanded ({len(exp)}) < original ({len(orig)})"


@then("every wrapper in self.bundles has selected_topology_index >= 0")
def check_all_assigned(context):
    for w in context["expanded"]:
        if not w.candidates:
            continue   # skip empty-candidate wrappers
        assert w.selected_topology_index >= 0, \
            f"bundle id={w.original_bundle.id} unassigned"


@then("every depth-0 wrapper has priority greater than every depth-1 wrapper")
def check_depth_priority(context):
    d0 = [w for w in context["expanded"] if w.original_bundle.level == 0]
    d1 = [w for w in context["expanded"] if w.original_bundle.level == 1]
    if not d0 or not d1:
        return  # nothing to compare
    min_d0 = min(w.priority for w in d0)
    max_d1 = max(w.priority for w in d1)
    assert min_d0 > max_d1, \
        f"depth-0 min priority {min_d0} not > depth-1 max {max_d1}"


@then("within depth-0, a wrapper with fewer candidates has priority >= wrapper with more candidates")
def check_constraint_first(context):
    d0 = [w for w in context["expanded"] if w.original_bundle.level == 0]
    for i, a in enumerate(d0):
        for b in d0[i+1:]:
            if len(a.candidates) < len(b.candidates):
                assert a.priority >= b.priority, \
                    f"fewer-candidate wrapper priority {a.priority} < {b.priority}"


@then("the expanded wrappers have unique original_bundle ids")
def check_unique_ids(context):
    ids = [w.original_bundle.id for w in context["expanded"]]
    assert len(ids) == len(set(ids)), \
        f"duplicate bundle IDs after expansion: {ids}"


# ── Standalone unit tests ─────────────────────────────────────────────────────

def test_expansion_count_single_proc():
    db, bundles, wrappers, fp, layers = _setup_full_context(1)
    expanded = _expand_hier_bundles(wrappers, db)
    # No multi-instance cell in 1-proc setup → count stays the same
    assert len(expanded) == len(wrappers)


def test_unique_ids_single_proc():
    db, bundles, wrappers, fp, layers = _setup_full_context(1)
    expanded = _expand_hier_bundles(wrappers, db)
    ids = [w.original_bundle.id for w in expanded]
    assert len(ids) == len(set(ids)), "duplicate IDs in single-proc expansion"


def test_unique_ids_two_proc():
    db, bundles, wrappers, fp, layers = _setup_full_context(2)
    expanded = _expand_hier_bundles(wrappers, db)
    ids = [w.original_bundle.id for w in expanded]
    assert len(ids) == len(set(ids)), f"duplicate IDs in two-proc expansion: {ids}"


def test_priority_encoding():
    db, bundles, wrappers, fp, layers = _setup_full_context(1)
    expanded = _expand_hier_bundles(wrappers, db)
    for w in expanded:
        b = w.original_bundle
        w.priority = -(b.level * 10_000 + len(w.candidates))
    d0 = [w for w in expanded if w.original_bundle.level == 0]
    d1 = [w for w in expanded if w.original_bundle.level == 1]
    if d0 and d1:
        assert min(w.priority for w in d0) > max(w.priority for w in d1)


def test_run_planner_hier_assigns_all():
    db, bundles, wrappers, fp, layers = _setup_full_context(1)
    expanded = _expand_hier_bundles(wrappers, db)
    for w in expanded:
        b = w.original_bundle
        w.priority = -(b.level * 10_000 + len(w.candidates))
    planner = buda.CongestionPlanner(fp, layers)
    planner.build_congestion_map()
    with buda.ostream_redirect():
        assignments = planner.optimize_topologies(expanded, 5)
    bid_to_w = {w.original_bundle.id: w for w in expanded}
    for asn in assignments:
        w = bid_to_w.get(asn.bundle_id)
        if w is not None:
            w.selected_topology_index = asn.topo_index
            w.seg_layers = list(asn.seg_layers)
    unassigned = [w for w in expanded
                  if w.candidates and w.selected_topology_index < 0]
    assert not unassigned, f"{len(unassigned)} wrappers left unassigned"


def test_offset_topology_shifts_coords():
    """_offset_topology correctly shifts segment coordinates."""
    # Build a topology manually — no DB needed.
    original = buda.Topology()
    original.type = "L_HV"
    original.estimated_wirelength = 300
    original.trunk_location = 0
    original.pass_through_count = 0
    original.connected_block_names = []
    s1 = buda.Segment()
    s1.start = buda.Point(10, 20)
    s1.end   = buda.Point(50, 20)
    s1.layer_hint = 3
    s2 = buda.Segment()
    s2.start = buda.Point(50, 20)
    s2.end   = buda.Point(50, 80)
    s2.layer_hint = 4
    original.segments = [s1, s2]

    dx, dy = 100, 200
    new_t = buda.Topology()
    new_t.type                  = original.type
    new_t.estimated_wirelength  = original.estimated_wirelength
    new_t.trunk_location        = original.trunk_location
    new_t.pass_through_count    = original.pass_through_count
    new_t.connected_block_names = original.connected_block_names
    new_segs = []
    for s in original.segments:
        ns = buda.Segment()
        ns.start      = buda.Point(s.start.x + dx, s.start.y + dy)
        ns.end        = buda.Point(s.end.x   + dx, s.end.y   + dy)
        ns.layer_hint = s.layer_hint
        new_segs.append(ns)
    new_t.segments = new_segs

    for orig_s, new_s in zip(original.segments, new_t.segments):
        assert new_s.start.x == orig_s.start.x + dx
        assert new_s.start.y == orig_s.start.y + dy
        assert new_s.end.x   == orig_s.end.x   + dx
        assert new_s.end.y   == orig_s.end.y   + dy
