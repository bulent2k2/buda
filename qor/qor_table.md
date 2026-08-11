# QoR corpus snapshot — 2026-08-11 (main @ 83dd7d09)

Regenerate with `tools/qor_table.py --out qor/qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock of THIS run (contention-inflated when the sweep ran parallel).  `null` = that stage did not run.  `sec1` = last known SERIAL (-j 1) wall-clock, captured 2026-08-02 (main @ 557c9de) — refresh with a full `-j 1 --out` run ('-' = no serial timing yet).

32 clean · 9 with residuals · 41 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec    sec1   note
--------------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  1790  36464  2541681   49637160 |    4  134    9  209.4   138.2
chip/chip_bottomup_caps                        640  1809  36007  2527421   48327965 |   55  155   14  188.3   140.5
chip/chip_bottomup                             640  1809  35931  2516788   48203301 |   54  243   19  167.7   133.9
chip/chip3_topdown                             640  1705  35372  2490996   49182880 |    6  260   34  227.0   103.6
chip/chip3a_bottomup                           640  1729  35312  2493698   48238624 |   38   78    9  208.7   133.2
chip/chip_stack_topdown                        660  1798  32003  2281274   39380339 |   21  241   18  199.3       -
chip/chip_stack_bottomup                       660  1797  30856  2269788   38070160 |   69  252   20  321.0       -
rnr/mix2_fast_bottomup_caps                    100   267   3328    68371     841195 |    2    0    0   39.9    19.3
rnr/mix2_fast_on_aligned_sql                   100   262   3326    76368     901316 |    2   16    1   43.1    25.4
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec    sec1
--------------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   259   8980   405139   14073009    67.9    33.8
big_data_test/big                               80   261   8672   769850   26456791     2.1     1.4
big_data_test/big2/big2                         80   243   8308   346340   11818258     1.9     0.9
rnr/mix2_fast_bottomup_shared                  100   279   3530    72436     890954    54.0    28.6
rnr/mix2_fast_topdown                          100   268   3412    66129     802202     8.1     5.7
rnr/mix2_fast_bottomup                         100   262   3324    71805     885066     8.0     5.4
rnr/mix2_topdown_refine                        100   259   3284    65339     793215    58.7    53.8
rnr/mix2                                       100   244   3204    69713     866029    37.9    17.1
rnr/mix                                        100   248   3132    62039     761520     5.0     3.6
rnr/mix2_fast_bottomup_caps_2x                 100   244   2888    62778     746026    19.4       -
rnr/mix2_fast_on_aligned_sql_2x                100   228   2794    65160     781287     5.1       -
hbundles/10_chip_units_blocks_leaf             176   206   1200    46092     337719     1.2     0.6
big_data_test/tc3a                              80   262   1108    78806     300930     3.0     2.0
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43973    1096866     0.1     0.1
big_3bundles_sel_trunk+mst_topo                  3    14    468    38826     973059     0.1     0.0
hbundles/06_multipin_stress                     35   144    462    14475      49609    22.1     8.8
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.1     0.0
hbundles/05_stress_grid                         61    86    372     6781      31978     0.4     0.2
hbundles/07_wide_fan_stress                     24   153    269    19587      40373     0.7     0.4
big_data_test/b61                                1    10    160    15333     245348     0.0     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.0     0.0
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1     0.1
big_data_test/b44                                1     2    104     3715     193376     0.0     0.0
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.1     0.0
hbundles/02_two_procs                            8     8     64      660       5280     0.1     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.1     0.1
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.0     0.0
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0     0.0
```
