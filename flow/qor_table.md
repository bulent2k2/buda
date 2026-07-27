
July 26
=======
```
flow                                          bund  busS   netS | ovl  unpl viol   sec
----                                          -----------------------------------------------------------------------
rnr/mix2_fast                                  100   265   3115 |  34   241   14   4.6   healer-skipping
rnr/mix2_fast_on_aligned_sql                   100   265   3115 |  34   241   14   6.9   healer-skipping
rnr/mix2_fast_bottomup                         100   259   3128 |  17   196   11   3.5   healer-skipping
rnr/mix2_fast_topdown                          100   265   3157 |  16   175   11   4.0   healer-skipping
rnr/mix2_repro                                 100   238   null |  17  null   10   3.0   repro (stops pre-DNUTS)
rnr/mix2                                       100   240   3078 |   2    42    3  28.6   full healed, known residual
hbundles/06_multipin_stress                     35   143    384 |   1    54    5   0.6   stress vehicle
big_data_test/big2/big2_noviz                   80   253   8328 |   2    28    2   0.6   over-congested big2
big_data_test/big2/tc3b_flat                    80   253   8328 |   2    28    2   0.7   over-congested big2
big_data_test/big2/big2                         80   238   8036 |   0     0    1   0.9   1 electrically-broken bundle
big_3bundles_sel_pure_mst_topo                   3    20    484 |   0    12    1   0.0   selection test
rnr/slowdown_rnr                               100   256   3298 |   2     8    1  46.9   known hard/slow
```

clean
--
```
flow                                          bund  busS   netS    sec
-----------------------------------------------------------------------
big_data_test/bigHalf                           80   269   9400    5.1
big_data_test/big                               80   258   8536    1.8
rnr/mix                                        100   265   3348   24.4
hbundles/10_chip_units_blocks_leaf             176   209   1220    1.4
big_data_test/tc3a                              80   264   1116    2.8
big_data_test/big2/big2_b4_b24                   2    14    744    0.0
big_data_test/big_3bundles_sel_trunk+mst_topo    3    14    468    0.0
big_data_test/big2/b24_bus_056                   1     8    384    0.0
hbundles/05_stress_grid                         61    86    372    0.3
hbundles/07_wide_fan_stress                     24   153    269    0.7
big_data_test/b61                                1    10    160    0.0
big_data_test/big2/b1_bus_007                    1     5    140    0.0
big_data_test/big2/b4_bus_077                    1     2    120    0.1
big_data_test/b44                                1     2    104    0.1
big_data_test/big2/b34_bus_028                   1     3     84    0.0
hbundles/08_cross_level                         14    19     76    0.1
hbundles/02_two_procs                            8     8     64    0.0
big_data_test/bigHalf_bus038_bitrunk            80     1     56    0.1
hbundles/09_local_global_compete                 2     2     56    0.0
hbundles/01_pipeline_hier                        4     4     32    0.0
hbundles/04_deep_hierarchy                       7     8     32    0.0
hbundles/03_priority_ordering                    3     3     20    0.0
big_data_test/big2/b3_bus_023                    1     6      6    0.0
```
