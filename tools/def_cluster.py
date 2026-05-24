#!/usr/bin/env python3
"""
def_cluster.py — Spatial net clustering from placed DEF + LEF.

Reads a placed DEF and its LEF, computes the physical centre of every
signal-net pin, then clusters nets whose pin-centroids are within a
configurable distance (DBSCAN epsilon).  Each cluster becomes a candidate
BUDA bundle; its busterm is the bounding box of all pins in the cluster.

Usage:
    python3 def_cluster.py <design.input.def> <design.input.lef> [options]

Options:
    --eps   EPSILON   Clustering radius in µm (default: 10.0)
    --min   MIN_NETS  Minimum nets per cluster to report (default: 3)
    --out   FILE      Write busterms to FILE in BUDA add_block format
    --hist            Print fanout and cluster-size histograms
"""

import argparse, re, sys, math
from collections import defaultdict

UNITS = 2000  # DEF units per µm (overridden by UNITS line in DEF)

# ── LEF parser ────────────────────────────────────────────────────────────────

def parse_lef(lef_path):
    """Return {cell_type: {pin_name: (cx, cy)}} — signal pin centres only."""
    pins = {}  # cell → {pin: (cx, cy)}
    with open(lef_path) as f:
        content = f.read()

    for macro_m in re.finditer(r'MACRO (\S+)(.*?)END \1', content, re.DOTALL):
        cell = macro_m.group(1)
        cell_body = macro_m.group(2)
        cell_pins = {}
        for pin_m in re.finditer(r'PIN (\S+)(.*?)END \1', cell_body, re.DOTALL):
            pin_name = pin_m.group(1)
            pin_body = pin_m.group(2)
            # skip power/ground pins
            use_m = re.search(r'USE\s+(\S+)\s*;', pin_body)
            if use_m and use_m.group(1) in ('POWER', 'GROUND', 'CLOCK'):
                continue
            # collect all RECT geometries on any signal layer
            rects = re.findall(
                r'RECT\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*;',
                pin_body)
            if not rects:
                continue
            xs = [(float(x1) + float(x2)) / 2 for x1, y1, x2, y2 in rects]
            ys = [(float(y1) + float(y2)) / 2 for x1, y1, x2, y2 in rects]
            cell_pins[pin_name] = (sum(xs) / len(xs), sum(ys) / len(ys))
        if cell_pins:
            pins[cell] = cell_pins
    return pins

# ── DEF parser ────────────────────────────────────────────────────────────────

def parse_def(def_path):
    """Return (units, die, components, nets).

    components: {inst_name: (cell_type, x_def, y_def, orient)}
    nets:       {net_name: [(inst_name, pin_name), ...]}  (signal nets only)
    die:        (x2_um, y2_um)
    """
    with open(def_path) as f:
        content = f.read()

    # UNITS
    global UNITS
    um = re.search(r'UNITS DISTANCE MICRONS (\d+)', content)
    if um:
        UNITS = int(um.group(1))

    # DIEAREA
    da = re.search(r'DIEAREA \( \d+ \d+ \) \( (\d+) (\d+) \)', content)
    die = (int(da.group(1)) / UNITS, int(da.group(2)) / UNITS) if da else (0, 0)

    # COMPONENTS
    components = {}
    comp_sec = re.search(r'^COMPONENTS \d+ ;(.*?)^END COMPONENTS',
                         content, re.DOTALL | re.MULTILINE)
    if comp_sec:
        for m in re.finditer(
                r'- (\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+(\S+)',
                comp_sec.group(1)):
            inst, cell, x, y, orient = m.groups()
            components[inst] = (cell, int(x), int(y), orient)

    # NETS — signal nets only (skip SPECIALNETS)
    nets = {}
    nets_sec = re.search(r'^NETS \d+ ;(.*?)^END NETS',
                         content, re.DOTALL | re.MULTILINE)
    if nets_sec:
        # Split on net boundaries
        for net_m in re.finditer(r'- (\S+)\s+(.*?)(?=\n\s*-\s|\nEND)', nets_sec.group(1), re.DOTALL):
            net_name = net_m.group(1)
            body = net_m.group(2)
            pins = re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)', body)
            # skip primary I/O pins (inst == 'PIN')
            cell_pins = [(i, p) for i, p in pins if i != 'PIN']
            if cell_pins:
                nets[net_name] = cell_pins
    return UNITS, die, components, nets

# ── Pin-position geometry ─────────────────────────────────────────────────────

# Orientation transforms for row-based placement.
# Standard cell orientations: N, S, FN, FS, E, W, FE, FW
# For NanGate45 (horizontal rows), only N/S/FN/FS appear in practice.
# Transform: cell_pin_pos(cx, cy, cell_w, cell_h, orient) → (px, py)
def transform_pin(px_lef, py_lef, cell_w, cell_h, orient):
    """Map LEF pin centre to placed coordinates given orientation."""
    if orient == 'N':
        return px_lef, py_lef
    elif orient == 'S':
        return cell_w - px_lef, cell_h - py_lef
    elif orient == 'FN':   # flip X
        return cell_w - px_lef, py_lef
    elif orient == 'FS':   # flip X + rotate 180° = flip Y
        return px_lef, cell_h - py_lef
    elif orient == 'E':
        return cell_h - py_lef, px_lef
    elif orient == 'W':
        return py_lef, cell_w - px_lef
    elif orient == 'FE':
        return py_lef, px_lef
    elif orient == 'FW':
        return cell_h - py_lef, cell_w - px_lef
    return px_lef, py_lef  # fallback

def compute_pin_positions(components, nets, lef_pins):
    """Return {net_name: [(px_um, py_um), ...]} for all signal nets."""
    # cell dimensions from LEF (need SIZE line — approximated from pin extents)
    # We store per-cell dimensions during LEF parse; for now approximate cell
    # size from the placed-row step (0.1 µm per site).  A more accurate version
    # would parse the SIZE line in the LEF MACRO.
    net_pin_pos = {}
    missing_cells = set()
    missing_pins  = set()

    for net_name, cell_pins in nets.items():
        positions = []
        for inst, pin_name in cell_pins:
            if inst not in components:
                continue
            cell_type, x_def, y_def, orient = components[inst]
            x_um = x_def / UNITS
            y_um = y_def / UNITS

            if cell_type not in lef_pins:
                missing_cells.add(cell_type)
                # fall back to cell origin
                positions.append((x_um, y_um))
                continue

            if pin_name not in lef_pins[cell_type]:
                missing_pins.add((cell_type, pin_name))
                positions.append((x_um, y_um))
                continue

            px_lef, py_lef = lef_pins[cell_type][pin_name]
            # For simplicity, use N orientation as base (most cells in N/FN rows).
            # Full orientation transform requires cell SIZE from LEF; we skip that
            # here since pin offsets are small relative to clustering epsilon.
            px_um = x_um + px_lef
            py_um = y_um + py_lef
            positions.append((px_um, py_um))

        if positions:
            net_pin_pos[net_name] = positions

    if missing_cells:
        print(f"  [warn] {len(missing_cells)} cell types missing from LEF (used origin)")
    if missing_pins:
        print(f"  [warn] {len(missing_pins)} pin references missing from LEF (used origin)")
    return net_pin_pos

# ── DBSCAN (simple, no scipy dependency) ─────────────────────────────────────

def dbscan(points, eps, min_samples=1):
    """Minimal DBSCAN on a list of (x, y) tuples. Returns list of cluster labels
    (-1 = noise). Uses a grid for O(n) neighbour lookup."""
    n = len(points)
    labels = [-1] * n
    cluster_id = 0

    # Grid-based neighbour index
    cell_size = eps
    grid = defaultdict(list)
    for i, (x, y) in enumerate(points):
        grid[(int(x / cell_size), int(y / cell_size))].append(i)

    def neighbours(idx):
        x, y = points[idx]
        gx, gy = int(x / cell_size), int(y / cell_size)
        result = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), []):
                    if j != idx:
                        px, py = points[j]
                        if (x - px) ** 2 + (y - py) ** 2 <= eps * eps:
                            result.append(j)
        return result

    visited = [False] * n
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        nb = neighbours(i)
        if len(nb) < min_samples - 1:
            labels[i] = -1  # noise
            continue
        labels[i] = cluster_id
        stack = list(nb)
        while stack:
            j = stack.pop()
            if not visited[j]:
                visited[j] = True
                nb2 = neighbours(j)
                if len(nb2) >= min_samples - 1:
                    stack.extend(nb2)
            if labels[j] == -1:
                labels[j] = cluster_id
        cluster_id += 1

    return labels

# ── Main ──────────────────────────────────────────────────────────────────────

def cluster_nets(net_pin_pos, eps, min_nets):
    """Cluster nets by pin-centroid proximity. Return list of clusters,
    each cluster = {'nets': [...], 'bbox': (x1,y1,x2,y2)}."""
    net_names = list(net_pin_pos.keys())
    # compute each net's centroid
    centroids = []
    for name in net_names:
        pts = net_pin_pos[name]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        centroids.append((cx, cy))

    labels = dbscan(centroids, eps, min_samples=1)

    # group nets by cluster label
    cluster_nets_map = defaultdict(list)
    for i, label in enumerate(labels):
        cluster_nets_map[label].append(net_names[i])

    clusters = []
    for label, nets_in_cluster in cluster_nets_map.items():
        if label == -1 or len(nets_in_cluster) < min_nets:
            continue
        # bounding box of all pins in this cluster
        all_pts = []
        for net in nets_in_cluster:
            all_pts.extend(net_pin_pos[net])
        x1 = min(p[0] for p in all_pts)
        y1 = min(p[1] for p in all_pts)
        x2 = max(p[0] for p in all_pts)
        y2 = max(p[1] for p in all_pts)
        clusters.append({'nets': nets_in_cluster, 'bbox': (x1, y1, x2, y2),
                         'centroid': centroids[cluster_nets_map[label].index(nets_in_cluster[0])]
                                     if False else None})
    clusters.sort(key=lambda c: -len(c['nets']))
    return clusters

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('def_file')
    ap.add_argument('lef_file')
    ap.add_argument('--eps',  type=float, default=10.0,
                    help='DBSCAN clustering radius in µm (default 10)')
    ap.add_argument('--min',  type=int,   default=3,
                    help='Minimum nets per cluster to report (default 3)')
    ap.add_argument('--out',  default=None,
                    help='Output file for BUDA add_block busterms')
    ap.add_argument('--hist', action='store_true',
                    help='Print histograms')
    args = ap.parse_args()

    print(f"Parsing LEF: {args.lef_file}")
    lef_pins = parse_lef(args.lef_file)
    print(f"  {len(lef_pins)} cell types, "
          f"{sum(len(v) for v in lef_pins.values())} signal pins total")

    print(f"Parsing DEF: {args.def_file}")
    units, die, components, nets = parse_def(args.def_file)
    print(f"  UNITS={units}, Die={die[0]:.1f}×{die[1]:.1f} µm")
    print(f"  {len(components)} instances, {len(nets)} signal nets")

    print("Computing pin positions...")
    net_pin_pos = compute_pin_positions(components, nets, lef_pins)
    print(f"  {len(net_pin_pos)} nets with resolved pin positions")

    if args.hist:
        from collections import Counter
        fanout_dist = Counter(len(v) for v in net_pin_pos.values())
        print("\nFanout distribution:")
        for k in sorted(fanout_dist)[:20]:
            bar = '█' * min(40, fanout_dist[k] // max(1, max(fanout_dist.values()) // 40))
            print(f"  {k:4d}: {fanout_dist[k]:6d}  {bar}")

    print(f"\nClustering (eps={args.eps} µm, min_nets={args.min})...")
    clusters = cluster_nets(net_pin_pos, args.eps, args.min)

    total_clustered = sum(len(c['nets']) for c in clusters)
    print(f"  {len(clusters)} clusters, {total_clustered} nets clustered "
          f"({100*total_clustered/len(net_pin_pos):.1f}% of all nets)\n")

    print(f"{'#':>4}  {'Nets':>6}  {'BBox (µm)':^40}  {'W×H':^16}")
    print("-" * 75)
    for i, c in enumerate(clusters[:30]):
        x1, y1, x2, y2 = c['bbox']
        print(f"{i+1:4d}  {len(c['nets']):6d}  "
              f"({x1:7.1f},{y1:7.1f})–({x2:7.1f},{y2:7.1f})  "
              f"{x2-x1:6.1f}×{y2-y1:6.1f}")
    if len(clusters) > 30:
        print(f"  ... {len(clusters)-30} more clusters")

    if args.hist:
        from collections import Counter
        sizes = Counter(len(c['nets']) for c in clusters)
        print("\nCluster-size distribution:")
        for k in sorted(sizes)[:20]:
            bar = '█' * min(40, sizes[k])
            print(f"  {k:4d} nets: {sizes[k]:5d} clusters  {bar}")

    if args.out:
        with open(args.out, 'w') as f:
            f.write(f"# Spatial net clusters from {args.def_file}\n")
            f.write(f"# eps={args.eps} µm, min_nets={args.min}\n")
            f.write(f"# Die: {die[0]:.1f} x {die[1]:.1f} µm\n\n")
            for i, c in enumerate(clusters):
                x1, y1, x2, y2 = c['bbox']
                f.write(f"add_block cluster_{i+1}  "
                        f"{x1:.2f} {y1:.2f}  {x2:.2f} {y2:.2f}"
                        f"  # {len(c['nets'])} nets\n")
        print(f"\nWrote {len(clusters)} busterms to {args.out}")

if __name__ == '__main__':
    main()
