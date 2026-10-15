"""Read the sweep written by adaptive_reach.py and score five policies:
always short, always long, the shipped default (long except designs with
bottom-up marks), and two whole-design adaptive rules -- 'adapt_shorts' (try
long only if the short run left cross-bundle shorts) and 'adapt_dirty' (try
long if the short run is dirty at all) -- both taking the long run only when
it is componentwise no worse on (overlaps, unplaced, viol_bundles) and
strictly better on one.  Also prints, for flows whose short run left shorts,
the abstract-WL cost of lengthening every bundle against only the bundles the
shorts sit on.   Usage: python3 tools/experiment/adaptive_reach_analyze.py OUT.json"""
import json, os, re, sys

R = json.load(open(sys.argv[1])); by = {}
for r in R:
    if 'err' not in r: by.setdefault(r['flow'], {})[r['mode']] = r

def bottom_up(f):
    if 'bottom' in f: return True
    def txt(p, depth=0):
        try: t = open(p).read()
        except OSError: return ''
        out = t
        if depth < 2:
            for m in re.finditer(r'^\s*source\s+(\S+)', t, re.M):
                out += txt(os.path.join(os.path.dirname(p), m.group(1)), depth + 1)
        return out
    return bool(re.search(r'^\s*set_bottom_up\s+(?!\S+\s+off)', txt(f), re.M))

key = lambda r: (r['ovl'] or 0, r['unpl'] or 0, r['viol'] or 0)
dirty = lambda r: sum(key(r)) > 0
better = lambda a, b: all(x <= y for x, y in zip(key(a), key(b))) and key(a) != key(b)
pol = {k: [0, 0, 0, 0, 0.0, 0] for k in ('short', 'long', 'default', 'adapt_shorts', 'adapt_dirty')}
for f, d in sorted(by.items()):
    if 'off' not in d or 'on' not in d: continue
    s, l = d['off'], d['on']
    pick = {'short': s, 'long': l, 'default': s if bottom_up(f) else l}
    pick['adapt_shorts'] = l if ((s.get('xshorts') or 0) > 0 and (better(l, s) or (
        l.get('xshorts', 0) < s['xshorts'] and key(l) <= key(s)))) else s
    pick['adapt_dirty'] = l if (dirty(s) and better(l, s)) else s
    for k, r in pick.items():
        p = pol[k]; p[0] += r['ovl'] or 0; p[1] += r['unpl'] or 0; p[2] += r['viol'] or 0
        p[3] += 0 if dirty(r) else 1; p[4] += r['awl'] or 0; p[5] += r.get('xshorts') or 0
print("policy          ovl  unpl  viol  clean  absWL(sum)      xshorts")
for k, p in pol.items(): print(f"{k:14} {p[0]:5} {p[1]:5} {p[2]:5} {p[3]:5} {p[4]:14.0f} {p[5]:5}")
print("\nabstract WL delta (long - short): every bundle vs only the bundles the short run's shorts sit on")
for f, d in sorted(by.items()):
    s, l = d.get('off'), d.get('on')
    if not s or not l or not s.get('xs_bundles'): continue
    sa, la = s['awl_b'], l['awl_b']
    print(f"{f:50} bundles={len(sa):4} affected={len(s['xs_bundles']):2} "
          f"all={sum(la.get(k, 0) - sa.get(k, 0) for k in sa):8} "
          f"affected={sum(la.get(str(k), 0) - sa.get(str(k), 0) for k in s['xs_bundles']):6}")
