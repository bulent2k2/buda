#!/usr/bin/env python3
"""The numbers the benchmark wants, out of one LibreLane run directory.

    runtimes.py runs/<tag> [--json]

Per-step wall time from each step's `runtime.txt` (what LibreLane writes when
a step finishes), grouped into the stages the plan reports on -- synthesis,
floorplan+placement, CTS, routing, signoff -- plus the PPA metrics from the
run's final `metrics.json`: instance/die area, utilization, setup/hold
slack, power, wirelength, DRC counts.  One row per run, so three arms at
one N are three lines of a table, and `--json` is the machine-readable row.
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
METRICS = ["design__instance__area", "design__die__area", "design__instance__utilization",
           "timing__setup__ws", "timing__setup__tns", "timing__hold__ws", "power__total",
           "route__wirelength", "route__drc_errors", "magic__drc_error__count",
           "klayout__drc_error__count", "design__instance__count"]


def step_seconds(step_dir):
    p = os.path.join(step_dir, "runtime.txt")
    if not os.path.exists(p):
        return None
    txt = open(p).read().strip()
    # LibreLane's format_elapsed_time: "{hours}:{minutes}:{seconds}:{milliseconds}".
    m = re.fullmatch(r"(\d+):(\d+):(\d+):(\d+)", txt)
    if not m:
        raise ValueError(f"{p}: not h:m:s:ms: {txt!r}")
    h, mnt, sec, ms = map(int, m.groups())
    return h * 3600 + mnt * 60 + sec + ms / 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    steps = sorted(glob.glob(os.path.join(a.run_dir, "[0-9]*-*")))
    if not steps:
        raise SystemExit(f"no step directories under {a.run_dir}")
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
    mpath = os.path.join(a.run_dir, "final", "metrics.json")
    metrics = json.load(open(mpath)) if os.path.exists(mpath) else {}
    row = {"run": a.run_dir, "steps": len(steps), "total_s": round(total, 1),
           **{f"{k}_s": round(v, 1) for k, v in per_stage.items()},
           "other_s": round(unassigned, 1),
           **{k: metrics.get(k) for k in METRICS}}
    if a.json:
        print(json.dumps(row))
        return
    print(f"{a.run_dir}: {len(steps)} steps, {total:.0f}s total")
    for name, _ in STAGES:
        print(f"  {name:<16} {per_stage[name]:8.1f}s")
    print(f"  {'other':<16} {unassigned:8.1f}s")
    for k in METRICS:
        if k in metrics:
            print(f"  {k:<36} {metrics[k]}")


if __name__ == "__main__":
    main()
