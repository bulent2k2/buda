# Decompose one layer's polygons into RECTANGLES, area-preserving, so
# `notch_obs.py`'s rectangle precondition holds on a real Magic GDS.
#
#   run_or.sh <run> rectify_gds.py GDS=<in.gds> LNUM=69 LDT=20 OUT=<out.gds>
#   (a KLayout script: pass values with -rd, which run_or.sh forwards)
#
# WHY.  `notch_obs.py` needs the macro's real metal as rectangles and REFUSES
# a non-rectangle rather than take its bbox -- correct, since a bbox
# over-claims and would obstruct metal that is not there.  But Magic streams
# routed metal as rectilinear BOUNDARYs: a wire with a jog is one 12- or
# 14-corner polygon, and on the tier-1a blocks that is 982 shapes on
# `pe_cell` alone, so the tool refuses every cell and the #896 fix cannot be
# measured at all.
#
# A trapezoid decomposition of an axis-aligned polygon IS a set of rectangles
# covering exactly the same area, so this changes the REPRESENTATION and not
# the geometry -- and it says so rather than assuming: the region's area is
# compared before and after and the script exits non-zero if they differ or
# if any output shape is not a box.  Feed the result to `notch_obs.py`
# unmodified.
import pya

ly = pya.Layout(); ly.read(gds)
li = ly.layer(int(lnum), int(ldt))
if li < 0:
    print(f"rectify_gds: no layer {lnum}/{ldt} in {gds}"); raise SystemExit(1)
top = ly.top_cell()
reg = pya.Region(top.begin_shapes_rec(li)).merged()
before, n_poly = reg.area(), 0
rects = pya.Region()
for p in reg.each():
    if p.is_box():
        rects.insert(p.bbox())
        continue
    n_poly += 1
    for t in p.decompose_trapezoids(pya.Polygon.TD_simple):
        rects.insert(t.bbox())
after = rects.area()
nonrect = sum(0 if r.is_box() else 1 for r in rects.each())
ok = (before == after and nonrect == 0)
print(f"  {gds.split('/')[-1]}: {n_poly} polygon(s) -> {rects.count()} rect(s); "
      f"non-rect left {nonrect}; area {before} -> {after} "
      f"({'IDENTICAL' if before == after else 'CHANGED'})")
if not ok:
    print("rectify_gds: refusing to write -- the decomposition changed the geometry")
    raise SystemExit(2)
top.clear(li)
for r in rects.each():
    top.shapes(li).insert(r.bbox())
ly.write(out)
print(f"    wrote {out}")
