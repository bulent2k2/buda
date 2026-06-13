#pragma once
// busterm.h — Hierarchical busterm derivation and incremental refinement.
// Derives spatial busterm regions from BDB at the best currently-available
// resolution per component endpoint, and updates them as placement matures.
//
// NOTE: This "HierBusterm" is a BDB-level concept distinct from the
// routing-level "Busterm" in topology.h (which carries block_name + Rect).

#include <string>
#include <unordered_set>
#include <vector>
#include "bdb.h"

namespace buda {

enum class BustermResolution { BLOCK, SPATIAL_CLUSTER, PORT };

// Hierarchical busterm: one per component, representing that component's
// routing connection region in the physical hierarchy.
struct HierBusterm {
    std::string      id;         // unique: "bt:" + hier_path
    std::string      hier_path;  // component path (e.g. "ai/a1i1")
    int              depth       = 0;
    double           x1, y1, x2, y2;
    BustermResolution resolution = BustermResolution::BLOCK;
    std::string      parent_id;  // coarser busterm; empty at depth 0
    // Optional multi-rect geometry (e.g. TEG blocks).  Empty = single-rect.
    std::vector<std::tuple<double,double,double,double>> rects;
};

class BustermGen {
public:
    explicit BustermGen(BDB& db);

    // Derive busterms for all component endpoints up to max_depth.
    // Clears the existing busterm table and rewrites from BDB components.
    void derive(int max_depth = 0);

    // Re-derive using the same max_depth as the last derive() call.
    // Clears the busterm table and re-runs from scratch; a smarter
    // incremental implementation is deferred until needed.
    void refine();

    // Return all busterms currently in BDB.
    std::vector<HierBusterm> all() const;

private:
    BDB& _db;
    int  _max_depth = 0;
    HierBusterm _from_component(const ComponentRow& comp,
                                const std::unordered_set<std::string>& cells_with_pins) const;
};

}  // namespace buda
