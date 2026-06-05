Hanan Grid Use
====

Commit: Add global corner_margin command; per-block overrides take precedence
      7 files changed, 93 insertions(+), 5 deletions(-)

⏺ All 29 tests pass (3 pre-existing bundler failures are unrelated). The global corner_margin feature is complete and committed.

  Root cause of the test failure: pybind11 converts std::map<int, SegEndpoints> to a Python dict by value, so topo.seg_busterms[0] = (bt, None) was silently discarded — the C++ map never received the annotation. This meant the geometric busterm fallback always ran, picking up the "src_helper" block at the segment endpoint and applying the global margin (dy=20) there, which intersected with block "b"'s per-block margin (dy=10) to produce [20,180] instead of [10,190]. The fix builds the topology for block "b" without a helper block at the segment endpoint, so only "b" is found geometrically. 

❯ When topology generation runs on a bundle, it uses the Hanan Grid, right? 
 
⏺ Yes. The Hanan grid provides the candidate bend/trunk coordinates for Z and U shapes. 
 
  Specifically in TopologyGenerator::generate_candidates(): 
 
  - L-shapes don't need the grid — the single bend point is just one block's center projected onto the other block's axis. Two candidates: L_HV and L_VH. 
  - Z-shapes iterate the Hanan grid lines that fall between the two blocks. Each intermediate grid coordinate becomes a trunk position, so you get one Z candidate per qualifying grid line. 
  - U-shapes use the grid lines outside the bounding box of the two blocks (one candidate beyond the min extreme, one beyond the max extreme). 
 
  The grid itself comes from Floorplan::get_hanan_grid(), which returns sorted unique x and y coordinates of all block edges. So every block corner in the floorplan contributes lines to the grid, and 
  the topology generator uses those lines as the universe of valid trunk positions. 
 
❯ Now, check how the grid is created. It should use only the busterms for the given bundle. I suspect it is using them all. 
 
  Searched for 1 pattern (ctrl+o to expand) 
 
⏺ There's already a bundle_hanan_grid helper. Let me see what it uses: 
 

Commit: Add global corner_margin command; per-block overrides take precedence
      7 files changed, 93 insertions(+), 5 deletions(-)

⏺ All 29 tests pass (3 pre-existing bundler failures are unrelated). The global corner_margin feature is complete and committed.

  Root cause of the test failure: pybind11 converts std::map<int, SegEndpoints> to a Python dict by value, so topo.seg_busterms[0] = (bt, None) was silently discarded — the C++ map never received the annotation. This meant the geometric busterm fallback always ran, picking up the "src_helper" block at the segment endpoint and applying the global margin (dy=20) there, which intersected with block "b"'s per-block margin (dy=10) to produce [20,180] instead of [10,190]. The fix builds the topology for block "b" without a helper block at the segment endpoint, so only "b" is found geometrically. 

❯ When topology generation runs on a bundle, it uses the Hanan Grid, right? 
 
⏺ Yes. The Hanan grid provides the candidate bend/trunk coordinates for Z and U shapes. 
 
  Specifically in TopologyGenerator::generate_candidates(): 
 
  - L-shapes don't need the grid — the single bend point is just one block's center projected onto the other block's axis. Two candidates: L_HV and L_VH. 
  - Z-shapes iterate the Hanan grid lines that fall between the two blocks. Each intermediate grid coordinate becomes a trunk position, so you get one Z candidate per qualifying grid line. 
  - U-shapes use the grid lines outside the bounding box of the two blocks (one candidate beyond the min extreme, one beyond the max extreme). 
 
  The grid itself comes from Floorplan::get_hanan_grid(), which returns sorted unique x and y coordinates of all block edges. So every block corner in the floorplan contributes lines to the grid, and 
  the topology generator uses those lines as the universe of valid trunk positions. 
 
❯ Now, check how the grid is created. It should use only the busterms for the given bundle. I suspect it is using them all. 
 
  Searched for 1 pattern (ctrl+o to expand) 
 
⏺ There's already a bundle_hanan_grid helper. Let me see what it uses: 
 
  Read 1 file (ctrl+o to expand) 
 
⏺ It's already using bundle_hanan_grid with only the bundle's own block rects — not the full floorplan grid. The comment on line 511 even calls this out explicitly. 
 
  So it's correct: topology generation uses only the blocks belonging to that specific bundle (extracted from the Busterm list at line 899–901), not all blocks in the floorplan. 
 
❯ New simple testcase: one.buda. Only one bundle. Please go over all topologies and make sure that corner_margin is handled correctly. 
 
  Read 1 file, listed 1 directory (ctrl+o to expand) 
 
⏺ Good, one.buda already exists with one bundle. Let me run it and see all topologies:
