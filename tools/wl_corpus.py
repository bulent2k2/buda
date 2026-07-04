"""Capture abstract + detailed wirelength (and overlap/open counts) for a
corpus of flows, for before/after comparison of a routing change.  Sources each
flow in-process (viz stripped) and reads the final routed state.

Usage: PYTHONPATH=build:tools python3 tools/wl_corpus.py [label] [out_path]
Writes <out_path> (default: $TMPDIR/wl_<label>.txt) with one line per flow.
"""
import contextlib, io, os, sys, tempfile

# Repo root derived from this file's location (tools/ is one level under root),
# so the harness works in any checkout, not just /home/user/buda.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import buda_cli

CORPUS = [
    "flow/big_data_test/tc3a_flat.buda",    # 80 bundles, ~96% trunks — primary target
    "flow/comprehensive_demo.buda",
    "flow/channel_stress.buda",
    "flow/four_blocks.buda",
    "flow/four_blocks_3_bundles.buda",
    "flow/dogleg1.buda", "flow/dogleg2.buda",
    "flow/double_detour.buda",              # flexible-root exemplar
    "flow/big_data_test/big2/b4_bus_077.buda",   # the x5772 case
    "flow/rnr/mix.buda",                    # hier
]

def wl(segments):
    per_layer, total, unpl = {}, 0.0, 0
    for s in segments:
        if getattr(s, 'placed', True) is False:
            unpl += 1; continue
        L = abs(s.span_hi - s.span_lo)
        per_layer[s.layer] = per_layer.get(s.layer, 0.0) + L
        total += L
    return total, per_layer, unpl

def run_flow(path):
    s = buda_cli.BudaSession(); s.no_viz = True
    os.chdir(os.path.join(ROOT, os.path.dirname(path)))
    fname = os.path.basename(path)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            for line in open(fname):
                c = line.strip()
                if not c or c.startswith('#'): continue
                if c.split()[0] in ('visualize', 'visualize_topologies', 'exit',
                                    'report_wirelength', 'report_wl'):
                    continue
                s.do_command(c)
    except BaseException as e:
        return f"ERROR {type(e).__name__}: {e}"
    if s.nuts_result is None:
        return "no nuts_result"
    ab_t, ab_l, _ = wl(s.nuts_result.segments)
    ovl = s.nuts_result.num_overlaps
    lstr = " ".join(f"L{k}={v:.0f}" for k, v in sorted(ab_l.items()))
    out = f"abstractWL={ab_t:.0f} overlaps={ovl} [{lstr}]"
    if s.detailed_result is not None:
        de_t, _, _ = wl(s.detailed_result.net_segments)
        out += f" detailedWL={de_t:.0f} unplaced={s.detailed_result.num_unplaced}"
    return out

def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    # Output path: 2nd arg if given, else a portable temp file (dir always exists).
    outp = sys.argv[2] if len(sys.argv) > 2 \
        else os.path.join(tempfile.gettempdir(), f"wl_{label}.txt")
    os.makedirs(os.path.dirname(os.path.abspath(outp)), exist_ok=True)
    lines = []
    for path in CORPUS:
        if not os.path.exists(os.path.join(ROOT, path)):
            lines.append(f"{path}: MISSING"); continue
        res = run_flow(path)
        lines.append(f"{path}: {res}")
        print(lines[-1], flush=True)
    open(outp, "w").write("\n".join(lines) + "\n")
    print("wrote", outp)

if __name__ == "__main__":
    main()
