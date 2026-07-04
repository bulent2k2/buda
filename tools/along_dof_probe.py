"""Probe: where would the along-flex trunk DOF actually save wirelength?

For each corpus flow, after routing, rebuild ConnTopology on every bundle's
SELECTED topology and inspect each segment's flex ends (Stage-A along-flex
fields).  A flex end whose along-coverage floor sits strictly INSIDE the
segment's generated extent carries "dead wire" the DOF could contract away.

BUT generation already extends a spine past its extreme stub to cover
pass-through blocks, so that extension is real coverage, not dead wire.  The
probe therefore splits each flex-end gap into:
  - COVERAGE: a connected block of this bundle lies in the gap (the extension
    exists to reach it by containment) -> NOT contractible.
  - DEAD:     no block in the gap -> genuinely removable by the DOF.

Only DEAD wire is a WL saving the DOF could realize with no new open.

Usage: PYTHONPATH=build:tools python3 tools/along_dof_probe.py
"""
import contextlib, io, os, sys

ROOT = "/home/user/buda"
sys.path.insert(0, ROOT + "/src")
import buda_cli
import buda

CORPUS = [
    "flow/big_data_test/tc3a_flat.buda",
    "flow/comprehensive_demo.buda",
    "flow/channel_stress.buda",
    "flow/four_blocks.buda",
    "flow/four_blocks_3_bundles.buda",
    "flow/dogleg1.buda", "flow/dogleg2.buda",
    "flow/double_detour.buda",
    "flow/big_data_test/big2/b4_bus_077.buda",
    "flow/planner6.buda",                       # double_detour
    "flow/dnuts2.buda",                          # double_detour
    "flow/rnr/mix.buda",
]


def block_rects(fp, name):
    try:
        rs = fp.get_block_rects(name)
        if rs:
            return [(r.x1, r.y1, r.x2, r.y2) for r in rs]
    except Exception:
        pass
    try:
        r = fp.get_block_bounds(name)
        return [(r.x1, r.y1, r.x2, r.y2)]
    except Exception:
        return []


def gap_has_block(cs, topo, fp, lo, hi):
    """Does any connected block of this topology occupy the along-gap [lo,hi]
    at the segment's perp position?  If so the extension is coverage."""
    try:
        names = list(topo.connected_block_names)
    except Exception:
        names = []
    for nm in names:
        for (x1, y1, x2, y2) in block_rects(fp, nm):
            if cs.horiz:
                # gap is in x; perp is y
                if x1 <= hi and x2 >= lo and y1 <= cs.perp_pos <= y2:
                    return True
            else:
                if y1 <= hi and y2 >= lo and x1 <= cs.perp_pos <= x2:
                    return True
    return False


def probe_flow(path):
    s = buda_cli.BudaSession(); s.no_viz = True
    os.chdir(os.path.join(ROOT, os.path.dirname(path)))
    fname = os.path.basename(path)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            for line in open(fname):
                c = line.strip()
                if not c or c.startswith('#'):
                    continue
                if c.split()[0] in ('visualize', 'visualize_topologies', 'exit',
                                    'report_wirelength', 'report_wl'):
                    continue
                s.do_command(c)
    except BaseException as e:
        return f"ERROR {type(e).__name__}: {e}", []

    dead_total, cover_total, hits = 0, 0, []
    for w in s.bundles:
        sel = w.plan.selected_topology_index
        if sel is None or sel < 0 or sel >= len(w.input.candidates):
            continue
        topo = w.input.candidates[sel]
        ct = buda.ConnTopology()
        try:
            ct.build(topo, s.fp)
        except Exception:
            continue
        bid = w.input.original_bundle.id
        for si, cs in enumerate(ct.segs()):
            for end, flex, cov, ext in (
                ('hi', cs.along_flex_hi, cs.along_cover_hi, cs.along_hi),
                ('lo', cs.along_flex_lo, cs.along_cover_lo, cs.along_lo),
            ):
                if not flex:
                    continue
                gap = (ext - cov) if end == 'hi' else (cov - ext)
                if gap <= 0:
                    continue
                glo, ghi = (cov, ext) if end == 'hi' else (ext, cov)
                if gap_has_block(cs, topo, s.fp, glo, ghi):
                    cover_total += gap
                else:
                    dead_total += gap
                    hits.append((bid, si, end, gap, topo.type))
    return None, (dead_total, cover_total, hits)


def main():
    print(f"{'flow':<48} {'DEAD':>8} {'COVER':>8}  top DEAD segments")
    grand_dead = 0
    for path in CORPUS:
        if not os.path.exists(os.path.join(ROOT, path)):
            print(f"{path:<48} MISSING"); continue
        err, res = probe_flow(path)
        if err:
            print(f"{path:<48} {err}"); continue
        dead, cover, hits = res
        grand_dead += dead
        hits.sort(key=lambda h: -h[3])
        top = "; ".join(f"b{b}s{s}.{e}={g}({t})" for b, s, e, g, t in hits[:3])
        print(f"{os.path.basename(path):<48} {dead:>8} {cover:>8}  {top}")
    print(f"\nGRAND TOTAL contractible DEAD wire across corpus: {grand_dead}")


if __name__ == "__main__":
    main()
