#!/usr/bin/env python3
"""Choose a SoC container's slicing plan by scoring candidates ON THE CHIP.

    tools/soc_plan_search.py --knobs "-PAD 10 -GAP 4 -M 4 -FACES 4" \\
        [--cell cluster_cell] [--slack 0.5] [--jobs 4] [--top 12] [--heal 4] \\
        [--out DIR]

Round 3 of `flow/tcl/soc.md` measured that nothing at cell scope predicts
how a plan fares in the whole chip -- not the cell routed alone, not a
connectivity proxy (Spearman 0.24 at best over 19 plans) -- while the
chip's own first check is stable to about +-10 % under a one-unit
perturbation and its healed end state is not.  So this scores on the chip:

  1. SCREEN   every plan of `--cell` (`soc_local.tcl -list`, the arrangements
              within `--slack` of the smallest) routed once through
              `soc.tcl -PACK slice -FIX {<cell> k} -noheal`: the first
              check, as score = unplaced + 16 x overlaps (an overlap is a
              whole segment, a bus's worth of bits);
  2. RESAMPLE the `--top` best screened plans twice more, at PAD+1 and at
              GAP = M + 1 -- the SAME arrangement, found in the perturbed
              plan list by its slicing tree (an index is not stable: the
              list is sorted by area and pruned per subset); a plan is
              ranked by its MEAN over all three, and one the pruning left
              out of a perturbed list is ranked last and never healed;
  3. HEAL     the `--heal` best by mean routed in full with the vehicle's
              own healing, timed, beside two baselines -- the slice
              packer's own choice (`-FIX` absent) and the grid packer.

Every run is cached in DIR/runs.json by its exact command line, so a
re-run resumes.  Screening runs use one worker thread each and run
`--jobs` at a time (the score is a count, not a time); healed runs use the
default threads, two at a time, and their times are what the table quotes.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BTCL = ROOT / "bin" / "btcl"
SOC = ROOT / "flow" / "tcl" / "soc.tcl"
LOCAL = ROOT / "flow" / "tcl" / "soc_local.tcl"
BASE = ["8", "-LAYOUT", "compact"]


class Cache:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.d = json.loads(path.read_text()) if path.exists() else {}

    def get(self, k):
        with self.lock:
            return self.d.get(k)

    def put(self, k, v):
        with self.lock:
            self.d[k] = v
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.d, indent=1))
            tmp.replace(self.path)


def knob_list(s):
    return shlex.split(s)


def set_knob(knobs, name, value):
    out = list(knobs)
    flag = "-" + name
    if flag in out:
        out[out.index(flag) + 1] = str(value)
    else:
        out += [flag, str(value)]
    return out


def grid_knobs(knobs):
    """The same knobs for the GRID packer, whose `configure` refuses the
    slice-only ones: ASPECT back to 1, CGAP back to -1, no FIX / FIXSLACK."""
    out = set_knob(set_knob(set_knob(knobs, "PACK", "grid"), "ASPECT", 1), "CGAP", -1)
    for name in ("FIX", "FIXSLACK"):
        flag = "-" + name
        if flag in out:
            i = out.index(flag)
            del out[i:i + 2]
    return out


def get_knob(knobs, name, default=None):
    flag = "-" + name
    return knobs[knobs.index(flag) + 1] if flag in knobs else default


def list_plans(cell, knobs, slack):
    cmd = ["tclsh", str(LOCAL), cell, "-list", "-slack", str(slack), *knobs]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=300)
    if r.returncode != 0:
        sys.exit("soc_plan_search: plan listing failed:\n" + r.stdout + r.stderr)
    plans = []
    for ln in r.stdout.splitlines():
        f = ln.split(None, 5)
        if not f or f[0] != "PLAN" or f[1] == "grid":
            continue
        rest, _, sig = f[5].partition(" SIG ")
        pos = [tuple(map(int, p.split())) for p in re.findall(r"\{(-?\d+ -?\d+)\}", rest)]
        plans.append(dict(k=int(f[1]), w=int(f[2]), h=int(f[3]), area=int(f[4]), pos=pos,
                          sig=sig.strip()))
    sigs = [q["sig"] for q in plans]
    if not all(sigs) or len(set(sigs)) != len(sigs):
        # the signature is what a resample is matched on, so two plans
        # sharing one would resample the wrong plan (pairwise child order,
        # the key this replaced, mapped 204 plans onto 156 keys)
        sys.exit("soc_plan_search: plan signatures missing or not unique")
    return plans


def parse(log):
    t = log
    die = re.search(r"die (\d+)x(\d+)", t)
    m = re.search(r"FAILED -- (\d+) overlaps, (\d+) unplaced, (\d+) audit", t)
    clean = bool(re.search(r"^soc.tcl: clean --", t, re.M))
    wl = re.findall(r"total detailed WL = (\d+)", t)
    first = re.search(r"^soc.tcl: dirty \((\d+) overlaps, (\d+) unplaced", t, re.M)
    out = dict(die=[int(die.group(1)), int(die.group(2))] if die else None,
               clean=clean, wl=int(wl[-1]) if wl else None)
    if m:
        out.update(ovl=int(m.group(1)), unpl=int(m.group(2)), viol=int(m.group(3)))
    elif clean:
        out.update(ovl=0, unpl=0, viol=0)
    else:
        out.update(ovl=None, unpl=None, viol=None, timeout=True)
    if first:
        out.update(first_ovl=int(first.group(1)), first_unpl=int(first.group(2)))
    return out


def chip(cache, logdir, knobs, fix=None, heal=False, threads=None, timeout=900):
    args = [*BASE, *knobs]
    if fix is not None:
        args += ["-FIX", fix]
    if not heal:
        args += ["-noheal"]
    pre = ["-j", str(threads)] if threads else []
    # the timeout is part of a run's identity: a run cut off at one budget
    # says nothing about a larger one, so a rerun with a longer
    # --heal-timeout must run again rather than replay the cached cutoff
    key = " ".join(shlex.quote(a) for a in ["btcl", *pre, "soc.tcl", *args]) \
        + f" #timeout={timeout}"
    got = cache.get(key)
    if got is not None:
        return got
    tag = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[-150:]
    t0 = time.time()
    # its own process group, so a timeout takes the engine server with it
    pr = subprocess.Popen([str(BTCL), *pre, str(SOC), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                          start_new_session=True)
    try:
        log, _ = pr.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(pr.pid, 9)
        log, _ = pr.communicate()
    secs = round(time.time() - t0, 1)
    (logdir / (tag + ".log")).write_text(log)
    res = parse(log)
    res["secs"] = secs
    res["cmd"] = key
    cache.put(key, res)
    return res


def score(r):
    if r.get("unpl") is None:
        return float("inf")
    return r["unpl"] + 16 * r["ovl"]


def rank_resampled(top, n_samples):
    """Rank re-sampled plans in place by their mean score over ALL
    `n_samples` samples.  A plan the slack pruned from a perturbed list was
    never tried there, so a mean over fewer samples is not comparable with
    one over all of them: such a plan is marked incomplete, ranked after
    every complete one, and never healed (Codex P2 on #961)."""
    for p in top:
        sc = [score(r) for r in p["samples"]]
        p["scores"] = sc
        p["complete"] = len(sc) == n_samples
        p["mean"] = sum(sc) / len(sc) if p["complete"] else float("inf")
    top.sort(key=lambda p: (not p["complete"], p["mean"], p["area"]))
    return top


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--knobs", required=True)
    ap.add_argument("--cell", default="cluster_cell")
    ap.add_argument("--slack", type=float, default=0.5)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--heal", type=int, default=4)
    ap.add_argument("--heal-timeout", type=int, default=300)
    ap.add_argument("--out", default="plan_search")
    a = ap.parse_args()

    out = Path(a.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    cache = Cache(out / "runs.json")
    knobs = knob_list(a.knobs)
    kn = set_knob(set_knob(knobs, "PACK", "slice"), "FIXSLACK", a.slack)
    fix = lambda k: f"{a.cell} {k}"

    # 1. SCREEN
    plans = list_plans(a.cell, kn, a.slack)
    print(f"[screen] {len(plans)} plans of {a.cell}", flush=True)
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(chip, cache, out / "logs", kn, fix(p["k"]), False, 1): p for p in plans}
        for f in cf.as_completed(futs):
            p = futs[f]
            p["screen"] = f.result()
    plans.sort(key=lambda p: (score(p["screen"]), p["area"]))
    for p in plans[:a.top]:
        r = p["screen"]
        print(f"[screen] plan {p['k']:>3} {p['w']}x{p['h']} die {r['die']} score {score(r)}", flush=True)

    # 2. RESAMPLE at PAD+1 and at GAP = M + 1, the same arrangement
    pad = int(get_knob(kn, "PAD", 24))
    gap = int(get_knob(kn, "GAP", 16))
    perturbed = [set_knob(kn, "PAD", pad + 1),
                 set_knob(set_knob(kn, "GAP", gap + 1), "M", gap + 1)]
    lists = [list_plans(a.cell, pk, a.slack) for pk in perturbed]
    maps = [{} for _ in perturbed]
    for i, pl in enumerate(lists):
        for q in pl:
            maps[i][q["sig"]] = q["k"]
    top = plans[:a.top]
    jobs = []
    for p in top:
        p["samples"] = [p["screen"]]
        for i, pk in enumerate(perturbed):
            k2 = maps[i].get(p["sig"])
            if k2 is not None:
                jobs.append((p, pk, k2))
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(chip, cache, out / "logs", pk, fix(k2), False, 1): p for p, pk, k2 in jobs}
        for f in cf.as_completed(futs):
            futs[f]["samples"].append(f.result())
    rank_resampled(top, 1 + len(perturbed))
    for p in top:
        mean = f"mean {p['mean']:.0f}" if p["complete"] else "INCOMPLETE (not healed)"
        print(f"[resample] plan {p['k']:>3} die {p['screen']['die']} scores {p['scores']} "
              f"{mean}", flush=True)

    # 3. HEAL the best by mean, and the two baselines
    heals = [("plan %d" % p["k"], kn, fix(p["k"])) for p in top[:a.heal] if p["complete"]]
    heals += [("slice choice", set_knob(knobs, "PACK", "slice"), None),
              ("grid packer", grid_knobs(knobs), None)]
    with cf.ThreadPoolExecutor(2) as ex:
        futs = {ex.submit(chip, cache, out / "logs", k, f, True, None, a.heal_timeout): n
                for n, k, f in heals}
        res = {futs[f]: f.result() for f in cf.as_completed(futs)}
    print("\n| candidate | die | area | first check | end | detailed WL | s |")
    print("|---|---|---|---|---|---|---|")
    for n, _k, _f in heals:
        r = res[n]
        die = r["die"] or [0, 0]
        first = (f"{r.get('first_unpl', 0)}u/{r.get('first_ovl', 0)}o"
                 if "first_unpl" in r else "clean")
        end = ("clean" if r["clean"] else "timeout" if r.get("timeout")
               else f"{r['unpl']}u/{r['ovl']}o/{r['viol']}v")
        wl = f"{r['wl']:,}" if r["clean"] and r["wl"] else "–"
        print(f"| {n} | {die[0]}x{die[1]} | {die[0]*die[1]:,} | {first} | {end} | {wl} | {r['secs']} |")
    (out / "summary.json").write_text(json.dumps(dict(plans=plans, heals=res), indent=1, default=str))


if __name__ == "__main__":
    main()
