#pragma once
// bundle_refiner.h — Hierarchical bundle splitting.
// Monitors BDB for newly resolved subblock boundaries; splits depth-N bundles
// into depth-(N+1) sub-bundles when BOTH netlist divergence AND subblock
// placement are known. Parent bundle plan is a hard constraint on children.
// Replicated components are flagged for separate BUDA planning tasks.

#include <string>
#include <vector>
#include "bdb.h"

namespace buda {

struct RefinementResult {
    std::vector<std::string> new_bundle_ids;    // child bundles created
    std::vector<std::string> affected_nets;     // nets re-assigned
    std::vector<std::string> replicated_comps;  // flagged for separate planning
};

class BundleRefiner {
public:
    explicit BundleRefiner(BDB& db);

    // Split all refineable bundles up to max_depth levels below their current depth.
    RefinementResult refine(int max_depth = 1);

    // Split one specific bundle. Returns ids of child bundles created.
    std::vector<std::string> refine_bundle(const std::string& bundle_id);

private:
    BDB& _db;

    bool _can_refine(const BundleRow& b) const;
};

}  // namespace buda
