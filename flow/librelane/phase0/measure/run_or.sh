#!/usr/bin/env bash
# Run an OpenROAD Tcl script INSIDE the LibreLane container, on a LibreLane run.
#
#   run_or.sh <run_dir> <script.tcl> [ENV=value ...]
#
# `librelane --dockerized` prints the exact `docker run` it uses ("Running
# containerized command:"); this mirrors it -- same image, your home and the
# PDK root mounted at the same paths, cwd as workdir -- with `openroad -exit`
# in place of the librelane entrypoint.  The run's resolved.json supplies the
# layer names and LEF paths the scripts need, so nothing here is typed twice.
set -euo pipefail
run_dir=$(cd "$1" && pwd); script=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); shift 2
: "${LIBRELANE_IMAGE:=ghcr.io/librelane/librelane:3.0.11}"
: "${PDK_ROOT:=$HOME/.ciel}"
resolved="$run_dir/resolved.json"
[ -f "$resolved" ] || { echo "no $resolved -- is $run_dir a LibreLane run directory?" >&2; exit 2; }
# One value per line: RT_MIN_LAYER RT_MAX_LAYER TECH_LEF CELL_LEFS DESIGN_NAME.
# A separate script rather than an inline heredoc, and run BEFORE the array
# is read: inside `while read < <(...)` a failure is silent and leaves a short
# array, which then surfaces as an unbound element at the docker line, far
# from the cause.  Here a failure stops the script with the reader's message.
# (A `while read` loop rather than `readarray`, which is bash 4: macOS ships
# bash 3.2, and this recipe is written for macOS -- measured 2026-09-05,
# `readarray: command not found` on the first run.)
# read_resolved.py lives beside THIS script, not beside the Tcl: a scratch
# Tcl in another directory is a legitimate use (measured: a one-off in out/
# failed with "can't open file .../out/read_resolved.py").
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cfg_text=$(python3 "$here/read_resolved.py" "$resolved") || exit $?
cfg=(); while IFS= read -r line; do cfg+=("$line"); done <<< "$cfg_text"
extra=(); for kv in "$@"; do extra+=(-e "$kv"); done
# `-t` only when there is a terminal to give it: `docker run -t` from a
# script or a CI step fails with "the input device is not a TTY", the same
# way `librelane --dockerized` does without `--docker-no-tty`.
# (The `${a[@]+"${a[@]}"}` spelling is how an array that may be EMPTY is
# expanded under `set -u` on bash 3.2, where a bare "${a[@]}" is "unbound".)
tty=(); [ -t 0 ] && tty=(-t)
docker run --rm ${tty[@]+"${tty[@]}"} \
  -v "$HOME:$HOME" -v "$PDK_ROOT:$PDK_ROOT" -e "PDK_ROOT=$PDK_ROOT" \
  -v "$PWD:$PWD" -w "$PWD" \
  -e "RUN_DIR=$run_dir" -e "RT_MIN_LAYER=${cfg[0]}" -e "RT_MAX_LAYER=${cfg[1]}" \
  -e "TECH_LEF=${cfg[2]}" -e "CELL_LEFS=${cfg[3]}" -e "DESIGN_NAME=${cfg[4]}" \
  ${extra[@]+"${extra[@]}"} "$LIBRELANE_IMAGE" openroad -exit -no_splash "$script"
