#!/usr/bin/env bash
# One IDENTICAL repeat of an H+B top run, to measure run-to-run noise.
#
#   repeat_run.sh <arm_dir> <config.json> <new_tag> [wait_for_file]
#
# WHY.  §7.4's floor (decided in #903) counts a 0.021 ns setup loss against
# arm H because the study has measured run-to-run noise for WALL TIME only
# (±25 %, §8 step 7d) and never for TIMING -- and a tolerance invented after
# seeing the result is the number-after-the-data the section warns against.
# Two identical runs put a figure on it.  LibreLane pins `-or_seed 42`, so
# the layout may well be reproducible and the noise exactly zero; that is a
# hypothesis, and this is the measurement.
#
# It repeats all THREE legs, because the arm is three legs: the pre-DRT run,
# BUDA's corridors, and the resumed detailed route.  Repeating only the last
# would hold the placement fixed and measure less than the arm varies by.
# BUDA's guide file is checksummed before and after -- if the corridors
# differ between runs, the input to leg 3 is not identical and the timing
# comparison is measuring two things at once, so it says so.
set -euo pipefail
arm=${1:?usage: repeat_run.sh <arm_dir> <config.json> <new_tag> [wait_for_file]}
cfg=${2:?}; tag=${3:?}; waitfor=${4:-}
here=$(cd "$(dirname "$0")" && pwd)
cd "$arm"
if [ -n "$waitfor" ]; then
    echo "repeat: waiting for $waitfor"
    while [ ! -f "$waitfor" ]; do sleep 60; done
    echo "repeat: predecessor finished, starting"
fi
# `<arm>` is <t1a_dir>/n<N>/h, so N is one level up and T1A_DIR is two.
# Derived and CHECKED here, before the six-minute first leg: getting these
# wrong cost a queued run 36 idle minutes, because the script did the
# expensive leg first and only then handed guides.sh a path it refused.
n=$(basename "$(cd "$arm/.." && pwd)"); n=${n#n}
t1a=$(cd "$arm/../.." && pwd)
case "$n" in ''|*[!0-9]*) echo "repeat: derived N='$n' from $arm -- expected <t1a>/n<N>/h" >&2; exit 1;; esac
[ -d "$t1a/n$n/h/top" ] || { echo "repeat: derived T1A_DIR=$t1a, but $t1a/n$n/h/top is not there" >&2; exit 1; }
[ -f "$cfg" ] || { echo "repeat: no config at $cfg" >&2; exit 1; }
echo "repeat: N=$n  T1A_DIR=$t1a  tag=$tag"
before=$(md5 -q top/out/buda_bus.guide 2>/dev/null || echo none)

date +%s > "rep_${tag}.start"
if [ -d "top/runs/$tag" ] && ls "top/runs/$tag"/43-* >/dev/null 2>&1; then
    echo "repeat: leg 1 already complete for tag $tag -- resuming at the corridors"
else
(cd top && caffeinate -ims ~/.venvs/librelane/bin/librelane --docker-no-tty --dockerized \
    --run-tag "$tag" --to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting \
    "$(basename "$cfg")" > "../rep_${tag}_3a.log" 2>&1)
fi
echo "repeat: 3a done"

TAG="$tag" T1A_DIR="$t1a" "$here/guides.sh" "$n" > "rep_${tag}_guides.log" 2>&1
after=$(md5 -q top/out/buda_bus.guide)
if [ "$before" = "$after" ]; then
    echo "repeat: BUDA's corridors are byte-identical to the first run ($after)"
else
    echo "repeat: WARNING: corridors DIFFER ($before -> $after) -- leg 3's input is not identical,"
    echo "        so a timing delta is not purely run-to-run noise"
fi

ODB=$(ls -t "top/runs/$tag"/*/*.odb | head -1)
caffeinate -ims "$here/../phase0/measure/run_or.sh" "top/runs/$tag" "$here/guide_route.tcl" \
    ODB="$PWD/$ODB" GUIDE="$PWD/top/out/buda_bus.guide" OUT="$PWD/top/out" \
    > "rep_${tag}_route.log" 2>&1
echo "repeat: corridors in"

(cd top && caffeinate -ims ~/.venvs/librelane/bin/librelane --docker-no-tty --dockerized \
    --last-run --from OpenROAD.DetailedRouting -e odb="$PWD/out/guided.odb" \
    "$(basename "$cfg")" > "../rep_${tag}_3c.log" 2>&1) || true
date +%s > "rep_${tag}.end"
echo "repeat: done in $(( $(cat "rep_${tag}.end") - $(cat "rep_${tag}.start") ))s -> top/runs/$tag"
