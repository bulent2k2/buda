#!/usr/bin/env python3
"""
tools/bdb2buda.py — Convert a BDB to a flat .buda script.

Usage:
  python3 tools/bdb2buda.py <file.bdb|file.bdb.sql> [-cell <cellname>] [-scale <N>] [-o <output.buda>]

The input may be a binary ``.bdb`` or a diffable ``.bdb.sql`` text fixture
(the latter is materialized to a throwaway temp binary, read-only).

With no -cell flag, the top-level (depth-0) components are written as blocks
and all nets connecting them are emitted.

With -cell <cellname>, the children of the first component whose name or
cell-type matches <cellname> are written instead.  Block coordinates are
made relative to the parent's lower-left corner.

-scale <N> (default 10) multiplies all coordinates by N and rounds to the
nearest integer.  The flat routing flow expects integer coordinates; the
default factor of 10 converts µm values (e.g. 12.5 µm) to tenths of a µm
(125), avoiding precision loss.  Use -scale 1 to suppress scaling.

Nets with ≥2 endpoints inside the selected scope are emitted.  If a net has
a pin with direction DRIVER or OUTPUT it becomes the driver; otherwise the
first pin is used as driver and the rest as receivers.

Sequential nets sharing the same prefix, driver, and receiver set — with
indices 0 … N-1 and no leading zeros — are collapsed to a single add_bus
command.  All others use add_net.
"""

import os
import re
import sys
from collections import defaultdict
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

# Lets convert() accept a diffable *.bdb.sql text fixture as transparently as a
# binary *.bdb (materialized to a throwaway temp binary, read-only).
import bdb_serialize


def _leaf(name: str) -> str:
    """Return the leaf segment of a hierarchical path (part after last '/')."""
    return name.rsplit("/", 1)[-1]


def _parse_bus_suffix(name: str) -> Optional[tuple]:
    """
    Return (prefix, idx) if name matches '<prefix>_<N>' where N is a
    non-negative integer with no leading zeros (except the literal "0").
    Return None otherwise.
    """
    m = re.match(r'^(.+)_(\d+)$', name)
    if m is None:
        return None
    prefix, idx_str = m.group(1), m.group(2)
    # Reject zero-padded indices — add_bus expansion produces _0, _1, … not _00, _01
    if len(idx_str) > 1 and idx_str[0] == '0':
        return None
    return prefix, int(idx_str)


def _fmt_pin(comp_name: str, pin_name: str) -> str:
    """
    Format a pin reference as 'leaf_name.port'.

    When the stored pin_name is the component name itself (the convention
    used by add_net_pins_undirected when given bare component names), fall
    back to the generic port label 'p' for cleaner output.
    """
    leaf = _leaf(comp_name)
    if not pin_name or pin_name == comp_name or pin_name == leaf:
        return f"{leaf}.p"
    return f"{leaf}.{pin_name}"


def _sc(v: float, scale: float) -> str:
    """Scale v by scale and format as an integer (routing flow requires integers)."""
    return str(round(v * scale))


def convert(bdb_path: str, cell_name: Optional[str] = None,
            scale: float = 10.0) -> str:
    """
    Convert a BDB to a flat .buda script and return it as a string.

    Parameters
    ----------
    bdb_path :
        Path to the BDB file.
    cell_name :
        If given, export the children of the first component whose ``name``
        or ``cell`` field matches this string.  Default: top-level (depth-0)
        components.
    scale :
        Multiply every coordinate by this factor and round to the nearest
        integer.  Default 10 converts µm → tenths-of-µm so that fractional
        coordinates (e.g. 12.5 µm) become valid integers (125).

    A ``*.bdb.sql`` (or ``*.sql``) text fixture is accepted transparently: it
    is materialized to a throwaway temp binary first (read-only — the source
    ``.sql`` is never modified). A binary ``*.bdb`` is used directly.
    """
    try:
        bin_path = bdb_serialize.materialize_if_sql(bdb_path)
    except Exception as e:
        raise ValueError(f"could not read serialized BDB {bdb_path!r}: {e}")
    db = buda_db.BDB(bin_path)
    all_comps = {c.id: c for c in db.all_components()}

    # ── Select the target component set ──────────────────────────────────────
    parent_comp = None
    if cell_name is None:
        target_comps = [c for c in all_comps.values() if c.parent_id == -1]
        die_w, die_h = db.die_w(), db.die_h()
    else:
        parent_comp = next(
            (c for c in all_comps.values() if c.name == cell_name), None
        )
        if parent_comp is None:
            parent_comp = next(
                (c for c in all_comps.values() if c.cell == cell_name), None
            )
        if parent_comp is None:
            raise ValueError(
                f"No component named or of cell type '{cell_name}' found in {bdb_path}"
            )
        target_comps = [
            c for c in all_comps.values() if c.parent_id == parent_comp.id
        ]
        die_w = parent_comp.x2 - parent_comp.x1
        die_h = parent_comp.y2 - parent_comp.y1

    if not target_comps:
        raise ValueError("No target components found")

    # Fallback: derive die size from component bounding box when not set.
    if die_w == 0.0 or die_h == 0.0:
        all_x = [v for c in target_comps for v in (c.x1, c.x2)]
        all_y = [v for c in target_comps for v in (c.y1, c.y2)]
        die_w = max(all_x) - min(all_x) if all_x else 0.0
        die_h = max(all_y) - min(all_y) if all_y else 0.0

    target_ids = {c.id for c in target_comps}
    comp_name_by_id = {c.id: c.name for c in target_comps}

    # Origin offset so child coordinates are relative to the parent's LL corner.
    # An UNPLACED parent (bbox -1..-1, the canonical DEF+Verilog merge state)
    # has no meaningful corner: derive the origin from the children's own
    # extent instead, matching the die-size fallback above — else every block
    # shifts by +1 and lands outside the emitted die (audit T1-03).
    parent_unplaced = (parent_comp is None or parent_comp.x2 <= parent_comp.x1
                       or parent_comp.x1 < 0 or parent_comp.y1 < 0)
    if parent_comp is not None and not parent_unplaced:
        ox, oy = parent_comp.x1, parent_comp.y1
    elif target_comps:
        ox = min(c.x1 for c in target_comps)
        oy = min(c.y1 for c in target_comps)
    else:
        ox = oy = 0.0

    # ── Collect and group pins ────────────────────────────────────────────────
    pins_by_net: dict = defaultdict(list)
    for p in db.all_pins():
        if p.comp_id in target_ids:
            pins_by_net[p.net_id].append(p)

    net_name_by_id = {n.id: n.name for n in db.all_nets()}

    # ── Build per-net descriptors (driver + receivers) ────────────────────────
    DRIVER_DIRS = {"OUTPUT", "DRIVER"}
    net_descs: dict = {}

    for net_id, pins in pins_by_net.items():
        if len(pins) < 2:
            continue  # boundary / interface pin — skip

        net_name = net_name_by_id.get(net_id, f"net_{net_id}")
        drivers = [p for p in pins if p.dir in DRIVER_DIRS]
        if drivers:
            drv, rcvs = drivers[0], [p for p in pins if p is not drivers[0]]
        else:
            drv, rcvs = pins[0], pins[1:]

        def _fp(p, _cni=comp_name_by_id):
            return _fmt_pin(_cni[p.comp_id], p.pin_name)

        # Direction class for the add_net suffix (audit T1-01): all-INOUT nets
        # emit ` inout`, all-UNKNOWN emit ` unknown` — without it INOUT/UNKNOWN
        # nets silently degraded to plain directed nets on export.
        dirs = {p.dir for p in pins}
        if dirs <= {"INOUT"}:
            dir_suffix = " inout"
        elif dirs <= {"UNKNOWN", ""}:
            dir_suffix = " unknown"
        else:
            dir_suffix = ""

        # A driver block appearing among its own receivers hard-fails the CLI
        # ("used as both driver and receiver"): drop those receivers with a
        # warning (the driver block is already attached) (audit T1-01).
        drv_block = comp_name_by_id[drv.comp_id]
        kept_rcvs = []
        for p in rcvs:
            if comp_name_by_id[p.comp_id] == drv_block:
                sys.stderr.write(
                    f"bdb2buda: net {net_name!r}: dropping receiver on driver "
                    f"block {drv_block!r} (self-loop)\n")
                continue
            kept_rcvs.append(p)

        net_descs[net_id] = {
            "name": net_name,
            "drv":  _fp(drv),
            "rcvs": [_fp(p) for p in kept_rcvs],
            "dir":  dir_suffix,
        }

    # ── Detect buses: groups of nets prefix_0 … prefix_{N-1} ─────────────────
    prefix_items: dict = defaultdict(list)   # prefix → [(idx, net_id)]
    for net_id, desc in net_descs.items():
        parsed = _parse_bus_suffix(desc["name"])
        if parsed is not None:
            prefix_items[parsed[0]].append((parsed[1], net_id))

    bus_net_ids: set = set()
    bus_lines: list = []

    for prefix, items in sorted(prefix_items.items()):
        items.sort()
        idxs   = [i for i, _ in items]
        if idxs != list(range(len(idxs))):
            continue  # missing or non-consecutive indices
        net_ids = [n for _, n in items]
        descs   = [net_descs[n] for n in net_ids]
        first   = descs[0]
        # All bits must share the same driver and the same (unordered) receiver list.
        if not all(
            d["drv"] == first["drv"] and sorted(d["rcvs"]) == sorted(first["rcvs"])
            for d in descs[1:]
        ):
            continue
        N = len(net_ids)
        rcv_csv = ",".join(first["rcvs"])
        bus_lines.append(f"add_bus {prefix}[{N}] {first['drv']} {rcv_csv}")
        bus_net_ids.update(net_ids)

    # ── Assemble the script ───────────────────────────────────────────────────
    def s(v: float) -> str:
        return _sc(v, scale)

    lines: list = []
    lines.append(f"set_die {s(die_w)} {s(die_h)}")
    lines.append("")

    for c in sorted(target_comps, key=lambda c: c.name):
        leaf = _leaf(c.name)
        lines.append(
            f"add_block {leaf}"
            f" {s(c.x1 - ox)} {s(c.y1 - oy)}"
            f" {s(c.x2 - ox)} {s(c.y2 - oy)}"
        )

    if net_descs:
        lines.append("")
        for line in bus_lines:
            lines.append(line)

        remaining = sorted(
            [(desc["name"], nid)
             for nid, desc in net_descs.items()
             if nid not in bus_net_ids],
            key=lambda x: x[0],
        )
        for name, nid in remaining:
            desc = net_descs[nid]
            suffix = desc.get("dir", "")
            if desc["rcvs"]:
                lines.append(f"add_net {name} {desc['drv']} "
                             f"{','.join(desc['rcvs'])}{suffix}")
            elif suffix:
                # No receivers left but a direction class to preserve: emit
                # the driver as its own sole endpoint with the suffix.
                lines.append(f"add_net {name} {desc['drv']}{suffix}")
            else:
                lines.append(f"add_net {name} {desc['drv']}")

    return "\n".join(lines) + "\n"


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if argv else 1)

    bdb_path  = argv[0]
    cell_name: Optional[str] = None
    out_path:  Optional[str] = None
    scale: float = 10.0

    i = 1
    while i < len(argv):
        if argv[i] in ("-cell", "--cell") and i + 1 < len(argv):
            cell_name = argv[i + 1]
            i += 2
        elif argv[i] in ("-scale", "--scale") and i + 1 < len(argv):
            try:
                scale = float(argv[i + 1])
            except ValueError:
                print(f"Error: -scale requires a number, got {argv[i+1]!r}",
                      file=sys.stderr)
                sys.exit(1)
            i += 2
        elif argv[i] in ("-o", "--output") and i + 1 < len(argv):
            out_path = argv[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {argv[i]}", file=sys.stderr)
            print(
                "Usage: bdb2buda.py <file.bdb|file.bdb.sql> [-cell <cellname>] "
                "[-scale <N>] [-o <output.buda>]",
                file=sys.stderr,
            )
            sys.exit(1)

    if not os.path.exists(bdb_path):
        print(f"Error: file not found: {bdb_path}", file=sys.stderr)
        sys.exit(1)

    try:
        script = convert(bdb_path, cell_name, scale)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if out_path:
        with open(out_path, "w") as f:
            f.write(script)
        print(f"Written to {out_path}")
    else:
        sys.stdout.write(script)


if __name__ == "__main__":
    main()
