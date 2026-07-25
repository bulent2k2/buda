#!/usr/bin/env python3
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

"""Edit a bus's bit count (or delete it) in a BDB netlist.

A **bus** is a set of `net` rows sharing a base name plus a numeric bit suffix —
e.g. `bus_011_b00, bus_011_b01, ... bus_011_b63`, or `s2p_0, s2p_1, s2p_2`.  The
bit membership is carried in the net NAME (not `net_props.bus_name`, which real
BDBs leave unset), so this tool identifies a bus by name pattern and resizes the
set:

  * PRUNE  — keep the lowest-index bits, drop the rest (64-bit -> 4-bit);
  * GROW   — clone a template bit's pin structure to add higher-index bits
             (2-bit -> 16-bit);
  * DELETE — remove every bit of the bus.

It operates directly on the SQLite BDB (`.bdb` binary) or its diffable text form
(`.bdb.sql`, round-tripped via tools/bdb_serialize.py), deleting/inserting across
every net-referencing table: net, pin, net_props, bundle_net, net_segment,
net_via.

A bit-count change invalidates any existing bundling/routing: the tool prints a
WARNING and, with --clear-routing, also drops the derived route tables so the BDB
is a clean pre-bundle state.  Re-run the flow (run_bundler / run_hier_bundler ->
generate_topologies -> ...) afterwards.

Usage:
    tools/bdb_edit_bus.py <db>                       # list every detected bus
    tools/bdb_edit_bus.py <db> --list bus_01         # list buses matching a prefix
    tools/bdb_edit_bus.py <db> --bus bus_011 --set-bits 4     # prune or grow to 4
    tools/bdb_edit_bus.py <db> --bus bus_011 --delete
    tools/bdb_edit_bus.py <db> --bus bus_011 --set-bits 16 --dry-run
    tools/bdb_edit_bus.py <db> --bus bus_011 --delete --clear-routing
"""
import argparse
import os
import re
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))

# Every table with a column referencing net(id), and its net-key column.  A bit
# net is removed from ALL of them.
_NET_REF_TABLES = [
    ("pin",         "net_id"),
    ("net_props",   "net_id"),
    ("bundle_net",  "net_id"),
    ("net_segment", "net_id"),
    ("net_via",     "net_id"),
]

# Route-derived tables cleared by --clear-routing (a netlist change invalidates
# the bundling and everything downstream of it).
_ROUTE_TABLES = [
    "bundle_net", "bundle_busterm", "bundle",
    "topology_segment", "topology_seg_busterm", "topology_bridge_segment",
    "topology", "bus_segment", "bus_via", "net_segment", "net_via",
    "route_snapshot",
]

# A bit token is the tail of a net name after the bus base: an optional
# non-digit separator (``_``, ``_b``, ``[`` ...), the bit digits, and an
# optional closing non-digit (``]``).  The base + token must be the WHOLE name.
_TOKEN_RE = re.compile(r"^(?P<sep>\D*?)(?P<num>\d+)(?P<tail>\D*)$")


class Bit:
    __slots__ = ("index", "net_id", "name", "sep", "width", "tail")

    def __init__(self, index, net_id, name, sep, width, tail):
        self.index, self.net_id, self.name = index, net_id, name
        self.sep, self.width, self.tail = sep, width, tail


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def find_bus_bits(con, base):
    """Return the sorted list of Bit(s) whose net name is `base` + a bit token,
    restricted to the single most common token frame (sep,tail,width) so a
    prefix collision (bus_01 vs bus_011) or a stray same-prefix net can't leak
    in."""
    rows = con.execute(
        "SELECT id, name FROM net WHERE name LIKE ? ESCAPE '\\'",
        (_like_prefix(base),),
    ).fetchall()
    cand = []
    for nid, name in rows:
        if not name.startswith(base):
            continue
        m = _TOKEN_RE.fullmatch(name[len(base):])
        if not m or m.group("tail") not in ("", "]"):
            continue
        cand.append(Bit(int(m.group("num")), nid, name,
                        m.group("sep"), len(m.group("num")), m.group("tail")))
    if not cand:
        return []
    # Keep only the dominant token frame (separator + closing + digit width).
    from collections import Counter
    frames = Counter((b.sep, b.tail, b.width) for b in cand)
    best = frames.most_common(1)[0][0]
    bits = [b for b in cand if (b.sep, b.tail, b.width) == best]
    bits.sort(key=lambda b: b.index)
    return bits


def _like_prefix(base):
    # Escape LIKE metacharacters in the base so `bus_011` treats `_` literally.
    return base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def make_namer(base, template):
    """Return f(index) -> bit net name in the template bit's frame."""
    sep, width, tail = template.sep, template.width, template.tail
    return lambda i: f"{base}{sep}{str(i).zfill(width)}{tail}"


def list_buses(con, prefix=None):
    """Group every net into buses by (base, token frame) and print bit counts."""
    rows = con.execute("SELECT id, name FROM net ORDER BY name").fetchall()
    buses = {}
    # A bit token is anchored on a real separator (`_`, `_b`, or `[`) so a base
    # that itself contains digits (bus_011) is split correctly.
    bit_re = re.compile(r"^(?P<base>.+?)(?P<sep>[_\[]b?)(?P<num>\d+)(?P<tail>\]?)$")
    for nid, name in rows:
        mm = bit_re.fullmatch(name)
        if not mm:
            buses.setdefault((name, "", "", 0), []).append((nid, name))
            continue
        key = (mm.group("base"), mm.group("sep"), mm.group("tail"),
               len(mm.group("num")))
        buses.setdefault(key, []).append((nid, int(mm.group("num"))))
    printed = 0
    for (base, sep, tail, width), members in sorted(buses.items()):
        if prefix and not base.startswith(prefix):
            continue
        if width == 0:            # scalar net (no bit token)
            if prefix:            # only show scalars when a prefix was asked for
                print(f"  {base:32} (scalar net)")
                printed += 1
            continue
        idxs = sorted(i for _, i in members)
        frame = f"{sep}{'#'*width}{tail}"
        gap = "" if idxs == list(range(idxs[0], idxs[-1] + 1)) else "  [non-contiguous]"
        print(f"  {base:28} {len(idxs):4d} bits  "
              f"[{idxs[0]}..{idxs[-1]}] frame='{base}{frame}'{gap}")
        printed += 1
    if not printed:
        print("  (no buses found)" + (f" matching '{prefix}'" if prefix else ""))


def _delete_bits(con, bits, verbose):
    ids = [b.net_id for b in bits]
    if not ids:
        return
    qmarks = ",".join("?" * len(ids))
    for tbl, col in _NET_REF_TABLES:
        if _table_exists(con, tbl):
            con.execute(f"DELETE FROM {tbl} WHERE {col} IN ({qmarks})", ids)
    con.execute(f"DELETE FROM net WHERE id IN ({qmarks})", ids)
    if verbose:
        for b in bits:
            print(f"    - deleted bit {b.index}: {b.name} (net_id {b.net_id})")


def _grow_bits(con, base, bits, target, verbose):
    """Add bits until the bus has `target` bits, cloning the highest-index bit's
    pin structure (and bundle membership, if bundled)."""
    template = bits[-1]                    # highest existing index
    namer = make_namer(base, template)
    template_pins = con.execute(
        "SELECT comp_id, pin_name, dir, px, py FROM pin WHERE net_id=?",
        (template.net_id,)).fetchall()
    template_props = con.execute(
        "SELECT hpwl, fanout, driver_comp, bus_name, bit_index, bundle_id "
        "FROM net_props WHERE net_id=?", (template.net_id,)).fetchone()
    template_bundle = []
    if _table_exists(con, "bundle_net"):
        template_bundle = con.execute(
            "SELECT bundle_id, ord FROM bundle_net WHERE net_id=?",
            (template.net_id,)).fetchall()

    next_index = template.index + 1
    n_add = target - len(bits)
    added = []
    for k in range(n_add):
        idx = next_index + k
        name = namer(idx)
        if con.execute("SELECT 1 FROM net WHERE name=?", (name,)).fetchone():
            raise SystemExit(f"error: net '{name}' already exists — refusing to "
                             f"clobber (bus frame collision)")
        cur = con.execute("INSERT INTO net(name) VALUES(?)", (name,))
        new_id = cur.lastrowid
        # Clone pins.  A pin whose name equals the TEMPLATE net name is a
        # per-bit interface pin (hierarchy boundary) — rename it to the new bit;
        # leaf port pins (out/in/...) keep their name.
        for comp_id, pin_name, pdir, px, py in template_pins:
            pn = name if pin_name == template.name else pin_name
            con.execute("INSERT OR IGNORE INTO pin(net_id,comp_id,pin_name,dir,"
                        "px,py) VALUES(?,?,?,?,?,?)",
                        (new_id, comp_id, pn, pdir, px, py))
        if template_props is not None:
            hpwl, fanout, drv, busn, _bidx, bundle_id = template_props
            con.execute("INSERT INTO net_props(net_id,hpwl,fanout,driver_comp,"
                        "bus_name,bit_index,bundle_id) VALUES(?,?,?,?,?,?,?)",
                        (new_id, hpwl, fanout, drv, busn, idx, bundle_id))
        for bundle_id, _ord in template_bundle:
            con.execute("INSERT OR IGNORE INTO bundle_net(bundle_id,net_id,ord) "
                        "VALUES(?,?,?)", (bundle_id, new_id, idx))
        added.append((idx, name, new_id))
        if verbose:
            extra = " +bundle_net" if template_bundle else ""
            print(f"    + added bit {idx}: {name} (net_id {new_id}, "
                  f"{len(template_pins)} pins{extra})")
    return added


def _clear_routing(con, verbose):
    n = 0
    for tbl in _ROUTE_TABLES:
        if _table_exists(con, tbl):
            cur = con.execute(f"DELETE FROM {tbl}")
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if verbose:
        print(f"    cleared route-derived tables ({', '.join(_ROUTE_TABLES)})")


def _resize(con, base, target, verbose):
    bits = find_bus_bits(con, base)
    if not bits:
        raise SystemExit(f"error: no bus '{base}' found (no nets named "
                         f"'{base}<sep><digits>')")
    cur_n = len(bits)
    print(f"bus '{base}': {cur_n} bit(s) "
          f"[{bits[0].index}..{bits[-1].index}], frame "
          f"'{base}{bits[-1].sep}{'#'*bits[-1].width}{bits[-1].tail}'")
    if target == cur_n:
        print(f"  already {target} bits — nothing to do")
        return
    if target < cur_n:
        drop = bits[target:]              # keep the lowest-index `target` bits
        print(f"  PRUNE {cur_n} -> {target}: dropping {len(drop)} high bit(s) "
              f"[{drop[0].index}..{drop[-1].index}]")
        _delete_bits(con, drop, verbose)
    else:
        print(f"  GROW {cur_n} -> {target}: adding {target - cur_n} bit(s), "
              f"cloning template bit {bits[-1].index} ({bits[-1].name})")
        _grow_bits(con, base, bits, target, verbose)


def _delete(con, base, verbose):
    bits = find_bus_bits(con, base)
    if not bits:
        raise SystemExit(f"error: no bus '{base}' found")
    print(f"bus '{base}': DELETE all {len(bits)} bit(s) "
          f"[{bits[0].index}..{bits[-1].index}]")
    _delete_bits(con, bits, verbose)


def _open(db_path):
    """Open the BDB.  Returns (con, commit_fn).  For a *.bdb.sql text file, load
    it into a temp binary, and on commit re-dump the text in place."""
    if db_path.endswith(".sql"):
        sys.path.insert(0, _HERE)
        import bdb_serialize
        tmp = tempfile.NamedTemporaryFile(suffix=".bdb", delete=False)
        tmp.close()
        bdb_serialize.load(db_path, tmp.name)
        con = sqlite3.connect(tmp.name)

        def commit():
            con.commit()
            con.close()
            bdb_serialize.dump(tmp.name, db_path)
            os.unlink(tmp.name)
        return con, commit, tmp.name
    if not os.path.exists(db_path):
        raise SystemExit(f"error: {db_path} not found")
    con = sqlite3.connect(db_path)

    def commit():
        con.commit()
        con.close()
    return con, commit, db_path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Edit a bus's bit count (or delete it) in a BDB netlist.")
    ap.add_argument("db", help="BDB file (.bdb binary or .bdb.sql text)")
    ap.add_argument("--bus", help="bus base name, e.g. bus_011 or s2p")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--set-bits", type=int, metavar="N",
                   help="resize the bus to N bits (prune or grow)")
    g.add_argument("--delete", action="store_true",
                   help="delete every bit of the bus")
    ap.add_argument("--list", nargs="?", const="", metavar="PREFIX",
                    help="list detected buses (optionally filtered by prefix)")
    ap.add_argument("--clear-routing", action="store_true",
                    help="also drop route-derived tables (bundling/topology/"
                         "NUTS) — a bit-count change invalidates them")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the planned change without writing")
    args = ap.parse_args(argv)

    if args.set_bits is not None and args.set_bits < 0:
        ap.error("--set-bits must be >= 0 (use --delete for 0/none)")
    is_edit = args.delete or args.set_bits is not None
    if not is_edit and args.list is None:
        # Default action with no --bus/--list: list everything.
        args.list = ""

    con, commit, path = _open(args.db)
    try:
        if args.list is not None and not is_edit:
            print(f"buses in {args.db}:")
            list_buses(con, args.list or None)
            con.close()
            return 0

        if not args.bus:
            ap.error("--bus is required for --set-bits/--delete")

        verbose = True
        if args.set_bits is not None and args.set_bits == 0:
            _delete(con, args.bus, verbose)
        elif args.delete:
            _delete(con, args.bus, verbose)
        else:
            _resize(con, args.bus, args.set_bits, verbose)

        if args.clear_routing:
            _clear_routing(con, verbose)
        else:
            print("  NOTE: bundling/routing is now stale — re-run the flow "
                  "(run_bundler/run_hier_bundler -> generate_topologies -> ...) "
                  "or pass --clear-routing.")

        if args.dry_run:
            con.rollback()
            con.close()
            print("dry-run: rolled back, no changes written")
            return 0
        commit()
        print(f"written to {args.db}")
        return 0
    except SystemExit:
        con.rollback()
        con.close()
        raise


if __name__ == "__main__":
    sys.exit(main())
