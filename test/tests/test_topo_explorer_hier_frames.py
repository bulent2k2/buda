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

"""Explorer per-bundle frames (wishlist-topoedit follow-on #2).

The TopologyExplorer resolves each shown bundle's OWN floorplan via the
session's `_make_topo_fp_resolver` (the same `_floorplan_for_hbundle` cases
check_design and the CLI edit session use): a hier CELL-LOCAL template draws
and edits against its cell frame — cell-local block names and coordinates —
while flat bundles, expanded instance wrappers, and same-level hier bundles
stay on the session floorplan.  Before this, a template's candidate drew at
cell coordinates over ABSOLUTE-frame blocks: wrong backdrop, no busterm
highlight, and `S`/`P` block hit-tests resolving the wrong namespace.
"""
import contextlib
import io
import shutil
from types import SimpleNamespace

import pytest

import matplotlib
matplotlib.use("Agg")

import buda      # noqa: E402
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

pytestmark = pytest.mark.mid

LAYERS = ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10")


def _build_design(path, cross=False):
    """proc_cell (pa_i, pb_i) instantiated twice; 4 cell-local nets per
    instance, plus (cross=True) 4 level-0 nets between the instances."""
    db = buda.BDB(path)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
        if cross:
            db.add_net_pins(f"x_{i}", "proc_i1/pb_i.out", ["proc_i2/pa_i.in"])
    db.compute_all()


def _hier_session(db_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in (*LAYERS, f"open_bdb {db_path}",
              "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
              "derive_busterms 1", "run_hier_bundler depth 1",
              "generate_hier_topologies"):
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _explorer(s, tmp_path, start_bidx=0):
    return buda_viz.TopologyExplorer(
        s.fp, s.bundles, sidecar_path=str(tmp_path / "sc.json"),
        layer_stack=s.layers, start_bidx=start_bidx,
        fp_resolver=s._make_topo_fp_resolver())


def _key(exp, key, x=None, y=None):
    exp._on_key(SimpleNamespace(key=key, xdata=x, ydata=y))


def _tmpl_idx(s):
    return next(i for i, w in enumerate(s.bundles)
                if len(list(w.input.original_bundle.instances)) == 2)


def test_explorer_opens_template_in_cell_frame(tmp_path):
    """Opening on a cell-local template swaps self.fp to the CELL frame:
    cell-local block names, cell-extent Hanan grid, cell-frame hit-tests."""
    db = str(tmp_path / "d.bdb")
    _build_design(db)
    s = _hier_session(db)
    exp = _explorer(s, tmp_path, start_bidx=_tmpl_idx(s))
    try:
        assert sorted(n for n, _ in exp.fp.get_all_blocks()) == ["pa_i", "pb_i"]
        assert exp._block_at(50, 100) == "pa_i"       # cell coords, cell names
        xs, ys = exp._bundle_hanan_grid()
        assert max(xs) < 500                          # cell extent, not the die
        assert exp._bundle_busterm_names() == {"pa_i", "pb_i"}
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_gui_gestures_build_template_topo_in_cell_frame(tmp_path):
    """The full interaction language works on a template: E, two-step T
    trunk on a cell-local row, S stubs hitting CELL-LOCAL blocks under the
    cursor, commit + pin — the committed USER candidate carries cell-local
    taps (exactly what expansion replicates and the BDB persists)."""
    db = str(tmp_path / "d.bdb")
    _build_design(db)
    s = _hier_session(db)
    ti = _tmpl_idx(s)
    exp = _explorer(s, tmp_path, start_bidx=ti)
    try:
        _key(exp, 'E')
        _key(exp, 'T', 100, 30); _key(exp, 'T', 100, 30)   # cell-local row
        _key(exp, 'S', x=50, y=100)                        # over pa_i
        _key(exp, 'S', x=200, y=100)                       # over pb_i
        assert 'clean' in exp._edit_msg
        _key(exp, 'enter')
        w = s.bundles[ti]
        t = w.input.candidates[w.plan.selected_topology_index]
        assert t.type == "USER" and w.input.topology_pinned
        assert sorted(t.connected_block_names) == ["pa_i", "pb_i"]
        # And the committed shape is in cell coordinates.
        assert all(0 <= sg.start.x <= 420 and 0 <= sg.end.x <= 420
                   for sg in t.segments)
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_frame_swaps_between_template_and_level0_bundle(tmp_path):
    """Paging between a cell-local template and a level-0 cross-block bundle
    swaps the frame both ways (cell names <-> session floorplan) and marks
    the view for re-home each time (the extents differ wildly)."""
    db = str(tmp_path / "d.bdb")
    _build_design(db, cross=True)
    s = _hier_session(db)
    exp = _explorer(s, tmp_path, start_bidx=_tmpl_idx(s))
    try:
        assert exp._block_at(50, 100) == "pa_i"            # cell frame
        # Page until a NON-cell-context bundle is shown (the level-0 cross).
        for _ in range(len(s.bundles)):
            if not exp.wrapper.input.original_bundle.cell_context:
                break
            _key(exp, ']')
        assert not exp.wrapper.input.original_bundle.cell_context
        assert exp.fp is s.fp                              # session frame
        assert exp._block_at(50, 100) in ("proc_i1", "proc_i1/pa_i")
        # And back to a template: the cell frame returns.
        for _ in range(len(s.bundles)):
            if exp.wrapper.input.original_bundle.cell_context:
                break
            _key(exp, '[')
        assert exp.fp is not s.fp
        assert exp._block_at(50, 100) == "pa_i"
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')


def test_gui_template_edit_survives_flow_rerun_via_sidecar(tmp_path):
    """Convergence of the two persistence paths: a GUI template edit records
    CELL-FRAME ops in the sidecar; a fresh run of the same flow replays them
    through the CLI edit session — which is frame-aware since opens item 12 —
    so the USER candidate is rebuilt in the right frame and the pin resolves."""
    pristine = str(tmp_path / "pristine.bdb")
    _build_design(pristine)
    flow = tmp_path / "f.buda"       # constant script path → constant sidecar
    runs = [0]

    def run_flow():
        # Fresh binary AT A FRESH PATH per run — mirrors how .bdb.sql fixtures
        # materialize a throwaway copy (re-running never re-derives a dirty
        # DB), and avoids overwriting an inode a lingering prior-session
        # SQLite connection could still flush stale pages onto.
        runs[0] += 1
        live = str(tmp_path / f"live{runs[0]}.bdb")
        shutil.copy(pristine, live)
        flow.write_text("\n".join((
            *LAYERS, f"open_bdb {live}",
            "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
            "derive_busterms 1", "run_hier_bundler depth 1",
            "generate_hier_topologies")) + "\n")
        s = buda_cli.BudaSession()
        s.no_viz = True
        s.script_path = str(flow)
        outs = []
        for line in flow.read_text().splitlines():
            with contextlib.redirect_stdout(io.StringIO()) as b:
                s.do_command(line.strip())
            outs.append(b.getvalue())
        return s, "".join(outs)

    s1, _ = run_flow()
    ti = _tmpl_idx(s1)
    exp = buda_viz.TopologyExplorer(
        s1.fp, s1.bundles, sidecar_path=str(tmp_path / "f.json"),
        layer_stack=s1.layers, start_bidx=ti,
        fp_resolver=s1._make_topo_fp_resolver())
    try:
        _key(exp, 'E')
        _key(exp, 'T', 100, 30); _key(exp, 'T', 100, 30)
        _key(exp, 'S', x=50, y=100)
        _key(exp, 'S', x=200, y=100)
        _key(exp, 'enter')
        w1 = s1.bundles[ti]
        uid = buda.topo_uid(
            w1.input.candidates[w1.plan.selected_topology_index])
        tid = w1.input.original_bundle.id
    finally:
        import matplotlib.pyplot as plt
        plt.close('all')
    del s1

    s2, out = run_flow()
    assert "rebuilding USER candidate" in out
    assert "could not be resolved" not in out
    w2 = next(w for w in s2.bundles if w.input.original_bundle.id == tid)
    sel = w2.plan.selected_topology_index
    assert w2.input.topology_pinned
    assert w2.input.candidates[sel].type == "USER"
    assert buda.topo_uid(w2.input.candidates[sel]) == uid
