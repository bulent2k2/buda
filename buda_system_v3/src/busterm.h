#pragma once
// busterm.h — Busterm derivation and incremental refinement.
// Derives spatial busterm regions from BDB at the best currently-available
// resolution per component endpoint, and updates them as placement matures.

#include <string>
#include <vector>
#include "bdb.h"

namespace buda {

enum class BustermResolution { BLOCK, SPATIAL_CLUSTER, PORT };

struct Busterm {
    std::string      id;
    std::string      hier_path;
    int              depth       = 0;
    double           x1, y1, x2, y2;
    BustermResolution resolution = BustermResolution::BLOCK;
    std::string      parent_id;   // coarser busterm; empty at depth 0
};

class BustermGen {
public:
    explicit BustermGen(BDB& db);

    // Derive busterms for all component endpoints up to max_depth.
    // Writes results into BDB's busterm table.
    void derive(int max_depth = 0);

    // Re-derive only the busterms affected by newly placed components
    // (incremental update called after design evolves).
    void refine();

    // Return all busterms currently in BDB.
    std::vector<Busterm> all() const;

private:
    BDB& _db;
    Busterm _from_component(const ComponentRow& comp, int depth) const;
};

}  // namespace buda
