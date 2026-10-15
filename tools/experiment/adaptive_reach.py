"""Adaptive junction-reach experiment (docs/internal/cross_bundle_shorts_study.md,
"Adaptive junction reach, measured"):

Run from the repo root:  PYTHONPATH=build:src:tools python3 tools/experiment/adaptive_reach.py OUT.json
Then read it with tools/experiment/adaptive_reach_analyze.py OUT.json.

 every corpus flow under short ('off') and
long ('on', every design) spans; record final QoR, cross shorts + their bundles,
per-bundle abstract/detailed wirelength."""
import contextlib, io, json, os, sys, time
sys.path[:0] = ['build', 'src', 'tools']
ROOT = os.getcwd()
import qor_corpus as qc

def one(args):
    flow, mode = args
    os.chdir(ROOT)
    import buda_cli
    s = buda_cli.BudaSession(); s.no_viz = True
    s._junction_reach_mode = mode
    tmp = qc.private_log_dir(s)
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            d = os.path.dirname(flow)
            try:
                if d: os.chdir(d)
                s.do_command(f"source {os.path.basename(flow)}")
            finally:
                os.chdir(ROOT)
    except SystemExit as e:
        if e.code not in (0, None): return dict(flow=flow, mode=mode, err=f"exit {e.code}")
    except Exception as e:
        return dict(flow=flow, mode=mode, err=f"{type(e).__name__}: {str(e)[:80]}")
    ov = getattr(getattr(s, "nuts_result", None), "num_overlaps", None)
    dr = getattr(s, "detailed_result", None); nr = getattr(s, "nuts_result", None)
    un = getattr(dr, "num_unplaced", None)
    vb = qc._check_design_bundles(s)
    awl, dwl = qc._wirelengths(s)
    r = dict(flow=flow, mode=mode, ovl=ov, unpl=un, viol=vb, awl=awl, dwl=dwl,
             sec=round(time.time() - t0, 1))
    if dr is not None:
        cs = list(dr.cross_shorts)
        r["xshorts"] = len(cs)
        r["xs_bundles"] = sorted({c.bundle_a for c in cs} | {c.bundle_b for c in cs})
    try:
        r["awl_b"] = {str(k): round(v) for k, v in s._wirelength_by_bundle(nr.segments)[0].items()} if nr else None
        r["dwl_b"] = {str(k): round(v) for k, v in s._wirelength_by_bundle(
            [x for x in dr.net_segments if not getattr(x, "is_shield", False)])[0].items()} if dr else None
    except Exception as e:
        r["wl_err"] = str(e)[:60]
    return r

if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor
    out = sys.argv[1]
    flows = qc.CORPUS
    jobs = [(f, m) for f in flows for m in (sys.argv[2].split(",") if len(sys.argv) > 2 else ("off", "on"))]
    res = []
    with ProcessPoolExecutor(max_workers=4, initializer=qc._worker_init) as ex:
        for r in ex.map(one, jobs):
            res.append(r)
            json.dump(res, open(out, "w"))
            print(r["flow"], r["mode"], r.get("ovl"), r.get("unpl"), r.get("viol"), r.get("xshorts"), r.get("sec"), r.get("err", ""), flush=True)
