Background
==

This is built as a variation on [the original test case](../ReadMe_big_data.md).

*../big.buda* and this test-case has the same connectivity/netlist. However, this one has two major changes:
1. scale factor (used in `bdb2buda`) is 5x. *big* has 10x.
2. all channel space is absorbed into blocks. *big* has a lot. 

History
==

# Manual edits to remove channel space:
moved to ~/chip_designs/tc3b

## don't edit here
fp flow/big_data_test/tc3b.bdb &

## After enh for gridded move of block edges:
cd flow/big_data_test
cp tc3a.bdb tc3b.bdb
cd -
fp flow/big_data_test/tc3b.bdb &

cd ~/src/git/buda/gem
python3 tools/bdb2buda.py ~/chip_designs/tc3b/tc3b.bdb -scale 5 -o flow/big_data_test/tc3b_flat_x5.buda
> Written to flow/big_data_test/tc3b_flat_x5.buda

