#!/usr/bin/env python3
"""
tools/buda2bdb.py — Ingest a flat .buda script into a BDB as a cell.

Usage:
  python3 tools/buda2bdb.py <cellname.buda> <bdbfile.bdb|bdbfile.bdb.sql> [-cell <name>]

The script's blocks become the cell's internal blocks, its nets become the
cell's local nets, and `set_die` (or, when absent, the bounding box of all
blocks) becomes the cell size.

  * The cell name defaults to the input filename stem (cpu.buda -> cell "cpu");
    override with -cell.
  * <bdbfile> may be a binary `.bdb` or a diffable `.bdb.sql` text fixture; a
    `.sql` is edited via a temp binary and serialized back (existing cells are
    preserved, so REPLACE works on a fixture too).
  * If <bdbfile> does not exist it is created.  If it exists, the cell is added.
  * If the cell already exists in the BDB it is REPLACED (its representative
    instance, children, cell_children template rows, synthetic child cells, and
    nets are removed first).
  * Any OTHER instances of the cell already in the BDB are size-synced to the
    new cell size (via resize_cell).

Commands read: set_die, add_block (single-rect; multi-rect `rect ...` uses the
union bbox with a warning), add_net, add_bus.  `source <f>` is followed when the
file is found.  All other commands (def_layer, run_*, visualize, corner_margin,
container/teg_mode/corner_margin modifiers, …) are ignored with a warning.

Coordinates are stored 1:1 as micron values.  Round-trip with:
  python3 tools/bdb2buda.py <bdbfile> -cell <name> -scale 1

Out of scope: cell external ports (cell_pin), routing technology (layers/tracks).
"""

import os
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in [os.path.join(_ROOT, "build"), _HERE]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import buda_db
except ModuleNotFoundError:
    sys.exit(
        "Error: buda_db module not found — run 'bin/bb' to build first, "
        "or set PYTHONPATH=build."
    )

# Lets convert() write (and cell-REPLACE into) a diffable *.bdb.sql text fixture
# as transparently as a binary *.bdb — see convert().
import bdb_serialize

# Commands that carry data we translate; everything else is ignored.
_BUS_RE = re.compile(r'^(.+)\[(\d+)(?::(\d+))?\]$')


def _warn(msg: str) -> None:
    print(f"buda2bdb: warning: {msg}", file=sys.stderr)


class ParsedScript:
    """Blocks, nets, and optional die parsed from a .buda script."""

    def __init__(self) -> None:
        # name -> (x1, y1, x2, y2)
        self.blocks: dict = {}
        # list of {name, drv, rcvs, dir}  (dir in {"", "unknown", "inout"})
        self.nets: list = []
        self.die: Optional[tuple] = None      # (w, h) or None


def _block_of(pin: str) -> str:
    """Instance/block name for a pin: substring up to the LAST '.' (a block name
    may itself contain dots), matching buda_cli._pin_instance."""
    return pin.rsplit('.', 1)[0]


def parse_script(path: str, parsed: Optional[ParsedScript] = None,
                 _seen: Optional[set] = None) -> ParsedScript:
    """Parse a .buda script (following `source`) into a ParsedScript."""
    if parsed is None:
        parsed = ParsedScript()
    if _seen is None:
        _seen = set()
    real = os.path.realpath(path)
    if real in _seen:
        return parsed
    _seen.add(real)

    with open(path) as f:
        lines = f.readlines()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "set_die":
            if len(args) < 2:
                _warn("set_die needs <w> <h> — ignored")
                continue
            parsed.die = (float(args[0]), float(args[1]))

        elif cmd == "add_block":
            _parse_add_block(args, parsed)

        elif cmd == "add_net":
            _parse_add_net(args, parsed)

        elif cmd == "add_bus":
            _parse_add_bus(args, parsed)

        elif cmd == "source":
            if not args:
                continue
            sub = args[0]
            if not os.path.isabs(sub):
                sub = os.path.join(os.path.dirname(path), sub)
            if os.path.exists(sub):
                parse_script(sub, parsed, _seen)
            else:
                _warn(f"source file not found, skipped: {args[0]}")

        else:
            _warn(f"ignored command: {cmd}")

    return parsed


def _parse_add_block(args: list, parsed: ParsedScript) -> None:
    if len(args) < 5:
        _warn(f"add_block needs <name> <x1> <y1> <x2> <y2> — ignored: {args}")
        return
    name = args[0]
    if args[1].lower() == "rect":
        # Multi-rect: union bbox of all rect quads; warn that detail is dropped.
        xs, ys, i = [], [], 1
        while i + 4 < len(args) + 1 and i < len(args) and args[i].lower() == "rect":
            try:
                x1, y1, x2, y2 = (float(args[i + 1]), float(args[i + 2]),
                                  float(args[i + 3]), float(args[i + 4]))
            except (IndexError, ValueError):
                break
            xs += [x1, x2]
            ys += [y1, y2]
            i += 5
        if not xs:
            _warn(f"add_block {name}: malformed rect form — ignored")
            return
        _warn(f"add_block {name}: multi-rect collapsed to union bbox")
        parsed.blocks[name] = (min(xs), min(ys), max(xs), max(ys))
        return
    try:
        x1, y1, x2, y2 = (float(args[1]), float(args[2]),
                          float(args[3]), float(args[4]))
    except ValueError:
        _warn(f"add_block {name}: non-numeric coords — ignored")
        return
    if len(args) > 5:
        _warn(f"add_block {name}: extra modifiers ignored ({' '.join(args[5:])})")
    parsed.blocks[name] = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _net_dir(args: list):
    """Return (dir_keyword, args_without_keyword) — dir in {'', 'unknown', 'inout'}."""
    if args and args[-1].lower() in ("unknown", "inout"):
        return args[-1].lower(), args[:-1]
    return "", args


def _parse_add_net(args: list, parsed: ParsedScript) -> None:
    direction, a = _net_dir(args)
    if len(a) < 3:
        _warn(f"add_net needs <name> <drv> <rcv_csv> — ignored: {args}")
        return
    name, drv, rcv_csv = a[0], a[1], a[2]
    rcvs = [r for r in rcv_csv.split(',') if r]
    parsed.nets.append({"name": name, "drv": drv, "rcvs": rcvs, "dir": direction})


def _parse_add_bus(args: list, parsed: ParsedScript) -> None:
    direction, a = _net_dir(args)
    if len(a) < 3:
        _warn(f"add_bus needs <prefix>[N] <drv> <rcv_csv> — ignored: {args}")
        return
    m = _BUS_RE.match(a[0])
    if not m:
        _warn(f"bad add_bus spec '{a[0]}' — expected name[N] or name[lo:hi]")
        return
    prefix = m.group(1)
    if m.group(3) is not None:          # name[lo:hi]
        lo, hi = int(m.group(2)), int(m.group(3))
    else:                               # name[N] -> 0 .. N-1
        lo, hi = 0, int(m.group(2)) - 1
    drv, rcv_csv = a[1], a[2]
    rcvs = [r for r in rcv_csv.split(',') if r]
    for i in range(lo, hi + 1):
        parsed.nets.append({"name": f"{prefix}_{i}", "drv": drv,
                            "rcvs": rcvs, "dir": direction})


def _validate(parsed: ParsedScript) -> None:
    """Every net endpoint must reference a defined block."""
    for net in parsed.nets:
        eps = [net["drv"]] + net["rcvs"]
        for ep in eps:
            blk = _block_of(ep)
            if blk not in parsed.blocks:
                sys.exit(f"Error: net '{net['name']}' endpoint '{ep}' references "
                         f"unknown block '{blk}'")


def _cell_size_and_origin(parsed: ParsedScript):
    """Return (w, h, ox, oy).  set_die fixes the size with origin (0,0); without
    it, the cell is the block bounding box and blocks shift to the origin."""
    if parsed.die is not None:
        return parsed.die[0], parsed.die[1], 0.0, 0.0
    xs = [v for b in parsed.blocks.values() for v in (b[0], b[2])]
    ys = [v for b in parsed.blocks.values() for v in (b[1], b[3])]
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    ox, oy = min(xs), min(ys)
    return max(xs) - ox, max(ys) - oy, ox, oy


def _delete_cell_footprint(bdb_path: str, cell: str) -> None:
    """Remove the cell's representative instance subtree, cell_children, stale
    synthetic child cells, and now-orphaned nets — so a re-run replaces the cell
    cleanly without corrupting unrelated hierarchy.

    Name matching is done in Python (NOT SQL LIKE/GLOB) so cell names containing
    `_`, `%`, `*`, etc. are treated literally.  A synthetic child cell is dropped
    only when no surviving component still references it, so other instances of
    the cell keep valid (if size-synced) bodies rather than dangling cell refs.
    """
    if not os.path.exists(bdb_path):
        return
    con = sqlite3.connect(bdb_path)
    try:
        cur = con.cursor()
        # Tables may not exist yet in a brand-new/empty file.
        tabs = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "component" not in tabs:
            return

        # Representative-instance subtree: exact name == cell, or under "cell/".
        prefix = cell + "/"
        rows = cur.execute("SELECT id, name FROM component").fetchall()
        sub_ids = [cid for cid, name in rows
                   if name == cell or name.startswith(prefix)]
        if sub_ids:
            qm = ",".join("?" * len(sub_ids))
            cur.execute(f"DELETE FROM pin WHERE comp_id IN ({qm})", sub_ids)
            cur.execute(f"DELETE FROM component WHERE id IN ({qm})", sub_ids)

        # Prune nets left with no pins (the cell's old nets, plus any dead net).
        if "net" in tabs:
            orphans = [r[0] for r in cur.execute(
                "SELECT id FROM net WHERE id NOT IN "
                "(SELECT DISTINCT net_id FROM pin WHERE net_id IS NOT NULL)"
            ).fetchall()]
            if orphans:
                qm = ",".join("?" * len(orphans))
                if "net_props" in tabs:
                    cur.execute(f"DELETE FROM net_props WHERE net_id IN ({qm})",
                                orphans)
                cur.execute(f"DELETE FROM net WHERE id IN ({qm})", orphans)

        # Prune the derived routing/pipeline tables (audit T1-05): replacing a
        # cell body invalidates any persisted route, and those rows reference
        # the just-deleted nets/bundles — leaving them makes load_pipeline
        # rehydrate a corrupt checkpoint (dangling net_id / bundle FKs). The
        # tables are fully regenerated by a re-route, so clear them child-first.
        for tbl in ("route_snapshot", "net_via", "net_segment", "bus_via",
                    "bus_segment", "topology_seg_busterm", "topology_seg_conn",
                    "topology_bridge_segment", "topology_segment", "topology",
                    "bundle_busterm", "bundle_net", "bundle"):
            if tbl in tabs:
                cur.execute(f"DELETE FROM {tbl}")
        # Routing-time busterm rows ('tb:') back the (now-cleared) topology
        # annotations; drop them. Hier-derived 'bt:' rows are re-derived.
        if "busterm" in tabs:
            cur.execute("DELETE FROM busterm WHERE id LIKE 'tb:%'")

        if "cell_children" in tabs:
            cur.execute("DELETE FROM cell_children WHERE parent_cell=?", (cell,))

        # Synthetic per-block child cells created by this tool ("<cell>__<block>").
        if "cell" in tabs:
            syn_prefix = cell + "__"
            syn = [r[0] for r in cur.execute("SELECT name FROM cell").fetchall()
                   if r[0].startswith(syn_prefix)]
            if syn:
                qm = ",".join("?" * len(syn))
                # Always clear stale port declarations so a rebuild re-registers
                # fresh directions (cell_pin uses INSERT OR IGNORE downstream).
                if "cell_pin" in tabs:
                    cur.execute(f"DELETE FROM cell_pin WHERE cell IN ({qm})", syn)
                # Drop the cell row only when nothing references it anymore — keep
                # synthetic cells still used by OTHER instances of `cell`.
                referenced = {r[0] for r in cur.execute(
                    "SELECT DISTINCT cell FROM component").fetchall()}
                drop = [s for s in syn if s not in referenced]
                if drop:
                    qm = ",".join("?" * len(drop))
                    cur.execute(f"DELETE FROM cell WHERE name IN ({qm})", drop)
        con.commit()
    finally:
        con.close()


def convert(buda_path: str, bdb_path: str, cell: str) -> dict:
    """Parse the script and (re)create `cell` in the BDB.  Returns a small stats
    dict {cell, w, h, n_blocks, n_nets}.

    `bdb_path` may be a binary ``*.bdb`` (edited in place) or a diffable
    ``*.bdb.sql`` (or ``*.sql``) text fixture: the operation runs on a temp
    binary — an existing ``.sql`` is materialized first so a cell REPLACE works
    on a fixture — and the result is serialized back to the ``.sql`` at the end.
    """
    parsed = parse_script(buda_path)
    if not parsed.blocks:
        sys.exit("Error: no add_block commands found in the script")
    _validate(parsed)
    w, h, ox, oy = _cell_size_and_origin(parsed)

    # Route a *.bdb.sql target through a temp binary (materialize an existing
    # one first so REPLACE sees prior cells; serialize back at the end).
    is_sql = bdb_path.endswith(".sql")
    work_bin = bdb_path
    tmp_dir = None
    if is_sql:
        tmp_dir = tempfile.mkdtemp(prefix="buda_bdb_")
        work_bin = os.path.join(tmp_dir, os.path.basename(bdb_path)[:-4] or "out.bdb")
        if os.path.exists(bdb_path):
            bdb_serialize.load(bdb_path, work_bin)   # existing text -> binary

    try:
        # Replace an existing cell before rebuilding.
        _delete_cell_footprint(work_bin, cell)

        db = buda_db.BDB(work_bin)
        # 1. Size the parent cell.
        db.add_cell(cell, w, h)
        # 2. Define internal structure: a synthetic leaf cell + cell_children row
        #    per block, with coordinates local to the cell origin.
        for name, (x1, y1, x2, y2) in parsed.blocks.items():
            child_cell = f"{cell}__{name}"
            db.add_cell(child_cell, x2 - x1, y2 - y1)
            db.add_inst_to_cell(cell, name, child_cell, x1 - ox, y1 - oy)
        # 3. Materialize the representative instance "<cell>" + its children.
        db.add_inst(cell, cell, "", 0.0, 0.0)
        # 4. Create the cell-local nets on the child components.
        for net in parsed.nets:
            drv = f"{cell}/{net['drv']}"
            rcvs = [f"{cell}/{r}" for r in net["rcvs"]]
            if net["dir"] == "unknown":
                db.add_net_pins_undirected(net["name"], [drv] + rcvs)
            elif net["dir"] == "inout":
                db.add_net_pins_inout(net["name"], [drv] + rcvs)
            else:
                db.add_net_pins(net["name"], drv, rcvs)
        # 5. Size-sync any other instances of this cell already in the BDB.
        db.resize_cell(cell, w, h)

        if is_sql:
            del db                                   # flush/close before dump
            bdb_serialize.dump(work_bin, bdb_path)   # temp binary -> text
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"cell": cell, "w": w, "h": h,
            "n_blocks": len(parsed.blocks), "n_nets": len(parsed.nets)}


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if argv else 1)

    positional: list = []
    cell_override: Optional[str] = None
    i = 0
    while i < len(argv):
        if argv[i] in ("-cell", "--cell") and i + 1 < len(argv):
            cell_override = argv[i + 1]
            i += 2
        else:
            positional.append(argv[i])
            i += 1

    if len(positional) < 2:
        print("Usage: buda2bdb.py <cellname.buda> "
              "<bdbfile.bdb|bdbfile.bdb.sql> [-cell <name>]",
              file=sys.stderr)
        sys.exit(1)

    buda_path, bdb_path = positional[0], positional[1]
    if not os.path.exists(buda_path):
        print(f"Error: file not found: {buda_path}", file=sys.stderr)
        sys.exit(1)

    cell = cell_override or os.path.splitext(os.path.basename(buda_path))[0]
    stats = convert(buda_path, bdb_path, cell)
    print(f"Created cell '{stats['cell']}' ({stats['w']:g} x {stats['h']:g}) "
          f"with {stats['n_blocks']} block(s), {stats['n_nets']} net(s) "
          f"in {bdb_path}")


if __name__ == "__main__":
    main()
