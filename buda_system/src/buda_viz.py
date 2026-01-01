import matplotlib.pyplot as plt
import matplotlib.patches as patches
class BudaVisualizer:
    def __init__(self, floorplan, bundles):
        self.fp = floorplan
        self.bundles = bundles
        self.fig, self.ax = plt.subplots(figsize=(10, 10))
    def draw_blocks(self):
        blocks = self.fp.get_all_blocks()
        for name, rect in blocks:
            width = rect.x2 - rect.x1
            height = rect.y2 - rect.y1
            self.ax.add_patch(patches.Rectangle((rect.x1, rect.y1), width, height, linewidth=1, edgecolor='black', facecolor='lightgrey', alpha=0.5))
            self.ax.text((rect.x1+rect.x2)/2, (rect.y1+rect.y2)/2, name, ha='center', va='center')
    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        for x in xs: self.ax.axvline(x=x, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
        for y in ys: self.ax.axhline(y=y, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
    def draw_buses(self):
        layer_colors = { 3: 'blue', 4: 'red', 1: 'cyan', 2: 'magenta' }
        for wrapper in self.bundles:
            selected_topo = wrapper.candidates[wrapper.selected_topology_index]
            for seg in selected_topo.segments:
                self.ax.plot([seg.start.x, seg.end.x], [seg.start.y, seg.end.y], color=layer_colors.get(seg.layer_hint, 'green'), linewidth=wrapper.width, solid_capstyle='round', alpha=0.8)
    def show(self):
        self.ax.set_aspect('equal')
        plt.title("BUDA: Z-Shape Topology Visualization")
        plt.grid(False)
        plt.show()