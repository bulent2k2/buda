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
import shutil
import subprocess
import sys
from typing import Callable, Iterable

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda

# The one shared definition of bottom-up congruence (also used by the CLI's
# set_bottom_up / run_planner hier), so the GUI and CLI can never diverge on
# what "congruent" (bottom-up-eligible) means.  Orientation-aware since
# PR #249: translation plus the direction-preserving orients (S/FN/FS) are
# congruent, 90° rotations and no-match instances are not.  The GUI passes
# no phase_score (it has no routing grid); the tiebreak only picks BETWEEN
# geometrically valid orientations, so the eligibility verdict is identical
# to the CLI's.
from buda_session.hier import (bottom_up_congruence_index,
                               bottom_up_congruence_issues)


def parse_time_budget(s: str) -> float:
    """Parse an optimizer runtime-budget string to seconds: '5s'→5, '2m'→120,
    '1h'→3600, '30'→30 (bare number = seconds).  Blank/invalid/negative → 0.0
    (= off; the run stays iteration-bounded).

    Lives in this GUI-free module so it is testable without importing the Tk
    frontend (and so the Optimize dialog and any CLI share one parser)."""
    s = (s or "").strip().lower()
    if not s:
        return 0.0
    mult = 1.0
    if s.endswith("s"):
        s = s[:-1]
    elif s.endswith("m"):
        mult, s = 60.0, s[:-1]
    elif s.endswith("h"):
        mult, s = 3600.0, s[:-1]
    try:
        return max(0.0, float(s) * mult)
    except ValueError:
        return 0.0


def parse_bloat(s: str):
    """Parse an optimizer block-bloat string to a dict, or None when off.

    '20%'          → {'pct': 20.0}          (scale both dims ×1.2)
    '50'           → {'dx': 50.0,'dy': 50.0}(absolute margin, both dims)
    'dx=50,dy=80'  → {'dx': 50.0,'dy': 80.0}
    blank/invalid  → None

    Matches build_hier_demo's --bloat so the GUI and CLI behave the same."""
    s = (s or "").strip()
    if not s:
        return None
    if s.endswith("%"):
        try:
            return {"pct": float(s[:-1])}
        except ValueError:
            return None
    if "=" in s:
        d = {}
        for part in s.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            try:
                d[k.strip()] = float(v)
            except ValueError:
                pass
        if not d:
            return None
        dx = d.get("dx", d.get("dy", 0.0))
        dy = d.get("dy", d.get("dx", 0.0))
        return {"dx": dx, "dy": dy}
    try:
        v = float(s)
        return {"dx": v, "dy": v}
    except ValueError:
        return None


def _bloated_size(w: float, h: float, bloat):
    """Inflated (w, h) used ONLY during optimization, to leave routing channels."""
    if not bloat:
        return w, h
    if "pct" in bloat:
        f = 1.0 + bloat["pct"] / 100.0
        return w * f, h * f
    return w + bloat.get("dx", 0.0), h + bloat.get("dy", 0.0)


def bbox_area(state) -> float:
    """Bounding-box (envelope) area of root-level blocks at live engine positions."""
    xs, ys = [], []
    for name in state.block_names:
        if "/" in name:
            continue
        try:
            b = state.engine.get_block(name)
        except Exception:
            continue
        xs += [b.x1, b.x2]
        ys += [b.y1, b.y2]
    if not xs:
        return 0.0
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


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


def _lock_path_for(path: str) -> str:
    """Canonical sidecar lock path for a BDB (see _try_acquire_write_lock)."""
    return os.path.realpath(path) + ".fplock"


def _try_acquire_write_lock(path: str) -> int | None:
    """Try a non-blocking exclusive flock coordinating fp sessions on `path`.

    Returns an open file descriptor (the caller must keep it alive to hold the
    lock) or None if another process already holds the lock.  The OS releases
    the lock automatically when the fd is closed or the process exits, so
    there are no stale-lock issues.

    The lock is taken on a dedicated sidecar file ``<canonical-path>.fplock``,
    NOT on the BDB file itself: SQLite's macOS VFS already holds an ``flock`` on
    the open database, so flocking the same file would collide with our own live
    ``buda.BDB`` connection and wrongly report the session as read-only.  A
    separate sidecar is never touched by SQLite and works on every platform.

    The path is canonicalised with ``os.path.realpath`` first so that aliases of
    the same database (symlinks, ``..`` segments, relative vs absolute) map to a
    single sidecar — otherwise two sessions opening the same DB through different
    paths would lock distinct files and both stay writable, letting their
    ``write_bdb`` calls clobber each other.

    Falls back to None (no lock, always writable) on platforms without fcntl.
    """
    lock_path = _lock_path_for(path)
    try:
        fd = os.open(lock_path, os.O_RDONLY | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        try:
            os.close(fd)
        except Exception:
            pass
        return None
    except Exception:
        # Unsupported platform, unwritable directory, etc. — allow write.
        return None


def release_bdb_lock(state: "FloorplannerAppState") -> None:
    """Release the exclusive write lock held by this session, if any, and remove
    the sidecar .fplock file so it isn't left behind after closing."""
    if state._lock_fd is None:
        return
    lock_path = state._lock_path
    # Release our held lock first (never unlink while our fd is still locked —
    # that would let a racing O_CREAT lock a new inode at the same path).
    try:
        fcntl.flock(state._lock_fd, fcntl.LOCK_UN)
        os.close(state._lock_fd)
    except Exception:
        pass
    state._lock_fd = None
    state._lock_path = None
    state.is_read_only = False
    if lock_path:
        _safe_unlink_lockfile(lock_path)


def _safe_unlink_lockfile(lock_path: str) -> None:
    """Remove the sidecar lock file race-free.

    Re-open and re-lock it (non-blocking): if a *different* session has taken the
    lock in the gap since we released, the lock fails and we leave their file in
    place; if we win it, no other session holds it, so it is safe to unlink.
    While we hold this re-lock no racing O_CREAT can lock a competing inode at the
    same path, and the inode check guards against the file being swapped between
    open and lock — so the single-writer guarantee is preserved."""
    try:
        fd = os.open(lock_path, os.O_RDONLY)   # no O_CREAT: don't resurrect it
    except FileNotFoundError:
        return
    except Exception:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_ino == os.stat(lock_path).st_ino:
            os.unlink(lock_path)
    except Exception:
        pass   # BlockingIOError → another session owns it now; leave it.
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


@dataclass
class FloorplannerAppState:
    engine: object = field(default_factory=buda.FloorplannerEngine)
    bdb: object | None = None
    bdb_path: str = ""
    # Diffable *.bdb.sql this design should write back to (empty = binary-only).
    # Set when a *.bdb.sql is opened; Save re-serializes the working binary here.
    sql_source: str = ""
    verilog_path: str = ""
    block_names: list[str] = field(default_factory=list)
    unplaced_names: list[str] = field(default_factory=list)
    selected: str | None = None
    is_read_only: bool = False
    _lock_fd: int | None = None
    _lock_path: str | None = None

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


def load_bdb(path: str, sql_source: str = "") -> FloorplannerAppState:
    state = new_state()
    state.bdb = buda.BDB(path)
    state.bdb_path = path
    # `path` is the working binary (a temp materialization when opened from a
    # *.bdb.sql); sql_source names the .sql to write back to on Save.
    state.sql_source = os.path.abspath(sql_source) if sql_source else ""

    # Single-writer lock: key on the WRITE-BACK SOURCE when one is set (the
    # shared .sql), not the per-session throwaway temp binary — otherwise two
    # `fp foo.bdb.sql` windows each lock a distinct temp, both stay writable,
    # and their Write/Save silently clobber the same source .sql.
    lock_key = state.sql_source or path
    fd = _try_acquire_write_lock(lock_key)
    if fd is not None:
        state._lock_fd = fd
        state._lock_path = _lock_path_for(lock_key)
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
        state._lock_path = _lock_path_for(path)
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

    Raises PermissionError if bdb_path already exists and is write-locked by
    another floorplanner session — the import is aborted before any mutation.
    """
    # If the target already exists, acquire the exclusive write lock BEFORE
    # opening or mutating it.  import_verilog() clears the DB (destructive),
    # so we must refuse if another session is editing the file.
    pre_existing = os.path.exists(bdb_path)
    if pre_existing:
        fd = _try_acquire_write_lock(bdb_path)
        if fd is None:
            raise PermissionError(
                f"Cannot import: another fp session holds the write lock on "
                f"{os.path.basename(bdb_path)}. Close that session first.")
    else:
        fd = None  # File doesn't exist yet; lock acquired after BDB creates it.

    try:
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

        # For new files, the file now exists — acquire the lock.
        if fd is None:
            fd = _try_acquire_write_lock(bdb_path)
        if fd is not None:
            state._lock_fd = fd
            state._lock_path = _lock_path_for(bdb_path)
            fd = None  # Ownership transferred; do not release in the except block.
        else:
            state.is_read_only = True
    except Exception:
        # If fd was acquired but ownership was never transferred to state,
        # release it now so the process doesn't hold a leaked write lock.
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:
                pass
        raise
    return state
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


def topmost(names: Iterable[str]) -> list:
    """Drop any name that is a descendant ("A/.../B") of another selected name,
    preserving order.  Subtree-carrying transforms (move/align/distribute/rotate)
    move a block's children automatically, so a selection holding both an
    ancestor and a descendant must not process the descendant twice."""
    names = list(names)
    nameset = set(names)
    out = []
    for n in names:
        parts = n.split('/')
        if any('/'.join(parts[:i]) in nameset for i in range(1, len(parts))):
            continue   # an ancestor of n is also selected → skip n
        out.append(n)
    return out


def align_bottom(state: FloorplannerAppState, names: Iterable[str]):
    names = topmost(names)
    if names:
        state.engine.align_bottom(names)


def align_top(state: FloorplannerAppState, names: Iterable[str]):
    names = topmost(names)
    if names:
        state.engine.align_top(names)


def align_left(state: FloorplannerAppState, names: Iterable[str]):
    names = topmost(names)
    if names:
        state.engine.align_left(names)


def align_right(state: FloorplannerAppState, names: Iterable[str]):
    names = topmost(names)
    if names:
        state.engine.align_right(names)


def align_center_h(state: FloorplannerAppState, names: Iterable[str]) -> None:
    """Align horizontal centerlines: move all blocks to share the first block's x-mid."""
    names = topmost(names)
    if len(names) < 2:
        return
    ref = state.engine.get_block(names[0])
    cx = (ref.x1 + ref.x2) / 2
    for name in names:
        b = state.engine.get_block(name)
        state.engine.move_block_raw(name, cx - (b.x2 - b.x1) / 2, b.y1)


def align_center_v(state: FloorplannerAppState, names: Iterable[str]) -> None:
    """Align vertical centerlines: move all blocks to share the first block's y-mid."""
    names = topmost(names)
    if len(names) < 2:
        return
    ref = state.engine.get_block(names[0])
    cy = (ref.y1 + ref.y2) / 2
    for name in names:
        b = state.engine.get_block(name)
        state.engine.move_block_raw(name, b.x1, cy - (b.y2 - b.y1) / 2)


def _edge_axis(edge_sel) -> str:
    """Return 'x' if all edges are l/r (vertical edges), 'y' if all t/b
    (horizontal edges).  Raises ValueError on a mixed or empty selection."""
    chars = {e for _, e in edge_sel}
    if chars and chars <= {"l", "r"}:
        return "x"
    if chars and chars <= {"t", "b"}:
        return "y"
    raise ValueError(f"edge selection must be all-V or all-H: {chars}")


def move_edges(state: FloorplannerAppState, edge_sel, delta: float) -> None:
    """Shift each selected edge's controlled coordinate by `delta`, holding the
    opposite edge fixed.

    edge_sel: iterable of (block_name, edge) with edge in {l,r,t,b}, all the
              same orientation (all l/r OR all t/b).  l->x1, r->x2, t->y1, b->y2.
    Clamped so a moved edge cannot cross/invert its opposite edge (block extent
    on that axis stays >= one grid step).
    """
    sel = list(edge_sel)
    if not sel:
        return
    _edge_axis(sel)                      # validate homogeneity
    min_ext = max(state.engine.grid(), 1.0)
    # Group by block so both opposite edges of one block move atomically against
    # its ORIGINAL bbox: selecting l+r and moving is a translation (extent
    # preserved), not a clamp-corrupted resize.
    by_block: dict = {}
    for name, edge in sel:
        by_block.setdefault(name, set()).add(edge)
    for name, edges in by_block.items():
        try:
            b = state.engine.get_block(name)
        except Exception:
            continue
        x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
        if "l" in edges: x1 += delta
        if "r" in edges: x2 += delta
        if "t" in edges: y1 += delta
        if "b" in edges: y2 += delta
        # Clamp a lone moved edge against its fixed opposite (a translation of
        # both edges keeps extent, so no clamp fires there).
        if x2 - x1 < min_ext:
            if "l" in edges and "r" not in edges: x1 = x2 - min_ext
            elif "r" in edges and "l" not in edges: x2 = x1 + min_ext
        if y2 - y1 < min_ext:
            if "t" in edges and "b" not in edges: y1 = y2 - min_ext
            elif "b" in edges and "t" not in edges: y2 = y1 + min_ext
        resize_block(state, name, x1, y1, x2, y2)


def align_edges(state: FloorplannerAppState, edge_sel, mode: str) -> None:
    """Set every selected edge to a common coordinate.

    mode: 'min' | 'max' | 'mean' of the selected edges' current controlled
          coordinates.  V edges (l/r) align to a common X; H edges (t/b) align
          to a common Y.  Same anti-inversion clamp as move_edges.
    """
    sel = list(edge_sel)
    if not sel:
        return
    _edge_axis(sel)
    min_ext = max(state.engine.grid(), 1.0)
    pick = {"l": lambda b: b.x1, "r": lambda b: b.x2,
            "t": lambda b: b.y1, "b": lambda b: b.y2}
    blocks = [(n, e, state.engine.get_block(n)) for n, e in sel]
    vals = [pick[e](b) for _, e, b in blocks]
    if not vals:
        return
    target = (min(vals) if mode == "min"
              else max(vals) if mode == "max"
              else sum(vals) / len(vals))
    # Group by block so a block with both opposite edges selected is set
    # atomically against its original bbox (one resize, deterministic clamp).
    by_block: dict = {}
    for name, edge, b in blocks:
        slot = by_block.setdefault(name, [b, set()])
        slot[1].add(edge)
    for name, (b, edges) in by_block.items():
        x1, y1, x2, y2 = b.x1, b.y1, b.x2, b.y2
        if "l" in edges: x1 = target
        if "r" in edges: x2 = target
        if "t" in edges: y1 = target
        if "b" in edges: y2 = target
        if x2 - x1 < min_ext:
            if "l" in edges and "r" not in edges: x1 = x2 - min_ext
            elif "r" in edges and "l" not in edges: x2 = x1 + min_ext
        if y2 - y1 < min_ext:
            if "t" in edges and "b" not in edges: y1 = y2 - min_ext
            elif "b" in edges and "t" not in edges: y2 = y1 + min_ext
        resize_block(state, name, x1, y1, x2, y2)


def rotate_blocks_cw(state: FloorplannerAppState, names: Iterable[str]) -> None:
    """Rotate each block 90° clockwise around its own lower-left corner.

    In y-up coordinates, CW 90° maps (dx, dy) → (dy, -dx) relative to the
    pivot.  For a block at (x1, y1) with width w and height h the result is:
        new bbox = (x1, y1-w, x1+h, y1)   [width becomes h, height becomes w]
    The block shifts downward by w.  Out-of-die positions are flagged by
    validate() — the caller is responsible for checking.
    """
    for name in topmost(names):
        try:
            state.engine.rotate_block(name, True)   # carries the sub-hierarchy
        except Exception:
            continue


def rotate_blocks_ccw(state: FloorplannerAppState, names: Iterable[str]) -> None:
    """Rotate each block 90° counter-clockwise around its own lower-left corner.

    In y-up coordinates, CCW 90° maps (dx, dy) → (-dy, dx) relative to the
    pivot.  For a block at (x1, y1) with width w and height h the result is:
        new bbox = (x1-h, y1, x1, y1+w)   [width becomes h, height becomes w]
    The block shifts leftward by h.  Out-of-die positions are flagged by
    validate().
    """
    for name in topmost(names):
        try:
            state.engine.rotate_block(name, False)  # carries the sub-hierarchy
        except Exception:
            continue


def validate(state: FloorplannerAppState):
    return list(state.engine.validate())


def find_gap_violations(state: FloorplannerAppState,
                        names: list,
                        step: float) -> list:
    """Return gap violations between immediate neighbors.

    For every block in `names`, scan the other three cardinal directions and
    find the nearest block whose perpendicular interval overlaps.  The gap to
    that nearest neighbor is the *immediate-neighbor gap* in that direction.
    A block is reported when its largest immediate-neighbor gap >= `step`.

    Returns a list of (name, max_gap, direction_arrow, neighbor_name) sorted
    by max_gap descending.
    """
    # Collect bounding boxes.
    boxes = {}
    for name in names:
        try:
            b = state.block(name)
            boxes[name] = (b.x1, b.y1, b.x2, b.y2)
        except Exception:
            pass

    items = list(boxes.items())
    # nearest[(name, dir)] = (min_gap, neighbor_name)
    nearest: dict = {}

    for na, (ax1, ay1, ax2, ay2) in items:
        for nb, (bx1, by1, bx2, by2) in items:
            if na == nb:
                continue
            # Right: B starts at or beyond A's right edge, Y-intervals overlap
            if bx1 >= ax2 and min(ay2, by2) > max(ay1, by1):
                gap = bx1 - ax2
                k = (na, '→')
                if k not in nearest or gap < nearest[k][0]:
                    nearest[k] = (gap, nb)
            # Left: B ends at or before A's left edge, Y-intervals overlap
            if bx2 <= ax1 and min(ay2, by2) > max(ay1, by1):
                gap = ax1 - bx2
                k = (na, '←')
                if k not in nearest or gap < nearest[k][0]:
                    nearest[k] = (gap, nb)
            # Up: B starts at or beyond A's top edge, X-intervals overlap
            if by1 >= ay2 and min(ax2, bx2) > max(ax1, bx1):
                gap = by1 - ay2
                k = (na, '↑')
                if k not in nearest or gap < nearest[k][0]:
                    nearest[k] = (gap, nb)
            # Down: B ends at or before A's bottom edge, X-intervals overlap
            if by2 <= ay1 and min(ax2, bx2) > max(ax1, bx1):
                gap = ay1 - by2
                k = (na, '↓')
                if k not in nearest or gap < nearest[k][0]:
                    nearest[k] = (gap, nb)

    # Per block: keep only the largest gap across all directions.
    worst: dict = {}  # name -> (max_gap, arrow, neighbor)
    for (name, arrow), (gap, neighbor) in nearest.items():
        if name not in worst or gap > worst[name][0]:
            worst[name] = (gap, arrow, neighbor)

    result = [
        (name, gap, arrow, neighbor)
        for name, (gap, arrow, neighbor) in worst.items()
        if gap >= step
    ]
    result.sort(key=lambda x: -x[1])
    return result


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


def save_sql(state: FloorplannerAppState, sql_path: str | None = None) -> str:
    """Flush placements to the working binary, then serialize it to a diffable
    ``*.bdb.sql`` (the same text form `tools/bdb_serialize.dump` produces and
    the CLI's `open_bdb … writeback` writes).

    The target defaults to ``state.sql_source`` — i.e. write back to the .sql the
    design was opened from. Passing ``sql_path`` (Save As) writes there and
    remembers it as the new source. Read-only sessions raise, exactly as
    ``write_bdb``. Returns the absolute target path.
    """
    target = os.path.abspath(sql_path) if sql_path else state.sql_source
    if not target:
        raise RuntimeError(
            "No .sql target — open a *.bdb.sql or use Save As to choose one.")
    write_bdb(state)                       # commit placements to the binary
    import bdb_serialize
    bdb_serialize.dump(state.bdb_path, target)
    state.sql_source = target
    return target


def save_bdb_as_binary(state: FloorplannerAppState, target: str) -> "FloorplannerAppState":
    """Write the design to a new *binary* BDB at ``target`` and return a fresh
    state the caller switches to (re-locked on ``target``).

    Goes through a serialize round-trip (`dump` → `load`) rather than a raw file
    copy so no committed-but-unwritten WAL data is missed and the result is a
    clean, standalone binary. Not a .sql write-back — ``sql_source`` is cleared.
    """
    target = os.path.abspath(target)
    # Save As onto the currently-open binary is just an in-place Write — no
    # destructive reload (and no self-lock conflict).
    if state.bdb_path and os.path.realpath(target) == os.path.realpath(state.bdb_path):
        write_bdb(state)
        return state
    # Guard the target's single-writer lock BEFORE the destructive dump→load:
    # never clobber a BDB another floorplanner session is editing. Hold it
    # across the rewrite, then release so load_bdb below takes the real
    # session lock.
    guard_fd = _try_acquire_write_lock(target)
    if guard_fd is None:
        raise PermissionError(
            "Save As target is open in another floorplanner session: " + target)
    try:
        write_bdb(state)                   # commit placements to the binary
        import bdb_serialize
        tmp_sql = target + ".saveas.tmp.sql"
        bdb_serialize.dump(state.bdb_path, tmp_sql)
        try:
            bdb_serialize.load(tmp_sql, target)
        finally:
            if os.path.exists(tmp_sql):
                os.unlink(tmp_sql)
    finally:
        try:
            fcntl.flock(guard_fd, fcntl.LOCK_UN)
            os.close(guard_fd)
        except Exception:
            pass
    release_bdb_lock(state)
    return load_bdb(target)


def design_max_depth(state: FloorplannerAppState) -> int:
    """Deepest component depth in the open BDB (0 if none / no BDB)."""
    if state.bdb is None:
        return 0
    return max((c.depth for c in state.bdb.all_components()), default=0)


def export_hbundle_script(state: FloorplannerAppState, path: str,
                          depth: int | None = None,
                          visualize: bool = True) -> int:
    """Write a hier-flow .buda script for the open BDB and return the routing
    depth it covers.

    The full hierarchy is loaded into the floorplan: `add_blocks_from_bdb` is
    emitted for every level 0..max_depth so NUTS / topology gen build their
    Hanan grid from real block edges at all depths.  A self-contained sidecar
    `<name>_tracks.buda` (copied from flow/tracks/tracks4top.buda) defines the
    layers AND track patterns and is sourced relatively, so the script can run the
    full flow through detailed NUTS without external tech files.
    """
    bdb_path = state.bdb_path or "floorplan.bdb"
    max_depth = design_max_depth(state)
    # Cover the whole design (never less than its actual depth).
    depth = max_depth if depth is None else max(depth, max_depth)

    # Self-contained tech sidecar next to the script (layers + track patterns).
    sidecar = os.path.splitext(path)[0] + "_tracks.buda"
    shutil.copyfile(os.path.join(_ROOT, "flow", "tracks", "tracks4top.buda"), sidecar)

    lines = [
        f"source {os.path.basename(sidecar)}",   # resolved relative to this script
        f"open_bdb {bdb_path}",
        f"derive_busterms {depth}",
        "add_blocks_from_bdb 0",
    ]
    # Load every routing level (1..max_depth) so the floorplan is fully populated.
    for d in range(1, depth + 1):
        lines.append(f"add_blocks_from_bdb {d} skip")
    lines += [
        f"run_hier_bundler depth {depth}",
        "dump_hbundles",
        "generate_hier_topologies",
        "run_planner hier 5",
        "run_nuts",
        "check_design nuts",
        "run_detailed_nuts",
        "check_design dnuts",
    ]
    if visualize:
        lines.append("visualize")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return depth


def run_hbundle_flow(state: FloorplannerAppState, script_path: str | None = None,
                     depth: int | None = None):
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


# ── Per-cell settings (extensible registry; docs/internal/cell_settings_ui.md) ─
#
# Each CellSetting descriptor carries everything the Cell Settings dialog, this
# command layer, and (optionally) a generic CLI need to render, read, validate,
# and write one per-cell property.  Adding a future per-cell config (route
# priority, keepout margin, layer preference, orientation-lock, …) = append one
# descriptor; the dialog builds one column per descriptor from `kind`.


@dataclass
class CellSetting:
    """One per-cell configuration property.

    `eligible` gates on the TRANSITION, not a direction heuristic:
    (state, cell, old, new, ctx=None) -> (ok, reason) for moving `cell`
    from `old` to `new`.  Each descriptor owns its own rule — bottom_up
    gates only the ON transition (clearing is always allowed), a future
    float/choice kind defines exactly which of ITS transitions need
    validation.  The optional `ctx` is a dict list_cell_settings shares
    across every cell of one call (seeded with "comps", the pre-fetched
    `all_components()` rows) so a descriptor can cache whatever derived
    index it needs — the DB read AND the index build are paid once per
    dialog open, not per cell.
    """
    key:      str                # logical/BDB key, e.g. "bottom_up"
    label:    str                # column header, e.g. "Bottom-Up"
    kind:     str                # "bool" now; "choice" | "float" later
    default:  object             # value that is "unset" (False for bottom_up)
    get:      Callable           # (state, cell) -> value
    set:      Callable           # (state, cell, value) -> None (raises on invalid)
    eligible: Callable           # (state, cell, old, new, ctx=None) -> (ok, reason)
    help:     str = ""           # tooltip / status text


def _bottom_up_eligible(state, cell, old, new, ctx=None):
    """Gate only the ON transition on instance congruence; `off` clears
    unconditionally — a cell marked bottom_up that later became incongruent
    (a rotate/move this session, or a stale BDB) must always be clearable.
    This mirrors the CLI's set_bottom_up exactly.  The GUI gate is UX, not
    the safety net: run_planner hier re-checks congruence at expansion time.

    The component index is cached in `ctx` so one list_cell_settings call
    builds it once and every cell's check reuses it (Codex #251 P2)."""
    if not new:
        return (True, "")
    index = ctx.get("bu_index") if ctx is not None else None
    if index is None:
        comps = (ctx.get("comps") if ctx is not None else None)
        if comps is None:
            comps = state.bdb.all_components()
        index = bottom_up_congruence_index(comps)
        if ctx is not None:
            ctx["bu_index"] = index
    issues = bottom_up_congruence_issues(None, cell, index=index)
    if issues:
        shown = "; ".join(issues[:4])
        more = "" if len(issues) <= 4 else f" (+{len(issues) - 4} more)"
        return (False, f"instances are not congruent: {shown}{more}")
    return (True, "")


CELL_SETTINGS: list[CellSetting] = [
    CellSetting(
        key="bottom_up", label="Bottom-Up", kind="bool", default=False,
        get=lambda st, c: st.bdb.cell_bottom_up(c),
        set=lambda st, c, v: st.bdb.set_cell_bottom_up(c, bool(v)),
        eligible=_bottom_up_eligible,
        help="Plan/NUTS this cell's local interconnect once, "
             "copy to every instance.",
    ),
]


@dataclass
class CellSettingsRow:
    """One cell type's settings snapshot for the dialog: per-key
    {key: (value, can_activate, reason)} — can_activate is whether the
    setting's *activating* transition (bool: -> True) would be accepted,
    with a human-readable reason when it would not."""
    cell:      str
    instances: int
    settings:  dict[str, tuple]


def _cell_setting(key: str) -> CellSetting:
    for s in CELL_SETTINGS:
        if s.key == key:
            return s
    raise ValueError(f"unknown cell setting '{key}' "
                     f"(known: {', '.join(s.key for s in CELL_SETTINGS)})")


def list_cell_settings(state: FloorplannerAppState) -> list[CellSettingsRow]:
    """One row per cell type with its per-setting value + activate-eligibility.

    Computed from a single all_components() read per call — and a single
    derived component index, cached in the shared `ctx` dict — so a large
    BDB pays the DB read and the index build once when the dialog opens,
    not per cell/widget (the per-cell cost is just that cell's subtree
    compare).  Non-bool kinds have no single "activating" value, so their
    can_activate is True (their eligible runs at set time instead).
    """
    if state.bdb is None:
        return []
    comps = state.bdb.all_components()
    ctx: dict = {"comps": comps}      # shared across all cells of this call
    inst_counts: dict[str, int] = {}
    for c in comps:
        inst_counts[c.cell] = inst_counts.get(c.cell, 0) + 1
    rows = []
    for cr in sorted(state.bdb.all_cells(), key=lambda c: c.name):
        settings = {}
        for s in CELL_SETTINGS:
            value = s.get(state, cr.name)
            if s.kind == "bool":
                try:
                    ok, reason = s.eligible(state, cr.name, value, True, ctx)
                except TypeError:
                    # Descriptor without the optional ctx parameter.
                    ok, reason = s.eligible(state, cr.name, value, True)
            else:
                ok, reason = True, ""
            settings[s.key] = (value, ok, reason)
        rows.append(CellSettingsRow(cr.name, inst_counts.get(cr.name, 0),
                                    settings))
    return rows


def set_cell_setting(state: FloorplannerAppState, cell: str, key: str, value):
    """Validate and persist one per-cell setting straight to state.bdb.

    Raises PermissionError on a read-only session (exactly as write_bdb),
    ValueError when the descriptor's eligible refuses the transition (e.g.
    enabling bottom_up on non-congruent instances), and whatever the BDB
    write raises for an undefined cell.  Persistence rides the existing
    model: a binary BDB lands immediately; a *.bdb.sql session lands in the
    temp binary and Write / Save As serializes it back.
    """
    if state.is_read_only:
        raise PermissionError(
            "This session is read-only — another fp session has the write lock.")
    if state.bdb is None:
        raise RuntimeError("No BDB open")
    s = _cell_setting(key)
    old = s.get(state, cell)
    ok, reason = s.eligible(state, cell, old, value)
    if not ok:
        raise ValueError(f"{key}: cannot set '{cell}' to {value!r}: {reason}")
    s.set(state, cell, value)


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
    bloat=None,
    **kwargs,
):
    """Run global placement optimization on root-level blocks.

    fixed:       names of blocks whose position must not change.
    reshapeable: names of blocks whose aspect ratio may change (area kept constant).
    min_sizes:   {name: (min_w, min_h)} minimum dimension constraints.
    bloat:       optional {'pct':..} or {'dx':..,'dy':..} — inflate each movable
                 block ONLY during optimization (so it leaves routing channels);
                 the real-sized block is then re-centered in its bloated slot.
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

    # Bloat only movable, non-reshapeable blocks (a fixed block can't move, and
    # area-preserving reshape doesn't compose with inflation).  bloat_info maps
    # name -> (real_w, real_h, bloated_w, bloated_h) for those we inflated.
    bloat_info: dict = {}

    for name in state.block_names:
        if "/" in name:
            continue
        b = state.engine.get_block(name)
        w, h   = b.x2 - b.x1, b.y2 - b.y1
        bw, bh = w, h
        if bloat and name not in fixed_set and name not in reshape_set:
            bw, bh = _bloated_size(w, h, bloat)
            bloat_info[name] = (w, h, bw, bh)
        mw, mh = min_dict.get(name, (0.0, 0.0))
        opt.add_block_ex(
            name, bw, bh, b.x1, b.y1, mw, mh,
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
            if c.name in bloat_info:
                # The real block is centered in its bloated slot, so the final
                # pin sits at bloated_origin + centering_offset + local offset.
                rw, rh, bw, bh = bloat_info[c.name]
                lx += (bw - rw) / 2.0
                ly += (bh - rh) / 2.0
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
            if pb.name in bloat_info:
                # Center the real-sized block inside its bloated slot → routing
                # channels on all sides.  Pure move (keeps the real footprint).
                rw, rh, bw, bh = bloat_info[pb.name]
                state.engine.move_block_raw(
                    pb.name, pb.x + (bw - rw) / 2.0, pb.y + (bh - rh) / 2.0)
                continue
            b = state.engine.get_block(pb.name)
            same_size = (abs((b.x2 - b.x1) - pb.w) < 1e-6 and
                         abs((b.y2 - b.y1) - pb.h) < 1e-6)
            if same_size:
                # Pure relocation → move (carries the block's child sub-hierarchy).
                state.engine.move_block_raw(pb.name, pb.x, pb.y)
            else:
                # The optimizer reshaped this block (reshapeable): apply as a
                # resize (children not carried, same as a manual resize).
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
    pairs = sorted(((n, state.engine.get_block(n)) for n in topmost(names)),
                   key=lambda x: x[1].x1)
    if len(pairs) < 3:
        return
    total_w = sum(b.x2 - b.x1 for _, b in pairs)
    span = pairs[-1][1].x2 - pairs[0][1].x1
    gap = (span - total_w) / (len(pairs) - 1)
    x = pairs[0][1].x1
    for name, b in pairs:
        w = b.x2 - b.x1
        state.engine.move_block_raw(name, x, b.y1)   # move (carries children)
        x += w + gap


def distribute_v(state, names: list) -> None:
    """Space blocks evenly vertically (topmost and bottommost anchored)."""
    pairs = sorted(((n, state.engine.get_block(n)) for n in topmost(names)),
                   key=lambda x: x[1].y1)
    if len(pairs) < 3:
        return
    total_h = sum(b.y2 - b.y1 for _, b in pairs)
    span = pairs[-1][1].y2 - pairs[0][1].y1
    gap = (span - total_h) / (len(pairs) - 1)
    y = pairs[0][1].y1
    for name, b in pairs:
        h = b.y2 - b.y1
        state.engine.move_block_raw(name, b.x1, y)   # move (carries children)
        y += h + gap
