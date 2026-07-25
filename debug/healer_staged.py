#!/usr/bin/env python3
"""Staged / cumulative healer experiment.

For each flow with DNUTS opens (no healer), impose the CANONICAL healer
sequence and report opens / NUTS overlaps / CUMULATIVE runtime at five
cumulative stages:
  S0 base      run_nuts ; run_detailed_nuts
  S1 +NCa      run_nuts ; negotiate ; run_detailed_nuts
  S2 +RRa      run_nuts ; negotiate ; ripup ; run_detailed_nuts
  S3 +NCb      run_nuts ; negotiate ; ripup ; run_detailed_nuts ; negotiate
  S4 +RRb      run_nuts ; negotiate ; ripup ; run_detailed_nuts ; negotiate ; ripup

Controls identical to healer_experiment.py: script_path set (kSegsRel + gates
as in the real CLI), `_dead_span_auto_at_run_nuts=False` so S0 is pre-healing;
the stage-b `_heal_dead_spans` fold is part of NCb/RRb (measured as it behaves).

The setup (everything up to and including the FIRST run_nuts) is taken from the
flow with all healer lines stripped; the canonical healer sequence is then
imposed uniformly, so every flow is compared under the same protocol.
"""
import sys, os, io, contextlib, importlib, time, json
ROOT = "/Users/ben/src/git/buda/cc"
sys.path.insert(0, ROOT + "/build")
sys.path.insert(0, ROOT + "/src")
import buda, buda_cli  # noqa: E402

HEALERS = ("negotiate_congestion", "ripup_reroute")
SKIP = ("#", "visualize", "exit")
OUT = "./healer_staged_out.txt"

# flows with opens (from the first sweep)
FLOWS = [
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/big_3bundles_sel_pure_mst_topo.buda",
    "flow/big_data_test/tc3a.buda",
    "flow/big_data_test/big2/big2_noviz.buda",
    "flow/big_data_test/big2/tc3b_flat.buda",
    "flow/hbundles/06_multipin_stress.buda",
    "flow/hbundles/07_wide_fan_stress.buda",
    "flow/rnr/mix.buda",
    "flow/rnr/mix2.buda",
    "flow/rnr/mix2_fast.buda",
    "flow/rnr/mix2_fast_bottomup.buda",
    "flow/rnr/mix2_fast_topdown.buda",
    "flow/rnr/slowdown_rnr.buda",
]


def setup_cmds(absf):
    """Setup up to and including the FIRST run_nuts, healers stripped, no
    run_detailed_nuts (added per stage)."""
    out = []
    for raw in open(absf):
        line = raw.strip()
        if not line or line.startswith(SKIP):
            continue
        if line.split()[0] in HEALERS:
            continue
        if line.split()[0] == "run_detailed_nuts":
            continue          # added per stage
        out.append(line)
        if line.split()[0] == "run_nuts":
            break             # stop at the first run_nuts
    return out


def new_session(absf):
    s = buda_cli.BudaSession(); s.no_viz = True
    s.script_path = absf
    s._dead_span_auto_at_run_nuts = False
    return s


def do(s, cmd):
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        try: s.do_command(cmd)
        except SystemExit: pass


def metric(s):
    d = s.detailed_result
    return (s.nuts_result.num_overlaps if s.nuts_result else -1,
            d.num_unplaced if d else -1)


def run_flow(flow):
    """Returns dict stage -> (ov, un, cumulative_seconds)."""
    absf = os.path.join(ROOT, flow); os.chdir(os.path.dirname(absf))
    res = {}

    # Run 1: S0 (base) and S1 (+NCa) share the no-RRa prefix but diverge at
    # the stage-a healer, so run each; capture cumulative time from t0.
    # --- S0 ---
    importlib.reload(buda_cli)
    s = new_session(absf); t0 = time.perf_counter()
    for c in setup_cmds(absf): do(s, c)
    do(s, "run_detailed_nuts")
    res["S0"] = (*metric(s), round(time.perf_counter() - t0, 1))

    # --- S1: +NCa ---
    importlib.reload(buda_cli)
    s = new_session(absf); t0 = time.perf_counter()
    for c in setup_cmds(absf): do(s, c)
    do(s, "negotiate_congestion")
    do(s, "run_detailed_nuts")
    res["S1"] = (*metric(s), round(time.perf_counter() - t0, 1))

    # --- S2/S3/S4: shared chain, checkpoint inline ---
    importlib.reload(buda_cli)
    s = new_session(absf); t0 = time.perf_counter()
    for c in setup_cmds(absf): do(s, c)
    do(s, "negotiate_congestion")           # NCa
    do(s, "ripup_reroute")                   # RRa
    do(s, "run_detailed_nuts")
    res["S2"] = (*metric(s), round(time.perf_counter() - t0, 1))
    do(s, "negotiate_congestion")           # NCb
    res["S3"] = (*metric(s), round(time.perf_counter() - t0, 1))
    do(s, "ripup_reroute")                    # RRb
    res["S4"] = (*metric(s), round(time.perf_counter() - t0, 1))
    return res


def main():
    log = open(OUT, "w")
    def emit(m):
        print(m); log.write(m + "\n"); log.flush()
    emit("stages: S0 base | S1 +NCa | S2 +RRa | S3 +NCb | S4 +RRb  (ov/un @ cum-seconds)")
    emit(f"{'flow':34} {'S0':>13} {'S1 +NCa':>13} {'S2 +RRa':>13} {'S3 +NCb':>13} {'S4 +RRb':>13}")
    allres = {}
    for flow in FLOWS:
        try:
            r = run_flow(flow)
        except Exception as e:
            emit(f"{os.path.basename(flow):34}  ERR {type(e).__name__}: {str(e)[:40]}")
            continue
        allres[flow] = r
        def cell(k):
            ov, un, t = r[k]
            return f"{ov}/{un}@{t}"
        emit(f"{os.path.basename(flow):34} "
             f"{cell('S0'):>13} {cell('S1'):>13} {cell('S2'):>13} "
             f"{cell('S3'):>13} {cell('S4'):>13}")
    log.write("\nJSON " + json.dumps(allres) + "\n"); log.flush()
    emit("\nDONE")


if __name__ == "__main__":
    main()
