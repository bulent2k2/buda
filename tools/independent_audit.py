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
THE JUDGE — an independent geometric audit of a routed BUDA design
(convergence ladder build item 2, docs/internal/convergence_ladder.md
"The judge must not be BUDA").

If DetailedNUTS routes both arms of an A/B table and `check_design` scores
them, a skeptic says the referee wears our jersey.  This file is the answer:
it reads the PERSISTED BDB tables with `sqlite3` from the standard library
and checks the route from geometry alone.

**It imports no engine.**  Not `buda`, not `buda_db`, not `src/`, not the
other `tools/` modules that do.  That is the whole point and it is enforced
by a test (`test_independent_audit.py::test_the_judge_imports_no_engine`):
a judge that calls the router's own predicates is the router marking its own
homework by a longer route.  Everything below — where a track lies, what a
keepout blocks, what "connected" means — is re-derived here from the stored
numbers, so a defect in the engine's arithmetic shows up as a DISAGREEMENT
rather than being reproduced faithfully by both sides.

What it checks, per the ladder page:

  OFF_GRID     a bit-wire whose track position is not the centre of a SIGNAL
               slot of its layer's effective pattern
  SHORT        two DIFFERENT nets whose metal overlaps on one layer
  KEEPOUT      a bit-wire lying over a keepout that blocks its layer
  OPEN         a net whose metal is not one connected piece, or that does not
               reach an endpoint block it is declared on
  NO_METAL     a net carried by a bundle with no placed metal at all
  LAYER_DIR    a bit-wire running across its layer's declared direction

Usage:

    tools/independent_audit.py <design.bdb|design.bdb.sql> [--json out.json]
    tools/independent_audit.py ckpt.bdb --quiet          # exit code only
    tools/independent_audit.py ckpt.bdb --max-report 20  # examples per kind

Exit status is 0 when the design is clean, 1 when any violation is found and
2 when the file cannot be judged (no routed rows, no track patterns).  A
clean verdict from this file is a claim about GEOMETRY — tracks, overlaps,
blockages, connectivity — and about nothing else: not timing, not a real
detailed router's rule deck, not anything the ladder page's "What is
deliberately not claimed" section disclaims.

LIMITATIONS, stated rather than discovered later:

  * A region override (`add_grid_override`) makes the effective pattern a
    function of position, and this file evaluates it at each wire's MIDPOINT.
    A wire whose span crosses an override boundary is counted and reported as
    a note, not as a violation: whether the engine is entitled to place such
    a wire is a modelling question this file has not settled, and a judge
    must not manufacture violations out of one.
  * Endpoint reach is judged against the endpoint COMPONENT's bbox, not the
    pin rectangle, because that is the contract BUDA routes to: a bundle
    lands on a block face and the block's own internal routing takes it from
    there.  A judge that demanded metal over the pin point would fail every
    correct design here.
  * Reach is required of the OUTERMOST pin-bearing components only.  A
    hierarchy propagates a net's pin to every ancestor between the leaf and
    the common ancestor, so one net names a leaf AND the container holding
    it; the bundle routes to the container face and the container's own
    internal routing carries it to the leaf, which is a level this file is
    not looking at.  Requiring every pin-bearing component was the first
    rule written here and it reported 2048 OPENs on `flow/soc_small.buda`
    where the route is sound — a cluster-level bus landing on `core` and
    `l1d` at their faces, with `core/regf` and `l1d/tag` inside them.  The
    weakening is exactly what the hierarchy licenses and no more: an inner
    component is satisfied only by an ancestor that CARRIES A PIN OF THIS
    NET, never by a container that merely encloses it.
  * An UNPLACED component (the -1,-1,-1,-1 convention) is skipped for reach,
    counted, and named in the summary.
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

# Absolute tolerance for "the same coordinate".  Track positions are doubles
# derived by summing slot widths from an origin, so the error is a few ULP of
# the coordinate magnitude; 1e-6 is far above that and far below any real
# geometry (the finest declared slot width in the tree is 0.07 um).
TOL = 1e-6

KINDS = ("OFF_GRID", "SHORT", "KEEPOUT", "OPEN", "NO_METAL", "LAYER_DIR")


# What "cannot be read" is made of: a corrupt or truncated SQLite file, a
# `.bdb.sql` that is not valid SQL or not valid UTF-8, a slot list that is not
# valid JSON, a path that turns out to be unreadable between the isfile check
# and the open.  Named rather than spelled `except Exception` so a genuine
# defect in this file still crashes loudly instead of being reported as an
# unjudgeable design.
UNREADABLE = (sqlite3.Error, json.JSONDecodeError, OSError, UnicodeDecodeError)


class Unjudgeable(Exception):
    """The file holds nothing this audit can speak about.  Distinct from a
    dirty verdict: exit 2, never 1 — "I cannot judge this" and "this is
    broken" must not share a status, or a harness gating on the judge reads
    a missing route as a clean one."""


# ---------------------------------------------------------------------------
# Reading the design
# ---------------------------------------------------------------------------

def open_design(path):
    """Open a `.bdb` binary or a `.bdb.sql` text dump, read-only.

    The text form is replayed into an in-memory database with `executescript`
    rather than through `tools/bdb_serialize.py`, so this file keeps its one
    import rule (`sqlite3` and nothing of BUDA's).
    """
    if not os.path.isfile(path):
        # NOT SystemExit(str), which exits 1 — the status this file documents
        # as "violations".  A design that cannot be READ is unjudgeable, and
        # automation gating on the contract would otherwise book a typo'd
        # path as a dirty route (Codex P2 on #942).
        raise Unjudgeable(f"no such file: {path}")
    if path.endswith(".sql"):
        con = sqlite3.connect(":memory:")
        with open(path, encoding="utf-8") as f:
            con.executescript(f.read())
    else:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con, name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone()
    return row is not None


def _rows(con, sql, args=()):
    return con.execute(sql, args).fetchall()


class Slot:
    __slots__ = ("type", "label", "width", "space_after")

    def __init__(self, d):
        self.type = d["t"]
        self.label = d["l"]
        self.width = float(d["w"])
        self.space_after = float(d["s"])


class Pattern:
    """A repeating track pattern, re-derived from the stored slot list.

    The tiling rule: the unit starts at `origin` and repeats every
    `unit_pitch` (the sum of each slot's width plus the space after it); a
    slot's TRACK is the centre of its width.  A BOUNDED pattern enumerates
    its tracks instead of stating a rule, so nothing exists outside
    [bound_lo, bound_hi] — a DEF `TRACKS ... DO n STEP s` says there are
    exactly n of them.
    """

    def __init__(self, origin, slots, bounded=False, lo=0.0, hi=0.0):
        self.origin = float(origin)
        self.slots = slots
        self.bounded = bool(bounded)
        self.bound_lo = float(lo)
        self.bound_hi = float(hi)
        self.pitch = sum(s.width + s.space_after for s in slots)

    def signal_centres(self, lo, hi):
        """Every SIGNAL track centre in [lo, hi], ascending."""
        if self.pitch <= 0.0 or not self.slots or lo > hi:
            return []
        if self.bounded:
            lo = max(lo, self.bound_lo)
            hi = min(hi, self.bound_hi)
            if lo > hi:
                return []
        out = []
        n = int(math.floor((lo - self.origin) / self.pitch)) - 1
        while True:
            unit = self.origin + n * self.pitch
            if unit > hi:
                break
            pos = unit
            for s in self.slots:
                centre = pos + s.width / 2.0
                if s.type == "SIGNAL" and lo <= centre <= hi:
                    out.append(centre)
                pos += s.width + s.space_after
            n += 1
        return out

    def is_signal_track(self, pos):
        """Is `pos` the centre of a SIGNAL slot of this pattern?"""
        for c in self.signal_centres(pos - self.pitch, pos + self.pitch):
            if abs(c - pos) <= TOL:
                return True
        return False


class Grid:
    """Per-layer patterns plus the region overrides that shadow them."""

    def __init__(self, con):
        self.patterns = {}      # layer_id -> Pattern
        self.is_horiz = {}      # layer_id -> bool
        self.overrides = defaultdict(list)   # layer_id -> [(x1,y1,x2,y2,Pattern)]
        if _table_exists(con, "track_pattern"):
            for r in _rows(con, "SELECT * FROM track_pattern"):
                slots = [Slot(d) for d in json.loads(r["slots"] or "[]")]
                self.patterns[int(r["layer_id"])] = Pattern(
                    r["origin"], slots, r["bounded"],
                    r["bound_lo"], r["bound_hi"])
                self.is_horiz[int(r["layer_id"])] = bool(r["is_horiz"])
        if _table_exists(con, "grid_override"):
            # Stored order is insertion order, which is the order the engine
            # resolves them in (first match wins).
            for r in _rows(con, "SELECT rowid, * FROM grid_override ORDER BY rowid"):
                slots = [Slot(d) for d in json.loads(r["slots"] or "[]")]
                self.overrides[int(r["layer_id"])].append(
                    (r["x1"], r["y1"], r["x2"], r["y2"],
                     Pattern(r["origin"], slots)))

    def at(self, layer, x, y):
        for x1, y1, x2, y2, pat in self.overrides.get(layer, ()):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return pat
        return self.patterns.get(layer)

    def pattern_varies_along(self, layer, x1, y1, x2, y2):
        """Does the effective pattern CHANGE along this wire?

        Asked of the pattern, not of the rectangles: a wire lying wholly
        inside one override intersects that override, and counting
        intersections reported it as crossing a boundary it never reaches
        (Codex P3 on #942).  What the note claims is that the wire is judged
        at a point where the pattern in force is not the pattern in force
        everywhere along it, so the honest test is whether the two ends and
        the midpoint resolve to the SAME pattern.

        Three samples, not a sweep: this feeds a NOTE, and a wire that
        re-enters its starting region between the samples is a shape no
        `add_grid_override` in this tree produces.  Under-reporting a note
        is the safe direction; a VERDICT would deserve the sweep.
        """
        if not self.overrides.get(layer):
            return False
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        a = self.at(layer, x1, y1)
        return (self.at(layer, x2, y2) is not a
                or self.at(layer, mx, my) is not a)


class Wire:
    """One placed bit-wire, as geometry."""
    __slots__ = ("bundle", "seg", "bit", "net", "layer", "horiz",
                 "x1", "y1", "x2", "y2", "pos", "width")

    def __init__(self, r, net_name):
        self.bundle = r["bundle_id"]
        self.seg = r["seg_idx"]
        self.bit = r["bit_index"]
        self.net = net_name
        self.layer = int(r["layer"])
        self.horiz = bool(r["is_horiz"])
        # Spans are stored AS-IS and may be reversed (the engine's endpoint
        # snap); every consumer takes min/max and so does this one.
        self.x1, self.x2 = sorted((float(r["x1"]), float(r["x2"])))
        self.y1, self.y2 = sorted((float(r["y1"]), float(r["y2"])))
        self.pos = float(r["track_position"])
        self.width = float(r["width"])

    @property
    def key(self):
        return (self.bundle, self.seg, self.bit)

    def along(self):
        return (self.x1, self.x2) if self.horiz else (self.y1, self.y2)

    def perp(self):
        return (self.y1, self.y2) if self.horiz else (self.x1, self.x2)

    def mid(self):
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def label(self):
        return (f"{self.net or '?'} (bundle {self.bundle} seg {self.seg} "
                f"bit {self.bit}) M{self.layer}")


def read_wires(con):
    """Every placed bit-wire with its net NAME resolved."""
    if not _table_exists(con, "net_segment"):
        return []
    names = {}
    if _table_exists(con, "net"):
        names = {int(r["id"]): r["name"] for r in _rows(con, "SELECT id, name FROM net")}
    return [Wire(r, names.get(r["net_id"], ""))
            for r in _rows(con, "SELECT * FROM net_segment")]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _overlap(a_lo, a_hi, b_lo, b_hi, tol=TOL):
    """Length of the intersection of two closed intervals, above `tol`.

    Positive only for a real overlap: two wires that ABUT share a boundary
    and no area, which is a legal T-junction, not a short.
    """
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    return hi - lo if hi - lo > tol else 0.0


def _touch(a_lo, a_hi, b_lo, b_hi, tol=TOL):
    """Do two closed intervals meet at all (abutment included)?"""
    return min(a_hi, b_hi) - max(a_lo, b_lo) >= -tol


def _bucketed(wires, key_lo, key_hi, size):
    """Index wires into uniform bins over one axis."""
    bins = defaultdict(list)
    if size <= 0:
        size = 1.0
    for w in wires:
        lo, hi = key_lo(w), key_hi(w)
        b0, b1 = int(math.floor(lo / size)), int(math.floor(hi / size))
        for b in range(b0, b1 + 1):
            bins[b].append(w)
    return bins, size


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def check_on_grid(wires, grid):
    """Every bit lies on a SIGNAL slot of its layer's effective pattern.

    `audit` refuses a design whose routed layers are not all patterned, so
    an unpatterned layer cannot reach here; a wire on one is reported rather
    than skipped, because a checker that silently passes what it cannot
    evaluate is the failure this file exists to avoid.
    """
    bad, crossing = [], 0
    for w in wires:
        pat = grid.at(w.layer, *w.mid())
        if pat is None:
            bad.append((w, f"M{w.layer} has no track pattern, so this wire "
                           f"lies on no track this file can name"))
            continue
        if grid.pattern_varies_along(w.layer, w.x1, w.y1, w.x2, w.y2):
            crossing += 1
        if not pat.is_signal_track(w.pos):
            bad.append((w, f"track {w.pos:g} is not a SIGNAL slot centre of "
                           f"the M{w.layer} pattern in force there"))
    return bad, crossing


def check_layer_dir(wires, grid):
    """A wire must run along its layer's declared direction."""
    bad = []
    for w in wires:
        declared = grid.is_horiz.get(w.layer)
        if declared is None:
            continue
        if bool(declared) != w.horiz:
            ran = "horizontally" if w.horiz else "vertically"
            want = "horizontal" if declared else "vertical"
            bad.append((w, f"runs {ran} on a {want} layer"))
    return bad


def check_shorts(wires, max_report):
    """No two DIFFERENT nets may overlap on one layer.

    Same-net overlap is not a short — a bit's stub and its trunk meet, and a
    fan-in tree's branches share metal by construction.
    """
    bad, n = [], 0
    by_layer = defaultdict(list)
    for w in wires:
        by_layer[w.layer].append(w)
    for layer, ws in by_layer.items():
        # ONE axis for the whole layer, and the STORED rectangles — never
        # each wire's own `along`/`perp`, which are a function of its
        # `is_horiz`.  A wire whose orientation is wrong (the LAYER_DIR
        # case) would otherwise be bucketed on a different physical axis
        # from its neighbours and compared x-against-y, so a checkpoint
        # could report LAYER_DIR and hide the SHORT in the same metal
        # (Codex P2 on #942).  Overlap is a property of two rectangles;
        # what each wire CLAIMS about its direction does not enter into it.
        n_horiz = sum(1 for w in ws if w.horiz)
        by_y = n_horiz >= len(ws) - n_horiz     # bin on the majority's thin axis
        b_lo = (lambda w: w.y1) if by_y else (lambda w: w.x1)
        b_hi = (lambda w: w.y2) if by_y else (lambda w: w.x2)
        s_lo = (lambda w: w.x1) if by_y else (lambda w: w.y1)
        s_hi = (lambda w: w.x2) if by_y else (lambda w: w.y2)
        size = max((w.width for w in ws), default=1.0)
        bins, size = _bucketed(ws, b_lo, b_hi, size)
        seen = set()
        for group in bins.values():
            group.sort(key=s_lo)
            active = []
            for w in group:
                active = [o for o in active if s_hi(o) > s_lo(w) - TOL]
                for o in active:
                    if o.net == w.net:
                        continue
                    pair = (o.key, w.key) if o.key < w.key else (w.key, o.key)
                    if pair in seen:
                        continue
                    if (_overlap(w.x1, w.x2, o.x1, o.x2)
                            and _overlap(w.y1, w.y2, o.y1, o.y2)):
                        seen.add(pair)
                        n += 1
                        if len(bad) < max_report:
                            bad.append((w, f"overlaps {o.label()} — two "
                                           f"different nets on M{layer}"))
                active.append(w)
    return bad, n


def read_layer_stack(con):
    """{layer id -> {"name", "horiz", "top"}}, or None when the checkpoint
    does not record it.

    Which layers are TOP is not decoration: it decides whether a solid cell
    footprint blocks a wire (`read_leaf_keepouts`).  `track_pattern` says
    which way a layer runs and nothing says whether it is TOP, so this is a
    meta row the session writes beside the route snapshot; a checkpoint from
    before that is judged WITHOUT the implicit keepouts, and says so rather
    than passing them over in silence.
    """
    if not _table_exists(con, "meta"):
        return None
    row = con.execute("SELECT value FROM meta WHERE key='layer_stack'").fetchone()
    if not row or not row[0]:
        return None
    out = {}
    for d in json.loads(row[0]):
        out[int(d["id"])] = {"name": d.get("name", ""),
                             "horiz": bool(d.get("horiz")),
                             "top": bool(d.get("top"))}
    return out or None


def read_keepouts(con):
    """(layer -> [(x1, y1, x2, y2, who, kind)]).

    An EMPTY layer list governs no layer — that is what the restore does with
    it, so a zone declared with no layers blocks nothing here either.
    """
    out = defaultdict(list)
    if not _table_exists(con, "keepout"):
        return out
    for r in _rows(con, "SELECT * FROM keepout"):
        for t in str(r["layers"]).split(","):
            if t.strip():
                out[int(t)].append((float(r["x1"]), float(r["y1"]),
                                    float(r["x2"]), float(r["y2"]),
                                    r["net"] or "", "zone"))
    return out


def read_leaf_keepouts(con, layers):
    """The keepouts nobody declared: every solid LEAF cell's footprint, on
    every non-TOP layer.

    This is not the judge inventing a rule — it is the rule the engine
    enforces everywhere.  `Floorplan::low_layer_keepouts` hands the planner,
    NUTS, DetailedNUTS and `verify.cpp` the declared zones PLUS one zone per
    non-container leaf block on the non-TOP layers, and a reader that saw
    only the `keepout` table could therefore call a LOW-layer wire over a
    cell clean where `check_design` says KEEPOUT_CROSS (Codex P1 on #942).

    A container is transparent by design (its content is the obstacle, not
    its box), an UNPLACED component has no footprint, and a MULTI-RECT cell
    is skipped rather than approximated: its rects tile its bbox exactly, so
    using the bbox would claim the notches too and accuse a wire that routes
    through a gap the design left open.  Both are counted and reported.  A
    boundary PORT is skipped too — it is a terminal a wire is supposed to
    reach, so blocking it would accuse the very metal that lands on it.

    Applied over the whole design, which is STRICTER than the engine: the
    engine enforces it per routing frame, where a container is transparent
    and a deep leaf inside it is not in the frame at all, so a top-level LOW
    wire over a distant cell is permitted there.  That is the right way round
    for a judge — the metal is physically over a cell either way — and it is
    measured silent: `flow/soc_small.buda` (5,680 LOW wires over 219 leaves),
    `flow/soc_mid.buda` (21,456 over 843) and a bottom-up `soc.tcl 2` round
    (536 over 63) report ZERO, while the same probe on the TOP layers finds
    1,461 crossings on soc_small alone — over-the-cell routing, legal, and
    the control that says the test can see anything at all.
    """
    out, notes = defaultdict(list), {"multirect": 0, "unplaced": 0}
    if layers is None or not _table_exists(con, "component"):
        return out, notes
    low = sorted(lid for lid, l in layers.items() if not l["top"])
    if not low:
        return out, notes
    multirect = set()
    if _table_exists(con, "cell_rect"):
        multirect = {r[0] for r in
                     con.execute("SELECT DISTINCT cell FROM cell_rect")}
    for r in _rows(con, "SELECT name,cell,x1,y1,x2,y2 FROM component"
                        " WHERE is_leaf=1 AND is_port=0"):
        # Degeneracy is the test, not a sign check: the no-placement
        # convention is `-1,-1,-1,-1`, which is degenerate, while a design
        # legitimately placed across the origin is not this audit's business
        # to refuse.
        if r["x1"] is None or r["x2"] <= r["x1"] or r["y2"] <= r["y1"]:
            notes["unplaced"] += 1
            continue
        if r["cell"] in multirect:
            notes["multirect"] += 1
            continue
        z = (float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]),
             r["name"], "leaf")
        for lid in low:
            out[lid].append(z)
    return out, notes


def check_keepouts(wires, zones, max_report):
    """No bit may lie over a keepout that blocks its layer."""
    bad, n = [], 0
    by_layer = defaultdict(list)
    for w in wires:
        by_layer[w.layer].append(w)
    for layer, ws in by_layer.items():
        zs = zones.get(layer)
        if not zs:
            continue
        size = max((z[2] - z[0] for z in zs), default=1.0) or 1.0
        zbins = defaultdict(list)
        for z in zs:
            for b in range(int(math.floor(z[0] / size)),
                           int(math.floor(z[2] / size)) + 1):
                zbins[b].append(z)
        for w in ws:
            hit = None
            for b in range(int(math.floor(w.x1 / size)),
                           int(math.floor(w.x2 / size)) + 1):
                for z in zbins.get(b, ()):
                    if (_overlap(w.x1, w.x2, z[0], z[2])
                            and _overlap(w.y1, w.y2, z[1], z[3])):
                        hit = z
                        break
                if hit:
                    break
            if hit:
                n += 1
                if len(bad) < max_report:
                    where = (f"({hit[0]:g},{hit[1]:g})-"
                             f"({hit[2]:g},{hit[3]:g})")
                    if hit[5] == "leaf":
                        bad.append((w, f"lies over the leaf cell {hit[4]} "
                                       f"at {where} — M{layer} is not a TOP "
                                       f"layer, so the cell's own footprint "
                                       f"blocks it"))
                    else:
                        who = f" ({hit[4]})" if hit[4] else ""
                        bad.append((w, f"lies over a keepout on M{layer} at "
                                       f"{where}{who}"))
    return bad, n


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def check_connectivity(con, wires, max_report):
    """Every net's metal is ONE piece and reaches every endpoint block.

    Two wires of one net are joined when a via joins them or when their metal
    touches (abutment included — a T-junction is contact, not overlap).
    Reach is judged against the endpoint COMPONENT's bbox: that is the
    contract BUDA routes to (see the module docstring).
    """
    bad, n_open, n_no_metal, unplaced = [], 0, 0, 0
    by_net = defaultdict(list)
    for w in wires:
        by_net[w.net].append(w)

    # Which nets are supposed to carry metal: the ones a bundle holds.  A
    # net no bundle carries is out of scope — `flow/tpu`'s 256 edge-of-array
    # ports are the design, not an open — and saying so is the difference
    # between a judge and a noise generator.
    in_scope, names, scoped = set(), {}, True
    if _table_exists(con, "net"):
        names = {int(r["id"]): r["name"] for r in _rows(con, "SELECT id, name FROM net")}
    if _table_exists(con, "bundle_net"):
        for r in _rows(con, "SELECT DISTINCT net_id FROM bundle_net"):
            nm = names.get(r["net_id"])
            if nm:
                in_scope.add(nm)
    if not in_scope:
        # No membership rows at all (a flow that never mirrored its nets into
        # the BDB).  Fall back to the nets that HAVE metal, so shorts and
        # broken metal are still judged, and say what the fallback costs: a
        # net with no metal cannot be distinguished from a net that was never
        # meant to have any, so NO_METAL is not judged here.
        in_scope = set(by_net) - {""}
        scoped = False

    vias = defaultdict(list)
    if _table_exists(con, "net_via"):
        for r in _rows(con, "SELECT * FROM net_via"):
            # A NEGATIVE to_seg is an NDR shield BOND strap: its far end is a
            # power-grid rail, not a routed segment, so it joins nothing here.
            if r["to_seg"] is not None and int(r["to_seg"]) < 0:
                continue
            vias[names.get(r["net_id"], "")].append(
                (r["bundle_id"], int(r["from_seg"]), int(r["to_seg"]),
                 int(r["bit_index"]), float(r["x"]), float(r["y"])))

    # Endpoint blocks per net, from the pin table — reduced to the OUTERMOST
    # pin-bearing components, which is the level the bundle actually routes
    # to (see the module docstring).
    endpoints = defaultdict(list)
    if _table_exists(con, "pin") and _table_exists(con, "component"):
        parent = {int(r["id"]): r["parent_id"] for r in
                  _rows(con, "SELECT id, parent_id FROM component")}
        held = defaultdict(list)
        for r in _rows(con, """SELECT p.net_id, c.id, c.name, c.x1, c.y1,
                                      c.x2, c.y2
                               FROM pin p JOIN component c ON c.id = p.comp_id"""):
            nm = names.get(r["net_id"])
            if nm:
                held[nm].append((int(r["id"]), r["name"], r["x1"], r["y1"],
                                 r["x2"], r["y2"]))
        for nm, comps in held.items():
            ids = {c[0] for c in comps}
            for cid, name, x1, y1, x2, y2 in comps:
                anc, inner = parent.get(cid), False
                while anc is not None:
                    if int(anc) in ids:
                        inner = True
                        break
                    anc = parent.get(int(anc))
                if not inner:
                    endpoints[nm].append((name, x1, y1, x2, y2))

    for net in sorted(in_scope):
        ws = by_net.get(net, [])
        if not ws:
            n_no_metal += 1
            if len(bad) < max_report:
                bad.append((None, f"NO_METAL: net {net} is carried by a "
                                  f"bundle and has no placed metal"))
            continue
        uf = _UF()
        at = {}
        for w in ws:
            uf.find(w.key)
            at[w.key] = w
        for bundle, fseg, tseg, bit, vx, vy in vias.get(net, ()):
            a, b = (bundle, fseg, bit), (bundle, tseg, bit)
            wa, wb = at.get(a), at.get(b)
            # A via joins two wires only where it LANDS ON BOTH.  Taking the
            # row's word for it would let a via placed off its own wires
            # report a net connected that physically is not — the row says
            # two segments are joined, and whether they are is geometry.
            if wa is None or wb is None:
                continue
            if not all(w.x1 - TOL <= vx <= w.x2 + TOL
                       and w.y1 - TOL <= vy <= w.y2 + TOL for w in (wa, wb)):
                continue
            uf.union(a, b)
        # Metal contact, bucketed per layer like the short check.
        by_layer = defaultdict(list)
        for w in ws:
            by_layer[w.layer].append(w)
        for group in by_layer.values():
            group.sort(key=lambda w: (w.x1, w.y1))
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if b.x1 > a.x2 + TOL and b.y1 > a.y2 + TOL:
                        continue
                    if (_touch(a.x1, a.x2, b.x1, b.x2)
                            and _touch(a.y1, a.y2, b.y1, b.y2)):
                        uf.union(a.key, b.key)
        roots = {uf.find(w.key) for w in ws}
        if len(roots) > 1:
            n_open += 1
            if len(bad) < max_report:
                bad.append((ws[0], f"OPEN: net {net}'s metal is {len(roots)} "
                                   f"disconnected pieces ({len(ws)} wires)"))
            continue
        for name, x1, y1, x2, y2 in endpoints.get(net, ()):
            if x1 is None or (x1 < 0 and y1 < 0 and x2 < 0 and y2 < 0):
                unplaced += 1
                continue
            if not any(_touch(w.x1, w.x2, x1, x2) and _touch(w.y1, w.y2, y1, y2)
                       for w in ws):
                n_open += 1
                if len(bad) < max_report:
                    bad.append((ws[0], f"OPEN: net {net} does not reach its "
                                       f"endpoint block {name}"))
                break
    return bad, n_open, n_no_metal, unplaced, scoped


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def audit(path, max_report=8):
    """Judge one design.  Returns a result dict; raises `Unjudgeable` when
    the file holds nothing to judge."""
    con = open_design(path)
    grid = Grid(con)
    wires = read_wires(con)
    if not wires:
        raise Unjudgeable(f"{path} holds no placed bit-wires "
                          f"(net_segment is empty)")
    if not grid.patterns:
        raise Unjudgeable(f"{path} declares no track pattern — every "
                          f"on-grid question would be vacuous")
    # ...and the same is true ONE LAYER AT A TIME.  A checkpoint carrying a
    # pattern for some layers and none for a layer that holds wires would
    # otherwise pass the guard above, leave OFF_GRID entirely unevaluated
    # for those wires, and — if nothing else fired — exit 0, so a converge
    # table would read `clean` for metal nothing judged (Codex P1 on #942).
    # Partial coverage is exactly the case exit 2 exists for.
    blind = sorted({w.layer for w in wires} - set(grid.patterns))
    if blind:
        raise Unjudgeable(
            f"{path} carries metal on "
            f"{', '.join('M%d' % l for l in blind)} with no track pattern "
            f"there — OFF_GRID cannot be judged for those wires, and a "
            f"partial verdict must not read as a clean one")

    off_grid, crossing = check_on_grid(wires, grid)
    dirs = check_layer_dir(wires, grid)
    shorts, n_short = check_shorts(wires, max_report)
    layers = read_layer_stack(con)
    zones = read_keepouts(con)
    leaf_zones, leaf_notes = read_leaf_keepouts(con, layers)
    for lid, zs in leaf_zones.items():
        zones[lid].extend(zs)
    ko, n_ko = check_keepouts(wires, zones, max_report)
    conn, n_open, n_no_metal, unplaced, scoped = check_connectivity(
        con, wires, max_report)

    counts = {
        "OFF_GRID": len(off_grid),
        "SHORT": n_short,
        "KEEPOUT": n_ko,
        "OPEN": n_open,
        "NO_METAL": n_no_metal,
        "LAYER_DIR": len(dirs),
    }
    examples = []
    for kind, rows in (("OFF_GRID", off_grid), ("LAYER_DIR", dirs),
                       ("SHORT", shorts), ("KEEPOUT", ko)):
        for w, why in rows[:max_report]:
            examples.append({"kind": kind, "where": w.label(), "why": why})
    for w, why in conn[:max_report]:
        kind = why.split(":", 1)[0]
        examples.append({"kind": kind,
                         "where": w.label() if w else "",
                         "why": why.split(": ", 1)[-1]})

    return {
        "design": os.path.abspath(path),
        "wires": len(wires),
        "nets": len({w.net for w in wires}),
        "layers": sorted({w.layer for w in wires}),
        "counts": counts,
        "total": sum(counts.values()),
        "clean": sum(counts.values()) == 0,
        "notes": {
            "wires_crossing_a_region_override": crossing,
            "endpoint_blocks_unplaced": unplaced,
            "bundle_membership_known": scoped,
            "layer_types_known": layers is not None,
            "leaf_cells_blocking": sum(len(z) for z in leaf_zones.values()),
            "leaf_cells_multirect": leaf_notes["multirect"],
            "leaf_cells_unplaced": leaf_notes["unplaced"],
        },
        "examples": examples,
    }


def report(res, quiet=False):
    if quiet:
        return
    print(f"[judge] {res['design']}")
    print(f"[judge] {res['wires']} bit-wire(s), {res['nets']} net(s), "
          f"layers {','.join('M%d' % l for l in res['layers'])}")
    for kind in KINDS:
        n = res["counts"][kind]
        if n:
            print(f"[judge]   {kind:<9} {n}")
    for e in res["examples"]:
        print(f"[judge]     {e['kind']}: {e['where']}: {e['why']}")
    notes = res["notes"]
    if notes["wires_crossing_a_region_override"]:
        print(f"[judge] note: {notes['wires_crossing_a_region_override']} wire(s) "
              f"span a region-override boundary — judged at their midpoint")
    if notes["endpoint_blocks_unplaced"]:
        print(f"[judge] note: {notes['endpoint_blocks_unplaced']} endpoint "
              f"block(s) are unplaced — reach not judged for those")
    if not notes["layer_types_known"]:
        print("[judge] note: this checkpoint does not record which layers "
              "are TOP, so the implicit keepouts — every solid leaf cell's "
              "footprint on the non-TOP layers — were NOT judged; KEEPOUT "
              "covers the declared zones alone")
    if notes["leaf_cells_multirect"]:
        print(f"[judge] note: {notes['leaf_cells_multirect']} leaf "
              f"component(s) have a multi-rect footprint — not judged as "
              f"implicit keepouts, since their bbox includes notches the "
              f"design leaves routable")
    if not notes["bundle_membership_known"]:
        print("[judge] note: this design records no bundle membership "
              "(`bundle_net` is empty), so a net with NO metal cannot be "
              "told from one that was never routed — NO_METAL not judged")
    print(f"[judge] VERDICT: {'CLEAN' if res['clean'] else 'VIOLATIONS'} "
          f"({res['total']})")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Independent geometric audit of a routed BUDA design "
                    "(imports no engine).")
    ap.add_argument("design", help="a .bdb or .bdb.sql holding a routed design")
    ap.add_argument("--json", metavar="PATH", help="write the result as JSON")
    ap.add_argument("--max-report", type=int, default=8,
                    help="examples printed per violation kind (default 8)")
    ap.add_argument("--quiet", action="store_true",
                    help="exit status only, no output")
    a = ap.parse_args(argv)

    try:
        res = audit(a.design, max_report=a.max_report)
    except Unjudgeable as e:
        print(f"independent_audit: cannot judge: {e}", file=sys.stderr)
        return 2
    except UNREADABLE as e:
        # A file that cannot be READ is the same verdict as one that holds
        # nothing to judge, and emphatically not the same as a dirty route:
        # an uncaught sqlite3.DatabaseError exits 1 through Python's own
        # traceback, which is the status this file documents as violations,
        # so a harness gating on the contract would book a truncated
        # checkpoint or a half-written `.bdb.sql` as a broken design (Codex
        # P2 on #942 — the missing-path guard above covers only the case
        # where there is no file at all).  Caught around the WHOLE audit
        # rather than at the open, because the reads are lazy: a corrupt
        # page surfaces at whichever query first touches it.
        print(f"independent_audit: cannot judge: {a.design} cannot be read "
              f"({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    report(res, quiet=a.quiet)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, sort_keys=True)
            f.write("\n")
    return 0 if res["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
