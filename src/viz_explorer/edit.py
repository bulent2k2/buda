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

"""TopologyExplorer mixin — TopoEdit session ops (open/commit, trunks, stubs, pairs, slide windows).

Split out of buda_viz.py (see viz_explorer/__init__.py); methods run on
the composed class and share its state via self."""
import json
import math
import os
import re
import sys
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.widgets import Button

import buda as ic
from ui_state import ViewState              # noqa: F401
from viz_common import *                    # noqa: F401,F403
import viz_window

class ExplorerEditMixin:

    # ── TopoEdit mode (Phase E3b) ─────────────────────────────────────
    def _edit_default_layers(self):
        h = v_ = -1
        if self.layer_stack is not None:
            h  = self.layer_stack.get_top_layer(ic.LayerDir.HORIZONTAL)
            v_ = self.layer_stack.get_top_layer(ic.LayerDir.VERTICAL)
        return (h if h != -1 else 4), (v_ if v_ != -1 else 5)


    def _edit_open(self, empty):
        was_open = self._edit_topo is not None
        if was_open:
            self._edit_msg = "edit session already open — enter commits, esc aborts"
        elif empty:
            self._edit_topo = ic.Topology()
            self._edit_topo.type = "USER"
            self._edit_msg = "EDIT: empty topology — T/Y add a trunk at the cursor"
        else:
            if not (0 <= self.idx < len(self.topos)):
                return
            # candidates[] elements alias pool storage; deep-copy explicitly.
            self._edit_topo = ic.offset_topology(self.topos[self.idx], 0, 0)
            self._edit_msg = f"EDIT: copy of topo {self.idx + 1} ({self._edit_topo.type})"
        if not was_open and self._edit_topo is not None:
            self._edit_slide = {}          # fresh session: no staged windows
            self._edit_slide_mark = None
        self._edit_pending = -1
        self._draw()


    def _edit_close(self, msg):
        self._edit_topo    = None
        self._edit_pending = -1
        self._edit_slide   = {}
        self._edit_slide_mark = None
        self._trunk_mode   = None
        self._trunk_hover  = None
        self._trunk_pin_seg = -1
        self._trunk_pin_set = None
        self._edit_msg     = msg
        self._draw()


    def _edit_apply(self, verdict):
        """Render one op's verdict into the edit banner (and the console).
        Returns verdict.applied so callers can chain bookkeeping (e.g. the
        staged-slide-window remap after a segment removal)."""
        if not verdict.applied:
            self._edit_msg = f"EDIT rejected: {verdict.note}"
        else:
            kinds = {}
            for viol in verdict.conn.violations:
                k = str(viol.kind).split('.')[-1]
                kinds[k] = kinds.get(k, 0) + 1
            issues = ", ".join(f"{k}x{n}" for k, n in sorted(kinds.items())) or "none"
            state = "clean" if verdict.ok() else \
                f"violations: {issues}; comps={verdict.components}" \
                + ("; PINCHED" if verdict.pinched else "")
            self._edit_msg = f"EDIT: {verdict.note} — {state}"
        print(f"[edit] {self._edit_msg}")
        self._draw()
        return verdict.applied


    @staticmethod
    def _fmt_perp(v):
        return ("-inf" if v < -_UNCONSTRAINED
                else "inf" if v > _UNCONSTRAINED else f"{v:.0f}")


    def _edit_slide_at(self, event):
        """Two-step slide-window refine ('W'): with a segment selected, the
        first press marks the cursor's PERPENDICULAR coordinate as one bound,
        the second applies [min, max] of the two marks — intersected with the
        segment's structural slide range (a window outside it would make the
        NUTS placement infeasible) — as a staged override that lands on
        plan.seg_slide_lo/hi at commit.  'w' clears the selected segment's
        staged window."""
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select a segment first (j/k)"
            self._draw(); return
        if event.xdata is None or event.ydata is None:
            self._edit_msg = "EDIT: put the cursor on the canvas first"
            self._draw(); return
        cs = list(self._build_conn_topo(self._edit_topo).segs())[self.sidx]
        coord = float(event.ydata if cs.horiz else event.xdata)
        if (self._edit_slide_mark is None
                or self._edit_slide_mark[0] != self.sidx):
            self._edit_slide_mark = (self.sidx, coord)
            self._edit_msg = (f"EDIT: seg {self.sidx} slide bound at "
                              f"{coord:.0f} — press W at the other bound")
            self._draw(); return
        _, c1 = self._edit_slide_mark
        self._edit_slide_mark = None
        lo, hi = min(c1, coord), max(c1, coord)
        s_lo, s_hi = float(cs.perp_lo), float(cs.perp_hi)
        clo, chi = max(lo, s_lo), min(hi, s_hi)
        if clo > chi:
            self._edit_msg = (
                f"EDIT rejected: window [{lo:.0f},{hi:.0f}] is outside seg "
                f"{self.sidx}'s slide range "
                f"[{self._fmt_perp(s_lo)},{self._fmt_perp(s_hi)}]")
            self._draw(); return
        self._edit_slide[self.sidx] = (clo, chi)
        note = "" if (clo, chi) == (lo, hi) else " (clamped to slide range)"
        self._edit_msg = (f"EDIT: seg {self.sidx} slide window "
                          f"[{clo:.0f},{chi:.0f}]{note} — applies on commit")
        print(f"[edit] {self._edit_msg}")
        self._draw()


    def _edit_slide_clear(self):
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select a segment first (j/k)"
        elif self._edit_slide.pop(self.sidx, None) is not None:
            self._edit_msg = f"EDIT: seg {self.sidx} slide window cleared"
        else:
            self._edit_msg = f"EDIT: seg {self.sidx} has no slide window"
        self._edit_slide_mark = None
        self._draw()


    @staticmethod
    def _snap(val, coords):
        """Snap a cursor coordinate to the nearest Hanan line (or round)."""
        iv = int(round(val))
        return min(coords, key=lambda c: abs(c - iv)) if coords else iv


    def _block_at(self, x, y):
        for name, r in self.fp.get_all_blocks():
            if r.x1 <= x <= r.x2 and r.y1 <= y <= r.y2:
                return name
        return None


    # ── Two-step trunk placement (T/Y): arm → hover-preview → place ──────────
    def _edit_trunk_key(self, event, horiz):
        """T/Y in a session: arm 'add trunk' mode (first press), place it (same
        key again), or switch orientation (the other key)."""
        if self._trunk_mode is None or self._trunk_mode != horiz:
            self._edit_trunk_begin(horiz, event)
        else:
            self._edit_trunk_place(event)

    def _edit_trunk_begin(self, horiz, event):
        self._trunk_mode = horiz
        self._trunk_hover = None
        if event is not None and event.xdata is not None and event.ydata is not None:
            xs, ys = self._bundle_hanan_grid()
            self._trunk_hover = self._snap(event.ydata if horiz else event.xdata,
                                           ys if horiz else xs)
        self._edit_msg = (
            f"ADD {'H' if horiz else 'V'} TRUNK: hover a grid line "
            f"(cell highlights), click or {'T' if horiz else 'Y'}/enter to "
            f"place, esc to cancel")
        self._draw()

    def _edit_trunk_cancel(self):
        self._trunk_mode = None
        self._trunk_hover = None
        self._edit_msg = "EDIT: trunk cancelled"
        self._draw()

    def _edit_trunk_place(self, event):
        """Commit the armed trunk at the cursor's (or last-hovered) grid line."""
        horiz = self._trunk_mode
        self._trunk_mode = None
        hover = self._trunk_hover
        self._trunk_hover = None
        if event is not None and event.xdata is not None and event.ydata is not None:
            self._edit_add_trunk_at(event, horiz)
        elif hover is not None:
            # Placed by a key with no cursor coords: use the previewed line.
            self._edit_add_trunk_from_perp(horiz, hover)
        else:
            self._edit_msg = "EDIT: hover a grid line first"
            self._draw()

    def _on_trunk_motion(self, event):
        """Preview the armed trunk's target line under the cursor (redraw only
        when the snapped grid line changes, so hover is cheap)."""
        if (self._trunk_mode is None or event.inaxes is not self.ax
                or event.xdata is None or event.ydata is None):
            return
        xs, ys = self._bundle_hanan_grid()
        horiz = self._trunk_mode
        new = self._snap(event.ydata if horiz else event.xdata, ys if horiz else xs)
        if new != self._trunk_hover:
            self._trunk_hover = new
            self._draw()

    def _on_trunk_click(self, event):
        """Left-click drives the click gestures while editing: toggle a busterm
        in 'pin span' mode, else place an armed trunk.  Other buttons (right-drag
        zoom) are left alone."""
        if getattr(event, 'button', None) != 1 or event.inaxes is not self.ax:
            return
        if self._trunk_pin_set is not None:
            if event.xdata is None or event.ydata is None:
                return
            block = self._block_at(event.xdata, event.ydata)
            if block is not None and block in self._bundle_busterm_names():
                self._edit_trunk_pin_toggle(block)
            return
        if self._trunk_mode is not None:
            self._edit_trunk_place(event)

    # ── Pin trunk span to selected busterms (P): click blocks → set span ─────
    def _edit_trunk_pin_begin(self):
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select the trunk first (j/k), then P"
            self._draw(); return
        self._trunk_pin_seg = self.sidx
        self._trunk_pin_set = set()
        self._edit_msg = ("PIN SPAN: click the busterm blocks this trunk should "
                          "span, enter to apply, esc to cancel")
        self._draw()

    def _edit_trunk_pin_toggle(self, block):
        if block in self._trunk_pin_set:
            self._trunk_pin_set.discard(block)
        else:
            self._trunk_pin_set.add(block)
        self._edit_msg = (f"PIN SPAN: {len(self._trunk_pin_set)} busterm(s) — "
                          f"enter to apply, esc to cancel")
        self._draw()

    def _edit_trunk_pin_cancel(self):
        self._trunk_pin_seg = -1
        self._trunk_pin_set = None
        self._edit_msg = "EDIT: pin span cancelled"
        self._draw()

    def _edit_trunk_pin_apply(self):
        seg_idx = self._trunk_pin_seg
        picked = set(self._trunk_pin_set or ())
        self._trunk_pin_seg = -1
        self._trunk_pin_set = None
        if not (0 <= seg_idx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: pin target gone"; self._draw(); return
        if not picked:
            self._edit_msg = "PIN SPAN cancelled: no busterms picked"
            self._draw(); return
        seg = self._edit_topo.segments[seg_idx]
        horiz = (seg.start.y == seg.end.y)
        span = self._along_span_of_blocks(picked, horiz)
        if span is None:
            self._edit_msg = ("PIN SPAN rejected: those busterms give a "
                              "degenerate span (pick two apart on the trunk axis)")
            self._draw(); return
        self._edit_apply(ic.edit_set_span(self._edit_topo, self.fp, seg_idx,
                                          span[0], span[1]))

    def _edit_add_trunk_from_perp(self, horiz, perp):
        lo, hi = self._busterm_along_span(horiz)
        h, v_ = self._edit_default_layers()
        self._edit_apply(ic.edit_add_trunk(
            self._edit_topo, self.fp, horiz, int(perp), lo, hi,
            h if horiz else v_))

    def _edit_add_trunk_from_perp(self, horiz, perp):
        lo, hi = self._busterm_along_span(horiz)
        h, v_ = self._edit_default_layers()
        self._edit_apply(ic.edit_add_trunk(
            self._edit_topo, self.fp, horiz, int(perp), lo, hi,
            h if horiz else v_))

    def _edit_add_trunk_at(self, event, horiz):
        if event.xdata is None or event.ydata is None:
            self._edit_msg = "EDIT: put the cursor on the canvas first"
            self._draw(); return
        # Snap to the BUNDLE-scoped grid — exactly the lines the toggle
        # displays (busterm-block + keepout edges + OOB detour lines), so a
        # trunk never lands on an invisible full-design line.
        xs, ys = self._bundle_hanan_grid()
        perp = self._snap(event.ydata if horiz else event.xdata,
                          ys if horiz else xs)
        # Span the bundle's busterm extent along the trunk axis instead of the
        # whole-design Hanan extent (lo>hi) — a full-span trunk overshoots its
        # busterms and stubs.  Endpoints at the extreme busterm centres (where
        # stubs will drop), so the trunk covers exactly the blocks it serves.
        lo, hi = self._busterm_along_span(horiz)
        h, v_ = self._edit_default_layers()
        self._edit_apply(ic.edit_add_trunk(
            self._edit_topo, self.fp, horiz, perp, lo, hi,
            h if horiz else v_))

    def _along_span_of_blocks(self, names, horiz):
        """(lo, hi) covering the given blocks along a trunk axis, or None if
        degenerate: the extreme block CENTRES (x for H, y for V) when they
        differ, else the block EXTENT on that axis (blocks aligned on the axis —
        span their footprint), else None (a single point)."""
        cs, edges = [], []
        for n in names:
            r = self.fp.get_block_bounds(n)
            if horiz:
                cs.append(int(round((r.x1 + r.x2) / 2))); edges += [r.x1, r.x2]
            else:
                cs.append(int(round((r.y1 + r.y2) / 2))); edges += [r.y1, r.y2]
        if len(cs) >= 2 and max(cs) > min(cs):
            return min(cs), max(cs)
        if edges and max(edges) > min(edges):
            return min(edges), max(edges)
        return None

    def _busterm_along_span(self, horiz):
        """Default trunk span = ALL the bundle's busterms' extent (no
        whole-design overshoot); (1, 0) — the C++ full-span sentinel — only for
        a single degenerate busterm."""
        return self._along_span_of_blocks(self._bundle_busterm_names(), horiz) \
            or (1, 0)


    def _edit_add_stub_at(self, event):
        if event.xdata is None or event.ydata is None:
            self._edit_msg = "EDIT: put the cursor over a block"
            self._draw(); return
        block = self._block_at(event.xdata, event.ydata)
        if block is None:
            self._edit_msg = "EDIT: no block under the cursor"
            self._draw(); return
        # With just the trunk in the topology, auto-select it as the stub
        # target — there's only one thing a stub can attach to.
        if self.sidx == -1 and len(self._edit_topo.segments) == 1:
            self.sidx = 0
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select the target segment first (j/k)"
            self._draw(); return
        tgt = self._edit_topo.segments[self.sidx]
        h, v_ = self._edit_default_layers()
        layer = v_ if tgt.start.y == tgt.end.y else h    # stub ⟂ target
        self._edit_apply(ic.edit_add_stub(
            self._edit_topo, self.fp, block, self.sidx, layer))


    def _edit_pair_op(self, event, connect):
        """Two-step connect/disconnect: first press marks the current segment,
        second press pairs it with the (newly j/k-selected) current one."""
        if not (0 <= self.sidx < len(self._edit_topo.segments)):
            self._edit_msg = "EDIT: select a segment first (j/k)"
            self._draw(); return
        if self._edit_pending < 0:
            self._edit_pending = self.sidx
            self._edit_msg = (f"EDIT: seg {self.sidx} marked — select the "
                              f"partner (j/k) and press the key again")
            self._draw(); return
        i, j = self._edit_pending, self.sidx
        self._edit_pending = -1
        if i == j:
            self._edit_msg = "EDIT: same segment twice — pair cancelled"
            self._draw(); return
        if connect:
            self._edit_apply(ic.edit_connect(self._edit_topo, self.fp, i, j))
        else:
            si = self._edit_topo.segments[i]
            horiz = si.start.y == si.end.y
            coord = event.xdata if horiz else event.ydata
            if coord is None:
                self._edit_msg = "EDIT: cursor sets the retract position"
                self._draw(); return
            self._edit_apply(ic.edit_disconnect(
                self._edit_topo, self.fp, i, j, int(round(coord))))


    def _edit_commit(self):
        topo = self._edit_topo
        if topo is None or not topo.segments:
            self._edit_close("EDIT: nothing to commit")
            return
        topo.type = "USER"
        topo.estimated_wirelength = (
            sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                for s in topo.segments)
            + sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
                  for s in topo.bridge_segments.values()))
        w = self.wrapper
        uid = ic.topo_uid(topo)
        pool = list(w.input.candidates)
        existing = next((k for k, c in enumerate(pool)
                         if ic.topo_uid(c) == uid), None)
        if existing is not None:
            idx = existing
            note = f"identical candidate already at topo {idx + 1}"
        else:
            pool.append(topo)
            idx = len(pool) - 1
            w.input.candidates = pool
            note = f"committed as topo {idx + 1} (USER)"
        self._edit_topo    = None
        self._edit_pending = -1
        self.idx  = idx
        self.sidx = -1
        self._select_current()          # pin + sidecar (uid-carrying) + redraw
        self._edit_msg = f"EDIT: {note}, pinned"
        # Land the session's slide-window refinements as NUTS overrides on the
        # pinned candidate (plan.seg_slide_lo/hi, NaN = free) — the same hatch
        # dogleg splits ride: the next run_nuts honors them, a re-plan clears
        # them (existing override semantics).  Each staged window is
        # REVALIDATED against the committed topology's connectivity first: a
        # geometry edit after staging (stub/connect/disconnect/span) can
        # narrow the segment's structural slide range, and NUTS honors any
        # non-NaN override verbatim — so a shrunken window clamps and a
        # now-disjoint one is dropped LOUD, never written stale (Codex #294).
        if self._edit_slide:
            cs_list = list(self._build_conn_topo(topo).segs())
            nseg = len(topo.segments)
            slo = [float('nan')] * nseg
            shi = [float('nan')] * nseg
            applied, dropped = 0, []
            for si, (lo, hi) in sorted(self._edit_slide.items()):
                if not (0 <= si < nseg):
                    dropped.append(si)
                    continue
                s_lo = float(cs_list[si].perp_lo)
                s_hi = float(cs_list[si].perp_hi)
                clo, chi = max(float(lo), s_lo), min(float(hi), s_hi)
                if clo > chi:
                    dropped.append(si)
                    continue
                slo[si], shi[si] = clo, chi
                applied += 1
            # Written even when NOTHING survived (all-NaN): once the session
            # staged windows, its intent REPLACES any prior overrides — an
            # earlier commit's or a dogleg's matching-length arrays would
            # otherwise leak onto the newly pinned topology (Codex #295).  A
            # session that staged nothing leaves the plan untouched, so a
            # no-op re-commit of a dogleg-split candidate keeps its
            # load-bearing pin.
            w.plan.seg_slide_lo = slo
            w.plan.seg_slide_hi = shi
            if applied:
                self._edit_msg += f" (+{applied} slide window(s))"
            if dropped:
                self._edit_msg += (
                    f" (dropped stale slide window(s) on seg {dropped} — "
                    f"outside the segment's current slide range)")
            self._edit_slide = {}
            self._edit_slide_mark = None
        print(f"[edit] {self._edit_msg}")
        self._draw()
