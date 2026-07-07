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
Centralized UI state management for BUDA visualizers.

Provides a unified ViewState object that can be shared across multiple windows
(e.g., BudaVisualizer and TopologyExplorer) to keep visibility toggles in sync.
"""

from __future__ import annotations
from typing import Callable, Set


class ViewState:
    def __init__(self):
        # Visibility flags
        self.blocks = True
        self.block_names = True
        self.hanan_grid = False
        self.keepouts = True
        self.heatmap = False   # off by default: the congestion overlay is the
                               # heaviest layer to render on large designs
        self.busterms = True
        self.vias_conns = True
        self.detailed_mode = False
        self.tracks = False
        # Pre-route layer (draw_preroutes): a CYCLING mode rather than a
        # boolean — 'off' -> 'ALL' -> one slot type at a time -> 'off'.
        self.preroutes_mode = 'off'
        self.preroute_cycle = ('off', 'ALL', 'POWER', 'GROUND', 'CLOCK', 'SHIELD')
        self.all_vis = True
        self.solo = False

        # Internal listener registry for cross-window synchronization
        self._listeners: Set[Callable[[], None]] = set()

    def add_listener(self, callback: Callable[[], None]):
        """Register a callback to be invoked when any state changes."""
        self._listeners.add(callback)

    def remove_listener(self, callback: Callable[[], None]):
        """Unregister a callback."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def notify(self):
        """Invoke all registered listeners to trigger redraws."""
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                pass

    def toggle_blocks(self):
        self.blocks = not self.blocks
        self.notify()

    def toggle_block_names(self):
        self.block_names = not self.block_names
        self.notify()

    def toggle_hanan_grid(self):
        self.hanan_grid = not self.hanan_grid
        self.notify()

    def toggle_keepouts(self):
        self.keepouts = not self.keepouts
        self.notify()

    def toggle_heatmap(self):
        self.heatmap = not self.heatmap
        self.notify()

    def toggle_busterms(self):
        self.busterms = not self.busterms
        self.notify()

    def toggle_vias_conns(self):
        self.vias_conns = not self.vias_conns
        self.notify()

    def toggle_detailed(self):
        self.detailed_mode = not self.detailed_mode
        self.notify()

    def toggle_tracks(self):
        self.tracks = not self.tracks
        self.notify()

    def cycle_preroutes(self):
        """Advance the pre-route display mode one step around the cycle."""
        cyc = self.preroute_cycle
        i = cyc.index(self.preroutes_mode) if self.preroutes_mode in cyc else 0
        self.preroutes_mode = cyc[(i + 1) % len(cyc)]
        self.notify()

    def toggle_all(self):
        self.all_vis = not self.all_vis
        # When toggling 'All', typically we want to align the main flags
        self.blocks = self.all_vis
        self.block_names = self.all_vis
        self.keepouts = self.all_vis
        self.heatmap = self.all_vis
        self.busterms = self.all_vis
        self.vias_conns = self.all_vis
        self.hanan_grid = self.all_vis
        self.tracks = self.all_vis
        self.preroutes_mode = 'ALL' if self.all_vis else 'off'
        self.notify()

    def toggle_solo(self):
        self.solo = not self.solo
        self.notify()


def bind_common_keys(fig, state: ViewState):
    """
    Standardize keyboard shortcuts across all Matplotlib-based visualizers.
    
    Usage:
        def on_key(event):
            if bind_common_keys(self.fig, self.ui_state):
                return
            # ... handle custom tool-specific keys ...
    """
    # This is a helper intended to be called within a key_press_event handler.
    # We don't attach the event here directly because tools often have
    # complex key handlers that need to consume events conditionally.
    pass
