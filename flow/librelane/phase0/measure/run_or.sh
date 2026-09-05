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
# One value per line: RT_MIN_LAYER RT_MAX_LAYER TECH_LEF CELL_LEFS(space-joined) DESIGN_NAME
readarray -t cfg < <(python3 - "$resolved" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print(c["RT_MIN_LAYER"]); print(c["RT_MAX_LAYER"])
tl = c["TECH_LEFS"]; print(tl[c.get("DEFAULT_CORNER", "nom_tt_025C_1v80")] if isinstance(tl, dict) else tl)
cl = c["CELL_LEFS"]; print(" ".join(cl) if isinstance(cl, list) else cl)
print(c["DESIGN_NAME"])
PY
)
extra=(); for kv in "$@"; do extra+=(-e "$kv"); done
docker run --rm -t \
  -v "$HOME:$HOME" -v "$PDK_ROOT:$PDK_ROOT" -e "PDK_ROOT=$PDK_ROOT" \
  -v "$PWD:$PWD" -w "$PWD" \
  -e "RUN_DIR=$run_dir" -e "RT_MIN_LAYER=${cfg[0]}" -e "RT_MAX_LAYER=${cfg[1]}" \
  -e "TECH_LEF=${cfg[2]}" -e "CELL_LEFS=${cfg[3]}" -e "DESIGN_NAME=${cfg[4]}" \
  "${extra[@]}" "$LIBRELANE_IMAGE" openroad -exit -no_splash "$script"
