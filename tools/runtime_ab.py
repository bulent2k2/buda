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

"""runtime_ab — base-vs-branch runtime A/B for .buda flows.

The two-class measurement rule (risk_reduction_plan.md R4): any
runtime-motivated change measures at least one rnr AND one chip vehicle
before/after.  This tool makes that one command:

    tools/runtime_ab.py flow/rnr/mix2_fast_bottomup_caps.buda \\
                        flow/chip/chip_stack_topdown.buda [-n 3] \\
                        [--base origin/main] [--threads 4]

It materializes the base ref in a git worktree (cached under the
system temp dir, keyed by commit), builds BOTH sides, runs each flow N
times per side ALTERNATING base/branch (so slow machine drift hits both
legs equally), parses each run's per-command timing summary, and reports
the per-command MEDIANS side by side with deltas.  Runs are sequential
(never concurrent) so the numbers are contention-free.

Exit code is always 0 — runtime is informational; the QoR corpus is the
regression gate.
"""

import argparse
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Final-summary rows: "  <command...>   12.34s" (the second, bare block).
_ROW = re.compile(r"^  (\S.*?)\s{2,}(\d+\.\d+)s(?:\s*!)?\s*$")


def sh(cmd, cwd=ROOT, check=True, env=None):
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       env=env)
    if check and r.returncode != 0:
        sys.exit(f"command failed ({' '.join(cmd)}):\n{r.stderr[-2000:]}")
    return r


def prepare_base(ref):
    commit = sh(["git", "rev-parse", ref]).stdout.strip()
    wt = Path(tempfile.gettempdir()) / f"buda-ab-{commit[:12]}"
    if not (wt / "CMakeLists.txt").exists():
        sh(["git", "worktree", "add", "--detach", str(wt), commit])
    return wt, commit


def build(tree):
    bdir = tree / "build"
    bdir.mkdir(exist_ok=True)
    sh(["cmake", "..", "-DCMAKE_BUILD_TYPE=Release"], cwd=bdir)
    sh(["make", f"-j{os.cpu_count() or 2}"], cwd=bdir)


def run_flow(tree, flow, threads):
    env = {**os.environ, "PYTHONPATH": f"{tree}/build:{tree}/src"}
    if threads:
        env["BUDA_THREADS"] = str(threads)
    r = sh([sys.executable, str(tree / "src" / "buda_cli.py"),
            "--no-viz", flow], cwd=tree, check=False, env=env)
    if r.returncode != 0:
        sys.exit(f"{flow} failed on {tree}:\n{r.stdout[-1500:]}"
                 f"{r.stderr[-500:]}")
    rows = {}
    for line in r.stdout.splitlines():
        m = _ROW.match(line)
        if m:
            rows[m.group(1).strip()] = float(m.group(2))  # last block wins
    return rows


def median_rows(samples):
    out = {}
    for k in samples[0]:
        vals = [s.get(k) for s in samples if k in s]
        out[k] = statistics.median(vals)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flows", nargs="+", help=".buda flow paths (repo-relative)")
    ap.add_argument("-n", type=int, default=3, help="runs per side (default 3)")
    ap.add_argument("--base", default="origin/main", help="base git ref")
    ap.add_argument("--threads", type=int, default=None,
                    help="BUDA_THREADS for both sides (default: env/None)")
    ap.add_argument("--min-delta", type=float, default=0.3,
                    help="hide per-command rows moving less than this (s)")
    args = ap.parse_args()

    base_tree, commit = prepare_base(args.base)
    print(f"[ab] base {args.base} = {commit[:12]} at {base_tree}")
    print("[ab] building base ...");   build(base_tree)
    print("[ab] building branch ..."); build(ROOT)

    for flow in args.flows:
        base_s, mine_s = [], []
        for i in range(args.n):        # alternate: drift hits both legs
            print(f"[ab] {flow}: run {i + 1}/{args.n} (base, then branch)")
            base_s.append(run_flow(base_tree, flow, args.threads))
            mine_s.append(run_flow(ROOT, flow, args.threads))
        b, m = median_rows(base_s), median_rows(mine_s)
        print(f"\n== {flow}  (median of {args.n}, "
              f"threads={args.threads or 'env'}) ==")
        keys = [k for k in b if k in m]
        keys.sort(key=lambda k: -(abs(m[k] - b[k])))
        for k in keys:
            d = m[k] - b[k]
            if abs(d) < args.min_delta and not k.startswith("total"):
                continue
            print(f"  {k:<44} {b[k]:8.2f}s -> {m[k]:8.2f}s  {d:+.2f}s")
        tb = next((k for k in b if k.startswith("total")), None)
        if tb and tb in m:
            d = m[tb] - b[tb]
            pct = 100.0 * d / tb_val if (tb_val := b[tb]) else 0.0
            print(f"  {'TOTAL':<44} {b[tb]:8.2f}s -> {m[tb]:8.2f}s  "
                  f"{d:+.2f}s ({pct:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
