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

    // Fan-in metadata (multi-driver CONVERGENT/COMBINED hier bundles only —
    // same-level OR cross-level, else empty): per-net driver and receiver
    // names ALIGNED with net_names, in the bundle's frame (depth-level or
    // cross-level component paths).  Feeds the per-bit taper
    // (derive_fanin_seg_bits) at hier generation; not persisted — a resumed
    // session recovers the endpoints from the FANIN reason and falls back to
    // conservative full width.
    std::vector<std::string>              net_drivers;
    std::vector<std::vector<std::string>> net_receivers;

    std::vector<std::string> get_net_names() const { return net_names; }
};
// STRICT       — same driver + same receivers (a true parallel bus).
// CONVERGENT    — same receivers only, driver ignored (fan-in).
// DIVERGENT     — same DRIVER only, receivers ignored (fan-OUT): the mirror of
//                 CONVERGENT, and the point the lattice was missing.  N nets
//                 leaving one driver for N different places are the same
//                 physical object as N nets arriving at one sink from N
//                 places, drawn backwards — a 32-bit port bus reaching 32 die
//                 pads bundled under CONVERGENT going IN and not coming OUT
//                 (opens_interchange.md item 11).  Realized as a per-bit
//                 tapered tree rooted at the shared driver (reason
//                 'FANOUT:root|TO:leaves'), so bit k's wire lands only on the
//                 block bit k actually reaches.
//
//                 It is a WEAKER signal than CONVERGENT and is opt-in for
//                 that reason: a high-fanout driver is not always a bus (a
//                 clock buffer's 200 sinks are not), so ask for it per design
//                 and exclude what it should not touch with `set_bundling
//                 <prefix> no_divergent`.
// BIDIRECTIONAL — direction-agnostic: signature is the sorted set of ALL
//                 endpoint instances (driver + receivers), so nets that connect
//                 the same group of instances in any driver/receiver roles are
//                 bundled together — a→b,c with b→c,a with c→b,a, or simply
//                 A→B with its return B→A.  Routing is block-to-block and
//                 direction-agnostic, so the single trunk serves every net.
// COMBINED      — the JOIN of CONVERGENT and BIDIRECTIONAL: nets merge when
//                 connected by a CHAIN of either relation (union-find), the
//                 only genuinely new point on the strategy lattice
//                 STRICT ⊂ {CONVERGENT, BIDIRECTIONAL} ⊂ COMBINED.
//
//                 COMBINED deliberately does NOT include DIVERGENT.  The join
//                 is of the relations that are safe to apply unasked; fan-out
//                 is not one of them (see DIVERGENT above), and folding it in
//                 would silently re-bundle every existing COMBINED flow.
//                 Ask for DIVERGENT by name.
enum class Strategy { STRICT, CONVERGENT, BIDIRECTIONAL, COMBINED, DIVERGENT };
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
    // STRICT (default) groups by driver + receivers; BIDIRECTIONAL is
    // direction-agnostic (sorted set of all endpoint names), so a net and its
    // reverse — and the cyclic multi-receiver case — bundle together;
    // CONVERGENT groups by receiver set only (fan-in); COMBINED is the join
    // of the latter two (chains of either relation, union-find).  Applied per
    // bundling depth to same-level AND cross-level nets alike: a multi-driver
    // cross-level group under CONVERGENT/COMBINED forms one fan-in bundle
    // (per-net endpoints in net_drivers/net_receivers + a FANIN reason), the
    // cross-level twin of the same-level fan-in.
    void set_strategy(Strategy s) { _strategy = s; }
    // Per-net-name-prefix permission overrides (set_bundling): mode is one
    // of strict|no_convergent|no_bidirectional|combined; longest matching
    // prefix wins, "*" is the global default.  A merge via a relation needs
    // the strategy AND both nets to permit it.
    void set_bundling_overrides(
        const std::vector<std::pair<std::string, std::string>>& ovr) {
        _overrides = ovr;
    }
    std::vector<HBundle> run(int max_depth = 1);

private:
    BDB& _db;
    Strategy _strategy = Strategy::STRICT;
    std::vector<std::pair<std::string, std::string>> _overrides;
    // Relations ("conv"/"bidir") the named net may merge through, resolved
    // from _overrides (longest prefix) ∩ the strategy's relations.
    bool _net_allows(const std::string& net_name, const char* rel) const;

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

    // Build BIDIRECTIONAL sig: "BIDIR:<name1>,<name2>,…" — the sorted, unique set
    // of ALL endpoint names (driver + receivers), so direction is ignored.
    static std::string _bidir_sig(std::vector<std::string> all_names);

    // Signature for the active strategy given a net's driver + receiver names.
    std::string _sig(const std::string& drv_name,
                     const std::vector<std::string>& sorted_rcv_names) const;
};
}
