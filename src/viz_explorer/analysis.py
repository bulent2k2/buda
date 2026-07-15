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

"""TopologyExplorer mixin — Slide/conn/analysis readers over the shown topology.

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

class ExplorerAnalysisMixin:

    # ------------------------------------------------------------------

    def _build_conn_topo(self, topo):
        ct = ic.ConnTopology()
        ct.build(topo, self.fp)
        return ct


    # The dogleg pass pins per-segment net_pull and slide windows on the plan
    # (ConnTopology would recompute them wrongly on the split topology).  Those
    # overrides are indexed by the SELECTED topology's segments, so honor them
    # only when the explorer is showing that topology — then the view matches
    # what NUTS actually used.
    def _show_overrides(self):
        # Only the SELECTED topology, and only while an override array still
        # matches that topology's segment count — a later run_planner can replace
        # the selected topology without refreshing these, so a size mismatch means
        # the overrides are stale and must be ignored (as build_nuts_maps does).
        return self.idx == self.wrapper.plan.selected_topology_index


    def _n_segs(self):
        return len(self.topos[self.idx].segments)


    def _seg_net_pull(self, cs, ci):
        snp = getattr(self.wrapper.plan, 'seg_net_pull', None)
        if self._show_overrides() and snp and len(snp) == self._n_segs():
            if snp[ci] != -2147483648:
                return snp[ci]
        return cs.net_pull


    def _seg_slide(self, cs, ci):
        # A staged edit-session slide window wins while editing, so the slide
        # band and its bound labels live-update as 'W' refines the window.
        if self._edit_topo is not None and ci in self._edit_slide:
            return self._edit_slide[ci]
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        shi = getattr(self.wrapper.plan, 'seg_slide_hi', None)
        if (self._show_overrides() and slo and shi and len(slo) == self._n_segs()
                and len(shi) == self._n_segs() and not math.isnan(slo[ci])):
            return slo[ci], shi[ci]
        return cs.perp_lo, cs.perp_hi


    def _is_dogleg_seg(self, ci):
        # A dogleg piece/jog carries a pinned slide window; such a segment must
        # display at its NOMINAL position (the two pieces share a slide range, so
        # the range-centre display would collapse them and hide the dogleg step).
        slo = getattr(self.wrapper.plan, 'seg_slide_lo', None)
        return bool(self._show_overrides() and slo and len(slo) == self._n_segs()
                    and not math.isnan(slo[ci]))


    def _centered_perp(self, cs) -> float:
        """Centered display perp position within the slide interval.

        When both perp_lo and perp_hi are finite, returns the interval midpoint
        so the drawn segment line never obscures either boundary.  Falls back to
        the nominal perp_pos when one or both ends are unconstrained.
        """
        lo, hi = cs.perp_lo, cs.perp_hi
        if abs(lo) < _UNCONSTRAINED and abs(hi) < _UNCONSTRAINED:
            return (lo + hi) / 2.0
        return float(cs.perp_pos)


    def _bundle_busterm_names(self):
        """The bundle's busterm block set — the union of seg_busterms taps
        across this bundle's candidates (the generator's own tap record; every
        valid candidate taps exactly the bundle's endpoint blocks, per the
        coverage gate).  Falls back to the shown topo's connected_block_names
        when no candidate carries taps (a fully hand-built pool)."""
        names = set()
        for t in self.wrapper.input.candidates:
            for eps in t.seg_busterms.values():
                for bt in eps:
                    if bt is not None:
                        names.add(bt.block_name)
        if not names:
            names = set(self._shown_topo().connected_block_names)
        return names


    def _bundle_hanan_grid(self):
        """The per-bundle Hanan grid GENERATION actually uses for this bundle
        (TopologyGenerator::generate_npin): edges of the bundle's busterm
        rects — each individual rect of a multi-rect block, the orig bbox
        otherwise — plus every keepout's edges.  The full-design
        fp.get_hanan_grid() adds every unrelated block's lines, which is
        noise when editing one bundle."""
        xs, ys = set(), set()
        for n in self._bundle_busterm_names():
            rects = self.fp.get_block_rects(n)
            if not rects:
                rects = [self.fp.get_block_bounds(n)]
            for r in rects:
                xs.update((r.x1, r.x2)); ys.update((r.y1, r.y2))
        for koz in self.fp.get_keepout_zones():
            xs.update((koz.bbox.x1, koz.bbox.x2))
            ys.update((koz.bbox.y1, koz.bbox.y2))
        return sorted(xs), sorted(ys)
