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
Thin command layer for the BUDA floorplanner prototype.

The GUI owns selection and drawing state.  Placement semantics stay in
FloorplannerEngine so the same operations are testable without Tk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import os
import subprocess
import sys
from typing import Iterable

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda


@dataclass
class BlockNode:
    """One node in the component hierarchy tree."""
    name:     str
    cell:     str
    x1: float
    y1: float
    x2: float
    y2: float
    depth:    int
    children: list["BlockNode"] = field(default_factory=list)

    @property
    def leaf_name(self) -> str:
        return self.name.split("/")[-1]

    @property
    def label(self) -> str:
        return f"{self.leaf_name}  [{self.cell}]"


def _try_acquire_write_lock(path: str) -> int | None:
    """Try a non-blocking exclusive flock on `path`.

    Returns an open file descriptor (the caller must keep it alive to hold the
    lock) or None if another process already holds the lock.  The OS releases
    the lock automatically when the fd is closed or the process exits, so
    there are no stale-lock issues.

    Falls back to None (no lock, always writable) on platforms without fcntl.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        try:
            os.close(fd)
        except Exception:
            pass
        return None
    except Exception:
        # File not yet created, unsupported platform, etc. — allow write.
        return None


def release_bdb_lock(state: "FloorplannerAppState") -> None:
    """Release the exclusive write lock held by this session, if any."""
    if state._lock_fd is not None:
        try:
            fcntl.flock(state._lock_fd, fcntl.LOCK_UN)
            os.close(state._lock_fd)
        except Exception:
            pass
        state._lock_fd = None
        state.is_read_only = False


@dataclass
class FloorplannerAppState:
    engine: object = field(default_factory=buda.FloorplannerEngine)
    bdb: object | None = None
    bdb_path: str = ""
    verilog_path: str = ""
    block_names: list[str] = field(default_factory=list)
    unplaced_names: list[str] = field(default_factory=list)
    selected: str | None = None
    is_read_only: bool = False
    _lock_fd: int | None = None

    def add_name(self, name: str):
        if name not in self.block_names:
            self.block_names.append(name)
            self.block_names.sort()

    def add_unplaced(self, name: str):
        if name not in self.unplaced_names:
            self.unplaced_names.append(name)
            self.unplaced_names.sort()

    def block(self, name: str):
        return self.engine.get_block(name)

    def blocks(self):
        for name in self.block_names:
            yield self.engine.get_block(name)

    def names_at_depth(self, depth: int):
        return [n for n in self.block_names if n.count("/") == depth]

    def blocks_at_depth(self, depth: int):
        for name in self.names_at_depth(depth):
            yield self.engine.get_block(name)


def new_state() -> FloorplannerAppState:
    return FloorplannerAppState()


def load_bdb(path: str) -> FloorplannerAppState:
    state = new_state()
    state.bdb = buda.BDB(path)
    state.bdb_path = path

    fd = _try_acquire_write_lock(path)
    if fd is not None:
        state._lock_fd = fd
        state.is_read_only = False
    else:
        state.is_read_only = True

    die_w, die_h = state.bdb.die_w(), state.bdb.die_h()
    if die_w > 0 and die_h > 0:
        state.engine.set_die(die_w, die_h)

    for comp in state.bdb.all_components():
        if comp.x1 < 0 or comp.y1 < 0 or comp.x2 <= comp.x1 or comp.y2 <= comp.y1:
            continue
        state.engine.add_block(comp.name, comp.x1, comp.y1, comp.x2, comp.y2)
        state.add_name(comp.name)
    return state


def create_bdb(path: str, die_w: float, die_h: float, grid: float = 10.0) -> FloorplannerAppState:
    state = new_state()
    state.bdb = buda.BDB(path)
    state.bdb_path = path
    state.engine.set_die(float(die_w), float(die_h))
    state.engine.set_grid(float(grid))
    # File exists now that BDB() created it; acquire the write lock.
    fd = _try_acquire_write_lock(path)
    if fd is not None:
        state._lock_fd = fd
    else:
        state.is_read_only = True
    return state


def _pack_origins(count: int, die_w: float, grid: float,
                  block_w: float, block_h: float):
    x = float(grid)
    y = float(grid)
    step_x = block_w + 4.0 * float(grid)
    step_y = block_h + 4.0 * float(grid)
    for _ in range(count):
        yield x, y
        x += step_x
        if x + block_w > die_w:
            x = float(grid)
            y += step_y


def _subtree_sizes(comps):
    children = {}
    by_id = {}
    for c in comps:
        by_id[c.id] = c
        children.setdefault(c.parent_id, []).append(c)

    memo = {}

    def count_desc(comp_id):
        if comp_id in memo:
            return memo[comp_id]
        kids = children.get(comp_id, [])
        total = max(1, len(kids))
        for child in kids:
            total += count_desc(child.id)
        memo[comp_id] = total
        return total

    return {c.id: count_desc(c.id) for c in comps}, children


def import_verilog(v_path: str, bdb_path: str, die_w: float = 2000.0,
                   die_h: float = 1200.0, grid: float = 10.0,
                   default_w: float = 200.0,
                   default_h: float = 160.0,
                   seed_depth: int = 1) -> FloorplannerAppState:
    """Create a BDB from Verilog and seed top-level placeholder blocks.

    Verilog import gives hierarchy/connectivity but no physical sizes.  The
    prototype creates editable top-level blocks so the designer can begin a
    quick manual floorplan immediately.
    """
    state = new_state()
    state.bdb = buda.BDB(bdb_path)
    state.bdb_path = bdb_path
    state.verilog_path = v_path
    state.bdb.import_verilog(v_path)
    state.bdb.set_die(float(die_w), float(die_h))
    state.engine.set_die(float(die_w), float(die_h))
    state.engine.set_grid(float(grid))

    comps = state.bdb.all_components()
    roots = [c for c in comps if c.parent_id == -1]
    subtree_count, children = _subtree_sizes(comps)
    root_dims = {}
    for comp in roots:
        child_count = max(1, len(children.get(comp.id, [])))
        cols = max(1, int(child_count ** 0.5 + 0.999))
        rows = max(1, int((child_count + cols - 1) / cols))
        w = max(default_w, cols * (default_w + 2.0 * grid) + 2.0 * grid)
        h = max(default_h, rows * (default_h + 2.0 * grid) + 2.0 * grid)
        # Give larger hierarchy roots a little more room for manual refinement.
        scale = min(1.8, max(1.0, subtree_count.get(comp.id, 1) / 8.0))
        root_dims[comp.name] = (w * scale, h * scale)

    origins = list(_pack_origins(
        len(roots), die_w, grid,
        max((d[0] for d in root_dims.values()), default=default_w),
        max((d[1] for d in root_dims.values()), default=default_h)))

    for comp in sorted(roots, key=lambda c: c.name):
        idx = sorted(roots, key=lambda c: c.name).index(comp)
        seed_w, seed_h = root_dims[comp.name]
        if comp.x1 >= 0 and comp.y1 >= 0 and comp.x2 > comp.x1 and comp.y2 > comp.y1:
            state.engine.add_block(comp.name, comp.x1, comp.y1, comp.x2, comp.y2)
        else:
            x, y = origins[idx]
            state.engine.add_block(comp.name, x, y, x + seed_w, y + seed_h)
            state.add_unplaced(comp.name)
        state.add_name(comp.name)

        if seed_depth >= 1:
            kids = sorted(children.get(comp.id, []), key=lambda c: c.name)
            if kids:
                cols = max(1, int(len(kids) ** 0.5 + 0.999))
                for i, child in enumerate(kids):
                    col = i % cols
                    row = i // cols
                    lx = 2.0 * grid + col * (default_w + 2.0 * grid)
                    ly = 2.0 * grid + row * (default_h + 2.0 * grid)
                    state.engine.add_child_block(child.name, lx, ly, default_w, default_h)
                    state.add_name(child.name)
                    state.add_unplaced(child.name)
    return state


def set_die(state: FloorplannerAppState, w: float, h: float):
    state.engine.set_die(float(w), float(h))


def set_grid(state: FloorplannerAppState, grid: float):
    state.engine.set_grid(float(grid))


def add_block(state: FloorplannerAppState, name: str, x: float, y: float, w: float, h: float):
    x = float(x)
    y = float(y)
    w = float(w)
    h = float(h)
    state.engine.add_block(name, x, y, x + w, y + h)
    state.add_name(name)
    state.selected = name


def add_child_block(state: FloorplannerAppState, name: str,
                    local_x: float, local_y: float, w: float, h: float):
    """Add a block as a child of its path-parent using local coordinates."""
    state.engine.add_child_block(name, float(local_x), float(local_y),
                                 float(w), float(h))
    state.add_name(name)
    state.selected = name


def move_block(state: FloorplannerAppState, name: str, raw_x: float, raw_y: float):
    state.engine.move_block_raw(name, float(raw_x), float(raw_y))


def resize_block(state: FloorplannerAppState, name: str,
                 x1: float, y1: float, x2: float, y2: float):
    state.engine.resize_block_raw(name, float(x1), float(y1), float(x2), float(y2))


def align_bottom(state: FloorplannerAppState, names: Iterable[str]):
    names = list(names)
    if names:
        state.engine.align_bottom(names)


def align_top(state: FloorplannerAppState, names: Iterable[str]):
    names = list(names)
    if names:
        state.engine.align_top(names)


def align_left(state: FloorplannerAppState, names: Iterable[str]):
    names = list(names)
    if names:
        state.engine.align_left(names)


def align_right(state: FloorplannerAppState, names: Iterable[str]):
    names = list(names)
    if names:
        state.engine.align_right(names)


def validate(state: FloorplannerAppState):
    return list(state.engine.validate())


def _sync_cell_children(state: FloorplannerAppState) -> None:
    """Update cell_children with local offsets from live engine positions.

    Called by write_bdb() after engine.write_bdb() so the cell template
    reflects the final written instance positions, regardless of interactive
    edits or undo operations performed during the session.

    Only updates shared-cell parents (those with more than one instance) since
    unique-cell parents have no siblings to template-expand into.
    """
    if state.bdb is None:
        return
    # Single pass to build name→cell and cell→count mappings.
    comp_cells: dict[str, str] = {}
    cell_counts: dict[str, int] = {}
    for c in state.bdb.all_components():
        comp_cells[c.name] = c.cell
        cell_counts[c.cell] = cell_counts.get(c.cell, 0) + 1

    processed: set[tuple[str, str]] = set()
    for name in state.block_names:
        parts = name.split("/")
        if len(parts) < 2:
            continue
        inst_local_name = parts[-1]
        parent_path = "/".join(parts[:-1])

        parent_cell = comp_cells.get(parent_path)
        if parent_cell is None or cell_counts.get(parent_cell, 0) <= 1:
            continue

        key = (parent_cell, inst_local_name)
        if key in processed:
            continue
        processed.add(key)

        child_cell = comp_cells.get(name)
        if child_cell is None:
            continue
        try:
            parent_b = state.engine.get_block(parent_path)
            b = state.engine.get_block(name)
            local_x = b.x1 - parent_b.x1
            local_y = b.y1 - parent_b.y1
            state.bdb.add_inst_to_cell(parent_cell, inst_local_name, child_cell,
                                       local_x, local_y)
        except Exception:
            pass


def write_bdb(state: FloorplannerAppState):
    if state.is_read_only:
        raise PermissionError(
            "This session is read-only — another fp session has the write lock.")
    if state.bdb is None:
        if not state.bdb_path:
            raise RuntimeError("No BDB path set")
        state.bdb = buda.BDB(state.bdb_path)
    state.engine.write_bdb(state.bdb)
    # Update cell_children so the template is consistent with the written
    # component positions for shared-cell hierarchies.
    _sync_cell_children(state)


def export_hbundle_script(state: FloorplannerAppState, path: str, depth: int = 1,
                          visualize: bool = True):
    bdb_path = state.bdb_path or "floorplan.bdb"
    tracks_path = os.path.join(_ROOT, "flow", "tracks.buda")
    lines = [
        f"source {tracks_path}",
        f"open_bdb {bdb_path}",
        f"derive_busterms {depth}",
        "add_blocks_from_bdb 0",
    ]
    if depth > 0:
        lines.append(f"add_blocks_from_bdb {depth} skip")
    lines += [
        f"run_hier_bundler depth {depth}",
        "dump_hbundles",
        "generate_hier_topologies",
        "run_planner hier 5",
        "run_nuts",
        "check_connectivity nuts",
    ]
    if visualize:
        lines.append("visualize")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def run_hbundle_flow(state: FloorplannerAppState, script_path: str | None = None,
                     depth: int = 1):
    write_bdb(state)
    if script_path is None:
        stem = os.path.splitext(state.bdb_path or "floorplan.bdb")[0]
        script_path = stem + "_hbundle.buda"
    export_hbundle_script(state, script_path, depth=depth, visualize=False)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([
        os.path.join(_ROOT, "build"),
        os.path.join(_ROOT, "tools"),
        env.get("PYTHONPATH", ""),
    ])
    cmd = [sys.executable, os.path.join(_ROOT, "src", "buda_cli.py"), script_path, "--no-viz"]
    return subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True, text=True)


def get_block_cell(state: FloorplannerAppState, name: str) -> str | None:
    """Return the BDB cell type for a component path, or None."""
    if state.bdb is None:
        return None
    for c in state.bdb.all_components():
        if c.name == name:
            return c.cell
    return None


def count_cell_instances(state: FloorplannerAppState, cell: str) -> int:
    """Count how many components share this cell type."""
    if state.bdb is None:
        return 0
    return sum(1 for c in state.bdb.all_components() if c.cell == cell)


def sync_cell_to_instances(state: FloorplannerAppState, name: str,
                            x1: float, y1: float,
                            x2: float, y2: float) -> tuple[str, int]:
    """Apply a new size to every engine block that shares this block's cell type.

    The cell table row is updated immediately so that the original cell name
    (which may differ from the synthesized _leaf + '_cell' name written by
    write_bdb) reflects the new dimensions.  Component bboxes are deferred to
    write_bdb() so that undo (which only restores engine positions) leaves
    the BDB positions consistent.

    Returns (cell_name, instance_count).  count=0 means no BDB or unique cell.
    """
    cell = get_block_cell(state, name)
    if cell is None:
        return ("", 0)
    w, h = x2 - x1, y2 - y1
    # Keep the cell row current so template expansion uses the new dimensions.
    state.bdb.add_cell(cell, w, h)
    count = 0
    for c in state.bdb.all_components():
        if c.cell == cell and c.name in state.block_names:
            try:
                b = state.engine.get_block(c.name)
                state.engine.resize_block_raw(c.name, b.x1, b.y1, b.x1 + w, b.y1 + h)
                count += 1
            except Exception:
                pass
    return (cell, count)


def sync_move_to_instances(state: FloorplannerAppState,
                           name: str,
                           new_x: float, new_y: float) -> tuple[str, int]:
    """Propagate a child block's new position to sibling instances in the engine.

    Only updates the live engine — BDB mutations are deferred to write_bdb()
    so that undo (which only restores engine positions) leaves the BDB
    consistent and the undo/redo stack works correctly.

    Returns (parent_cell, instance_count); count=0 if no BDB, root block,
    or the parent cell is unique (nothing to sync).
    """
    if state.bdb is None:
        return ("", 0)

    parts = name.split("/")
    if len(parts) < 2:
        # Root-level block: engine already updated; BDB written at Write time.
        return ("", 0)

    inst_local_name = parts[-1]
    parent_path = "/".join(parts[:-1])

    parent_cell = get_block_cell(state, parent_path)
    if parent_cell is None or count_cell_instances(state, parent_cell) <= 1:
        return (parent_cell or "", 0)

    # Compute local offset from the parent's live engine position.
    try:
        parent_block = state.engine.get_block(parent_path)
        local_x = float(new_x) - parent_block.x1
        local_y = float(new_y) - parent_block.y1
    except Exception:
        return (parent_cell, 0)

    # Propagate to every sibling instance in the engine using live positions.
    count = 0
    for c in state.bdb.all_components():
        if c.cell != parent_cell:
            continue
        child_name = c.name + "/" + inst_local_name
        if child_name not in state.block_names:
            continue
        try:
            sibling_parent = state.engine.get_block(c.name)
            abs_x = sibling_parent.x1 + local_x
            abs_y = sibling_parent.y1 + local_y
            state.engine.move_block_raw(child_name, abs_x, abs_y)
            count += 1
        except Exception:
            pass

    return (parent_cell, count)


def make_block_unique(state: FloorplannerAppState, name: str) -> str | None:
    """Create a private cell definition for this component.

    Subsequent resize operations via sync_cell_to_instances will not affect it.
    Returns the new cell name, or None if already unique or no BDB.
    """
    cell = get_block_cell(state, name)
    if cell is None or count_cell_instances(state, cell) <= 1:
        return None
    b = state.engine.get_block(name)
    leaf = name.replace("/", "_")
    new_cell = f"{cell}_{leaf}"
    w, h = b.x2 - b.x1, b.y2 - b.y1
    state.bdb.add_cell(new_cell, w, h)
    state.bdb.set_comp_cell(name, new_cell)
    return new_cell


def build_hierarchy_tree(state: FloorplannerAppState) -> list[BlockNode]:
    """Build a BlockNode tree for the full component hierarchy.

    Always uses state.block_names as the canonical list (the in-memory engine
    is the live state; the BDB may lag behind unsaved adds).  BDB is consulted
    only for cell-name metadata enrichment.
    Returns the list of root nodes (depth 0).
    """
    # Collect cell metadata from BDB for enrichment (names that are already
    # persisted carry their cell label; newly added blocks get an empty string).
    bdb_meta: dict[str, str] = {}
    if state.bdb is not None:
        for c in state.bdb.all_components():
            bdb_meta[c.name] = c.cell

    node_map: dict[str, BlockNode] = {}
    for name in sorted(state.block_names, key=lambda n: n.count("/")):
        try:
            b = state.engine.get_block(name)
            x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
        except Exception:
            x1 = y1 = x2 = y2 = 0.0
        node_map[name] = BlockNode(
            name, bdb_meta.get(name, ""), x1, y1, x2, y2, name.count("/"))
    for name, node in node_map.items():
        parent_name = "/".join(name.split("/")[:-1])
        if parent_name and parent_name in node_map:
            node_map[parent_name].children.append(node)
    return [n for name, n in node_map.items() if "/" not in name]


def optimize_placement(
    state: FloorplannerAppState,
    method: str = "sa",
    fixed=None,
    reshapeable=None,
    min_sizes=None,
    **kwargs,
):
    """Run global placement optimization on root-level blocks.

    fixed:       names of blocks whose position must not change.
    reshapeable: names of blocks whose aspect ratio may change (area kept constant).
    min_sizes:   {name: (min_w, min_h)} minimum dimension constraints.
    **kwargs:    forwarded to run_sa() or run_ga() (e.g. max_iter, seed).

    Returns an OptimizerResult; also applies the result to state.engine.
    """
    import buda as _buda

    die_w = state.engine.die_w()
    die_h = state.engine.die_h()
    grid  = state.engine.grid()
    opt   = _buda.PlacementOptimizer(die_w, die_h, grid)

    fixed_set   = set(fixed or [])
    reshape_set = set(reshapeable or [])
    min_dict    = dict(min_sizes or {})

    for name in state.block_names:
        if "/" in name:
            continue
        b = state.engine.get_block(name)
        w, h   = b.x2 - b.x1, b.y2 - b.y1
        mw, mh = min_dict.get(name, (0.0, 0.0))
        opt.add_block_ex(
            name, w, h, b.x1, b.y1, mw, mh,
            fixed=(name in fixed_set),
            reshapeable=(name in reshape_set),
        )

    if state.bdb is not None:
        comp_by_id = {c.id: c for c in state.bdb.all_components()}
        nets_pins: dict = {}
        for p in state.bdb.all_pins():
            c = comp_by_id.get(p.comp_id)
            if c is None or "/" in c.name:
                continue
            w, h = c.x2 - c.x1, c.y2 - c.y1
            lx = p.px - c.x1 if p.px >= 0 else w / 2.0
            ly = p.py - c.y1 if p.py >= 0 else h / 2.0
            nets_pins.setdefault(p.net_id, []).append((c.name, (lx, ly)))
        for pins in nets_pins.values():
            if len(pins) >= 2:
                opt.add_net(pins)

    if method == "sa":
        result = opt.run_sa(**kwargs)
    else:
        result = opt.run_ga(**kwargs)

    for pb in result.placements:
        try:
            state.engine.resize_block_raw(
                pb.name, pb.x, pb.y, pb.x + pb.w, pb.y + pb.h
            )
        except Exception:
            pass
    return result


def compute_hpwl(state) -> float:
    """HPWL from live engine positions and BDB pin connectivity."""
    if state.bdb is None:
        return 0.0
    comp_by_id = {c.id: c for c in state.bdb.all_components()}
    net_xs: dict = {}
    net_ys: dict = {}
    for p in state.bdb.all_pins():
        c = comp_by_id.get(p.comp_id)
        if c is None:
            continue
        try:
            b = state.engine.get_block(c.name)
            bx1, by1, bx2, by2 = b.x1, b.y1, b.x2, b.y2
        except Exception:
            bx1, by1, bx2, by2 = c.x1, c.y1, c.x2, c.y2
        w, h = bx2 - bx1, by2 - by1
        # p.px/p.py are absolute BDB coordinates; subtract c.x1/c.y1 to get
        # the local offset, then apply to the live engine origin.
        px = (bx1 + (p.px - c.x1)) if p.px >= 0 else (bx1 + w / 2)
        py = (by1 + (p.py - c.y1)) if p.py >= 0 else (by1 + h / 2)
        net_xs.setdefault(p.net_id, []).append(px)
        net_ys.setdefault(p.net_id, []).append(py)
    hpwl = 0.0
    for nid in net_xs:
        xs, ys = net_xs[nid], net_ys[nid]
        if len(xs) >= 2:
            hpwl += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return hpwl


def distribute_h(state, names: list) -> None:
    """Space blocks evenly horizontally (leftmost and rightmost anchored)."""
    pairs = sorted(((n, state.engine.get_block(n)) for n in names),
                   key=lambda x: x[1].x1)
    if len(pairs) < 3:
        return
    total_w = sum(b.x2 - b.x1 for _, b in pairs)
    span = pairs[-1][1].x2 - pairs[0][1].x1
    gap = (span - total_w) / (len(pairs) - 1)
    x = pairs[0][1].x1
    for name, b in pairs:
        w = b.x2 - b.x1
        state.engine.resize_block_raw(name, x, b.y1, x + w, b.y2)
        x += w + gap


def distribute_v(state, names: list) -> None:
    """Space blocks evenly vertically (topmost and bottommost anchored)."""
    pairs = sorted(((n, state.engine.get_block(n)) for n in names),
                   key=lambda x: x[1].y1)
    if len(pairs) < 3:
        return
    total_h = sum(b.y2 - b.y1 for _, b in pairs)
    span = pairs[-1][1].y2 - pairs[0][1].y1
    gap = (span - total_h) / (len(pairs) - 1)
    y = pairs[0][1].y1
    for name, b in pairs:
        h = b.y2 - b.y1
        state.engine.resize_block_raw(name, b.x1, y, b.x2, y + h)
        y += h + gap
