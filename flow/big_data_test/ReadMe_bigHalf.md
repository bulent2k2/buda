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

RR efficiency round (2026-07-14; different host than rows 1-9 — its row-6
config measured 48.5-50.8s and row 7 ~5.7s here, so compare within this block):
10. row 6 + commit-by-forward-restore + scoped restore      ~46/0/0
    (identical 9-commit trajectory; timing breakdown shows the residual is
     the per-trial full solves: stage-b nuts 20.4s + dnuts 7.1s over 116
     trials — see wishlist-ripup "RR efficiency round 2")
11. row 10 + layer-scoped two-tier cheap trials            ~160/0/0  -> REVERTED
    (layers too coarse a partition on a 4-layer flat design; ranking noise
     blew trials 116->1136; negative result recorded in wishlist-ripup)

The checked-in flow stays row 7 (the 0/0 endpoint is exercised by the
slow-tier test `test_bighalf_rr_reaches_clean_endpoint`, which generates the
rr-enabled variant); flipping the two `# ripup_reroute` lines back on is a
one-line edit once the fixed-context trial round lands.


