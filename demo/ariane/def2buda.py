#!/usr/bin/env python3
"""def2buda.py — Convert a DEF floorplan to a BUDA .buda script skeleton.

Reads DIEAREA and COMPONENTS (FIXED macros) from a DEF file, groups the
macros by hierarchy depth, computes bounding boxes per group, and emits
add_block commands scaled to BUDA units.  A bus netlist stub is appended
so the user can fill in add_net / add_bus lines by hand or via a separate
netlist parser.

Usage
-----
    python3 def2buda.py <input.def> [options]

Options
-------
    --dbu N          Database units per micron (default: auto-detect or 2000)
    --scale F        BUDA units per micron  (default: 1/7 ≈ 0.143)
    --depth N        Hierarchy depth for grouping (default: 2)
    --margin N       Global corner margin in BUDA units (default: 5)
    --gap N          Minimum gap inserted between touching blocks (default: 3)
    --out FILE       Output .buda file (default: <input_stem>.buda)
    --no-layers      Skip layer stack template
    --no-nets        Skip bus netlist stub

Example
-------
    python3 def2buda.py ariane133_manual_floorplan.def --depth 2 --scale 0.143
"""

# Cygwin's newest python is 3.9, where PEP 604 unions in EVALUATED
# annotations raise at import (TypeError) -- lazy annotations keep this
# converter runnable there; the project floor stays 3.13.
from __future__ import annotations
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict


# ---------------------------------------------------------------------------
# DEF parser
# ---------------------------------------------------------------------------

def parse_dbu(text: str) -> int:
    """Return database units per micron from UNITS DISTANCE MICRONS line."""
    m = re.search(r'UNITS\s+DISTANCE\s+MICRONS\s+(\d+)', text)
    return int(m.group(1)) if m else 2000


def parse_diearea(text: str):
    """Return (x1, y1, x2, y2) in DBU."""
    m = re.search(r'DIEAREA\s+\(\s*(\d+)\s+(\d+)\s*\)\s+\(\s*(\d+)\s+(\d+)\s*\)', text)
    if not m:
        return None
    return tuple(int(v) for v in m.groups())


def parse_macros(text: str):
    """Return list of (name, cell, x, y) for all FIXED components."""
    # Strip DEF escape backslashes from bracket notation
    clean = text.replace('\\[', '[').replace('\\]', ']')
    pattern = re.compile(
        r'^\s*-\s+(\S+)\s+(\S+)\s+\+\s+FIXED\s+\(\s*(\d+)\s+(\d+)\s*\)',
        re.MULTILINE,
    )
    return [(n, c, int(x), int(y)) for n, c, x, y in pattern.findall(clean)]


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_macros(macros, depth: int):
    """Group macros into bounding boxes by the first `depth` hierarchy levels.

    Returns dict: group_name → [x1, y1, x2, y2, count].
    """
    boxes: dict[str, list] = {}
    INF = float('inf')
    for name, _cell, x, y in macros:
        parts = name.split('/')
        key = '/'.join(parts[:depth]) if len(parts) >= depth else name
        if key not in boxes:
            boxes[key] = [INF, INF, -INF, -INF, 0]
        b = boxes[key]
        b[0] = min(b[0], x)
        b[1] = min(b[1], y)
        b[2] = max(b[2], x)
        b[3] = max(b[3], y)
        b[4] += 1
    return boxes


# ---------------------------------------------------------------------------
# Block layout helpers
# ---------------------------------------------------------------------------

def make_buda_name(hierarchy_key: str) -> str:
    """Convert a hierarchy key like 'i_cache_subsystem/i_icache' to 'u_icache'."""
    leaf = hierarchy_key.split('/')[-1]
    # Remove common RTL prefixes (i_, gen_, inst_) and replace with u_
    leaf = re.sub(r'^(i_|gen_|inst_)', '', leaf)
    return 'u_' + leaf


def scale_bbox(x1_dbu, y1_dbu, x2_dbu, y2_dbu, dbu: int, scale: float, gap: int):
    """Convert a DBU bbox to BUDA integer units, adding half-gap padding."""
    x1 = max(0, round(x1_dbu / dbu * scale) - gap // 2)
    y1 = max(0, round(y1_dbu / dbu * scale) - gap // 2)
    x2 = round(x2_dbu / dbu * scale) + gap // 2
    y2 = round(y2_dbu / dbu * scale) + gap // 2
    return x1, y1, x2, y2


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------

LAYER_TEMPLATE = """\
# ── Layer stack ──────────────────────────────────────────────
# Edit overhead% to match your process power-grid density.
def_layer 3 M3 V TOP 0.0  span_max 60
def_layer 4 M4 H TOP 0.0  span_max 80
def_layer 5 M5 V TOP 0.0
def_layer 6 M6 H TOP 0.0
def_layer 7 M7 V TOP 0.0  span_min 60
"""

NETLIST_STUB = """\
# ── Netlist ──────────────────────────────────────────────────
# TODO: fill in add_net / add_bus commands from your netlist.
# Format:
#   add_bus  <prefix>[<N>]  <driver_block>.<port>  <receiver_block>.<port>
#   add_net  <name>         <driver_block>.<port>  <receiver_block>.<port>
#
# Example (CVA6 / Ariane fetch path):
#   add_bus  fetch_data[20]  u_icache.rdata  u_frontend.idata
#   add_bus  fetch_addr[8]   u_frontend.pc   u_icache.addr
"""

PIPELINE_TEMPLATE = """\
# ── Stage 1: bundle ──────────────────────────────────────────
run_bundler strict

# ── Stage 2: topologies ──────────────────────────────────────
generate_topologies

# ── Stage 3: global route ────────────────────────────────────
run_planner 5

# ── Stage 4: abstract track placement ────────────────────────
run_nuts 2.0

# ── Visualise ────────────────────────────────────────────────
visualize
"""


def generate_buda(
    def_path: Path,
    *,
    dbu: int | None = None,
    scale: float = 1 / 7,
    depth: int = 2,
    margin: int = 5,
    gap: int = 3,
    include_layers: bool = True,
    include_nets: bool = True,
) -> str:
    text = def_path.read_text()

    detected_dbu = parse_dbu(text)
    dbu = dbu or detected_dbu

    die = parse_diearea(text)
    macros = parse_macros(text)

    groups = group_macros(macros, depth)

    die_w = round(die[2] / dbu * scale) if die else '?'
    die_h = round(die[3] / dbu * scale) if die else '?'

    lines = [
        f'# BUDA script generated from {def_path.name}',
        f'# Source: {def_path}',
        f'# DBU: {dbu}/µm  scale: 1 BUDA unit ≈ {1/scale:.1f} µm'
        f'  die ≈ {die_w} × {die_h} BUDA units',
        f'# Macro groups: {len(groups)} (hierarchy depth {depth})',
        f'# Total FIXED macros parsed: {len(macros)}',
        '',
    ]

    if include_layers:
        lines.append(LAYER_TEMPLATE)

    lines.append(f'# ── Global corner margin ─────────────────────────────────────')
    lines.append(f'corner_margin dx {margin}')
    lines.append('')

    lines.append('# ── Floorplan ────────────────────────────────────────────────')
    lines.append(f'# Blocks derived from FIXED macro bounding boxes (÷{1/scale:.0f} µm/unit)')
    lines.append('')

    for key in sorted(groups):
        b = groups[key]
        x1, y1, x2, y2 = scale_bbox(b[0], b[1], b[2], b[3], dbu, scale, gap)
        buda_name = make_buda_name(key)
        count = b[4]
        lines.append(
            f'add_block {buda_name:<20s} {x1:4d} {y1:4d} {x2:4d} {y2:4d}'
            f'  # {count} macros  [{key}]'
        )

    lines.append('')

    if include_nets:
        lines.append(NETLIST_STUB)

    lines.append(PIPELINE_TEMPLATE)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Convert a DEF floorplan to a BUDA .buda script skeleton.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Usage')[0].strip(),
    )
    ap.add_argument('def_file', type=Path, help='Input DEF file')
    ap.add_argument('--dbu', type=int, default=None,
                    help='DBU per micron (default: auto-detect)')
    ap.add_argument('--scale', type=float, default=1/7,
                    help='BUDA units per micron (default: 1/7)')
    ap.add_argument('--depth', type=int, default=2,
                    help='Hierarchy depth for grouping (default: 2)')
    ap.add_argument('--margin', type=int, default=5,
                    help='Global corner margin in BUDA units (default: 5)')
    ap.add_argument('--gap', type=int, default=3,
                    help='Half-gap padding added around each block (default: 3)')
    ap.add_argument('--out', type=Path, default=None,
                    help='Output .buda file (default: <input_stem>.buda)')
    ap.add_argument('--no-layers', action='store_true',
                    help='Omit layer stack template')
    ap.add_argument('--no-nets', action='store_true',
                    help='Omit bus netlist stub')
    args = ap.parse_args()

    if not args.def_file.exists():
        print(f'Error: {args.def_file} not found', file=sys.stderr)
        sys.exit(1)

    out_path = args.out or args.def_file.with_suffix('.buda')

    buda = generate_buda(
        args.def_file,
        dbu=args.dbu,
        scale=args.scale,
        depth=args.depth,
        margin=args.margin,
        gap=args.gap,
        include_layers=not args.no_layers,
        include_nets=not args.no_nets,
    )

    out_path.write_text(buda)
    print(f'Written: {out_path}')

    # Also print a summary to stdout
    macros = parse_macros(args.def_file.read_text())
    groups = group_macros(macros, args.depth)
    dbu = args.dbu or parse_dbu(args.def_file.read_text())
    die = parse_diearea(args.def_file.read_text())
    if die:
        print(f'Die: {die[2]/dbu:.1f} × {die[3]/dbu:.1f} µm  '
              f'({round(die[2]/dbu*args.scale)} × {round(die[3]/dbu*args.scale)} BUDA units)')
    print(f'Groups ({len(groups)}):')
    for key in sorted(groups):
        b = groups[key]
        x1, y1, x2, y2 = scale_bbox(b[0], b[1], b[2], b[3], dbu, args.scale, args.gap)
        print(f'  {make_buda_name(key):<20s}  n={b[4]:3d}  '
              f'bbox=({x1},{y1})-({x2},{y2})  [{key}]')


if __name__ == '__main__':
    main()
