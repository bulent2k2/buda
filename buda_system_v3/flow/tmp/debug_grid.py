import sys, os
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
os.chdir('/Users/ben/src/buda/buda_system_v2/src')

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt; plt.show = lambda: None

import importlib.util
spec = importlib.util.spec_from_file_location("buda_cli", "buda_cli.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

import interconnect as ic

session = mod.BudaSession()
session.script_path = "/Users/ben/src/buda/buda_system_v2/flow/two.buda"

with open("/Users/ben/src/buda/buda_system_v2/flow/two.buda", 'r') as f:
    for line in f:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped: continue
        if stripped.startswith('run_nuts') or stripped.startswith('visualize'): break
        session.do_command(line)

# Get Hanan grid from floorplan
x_grid, y_grid = [], []
session.fp.get_hanan_grid(x_grid, y_grid)
print(f"Hanan y-grid: {sorted(set(y_grid))}")
print(f"Hanan x-grid: {sorted(set(x_grid))}")

# Add extra points from planner
if session.planner:
    ex = list(session.planner.get_x_grid())
    ey = list(session.planner.get_y_grid())
    print(f"\nPlanner extra x: {sorted(set(ex))}")
    print(f"Planner extra y: {sorted(set(ey))}")
    
    # Full merged grid
    merged_y = sorted(set(y_grid) | set(ey))
    merged_x = sorted(set(x_grid) | set(ex))
    print(f"\nMerged y-grid: {merged_y}")
    print(f"Merged x-grid: {merged_x}")
    
    # Find cells for y=400 and x=500
    import bisect
    def find_cell(grid, val):
        idx = bisect.bisect_right(grid, val) - 1
        if 0 <= idx < len(grid)-1:
            return grid[idx], grid[idx+1]
        return None, None
    
    lo, hi = find_cell(merged_y, 400)
    print(f"\nHanan cell for H seg (y=400): [{lo}, {hi}]")
    lo, hi = find_cell(merged_x, 500)
    print(f"Hanan cell for V seg (x=500): [{lo}, {hi}]")
