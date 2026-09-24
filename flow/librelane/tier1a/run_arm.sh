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
# appended to $d/results.jsonl.  Environment knobs:
#   REUSE_BLOCKS=1        skip hardening + notch (every block's final/ and
#                         .notch.lef must exist) -- a TOP-only re-run
#   TOPTAG=<tag>          the top's run tag (default = the arm tag), so a
#                         re-run keeps the earlier top beside it
#   TOP_SET="K=V K=V"     JSON values patched into top/config.json after
#                         harm.sh writes it (`FP_TAPCELL_DIST=5`)
#   LL_HOME=<dir>         a home OUTSIDE $HOME for librelane's unconditional
#                         home mount (needs PDK_ROOT outside $HOME too; see
#                         the LL definition below for why and the copy recipe)
# Requires `librelane` on PATH (the venv) and Docker running.  Every LibreLane call passes `--docker-no-tty`: this runs
# unattended, and `--dockerized` otherwise asks Docker for a terminal it does
# not have ("cannot attach stdin to a TTY-enabled container").
set -euo pipefail
N=${1:?usage: run_arm.sh N h|hb [harm.sh args]}; shift
tag=${1:?usage: run_arm.sh N h|hb [harm.sh args]}; shift
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
# ABSOLUTE from here on: the block and top legs `cd` into the tree, so a
# relative T1A_DIR (the `T1A_DIR=../..` form the generated README hands a
# comparison root) would resolve its logs from the wrong place and reach
# docker as an invalid mount source (Codex on #952; notch.sh has the same rule).
t1a=$(cd "${T1A_DIR:-$here}" 2>/dev/null && pwd) || { echo "run_arm: T1A_DIR=${T1A_DIR:-} is not a directory" >&2; exit 1; }
d="$t1a/n$N"
[ -f "$d/tpu.def" ] || { echo "run_arm: $d has no emitted set" >&2; exit 1; }
case $tag in
    h)  pins=() ;;
    hb) pins=(--pins pins); [ -d "$d/pins" ] || { echo "run_arm: $d/pins missing -- run pins.sh $N first" >&2; exit 1; } ;;
    *)  echo "run_arm: tag must be h or hb" >&2; exit 1 ;;
esac
export PDK_ROOT="${PDK_ROOT:-$HOME/.ciel}"
# `--dockerized` mounts the COMMON path of the working directory and PDK_ROOT.
# Run from a tree under $HOME with the PDK in ~/.ciel and that is all of
# $HOME -- every file change under it is injected into the VM, and on a busy
# machine the file-sharing layer stalls ("inotify injection stalled", the
# containers sit at 0 % CPU).  Mount the arm's tree explicitly instead, so a
# T1A_DIR outside $HOME mounts only itself and the PDK.
LL=(librelane --docker-no-tty --docker-mount "$t1a" --dockerized)
# That is NOT enough: librelane/container.py mounts `Path.home()` UNCONDITIONALLY,
# so every run still carries `-v $HOME:$HOME`, and on 2026-09-23 a restarted
# VM was stalling again three minutes later with that mount alone (`init.log`:
# "inotify injection stalled for 1m0s" every minute) while the same resume ran
# ten steps in a minute without it.  LL_HOME=<dir outside $HOME> makes
# librelane see THAT as home; DOCKER_CONFIG keeps the docker CLI's Desktop
# context, which lives in the real ~/.docker.  PDK_ROOT must be outside $HOME
# too (copy: `cp -RL ~/.ciel/sky130A <dir>/pdk/sky130A`, then ciel fetches its
# own store beside it on first use), or the PDK mount brings $HOME back.
LLENV=()
if [ -n "${LL_HOME:-}" ]; then
    case $PDK_ROOT in "$HOME"|"$HOME"/*)
        echo "run_arm: LL_HOME is set but PDK_ROOT=$PDK_ROOT is under \$HOME, which the PDK mount would bring back -- copy the PDK out (cp -RL ~/.ciel/sky130A <dir>/pdk/sky130A) and set PDK_ROOT=<dir>/pdk" >&2
        exit 1 ;;
    esac
    mkdir -p "$LL_HOME"
    LLENV=(env HOME="$LL_HOME" DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}")
    LL=("${LLENV[@]}" "${LL[@]}")
fi
# The same prefix goes on EVERY containerised helper, not only librelane:
# notch.sh and run_or.sh each `docker run` with `-v $HOME:$HOME` of their own
# (and mount a tree outside it explicitly), so without it an H run could stall
# at the notch and an H+B run at the guide install, after the expensive
# LibreLane legs (Codex on #952).  guides.sh runs on the host.
mkdir -p "$d/h/log"
stages="$d/h/stages.txt"
stamp() { echo "$(date +%s) $1" >> "$stages"; echo "run_arm: $(date '+%H:%M:%S') $1"; }
# A LibreLane leg that fails must be SAID, with its last error lines, before
# `set -e` ends the run -- the first silent exit cost a watch that saw only
# "top start" (a DPL-0036 legalization failure two minutes in).
ll_run() {   # ll_run <log> <librelane args...>
    local log=$1; shift
    if ! "${LL[@]}" "$@" > "$log" 2>&1; then
        stamp "LIBRELANE FAILED ($log)"
        tr '\r' '\n' < "$log" | grep -E "ERROR|\[[A-Z]+-[0-9]+\]" | tail -6 | cut -c1-200 | sed 's/^/run_arm:   /'
        exit 1
    fi
}

TOPTAG=${TOPTAG:-$tag}
stamp "harm.sh start"
"$here/harm.sh" "$N" ${pins[@]+"${pins[@]}"} "$@"
cells=$(sed -n 's/^MACRO \([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' "$d/tpu.lef")
if [ -n "${TOP_SET:-}" ]; then
    python3 - "$d/h/top/config.json" $TOP_SET <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p))
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1); c[k] = json.loads(v)
    print(f"run_arm: top config {k} = {v}")
json.dump(c, open(p, "w"), indent=4)
PY
fi
if [ "${REUSE_BLOCKS:-0}" = 1 ]; then
    for c in $cells; do
        [ -f "$d/h/$c/runs/h/final/lef/$c.lef" ] || { echo "run_arm: REUSE_BLOCKS but $c has no final/lef" >&2; exit 1; }
    done
    stamp "blocks reused; notch reused"
else

# 1. harden the blocks, in parallel (the wall figure is the batch)
stamp "blocks start"
pids=()
for c in $cells; do
    (cd "$d/h/$c" && ll_run "$d/h/log/$c.log" --run-tag h config.json) &
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
# met2 AND met3: the compact H+B arm's clean row needed a met3 notch patched
# too (7.5 -- two KLayout m3.2 errors at one PE's a_in pins, both edges of an
# abstract hole on met3), and notch.sh prices a layer with no notch at one
# more pass reporting zero pieces, so the recorded met2-only arms are
# unchanged by naming it.
${LLENV[@]+"${LLENV[@]}"} "$here/notch.sh" "$N" --layers met2,met3 > "$d/h/log/notch.log" 2>&1
stamp "notch end"
fi

# 3. the top
stamp "top start"
if [ "$tag" = h ]; then
    (cd "$d/h/top" && ll_run "$d/h/log/top_$TOPTAG.log" --run-tag "$TOPTAG" config.json)
else
    (cd "$d/h/top" && ll_run "$d/h/log/top_${TOPTAG}_a.log" --run-tag "$TOPTAG" \
        --to OpenROAD.DetailedRouting --skip OpenROAD.DetailedRouting config.json)
    stamp "top cut at DetailedRouting; guides start"
    (cd "$here" && TAG="$TOPTAG" T1A_DIR="$t1a" ./guides.sh "$N" > "$d/h/log/guides_$TOPTAG.log" 2>&1)
    ODB=$(ls -t "$d"/h/top/runs/"$TOPTAG"/*/*.odb | head -1)
    (cd "$d/h" && ${LLENV[@]+"${LLENV[@]}"} "$root/flow/librelane/phase0/measure/run_or.sh" top/runs/"$TOPTAG" "$here/guide_route.tcl" \
        ODB="$ODB" GUIDE="$d/h/top/out/buda_bus.guide" OUT="$d/h/top/out" > "$d/h/log/guide_route_$TOPTAG.log" 2>&1)
    grep -q "wrote" "$d/h/log/guide_route_$TOPTAG.log" || { echo "run_arm: guide_route.tcl wrote nothing (see $d/h/log/guide_route_$TOPTAG.log)" >&2; exit 1; }
    stamp "guides in; top resume"
    # a DEFERRED error (an LVS count) exits non-zero AFTER final/ is written;
    # that is a verdict for the row, so this leg is allowed to fail
    (cd "$d/h/top" && ("${LL[@]}" --last-run --from OpenROAD.DetailedRouting \
        -e odb="$d/h/top/out/guided.odb" config.json > "$d/h/log/top_${TOPTAG}_b.log" 2>&1 || true))
fi
stamp "top end"
# Did the top COMPLETE?  Read the leg that ends the flow -- for H+B the
# resume leg's log, never the `_a` cut, whose own "Flow complete" would pass
# a `_b` killed before signoff (Codex on #952) -- and accept the two shapes
# a finished run has: "Flow complete", or the DEFERRED-error ending, which
# prints no "Flow complete" at all (measured: a run ending on a hold
# violation, exit 2, final/ written) but is a verdict for the row.  Either
# way final/metrics.json must exist, which a killed run never writes.
toplog="$d/h/log/top_$TOPTAG.log"; [ "$tag" = hb ] && toplog="$d/h/log/top_${TOPTAG}_b.log"
if ! { tr '\r' '\n' < "$toplog" | grep -q "Flow complete\|deferred errors"; } \
   || [ ! -f "$d/h/top/runs/$TOPTAG/final/metrics.json" ]; then
    echo "run_arm: the top did not complete (see $toplog)" >&2; exit 1
fi

# 4. the row
arm=$([ "$tag" = h ] && echo H || echo H+B)
python3 "$here/runtimes.py" "$d/h/top/runs/$TOPTAG" --set N="$N" --set arm="$arm" --blocks-from "$d/h/top/config.json" | tee "$d/h/log/row_$TOPTAG.txt"
python3 "$here/runtimes.py" "$d/h/top/runs/$TOPTAG" --set N="$N" --set arm="$arm" --blocks-from "$d/h/top/config.json" --json >> "$d/results.jsonl"
stamp "done"
