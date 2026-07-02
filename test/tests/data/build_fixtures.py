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

"""
Regenerate the checked-in `*.bdb.sql` hierarchical test fixtures.

Each fixture is built *deterministically* from code here (no randomness, no
external flow files), then serialized to diffable SQL text via
`tools/bdb_serialize.py`. Re-running this script must produce a no-op `git diff`
on the committed `*.bdb.sql` files — that reproducibility is the whole point.

Usage:
  python3 test/tests/data/build_fixtures.py          # write all fixtures
  python3 test/tests/data/build_fixtures.py --check   # build to temp, diff only

Tests never call this at runtime; they materialize the committed `.bdb.sql` into
a throwaway temp copy via the `bdb_input` conftest fixture.
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
for _p in (os.path.join(_ROOT, "build"), os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda  # noqa: E402
import bdb_serialize  # noqa: E402


def build_hier_mixed(path: str) -> None:
    """A small hierarchical design mixing uni- and bidirectional nets.

    Mirrors the shape used by test_hier_bidirectional.py: a src → proc(pa,pb,pc)
    → snk lane, with a bidirectional pair (pa↔pb) plus an extra one-way net. Rich
    enough to exercise derive_busterms + the hier bundler, small enough to review.
    """
    db = buda.BDB(path)
    for cell, w, h in [("proc_cell", 420, 200), ("pipe_cell", 110, 80),
                       ("src_cell", 200, 200), ("snk_cell", 200, 200),
                       ("gen_cell", 80, 80), ("rcv_cell", 80, 80)]:
        db.add_cell(cell, w, h)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst_to_cell("proc_cell", "pc_i", "pipe_cell", 290, 60)
    db.add_inst_to_cell("src_cell", "gen_i", "gen_cell", 60, 60)
    db.add_inst_to_cell("snk_cell", "rcv_i", "rcv_cell", 60, 60)
    db.add_inst("src_a", "src_cell", "", 50, 50)
    db.add_inst("proc_a", "proc_cell", "", 350, 50)
    db.add_inst("snk_a", "snk_cell", "", 870, 50)
    for i in range(4):
        db.add_net_pins(f"s2p_{i}", "src_a/gen_i.out", ["proc_a/pa_i.in"])
        db.add_net_pins(f"p2s_{i}", "proc_a/pc_i.out", ["snk_a/rcv_i.in"])
        db.add_net_pins(f"ab_fwd_{i}", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
        db.add_net_pins(f"ab_rev_{i}", "proc_a/pb_i.out", ["proc_a/pa_i.in"])
    db.add_net_pins("ab_extra", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
    buda.BustermGen(db).derive(1)
    db.compute_all()
    # db is a local — closed on return (SQLite connection released on GC).


def build_hier_routed(path: str) -> None:
    """hier_mixed's shape, scaled x8 and MISALIGNED: detailed-routable.

    hier_mixed's aligned geometry leaves detailed NUTS with degenerate point
    intervals (straight routes sit exactly on Hanan lines -> 0 signal tracks ->
    every bit unplaced), so hier detailed-persistence and hier resume could only
    be tested with direct row writes. This twin scales coordinates x8 AND
    offsets the instances vertically (src/snk low, proc high; pb_i raised
    inside proc_cell) so every route bends through real Hanan cells — with the
    mix_tracks patterns it runs run_detailed_nuts cleanly (0 unplaced), giving
    those paths flow-level coverage. Same cells and nets otherwise.
    """
    S = 8
    db = buda.BDB(path)
    for cell, w, h in [("proc_cell", 420, 300), ("pipe_cell", 110, 80),
                       ("src_cell", 200, 200), ("snk_cell", 200, 200),
                       ("gen_cell", 80, 80), ("rcv_cell", 80, 80)]:
        db.add_cell(cell, w * S, h * S)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20 * S, 40 * S)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155 * S, 180 * S)
    db.add_inst_to_cell("proc_cell", "pc_i", "pipe_cell", 290 * S, 40 * S)
    db.add_inst_to_cell("src_cell", "gen_i", "gen_cell", 60 * S, 60 * S)
    db.add_inst_to_cell("snk_cell", "rcv_i", "rcv_cell", 60 * S, 60 * S)
    db.add_inst("src_a", "src_cell", "", 50 * S, 50 * S)
    db.add_inst("proc_a", "proc_cell", "", 350 * S, 250 * S)
    db.add_inst("snk_a", "snk_cell", "", 870 * S, 50 * S)
    for i in range(4):
        db.add_net_pins(f"s2p_{i}", "src_a/gen_i.out", ["proc_a/pa_i.in"])
        db.add_net_pins(f"p2s_{i}", "proc_a/pc_i.out", ["snk_a/rcv_i.in"])
        db.add_net_pins(f"ab_fwd_{i}", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
        db.add_net_pins(f"ab_rev_{i}", "proc_a/pb_i.out", ["proc_a/pa_i.in"])
    db.add_net_pins("ab_extra", "proc_a/pa_i.out", ["proc_a/pb_i.in"])
    buda.BustermGen(db).derive(1)
    db.compute_all()


# name -> builder. Add new fixtures here.
FIXTURES = {
    "hier_mixed": build_hier_mixed,
    "hier_routed": build_hier_routed,
}


def _regenerate(name, builder, out_sql, check):
    with tempfile.TemporaryDirectory() as d:
        bdb_path = os.path.join(d, f"{name}.bdb")
        builder(bdb_path)
        tmp_sql = os.path.join(d, f"{name}.bdb.sql")
        bdb_serialize.dump(bdb_path, tmp_sql)
        with open(tmp_sql, encoding="utf-8") as f:
            new = f.read()
    old = None
    if os.path.exists(out_sql):
        with open(out_sql, encoding="utf-8") as f:
            old = f.read()
    if check:
        status = "OK" if new == old else "DRIFT"
        print(f"[{status}] {name}.bdb.sql")
        return new == old
    with open(out_sql, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print(f"wrote {out_sql} ({'unchanged' if new == old else 'updated'})")
    return True


def main(argv) -> int:
    check = "--check" in argv
    ok = True
    for name, builder in FIXTURES.items():
        out_sql = os.path.join(_HERE, f"{name}.bdb.sql")
        ok = _regenerate(name, builder, out_sql, check) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
