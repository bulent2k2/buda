# fable exploring the idea of combining bundler options bi-dir and conv
# To run:
# PYTHONPATH=build:src python3 -c bundler_combo.py
"""
<Before the fix> 
 2>&1 | grep -vE 'RoutingGrid|Planner\]|min_band|NUTS\] M|overlap log|Span adj'

Generated 24 topologies for bundle 1 ([A,C]->B fan-in) 3 nets: n0, n1, n2
[NUTS] junction infeasible: bundle 1 seg 1 cannot reach seg 0 within its slide window (closed by partner stretch; ripup may re-pin).
[NUTS] 3 segments placed. Track overlaps: 0, Interval violations: 0.
[DetailedNUTS] 6 net segments placed, 0 bits unplaced.
endpoints derived: ('B', ['A', 'C'], True)
type: TRUNK_H@y350 contract: ['A', 'B', 'C']
seg_bits: {0: [0, 1, 2], 1: [0, 1], 2: [2]}
unplaced: 0
[Check] Verifying topology-level design...
  Success: no violations found.
[Check] Verifying Detailed NUTS-level design...
  Bundle 1: Seg 1: 1 bit(s) — unplaced (no track in DetailedNUTS)
  Bundle 1: Seg 2: 2 bit(s) — unplaced (no track in DetailedNUTS)
  Total: 3 violation(s) in 2 group(s) across 1 bundle(s). Use --verbose-conn for per-bit detail.

< After the fix >
 2>&1 | grep -E 'Verifying|Success|violation|unplaced'

[NUTS] 3 segments placed. Track overlaps: 0, Interval violations: 0.
[DetailedNUTS] 6 net segments placed, 0 bits unplaced.
[Check] Verifying Detailed NUTS-level design...
  Success: no violations found.
"""
import buda, buda_cli
sess = buda_cli.BudaSession(); sess.no_viz = True
for line in '''def_layer 4 M4 H TOP 50
def_layer 5 M5 V TOP 50
def_track_pattern 4 0 SIGNAL 1 1
def_track_pattern 5 0 SIGNAL 1 1
add_block A 0 0 100 80
add_block B 800 250 950 450
add_block C 300 600 400 680
add_net n0 A.tx B.r0
add_net n1 B.tx A.r0
add_net n2 C.tx B.r1'''.splitlines():
    sess.do_command(line)
# Hand-merge all three nets into ONE bundle (the COMBINED join: n0~n1 bidir {A,B}, n0~n2 convergent {B})
b = buda.HBundle()
b.id = 1
b.net_names = ['n0', 'n1', 'n2']
b.reason = 'COMBINED:test'
w = buda.BundleWrapper()
w.input.original_bundle = b
w.input.width = 3 * 1.5
sess.bundles = [w]
for cmd in ('generate_topologies', 'run_planner', 'run_nuts', 'run_detailed_nuts'):
    sess.do_command(cmd)
w = sess.bundles[0]
topo = w.input.candidates[w.plan.selected_topology_index]
print('endpoints derived:', sess._bundle_endpoints(w))
print('type:', topo.type, 'contract:', sorted(topo.connected_block_names))
print('seg_bits:', dict(topo.seg_bits))
print('unplaced:', sess.detailed_result.num_unplaced)
sess.do_command('check_design topo')
sess.do_command('check_design dnuts')
