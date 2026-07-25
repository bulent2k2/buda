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

It reads a `.bdb` (SQLite binary) OR a `.bdb.sql` (diffable text, round-tripped
via tools/bdb_serialize.py) and edits every net-referencing table: net, pin,
net_props, bundle_net, net_segment, net_via.  By default it writes back in place;
`-o/--output` writes to a new file instead, and — since the format follows the
extension — also converts between `.bdb` and `.bdb.sql`.  The input is never
modified when `-o` is given.

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
    tools/bdb_edit_bus.py in.bdb --bus bus_011 --set-bits 8 -o out.bdb.sql  # + convert
"""
import argparse
import os
import re
import shutil
import sqlite3
import stat
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
    # topology children before the topology parent
    "topology_segment", "topology_seg_busterm", "topology_seg_conn",
    "topology_bridge_segment", "topology",
    "bus_segment", "bus_via", "net_segment", "net_via",
    "route_snapshot",
]

# A bus bit net name = base + a bit token, where the token is a real separator
# (`_`, `_b`, or `[`), the bit digits, and an optional closing `]`.  Requiring an
# actual separator (rather than "any non-digits") is what stops `--bus data` from
# swallowing `data_out_0` — that parses to base='data_out', not base='data'.  The
# SAME regex drives both find_bus_bits and list_buses, so an editable bus and a
# listed bus are identical by construction.
_BIT_RE = re.compile(r"^(?P<base>.+?)(?P<sep>[_\[]b?)(?P<num>\d+)(?P<tail>\]?)$")


class Bit:
    __slots__ = ("index", "net_id", "name", "sep", "num_str", "tail")

    def __init__(self, index, net_id, name, sep, num_str, tail):
        self.index, self.net_id, self.name = index, net_id, name
        self.sep, self.num_str, self.tail = sep, num_str, tail


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def find_bus_bits(con, base):
    """Return the sorted list of Bit(s) whose net name is `base` + a bit token,
    restricted to the single most common token FRAME (separator + closing) so a
    prefix collision (bus_01 vs bus_011) or a stray same-prefix net can't leak
    in.

    The frame deliberately does NOT include the digit WIDTH: an unpadded bus that
    crosses a decimal boundary (s2p_0 .. s2p_15) mixes 1- and 2-digit suffixes and
    must stay ONE bus — grouping on width would split it and leave bits 10-15
    behind on a delete/prune.  Zero-padding is handled purely as a naming-format
    concern for grow (see make_namer).

    The bus base must match EXACTLY (`_BIT_RE`'s `base` group), so a shorter base
    can never capture a longer bus — `--bus data` won't touch `data_out` (which
    parses to base='data_out')."""
    rows = con.execute(
        "SELECT id, name FROM net WHERE name LIKE ? ESCAPE '\\'",
        (_like_prefix(base),),
    ).fetchall()
    cand = []
    for nid, name in rows:
        m = _BIT_RE.fullmatch(name)
        if not m or m.group("base") != base:
            continue
        cand.append(Bit(int(m.group("num")), nid, name,
                        m.group("sep"), m.group("num"), m.group("tail")))
    if not cand:
        return []
    from collections import Counter
    frames = Counter((b.sep, b.tail) for b in cand)
    best = frames.most_common(1)[0][0]
    bits = [b for b in cand if (b.sep, b.tail) == best]
    bits.sort(key=lambda b: b.index)
    return bits


def _like_prefix(base):
    # Escape LIKE metacharacters in the base so `bus_011` treats `_` literally.
    return base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def make_namer(base, bits):
    """Return f(index) -> bit net name in this bus's frame.

    Zero-padding is inferred from the EXISTING bits: the bus is padded iff some
    bit's digit string is wider than its minimal decimal form (e.g. `05`, `00`).
    A padded bus keeps its common width (`bus_011_b02`); an UNPADDED bus formats
    each index at its natural width (`s2p_10`, never `s2p_010`), so growing a bus
    across a decimal boundary produces the right names."""
    sep, tail = bits[-1].sep, bits[-1].tail
    padded = any(len(b.num_str) > len(str(b.index)) for b in bits)
    width = max((len(b.num_str) for b in bits), default=1) if padded else 0
    return lambda i: f"{base}{sep}{str(i).zfill(width)}{tail}"


def list_buses(con, prefix=None):
    """Group every net into buses by (base, token frame) and print bit counts."""
    rows = con.execute("SELECT id, name FROM net ORDER BY name").fetchall()
    buses = {}
    # Shared _BIT_RE anchors the token on a real separator, so a base with digits
    # (bus_011) splits correctly; the digit WIDTH is NOT part of the key — an
    # unpadded bus crossing a decimal boundary (s2p_0..s2p_15) is ONE bus
    # (identical to find_bus_bits by construction).
    for nid, name in rows:
        mm = _BIT_RE.fullmatch(name)
        if not mm:
            buses.setdefault(("scalar", name, "", ""), []).append(None)
            continue
        key = ("bus", mm.group("base"), mm.group("sep"), mm.group("tail"))
        buses.setdefault(key, []).append(int(mm.group("num")))
    printed = 0
    for (kind, base, sep, tail), members in sorted(buses.items()):
        if prefix and not base.startswith(prefix):
            continue
        if kind == "scalar":      # a net with no bit token
            if prefix:            # only show scalars when a prefix was asked for
                print(f"  {base:32} (scalar net)")
                printed += 1
            continue
        idxs = sorted(members)
        frame = f"{sep}#{tail}"
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
    namer = make_namer(base, bits)
    # Each of these tables always exists in a full BDB, but guard so the tool
    # also works on a partial/minimal netlist (e.g. a net-only fixture).
    template_pins = []
    if _table_exists(con, "pin"):
        template_pins = con.execute(
            "SELECT comp_id, pin_name, dir, px, py FROM pin WHERE net_id=?",
            (template.net_id,)).fetchall()
    template_props = None
    if _table_exists(con, "net_props"):
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
    frame = f"{base}{bits[-1].sep}#{bits[-1].tail}"
    print(f"bus '{base}': {cur_n} bit(s) "
          f"[{bits[0].index}..{bits[-1].index}], frame '{frame}'")
    if target == cur_n:
        print(f"  already {target} bits — nothing to do")
        return False                     # no netlist change → routing NOT stale
    if target < cur_n:
        drop = bits[target:]              # keep the lowest-index `target` bits
        print(f"  PRUNE {cur_n} -> {target}: dropping {len(drop)} high bit(s) "
              f"[{drop[0].index}..{drop[-1].index}]")
        _delete_bits(con, drop, verbose)
    else:
        print(f"  GROW {cur_n} -> {target}: adding {target - cur_n} bit(s), "
              f"cloning template bit {bits[-1].index} ({bits[-1].name})")
        _grow_bits(con, base, bits, target, verbose)
    return True


def _delete(con, base, verbose):
    bits = find_bus_bits(con, base)
    if not bits:
        raise SystemExit(f"error: no bus '{base}' found")
    print(f"bus '{base}': DELETE all {len(bits)} bit(s) "
          f"[{bits[0].index}..{bits[-1].index}]")
    _delete_bits(con, bits, verbose)
    return True


def _load(input_path):
    """Materialize a binary working copy of the input BDB and open it.  Returns
    (con, work_path); the caller removes work_path.  A *.bdb.sql text input is
    round-tripped through bdb_serialize; a *.bdb binary is snapshotted through
    SQLite's backup API (NOT a raw byte copy), so committed changes that live only
    in a `-wal` sidecar — the engine opens every BDB with journal_mode=WAL — are
    included and an in-place edit can't overwrite the source from a stale copy.
    The input is never mutated."""
    tmp = tempfile.NamedTemporaryFile(suffix=".bdb", delete=False)
    tmp.close()
    if input_path.endswith(".sql"):
        sys.path.insert(0, _HERE)
        import bdb_serialize
        try:
            bdb_serialize.load(input_path, tmp.name)   # raises if missing
        except FileNotFoundError:
            os.unlink(tmp.name)
            raise SystemExit(f"error: {input_path} not found")
    else:
        if not os.path.exists(input_path):
            os.unlink(tmp.name)
            raise SystemExit(f"error: {input_path} not found")
        src = sqlite3.connect(input_path)
        dst = sqlite3.connect(tmp.name)
        try:
            src.backup(dst)                # WAL-aware, consistent snapshot
        finally:
            src.close()
            dst.close()
    return sqlite3.connect(tmp.name), tmp.name


def _write(work_path, target_path):
    """Write the edited working binary to `target_path`, format by extension
    (*.bdb.sql -> diffable text via bdb_serialize.dump, else a binary copy).

    Writes into a temp sibling first and os.replace()s it into place, so an
    interrupted or disk-full final write can never leave the target (which may be
    the user's original BDB on an in-place edit) partial or corrupt.

    The temp inherits the existing target's permission mode (mkstemp is 0600, so
    an in-place edit would otherwise silently downgrade a 0644 file), and after
    the replace any stale WAL/SHM sidecars beside the target are removed: the
    working copy is opened in SQLite's default rollback mode, so the file we write
    is a complete standalone database and any leftover `-wal`/`-shm` from the
    PREVIOUS database would be replayed over it on the next open and corrupt it.
    Editing a BDB that is concurrently open in the engine/Floorplanner is not
    supported (snapshot in, replace out)."""
    parent = os.path.dirname(os.path.abspath(target_path))
    if not os.path.isdir(parent):
        raise SystemExit(f"error: output directory does not exist: {parent}")
    if os.path.exists(target_path):
        mode = stat.S_IMODE(os.stat(target_path).st_mode)   # keep e.g. 0644
    else:
        umask = os.umask(0)          # new file: respect the process umask
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp_out = tempfile.mkstemp(dir=parent, prefix=".bdb_edit_bus.", suffix=".tmp")
    os.close(fd)
    try:
        if target_path.endswith(".sql"):
            sys.path.insert(0, _HERE)
            import bdb_serialize
            bdb_serialize.dump(work_path, tmp_out)
        else:
            shutil.copyfile(work_path, tmp_out)
        os.chmod(tmp_out, mode)
        os.replace(tmp_out, target_path)   # atomic on the same filesystem
    except BaseException:
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)
        raise
    # Drop any stale WAL/SHM sidecars from the database we just replaced, so a
    # subsequent open can't replay pre-edit frames over the new main file.
    for side in ("-wal", "-shm"):
        try:
            os.unlink(target_path + side)
        except FileNotFoundError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Edit a bus's bit count (or delete it) in a BDB netlist.")
    ap.add_argument("db", help="input BDB file (.bdb binary or .bdb.sql text)")
    ap.add_argument("-o", "--output", metavar="PATH",
                    help="write the result here instead of editing in place; the "
                         "format follows the extension (.bdb binary / .bdb.sql "
                         "text), so this also converts between the two")
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
    if args.output and not is_edit:
        ap.error("--output only applies to an edit (--set-bits/--delete)")

    con, work = _load(args.db)
    target = args.output or args.db
    try:
        if args.list is not None and not is_edit:
            print(f"buses in {args.db}:")
            list_buses(con, args.list or None)
            return 0

        if not args.bus:
            ap.error("--bus is required for --set-bits/--delete")

        verbose = True
        if args.delete or args.set_bits == 0:
            changed = _delete(con, args.bus, verbose)
        else:
            changed = _resize(con, args.bus, args.set_bits, verbose)

        # Routing is stale ONLY when the netlist actually changed — a no-op
        # resize must never clear valid routing (nor warn about it).
        if changed:
            if args.clear_routing:
                _clear_routing(con, verbose)
            else:
                print("  NOTE: bundling/routing is now stale — re-run the flow "
                      "(run_bundler/run_hier_bundler -> generate_topologies -> "
                      "...) or pass --clear-routing.")

        if args.dry_run:
            con.rollback()
            print("dry-run: rolled back, no changes written")
            return 0
        # Nothing changed and no format-conversion output requested: leave the
        # input untouched rather than rewrite an identical file.
        if not changed and not args.output:
            con.rollback()
            print("  no change — input left untouched")
            return 0
        con.commit()
        con.close()
        _write(work, target)
        print(f"written to {target}"
              + (f" (from {args.db})" if target != args.db else ""))
        return 0
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
        if os.path.exists(work):
            os.unlink(work)


if __name__ == "__main__":
    sys.exit(main())
