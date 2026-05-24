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

  Scale grouping:
  - ~10K:  test1, test3, test5  (note: test5 is 29K — no true 10K third case available)
  - ~100K: test2, test6, test7
  - ~1M:   test8, test9, test10 (tops out at ~900K; none exceeds 1M in this set)

  DEF structure confirmed (test1 + test3 downloaded and parsed):
  - COMPONENTS: inst<N> <CellType> + PLACED ( x y ) <orient>  [no routing geometry]
  - NETS: net<N> \n  ( inst<A> <pin> ) ( inst<B> <pin> ) ...
  - LEF: 93–208 cell types; each PIN has exact RECT geometry within the cell bbox
  - Fanout: mostly 2-pin nets; max observed 2035 (clock) in test1, 130 in test3
  - Orientation values: N, S, FN, FS (row-based placement, no 90° rotation)

  Key parsing note: actual signal pin position =
    cell_placed_origin + rotate(pin_rect_center, orientation)
  where rotate() handles N (identity), FN (flip-x), S (rotate 180°), FS (flip-x + rotate 180°).
