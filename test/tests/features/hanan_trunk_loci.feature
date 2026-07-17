@landed
# Executable coverage: test/tests/test_topo_hanan_loci.py (binds this file and
# adds hand-written asserts for the opt-in default and the WL floor).
Feature: Hanan-line trunk loci (opt-in hanan_loci generation knob)
  As a chip planner
  I want n-pin trunk loci sampled ON the in-bbox Hanan lines as well as at
  channel midpoints when I opt in with the hanan_loci generation knob,
  So that a block-edge-aligned trunk can nominal at the geometric wirelength
  floor instead of carrying a structural overshoot (the b44 +500 — see
  docs/internal/wishlist-topo.md "Nominal-WL comparability", piece (a)).

  # Fixture = the flow/big_data_test/b44.buda block layout: the WL-optimal
  # V-trunk locus x=1200 is io_pad_tl's right edge — a Hanan LINE — which
  # midpoint-only sampling (700/1950/2830/3810) structurally misses.
  #
  #   y=12800 ─  +----------+
  #              | io_pad_tl x            ← right face AT x=1200
  #   y=12000 ─  +----------x
  #                         ||            ← V trunk rides x=1200
  #   y=11830 ─  +----------++--------+
  #              |       blk_23       |   ← pass-through (200..2700 ∋ 1200)
  #   y=10830 ─  +--------------------+
  #                         ||
  #   y=10250 ─             *=========x-------+   ← H stub to blk_07's left face
  #                                   |blk_07 |
  #   y=9750  ─                       +-------+
  #
  #   floor = |1200-2960| + |12000-10250| = 1760 + 1750 = 3510

  Scenario: trunk loci are sampled on Hanan lines as well as channel midpoints
    Given a multicast bundle whose WL-optimal trunk position lies ON a Hanan line
    When generate_topologies samples trunk loci with the hanan_loci knob
    Then a candidate at the Hanan-line locus exists with its nominal at the geometric floor

  Scenario: the extra Hanan-line loci are opt-in
    Given a multicast bundle whose WL-optimal trunk position lies ON a Hanan line
    When generate_topologies samples trunk loci without the hanan_loci knob
    Then no candidate is emitted at the Hanan-line locus
