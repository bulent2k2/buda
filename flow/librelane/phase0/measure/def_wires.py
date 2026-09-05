r"""The routed wiring of named nets, out of a DEF `NETS` section (shared).

`net_entries(text, prefix)` -> {net: entry_text}: each net's whole
`- name ... ;` entry, keyed by the net's NAME rather than its DEF spelling:
DEF escapes a name's special characters with a backslash, and OpenROAD
writes every bus bit that way (`mid\[0\]` in a routed DEF -- measured
2026-09-05, where a prefix match on `mid[` found 0 of the 32 bus nets), while
its guide writer prints the same net as `mid[0]`.  The key is what the two
files share; the entry text stays byte-for-byte as written, so a caller
that rewrites the file can still find it.  `paths(entry_text)` -> the entry's wiring as a list
of (layer, [(x, y), ...]) -- one item per DEF path, i.e. per `+ ROUTED` /
`+ FIXED` / `+ COVER` statement and per `NEW` inside it, so a segment is
only ever drawn between consecutive points of ONE path.  A `*` coordinate
repeats the previous point's (DEF's "unchanged" shorthand), a trailing
extension value is accepted, and a via name after a point is skipped.

Deliberately shallow otherwise: no wire widths, no via geometry, no
`SHAPE`/`TAPER`.  A `RECT ( dx1 dy1 dx2 dy2 )` -- the patch metal a
detailed router adds at a via, given RELATIVE to the previous point -- is
not a point on the path (read as one it put the wire at (-0.39, -0.15) um
and reported it outside every guide, measured 2026-09-05 on the first real
routed DEF); it IS metal, so `patches(entry_text)` returns each one as an
absolute (layer, x1, y1, x2, y2) for the containment check to cover too
(Codex #877).  It answers "where is this net's metal, layer by layer" for
a containment check and "did this text change" for a before/after diff,
which is what the two measurements ask.  A malformed point raises with the
offending text rather than dropping it: a point the reader skipped would
make a wire look shorter than it is, which is the failure mode the
containment check exists to catch.
"""
import re

_ENTRY = re.compile(r"^\s*-\s+(\S+)(.*?);", re.S | re.M)
_WIRING = re.compile(r"\+\s*(ROUTED|FIXED|COVER)\b(.*?)(?=\+\s*(?:ROUTED|FIXED|COVER)\b|$)", re.S)
_TOKEN = re.compile(r"\(\s*([^()]*?)\s*\)|\bNEW\b|(\S+)")
_LAYER = re.compile(r"^(met\d+|li1|M\d+)$")


def net_entries(def_text, prefix):
    m = re.search(r"^NETS\s+\d+\s*;(.*?)^END NETS", def_text, re.S | re.M)
    if not m:
        raise ValueError("no NETS section")
    out = {}
    for e in _ENTRY.finditer(m.group(1)):
        name = unescape(e.group(1))
        if name.startswith(prefix):
            out[name] = e.group(0)
    return out


def unescape(def_name):
    """The net a DEF spelling names: `mid\\[0\\]` -> `mid[0]`."""
    return def_name.replace("\\", "")


def _point(inner, prev):
    p = inner.split()
    if len(p) < 2:
        raise ValueError(f"malformed DEF point '( {inner} )'")
    if (p[0] == "*" or p[1] == "*") and prev is None:
        raise ValueError(f"'*' with no previous point in '( {inner} )'")
    x = prev[0] if p[0] == "*" else int(p[0])
    y = prev[1] if p[1] == "*" else int(p[1])
    return (x, y)


def paths(entry_text, patch_out=None):
    out = []
    patch_out = [] if patch_out is None else patch_out
    for w in _WIRING.finditer(entry_text):
        body = w.group(2)
        layer, pts, prev = None, [], None
        expect_layer = True
        rect = False
        for t in _TOKEN.finditer(body):
            if t.group(0) == "NEW":
                if pts:
                    out.append((layer, pts))
                pts, prev, expect_layer = [], None, True
            elif t.group(1) is not None:
                if rect:                  # RECT ( dx1 dy1 dx2 dy2 ): no point
                    rect = False
                    if prev is None:
                        raise ValueError(f"RECT with no previous point in '{t.group(0)}'")
                    d = [int(v) for v in t.group(1).split()]
                    if len(d) != 4:
                        raise ValueError(f"malformed DEF RECT '{t.group(0)}'")
                    patch_out.append((layer, prev[0] + min(d[0], d[2]), prev[1] + min(d[1], d[3]),
                                      prev[0] + max(d[0], d[2]), prev[1] + max(d[1], d[3])))
                    continue
                pt = _point(t.group(1), prev)
                pts.append(pt)
                prev = pt
            else:
                word = t.group(2)
                if expect_layer and _LAYER.match(word):
                    layer = word
                    expect_layer = False
                elif word == "RECT":
                    rect = True
                # anything else after the layer (a via name, TAPER, a width
                # number, a mask) carries no geometry this reader models
        if pts:
            out.append((layer, pts))
    return out


def patches(entry_text):
    """Every RECT patch as an absolute (layer, x1, y1, x2, y2)."""
    out = []
    paths(entry_text, out)
    return out


def points(entry_text):
    """Every point with its layer, flattened -- for callers that only count."""
    return [(x, y, layer) for layer, pts in paths(entry_text) for x, y in pts]
