#!/usr/bin/env bash
# Tier 1a of the LibreLane study (docs/internal/librelane_hier_flow.md §11
# item 13, issue #896): close the ABSTRACTION NOTCH in every hardened
# block's LEF, so the top run reads the macro's real metal.
#
#   flow/librelane/tier1a/notch.sh N [--layers met2] [--arm h]
#
# Run it from anywhere, AFTER the blocks have hardened (README.md step 1)
# and BEFORE the top runs.  It is not optional and cannot be quietly
# skipped: `harm.py` points the top's `MACROS.<cell>.lef` at the
# `<cell>.notch.lef` this writes, so a top run without it stops at once on a
# LEF that is not there -- which is the whole reason the fix moved out of
# the hand recipe.  For the same reason each cell's previous `.notch.lef` is
# REMOVED before that cell is reprocessed: a failure must not leave the
# stale one standing for the top to pick up.
#
# WHY.  Magic's `final/lef/<cell>.lef` abstracts the block rect by rect and
# leaves a notch uncovered where a pin rect ends and the OBS blanket begins.
# The macro's real metal sits in it, the top's router reads the notch as
# free and overhangs a wire into it, and the result is an `m2.2` marker
# INSIDE the macro box that the block's own DRC cannot see -- the partner
# shape is the top's wire.  Measured on N=8 H+B: KLayout DRC 2 -> 0, Magic
# overlaps 0, PDN clean, top wire +11 um on 300 mm (+0.004 %).
#
# TWO TOOLS, IN THIS ORDER, per cell:
#
#   rectify_gds.py   the macro's metal as RECTANGLES.  `notch_obs.py` needs
#                    rectangles and REFUSES a polygon rather than take its
#                    bbox (a bbox over-claims), but Magic streams routed
#                    metal as rectilinear BOUNDARYs -- 982 of them on
#                    `pe_cell` alone -- so without this every cell is
#                    refused.  A trapezoid decomposition of an axis-aligned
#                    polygon IS rectangles over the same area, and the
#                    script compares the region area before and after and
#                    refuses to write if they differ.
#   notch_obs.py     the boolean difference between that metal and what the
#                    LEF claims (every pin rect and every OBS rect), added
#                    to the OBS.  Exactly the metal the abstract omits and
#                    nothing else, so every place the top legitimately
#                    routes the layer stays free -- which the two blanket
#                    fixes did not (§11 item 13: the whole abstract cost
#                    125,800 PDN violations, a met2 blanket 6,233 Magic
#                    overlaps).
#
# THE LAYER LIST is met2 by default because that is the only layer a marker
# has appeared on.  `--layers met2,met3` runs the pair, chaining both tools
# so the second reads the first's output; the cost of naming a layer that
# has no notch is one more pass reporting zero pieces.
#
# RECTIFY RUNS IN THE CONTAINER, as a KLayout batch script (`klayout -b -r`,
# values through `-rd`), in the same LibreLane image the flow uses -- there
# is no host KLayout in this recipe and `pya` is KLayout's own module.
# `notch_obs.py` is plain Python and runs on the host.
set -euo pipefail
N=${1:?usage: notch.sh N [--layers met2] [--arm h]   (after the blocks harden)}; shift || true
layers=met2
arm=h
# `--arm` because the arm directory is not always `h`: `harm.py --out` puts one
# wherever it is told, and the study already does that -- `n2/hs` and
# `hb4/n4/hs` are the H+size arms sitting beside their H+B twin in the SAME
# emitted set.  Hard-coding `h` left those arms with no way to run this at all,
# so the only route was the per-cell hand recipe the README offers as a
# fallback for a cell the decomposition cannot handle -- which is not what an
# arm in the wrong directory is.
while [ $# -ge 2 ]; do
    case $1 in
        --layers) layers=$2; shift 2 ;;
        --arm)    arm=$2;    shift 2 ;;
        *) break ;;
    esac
done
if [ $# -ne 0 ]; then echo "notch.sh: unexpected arguments: $*" >&2; exit 1; fi
# An EMPTY layer list is refused HERE rather than run.  Zero passes leaves
# `src`/`inlef` pointing at the cell's own hardened `.gds` and Magic's
# `.lef`, and the moves below then RENAME the two deliverables to the
# derived names and report success -- the top's configured GDS gone and a
# `.notch.lef` that is the unpatched abstract (Codex #909, reproduced).  The
# move is guarded on a completed pass as well: one refusal is enough, and
# neither guard should be the only one.
layer_list=$(echo "$layers" | tr ',' ' ')
if [ -z "$(echo $layer_list)" ]; then
    echo "notch.sh: --layers '$layers' names no layer -- met2 is the default; there is nothing to patch" >&2
    exit 1
fi
here=$(cd "$(dirname "$0")" && pwd)
# T1A_DIR overrides where the design lives (the tests use a temp dir), as in gen.sh.
d="${T1A_DIR:-$here}/n$N"
h="$d/$arm"
[ -f "$d/tpu.lef" ] || { echo "notch.sh: $d has no tpu.lef -- run gen.sh $N first" >&2; exit 1; }
[ -d "$h" ] || { echo "notch.sh: no $h -- run ./harm.sh $N first" \
    "${arm:+(or --arm <dir> if the arm is not in h/)}" >&2; exit 1; }
cells=$(sed -n 's/^MACRO \([A-Za-z_][A-Za-z0-9_]*\).*/\1/p' "$d/tpu.lef")
if [ -z "$cells" ]; then echo "notch.sh: $d/tpu.lef declares no MACRO" >&2; exit 1; fi
: "${LIBRELANE_IMAGE:=ghcr.io/librelane/librelane:3.0.11}"
# Mount what the container has to SEE, and check it rather than assume it:
# the image sees only what is bind-mounted, and a run tree outside $HOME is
# invisible in there with nothing but a path error to say so.  $HOME covers
# the normal layout; a design or a checkout elsewhere gets its own mount.
mounts=(-v "$HOME:$HOME")
for p in "$d" "$here"; do
    case "$p" in "$HOME"/*) ;; *) mounts+=(-v "$p:$p") ;; esac
done

fail=()
for c in $cells; do
    gdsdir="$h/$c/runs/h/final/gds"; lefdir="$h/$c/runs/h/final/lef"
    out="$lefdir/$c.notch.lef"
    rm -f "$out"                       # never leave a stale fix for the top
    if [ ! -f "$gdsdir/$c.gds" ] || [ ! -f "$lefdir/$c.lef" ]; then
        echo "notch.sh: $c is not hardened ($gdsdir/$c.gds or $lefdir/$c.lef missing)" >&2
        fail+=("$c (not hardened)"); continue
    fi
    ok=1
    src="$gdsdir/$c.gds"; inlef="$lefdir/$c.lef"; i=0; tmp=()
    for layer in $layer_list; do
        i=$((i + 1))
        ldef=$(PYTHONPATH="$here" python3 -c \
            'import sys, notch_obs; print(*notch_obs.SKY130_GDS[sys.argv[1]])' "$layer" 2>/dev/null) || {
            echo "notch.sh: $c: no GDS layer map for $layer (notch_obs.py knows ${layers})" >&2
            ok=0; break; }
        rgds="$gdsdir/$c.rect$i.gds"; nlef="$lefdir/$c.notch$i.lef"
        tmp+=("$rgds" "$nlef")
        # shellcheck disable=SC2086
        if ! docker run --rm "${mounts[@]}" -w "$h" "$LIBRELANE_IMAGE" \
                klayout -b -r "$here/rectify_gds.py" \
                -rd gds="$src" -rd lnum=${ldef% *} -rd ldt=${ldef#* } -rd out="$rgds"; then
            echo "notch.sh: $c: rectify_gds.py refused on $layer -- see README.md step 2 for the fallback" >&2
            ok=0; break
        fi
        if ! python3 "$here/notch_obs.py" "$rgds" "$inlef" "$nlef" --layer "$layer" \
                --json "$lefdir/$c.notch.$layer.json"; then
            echo "notch.sh: $c: notch_obs.py refused on $layer -- see README.md step 2 for the fallback" >&2
            ok=0; break
        fi
        src="$rgds"; inlef="$nlef"
    done
    if [ "$ok" = 1 ] && [ "$i" -gt 0 ]; then
        mv "$src" "$gdsdir/$c.rect.gds"; mv "$inlef" "$out"
    else
        fail+=("$c")
    fi
    for f in "${tmp[@]+"${tmp[@]}"}"; do rm -f "$f"; done
done

if [ ${#fail[@]} -ne 0 ]; then
    echo "" >&2
    echo "notch.sh: REFUSING -- ${#fail[@]} cell(s) have no patched abstract: ${fail[*]}" >&2
    echo "  Do NOT run the top: its MACROS entries name <cell>.notch.lef, so it would stop on the" >&2
    echo "  missing file rather than route against Magic's abstract by accident.  $h/README.md" >&2
    echo "  step 2 has the per-cell hand recipe and what to do with a cell that cannot be fixed." >&2
    exit 1
fi
n=$(echo "$cells" | wc -l | tr -d ' ')
echo "tier1a: N=$N $arm -> $n patched abstract(s) ($layers), one <cell>.notch.lef per cell; next: README.md step 3"
