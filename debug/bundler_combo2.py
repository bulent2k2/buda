# $ PYTHONPATH=build:src python3 -c "
import buda_cli
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
add_net n2 C.tx B.r1
run_bundler COMBINED'''.splitlines():
    sess.do_command(line)
for w in sess.bundles:
    b = w.input.original_bundle
    print('bundle', b.id, b.get_net_names(), 'reason:', b.reason)
for cmd in ('generate_topologies','run_planner','run_nuts','run_detailed_nuts','check_design dnuts'):
    sess.do_command(cmd)
#" 2>&1 | grep -E 'COMBINED|bundle |Generated|Success|violation|unplaced|fan-in'
"""
[Bundler] COMBINED: join of CONVERGENT and BIDIRECTIONAL (transitive; see docs/internal/convergent_bundling.md). Restrict per prefix with set_bundling.
bundle 1 ['n0', 'n1', 'n2'] reason: COMBINED:A,B,C
Generated 24 topologies for bundle 1 ([A,C]->B fan-in) 3 nets: n0, n1, n2
[NUTS] junction infeasible: bundle 1 seg 1 cannot reach seg 0 within its slide window (closed by partner stretch; ripup may re-pin).
[NUTS] 3 segments placed. Track overlaps: 0, Interval violations: 0.
[DetailedNUTS] 6 net segments placed, 0 bits unplaced.
  Success: no violations found.
"""
