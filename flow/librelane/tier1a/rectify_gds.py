# Decompose one layer's polygons into RECTANGLES, area-preserving, so
# `notch_obs.py`'s rectangle precondition holds on a real Magic GDS.
#
# `notch.sh N` runs it per cell; the hand form is a KLayout BATCH script, so
# it goes to `klayout -b -r` with its values as `-rd`, in the LibreLane image
# (there is no host KLayout in this recipe, and `pya` is KLayout's own
# module -- this is NOT an OpenROAD script and `phase0/measure/run_or.sh`,
# which runs `openroad -exit`, cannot carry it):
#
#   docker run --rm -v "$HOME:$HOME" -w "$PWD" ghcr.io/librelane/librelane:3.0.11 \
#       klayout -b -r rectify_gds.py \
#       -rd gds=<in.gds> -rd lnum=69 -rd ldt=20 -rd out=<out.gds>
#
# Every path must be inside a mount: a GDS outside $HOME is simply not there
# for the container, and the failure is `Unable to open file`, not a mount
# error.  `notch.sh` mounts the design tree when it is elsewhere.
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
# RECURSIVE: shapes on this layer may sit in subcells as well as the top,
# and `notch_obs.py` flattens through SREF/AREF, so the whole hierarchy is
# what it will see.
reg = pya.Region(top.begin_shapes_rec(li)).merged()
before, n_poly, n_trap = reg.area(), 0, 0
rects = pya.Region()
for p in reg.each():
    if p.is_box():
        rects.insert(p.bbox())
        continue
    n_poly += 1
    for tz in p.decompose_trapezoids(pya.Polygon.TD_simple):
        # Check the TRAPEZOID, not the bbox that is about to be inserted:
        # `is_box()` on an inserted `bbox()` is true by construction, so the
        # obvious check is vacuous.  An axis-aligned polygon decomposes into
        # rectangles; anything else would have its bbox OVER-cover, which the
        # area comparison below also catches -- this just names it precisely.
        if not tz.is_box():
            n_trap += 1
        rects.insert(tz.bbox())
after = rects.area()
ok = (before == after and n_trap == 0)
print(f"  {gds.split('/')[-1]}: {n_poly} polygon(s) -> {rects.count()} rect(s); "
      f"non-rectangular trapezoid(s) {n_trap}; area {before} -> {after} "
      f"({'IDENTICAL' if before == after else 'CHANGED'})")
if not ok:
    print("rectify_gds: refusing to write -- the decomposition changed the geometry")
    raise SystemExit(2)
# CLEAR EVERY CELL, not just the top.  The region was taken recursively, so
# the rectangles inserted into the top already cover what the subcells drew;
# clearing only the top would leave those shapes in place, and a flattening
# reader would then see each of them TWICE -- once merged into the top and
# once in its own cell -- and refuse on the subcell's polygon, which is the
# case this script exists to remove.  Latent rather than absent: measured on
# `pe_cell.gds`, met2 is 1174 shapes ALL on the top (so the top-only clear
# happened to be right) while met1 has 127 in standard cells (Codex/review
# #906).
for c in ly.each_cell():
    c.clear(li)
for r in rects.each():
    top.shapes(li).insert(r.bbox())
ly.write(out)
print(f"    wrote {out}")
