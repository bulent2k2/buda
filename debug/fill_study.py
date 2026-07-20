#!/usr/bin/env python3
"""Corpus fill study: after each flow's run_nuts, run report_wl and collect the
per-bundle fill of routed abstract WL within the candidate's [lo..hi] envelope.
Validates the 'realized = lo + alpha*(hi-lo)' predictor for the b44 fix."""

# after pr#311

import sys, os, io, re, contextlib
sys.path.insert(0, "/home/user/buda/build")
sys.path.insert(0, "/home/user/buda/src")

FLOWS = [
    "flow/rnr/mix.buda",
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/big2/big2_noviz.buda",
    "flow/channel_stress.buda",
    "demo/comprehensive_demo.buda",
    "flow/big_data_test/b44.buda",
]

def study(path):
    import buda_cli
    repo = "/home/user/buda"
    full = os.path.join(repo, path)
    os.chdir(os.path.dirname(full))
    s = buda_cli.BudaSession()
    s.no_viz = True
    ran_nuts = False
    with open(full) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("visualize", "exit")):
                continue
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    s.do_command(line)
            except SystemExit:
                pass
            except Exception as e:
                print(f"  [skip cmd error] {line[:50]}: {e}", file=sys.stderr)
            if line.startswith("run_nuts"):
                ran_nuts = True
    if not ran_nuts:
        return []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            s.do_command("report_wl")
        except Exception as e:
            print(f"  [report_wl error] {e}", file=sys.stderr)
            return []
    out = buf.getvalue()
    # parse the abstract table rows:  bundle bits lo WL hi jog fill%
    fills = []
    for m in re.finditer(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%",
                         out, re.M):
        b, bits, lo, wl, hi, jog, fill = map(int, m.groups())
        if hi > lo:
            fills.append((b, lo, wl, hi, fill))
    return fills

all_fills = []
for fl in FLOWS:
    try:
        fills = study(fl)
    except Exception as e:
        print(f"{fl}: FAILED {e}")
        continue
    fs = [f[4] for f in fills]
    if fs:
        fs_sorted = sorted(fs)
        med = fs_sorted[len(fs)//2]
        mean = sum(fs)/len(fs)
        print(f"{fl}: n={len(fs)} fill mean={mean:.1f}% median={med}% "
              f"p90={fs_sorted[int(len(fs)*0.9)]}% max={max(fs)}%")
        all_fills += fs
    else:
        print(f"{fl}: no bundles with spread")

if all_fills:
    fs = sorted(all_fills)
    n = len(fs)
    print(f"\nOVERALL: n={n} mean={sum(fs)/n:.1f}% median={fs[n//2]}% "
          f"p75={fs[int(n*0.75)]}% p90={fs[int(n*0.9)]}% max={max(fs)}%")
