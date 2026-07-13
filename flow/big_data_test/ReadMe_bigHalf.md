stats runtime(seconds)/NUTS overlaps/DNUTS opens (opens in x bundles, y segs(groups))
===
1. run_planner/run_nuts/run_detailed_nuts   1.0/7/358 (10, 12)
2. signal_tracks opt_in run_planner         1.6/8/409 (10, 13)
3. w/o signal_tracks + neg_cong(a)          1.3/7/358 (10, 12)
4. +ripup_reroute                          20.5/0/324 ( 7,  9)
5. +neg_cong(b)                            19.9/7/92  ( 2,  2)
6. +rr(b)                                  50.9/0/0   ( 0,  0)  -> clean slow (commented out)
7. sig_trac + neg_cong(a+b)                   3/9/170 ( 8, 11)  -> best  fast (checked in)
8. neg_cong(a) iter=20                      3.9/8/347 ( 8, 11)
9. neg_cont(a+b) iter=20                    3.8/8/347 ( 8, 11)


