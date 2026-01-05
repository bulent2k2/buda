import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

class BudaVisualizer:
    def __init__(self, floorplan, bundles):
        self.fp = floorplan
        self.bundles = bundles
        # Increase figure size for better detail
        self.fig, self.ax = plt.subplots(figsize=(14, 12))
        self.fig.patch.set_facecolor('#f0f0f0') # Light gray background for contrast

    def draw_blocks(self):
        blocks = self.fp.get_all_blocks()
        for name, rect in blocks:
            width = rect.x2 - rect.x1
            height = rect.y2 - rect.y1

            # Draw the block body
            rect_patch = patches.Rectangle(
                (rect.x1, rect.y1), width, height,
                linewidth=2, edgecolor='#444444', facecolor='#d9d9d9',
                alpha=0.6, zorder=1
            )
            self.ax.add_patch(rect_patch)

            # Draw the label
            cx, cy = (rect.x1 + rect.x2)/2, (rect.y1 + rect.y2)/2
            self.ax.text(
                cx, cy, name,
                ha='center', va='center', fontsize=9, fontweight='bold',
                color='#333333', zorder=2
            )

    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        # Draw grid very faintly in the background
        for x in xs:
            self.ax.axvline(x=x, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)
        for y in ys:
            self.ax.axhline(y=y, color='#cccccc', linestyle='--', linewidth=0.5, zorder=0)

    def draw_buses(self):
        # Layer Colors: Standardized EDA colors (Blue/Red for M3/M4)
        layer_specs = {
            3: {'color': '#007ACC', 'style': 'solid'},  # M3 (Horizontal) - Blue
            4: {'color': '#CC0000', 'style': 'solid'}   # M4 (Vertical) - Red
        }

        # Offset generator to prevent overlapping lines from looking like one
        # We shift slightly based on the bundle ID
        for i, wrapper in enumerate(self.bundles):
            selected_topo = wrapper.candidates[wrapper.selected_topology_index]
            # 1. Calculate Visual Width
            # Scale factor: 1 unit of logical width = X points of visual width
            # We add a base thickness so even 1-bit buses are visible
            viz_width = (wrapper.width * 1.5) + 2.0
            # 2. Draw Segments
            offset = (i % 3 - 1) * 2.0 # Simple jitter (-2, 0, +2)
            # We collect start/end points to draw terminals later
            topo_start = None
            topo_end = None

            for idx, seg in enumerate(selected_topo.segments):
                spec = layer_specs.get(seg.layer_hint, {'color': 'green', 'style': 'dashed'})

                sx, sy = seg.start.x + offset, seg.start.y + offset
                ex, ey = seg.end.x + offset, seg.end.y + offset

                # Capture terminals
                if idx == 0: topo_start = (sx, sy)
                if idx == len(selected_topo.segments) - 1: topo_end = (ex, ey)

                self.ax.plot(
                    [sx, ex], [sy, ey],
                    color=spec['color'],
                    linewidth=viz_width,
                    linestyle=spec['style'],
                    solid_capstyle='butt', # 'butt' allows clean corner joints
                    alpha=0.8,
                    zorder=10 + i # Ensure distinct stacking order
                )

                # Draw small circles at segment joints (vias)
                if idx < len(selected_topo.segments) - 1:
                     self.ax.plot(ex, ey, 'o', color='black', markersize=viz_width/3, zorder=11+i)

            # 3. Draw Terminals (Bus Ports)
            if topo_start:
                # Driver: Cyan Square
                self.ax.plot(topo_start[0], topo_start[1], 's',
                             color='#00FFFF', markeredgecolor='black',
                             markersize=viz_width, zorder=20)
                # Label ID at start
                self.ax.text(topo_start[0], topo_start[1], f"B{wrapper.original_bundle.id}",
                             fontsize=8, color='black', fontweight='bold', zorder=21,
                             ha='center', va='center')

            if topo_end:
                # Receiver: Magenta Circle
                self.ax.plot(topo_end[0], topo_end[1], 'o',
                             color='#FF00FF', markeredgecolor='black',
                             markersize=viz_width, zorder=20)

    def show(self):
        self.ax.set_aspect('equal')
        plt.title("BUDA Global Plan: Non-Uniform Widths & Terminals", fontsize=14)

        # Legend
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], color='#007ACC', lw=4, label='Metal 3 (Horiz)'),
            Line2D([0], [0], color='#CC0000', lw=4, label='Metal 4 (Vert)'),
            Line2D([0], [0], marker='s', color='w', markerfacecolor='#00FFFF', markeredgecolor='k', label='Driver'),
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#FF00FF', markeredgecolor='k', label='Receiver')
        ]
        self.ax.legend(handles=legend_elements, loc='upper right')

        # Auto-scale limits with some padding
        self.ax.autoscale_view()
        plt.tight_layout()
        plt.show()
