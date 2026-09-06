#!/usr/bin/env bash
# Tier 1a of the LibreLane study (docs/internal/librelane_hier_flow.md §7.1):
# the systolic array at size N as a FLAT LibreLane design -- arm F.
#
#   flow/librelane/tier1a/gen.sh N [extra tpu.tcl args...]
#
# Emits the array at N with `btcl flow/tcl/tpu.tcl N -emit` (tpu_rtl.v is the
# synthesizable twin of tpu.v: same modules, instances and widths, with a
# streaming-MAC datapath inside) into flow/librelane/tier1a/n<N>/, and writes
# the LibreLane config beside it.  Then:
#
#   cd flow/librelane/tier1a/n<N> && librelane --dockerized --run-tag flat config.json
#   python3 ../runtimes.py runs/flat --set N=<N> --set arm=F   # per-stage runtime + the PPA metrics
#
# Sizing is RELATIVE (FP_CORE_UTIL): the flat arm gets to choose its own die
# from the cell area, which is what a flat flow does; the DEF the emitter
# writes beside it is the block placement the H arms will use instead.
set -euo pipefail
N=${1:?usage: gen.sh N [tpu.tcl args]}; shift || true
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
# T1A_DIR overrides where the design lands (the tests use a temp dir).
d="${T1A_DIR:-$here}/n$N"; mkdir -p "$d"
"$root/bin/btcl" "$root/flow/tcl/tpu.tcl" "$N" "$@" -emit "$d" >/dev/null
cat > "$d/config.json" <<JSON
{
    "meta": {"version": 2},
    "DESIGN_NAME": "tpu_top",
    "VERILOG_FILES": ["dir::tpu_rtl.v"],
    "CLOCK_PORT": "clk",
    "CLOCK_PERIOD": 20,

    "FP_SIZING": "relative",
    "FP_CORE_UTIL": 40,
    "PL_TARGET_DENSITY_PCT": 45,

    "RT_MAX_LAYER": "met5",
    "GRT_ALLOW_CONGESTION": false,
    "RUN_KLAYOUT_XOR": false,
    "RUN_MAGIC_DRC": false,
    "RUN_KLAYOUT_DRC": true
}
JSON
echo "tier1a: N=$N -> $d  (tpu_rtl.v + config.json; DEF/LEF/tpu.v beside them)"
