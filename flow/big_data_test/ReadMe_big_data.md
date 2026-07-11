Also see the [variation on this design](big2/ReadMe-big2.md).

# init/origin:
bfp tc3 ~/chip_designs/tc3a.bdb
> Then, sa run + manual edits
python3 tools/bdb_serialize.py dump ~/chip_designs/tc3a.bdb tc3a.bdb.sql
buda tc3a.buda

# bdb2buda

cd ~/src/buda
python3 tools/bdb2buda.py tc3a.bdb -o tc3a_flat_x10.buda

# sourced in tc3a_flat.buda -> renamed to big.buda

## use it in tc3a_flat.buda
buda --no-viz big > big_flat_out.log 2> big_flat_err.log &
> 1.41s user 0.11s system 80% cpu 1.884 total

# 5x (new on 2026.07.11)
python3 ../../tools/bdb2buda.py ~/chip_designs/tc3a.bdb -scale 5.0 -o tc3a_flat_5x.buda

# (old) repro
cd ~/src/buda
time buda --no-viz flow/big_data_test/tc3a.buda > big_out.log 2> big_err.log &
> 5.21s user 0.41s system 105% cpu 5.335 total

wc -l big*
>   5680 big_err.log
>   9084 big_out.log
>   14764 total

# copy
cd flow/big_data_test
bfp ~/chip_designs/tc3a.bdb
buda tc3a.buda

# FIXED. big dump problem
15k lines:
wc ./out.log
## 5k + lines total: 
### 2.5k +
[HierBundler] net_id=2840: using UNKNOWN-direction pins as positional driver/receivers
### 2.5k + 
[HierBundler] net_id=2840 at depth 0: using UNKNOWN-direction pins as positional driver/receivers
### 8k +
  Bundle 1: Seg 0 Bit 0 has no placed track (unplaced in DetailedNUTS)
