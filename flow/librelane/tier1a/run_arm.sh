#!/usr/bin/env bash
# Run one hierarchical arm end to end, unattended, from an emitted set --
# the generated h/README.md's steps 1, notch, 3 (and 3a/3b/3c for H+B), 4.
#
#   T1A_DIR=<arm dir> flow/librelane/tier1a/run_arm.sh N h|hb [harm.sh args...]
#
# `h`  = arm H   (harm.sh N, top run tag h, LibreLane's own routing)
# `hb` = arm H+B (harm.sh N --pins pins, top cut at DetailedRouting, BUDA's
#        guides put in, LibreLane's DetailedRouting resumed on them)
# Extra arguments go to harm.sh (`--density 65`).  Every stage is timed into
# $d/h/stages.txt and logged under $d/h/log/; the row for the table is
# appended to $d/results.jsonl.  Requires `librelane` on PATH (the venv) and
# Docker running.  Every LibreLane call passes `--docker-no-tty`: this runs
# unattended, and `--dockerized` otherwise asks Docker for a terminal it does
# not have ("cannot attach stdin to a TTY-enabled container").
set -euo pipefail
N=${1:?usage: run_arm.sh N h|hb [harm.sh args]}; shift
tag=${1:?usage: run_arm.sh N h|hb [harm.sh args]}; shift
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
d="${T1A_DIR:-$here}/n$N"
[ -f "$d/tpu.def" ] || { echo "run_arm: $d has no emitted set" >&2; exit 1; }
case $tag in
    h)  pins=() ;;
    hb) pins=(--pins pins); [ -d "$d/pins" ] || { echo "run_arm: $d/pins missing -- run pins.sh $N first" >&2; exit 1; } ;;
    *)  echo "run_arm: tag must be h or hb" >&2; exit 1 ;;
esac
export PDK_ROOT="${PDK_ROOT:-$HOME/.ciel}"
mkdir -p "$d/h/log"
stages="$d/h/stages.txt"
stamp() { echo "$(date +%s) $1" >> "$stages"; echo "run_arm: $(date '+%H:%M:%S') $1"; }

stamp "harm.sh start"
"$here/harm.sh" "$N" "${pins[@]}" "$@"
cells=$(sed -n 's/^MACRO \([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' "$d/tpu.lef")

# 1. harden the blocks, in parallel (the wall figure is the batch)
stamp "blocks start"
pids=()
for c in $cells; do
    (cd "$d/h/$c" && librelane --docker-no-tty --dockerized --run-tag h config.json > "$d/h/log/$c.log" 2>&1) &
    pids+=($!)
done
fail=0
for p in "${pids[@]}"; do wait "$p" || fail=1; done
stamp "blocks end"
for c in $cells; do
    grep -q "Flow complete" "$d/h/log/$c.log" || { echo "run_arm: $c did not complete (see $d/h/log/$c.log)" >&2; fail=1; }
done
[ $fail = 0 ] || exit 1
if [ "$tag" = hb ]; then
    for c in $cells; do
        python3 "$root/tools/pin_def_verify.py" "$d/pins/$c.def" "$d/h/$c/runs/h/final/def/$c.def" \
            || { echo "run_arm: $c: template pins MOVED" >&2; exit 1; }
    done
fi

# 2. the patched abstracts the top's MACROS name
stamp "notch start"
"$here/notch.sh" "$N" > "$d/h/log/notch.log" 2>&1
stamp "notch end"

# 3. the top
stamp "top start"
if [ "$tag" = h ]; then
    (cd "$d/h/top" && librelane --docker-no-tty --dockerized --run-tag h config.json > "$d/h/log/top.log" 2>&1)
else
    (cd "$d/h/top" && librelane --docker-no-tty --dockerized --run-tag hb \
        --to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting config.json > "$d/h/log/top_a.log" 2>&1)
    stamp "top cut at DetailedRouting; guides start"
    (cd "$here" && TAG=hb T1A_DIR="${T1A_DIR:-$here}" ./guides.sh "$N" > "$d/h/log/guides.log" 2>&1)
    ODB=$(ls -t "$d"/h/top/runs/hb/*/*.odb | head -1)
    (cd "$d/h" && "$root/flow/librelane/phase0/measure/run_or.sh" top/runs/hb "$here/guide_route.tcl" \
        ODB="$ODB" GUIDE="$d/h/top/out/buda_bus.guide" OUT="$d/h/top/out" > "$d/h/log/guide_route.log" 2>&1)
    grep -q "wrote" "$d/h/log/guide_route.log" || { echo "run_arm: guide_route.tcl wrote nothing (see $d/h/log/guide_route.log)" >&2; exit 1; }
    stamp "guides in; top resume"
    (cd "$d/h/top" && librelane --docker-no-tty --dockerized --last-run --from OpenROAD.DetailedRouting \
        -e odb="$d/h/top/out/guided.odb" config.json > "$d/h/log/top_b.log" 2>&1)
fi
stamp "top end"
grep -q "Flow complete" "$d/h/log/top"*.log || { echo "run_arm: the top did not complete" >&2; exit 1; }

# 4. the row
arm=$([ "$tag" = h ] && echo H || echo H+B)
python3 "$here/runtimes.py" "$d/h/top/runs/$tag" --set N="$N" --set arm="$arm" --blocks-from "$d/h/top/config.json" | tee "$d/h/log/row.txt"
python3 "$here/runtimes.py" "$d/h/top/runs/$tag" --set N="$N" --set arm="$arm" --blocks-from "$d/h/top/config.json" --json >> "$d/results.jsonl"
stamp "done"
