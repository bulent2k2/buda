"""Probe: where would the along-flex trunk DOF actually save wirelength?

For each corpus flow, after routing, rebuild ConnTopology on each bundle's
candidate topologies and inspect every segment's flex ends (Stage-A along-flex
fields).  A flex end whose along-coverage floor sits strictly INSIDE the
segment's generated extent carries "dead wire" the DOF could contract away.

BUT generation already extends a spine past its extreme stub to cover
pass-through blocks, so that extension is real coverage, not dead wire.  The
probe therefore splits each flex-end gap into:
  - COVERAGE: a connected block of this bundle lies in the gap (the extension
    exists to reach it by containment) -> NOT contractible.
  - DEAD:     no block in the gap -> genuinely removable by the DOF.

Only DEAD wire is a WL saving the DOF could realize with no new open.

Two scopes are reported per flow so the finding is double-checked against every
candidate the planner *could* have picked, not just the one it did:
  - SELECTED: dead wire in each bundle's selected topology only.
  - ALL:      dead wire summed over ALL candidate topologies of every bundle
              (the planner's whole search space).
If ALL is 0 the DOF is a no-op for the entire candidate set, not merely the
chosen routes.

Usage: PYTHONPATH=build:tools python3 tools/along_dof_probe.py [--verbose]
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

    # Two scopes: SELECTED = each bundle's chosen topology; ALL = every candidate.
    sel_dead = sel_cover = 0
    all_dead = all_cover = 0
    n_cands = 0
    hits = []            # (bid, si, end, gap, type, is_selected) for ALL-scope dead

    def scan(topo, cs, si, bid, selected):
        nonlocal sel_dead, sel_cover, all_dead, all_cover
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
            is_cover = gap_has_block(cs, topo, s.fp, glo, ghi)
            if is_cover:
                all_cover += gap
                if selected:
                    sel_cover += gap
            else:
                all_dead += gap
                hits.append((bid, si, end, gap, topo.type, selected))
                if selected:
                    sel_dead += gap

    for w in s.bundles:
        sel = w.plan.selected_topology_index
        bid = w.input.original_bundle.id
        cands = list(w.input.candidates)
        for ci, topo in enumerate(cands):
            ct = buda.ConnTopology()
            try:
                ct.build(topo, s.fp)
            except Exception:
                continue
            n_cands += 1
            selected = (ci == sel)
            for si, cs in enumerate(ct.segs()):
                scan(topo, cs, si, bid, selected)
    return None, (sel_dead, sel_cover, all_dead, all_cover, n_cands, hits)


def main():
    verbose = "--verbose" in sys.argv
    print(f"{'flow':<40} {'selDEAD':>7} {'selCOV':>7} {'allDEAD':>7} "
          f"{'allCOV':>7} {'#cand':>6}  top DEAD (all candidates)")
    g_sel_dead = g_all_dead = g_cands = 0
    for path in CORPUS:
        if not os.path.exists(os.path.join(ROOT, path)):
            print(f"{path:<40} MISSING"); continue
        err, res = probe_flow(path)
        if err:
            print(f"{os.path.basename(path):<40} {err}"); continue
        sd, sc, ad, ac, nc, hits = res
        g_sel_dead += sd; g_all_dead += ad; g_cands += nc
        hits.sort(key=lambda h: -h[3])
        top = "; ".join(f"b{b}s{s}.{e}={g}({t}{'*' if sel else ''})"
                        for b, s, e, g, t, sel in hits[:3])
        print(f"{os.path.basename(path):<40} {sd:>7} {sc:>7} {ad:>7} "
              f"{ac:>7} {nc:>6}  {top}")
        if verbose and hits:
            for b, si, e, g, t, sel in hits:
                print(f"      b{b} seg{si} {e} gap={g} type={t} "
                      f"{'SELECTED' if sel else 'candidate'}")
    print(f"\nGRAND TOTAL removable DEAD wire — selected topos: {g_sel_dead}"
          f" | ALL {g_cands} candidate topos: {g_all_dead}")
    print("(* = the hit is in the planner-selected candidate)")


if __name__ == "__main__":
    main()
