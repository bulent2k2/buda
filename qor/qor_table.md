# QoR corpus snapshot — 2026-08-22 (main @ 20f8acad)

Regenerate with `tools/qor_table.py --out qor/qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock of THIS run (contention-inflated when the sweep ran parallel).  `null` = that stage did not run.  `sec1` = last known SERIAL (-j 1) wall-clock, captured 2026-08-02 (main @ 557c9de) — refresh with a full `-j 1 --out` run ('-' = no serial timing yet).

39 clean · 10 with residuals · 49 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec    sec1   note
--------------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  1790  36464  2536107   49477415 |    4  134    9  168.6   138.2
chip/chip_bottomup_caps                        640  1808  36007  2525347   47776950 |   56  143   14  147.2   140.5
chip/chip_bottomup                             640  1811  35967  2512244   47858490 |   56  231   17  136.7   133.9
chip/chip3_topdown                             640  1705  35372  2485624   49058601 |    6  260   34  179.2   103.6
chip/chip3a_bottomup                           640  1729  35312  2489438   48113210 |   38   78    9  163.0   133.2
chip/chip_stack_topdown                        660  1798  32003  2279102   39214164 |   21  241   18  155.8       -
chip/chip_stack_bottomup                       660  1796  30860  2266555   37203712 |   99  240   20  308.2       -
rnr/mix2_fast_bottomup_caps                    100   267   3328    68371     841195 |    2    0    0   39.8    19.3
rnr/mix2_fast_on_aligned_sql                   100   262   3326    76333     886438 |    2   16    1   41.2    25.4
ariane133/ariane133_heal                       111   110    117 60011575   66648920 |    0    0    1  122.3       -
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec    sec1
--------------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   259   8980   403764   13992578    69.2    33.8
big_data_test/big                               80   261   8672   768746   26408329     2.1     1.4
big_data_test/big2/big2                         80   243   8308   344921   11751065     1.8     0.9
rnr/mix2_fast_bottomup_shared                  100   279   3530    72436     890954    54.1    28.6
rnr/mix2_fast_topdown                          100   268   3412    66010     800286     7.3     5.7
rnr/mix2_fast_bottomup                         100   262   3324    71805     885066     6.9     5.4
rnr/mix2_topdown_refine                        100   259   3284    65339     793198    60.3    53.8
rnr/mix2                                       100   244   3204    69713     866029    37.2    17.1
rnr/mix                                        100   248   3132    62004     761157     4.4     3.6
rnr/mix2_fast_bottomup_caps_2x                 100   244   2888    62737     745400    18.7       -
rnr/mix2_fast_on_aligned_sql_2x                100   228   2794    64973     778283     4.6       -
rv/soc_conv_div                                 40   137   2014 16733457  436625200    28.9       -
hbundles/10_chip_units_blocks_leaf             176   206   1200    46092     337719     1.0     0.6
big_data_test/tc3a                              80   262   1108    78600     300104     2.7     2.0
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43089    1086282     0.1     0.1
big_3bundles_sel_trunk+mst_topo                  3    14    468    38826     973059     0.1     0.0
hbundles/06_multipin_stress                     35   144    462    14469      49596    21.6     8.8
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.1     0.0
hbundles/05_stress_grid                         61    86    372     6781      31978     0.3     0.2
hbundles/07_wide_fan_stress                     24   153    269    19587      40373     0.7     0.4
big_data_test/b61                                1    10    160    15057     240884     0.0     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4884     136752     0.0     0.0
comprehensive_regression                         5    24    121     5261      39726     0.0       -
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1     0.1
big_data_test/b44                                1     2    104     3715     193376     0.0     0.0
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.1     0.0
hbundles/02_two_procs                            8     8     64      660       5280     0.1     0.0
def/chip                                        15    21     60   273800     870800     0.3       -
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0     0.0
ndr_bottom_up                                    6     8     51      915       4632     0.1       -
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0     0.0
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0     0.0
ndr_shield_flat                                  4     4     30     2400      12000     0.0       -
c_dd_detour                                      1     7     28     2371       9534     0.0       -
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0     0.0
ndr_bond                                         2     2     15     1200       4800     0.0       -
big_data_test/big2/b3_bus_023                    1     6      6     7765       7768     0.0     0.0
```
