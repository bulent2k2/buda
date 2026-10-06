#!/usr/bin/env python3
"""Classify the cross-bundle shorts of a routed checkpoint by cause.

For each short pair (two wires of different nets with real metal overlap on
one layer) it asks which wire is stretched past its bus segment's abstract
span, which partner bit that wire followed, and why the partner bit sits
outside the partner's abstract footprint: a leaf-cell keepout, an overlap with
another bundle's footprint in the abstract plan, a neighbour's bit spilled
into it, or none (the footprint is simply wide).  Reads the persisted tables
only, with the judge's own readers.  See docs/internal/cross_bundle_shorts_study.md.

    python3 tools/experiment/short_causes.py a.bdb [b.bdb ...]
"""
import sys,sqlite3,collections,os
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'..'))
import independent_audit as ia
def analyse(path):
    c=sqlite3.connect(path); c.row_factory=sqlite3.Row
    layers=ia.read_layer_stack(c)
    leaf,_=ia.read_leaf_keepouts(c,layers) if layers else ({},None)
    decl,univ=ia.read_keepouts(c)
    bs={}
    for b,s,l,h,x1,y1,x2,y2,tp,w in c.execute("select bundle_id,seg_idx,layer,is_horiz,x1,y1,x2,y2,track_position,width from bus_segment where placed=1"):
        bs[(b,s)]=dict(layer=l,h=bool(h),a=(min(x1,x2),max(x1,x2)) if h else (min(y1,y2),max(y1,y2)),tp=tp,w=w)
    conn=collections.defaultdict(set)
    for b,f,t in c.execute("select bundle_id,from_seg,to_seg from bus_via"):
        conn[(b,f)].add(t); conn[(b,t)].add(f)
    bits={}; bylayer=collections.defaultdict(list)
    for b,s,bit,n,l,h,x1,y1,x2,y2,tp,wd in c.execute("select bundle_id,seg_idx,bit_index,net_id,layer,is_horiz,x1,y1,x2,y2,track_position,width from net_segment"):
        al=(min(x1,x2),max(x1,x2)) if h else (min(y1,y2),max(y1,y2))
        r=(b,s,bit,n,l,bool(h),al,tp,wd); bits[(b,s,bit)]=r; bylayer[l].append(r)
    def foot(k):
        d=bs[k]; return (d['tp']-d['w']/2,d['tp']+d['w']/2)
    def outside_foot(r):
        k=(r[0],r[1]);
        if k not in bs: return False
        f=foot(k); return r[7]<f[0]-1e-6 or r[7]>f[1]+1e-6
    def keepout_over(k):
        d=bs[k]; f=foot(k); a=d['a']
        zs=list(leaf.get(d['layer'],[]))+list(decl.get(d['layer'],[]))+list(univ)
        for z in zs:
            x1,y1,x2,y2=z[:4]
            za=(x1,x2) if d['h'] else (y1,y2); zp=(y1,y2) if d['h'] else (x1,x2)
            if za[1]>a[0] and za[0]<a[1] and zp[1]>f[0] and zp[0]<f[1]: return True
        return False
    def spill_into(k):
        d=bs[k]; f=foot(k); a=d['a']
        for r in bylayer[d['layer']]:
            if (r[0],r[1])==k or r[0]==k[0]: continue
            if f[0]<=r[7]<=f[1] and r[6][1]>a[0] and r[6][0]<a[1]:
                kk=(r[0],r[1])
                if kk in bs and outside_foot(r): return True
        return False
    def abstract_neighbour(k):
        d=bs[k]; f=foot(k); a=d['a']
        for r in bylayer[d['layer']]:
            if (r[0],r[1])==k or r[0]==k[0]: continue
            if f[0]<=r[7]<=f[1] and r[6][1]>a[0] and r[6][0]<a[1]: return True
        return False
    def abstract_overlap(k):
        d=bs[k]; f=foot(k); a=d['a']
        for k2,d2 in bs.items():
            if k2[0]==k[0] or d2['layer']!=d['layer']: continue
            f2=foot(k2); a2=d2['a']
            if a2[1]>a[0] and a2[0]<a[1] and f2[1]>f[0]+1e-6 and f2[0]<f[1]-1e-6: return True
        return False
    res=collections.Counter()
    for l,rs in bylayer.items():
        rs=sorted(rs,key=lambda r:r[6][0])
        for i,A in enumerate(rs):
            for B in rs[i+1:]:
                if B[6][0]>=A[6][1]: break
                if A[0]==B[0] or A[3]==B[3]: continue
                ov=(max(A[6][0],B[6][0]),min(A[6][1],B[6][1]))
                if ov[1]<=ov[0]: continue
                # perpendicular overlap: tracks are points; need width overlap ~ same track
                if abs(A[7]-B[7])>=(A[8]+B[8])/2-1e-9: continue
                # classify per wire
                kinds=[]
                for W in (A,B):
                    k=(W[0],W[1])
                    if k not in bs: kinds.append('noseg'); continue
                    lo,hi=bs[k]['a']
                    if not (ov[0]<lo-1e-6 or ov[1]>hi+1e-6): kinds.append('inside'); continue
                    # stretched: find driver partner bit
                    drv=None
                    for p in conn[k]:
                        pr=bits.get((W[0],p,W[2]))
                        if pr: 
                            if drv is None or abs(pr[7]-(ov[0] if ov[0]<lo else ov[1]))<abs(drv[7]-(ov[0] if ov[0]<lo else ov[1])): drv=pr
                    if drv is None: kinds.append('stretch:no_partner_bit'); continue
                    pk=(drv[0],drv[1])
                    if not outside_foot(drv): kinds.append('stretch:partner_in_footprint'); continue
                    if keepout_over(pk): kinds.append('stretch:partner_displaced_by_keepout'); continue
                    if abstract_overlap(pk): kinds.append('stretch:partner_displaced_abstract_overlap'); continue
                    if spill_into(pk): kinds.append('stretch:partner_displaced_by_neighbour_spill'); continue
                    if abstract_neighbour(pk): kinds.append('stretch:partner_displaced_neighbour_in_footprint'); continue
                    kinds.append('stretch:partner_displaced_other')
                res[tuple(sorted(kinds))]+=1
    return res
for p in sys.argv[1:]:
    r=analyse(p); print('==',p,sum(r.values()),'short pairs')
    for k,v in r.most_common(): print('  ',v,k)
