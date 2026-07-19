#!/usr/bin/env python3

# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
def_cluster.py — Spatial net clustering from placed DEF + LEF.

Three modes:

  centroid (default):
    Computes the physical centre of every signal-net pin, then clusters nets
    whose pin-centroids are within epsilon (DBSCAN). Each cluster becomes a
    candidate BUDA busterm (bounding box of all pins in the cluster).

  bipartite (--bipartite):
    Separates driver pins from receiver pins using LEF DIRECTION metadata.
    DBSCAN on driver positions → source busterms.
    DBSCAN on receiver centroids → destination busterms.
    Nets are matched to (source_cluster, dest_cluster) pairs; pairs with
    >= min_nets nets become BUDA buses with explicit src + dst blocks.

    Quality filters (bipartite mode only):
      --max-nets N    drop buses wider than N bits (default: 64)
      --min-span D    drop buses whose src→dst centroid distance < D µm (default: 5)

  --grid CELLSIZE (bipartite mode only, replaces DBSCAN):
    Instead of DBSCAN, assigns each net to a fixed (src_cell, dst_cell) grid pair
    based on driver pin and receiver centroid positions. Avoids DBSCAN chaining.
    CELLSIZE is the grid square side in µm (e.g., 10).

  high-fanout (--high-fanout):
    Each net with fanout ≥ min-fanout is extracted as a standalone 1-bit multicast
    bus candidate — no grouping with other nets.
      src block = driver pin ± src-margin µm  (tiny block, corner_margin dx 1 dy 1)
      dst block = bbox of all receiver pins
    Nets are sorted by HPWL descending so the highest-impact candidates come first.
    Filters: --min-fanout (default 5), --max-fanout (default 256),
             --min-hpwl (default 20 µm), --src-margin (default 2 µm).

Usage:
    python3 def_cluster.py <design.input.def> <design.input.lef> [options]

Options:
    --eps       EPSILON   Clustering radius in µm (default: 1.0)
    --min       MIN_NETS  Minimum nets per cluster/bus to report (default: 5)
    --bipartite           Use bipartite driver/receiver clustering (default: centroid)
    --grid      CELLSIZE  (bipartite) Grid cell size µm — replaces DBSCAN (default: off)
    --max-nets  N         (bipartite) Drop buses with more than N nets (default: 64)
    --min-span  D         (bipartite) Drop buses with src→dst distance < D µm (default: 5)
    --high-fanout         Extract each high-fanout net as a 1-bit BUDA multicast bus
    --min-fanout  N       (high-fanout) Minimum fanout (default 5)
    --max-fanout  N       (high-fanout) Maximum fanout — exclude global signals (default 256)
    --min-hpwl    D       (high-fanout) Minimum HPWL in µm (default 20)
    --src-margin  D       (high-fanout) Driver pin block half-size in µm (default 2)
    --out         FILE    Write busterms/buses to FILE in BUDA add_block format
    --hist                Print fanout and cluster-size histograms
    --hpwl                Print half-perimeter wirelength distribution (all nets)
"""

import argparse, re, sys, math
from collections import defaultdict

UNITS = 2000  # DEF units per µm (overridden by UNITS line in DEF)

# ── LEF parser ────────────────────────────────────────────────────────────────

def parse_lef(lef_path):
    """Return {cell_type: {pin_name: (cx, cy, direction)}}.

    direction is 'OUTPUT', 'INPUT', or 'UNKNOWN'.
    Power/ground/clock pins are excluded.
    """
    pins = {}
    with open(lef_path) as f:
        content = f.read()

    for macro_m in re.finditer(r'MACRO (\S+)(.*?)END \1', content, re.DOTALL):
        cell = macro_m.group(1)
        cell_body = macro_m.group(2)
        cell_pins = {}
        for pin_m in re.finditer(r'PIN (\S+)(.*?)END \1', cell_body, re.DOTALL):
            pin_name = pin_m.group(1)
            pin_body = pin_m.group(2)
            use_m = re.search(r'USE\s+(\S+)\s*;', pin_body)
            if use_m and use_m.group(1) in ('POWER', 'GROUND', 'CLOCK'):
                continue
            dir_m = re.search(r'DIRECTION\s+(INPUT|OUTPUT|INOUT|FEEDTHRU)\s*;', pin_body)
            direction = dir_m.group(1) if dir_m else 'UNKNOWN'
            # Treat INOUT as OUTPUT for routing purposes (bidirectional → drives net)
            if direction == 'INOUT':
                direction = 'OUTPUT'
            rects = re.findall(
                r'RECT\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*;',
                pin_body)
            if not rects:
                continue
            xs = [(float(x1) + float(x2)) / 2 for x1, y1, x2, y2 in rects]
            ys = [(float(y1) + float(y2)) / 2 for x1, y1, x2, y2 in rects]
            cell_pins[pin_name] = (sum(xs) / len(xs), sum(ys) / len(ys), direction)
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

    global UNITS
    um = re.search(r'UNITS DISTANCE MICRONS (\d+)', content)
    if um:
        UNITS = int(um.group(1))

    da = re.search(r'DIEAREA \( \d+ \d+ \) \( (\d+) (\d+) \)', content)
    die = (int(da.group(1)) / UNITS, int(da.group(2)) / UNITS) if da else (0, 0)

    components = {}
    comp_sec = re.search(r'^COMPONENTS \d+ ;(.*?)^END COMPONENTS',
                         content, re.DOTALL | re.MULTILINE)
    if comp_sec:
        for m in re.finditer(
                r'- (\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+(\S+)',
                comp_sec.group(1)):
            inst, cell, x, y, orient = m.groups()
            components[inst] = (cell, int(x), int(y), orient)

    nets = {}
    nets_sec = re.search(r'^NETS \d+ ;(.*?)^END NETS',
                         content, re.DOTALL | re.MULTILINE)
    if nets_sec:
        # The `\Z` alternative is essential (audit T3-01): the section body
        # captured by the NETS regex excludes the literal 'END NETS', so
        # without an end-of-string terminator the LAST net of every section
        # matched neither `\n- ` nor `\nEND` and was silently dropped.
        for net_m in re.finditer(r'- (\S+)\s+(.*?)(?=\n\s*-\s|\nEND|\Z)',
                                 nets_sec.group(1), re.DOTALL):
            net_name = net_m.group(1)
            body = net_m.group(2)
            pins = re.findall(r'\(\s*(\S+)\s+(\S+)\s*\)', body)
            cell_pins = [(i, p) for i, p in pins if i != 'PIN']
            if cell_pins:
                nets[net_name] = cell_pins
    return UNITS, die, components, nets

# ── Pin-position geometry ─────────────────────────────────────────────────────

def _pin_um(inst, pin_name, components, lef_pins, missing_cells, missing_pins):
    """Return (px_um, py_um, direction) for a single (inst, pin_name) pair."""
    if inst not in components:
        return None
    cell_type, x_def, y_def, orient = components[inst]
    x_um, y_um = x_def / UNITS, y_def / UNITS
    if cell_type not in lef_pins:
        missing_cells.add(cell_type)
        return (x_um, y_um, 'UNKNOWN')
    if pin_name not in lef_pins[cell_type]:
        missing_pins.add((cell_type, pin_name))
        return (x_um, y_um, 'UNKNOWN')
    px_lef, py_lef, direction = lef_pins[cell_type][pin_name]
    return (x_um + px_lef, y_um + py_lef, direction)


def compute_pin_positions(components, nets, lef_pins):
    """Return {net_name: [(px_um, py_um), ...]} — centroid mode."""
    net_pin_pos = {}
    missing_cells, missing_pins = set(), set()
    for net_name, cell_pins in nets.items():
        positions = []
        for inst, pin_name in cell_pins:
            result = _pin_um(inst, pin_name, components, lef_pins,
                             missing_cells, missing_pins)
            if result:
                positions.append(result[:2])
        if positions:
            net_pin_pos[net_name] = positions
    if missing_cells:
        print(f"  [warn] {len(missing_cells)} cell types missing from LEF (used origin)")
    if missing_pins:
        print(f"  [warn] {len(missing_pins)} pin refs missing from LEF (used origin)")
    return net_pin_pos


def compute_driver_receiver_positions(components, nets, lef_pins):
    """Return {net_name: {'driver': (px,py), 'receivers': [(px,py),...]}}
    using LEF DIRECTION to separate output (driver) from input (receiver) pins.
    Nets with no identified driver or no receivers are excluded.
    """
    net_endpoints = {}
    missing_cells, missing_pins = set(), set()
    no_driver = 0

    for net_name, cell_pins in nets.items():
        driver_pos = None
        receiver_pos = []
        for inst, pin_name in cell_pins:
            result = _pin_um(inst, pin_name, components, lef_pins,
                             missing_cells, missing_pins)
            if result is None:
                continue
            px, py, direction = result
            if direction == 'OUTPUT':
                driver_pos = (px, py)
            elif direction == 'INPUT':
                receiver_pos.append((px, py))
            else:
                # UNKNOWN: first listed is driver (DEF convention), rest receivers
                if driver_pos is None:
                    driver_pos = (px, py)
                else:
                    receiver_pos.append((px, py))

        if driver_pos and receiver_pos:
            net_endpoints[net_name] = {'driver': driver_pos, 'receivers': receiver_pos}
        elif not driver_pos:
            no_driver += 1

    if missing_cells:
        print(f"  [warn] {len(missing_cells)} cell types missing from LEF")
    if missing_pins:
        print(f"  [warn] {len(missing_pins)} pin refs missing from LEF")
    if no_driver:
        print(f"  [warn] {no_driver} nets skipped (no output pin identified)")
    return net_endpoints

# ── DBSCAN (grid-accelerated, no scipy) ──────────────────────────────────────

def dbscan(points, eps, min_samples=1):
    """Minimal DBSCAN on a list of (x, y) tuples. Returns list of cluster labels
    (-1 = noise). Uses a grid for O(n) neighbour lookup."""
    n = len(points)
    labels = [-1] * n
    cluster_id = 0
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
            labels[i] = -1
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

# ── Centroid clustering ───────────────────────────────────────────────────────

def cluster_nets(net_pin_pos, eps, min_nets):
    """Cluster nets by pin-centroid proximity. Return list of clusters,
    each cluster = {'nets': [...], 'bbox': (x1,y1,x2,y2)}."""
    net_names = list(net_pin_pos.keys())
    centroids = []
    for name in net_names:
        pts = net_pin_pos[name]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        centroids.append((cx, cy))

    labels = dbscan(centroids, eps, min_samples=1)

    cluster_nets_map = defaultdict(list)
    for i, label in enumerate(labels):
        cluster_nets_map[label].append(net_names[i])

    clusters = []
    for label, nets_in_cluster in cluster_nets_map.items():
        if label == -1 or len(nets_in_cluster) < min_nets:
            continue
        all_pts = []
        for net in nets_in_cluster:
            all_pts.extend(net_pin_pos[net])
        x1 = min(p[0] for p in all_pts)
        y1 = min(p[1] for p in all_pts)
        x2 = max(p[0] for p in all_pts)
        y2 = max(p[1] for p in all_pts)
        clusters.append({'nets': nets_in_cluster, 'bbox': (x1, y1, x2, y2)})
    clusters.sort(key=lambda c: -len(c['nets']))
    return clusters

# ── Bipartite clustering ──────────────────────────────────────────────────────

def cluster_bipartite(net_endpoints, eps, min_nets, max_nets=64, min_span=5.0):
    """Bipartite clustering: DBSCAN on driver positions and receiver centroids
    independently, then match nets to (src_label, dst_label) pairs.

    Quality filters applied after clustering:
      max_nets: drop buses wider than this (routing-corridor artifacts)
      min_span: drop buses whose src centroid → dst centroid distance < this µm

    Returns list of buses:
      {'nets': [...], 'src_bbox': (x1,y1,x2,y2), 'dst_bbox': (x1,y1,x2,y2),
       'span': float}
    """
    net_names = list(net_endpoints.keys())
    driver_pts = [net_endpoints[n]['driver'] for n in net_names]
    receiver_centroid_pts = []
    for n in net_names:
        rcv = net_endpoints[n]['receivers']
        cx = sum(p[0] for p in rcv) / len(rcv)
        cy = sum(p[1] for p in rcv) / len(rcv)
        receiver_centroid_pts.append((cx, cy))

    src_labels = dbscan(driver_pts, eps, min_samples=1)
    dst_labels = dbscan(receiver_centroid_pts, eps, min_samples=1)

    pair_nets = defaultdict(list)
    for i, name in enumerate(net_names):
        pair_nets[(src_labels[i], dst_labels[i])].append(name)

    buses = []
    for (src_lbl, dst_lbl), nets_in_bus in pair_nets.items():
        if src_lbl == -1 or dst_lbl == -1:
            continue
        if len(nets_in_bus) < min_nets:
            continue
        if len(nets_in_bus) > max_nets:
            continue
        src_pts = [net_endpoints[n]['driver'] for n in nets_in_bus]
        dst_pts = [p for n in nets_in_bus for p in net_endpoints[n]['receivers']]
        src_cx = sum(p[0] for p in src_pts) / len(src_pts)
        src_cy = sum(p[1] for p in src_pts) / len(src_pts)
        dst_cx = sum(p[0] for p in dst_pts) / len(dst_pts)
        dst_cy = sum(p[1] for p in dst_pts) / len(dst_pts)
        span = math.sqrt((src_cx - dst_cx) ** 2 + (src_cy - dst_cy) ** 2)
        if span < min_span:
            continue
        buses.append({
            'nets': nets_in_bus,
            'src_bbox': (_min_x(src_pts), _min_y(src_pts),
                         _max_x(src_pts), _max_y(src_pts)),
            'dst_bbox': (_min_x(dst_pts), _min_y(dst_pts),
                         _max_x(dst_pts), _max_y(dst_pts)),
            'span': span,
        })

    buses.sort(key=lambda b: -len(b['nets']))
    return buses


def cluster_bipartite_grid(net_endpoints, cell_size, min_nets, max_nets=64, min_span=5.0):
    """Grid-based bipartite clustering.

    Each net maps to (src_grid_cell, dst_grid_cell) from its driver pin and
    receiver centroid. Counts nets per pair; pairs with min_nets..max_nets nets
    and src→dst span ≥ min_span become buses.  No DBSCAN chaining.
    """
    pair_nets = defaultdict(list)
    for name, ep in net_endpoints.items():
        sx, sy = ep['driver']
        rcv = ep['receivers']
        dx = sum(p[0] for p in rcv) / len(rcv)
        dy = sum(p[1] for p in rcv) / len(rcv)
        src_cell = (int(sx / cell_size), int(sy / cell_size))
        dst_cell = (int(dx / cell_size), int(dy / cell_size))
        pair_nets[(src_cell, dst_cell)].append(name)

    buses = []
    for (src_cell, dst_cell), nets_in_bus in pair_nets.items():
        if len(nets_in_bus) < min_nets or len(nets_in_bus) > max_nets:
            continue
        src_pts = [net_endpoints[n]['driver'] for n in nets_in_bus]
        dst_pts = [p for n in nets_in_bus for p in net_endpoints[n]['receivers']]
        src_cx = sum(p[0] for p in src_pts) / len(src_pts)
        src_cy = sum(p[1] for p in src_pts) / len(src_pts)
        dst_cx = sum(p[0] for p in dst_pts) / len(dst_pts)
        dst_cy = sum(p[1] for p in dst_pts) / len(dst_pts)
        span = math.sqrt((src_cx - dst_cx) ** 2 + (src_cy - dst_cy) ** 2)
        if span < min_span:
            continue
        buses.append({
            'nets': nets_in_bus,
            'src_bbox': (_min_x(src_pts), _min_y(src_pts),
                         _max_x(src_pts), _max_y(src_pts)),
            'dst_bbox': (_min_x(dst_pts), _min_y(dst_pts),
                         _max_x(dst_pts), _max_y(dst_pts)),
            'span': span,
        })
    buses.sort(key=lambda b: -len(b['nets']))
    return buses


def _min_x(pts): return min(p[0] for p in pts)
def _min_y(pts): return min(p[1] for p in pts)
def _max_x(pts): return max(p[0] for p in pts)
def _max_y(pts): return max(p[1] for p in pts)

# ── High-fanout net extraction ────────────────────────────────────────────────

def extract_high_fanout_buses(net_endpoints, min_fanout=5, max_fanout=256,
                               min_hpwl=20.0, src_margin=2.0):
    """Extract each high-fanout net as a standalone 1-bit BUDA multicast bus.

    Every net with total fanout in [min_fanout, max_fanout] and HPWL >= min_hpwl
    becomes its own bus record:
      src_bbox  — driver pin ± src_margin (tiny block; use corner_margin dx 1 dy 1)
      dst_bbox  — bbox of all receiver pins
      span      — Euclidean distance from driver to receiver centroid

    Returns list sorted by hpwl descending (highest-impact first).
    """
    buses = []
    for name, ep in net_endpoints.items():
        fanout = 1 + len(ep['receivers'])
        if not (min_fanout <= fanout <= max_fanout):
            continue

        drv = ep['driver']
        rcv = ep['receivers']
        all_pts = [drv] + rcv
        hpwl = (_max_x(all_pts) - _min_x(all_pts) +
                _max_y(all_pts) - _min_y(all_pts))
        if hpwl < min_hpwl:
            continue

        rx1, ry1, rx2, ry2 = _min_x(rcv), _min_y(rcv), _max_x(rcv), _max_y(rcv)
        # Skip if shrunken src block touches (but doesn't overlap) shrunken dst block —
        # the topology generator returns 0 candidates for this degenerate geometry.
        # Uses cm=1 (global corner_margin in generated scripts) and src_margin.
        _cm = 1
        sx1s = round(drv[0] - src_margin) + _cm
        sy1s = round(drv[1] - src_margin) + _cm
        sx2s = round(drv[0] + src_margin) - _cm
        sy2s = round(drv[1] + src_margin) - _cm
        dx1s = round(rx1) + _cm;  dy1s = round(ry1) + _cm
        dx2s = round(rx2) - _cm;  dy2s = round(ry2) - _cm
        strict_sep  = (sx2s < dx1s or dx2s < sx1s or sy2s < dy1s or dy2s < sy1s)
        strict_over = (sx1s < dx2s and sx2s > dx1s and sy1s < dy2s and sy2s > dy1s)
        if not strict_sep and not strict_over:
            continue  # src and dst touch but don't overlap — degenerate for L/Z/U topo

        dst_cx = sum(p[0] for p in rcv) / len(rcv)
        dst_cy = sum(p[1] for p in rcv) / len(rcv)
        span = math.sqrt((drv[0] - dst_cx) ** 2 + (drv[1] - dst_cy) ** 2)

        buses.append({
            'net':      name,
            'fanout':   fanout,
            'hpwl':     hpwl,
            'span':     span,
            'src_bbox': (drv[0] - src_margin, drv[1] - src_margin,
                         drv[0] + src_margin, drv[1] + src_margin),
            'dst_bbox': (_min_x(rcv), _min_y(rcv), _max_x(rcv), _max_y(rcv)),
        })

    buses.sort(key=lambda b: -b['hpwl'])
    return buses

# ── HPWL analysis ────────────────────────────────────────────────────────────

def compute_hpwl(net_pin_pos):
    """Return {net_name: hpwl} for all nets with ≥2 resolved pin positions."""
    result = {}
    for name, pts in net_pin_pos.items():
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        result[name] = (max(xs) - min(xs)) + (max(ys) - min(ys))
    return result


def report_hpwl(net_pin_pos, die):
    """Print HPWL distribution: per-fanout breakdown and log-spaced histogram."""
    hpwl = compute_hpwl(net_pin_pos)
    if not hpwl:
        print("  No nets with ≥2 pins.")
        return

    vals = list(hpwl.values())
    vals.sort()
    n = len(vals)
    total = sum(vals)
    die_perimeter = 2 * (die[0] + die[1])

    print(f"\n  Nets analysed : {n:,}")
    print(f"  Total HPWL    : {total:,.1f} µm")
    print(f"  Die perimeter : {die_perimeter:.1f} µm  "
          f"(total = {total/die_perimeter:.0f}× perimeter)")

    def pct(p):
        return vals[max(0, int(p / 100 * n) - 1)]

    print(f"\n  Percentiles (µm):")
    print(f"    p10={pct(10):.2f}  p25={pct(25):.2f}  p50={pct(50):.2f}  "
          f"p75={pct(75):.2f}  p90={pct(90):.2f}  p95={pct(95):.2f}  "
          f"p99={pct(99):.2f}  max={vals[-1]:.2f}")

    # Log-spaced histogram — boundaries chosen to work for any die size
    boundaries = [0.1, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500,
                  1000, 2000, 5000, 10000]
    boundaries = [b for b in boundaries if b <= vals[-1] * 2]
    boundaries.append(vals[-1] + 1)

    print(f"\n  HPWL histogram  (bar = % of count, ● = % of total wirelength):")
    print(f"  {'Range (µm)':<22}  {'Count':>7}  {'%cnt':>5}  {'%WL':>5}  bar")
    print(f"  {'-'*70}")

    prev = 0.0
    for b in boundaries:
        bucket = [v for v in vals if prev <= v < b]
        if not bucket:
            prev = b
            continue
        cnt = len(bucket)
        wl  = sum(bucket)
        pct_cnt = 100 * cnt / n
        pct_wl  = 100 * wl  / total
        bar = '█' * int(pct_cnt / 2) + '·' * int(pct_wl / 2)
        lo = f"{prev:.1f}" if prev > 0 else "0"
        hi = f"{b:.1f}" if b < vals[-1] + 1 else f"{vals[-1]:.1f}+"
        label = f"[{lo}, {hi})"
        print(f"  {label:<22}  {cnt:>7,}  {pct_cnt:>5.1f}  {pct_wl:>5.1f}  {bar}")
        prev = b

    # Per-fanout breakdown
    fanout_buckets = defaultdict(list)
    for name, pts in net_pin_pos.items():
        fo = len(pts)
        if name in hpwl:
            fanout_buckets[fo].append(hpwl[name])

    print(f"\n  Per-fanout HPWL breakdown:")
    print(f"  {'Fanout':<8}  {'Nets':>7}  {'%nets':>6}  {'Median µm':>10}  "
          f"{'Mean µm':>9}  {'% of total WL':>14}")
    print(f"  {'-'*65}")
    fo_groups = [(2,2),(3,4),(5,8),(9,16),(17,32),(33,64),(65,256),(257,99999)]
    for lo, hi in fo_groups:
        combined = []
        for fo, wls in fanout_buckets.items():
            if lo <= fo <= hi:
                combined.extend(wls)
        if not combined:
            continue
        combined.sort()
        label = str(lo) if lo == hi else f"{lo}–{hi}"
        median = combined[len(combined)//2]
        mean   = sum(combined) / len(combined)
        pct_wl = 100 * sum(combined) / total
        print(f"  {label:<8}  {len(combined):>7,}  {100*len(combined)/n:>6.1f}  "
              f"{median:>10.2f}  {mean:>9.2f}  {pct_wl:>13.1f}%")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('def_file')
    ap.add_argument('lef_file')
    ap.add_argument('--eps',      type=float, default=1.0,
                    help='DBSCAN clustering radius in µm (default 1.0)')
    ap.add_argument('--min',      type=int,   default=5,
                    help='Minimum nets per cluster/bus to report (default 5)')
    ap.add_argument('--bipartite', action='store_true',
                    help='Bipartite driver/receiver clustering (default: centroid mode)')
    ap.add_argument('--grid',     type=float, default=None,
                    help='(bipartite) Fixed grid cell size µm — replaces DBSCAN')
    ap.add_argument('--max-nets', type=int,   default=64,
                    help='(bipartite) Drop buses with more than N nets (default 64)')
    ap.add_argument('--min-span', type=float, default=5.0,
                    help='(bipartite) Drop buses with src→dst distance < D µm (default 5)')
    ap.add_argument('--high-fanout', action='store_true',
                    help='Extract each high-fanout net as a 1-bit BUDA multicast bus')
    ap.add_argument('--min-fanout', type=int,   default=5,
                    help='(high-fanout) Minimum fanout (default 5)')
    ap.add_argument('--max-fanout', type=int,   default=256,
                    help='(high-fanout) Maximum fanout — exclude global signals (default 256)')
    ap.add_argument('--min-hpwl',  type=float, default=20.0,
                    help='(high-fanout) Minimum HPWL in µm (default 20)')
    ap.add_argument('--src-margin', type=float, default=2.0,
                    help='(high-fanout) Driver pin block half-size in µm (default 2)')
    ap.add_argument('--out',      default=None,
                    help='Output file for BUDA add_block busterms')
    ap.add_argument('--hist',     action='store_true',
                    help='Print fanout and cluster-size histograms')
    ap.add_argument('--hpwl',     action='store_true',
                    help='Print half-perimeter wirelength distribution (all nets)')
    args = ap.parse_args()

    print(f"Parsing LEF: {args.lef_file}")
    lef_pins = parse_lef(args.lef_file)
    print(f"  {len(lef_pins)} cell types, "
          f"{sum(len(v) for v in lef_pins.values())} signal pins total")

    print(f"Parsing DEF: {args.def_file}")
    units, die, components, nets = parse_def(args.def_file)
    print(f"  UNITS={units}, Die={die[0]:.1f}×{die[1]:.1f} µm")
    print(f"  {len(components)} instances, {len(nets)} signal nets")

    use_bipartite = args.bipartite or (args.grid is not None)

    grid_buses = []
    hf_buses   = []

    if use_bipartite or args.high_fanout:
        print("Computing driver/receiver pin positions...")
        net_endpoints = compute_driver_receiver_positions(components, nets, lef_pins)
        print(f"  {len(net_endpoints)} nets with driver+receiver positions")

        if use_bipartite:
            print(f"\nClustering (mode=bipartite, grid={args.grid} µm)...")
            grid_buses = _run_bipartite(args, die, net_endpoints,
                                         args.max_nets, args.min_span, args.grid)

        if args.high_fanout:
            print(f"\nExtracting high-fanout buses "
                  f"(fanout {args.min_fanout}–{args.max_fanout}, "
                  f"HPWL≥{args.min_hpwl} µm)...")
            hf_buses = _run_high_fanout(args, die, net_endpoints)

        if args.out:
            write_buda_script(args.out, die, args.def_file,
                              grid_buses, hf_buses, args)
    else:
        print("Computing pin positions (mode=centroid)...")
        _run_centroid(args, die, nets, components, lef_pins)


def _run_centroid(args, die, nets, components, lef_pins):
    net_pin_pos = compute_pin_positions(components, nets, lef_pins)
    print(f"  {len(net_pin_pos)} nets with resolved pin positions")

    if args.hpwl:
        print("\nHPWL analysis:")
        report_hpwl(net_pin_pos, die)

    if args.hist:
        from collections import Counter
        fanout_dist = Counter(len(v) for v in net_pin_pos.values())
        print("\nFanout distribution:")
        for k in sorted(fanout_dist)[:20]:
            bar = '█' * min(40, fanout_dist[k] // max(1, max(fanout_dist.values()) // 40))
            print(f"  {k:4d}: {fanout_dist[k]:6d}  {bar}")

    print(f"\nClustering centroid (eps={args.eps} µm, min_nets={args.min})...")
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
            f.write(f"# Spatial net clusters (centroid) from {args.def_file}\n")
            f.write(f"# eps={args.eps} µm, min_nets={args.min}\n")
            f.write(f"# Die: {die[0]:.1f} x {die[1]:.1f} µm\n\n")
            for i, c in enumerate(clusters):
                x1, y1, x2, y2 = c['bbox']
                f.write(f"add_block cluster_{i+1}  "
                        f"{x1:.2f} {y1:.2f}  {x2:.2f} {y2:.2f}"
                        f"  # {len(c['nets'])} nets\n")
        print(f"\nWrote {len(clusters)} busterms to {args.out}")


def _run_bipartite(args, die, net_endpoints, max_nets, min_span, grid):
    total_eligible = len(net_endpoints)

    if args.hpwl:
        # Build unified pin-pos map from endpoints for HPWL reporting
        unified = {n: [ep['driver']] + ep['receivers']
                   for n, ep in net_endpoints.items()}
        print("\nHPWL analysis:")
        report_hpwl(unified, die)

    if grid:
        print(f"\nClustering bipartite-grid (cell={grid} µm, min_nets={args.min}, "
              f"max_nets={max_nets}, min_span={min_span} µm)...")
        buses = cluster_bipartite_grid(net_endpoints, grid, args.min,
                                       max_nets=max_nets, min_span=min_span)
    else:
        print(f"\nClustering bipartite-DBSCAN (eps={args.eps} µm, min_nets={args.min}, "
              f"max_nets={max_nets}, min_span={min_span} µm)...")
        buses = cluster_bipartite(net_endpoints, args.eps, args.min,
                                  max_nets=max_nets, min_span=min_span)

    total_bused = sum(len(b['nets']) for b in buses)
    print(f"  {len(buses)} buses, {total_bused} nets assigned "
          f"({100*total_bused/max(1,total_eligible):.1f}% of eligible nets)\n")

    hdr = (f"{'#':>4}  {'N':>3}  {'Src bbox (µm)':^32}  {'Dst bbox (µm)':^32}  "
           f"{'Src W×H':^12}  {'Dst W×H':^12}  {'Span':>6}")
    print(hdr)
    print("-" * len(hdr))
    for i, b in enumerate(buses[:30]):
        sx1, sy1, sx2, sy2 = b['src_bbox']
        dx1, dy1, dx2, dy2 = b['dst_bbox']
        print(f"{i+1:4d}  {len(b['nets']):3d}  "
              f"({sx1:6.1f},{sy1:6.1f})–({sx2:6.1f},{sy2:6.1f})  "
              f"({dx1:6.1f},{dy1:6.1f})–({dx2:6.1f},{dy2:6.1f})  "
              f"{sx2-sx1:5.1f}×{sy2-sy1:4.1f}  "
              f"{dx2-dx1:5.1f}×{dy2-dy1:4.1f}  "
              f"{b['span']:6.1f}")
    if len(buses) > 30:
        print(f"  ... {len(buses)-30} more buses")

    return buses


def _run_high_fanout(args, die, net_endpoints):
    if args.hpwl:
        unified = {n: [ep['driver']] + ep['receivers']
                   for n, ep in net_endpoints.items()}
        print("\nHPWL analysis:")
        report_hpwl(unified, die)

    buses = extract_high_fanout_buses(
        net_endpoints,
        min_fanout=args.min_fanout,
        max_fanout=args.max_fanout,
        min_hpwl=args.min_hpwl,
        src_margin=args.src_margin,
    )

    total_hpwl = sum(b['hpwl'] for b in buses)
    all_hpwl   = sum(
        (_max_x([ep['driver']] + ep['receivers']) -
         _min_x([ep['driver']] + ep['receivers']) +
         _max_y([ep['driver']] + ep['receivers']) -
         _min_y([ep['driver']] + ep['receivers']))
        for ep in net_endpoints.values()
        if len(ep['receivers']) >= 1
    )
    print(f"  {len(buses)} buses, aggregate HPWL = {total_hpwl:,.0f} µm "
          f"({100*total_hpwl/max(1,all_hpwl):.1f}% of all-net HPWL)\n")

    hdr = (f"{'#':>5}  {'Net':<14}  {'Fo':>4}  {'HPWL µm':>9}  "
           f"{'Driver (µm)':^20}  {'Rcv bbox W×H':^16}  {'Span µm':>8}")
    print(hdr)
    print("-" * len(hdr))
    for i, b in enumerate(buses[:40]):
        sx1, sy1, sx2, sy2 = b['src_bbox']
        dx1, dy1, dx2, dy2 = b['dst_bbox']
        drv_x = (sx1 + sx2) / 2
        drv_y = (sy1 + sy2) / 2
        print(f"{i+1:5d}  {b['net']:<14}  {b['fanout']:>4}  {b['hpwl']:>9.1f}  "
              f"({drv_x:7.1f},{drv_y:7.1f})  "
              f"{dx2-dx1:6.1f}×{dy2-dy1:6.1f}  "
              f"{b['span']:>8.1f}")
    if len(buses) > 40:
        print(f"  ... {len(buses)-40} more buses")

    # Fanout distribution of extracted buses
    from collections import Counter
    fo_dist = Counter(b['fanout'] for b in buses)
    print(f"\n  Fanout distribution of extracted buses:")
    for fo_lo, fo_hi in [(5,8),(9,16),(17,32),(33,64),(65,128),(129,256)]:
        cnt = sum(v for k, v in fo_dist.items() if fo_lo <= k <= fo_hi)
        if cnt:
            wl = sum(b['hpwl'] for b in buses
                     if fo_lo <= b['fanout'] <= fo_hi)
            label = f"{fo_lo}–{fo_hi}"
            print(f"    fanout {label:<7}: {cnt:5d} buses, "
                  f"aggregate HPWL = {wl:,.0f} µm")

    return buses


# ── .buda script generation ───────────────────────────────────────────────────

_TP_M56 = ('SIGNAL 0.14 0.14 SIGNAL 0.14 0.14 SIGNAL 0.14 0.14 SIGNAL 0.14 0.14 '
            'POWER 0.28 0.14 SIGNAL 0.14 0.14 SIGNAL 0.14 0.14 SIGNAL 0.14 0.14 '
            'SIGNAL 0.14 0.14 GROUND 0.28 0.14')
_TP_M78  = ('SIGNAL 0.4 0.4 SIGNAL 0.4 0.4 SIGNAL 0.4 0.4 SIGNAL 0.4 0.4 '
            'POWER 0.8 0.4 SIGNAL 0.4 0.4 SIGNAL 0.4 0.4 SIGNAL 0.4 0.4 '
            'SIGNAL 0.4 0.4 GROUND 0.8 0.4')
_TP_M910 = ('SIGNAL 0.8 0.8 SIGNAL 0.8 0.8 SIGNAL 0.8 0.8 SIGNAL 0.8 0.8 '
            'POWER 1.6 0.8 SIGNAL 0.8 0.8 SIGNAL 0.8 0.8 SIGNAL 0.8 0.8 '
            'SIGNAL 0.8 0.8 GROUND 1.6 0.8')


def _buda_layer_stack(die_w, die_h):
    """Return (layer_lines, track_lines) for the NanGate45 stack that fits this die."""
    w, h = int(math.ceil(die_w)), int(math.ceil(die_h))
    if max(die_w, die_h) < 400:
        layers = [
            f'def_layer 5  M5  H LOW 20 span_min 0  span_max {w}',
            f'def_layer 6  M6  V LOW 20 span_min 0  span_max {h}',
            f'def_layer 7  M7  H TOP 15 span_min 0  span_max {w}',
            f'def_layer 8  M8  V TOP 15 span_min 0  span_max {h}',
        ]
        tracks = [
            f'def_track_pattern 5  0.07  {_TP_M56}',
            f'def_track_pattern 6  0.095 {_TP_M56}',
            f'def_track_pattern 7  0.07  {_TP_M78}',
            f'def_track_pattern 8  0.095 {_TP_M78}',
        ]
    else:
        layers = [
            f'def_layer 7  M7  H LOW 20 span_min 0  span_max {w}',
            f'def_layer 8  M8  V LOW 20 span_min 0  span_max {h}',
            f'def_layer 9  M9  H TOP 15 span_min 0  span_max {w}',
            f'def_layer 10 M10 V TOP 15 span_min 0  span_max {h}',
        ]
        tracks = [
            f'def_track_pattern 7  0.07  {_TP_M78}',
            f'def_track_pattern 8  0.095 {_TP_M78}',
            f'def_track_pattern 9  0.07  {_TP_M910}',
            f'def_track_pattern 10 0.095 {_TP_M910}',
        ]
    return layers, tracks


def _topo_valid(sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2, cm=1):
    """Return True if a src/dst block pair will yield ≥1 topology after corner margins.

    Two failure modes:
    1. Either block shrinks to zero (width or height ≤ 2*cm).
    2. Shrunken src and dst TOUCH (share boundary but not interior) → topology
       generator returns 0 candidates.
    Overlapping (src inside dst or dst inside src) is allowed — the generator handles it.
    """
    # Check non-degenerate after shrink
    for x1, y1, x2, y2 in [(sx1, sy1, sx2, sy2), (dx1, dy1, dx2, dy2)]:
        if (x2 - x1) <= 2 * cm or (y2 - y1) <= 2 * cm:
            return False
    # Shrunken rects
    ssx1, ssy1, ssx2, ssy2 = sx1+cm, sy1+cm, sx2-cm, sy2-cm
    sdx1, sdy1, sdx2, sdy2 = dx1+cm, dy1+cm, dx2-cm, dy2-cm
    # Strictly separated (gap ≥ 1 between boundaries)?
    sep = (ssx2 < sdx1 or sdx2 < ssx1 or ssy2 < sdy1 or sdy2 < ssy1)
    # Strictly overlapping (shared interior)?
    over = (ssx1 < sdx2 and ssx2 > sdx1 and ssy1 < sdy2 and ssy2 > sdy1)
    # Valid iff separated or overlapping; touching (neither) → 0 topologies
    return sep or over


def write_buda_script(path, die, def_file, grid_buses, hf_buses, args):
    """Write a complete runnable .buda script combining grid and high-fanout buses."""
    die_w, die_h = die
    layers, tracks = _buda_layer_stack(die_w, die_h)
    # Global corner_margin: 1 µm for tiny src blocks; HF src get per-block override too
    cm = 1

    n_grid = len(grid_buses)
    n_hf   = len(hf_buses)

    with open(path, 'w') as f:
        f.write(f"# Auto-generated BUDA script from {def_file}\n")
        f.write(f"# Die: {die_w:.1f} x {die_h:.1f} µm\n")
        f.write(f"# {n_grid + n_hf} buses: {n_grid} grid-clustered + {n_hf} high-fanout\n\n")

        for line in layers:
            f.write(line + '\n')
        f.write('\n')
        for line in tracks:
            f.write(line + '\n')
        f.write(f'\ncorner_margin dx {cm} dy {cm}\n')

        # ── Block + bus declarations ──────────────────────────────────────────
        def _ic(v):  # float µm → integer µm (BUDA C++ takes int coords)
            return int(round(v))

        grid_valid = []
        for b in grid_buses:
            sx1, sy1, sx2, sy2 = [_ic(v) for v in b['src_bbox']]
            dx1, dy1, dx2, dy2 = [_ic(v) for v in b['dst_bbox']]
            if _topo_valid(sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2, cm):
                grid_valid.append((b, sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2))

        hf_valid = []
        for b in hf_buses:
            sx1, sy1, sx2, sy2 = [_ic(v) for v in b['src_bbox']]
            dx1, dy1, dx2, dy2 = [_ic(v) for v in b['dst_bbox']]
            if _topo_valid(sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2, cm):
                hf_valid.append((b, sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2))

        skipped = (n_grid - len(grid_valid)) + (n_hf - len(hf_valid))

        if grid_valid:
            f.write(f'\n# ── Grid-clustered buses ({len(grid_valid)}) '
                    f'─────────────────────────────────\n')
            for i, (b, sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2) in enumerate(grid_valid):
                n = len(b['nets'])
                f.write(f'add_block g_{i+1}_src  '
                        f'{sx1} {sy1}  {sx2} {sy2}  # {n} nets\n')
                f.write(f'add_block g_{i+1}_dst  '
                        f'{dx1} {dy1}  {dx2} {dy2}  # {n} nets\n')
                f.write(f'add_bus g_{i+1}[{n}]  g_{i+1}_src.out  g_{i+1}_dst.in\n\n')

        if hf_valid:
            f.write(f'\n# ── High-fanout buses ({len(hf_valid)}) '
                    f'──────────────────────────────────────\n')
            for i, (b, sx1, sy1, sx2, sy2, dx1, dy1, dx2, dy2) in enumerate(hf_valid):
                f.write(f'# {b["net"]}  fanout={b["fanout"]}  '
                        f'HPWL={b["hpwl"]:.1f} µm  span={b["span"]:.1f} µm\n')
                f.write(f'add_block hf_{i+1}_src  '
                        f'{sx1} {sy1}  {sx2} {sy2}  '
                        f'corner_margin dx 1 dy 1\n')
                f.write(f'add_block hf_{i+1}_dst  '
                        f'{dx1} {dy1}  {dx2} {dy2}\n')
                f.write(f'add_bus hf_{i+1}[1]  hf_{i+1}_src.out  hf_{i+1}_dst.in\n\n')

        # ── Pipeline ─────────────────────────────────────────────────────────
        f.write('run_bundler strict\n\n')

        # Emit generation for the FILTERED buses (audit T3-05): the blocks/
        # buses above were declared from grid_valid/hf_valid, so iterating the
        # unfiltered grid_buses/n_hf here referenced g_/hf_ blocks that were
        # never declared whenever any bus failed _topo_valid — a hard CLI
        # error on the generated script.
        if grid_valid:
            for i in range(len(grid_valid)):
                f.write(f'generate_topologies_for_bundle g_{i+1}  '
                        f'g_{i+1}_src  g_{i+1}_dst\n')
            f.write('\n')

        if hf_valid:
            for i in range(len(hf_valid)):
                f.write(f'generate_topologies_for_bundle hf_{i+1}  '
                        f'hf_{i+1}_src  hf_{i+1}_dst\n')
            f.write('\n')

        f.write('run_planner 50\n')
        f.write('run_nuts 1.0\n')
        f.write('run_detailed_nuts\n\n')
        f.write('visualize\n')

    total = len(grid_valid) + len(hf_valid)
    msg = f'\nWrote {total} buses ({2*total} blocks) to {path}'
    if skipped:
        msg += f' ({skipped} degenerate bus(es) skipped)'
    print(msg)


if __name__ == '__main__':
    main()
