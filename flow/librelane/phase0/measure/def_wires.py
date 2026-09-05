"""The routed wiring of named nets, out of a DEF `NETS` section (shared).

Returns {net: entry_text} where entry_text is the net's whole `- name ... ;`
entry, and, via `points`, every `( x y )` coordinate the entry carries with
the layer in force at that point.  Deliberately shallow: it does not model
DEF wiring fully (no `NEW`/via/extension semantics), it answers "where is
this net's metal" for a containment check and "did this text change" for a
before/after diff, which is all the two measurements ask.
"""
import re

_ENTRY = re.compile(r"^\s*-\s+(\S+)(.*?);", re.S | re.M)
_LAYER_OR_PT = re.compile(r"\b(met\d|li1|mcon|via\d*)\b|\(\s*(-?\d+)\s+(-?\d+)\s*\)")


def net_entries(def_text, prefix):
    m = re.search(r"^NETS\s+\d+\s*;(.*?)^END NETS", def_text, re.S | re.M)
    if not m:
        raise ValueError("no NETS section")
    out = {}
    for e in _ENTRY.finditer(m.group(1)):
        name = e.group(1)
        if name.startswith(prefix):
            out[name] = e.group(0)
    return out


def points(entry_text):
    layer, pts = None, []
    for m in _LAYER_OR_PT.finditer(entry_text):
        if m.group(1):
            if m.group(1).startswith("met") or m.group(1) == "li1":
                layer = m.group(1)
        else:
            pts.append((int(m.group(2)), int(m.group(3)), layer))
    return pts
