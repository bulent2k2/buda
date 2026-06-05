import sys, os
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
os.chdir('/Users/ben/src/buda/buda_system_v2/src')

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt; plt.show = lambda: None

import importlib.util
spec = importlib.util.spec_from_file_location("buda_cli", "buda_cli.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

import interconnect as ic
import bisect

session = mod.BudaSession()
session.script_path = "/Users/ben/src/buda/buda_system_v2/flow/two.buda"

with open("/Users/ben/src/buda/buda_system_v2/flow/two.buda", 'r') as f:
    for line in f:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped: continue
        if stripped.startswith('run_nuts') or stripped.startswith('visualize'): break
        session.do_command(line)

x_grid, y_grid = session.fp.get_hanan_grid()
print(f"Hanan y-grid: {sorted(set(y_grid))}")
print(f"Hanan x-grid: {sorted(set(x_grid))}")

if session.planner:
    ex = list(session.planner.get_x_grid())
    ey = list(session.planner.get_y_grid())
    print(f"\nPlanner extra y (first 20): {sorted(set(ey))[:20]}")
    print(f"Planner extra x (first 20): {sorted(set(ex))[:20]}")
    
    merged_y = sorted(set(y_grid) | set(ey))
    merged_x = sorted(set(x_grid) | set(ex))
    
    def find_cell(grid, val):
        idx = bisect.bisect_right(grid, val) - 1
        if 0 <= idx < len(grid)-1:
            return grid[idx], grid[idx+1]
        return None, None
    
    lo, hi = find_cell(merged_y, 400)
    print(f"\nHanan cell for H seg (y=400): [{lo}, {hi}]")
    lo, hi = find_cell(merged_x, 500)
    print(f"Hanan cell for V seg (x=500): [{lo}, {hi}]")
    
    # Values around 400 in y
    near_400 = [v for v in merged_y if 390 <= v <= 460]
    print(f"y values near 400: {near_400}")
    # Values around 500 in x
    near_500 = [v for v in merged_x if 490 <= v <= 560]
    print(f"x values near 500: {near_500}")
