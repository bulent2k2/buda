import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
class BudaVisualizer:
    def __init__(self, floorplan, bundles):
        self.fp = floorplan
        self.bundles = bundles
        self.fig, self.ax = plt.subplots(figsize=(12, 10))
    def draw_blocks(self):
        blocks = self.fp.get_all_blocks()
        for name, rect in blocks:
            width = rect.x2 - rect.x1
            height = rect.y2 - rect.y1
            self.ax.add_patch(patches.Rectangle((rect.x1, rect.y1), width, height, linewidth=2, edgecolor='#555555', facecolor='#AAAAAA', alpha=0.3))
            self.ax.text((rect.x1+rect.x2)/2, (rect.y1+rect.y2)/2, name, ha='center', va='center', fontweight='bold')
    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        for x in xs: self.ax.axvline(x=x, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
        for y in ys: self.ax.axhline(y=y, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
    def draw_buses(self):
        layer_colors = { 3: 'blue', 4: 'red' }
        # Add slight offset so overlapping buses are visible
        offset_map = {} 
        for i, wrapper in enumerate(self.bundles):
            selected_topo = wrapper.candidates[wrapper.selected_topology_index]
            print(f"Bundle {wrapper.original_bundle.id} selected topology: {selected_topo.type}")
            for seg in selected_topo.segments:
                # Simple offset generation based on bundle ID
                off = (i % 5) * 3.0 
                sx, sy = seg.start.x + off, seg.start.y + off
                ex, ey = seg.end.x + off, seg.end.y + off
                self.ax.plot([sx, ex], [sy, ey], color=layer_colors.get(seg.layer_hint, 'green'), linewidth=wrapper.width/1.5 + 2, solid_capstyle='round', alpha=0.7)
    def show(self):
        self.ax.set_aspect('equal')
        plt.title("BUDA Comprehensive Demo: L, Z, and U Topologies")
        plt.grid(False)
        # Set limits to show everything clearly
        self.ax.set_xlim(0, 1000)
        self.ax.set_ylim(0, 1000)
        plt.show()