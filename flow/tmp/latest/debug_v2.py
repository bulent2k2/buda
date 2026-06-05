import sys, os
sys.path.insert(0, '/Users/ben/src/buda/buda_system_v2/src')
os.chdir('/Users/ben/src/buda/buda_system_v2/src')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.show = lambda: None

import importlib.util
spec = importlib.util.spec_from_file_location("buda_cli", 
    "/Users/ben/src/buda/buda_system_v2/src/buda_cli.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

session = mod.BudaSession()
session.script_path = "/Users/ben/src/buda/buda_system_v2/flow/two.buda"

# Run all commands except 'visualize'
with open("/Users/ben/src/buda/buda_system_v2/flow/two.buda", 'r') as f:
    for line in f:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped:
            continue
        if stripped.startswith('visualize'):
            break
        session.do_command(line)

result = session.nuts_result
print(f"\nTotal violations={result.num_violations}, overlaps={result.num_overlaps}")
print(f"Total segments: {len(result.segments)}")

viol_count = 0
for ts in sorted(result.segments, key=lambda x: (x.bundle_id, x.seg_idx)):
    lo, hi = ts.interval_lo, ts.interval_hi
    pos = ts.track_position
    w = ts.width
    if pos - w/2 < lo - 0.001 or pos + w/2 > hi + 0.001:
        print(f"VIOLATION bid={ts.bundle_id} seg={ts.seg_idx} layer={ts.layer} "
              f"pos={pos:.1f} ±{w/2:.1f} interval=[{lo:.1f},{hi:.1f}] span=[{ts.span_lo},{ts.span_hi}]")
        viol_count += 1

print(f"\nManual violation count: {viol_count}")
