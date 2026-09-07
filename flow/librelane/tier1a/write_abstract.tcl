# OpenROAD's own abstract of a hardened block, which the Classic flow does
# not write (`OpenROAD.WriteViews` is not in its step list, so
# `<cell>.openroad.lef` never exists) -- the #896 candidate fix.
#
#   ../../../phase0/measure/run_or.sh <block_run> write_abstract.tcl \
#       ODB=<block>/runs/h/final/odb/<cell>.odb OUT=<cell>.openroad.lef
#
# WHY.  Magic's LEF (`final/lef/<cell>.lef`, what the top reads) abstracts
# the block's metal rect by rect and leaves NOTCHES uncovered where a pin
# rect ends and the OBS blanket begins -- the top's router reads the notch
# as free, overhangs a wire into it, and lands inside the macro's own metal
# spacing (#896: `acc_cell` local x 69.37-69.65, gap 0.130 um against
# m2.2's 0.140).  `-bloat_occupied_layers` covers a used layer wholesale
# instead, so the notch cannot exist.  What it may cost is pin ACCESS: a
# blanket OBS on a layer the top must reach the pins through.  That is the
# measurement, not an assumption.
read_db $::env(ODB)
write_abstract_lef -bloat_occupied_layers $::env(OUT)
puts "wrote $::env(OUT)"
