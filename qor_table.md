# QoR corpus snapshot — 2026-08-01 (main @ a84c2aa)

Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock of THIS run (contention-inflated when the sweep ran parallel).  `null` = that stage did not run.  `sec1` = last known SERIAL (-j 1) wall-clock, captured 2026-08-01 (main @ a84c2aa) — refresh with a full `-j 1 --out` run ('-' = no serial timing yet).

29 clean · 8 with residuals · 37 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec    sec1   note
--------------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  2292  43793  3030492   61828900 |  266 3307  159  225.0   225.0
chip/chip3_topdown                             640  2093  43469  2889132   60833132 |  121 1731  133  218.0   218.0
chip/chip_bottomup_caps                        640  2292  42681  2854822   51392056 |  458 3131  126  167.8   167.8
chip/chip_bottomup                             640  2074  38575  2647203   46303673 |  482 2205  105  208.3   208.3
chip/chip3a_bottomup                           640  1959  37405  2648674   47203194 |  447 2269  103  160.2   160.2
rnr/mix2_topdown_refine                        100   276   3392    68200     835130 |    1    0    0   86.0    86.0
rnr/mix2_fast_bottomup_caps                    100   267   3328    68371     841195 |    2    0    0   30.0    30.0
rnr/mix2_fast_on_aligned_sql                   100   262   3326    76368     911840 |    2   16    1   41.7    41.7
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec    sec1
--------------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   259   8980   405139   14073009    51.0    51.0
big_data_test/big                               80   261   8672   769850   26456791     1.8     1.8
big_data_test/big2/big2                         80   243   8308   346340   11818258     1.3     1.3
rnr/mix2_fast_bottomup_shared                  100   279   3530    72436     890954    46.3    46.3
rnr/mix2_fast_topdown                          100   268   3412    66129     802202     7.8     7.8
rnr/mix2_fast_bottomup                         100   262   3324    71805     885066     7.3     7.3
rnr/mix2                                       100   244   3204    69713     866029    27.9    27.9
rnr/mix                                        100   248   3132    62039     761520     5.4     5.4
hbundles/10_chip_units_blocks_leaf             176   206   1200    46092     337719     1.1     1.1
big_data_test/tc3a                              80   262   1108    78806     300930     2.8     2.8
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43973    1096866     0.1     0.1
big_3bundles_sel_trunk+mst_topo                  3    14    468    38826     973059     0.1     0.1
hbundles/06_multipin_stress                     35   144    462    14475      49609    12.9    12.9
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.1     0.1
hbundles/05_stress_grid                         61    86    372     6781      31978     0.3     0.3
hbundles/07_wide_fan_stress                     24   153    269    19587      40373     0.6     0.6
big_data_test/b61                                1    10    160    15333     245348     0.0     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.0     0.0
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1     0.1
big_data_test/b44                                1     2    104     3715     193376     0.1     0.1
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.1     0.1
hbundles/02_two_procs                            8     8     64      660       5280     0.1     0.1
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0     0.0
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0     0.0
```
