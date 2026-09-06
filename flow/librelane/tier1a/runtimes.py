#!/usr/bin/env python3
"""The numbers the benchmark wants, out of LibreLane run directories.

    runtimes.py runs/<tag> [--set KEY=VALUE ...] [--block <run_dir>[:<instances>] ...] [--json]

Per-step wall time from each step's `runtime.txt` (what LibreLane writes when
a step finishes), grouped into the stages the plan reports on -- synthesis,
floorplan+placement, CTS, routing, signoff -- plus the PPA metrics from the
run's final `metrics.json`: instance/die area, utilization, setup/hold
slack, power, wirelength, DRC counts.  One row per run, so three arms at
one N are three lines of a table, and `--json` is the machine-readable row.
A row must say WHICH point it is on its own: `--set N=8 --set arm=F` puts
the benchmark coordinates into it (any KEY=VALUE, merged in verbatim), and
`run` is the run directory relative to the repository root -- three rows
that each said `runs/flat` because recipe 7 invokes this from each `n<N>`
directory were usable only by line order (Codex #881).

A HIERARCHICAL arm is one top run plus the hardening of each distinct
block, and its row must carry both (docs/internal/librelane_hier_flow.md
§7.3).  `--block <run_dir>[:<instances>]` names a block's run and how many
times the top places it, and the row gains: `blocks_wall_s` (the longest
block, since blocks harden in parallel) and `blocks_cpu_s` (their sum),
`arm_wall_s`/`arm_cpu_s` (top plus each), `route__wirelength__blocks` (each
block's wire TIMES its instance count -- a cell hardened once and placed
eight times has eight times the wire on silicon) with the per-block list,
`route__wirelength__arm` (top plus blocks), and the blocks' routing DRC
sum.  The block-internal wire is kept as its own column because it is
where the block-side handoff is paid for: on the phase-0 block, the pin
template that straightens the top-level bus costs the block +60 % of its
own wire (3937 -> 6303 um, measured 2026-09-05), and an arm's total alone
would hide that trade against the bus it buys.
"""
import argparse
import glob
import json
import os
import re
import sys

STAGES = [
    ("synth", ("yosys", "verilator", "checker-lint", "checker-yosys")),
    ("floorplan+place", ("openroad-floorplan", "openroad-cutrows", "openroad-tapendcap",
                         "openroad-generatepdn", "odb-", "openroad-ioplacement",
                         "openroad-globalplacement", "openroad-repairdesignpostgpl",
                         "openroad-detailedplacement", "openroad-stapre", "openroad-stamid",
                         "openroad-dumprc")),
    ("cts", ("openroad-cts", "openroad-resizertimingpostcts")),
    ("route", ("openroad-globalrouting", "openroad-repairdesignpostgrt",
               "openroad-resizertimingpostgrt", "openroad-repairantennas",
               "openroad-detailedrouting", "openroad-checkantennas")),
    ("signoff", ("openroad-fillinsertion", "openroad-rcx", "openroad-stapost",
                 "openroad-irdrop", "magic-", "klayout-", "netgen-", "checker-",
                 "misc-", "yosys-eqy")),
]
# The columns docs/internal/librelane_hier_flow.md §7.3 tabulates -- the
# power BREAKDOWN included (Codex #876 P2: only the total was kept, so the
# per-metric tables the plan prescribes could not be built from saved rows).
METRICS = ["design__instance__area", "design__die__area", "design__instance__utilization",
           "timing__setup__ws", "timing__setup__tns", "timing__hold__ws",
           "power__total", "power__internal__total", "power__switching__total",
           "power__leakage__total",
           "route__wirelength", "route__drc_errors", "magic__drc_error__count",
           "klayout__drc_error__count", "design__instance__count"]


def step_seconds(step_dir):
    p = os.path.join(step_dir, "runtime.txt")
    if not os.path.exists(p):
        return None
    txt = open(p).read().strip()
    # LibreLane's format_elapsed_time DOCUMENTS "{hours}:{minutes}:{seconds}:
    # {milliseconds}" and WRITES "HH:MM:SS.mmm" (every runtime.txt of every
    # real 3.0.11 run reads like 00:00:04.365 -- measured 2026-09-05, when
    # the first real run was refused by a parser written to the docstring).
    # Both are accepted, nothing else: a wrong time parser would be a silent
    # factor on every runtime number.
    m = re.fullmatch(r"(\d+):(\d+):(\d+)\.(\d{1,3})", txt) or re.fullmatch(r"(\d+):(\d+):(\d+):(\d+)", txt)
    if not m:
        raise ValueError(f"{p}: not HH:MM:SS.mmm (nor h:m:s:ms): {txt!r}")
    h, mnt, sec = map(int, m.groups()[:3])
    frac = m.group(4)
    ms = int(frac.ljust(3, "0")) if "." in txt else int(frac)
    return h * 3600 + mnt * 60 + sec + ms / 1000.0


def read_run(run_dir):
    """(n_steps, per-stage seconds, unassigned seconds, total seconds, metrics)."""
    steps = sorted(glob.glob(os.path.join(run_dir, "[0-9]*-*")))
    if not steps:
        raise SystemExit(f"no step directories under {run_dir}")
    per_stage = {name: 0.0 for name, _ in STAGES}
    unassigned, total = 0.0, 0.0
    for sd in steps:
        secs = step_seconds(sd)
        if secs is None:
            continue
        total += secs
        slug = os.path.basename(sd).split("-", 1)[1]
        for name, prefixes in STAGES:
            if any(slug.startswith(p) for p in prefixes):
                per_stage[name] += secs
                break
        else:
            unassigned += secs
    mpath = os.path.join(run_dir, "final", "metrics.json")
    metrics = json.load(open(mpath)) if os.path.exists(mpath) else {}
    return len(steps), per_stage, unassigned, total, metrics


def parse_block(spec):
    run_dir, _, count = spec.partition(":")
    if count and not count.isdigit():
        raise SystemExit(f"--block {spec}: the instance count after ':' must be an integer")
    return run_dir, int(count) if count else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="benchmark coordinates to record in the row, e.g. N=8 arm=F")
    ap.add_argument("--block", action="append", default=[], metavar="RUN_DIR[:INSTANCES]",
                    help="a hardened block's run directory and how many times the top places it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    coords = {}
    for kv in a.set:
        key, sep, value = kv.partition("=")
        if not sep or not key:
            raise SystemExit(f"--set {kv}: expected KEY=VALUE")
        coords[key] = int(value) if value.isdigit() else value
    n_steps, per_stage, unassigned, total, metrics = read_run(a.run_dir)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    run_abs = os.path.abspath(a.run_dir)
    run_id = os.path.relpath(run_abs, root) if run_abs.startswith(root + os.sep) else run_abs
    row = {**coords, "run": run_id, "steps": n_steps, "total_s": round(total, 1),
           **{f"{k}_s": round(v, 1) for k, v in per_stage.items()},
           "other_s": round(unassigned, 1),
           **{k: metrics.get(k) for k in METRICS}}
    blocks = []
    for spec in a.block:
        bdir, count = parse_block(spec)
        _, _, _, btotal, bm = read_run(bdir)
        for key in ("route__wirelength", "route__drc_errors"):
            if bm.get(key) is None:
                raise SystemExit(f"--block {bdir}: no {key} in its final/metrics.json -- a block "
                                 f"that did not get that far has nothing to account, and a zero "
                                 f"there would read as a clean one (Codex #878)")
        blocks.append({"run": bdir, "instances": count, "total_s": round(btotal, 1),
                       "route__wirelength": bm["route__wirelength"],
                       "route__wirelength__placed": bm["route__wirelength"] * count,
                       "route__drc_errors": bm.get("route__drc_errors")})
    if blocks:
        if metrics.get("route__wirelength") is None:
            raise SystemExit(f"{a.run_dir}: no route__wirelength in its final/metrics.json -- an arm "
                             f"total over the blocks alone would be a plausible, incomplete number "
                             f"(Codex #878)")
        top_wl = metrics["route__wirelength"]
        row["blocks"] = blocks
        row["blocks_wall_s"] = max(b["total_s"] for b in blocks)
        row["blocks_cpu_s"] = round(sum(b["total_s"] for b in blocks), 1)
        row["arm_wall_s"] = round(total + row["blocks_wall_s"], 1)
        row["arm_cpu_s"] = round(total + row["blocks_cpu_s"], 1)
        row["route__wirelength__blocks"] = sum(b["route__wirelength__placed"] for b in blocks)
        row["route__wirelength__arm"] = top_wl + row["route__wirelength__blocks"]
        row["route__drc_errors__blocks"] = sum(b["route__drc_errors"] for b in blocks)
    if a.json:
        print(json.dumps(row))
        return
    print(f"{run_id}{''.join(f' {k}={v}' for k, v in coords.items())}: {n_steps} steps, {total:.0f}s total")
    for name, _ in STAGES:
        print(f"  {name:<16} {per_stage[name]:8.1f}s")
    print(f"  {'other':<16} {unassigned:8.1f}s")
    for k in METRICS:
        if k in metrics:
            print(f"  {k:<36} {metrics[k]}")
    if blocks:
        print(f"blocks ({len(blocks)}): wall {row['blocks_wall_s']}s (parallel), cpu {row['blocks_cpu_s']}s; "
              f"arm wall {row['arm_wall_s']}s, cpu {row['arm_cpu_s']}s")
        for b in blocks:
            print(f"  {b['run']}: x{b['instances']}, {b['total_s']}s, wire {b['route__wirelength']} "
                  f"(placed {b['route__wirelength__placed']}), drc {b['route__drc_errors']}")
        print(f"  {'route__wirelength__blocks':<36} {row['route__wirelength__blocks']}")
        print(f"  {'route__wirelength__arm':<36} {row['route__wirelength__arm']}")


if __name__ == "__main__":
    main()
