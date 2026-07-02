/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

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
    // Reserved for the pending refinement implementation (refine()/refine_bundle()
    // are stubs today).  [[maybe_unused]] silences clang's -Wunused-private-field
    // while the class is still scaffolding; GCC ignores the attribute harmlessly.
    [[maybe_unused]] BDB& _db;

    bool _can_refine(const BundleRow& b) const;
};

}  // namespace buda
