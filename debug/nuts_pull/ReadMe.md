PR# 42/43/44 interaction
===
  - /tmp/dump.py <flow> — runs a flow through run_nuts in-process and
  prints (bundle_id, seg_idx) (net_pull, interval_lo, interval_hi, track_position) per
  segment. Handles relative source paths (cwds into the script's dir). Run:
  `python3 /tmp/dump.py flow/big_data_test/big.buda 2>/dev/null | grep -E '^\(|^OVERLAPS'`
  - /tmp/slack.py <flow> [bundle_ids] — for each pulled segment, computes the
  closest-to-pull position the final occupancy allows (a "would a greedy slide
  help?" probe) and reports the recoverable gain. This is what surfaced B20 seg2
  (cur 219.5 → best 2680.5).
  - /tmp/agg.py <old> <new> — aggregate pull-deviation over two dump files.
  - /tmp/n44.txt — the current big-flow dump I analyzed.

The 3 brittle tests
===

  All in test/tests/test_nuts_pull_repack.py (mid tier):

  1. test_pulled_anchors_end_near_pull — aggregate pull-deviation < 60k. Fails
  now (92k), inflated ~36k by the wide unbounded MST trunks (bogus metric) plus
  real B20-type misses.
  2. test_reported_bundles_reach_their_pull — asserts B9/B20/B28 reach pull.
  B9/B28 still pass; B20 is the genuine failure (the bug above).
  3. test_run_nuts_on_layer_tightens_and_keeps_other_layers — M7 rerun
  exact-idempotency; brittle to the topology change.

  Run them:
  pytest test/tests/test_nuts_pull_repack.py -o addopts="" -m mid -v
  
Big flow B20
===
  Visually inspect the underlying scenario (opens the matplotlib viewer on the
  big flow these tests exercise):
  ./buda flow/big_data_test/big.buda
  Click B20 to highlight its trunk — you'll see the left V-trunk parked at the
  far-left (x≈219) with the open channel around x≈1731 that it should slide
  into. B9 and B28 (also pulled segments) show the fix working correctly for
  contrast.
