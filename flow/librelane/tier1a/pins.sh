#!/usr/bin/env bash
# Tier 1a of the LibreLane study (docs/internal/librelane_hier_flow.md §8
# step 7f): the BLOCK-SIDE handoff of arm H+B.  `size.buda` gives each leaf
# cell its SIZE; this gives each one its PINS.
#
#   flow/librelane/tier1a/pins.sh N
#
# It writes n<N>/pins.buda and runs it, producing one `FP_DEF_TEMPLATE` per
# leaf CELL TYPE in n<N>/pins/.  `harm.py --pins` then writes each template
# into its block's config, which is what makes the row H+B rather than
# H+size: the block's pins come from the plan instead of from LibreLane's
# own placer.
#
# WHY THE FLOW IS GENERATED rather than authored once: a `.buda` script has
# no variables and resolves every relative path against ITS OWN directory,
# so an N-agnostic file could not name n<N>/tpu.def.  gen.sh writes
# n<N>/config.json the same way, from the same kind of heredoc; the
# reasoning lives here, in the writer.
#
# THE LAYERS are sky130A's, declared by hand as
# `phase0/two_reg32/pins.buda` declares them (that file carries the full
# reasoning).  What is specific to the array:
#
#  * TWO layers carry the buses, not one.  A systolic mesh runs both ways --
#    a_out/w_out cross a row to the next PE (an H wire onto an E/W face),
#    p_out climbs to the row above (a V wire onto an N/S face) -- so the
#    template needs a track grid on both axes.  met3 (H) and met2 (V) are
#    the pair BELOW the top's PDN, which is §9's block rule read from the
#    pin side: a pin the top's router reaches on met4 would sit in the PDN.
#  * EVERY SECOND PDK TRACK, for `GRT_LAYER_ADJUSTMENTS` (phase 0's lesson:
#    bits on consecutive tracks leave the router nothing on the face it has
#    to reach, and the run ends in GRT-0116 overflow).  The origin is a slot
#    START, so a PDK centre c wants origin c - width/2: met2's tracks are at
#    230 + 460k DBU (origin 160), met3's at 340 + 680k (origin 190).
#  * `set_bus_layers` keeps the buses on that pair.  `TOP` is a preference
#    the cost function outvotes (§8 step 3b measured a bus wandering to met1
#    and taking the template's pins with it); `expect_layer` is the check.
#  * THE NETLIST IS tpu_rtl.v, NOT tpu.v, and that is forced rather than
#    preferred.  `FP_DEF_TEMPLATE` is ALL OR NOTHING: declaring it makes
#    LibreLane SKIP `OpenROAD.IOPlacement` entirely ("I/O pins were loaded
#    from ..."), so a pin the template omits is placed by nobody and global
#    placement refuses it (`GPL-0326 clk toplevel port is not placed` --
#    measured on all four cells at N=2).  A template therefore has to cover
#    every port of the module the BLOCK RUN synthesizes, and that is
#    tpu_rtl.v: the emitter's structural view declares only the bus ports,
#    while the synthesizable twin also has `clk` and `rst`.  Reading the
#    same file LibreLane does is the fix, and `emit_pin_def` spreads the
#    ports no bus reaches on one edge, which is exactly what they need.
#    (`FP_TEMPLATE_MATCH_MODE permissive` does NOT rescue an incomplete
#    template -- it only downgrades the mismatch report; the pin still ends
#    up unplaced.  It is set anyway, because the top's die ports reach a
#    block on nets BUDA has no corridor for and the two sets are not
#    identical; `tools/pin_def_verify.py` is what checks the template
#    afterwards, and the block README's step 1 runs it.)
#  * `snap` and `on_mismatch reference` are the two costs this vehicle pays
#    that the phase-0 toy did not, and both are REPORTED per cell:
#      - The emitter's pitch is not a whole number of track periods, so no
#        instance origin is on phase.  Putting it on phase means rounding
#        PPX up to a multiple of 0.92 um and RPY to one of 1.36 -- paid once
#        per COLUMN and once per ROW, i.e. N times each, which at N=8 is
#        ~9 % of the die.  `snap` costs at most half a period (0.46 / 0.68
#        um) of jog at the face instead, so it is what this uses; the die
#        arithmetic is why, and it is the one place the array differs from
#        the toy, where the honest fix was free.
#      - A cell whose instances have DIFFERENT NEIGHBOURS cannot have one
#        template agree with every instance's plan: the last row of PEs
#        hands its psum to an accumulator, every other row to the PE above.
#        No re-plan fixes that -- it is the design -- so the disputed pins
#        come from the reference instance and the jog is reported.
set -euo pipefail
N=${1:?usage: pins.sh N   (after gen.sh N)}; shift || true
if [ $# -ne 0 ]; then echo "pins.sh: unexpected arguments: $*" >&2; exit 1; fi
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
# T1A_DIR overrides where the design lives (the tests use a temp dir), as in gen.sh.
d="${T1A_DIR:-$here}/n$N"
for f in tpu.def tpu.lef tpu.v; do
    if [ ! -f "$d/$f" ]; then
        echo "pins.sh: $d has no $f -- run gen.sh $N first (it emits the set)" >&2
        exit 1
    fi
done
mkdir -p "$d/pins"
cells=$(sed -n 's/^MACRO \([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' "$d/tpu.lef")
if [ -z "$cells" ]; then echo "pins.sh: $d/tpu.lef declares no MACRO" >&2; exit 1; fi

{
cat <<'HEAD'
# pins.buda -- generated by flow/librelane/tier1a/pins.sh; do not edit.
# The block-side handoff of arm H+B: one FP_DEF_TEMPLATE per leaf cell type,
# from the plan.  Every rule behind this file is in pins.sh.
open_bdb :memory:
set_import_scale dbu
set_unit_check on

def_layer 1 met1 H LOW 30
def_layer 2 met2 V LOW 30
def_layer 3 met3 H TOP 30
def_layer 4 met4 V TOP 30
def_layer 5 met5 H TOP 30
def_track_pattern 2 160 SIGNAL 140 320 CUSTOM 140 320
def_track_pattern 3 190 SIGNAL 300 380 CUSTOM 300 380
set_bus_layers * met2,met3

corner_margin dx 5000 dy 5000
set_min_stub_length 2000

require_file tpu.def tpu.lef tpu_rtl.v hint run flow/librelane/tier1a/gen.sh first
import_def_lef tpu.def tpu.lef
import_verilog tpu_rtl.v
derive_container_bboxes margin 12000
derive_busterms 2

add_blocks_from_bdb 0
add_blocks_from_bdb 1 skip
add_blocks_from_bdb 2 skip

run_hier_bundler depth 2
generate_hier_topologies
set_planner_param healersAhead 1
run_planner hier 5
run_nuts
run_detailed_nuts
check_design dnuts
report_wirelength
HEAD
for c in $cells; do
    echo "emit_pin_def pins/$c.def $c snap on_mismatch reference unrouted S met2 expect_layer met2,met3"
done
} > "$d/pins.buda"

"$root/bin/buda" --no-viz "$d/pins.buda"
echo "tier1a: N=$N -> $d/pins/  ($(echo "$cells" | wc -l | tr -d ' ') template(s); next: ./harm.sh $N --pins pins)"
