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
Two designs extracted from the MacroPlacement repo's NanGate45 placed-macro DEFs.
Block positions are centroid ± half-macro-size derived from fp_placed_macros.def.

  ┌──────────────────────────┬──────────────┬──────────────────────────────────────┬───────────────────────────────────────┬─────────┬───────────────────────────────────┬─────────────────────────────────────┐
  │           File           │     Die      │              Blocks                  │                  Buses                │ Bundles │          Abstract NUTS            │           Detailed NUTS             │
  ├──────────────────────────┼──────────────┼──────────────────────────────────────┼───────────────────────────────────────┼─────────┼───────────────────────────────────┼─────────────────────────────────────┤
  │ nvdla_cbuf.buda          │ 2354×2353 µm │ 31 (cbuf_ctrl + 30 cbuf banks)       │ 1×8-bit cbuf_addr (1→30 multicast)    │ 1       │ 0 violations, 4 span adjustments  │ 240 net segments, 0 bits unplaced ✓ │
  ├──────────────────────────┼──────────────┼──────────────────────────────────────┼───────────────────────────────────────┼─────────┼───────────────────────────────────┼─────────────────────────────────────┤
  │ ariane133_cache.buda     │ 1357×1357 µm │ 24 (ctrl + 7 d_tag + 8 d_data +      │ 4×8-bit (d_tag_addr 1→7, d_data_addr  │ 4       │ 10 violations, 3 overlaps         │ 168 net segments, 0 bits unplaced ✓ │
  │                          │              │      1 d_valid + 3 i_tag + 4 i_data) │ 1→8, i_data_fetch 1→4, i_tag_addr 1→3│         │                                   │                                     │
  └──────────────────────────┴──────────────┴──────────────────────────────────────┴───────────────────────────────────────┴─────────┴───────────────────────────────────┴─────────────────────────────────────┘

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
