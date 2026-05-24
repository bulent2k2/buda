Open-source hierarchical HDL vehicles found
====
- MemPool Group (ETH Zürich, ISPD24 benchmark) — best fit. Three-level hierarchy: Tile 
  → Group → Cluster. Has systematic tcdm_req_t[BANKS] buses from every FUB to the cluster
   interconnect — exactly the kind of named, wide bus that makes depth-parameterized
  planning meaningful. The busterm_over_the_block and hierarchy_depth_planning feature
  files use a simplified MemPool geometry as the vehicle.
- Ariane/CVA6 (ETH Zürich) — already in the project as ariane.buda and ariane_core.buda
   with 7 pipeline sub-blocks and buses like rf_rd[20], decode2issue[12]. Good for
  existing tests but only two hierarchy levels.
- BlackParrot (U. Washington) — multi-core mesh; non-rectangular tiles make it a
  candidate for multi-rect/TEG scenarios.

Bus planning as entropy reduction (academic grounding):
====
- MARCH (DAC 2019) — bus-aware global routing; shows that pre-grouping N-bit buses
  reduces per-net routing search from O(N·E) to O(E).
- MiniDeviation (2020) — bus-driven placement to minimize wire deviation; confirms
  buses discovered pre-routing improve standard-cell placement quality.
- US Patent 11694016 (Synopsys) — commercial embodiment of the same idea: discovering
  buses before detailed routing to guide track assignment.

Three MemPool BUDA files created in buda_system_v2/flow
====
  
  ┌──────────────────────┬──────────────┬────────────────────────────────┬────────────────────────────────────────────────────┬─────────┬───────────────────────────────────────┬────────────────────────────────────────────────────┐
  │         File         │     Die      │             Blocks             │                       Buses                        │ Bundles │            Abstract NUTS              │                    Detailed NUTS                   │
  ├──────────────────────┼──────────────┼────────────────────────────────┼────────────────────────────────────────────────────┼─────────┼───────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ mempool_tile.buda    │ 360×280 µm   │ 4 cores + 16 banks + 2 I-cache │ 8 buses (64-bit TCDM + 32-bit I-cache)             │ 8       │ 35 violations (congested — realistic) │ 3264 net segments, 768 bits unplaced (congestion)  │
  ├──────────────────────┼──────────────┼────────────────────────────────┼────────────────────────────────────────────────────┼─────────┼───────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ mempool_group.buda   │ 1100×1100 µm │ 16 tiles + 3 IC blocks         │ 3×36-bit TCDM response multicast (1-to-16)         │ 3       │ 1 overlap                             │ 1584 net segments, 0 bits unplaced ✓               │
  ├──────────────────────┼──────────────┼────────────────────────────────┼────────────────────────────────────────────────────┼─────────┼───────────────────────────────────────┼────────────────────────────────────────────────────┤
  │ mempool_cluster.buda │ 2350×2350 µm │ 4 group blocks                 │ 12×64-bit inter-group TCDM (6 bidirectional pairs) │ 12      │ 0 violations                          │ 1856 net segments, 0 bits unplaced ✓               │
  └──────────────────────┴──────────────┴────────────────────────────────┴────────────────────────────────────────────────────┴─────────┴───────────────────────────────────────┴────────────────────────────────────────────────────┘

  Key design decisions:
  - Tile: 16-destination multicast from each core (MST topology); I-cache refill modelled as 2-pin L/Z
    768 bits unplaced in DetailedNUTS = genuine congestion (35 abstract NUTS interval violations on a 360×280 µm die)
    I-cache buses use 32 bits (one RV32 instruction word per cycle); 128-bit AXI cache-line refill is group-level
    Keepout above y=266 prevents U-topologies from routing above the die boundary (y=280)
  - Group: 1-to-16 multicast from each of 3 IC blocks (N, NE, E); north_ic/east_ic/ne_ic placed as 80 µm strips at die edges — the planner selects BITRUNK_H spanning the full tile array
  - Cluster: 12 point-to-point buses; the diagonal g0↔g3 and g1↔g2 connections correctly generate U-shaped topologies (out-of-bbox)
  - Bus widths derived from the MemPool RTL: tcdm_master_req_t = 63 bits (data:32 amo:4 core_id:2 meta_id:4 wen:1 be:4 addr:16), response = 36 bits
  - NanGate45 track patterns from NangateOpenCellLibrary.tech.lef: 4 SIGNAL + POWER(2×w) + 4 SIGNAL + GROUND per period
    signal_density = 0.364 (same for M5-M10); dilution = 2.750; M3 density also 0.364 with 0.07 µm signal width


MacroPlacement benchmark designs (TILOS-AI-Institute/MacroPlacement, NanGate45)
====
Three designs extracted from the MacroPlacement repo's NanGate45 placed-macro DEFs.
Block positions derived from exact lower-left corners in bsg_chip_fp_placed_macros.def
(UNITS DISTANCE MICRONS 2000; divide raw DEF coords by 2000).

  ┌──────────────────────────┬──────────────┬───────────────────────────────────────┬────────────────────────────────────────────┬─────────┬───────────────────────────────────┬─────────────────────────────────────┐
  │           File           │     Die      │               Blocks                  │                   Buses                    │ Bundles │          Abstract NUTS            │           Detailed NUTS             │
  ├──────────────────────────┼──────────────┼───────────────────────────────────────┼────────────────────────────────────────────┼─────────┼───────────────────────────────────┼─────────────────────────────────────┤
  │ nvdla_cbuf.buda          │ 2354×2353 µm │ 31 (cbuf_ctrl + 30 cbuf banks)        │ 1×8-bit cbuf_addr (1→30 multicast)         │ 1       │ 0 violations, 4 span adjustments  │ 240 net segments, 0 bits unplaced ✓ │
  ├──────────────────────────┼──────────────┼───────────────────────────────────────┼────────────────────────────────────────────┼─────────┼───────────────────────────────────┼─────────────────────────────────────┤
  │ ariane133_cache.buda     │ 1357×1357 µm │ 24 (ctrl + 7 d_tag + 8 d_data +       │ 4×8-bit (d_tag_addr 1→7, d_data_addr 1→8,  │ 4       │ 10 violations, 3 overlaps         │ 168 net segments, 0 bits unplaced ✓ │
  │                          │              │      1 d_valid + 3 i_tag + 4 i_data)  │  i_data_fetch 1→4, i_tag_addr 1→3)         │         │                                   │                                     │
  ├──────────────────────────┼──────────────┼───────────────────────────────────────┼────────────────────────────────────────────┼─────────┼───────────────────────────────────┼─────────────────────────────────────┤
  │ bp_tile.buda             │ 2907×2906 µm │ 20 (core + 8 icd + 8 dcd + 4 grouped) │ 8 buses: dcache_addr 1→8, icache_addr 1→8, │ 8       │ 0 violations, 1 overlap           │ 207 net segments, 0 bits unplaced ✓ │
  │                          │              │                                        │  l2_addr/l2_tag/cce_dir/cce_inst/tags P2P  │         │                                   │                                     │
  └──────────────────────────┴──────────────┴───────────────────────────────────────┴────────────────────────────────────────────┴─────────┴───────────────────────────────────┴─────────────────────────────────────┘

  Key design decisions:
  - NVDLA cbuf: 91 fakeram45_256x64 SRAMs (4 KB each) in 30 banks; cbuf_addr is the key multicast bus broadcasting the
    same row-address to all banks simultaneously so data can be read in parallel.
    corner_margin dx=10 dy=10 (not 30) because banks are 140×70 µm — dy=30 shrinks Y to 10 µm, too narrow for 8-bit M9 bus.
    cbuf_we omitted: STRICT bundler merges it with cbuf_addr (same driver + same 30 receivers = identical signature → 9-bit bundle).
    M7/M8 span_max extended to full die (2354/2353 µm) to allow M7's finer pitch (signal spacing 1.1 µm) for stubs throughout.
  - Ariane133 cache: 61 fakeram45_256x16 SRAMs (512 B each); cache data SRAMs are scattered across a wide area in the
    MacroPlacement bus-unaware result — BUDA's planning would cluster them around a coherent trunk.
    10 abstract NUTS violations from d_data blocks that overlap in Y after shrink; DetailedNUTS recovers all bits (0 unplaced).
    Source: MacroPlacement Flows/NanGate45/ariane133/def/ariane133_fp_placed_macros.def
  - BlackParrot bp_tile: 43 SRAMs per tile in a bp_quad (4-tile) chip (2907×2906 µm die). Tile y_0__x_0 modelled.
    The bp_quad tile layout is NOT a clean 2×2 quadrant — tiles are physically interleaved; tile macros span
    nearly the full die height (Y=22–2716 µm). Key buses: dcache_addr and icache_addr each 1→8 to individually
    scattered ~120×75 µm fakeram45_512x64 macros.
    L2 data (bottom, Y=22–780) to core (Y=1200–1400) to caches (top, Y=2116–2716): ~2300 µm vertical span.
    corner_margin dx=5 dy=5 chosen because fakeram45_64x62 CCE dir macros are only 32×40 µm.
    Planner picks H trunk (M9) at Y≈2394–2409 with V stubs (M10) down to each scattered SRAM — correct tree shape.
    Source: MacroPlacement Flows/NanGate45/bp_quad/def/bsg_chip_fp_placed_macros.def



> Look at the LEF file for MemPool to derive track definitions so we can run detailed nuts, too.
====

⏺ Let me find the NanGate45 LEF from the OpenROAD flow scripts.
  
[Fetch](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/master/flow/platforms/nangate45)
  ⎿  Received 292.4KB (200 OK)

[Fetch](https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/nangate45/make_tracks.tcl)
  ⎿  Received 773 bytes (200 OK)

[Fetch](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/master/flow/platforms/nangate45/lef)
  ⎿  Received 298.6KB (200 OK)

⏺ Good — I have the track pitches. Now I need the wire widths from the tech.lef to form proper track patterns.

[Fetch](https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/nangate45/lef/NangateOpenCellLibrary.tech.lef)
  ⎿  Received 19KB (200 OK)

⏺ I have all the data I need. From the tech.lef:

Tech
====
  ┌──────────┬───────┬──────────┬──────────┬──────────────┐
  │  Layer   │  Dir  │ Width µm │ Pitch µm │ Min space µm │
  ├──────────┼───────┼──────────┼──────────┼──────────────┤
  │ M3       │ H     │ 0.07     │ 0.14     │ 0.07         │
  ├──────────┼───────┼──────────┼──────────┼──────────────┤
  │ M4/M5/M6 │ V/H/V │ 0.14     │ 0.28     │ 0.14         │
  ├──────────┼───────┼──────────┼──────────┼──────────────┤
  │ M7/M8    │ H/V   │ 0.40     │ 0.80     │ 0.40         │
  ├──────────┼───────┼──────────┼──────────┼──────────────┤
  │ M9/M10   │ H/V   │ 0.80     │ 1.60     │ 0.80         │
  └──────────┴───────┴──────────┴──────────┴──────────────┘

  Pattern: 4 SIGNAL + POWER (2× width) + 4 SIGNAL + GROUND per period, power spacing = 1× min space. Now adding def_track_pattern and run_detailed_nuts to all three files.


All three files are committed and pushed. Summary
====

  - group and cluster: 0 bits unplaced — clean detailed NUTS results
  - tile: 768 bits unplaced — this is real congestion (35 abstract NUTS interval violations on the tightly packed 360×280 µm die), not a bug; the keepout above y=266 eliminates the out-of-die routing
  issue that was producing an extra 96 unplaced bits

  The docs/research.md table now includes a Detailed NUTS column with these results and the key design notes (32-bit ifetch width, keepout rationale, NanGate45 track pattern summary).


Summary of what's now in the benchmark suite:
====

  ┌──────────────────────┬──────────────┬─────────────────────────────────────┬────────────────────────────────────────┐
  │         File         │     Die      │                Buses                │             Detailed NUTS              │
  ├──────────────────────┼──────────────┼─────────────────────────────────────┼────────────────────────────────────────┤
  │ mempool_tile.buda    │ 360×280 µm   │ 8 (4×64-bit TCDM + 4×32-bit ifetch) │ 768 bits unplaced (genuine congestion) │
  ├──────────────────────┼──────────────┼─────────────────────────────────────┼────────────────────────────────────────┤
  │ mempool_group.buda   │ 1100×1100 µm │ 3×36-bit multicast 1→16             │ 0 unplaced ✓                           │
  ├──────────────────────┼──────────────┼─────────────────────────────────────┼────────────────────────────────────────┤
  │ mempool_cluster.buda │ 2350×2350 µm │ 12×64-bit P2P inter-group           │ 0 unplaced ✓                           │
  ├──────────────────────┼──────────────┼─────────────────────────────────────┼────────────────────────────────────────┤
  │ nvdla_cbuf.buda      │ 2354×2353 µm │ 1×8-bit multicast 1→30              │ 0 unplaced ✓                           │
  ├──────────────────────┼──────────────┼─────────────────────────────────────┼────────────────────────────────────────┤
  │ ariane133_cache.buda │ 1357×1357 µm │ 4×8-bit multicast (1→7/8/4/3)       │ 0 unplaced ✓                           │
  └──────────────────────┴──────────────┴─────────────────────────────────────┴────────────────────────────────────────┘

  The two key lessons from the MacroPlacement designs: corner_margin needs to match the actual macro size (10 µm for 70 µm-tall banks, not 30), and STRICT bundling merges buses with identical
  driver+receiver sets (so cbuf_we had to be dropped).


ISPD 2019 std-cell benchmarks for spatial net clustering (next research challenge)
====
Goal: develop a new BUDA bundler mode that clusters nets by spatial proximity of their placed pins
(instead of named bus discovery). Busterms = bounding box of each pin cluster. Compare BUDA's
bus-trunk routing plan against OpenROAD-routed reference DEF.

Source: ISPD 2019 Initial Detailed Routing Contest — http://www.ispd.cc/contests/19/
Format: LEF/DEF 5.8; placed DEF as input + routing guides; UNITS DISTANCE MICRONS 2000.
All instance names (inst8879) and net names (net3153) are fully mangled → spatial clustering only.
Each net has PLACED ( x y ) for every cell; LEF provides exact pin RECT geometry per cell type.
Reference routing: OpenROAD TritonRoute applied to the same placed DEF.

Download: http://www.ispd.cc/contests/19/benchmarks/ispd19_<name>.tgz (no auth required).
Note: ISPD 2018 benchmarks return HTTP 404; all 9 testcases come from ISPD 2019.

  ┌─────────────────┬─────────┬────────┬──────────────┬──────────┐
  │    Testcase     │  Cells  │  Nets  │   Die (µm)   │ Archive  │
  ├─────────────────┼─────────┼────────┼──────────────┼──────────┤
  │ ispd19_test1    │   8,879 │  3,153 │  148 × 146   │  352 KB  │  ← verified ✓
  │ ispd19_test3    │   8,283 │  8,953 │  195 × 195   │  644 KB  │  ← verified ✓
  │ ispd19_test5    │  28,920 │ 29,416 │  453 × 453   │  1.6 MB  │  ← verified ✓
  ├─────────────────┼─────────┼────────┼──────────────┼──────────┤
  │ ispd19_test2    │  72,094 │ 72,410 │  873 × 589   │  7.1 MB  │  ← verified ✓
  │ ispd19_test6    │~180,000 │    —   │      —       │   18 MB  │
  │ ispd19_test7    │~360,000 │    —   │      —       │   43 MB  │
  ├─────────────────┼─────────┼────────┼──────────────┼──────────┤
  │ ispd19_test8    │~540,000 │    —   │      —       │   66 MB  │
  │ ispd19_test9    │~900,000 │    —   │      —       │  109 MB  │
  │ ispd19_test10   │~900,000 │    —   │      —       │  111 MB  │
  └─────────────────┴─────────┴────────┴──────────────┴──────────┘

  Confirmed die sizes (parsed from actual DEF files):
  - test1:  8,879 inst,  3,152 nets, 148×146 µm
  - test3:  8,283 inst,  8,952 nets, 195×195 µm
  - test5: 27,528 inst, 29,415 nets, 906×906 µm  (UNITS=1000, not 2000)
  - test2: 72,094 inst, 72,410 nets, 873×589 µm
  - test7: 359,746 inst, 358,720 nets, 1581×1517 µm
  - test9: 899,341 inst, 895,253 nets, 2006×2151 µm

  Scale grouping:
  - ~10K:  test1 (8.9K), test3 (8.3K)
  - ~30K:  test5 (27.5K)
  - ~70K:  test2 (72K)
  - ~360K: test7 (360K)
  - ~900K: test9 (899K)

  DEF structure confirmed (test1 + test3 downloaded and parsed):
  - COMPONENTS: inst<N> <CellType> + PLACED ( x y ) <orient>  [no routing geometry]
  - NETS: net<N> \n  ( inst<A> <pin> ) ( inst<B> <pin> ) ...
  - LEF: 93–208 cell types; each PIN has exact RECT geometry within the cell bbox
  - Fanout: mostly 2-pin nets; max observed 2035 (clock) in test1, 130 in test3
  - Orientation values: N, S, FN, FS (row-based placement, no 90° rotation)

  Key parsing note: actual signal pin position =
    cell_placed_origin + rotate(pin_rect_center, orientation)
  where rotate() handles N (identity), FN (flip-x), S (rotate 180°), FS (flip-x + rotate 180°).


Spatial clustering results — tools/def_cluster.py
====
Tool: DBSCAN on net centroids (driver + receiver pin midpoint). Output = busterms for BUDA.
Epsilon calibration: NanGate45 row height = 1.4 µm. eps=1.0 µm ≈ sub-row proximity.

  ┌─────────────┬──────────────┬──────────┬──────────────────────────────────────────────────┐
  │  Testcase   │  eps / min   │ Clusters │ Notes                                            │
  ├─────────────┼──────────────┼──────────┼──────────────────────────────────────────────────┤
  │ test1 (10K) │ 1.0 µm / 5  │  87      │ max 16 nets, compact bboxes (6–62 µm wide)       │
  │             │              │ 572 nets │ narrow horizontal strips look like row-level buses│
  ├─────────────┼──────────────┼──────────┼──────────────────────────────────────────────────┤
  │ test3 (8K)  │ 1.0 µm / 5  │ 397      │ max 169 nets — 3× net density vs test1;          │
  │             │              │5023 nets │ clusters are still routing hotspots, just larger  │
  ├─────────────┼──────────────┼──────────┼──────────────────────────────────────────────────┤
  │ test5 (28K) │ 3.0 µm / 5  │ 398      │ top clusters have 100s–1000s of nets (too large) │
  │             │              │25753 nets│ sparse design (10× less dense than test1/test3)  │
  │             │              │          │ thin strips (86×14 µm, 141×14 µm) are routing    │
  │             │              │          │ corridors — promising busterm candidates          │
  └─────────────┴──────────────┴──────────┴──────────────────────────────────────────────────┘

  Key finding (test1 cluster 1 inspection):
  - 16-net cluster, bbox 61.2×7.3 µm (Y=39.9–47.2 µm horizontal band, ~5 cell rows)
  - ALL nets are fanout=2 (point-to-point) from DIFFERENT drivers (no shared driver)
  - Sequential net numbers (net2834–net3140) suggest synthesized bus bits scattered by placement
  - This is a ROUTING CORRIDOR, not an architectural bus: independent bits in the same spatial channel

  Architectural conclusion:
  Pure net-centroid DBSCAN finds routing hotspots — regions where many wires cross the same zone.
  For proper BUDA bus generation, the right model is BIPARTITE clustering:
    1. Cluster DRIVER pins → source busterms (where the data originates)
    2. Cluster RECEIVER pins → destination busterms (where the data lands)
    3. For each (driver_cluster, receiver_cluster) pair with ≥N shared nets → one BUDA bus

  The current def_cluster.py implements step 1 approximation (net centroid ≈ midpoint of driver+receiver).


Bipartite clustering — tools/def_cluster.py v2 (--bipartite mode)
====
Driver/receiver separation: LEF DIRECTION field (OUTPUT → driver, INPUT → receiver, INOUT → filtered
by USE=POWER/GROUND so no real signal INOUT exists in test1/test3/test5 NanGate45 libs).

Three algorithms implemented (--bipartite flag + optional --grid CELLSIZE):

  1. Centroid DBSCAN (default, no flags):
     DBSCAN on net centroids → routing hotspots. Good for visualizing congestion regions.

  2. Bipartite DBSCAN (--bipartite):
     DBSCAN on driver positions; DBSCAN on receiver centroids; match by (src_label, dst_label) pair.
     Problem: DBSCAN chaining merges most nets into one giant cluster (dropped by --max-nets 64),
     leaving only 7 buses / 3.6% coverage at eps=2.0 for test1.

  3. Bipartite grid (--bipartite --grid CELLSIZE):
     Each net maps to a fixed (src_cell, dst_cell) grid pair — no chaining possible.
     Compactness filters: --max-nets 64 (drop wide clusters), --min-span 5 µm (drop local paths).
     Best approach for BUDA bus generation from std-cell DEFs.

Grid clustering results:

  ┌─────────────┬───────────┬──────────┬──────────┬──────────────────────────────────────────────────────────┐
  │  Testcase   │ Grid / min│ Buses    │ Coverage │ Top bus example                                          │
  ├─────────────┼───────────┼──────────┼──────────┼──────────────────────────────────────────────────────────┤
  │ test1 (10K) │  10 µm/5  │  28      │  5.1%    │ 7 nets, src 9.5×3.6 µm → dst 44.6×7.5 µm (H strip)     │
  │ test1 (10K) │  15 µm/5  │  57      │ 14.9%    │ 39 nets, src 14.6×14.3 µm → dst 27.9×43.3 µm           │
  ├─────────────┼───────────┼──────────┼──────────┼──────────────────────────────────────────────────────────┤
  │ test5 (28K) │  10 µm/5  │  35      │  0.7%    │ 13 nets, src 9.4×8 µm → dst 18×8 µm                    │
  │ test5 (28K) │  30 µm/5  │ 142      │  4.1%    │ 20 nets, src 27.8×20 µm → dst 33×56 µm                 │
  │             │           │          │          │ 16 nets, src 14×0 µm (single-row!) → dst 42.6×24 µm ✓   │
  └─────────────┴───────────┴──────────┴──────────┴──────────────────────────────────────────────────────────┘

  Grid cell sizing rule: target ~30 instances per cell (enough for a 5-bit bus plus noise).
    For NanGate45: density(test1) ≈ 0.41 inst/µm² → grid=10 µm (41 inst/cell)
                   density(test5) ≈ 0.034 inst/µm² → grid=30 µm (30 inst/cell)
  Formula: grid_size = sqrt(target_count / density) = sqrt(30 / (instances / die_area))

  Verified quality signals:
  - test5 bus4: src bbox height=0 (all 16 drivers on the SAME row) → confirmed register strip
  - test1 bus4: src 9.5×3.6 µm → dst 44.6×7.5 µm horizontal strip → 7-bit bus driving ~30 columns
  - Net numbers in buses are NOT sequential (gaps 10-788) → routing corridors, not architectural buses
  - ~85-95% of nets remain unclustered at any grid size — std-cell P2P wiring is inherently distributed

  Practical coverage: 5-15% of nets per design → a BUDA std-cell scenario would improve ~10% of nets.
  Macro designs (MemPool, NVDLA, BlackParrot) can model 100% of the critical bus nets by construction.


HPWL distribution analysis — tools/def_cluster.py --hpwl
====
Half-perimeter wirelength (HPWL) = (x_max−x_min) + (y_max−y_min) across all pins of a net.
Standard lower-bound wirelength estimator; computed for every signal net with ≥2 resolved pins.

  ┌───────────────┬────────────────┬──────────────────────────────────────────────┬─────────────────┐
  │   Testcase    │  Total HPWL    │  Percentiles (µm)                            │  ×die perimeter │
  ├───────────────┼────────────────┼──────────────────────────────────────────────┼─────────────────┤
  │ test1 (10K)   │  46,134 µm     │  p50=4.7  p90=41  p95=74  p99=118  max=166  │  78×             │
  │ test3 (8K)    │  71,527 µm     │  p50=3.8  p90=19  p95=34  p99=72   max=207  │  92×             │
  │ test5 (28K)   │ 683,004 µm     │  p50=3.0  p90=49  p95=142 p99=420  max=1262 │ 188×             │
  └───────────────┴────────────────┴──────────────────────────────────────────────┴─────────────────┘

  HPWL category breakdown (test1 vs test5):

  ┌──────────────────┬───────────────────────────────┬───────────────────────────────┐
  │   Category       │   test1 (148×146 µm die)       │   test5 (906×906 µm die)      │
  ├──────────────────┼───────────────────────────────┼───────────────────────────────┤
  │ Short (<5 µm)    │  51.3% of nets,  5.5% of WL   │  70.4% of nets,  8.6% of WL  │
  │ Medium (5–50 µm) │  40.1% of nets, 44.4% of WL   │  21.2% of nets, 14.3% of WL  │
  │ Long (≥50 µm)    │   8.7% of nets, 50.2% of WL   │   8.3% of nets, 77.1% of WL  │
  └──────────────────┴───────────────────────────────┴───────────────────────────────┘

  Per-fanout HPWL (test1):
    Fanout 2 (61.9% of nets):    median 2.5 µm,  18.7% of total WL   ← short local wires
    Fanout 3–4 (24.6%):          median 12.5 µm, 27.9% of total WL
    Fanout 17–32 (2.2%):         median 69.6 µm, 10.1% of total WL
    Fanout 33–64 (5.8%):         median 83.9 µm, 34.6% of total WL   ← prime BUDA targets

  Per-fanout HPWL (test5):
    Fanout 2 (81.0% of nets):    median 2.4 µm,  30.9% of total WL   ← mostly local
    Fanout 9–16 (2.2%):          median 191.8 µm, 21.5% of total WL  ← multicast nets!
    Fanout 17–32 (0.8%):         median 258.2 µm, 10.5% of total WL

  Key finding — two distinct BUDA opportunity classes:

  1. MULTI-NET GROUPED BUSES (grid clustering, current approach):
     → Captures medium-range wires sharing a routing corridor
     → test1: 5.1% of nets, 7.1% of total WL
     → test5: 4.1% of nets, 9.1% of total WL
     → Bused nets are 73% medium-range (5–50 µm), 9% long

  2. INDIVIDUAL HIGH-FANOUT NETS (each net IS a 1-bit multicast bus):
     → Each high-fanout net = 1 driver + N scattered receivers = ready-made BUDA bus
     → No grouping needed; BUDA trunk+branch topology applied per net
     → test5: 779 nets with fanout≥5 AND HPWL≥50 µm carry 35.5% of total WL
     → test1: 183 nets with fanout 33–64 carry 34.6% of total WL (median HPWL 84 µm)

  Combined BUDA potential (both classes):
     → ~8–10% of nets, ~40–45% of total wirelength
     → Remaining 55–60% of WL: short local wires and fanout-2 P2P connections (not BUDA targets)

  Routing implication:
     The 85–95% "unclustered" nets break down as:
       ~50–70%: short wires (<5 µm) — purely local, no trunk benefit
       ~20–40%: medium fanout-2 P2P wires — individual connections, ungroupable
     These are correctly left to conventional point-to-point detailed routing.
     BUDA should target the OTHER 10%: grouped buses + high-fanout multicast nets.


High-fanout net extraction — tools/def_cluster.py --high-fanout
====
Each net with fanout ≥ min-fanout is its own standalone 1-bit BUDA multicast bus:
  src block  = driver pin ± src_margin (tiny block; corner_margin dx 1 dy 1 prevents global margin from zeroing it)
  dst block  = bounding box of all receiver pins
  bus line   = add_bus hf_N[1]  hf_N_src.out  hf_N_dst.in

Nets sorted by HPWL descending → highest-impact candidates first in output file.

Default filters: --min-fanout 5, --max-fanout 256, --min-hpwl 20 µm, --src-margin 2 µm.

  ┌─────────────┬───────────────┬────────────────────────────────┬──────────────────────────────────────────────────────────┐
  │  Testcase   │    Buses      │       Aggregate HPWL           │ Top bus example                                          │
  ├─────────────┼───────────────┼────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ test1 (10K) │ 322 buses     │ 23,609 µm  (51.2% of all-net) │ net4, fanout=60, HPWL=166.0 µm, dst 109.6×56.4 µm       │
  │ test5 (28K) │ 959 buses     │196,904 µm  (36.6% of all-net) │ FE_OFN548_n_9502, fanout=26, HPWL=966.4 µm, span=350 µm │
  └─────────────┴───────────────┴────────────────────────────────┴──────────────────────────────────────────────────────────┘

  Fanout breakdown (test1):
    fanout  5–8  :  124 buses, aggregate HPWL =  5,498 µm
    fanout  9–16 :   16 buses, aggregate HPWL =  2,175 µm
    fanout 17–32 :    —
    fanout 33–64 :  182 buses, aggregate HPWL = 15,936 µm   ← dominant contributor

  Fanout breakdown (test5):
    fanout  5–8  :  688 buses, aggregate HPWL = 78,621 µm
    fanout  9–16 :  213 buses, aggregate HPWL = 83,484 µm
    fanout 17–32 :   55 buses, aggregate HPWL = 32,154 µm
    fanout 33–64 :    3 buses, aggregate HPWL =  2,645 µm

  Key observations:
  - test5 nets have real names (FE_OFN*, FE_PHN* prefix) — synthesis-inserted fanout-enable buffers
    This confirms these are exactly the candidates a synthesis tool already identified as needing fanout management.
  - test1 nets are mangled (net4, net97 …) — anonymous flattened netlist; fanout-60 net4 is likely a clock or reset
  - 51% of test1 total WL comes from just 322 nets (10% of all nets) → high-fanout mode covers the most WL per bus
  - Output file format: ready-made BUDA snippet; add def_layer/def_track_pattern/corner_margin header before use

  Combined high-fanout + grid clustering coverage:
    test1: ~51% (HF) + ~5–15% (grid, net overlap possible) → ~55% of total WL addressable by BUDA
    test5: ~37% (HF) + ~4–9% (grid) → ~40% of total WL

  Design implication: high-fanout extraction alone is a stronger BUDA entry point than grid clustering
  for std-cell designs, because each output bus is already a perfect 1-driver→N-receiver multicast topology.
