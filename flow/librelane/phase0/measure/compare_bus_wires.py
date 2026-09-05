#!/usr/bin/env python3
"""Measurement B verdict: did the routers leave the FIXED bus wiring alone?

    compare_bus_wires.py fixed.def fixed_after.def [--prefix 'mid[']

Compares each bus net's wiring entry before and after, whitespace-normalized.
Identical = the routers honoured FIXED.  Anything else is printed per net,
and is the finding.
"""
import argparse, re
from def_wires import net_entries

ap = argparse.ArgumentParser()
ap.add_argument("before"); ap.add_argument("after")
ap.add_argument("--prefix", default="mid[")
a = ap.parse_args()
norm = lambda s: re.sub(r"\s+", " ", s).strip()
b = net_entries(open(a.before).read(), a.prefix)
c = net_entries(open(a.after).read(), a.prefix)
changed = [n for n in b if norm(b[n]) != norm(c.get(n, ""))]
print(f"{len(b)} bus net(s): {len(b) - len(changed)} unchanged, {len(changed)} changed")
for n in changed[:10]:
    print(f"  CHANGED: {n}" + ("  (missing after)" if n not in c else ""))
raise SystemExit(1 if changed else 0)
