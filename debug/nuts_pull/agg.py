import sys, ast
def load(fn):
    d={}
    for ln in open(fn):
        ln=ln.strip()
        if not ln.startswith('('): continue
        key=ast.literal_eval(ln[:ln.index(')')+1])
        val=ast.literal_eval(ln[ln.index(')')+1:].strip())
        d[key]=val
    return d
for tag,fn in (('old',sys.argv[1]),('new',sys.argv[2])):
    d=load(fn); dev=0.0; n=0; worst=0.0
    for k,(np,lo,hi,pos) in d.items():
        if np==0: continue
        pull=hi if np>0 else lo
        dd=abs(pos-pull); dev+=dd; n+=1; worst=max(worst,dd)
    print(f"{tag}: pulled={n} total_pull_deviation={dev:.0f} worst={worst:.0f}")
