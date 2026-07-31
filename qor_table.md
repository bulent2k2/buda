# QoR corpus snapshot — 2026-07-31 (main @ 7aa0f0e)

Regenerate with `tools/qor_table.py --out qor_table.md`.  Columns: `bund`/`busS`/`netS` = bundle / bus-segment / net-segment counts; `busWL`/`netWL` = abstract (after NUTS) / detailed (after DNUTS) wirelength, placed-only; `ovl`/`unpl`/`viol` = overlaps / unplaced bits / bundles with `check_design` violations; `sec` = wall-clock.  `null` = that stage did not run.

28 clean · 4 with residuals · 32 flows.

## DIRTY — residual overlaps / unplaced / viol_bundles (or incomplete)

```
flow                                          bund  busS   netS    busWL      netWL |  ovl unpl viol    sec   note
------------------------------------------------------------------------------------------------------------------------------------
chip/chip_topdown                              640  2292  43915  3036142   61969962 |  255 3185  163  115.3
chip/chip_bottomup                             640  2074  38495  2659098   46670260 |  490 2285  110  108.4
rnr/mix2_topdown_refine                        100   279   3408    67967     825104 |    3    0    0   49.3
rnr/mix2_fast_on_aligned_sql                   100   275   3364    72298     876260 |    2   16    1   29.7
```

## CLEAN — 0 overlaps / 0 unplaced / 0 viol_bundles

```
flow                                          bund  busS   netS    busWL      netWL     sec
------------------------------------------------------------------------------------------
big_data_test/bigHalf                           80   262   9016   409138   14136485     7.0
big_data_test/big                               80   261   8672   769324   26420908     1.0
big_data_test/big2/big2                         80   239   8068   344242   11691106     1.2
rnr/mix2_fast_topdown                          100   271   3422    66000     800444     5.0
rnr/mix2_fast_bottomup                         100   263   3322    74908     919077    16.1
rnr/mix                                        100   248   3132    62043     762012     2.9
rnr/mix2                                       100   243   3132    70083     857242    12.2
hbundles/10_chip_units_blocks_leaf             176   209   1220    50362     363724     0.6
big_data_test/tc3a                              80   262   1108    78366     299104     1.7
big_data_test/big_3bundles_sel_pure_mst_topo     3    24    668    43351    1081116     0.0
big_3bundles_sel_trunk+mst_topo                  3    14    468    38826     973059     0.0
hbundles/06_multipin_stress                     35   138    452    14562      49774     5.1
big_data_test/big2/b24_bus_056                   1     8    384     4818     231264     0.0
hbundles/05_stress_grid                         61    86    372     6728      31298     0.1
hbundles/07_wide_fan_stress                     24   153    269    19596      40383     0.3
big_data_test/b61                                1    10    160    15294     244772     0.0
big_data_test/big2/b1_bus_007                    1     5    140     4946     138502     0.1
big_data_test/big2/b4_bus_077                    1     2    120     3199     191669     0.1
big_data_test/b44                                1     2    104     3715     193376     0.1
big_data_test/big2/b34_bus_028                   1     3     84      166       4732     0.0
hbundles/08_cross_level                         14    19     76     2576      10322     0.1
hbundles/02_two_procs                            8     8     64      660       5280     0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56     4245     237720     0.0
hbundles/09_local_global_compete                 2     2     56      860      21920     0.0
hbundles/01_pipeline_hier                        4     4     32      330       2640     0.1
hbundles/04_deep_hierarchy                       7     8     32      796       3203     0.0
hbundles/03_priority_ordering                    3     3     20     1950      13400     0.0
big_data_test/big2/b3_bus_023                    1     6      6     8424       8426     0.0
```
