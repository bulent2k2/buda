
Two identical defs
==
  ariane.def
  ariane133_manual_floorplan.def 
  
Buda files work
==
python3 src/buda_cli.py flow/ariane/ariane.buda &
python3 src/buda_cli.py flow/ariane/ariane_core.buda &

A synth placement by Claude
==
BDB=chip_designs/ariane136/ariane_buda5.bdb; python3 tools/def_viz_o3.py $BDB

Need a LEF?
==
python3 tools/def_viz_o3.py flow/ariane/ariane.def &
