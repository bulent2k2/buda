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

"""TopologyExplorer mixin — Selection state + JSON sidecar persistence.

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

class ExplorerSidecarMixin:

    # ------------------------------------------------------------------
    # Selection DB helpers
    # ------------------------------------------------------------------

    def _bundle_hint(self, wrapper=None):
        w = wrapper or self.wrapper
        names = w.input.original_bundle.get_net_names()
        return names[0] if names else f"bundle_{w.input.original_bundle.id}"


    def _find_selection(self, wrapper=None):
        """Return the saved selection dict for the given wrapper, or None.

        Tries the current bundle_hint first (first net name); falls back to
        matching by bundle_id so that sidecars created with older hint
        conventions (e.g. 't0_b0' instead of 't0_b0_00') still work.
        """
        w   = wrapper or self.wrapper
        sel = self._selections.get(self._bundle_hint(w))
        if sel is None:
            bid = w.input.original_bundle.id
            sel = next((s for s in self._selections.values()
                        if s.get('bundle_id') == bid), None)
        return sel


    def _selected_topo_index(self):
        """The single topology index pinned for the current bundle, or -1.

        A bundle has at most ONE pinned topology.  Resolution order:
          1. The live pin (script `select_topology` or an applied sidecar,
             reflected in plan.selected_topology_index + topology_pinned) — this
             is authoritative after run_planner and honours script-vs-sidecar
             precedence.
          2. Otherwise a sidecar entry for this bundle (used before run_planner,
             e.g. right after generate_topologies): its saved index, else the
             first candidate matching the saved (type, wirelength).

        Previously each of these criteria was OR'd inside _current_is_selected,
        so a stale sidecar could light up several different topologies of one
        bundle as "pinned" at once.
        """
        w = self.wrapper
        if getattr(w.input, 'topology_pinned', False):
            idx = w.plan.selected_topology_index
            return idx if 0 <= idx < len(self.topos) else -1

        sel = self._find_selection()
        if sel is not None:
            uid = sel.get('topo_uid')   # stable identity first (E1b)
            if uid:
                for i, topo in enumerate(self.topos):
                    if ic.topo_uid(topo) == uid:
                        return i
            hint = sel.get('topo_index_hint', -1)
            if 0 <= hint < len(self.topos):
                return hint
            for i, topo in enumerate(self.topos):
                if (topo.type == sel['topo_type'] and
                        topo.estimated_wirelength == sel['topo_wl']):
                    return i
        return -1


    def _focus_topo_index(self):
        """Which topology to display when opening or switching to the current
        bundle: its pinned topology (live pin or sidecar) if any, else the
        planner's choice, else topo 0.  Used by __init__, bundle cycling, and
        jump-to-bundle so all three focus the bundle's pin consistently."""
        idx = self._selected_topo_index()          # pinned (live or sidecar) or -1
        if idx >= 0:
            return idx
        sel = self.wrapper.plan.selected_topology_index
        if 0 <= sel < len(self.topos):              # planner's choice (unpinned)
            return sel
        return 0


    def _current_is_selected(self):
        return self.idx == self._selected_topo_index()


    def _select_current(self):
        topo = self.topos[self.idx]
        hint = self._bundle_hint()
        # Remove any old entry for this bundle (may have a different hint key).
        old_sel = self._find_selection()
        if old_sel is not None:
            stale_key = next((k for k, v in self._selections.items()
                              if v is old_sel), None)
            if stale_key and stale_key != hint:
                del self._selections[stale_key]
        
        wrapper = self.wrappers[self.bidx]
        sel = {
            'bundle_id':       wrapper.input.original_bundle.id,
            'topo_type':       topo.type,
            'topo_wl':         topo.estimated_wirelength,
            'topo_uid':        ic.topo_uid(topo),   # stable identity (E1b)
            'topo_index_hint': self.idx,
            'note':            '',
            'selected_at':     datetime.now().isoformat(timespec='seconds'),
        }
        
        pinned = list(wrapper.input.pinned_seg_layers)
        if len(pinned) == len(topo.segments):
            if any(lid != -1 for lid in pinned):
                sel['seg_layers'] = pinned

        # Re-selecting the SAME USER candidate (e.g. paging back to it and
        # pressing 's') must not shed its replay log: carry user_topo forward
        # when the old entry's uid matches the new selection's.
        if old_sel is not None and 'user_topo' in old_sel \
                and old_sel.get('topo_uid') == sel['topo_uid']:
            sel['user_topo'] = old_sel['user_topo']
        
        # Update live object
        if wrapper.plan.selected_topology_index != self.idx:
            wrapper.plan.seg_layers = [] # Clear stale results for different topology
        
        wrapper.plan.selected_topology_index = self.idx
        wrapper.input.topology_pinned = True
        
        self._selections[hint] = sel
        self._save_sidecar()
        self._draw()


    def _deselect_current(self):
        hint    = self._bundle_hint()
        old_sel = self._find_selection()
        if old_sel is not None:
            stale_key = next((k for k, v in self._selections.items()
                              if v is old_sel), hint)
            self._selections.pop(stale_key, None)
            self._save_sidecar()
            
            # Update live object
            wrapper = self.wrappers[self.bidx]
            wrapper.input.topology_pinned = False
            wrapper.input.pinned_seg_layers = []

        self._draw()


    def _load_sidecar(self):
        try:
            with open(self._sidecar_path) as f:
                data = json.load(f)
            for entry in data.get('selections', []):
                self._selections[entry['bundle_hint']] = {
                    k: entry[k] for k in
                    ('bundle_id', 'topo_type', 'topo_wl',
                     'topo_index_hint', 'note', 'selected_at')
                }
                if 'topo_uid' in entry:   # stable identity (E1b; older sidecars lack it)
                    self._selections[entry['bundle_hint']]['topo_uid'] = entry['topo_uid']
                if 'seg_layers' in entry:
                    self._selections[entry['bundle_hint']]['seg_layers'] = entry['seg_layers']
                if 'user_topo' in entry:  # TopoEdit replay log — must survive a
                    # load→save cycle, or one unrelated re-save silently strands
                    # the USER candidate on the next flow run (Codex #305)
                    self._selections[entry['bundle_hint']]['user_topo'] = entry['user_topo']

            print(f"Loaded {len(self._selections)} selection(s) from {self._sidecar_path}")
        except Exception as e:
            print(f"Warning: could not load sidecar {self._sidecar_path}: {e}")


    def _save_sidecar(self):
        if not self._sidecar_path:
            return
        entries = []
        for hint, sel in sorted(self._selections.items()):
            # Find the wrapper to check for actual pinned layers if they aren't in sel yet.
            # (Though _select_current usually populates sel from wrapper).
            entries.append({'bundle_hint': hint, **sel})

        try:
            with open(self._sidecar_path, 'w') as f:
                json.dump({'selections': entries}, f, indent=2)
            print(f"Saved {len(entries)} selection(s) to {self._sidecar_path}")
        except Exception as e:
            print(f"Warning: could not save sidecar {self._sidecar_path}: {e}")
