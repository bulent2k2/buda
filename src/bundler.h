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
#include <vector>
#include <string>
#include <map>
#include <set>
#include <unordered_map>
#include "bdb.h"
namespace buda {
struct Net {
    std::string name;
    std::string driver_pin;
    std::vector<std::string> receiver_pins;
    std::string get_driver_instance() const;
    std::set<std::string> get_receiver_instances() const;
};
struct HBundle {
    int id;
    std::vector<std::string> net_names;
    std::string reason;
    int num_terminals = 0;

    // Hierarchy fields
    int level = 0;                          // 0 = top-level, ≥1 = sub-level
    std::string cell_context;               // "" for top-level; cell type for cell-level designs
    std::vector<std::string> instances;     // instance paths this hbundle covers
    int parent_id = -1;                     // -1 for top-level hbundles
    std::vector<int> child_ids;
    std::vector<std::string> entry_busterm_ids;
    std::vector<std::string> exit_busterm_ids;

    // Cross-level endpoint info (set when driver and receiver are at different depths).
    // drv_spec_depth >= 0 distinguishes cross-level bundles from same-level ones.
    int drv_spec_depth = -1;
    int rcv_spec_depth = -1;
    std::string drv_spec_path;
    std::vector<std::string> rcv_spec_paths;

    std::vector<std::string> get_net_names() const { return net_names; }
};
enum class Strategy { STRICT, CONVERGENT };
class Netlist {
public:
    void add_net(const std::string& name, const std::string& driver, const std::vector<std::string>& receivers);
    const std::vector<Net>& get_nets() const { return nets_; }
private:
    std::vector<Net> nets_;
};
class Bundler {
public:
    Bundler() : current_strategy_(Strategy::STRICT) {}
    void set_strategy(Strategy s) { current_strategy_ = s; }
    void set_depth(int d) { depth_ = d; }
    std::vector<HBundle> run(const Netlist& netlist);
private:
    Strategy current_strategy_;
    int depth_ = 0;
    std::string generate_signature(const Net& net) const;
};

// Hierarchy-aware bundler: reads nets+pins directly from BDB and produces
// HBundles at each depth level 0..max_depth.  See docs/HIER_BUNDLER.md.
class HierarchicalBundler {
public:
    explicit HierarchicalBundler(BDB& db);
    std::vector<HBundle> run(int max_depth = 1);

private:
    BDB& _db;

    // Counts nets that fell back to UNKNOWN-direction positional driver/receiver
    // assignment during a run(); summarized once instead of one line per net.
    mutable int _unk_fallback_count = 0;

    struct NetEndpoints {
        int driver_comp_id = -1;
        std::vector<int> receiver_comp_ids;
    };

    // Returns, for each net that has both a driver and ≥1 receiver at
    // exactly depth D, its driver and receiver component ids.
    std::unordered_map<int, NetEndpoints> _endpoints_at_depth(
        const std::unordered_map<int, std::vector<PinRow>>& pins_by_net,
        const std::unordered_map<int, ComponentRow>& comp_by_id,
        int depth) const;

    // Build STRICT sig: "DRV:<drv_name>|REC:<rcv1>,<rcv2>,…"
    static std::string _strict_sig(
        const std::string& drv_name,
        const std::vector<std::string>& sorted_rcv_names);
};
}
