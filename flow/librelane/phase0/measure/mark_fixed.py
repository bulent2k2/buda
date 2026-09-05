#!/usr/bin/env python3
"""Turn the bus nets' `+ ROUTED` wiring into `+ FIXED` in a routed DEF.

    mark_fixed.py guided.def fixed.def [--prefix 'mid[']

Only the named nets' entries change; the rest of the file is byte-identical.
This is the input of measurement B (fixed_test.tcl): a DEF whose bus carries
pre-routes the router is asked to leave alone.

`--strip-others` also REMOVES the other nets' `+ ROUTED` wiring, so the
session that reads the file has exactly one kind of pre-existing wire -- the
FIXED bus -- and re-routes everything else from scratch.  Use it if
`global_route` objects to nets that already carry wiring; it does not change
what B measures.
"""
import argparse, re
from def_wires import net_entries

ap = argparse.ArgumentParser()
ap.add_argument("src"); ap.add_argument("dst")
ap.add_argument("--prefix", default="mid[")
ap.add_argument("--strip-others", action="store_true")
a = ap.parse_args()
text = open(a.src).read()
ents = net_entries(text, a.prefix)
if not ents:
    raise SystemExit(f"no {a.prefix!r} nets with wiring in {a.src}")
n = 0
for name, entry in ents.items():
    new, k = re.subn(r"\+\s*ROUTED\b", "+ FIXED", entry)
    n += k
    text = text.replace(entry, new, 1)
stripped = 0
if a.strip_others:
    for name, entry in net_entries(text, "").items():
        if name in ents:
            continue
        new = re.sub(r"\s*\+\s*ROUTED\b.*?(?=\s*;$)", "", entry, flags=re.S)
        if new != entry:
            stripped += 1
            text = text.replace(entry, new, 1)
open(a.dst, "w").write(text)
print(f"{len(ents)} bus net(s), {n} wiring statement(s) marked FIXED -> {a.dst}"
      + (f"; {stripped} other net(s) stripped of routing" if a.strip_others else ""))
