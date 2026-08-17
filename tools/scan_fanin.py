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

"""Scan every flow/demo for fan-in patterns CONVERGENT bundling would merge.

A fan-in merge = nets sharing the SAME receiver-instance set but having
DIFFERENT driver instances — exactly where `run_bundler CONVERGENT` diverges
from STRICT (see docs/internal/convergent_bundling.md, "Corpus scan").

Method: static parse of add_net/add_bus (following `source` includes) for
script-declared netlists; for flows whose nets live in a `*.bdb.sql` fixture
(open_bdb with no add_net/add_bus), the fixture is materialized via
tools/bdb_serialize.py and nets are read from the pin table at LEAF endpoint
level — an approximation of the depth-aware hier signatures, good enough to
locate candidate flows.

Usage (from the repo root):
    PYTHONPATH=build python3 tools/scan_fanin.py
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# The `.buda` line rules, shared with the engine (src/buda_script.py) — the
# scan follows `source`, so it has to resolve a path the way the engine does.
from buda_script import sole_path_arg                        # noqa: E402


def inst_of(pin):
    return pin.split(".")[0] if "." in pin else pin


def parse_script(path, seen=None):
    """(nets, mode_lines) from a .buda script, following `source` includes.
    Each net is (name, driver_inst, frozenset(receiver_insts), nbits)."""
    seen = seen or set()
    if path in seen or not path.exists():
        return [], []
    seen.add(path)
    nets, modes = [], []
    for raw in path.read_text().splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        tok = line.split()
        cmd = tok[0]
        if cmd == "source" and len(tok) > 1:
            sub = sole_path_arg(line)      # rest of the line, spaces included
            sub_nets, sub_modes = parse_script((path.parent / sub).resolve(), seen)
            nets += sub_nets
            modes += sub_modes
        elif cmd == "add_net" and len(tok) >= 4:
            nets.append((tok[1], inst_of(tok[2]),
                         frozenset(inst_of(r) for r in tok[3].split(",")), 1))
        elif cmd == "add_bus" and len(tok) >= 4:
            m = re.match(r"(.+)\[(\d+)(?::(\d+))?\]$", tok[1])
            n = 1
            if m:
                n = (int(m.group(3)) - int(m.group(2)) + 1) if m.group(3) \
                    else int(m.group(2))
            nets.append((tok[1], inst_of(tok[2]),
                         frozenset(inst_of(r) for r in tok[3].split(",")), n))
        elif cmd in ("run_bundler", "run_hier_bundler", "open_bdb"):
            modes.append(line)
    return nets, modes


def bdb_nets(sql_path):
    """Nets from a .bdb.sql fixture at leaf endpoint level, or None on error:
    (name, first_driver_inst, frozenset(receiver_insts), 1) per driven net."""
    import subprocess, tempfile, os
    tmp = tempfile.mktemp(suffix=".bdb")
    r = subprocess.run([sys.executable, str(ROOT / "tools/bdb_serialize.py"),
                        "load", str(sql_path), tmp],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    import buda
    db = buda.BDB(tmp)
    by_net = defaultdict(lambda: ([], []))   # net_id -> (drivers, receivers)
    for c in db.all_components():
        for p in db.pins_by_comp(c.id):
            drv, rcv = by_net[p.net_id]
            (drv if "OUT" in str(p.dir) else rcv).append(c.name)
    nets = [(str(nid), drv[0], frozenset(rcv), 1)
            for nid, (drv, rcv) in by_net.items() if drv and rcv]
    del db
    os.unlink(tmp)
    return nets


def analyze(nets):
    strict = defaultdict(list)
    conv = defaultdict(list)
    for name, drv, rcvs, n in nets:
        strict[(drv, rcvs)].append((name, n))
        conv[rcvs].append((name, drv, n))
    fanin = {}
    for rcvs, members in conv.items():
        drivers = {d for _, d, _ in members}
        if len(drivers) >= 2:
            fanin[rcvs] = (sorted(drivers), members)
    return len(strict), len(conv), fanin


def main():
    paths = sorted((ROOT / "flow").rglob("*.buda")) \
          + sorted((ROOT / "demo").rglob("*.buda"))
    for p in paths:
        nets, modes = parse_script(p.resolve())
        bdb_note = ""
        if not nets:
            for m in modes:
                if m.startswith("open_bdb"):
                    sql = (p.parent / m.split()[1]).resolve()
                    if sql.suffix == ".sql" and sql.exists():
                        bn = bdb_nets(sql)
                        if bn:
                            nets = bn
                            bdb_note = f" [nets from {sql.name}, leaf-level]"
                    break
        if not nets:
            continue
        n_strict, n_conv, fanin = analyze(nets)
        if not fanin:
            continue
        rel = p.relative_to(ROOT)
        mode = "; ".join(m for m in modes if "bundler" in m) or "(no bundler cmd)"
        print(f"\n=== {rel}{bdb_note}")
        print(f"    mode: {mode}")
        print(f"    nets/buses: {len(nets)}  strict-groups: {n_strict}  "
              f"convergent-groups: {n_conv}  FAN-IN merges: {len(fanin)}")
        for rcvs, (drivers, members) in sorted(
                fanin.items(), key=lambda kv: -len(kv[1][0]))[:4]:
            bits = sum(n for _, _, n in members)
            print(f"      -> {{{','.join(sorted(rcvs)[:3])}"
                  f"{'…' if len(rcvs) > 3 else ''}}} <= {len(drivers)} drivers "
                  f"{drivers[:5]}{'…' if len(drivers) > 5 else ''} "
                  f"({len(members)} buses, {bits} bits)")


if __name__ == "__main__":
    main()
