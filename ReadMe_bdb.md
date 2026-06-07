Quick
===
viz flow/hier_4_units.bdb
viz test/tests/hier_test1.bdb
buda design/small.buda
viz flow/manual/small_1.bdb

Setup (old)
===
export PYTHONPATH=/users/ben/src/buda/build
BDB=/Users/ben/src/buda/test/tests/hier_test1.bdb
BDB=chip_designs/ariane136/ariane_buda5.bdb
BDB=flow/lefdef/four_block/four_blocks.bdb

Std-cell + groups
==
BDB=flow/lefdef/gcd/gcd.bdb

Has insts but no nets
==
    BDB=flow/lefdef/nvdla_placed_macros.bdb
    BDB=flow/lefdef/ariane133_manual_floorplan.bdb

Cmd
==
python3 tools/def_viz_o3.py $BDB &


Gherkin/Cucumber (BDD)
==
~/src/buda/test/tests/features/bdb_combined.feature

All 24 tests pass. The artifact is at
  /Users/ben/src/buda/test/tests/hier_test1.bdb — you can inspect it with any
  SQLite browser (DB Browser for SQLite, etc.).
