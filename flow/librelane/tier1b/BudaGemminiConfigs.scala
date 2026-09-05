package chipyard

import org.chipsalliance.cde.config.{Config}
import gemmini.{GemminiCustomConfig, GemminiCustomConfigs}

// Tier 1b of the BUDA/LibreLane study (docs/internal/librelane_hier_flow.md
// §7.1): one Rocket core plus a Gemmini whose systolic MESH is N x N, at
// several N, so the same design is generated at several sizes.  Everything
// else is Gemmini's default (int8 inputs, int32 accumulators, 1x1 tiles, the
// default scratchpad/accumulator) -- the mesh is the only dial.
//
// Drop this file into generators/chipyard/src/main/scala/config/ of a Chipyard
// checkout; then, from sims/verilator:
//
//   make verilog CONFIG=BudaGemminiMesh4RocketConfig
//
// The mesh alone is the tier-1b design (the generated module is `Mesh`, which
// instantiates `Tile`, which instantiates `PE` -- the repeated cells); the
// whole `ChipTop` is tier 2.
class BudaGemminiMesh4RocketConfig extends Config(
  new GemminiCustomConfig(GemminiCustomConfigs.defaultConfig.copy(meshRows = 4, meshColumns = 4)) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class BudaGemminiMesh8RocketConfig extends Config(
  new GemminiCustomConfig(GemminiCustomConfigs.defaultConfig.copy(meshRows = 8, meshColumns = 8)) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class BudaGemminiMesh16RocketConfig extends Config(
  new GemminiCustomConfig(GemminiCustomConfigs.defaultConfig.copy(meshRows = 16, meshColumns = 16)) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
