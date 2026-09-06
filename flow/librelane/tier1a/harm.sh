#!/usr/bin/env bash
# Tier 1a of the LibreLane study (docs/internal/librelane_hier_flow.md §7.2):
# the systolic array at size N as a HIERARCHICAL LibreLane design WITHOUT
# BUDA -- arm H.  gen.sh N must have run first (it emits the set).
#
#   flow/librelane/tier1a/harm.sh N
#
# From n<N>/ (tpu_rtl.v + tpu.def + tpu.lef) writes n<N>/h/: one
# block-hardening directory per leaf cell type (pe_cell, feed_cell,
# wbuf_cell, acc_cell -- its module cut out of tpu_rtl.v, a fixed die of
# exactly its LEF SIZE, pins by LibreLane's own placer), top/ (tpu_rtl.v
# minus the leaf bodies, DIE_AREA from the DEF, a MACROS entry mapping every
# DEF component to its location -- `row_0/pe_0` becomes the flattened
# `row_0.pe_0` -- and PDN_* offsets/pitches derived from the array's pitch
# so every macro sits at one strap phase), predicted_lef/ for a dry run of
# pdn_phase.py, and README.md with the exact next commands.  The rules and
# every check are in harm.py's docstring; a set of a shape it did not expect
# exits 1 naming what did not match.  Then:
#
#   cd flow/librelane/tier1a/n<N>/h && cat README.md      # harden, check, top, account
set -euo pipefail
N=${1:?usage: harm.sh N   (after gen.sh N)}; shift || true
if [ $# -ne 0 ]; then echo "harm.sh: unexpected arguments: $*" >&2; exit 1; fi
here=$(cd "$(dirname "$0")" && pwd)
# T1A_DIR overrides where the design lives (the tests use a temp dir), as in gen.sh.
d="${T1A_DIR:-$here}/n$N"
if [ ! -f "$d/tpu.def" ] || [ ! -f "$d/tpu.lef" ] || [ ! -f "$d/tpu_rtl.v" ]; then
    echo "harm.sh: $d has no emitted set (tpu.def/tpu.lef/tpu_rtl.v) -- run gen.sh $N first" >&2
    exit 1
fi
python3 "$here/harm.py" "$d"
echo "tier1a: N=$N -> $d/h  (4 block dirs + top/ + predicted_lef/ + README.md)"
