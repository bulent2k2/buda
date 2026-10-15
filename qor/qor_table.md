# QoR corpus snapshot — 2026-10-15 (main @ 934d457a)

Regenerate with `tools/qor_table.py --out qor/qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock of THIS run (contention-inflated when the sweep ran parallel).  `null` = that stage did not run.  `sec1` = last known SERIAL (-j 1) wall-clock, captured 2026-08-02 (main @ 557c9de) — refresh with a full `-j 1 --out` run ('-' = no serial timing yet).

45 clean · 12 with residuals · 57 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec    sec1   note
--------------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  1789  36452  2558512   49465176 |    5  130   11  156.8   138.2
chip/chip_bottomup_caps                        640  1808  36007  2525347   47776933 |   56  143   14  146.1   140.5
chip/chip_bottomup                             640  1811  35967  2512244   47858490 |   56  231   17  136.7   133.9
chip/chip3_topdown                             640  1705  35366  2507066   49057519 |    6  266   35  169.6   103.6
chip/chip3a_bottomup                           640  1729  35312  2489438   48113210 |   38   78    9  159.2   133.2
chip/chip_stack_topdown                        660  1798  32013  2301605   39273797 |   21  235   19  147.2       -
chip/chip_stack_bottomup                       660  1798  30868  2268845   37216154 |  100  260   23  244.3       -
rnr/mix2_fast_bottomup_shared                  100   279   3530    72436     890954 |    0    0    2   72.3    28.6
rnr/mix2_fast_on_aligned_sql                   100   264   3334    76448     876264 |    2   16    1   51.7    25.4
rnr/mix2_fast_bottomup_caps                    100   267   3328    68371     841195 |    2    0    0   54.7    19.3
rnr/mix2_fast_bottomup                         100   256   3132    71642     879072 |    1    0    0   27.0     5.4
ariane133/ariane133_heal                       111   110    117 60137295   66648920 |    0    0    1  134.8       -
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec    sec1
--------------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   264   9176   426327   14460521    51.3    33.8
big_data_test/big                               80   261   8672   779424   26410318     2.2     1.4
big_data_test/big2/big2                         80   242   8220   351180   11622952     1.9     0.9
rnr/mix2_fast_topdown                          100   261   3316    71511     810579     7.6     5.7
rnr/mix2                                       100   243   3156    73316     857088    29.3    17.1
rnr/mix2_topdown_refine                        100   256   3154    70266     804950    70.0    53.8
rnr/mix                                        100   248   3132    67637     779346     5.1     3.6
tpu/tpu                                        152   152   2944     9312     197376     1.1       -
rnr/mix2_fast_bottomup_caps_2x                 100   244   2888    62737     745400    26.9       -
rnr/mix2_fast_on_aligned_sql_2x                100   228   2794    64973     778283     4.8       -
rv/soc_conv_div                                 40   137   2014 18099357  436867000    25.4       -
hbundles/10_chip_units_blocks_leaf             176   206   1200    46937     337719     1.2     0.6
big_data_test/tc3a                              80   262   1106    79430     298994     3.0     2.0
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    44132    1086282     0.1     0.1
big_3bundles_sel_trunk+mst_topo                  3    14    468    39534     973059     0.1     0.0
hbundles/06_multipin_stress                     35   144    462    15304      49596    20.9     8.8
big_data_test/big2/b24_bus_056                   1     8    384     5304     231264     0.1     0.0
hbundles/05_stress_grid                         61    86    372     7292      31298     0.4     0.2
hbundles/07_wide_fan_stress                     24   153    269    20018      40373     0.8     0.4
big_data_test/b61                                1    10    160    15312     240884     0.0     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4957     136752     0.0     0.0
comprehensive_regression                         5    24    121     5505      39726     0.0       -
big_data_test/big2/b4_bus_077                    1     2    120     3412     191669     0.1     0.1
big_data_test/b44                                1     2    104     3920     193376     0.0     0.0
teg_mst_over                                     2    22     88     3668      14040     0.1       -
big_data_test/big2/b34_bus_028                   1     3     84      292       4732     0.0     0.0
hbundles/08_cross_level                         14    19     76     2643      10322     0.1     0.0
hbundles/02_two_procs                            8     8     64      660       5280     0.1     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0     0.0
ndr_bottom_up                                    6     8     51      915       4632     0.1       -
def/chip                                        15    18     48   283400     879600     0.2       -
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0     0.0
hbundles/04_deep_hierarchy                       7     8     32      813       3203     0.1     0.0
teg_hier_cell                                    4     8     32     2055       8033     0.1       -
ndr_shield_flat                                  4     4     30     2400      12000     0.0       -
c_dd_detour                                      1     7     28     2462       9534     0.0       -
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0     0.0
teg_bitrunk                                      1     4     16     1532       6064     0.0       -
ndr_bond                                         2     2     15     1200       4800     0.0       -
teg_over_audit                                   1     3     12      508       1996     0.0       -
feedthru_multirect                               2     3     12     1300       5200     0.1       -
teg_adjacent                                     1     2      8      152        544     0.0       -
teg_hybrid                                       1     2      8     1316       5236     0.0       -
big_data_test/big2/b3_bus_023                    1     6      6     7770       7768     0.0     0.0
```
