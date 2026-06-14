"""
Thin command layer for the BUDA floorplanner prototype.

The GUI owns selection and drawing state.  Placement semantics stay in
FloorplannerEngine so the same operations are testable without Tk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
class FloorplannerAppState:
    engine: object = field(default_factory=buda.FloorplannerEngine)
    bdb: object | None = None
    bdb_path: str = ""
    verilog_path: str = ""
    block_names: list[str] = field(default_factory=list)
    unplaced_names: list[str] = field(default_factory=list)
    selected: str | None = None

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


def move_block(state: FloorplannerAppState, name: str, raw_x: float, raw_y: float):
    state.engine.move_block_raw(name, float(raw_x), float(raw_y))


def resize_block(state: FloorplannerAppState, name: str,
                 x1: float, y1: float, x2: float, y2: float):
    state.engine.resize_block_raw(name, float(x1), float(y1), float(x2), float(y2))


def align_bottom(state: FloorplannerAppState, names: Iterable[str]):
    names = list(names)
    if names:
        state.engine.align_bottom(names)


def validate(state: FloorplannerAppState):
    return list(state.engine.validate())


def write_bdb(state: FloorplannerAppState):
    if state.bdb is None:
        if not state.bdb_path:
            raise RuntimeError("No BDB path set")
        state.bdb = buda.BDB(state.bdb_path)
    state.engine.write_bdb(state.bdb)


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
