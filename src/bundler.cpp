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

#include "bundler.h"
#include <functional>
#include <set>
#include <sstream>
#include <algorithm>
#include <climits>
#include <iostream>
namespace buda {
std::string extract_instance(const std::string& pin) {
    size_t last_dot = pin.find_last_of('.');
    // A pin with no dot is the shorthand form: the bare token IS the block name
    // (this is how BudaSession._pin_instance and the topology path treat it).
    // Returning a "top" sentinel here collapsed every pinless endpoint to one
    // grouping key, so two distinct shorthand buses (e.g. `a left right` and
    // `b up down`) merged into a single bundle and mis-routed (issue #16).
    if (last_dot == std::string::npos) return pin;
    return pin.substr(0, last_dot);
}
std::string Net::get_driver_instance() const { return extract_instance(driver_pin); }
std::set<std::string> Net::get_receiver_instances() const {
    std::set<std::string> instances;
    for (const auto& pin : receiver_pins) instances.insert(extract_instance(pin));
    return instances;
}
void Netlist::add_net(const std::string& name, const std::string& driver, const std::vector<std::string>& receivers) {
    Net n; n.name = name; n.driver_pin = driver; n.receiver_pins = receivers;
    nets_.push_back(n);
}
std::string Bundler::generate_signature(const Net& net) const {
    std::set<std::string> recv_insts = net.get_receiver_instances();

    if (current_strategy_ == Strategy::BIDIRECTIONAL) {
        // Direction-agnostic: key on the sorted set of ALL endpoint instances
        // (driver + receivers) so nets connecting the same group of instances in
        // any roles bundle together — A→B with B→A, or a→b,c with b→c,a / c→b,a.
        std::set<std::string> all_insts = recv_insts;
        all_insts.insert(net.get_driver_instance());
        std::string sig = "BIDIR:";
        bool first = true;
        for (const auto& inst : all_insts) { if (!first) sig += ","; sig += inst; first = false; }
        return sig;
    }

    std::stringstream signature;
    std::string rec_sig;
    for (const auto& inst : recv_insts) {
        if (!rec_sig.empty()) rec_sig += ",";
        rec_sig += inst;
    }
    if (current_strategy_ == Strategy::STRICT) {
        signature << "DRV:" << net.get_driver_instance() << "|REC:" << rec_sig;
    } else {
        signature << "REC:" << rec_sig;
    }
    return signature.str();
}
std::vector<HBundle> Bundler::run(const Netlist& netlist) {
    std::vector<HBundle> bundles;
    std::map<std::string, std::vector<std::string>> groups;
    for (const auto& net : netlist.get_nets()) {
        groups[generate_signature(net)].push_back(net.name);
    }
    int bundle_id_counter = 0;
    for (const auto& [sig, net_list] : groups) {
        HBundle b;
	b.id = ++bundle_id_counter;
	b.net_names = net_list;
	b.reason = sig;
	const Net* first_net = nullptr;
	for (const auto& net : netlist.get_nets()) {
	  if (net.name == net_list[0]) {
	    first_net = &net; break;
	  }
	}
	if (first_net) {
	  b.num_terminals = 1 + (int)first_net->get_receiver_instances().size();
	}
        bundles.push_back(b);
    }
    return bundles;
}

// ── HierarchicalBundler ────────────────────────────────────────────────────

HierarchicalBundler::HierarchicalBundler(BDB& db) : _db(db) {}

std::string HierarchicalBundler::_strict_sig(
        const std::string& drv_name,
        const std::vector<std::string>& sorted_rcv_names) {
    std::string sig = "DRV:" + drv_name + "|REC:";
    for (const auto& n : sorted_rcv_names) { sig += n; sig += ','; }
    return sig;
}

std::string HierarchicalBundler::_bidir_sig(std::vector<std::string> all_names) {
    std::sort(all_names.begin(), all_names.end());
    all_names.erase(std::unique(all_names.begin(), all_names.end()), all_names.end());
    std::string sig = "BIDIR:";
    for (const auto& n : all_names) { sig += n; sig += ','; }
    return sig;
}

std::string HierarchicalBundler::_sig(
        const std::string& drv_name,
        const std::vector<std::string>& sorted_rcv_names) const {
    if (_strategy == Strategy::BIDIRECTIONAL) {
        std::vector<std::string> all = sorted_rcv_names;
        all.push_back(drv_name);
        return _bidir_sig(std::move(all));
    }
    if (_strategy == Strategy::CONVERGENT) {
        std::string sig = "REC:";
        for (const auto& n : sorted_rcv_names) { sig += n; sig += ','; }
        return sig;
    }
    return _strict_sig(drv_name, sorted_rcv_names);
}

bool HierarchicalBundler::_net_allows(const std::string& net_name,
                                      const char* rel) const {
    // Longest matching prefix wins; "*" is the global default; no match =
    // fully permissive.  Mirrors the flat _net_allowed_relations exactly.
    const std::string* best = nullptr;
    int best_len = -1;
    for (const auto& [prefix, mode] : _overrides) {
        if (prefix == "*") {
            if (best_len < 0) { best = &mode; best_len = 0; }
        } else if (net_name.compare(0, prefix.size(), prefix) == 0 &&
                   (int)prefix.size() > best_len) {
            best = &mode; best_len = (int)prefix.size();
        }
    }
    if (!best) return true;                       // permissive default
    if (*best == "strict")           return false;
    if (*best == "no_convergent")    return std::string(rel) == "bidir";
    if (*best == "no_bidirectional") return std::string(rel) == "conv";
    return true;                                  // "combined"
}

std::unordered_map<int, HierarchicalBundler::NetEndpoints>
HierarchicalBundler::_endpoints_at_depth(
        const std::unordered_map<int, std::vector<PinRow>>& pins_by_net,
        const std::unordered_map<int, ComponentRow>& comp_by_id,
        int depth) const {
    std::unordered_map<int, NetEndpoints> result;
    for (const auto& [net_id, pins] : pins_by_net) {
        NetEndpoints ep;
        std::vector<int> extra_output_ids;
        std::vector<int> inout_comp_ids;
        std::vector<int> unknown_comp_ids;
        for (const auto& p : pins) {
            auto it = comp_by_id.find(p.comp_id);
            if (it == comp_by_id.end() || it->second.depth != depth) continue;
            if (p.dir == "OUTPUT") {
                if (ep.driver_comp_id < 0)
                    ep.driver_comp_id = p.comp_id;
                else if (p.comp_id != ep.driver_comp_id)
                    extra_output_ids.push_back(p.comp_id);
            }
            else if (p.dir == "INPUT")
                ep.receiver_comp_ids.push_back(p.comp_id);
            else if (p.dir == "INOUT")
                inout_comp_ids.push_back(p.comp_id);
            else if (p.dir == "UNKNOWN")
                unknown_comp_ids.push_back(p.comp_id);
        }
        // Multi-driven net (audit C10-02): OUTPUT pins after the first are
        // REAL distinct drivers (same-depth comps are never each other's
        // ancestor projections).  They used to fall through every branch —
        // neither driver nor receiver — so their blocks were silently
        // dropped from the route: a guaranteed open no check could see.
        // Attach them as additional endpoints (the INOUT secondary-driver
        // treatment): routing needs the physical attachment, and the
        // fan-in/fidelity machinery models per-net drivers separately.
        if (!extra_output_ids.empty()) {
            for (int id : extra_output_ids)
                ep.receiver_comp_ids.push_back(id);
            std::cerr << "[HierBundler] net_id=" << net_id << " at depth "
                      << depth << ": " << (extra_output_ids.size() + 1)
                      << " OUTPUT drivers — extra driver blocks attached "
                      << "as endpoints\n";
        }
        // INOUT fallback: secondary driver when no OUTPUT exists;
        // otherwise INOUT pins are additional receivers.
        if (ep.driver_comp_id < 0 && !inout_comp_ids.empty()) {
            ep.driver_comp_id = inout_comp_ids[0];
            for (size_t i = 1; i < inout_comp_ids.size(); ++i)
                ep.receiver_comp_ids.push_back(inout_comp_ids[i]);
            std::cerr << "[HierBundler] net_id=" << net_id
                      << " at depth " << depth
                      << ": using INOUT pin as driver\n";
        } else {
            for (int id : inout_comp_ids)
                ep.receiver_comp_ids.push_back(id);
        }
        // Fallback: use UNKNOWN pins positionally when OUTPUT/INPUT are absent.
        if (ep.driver_comp_id < 0 && !unknown_comp_ids.empty()) {
            ep.driver_comp_id = unknown_comp_ids[0];
            for (size_t i = 1; i < unknown_comp_ids.size(); ++i)
                ep.receiver_comp_ids.push_back(unknown_comp_ids[i]);
            ++_unk_fallback_count;
        }
        // A net with a real OUTPUT (or INOUT) driver but ONLY UNKNOWN-direction
        // sinks (audit C10-03): the positional fallback above only fires when
        // NO driver was found, so such a net kept an empty receiver list and
        // was dropped entirely below — never routed — even though both the
        // all-UNKNOWN and the fully-annotated forms route fine. Promote the
        // UNKNOWN pins to receivers (skipping the driver's own comp), the same
        // positional spirit already applied to the all-UNKNOWN case.
        else if (ep.driver_comp_id >= 0 && ep.receiver_comp_ids.empty()
                 && !unknown_comp_ids.empty()) {
            for (int id : unknown_comp_ids)
                if (id != ep.driver_comp_id)
                    ep.receiver_comp_ids.push_back(id);
            if (!ep.receiver_comp_ids.empty())
                ++_unk_fallback_count;
        }
        if (ep.driver_comp_id >= 0 && !ep.receiver_comp_ids.empty())
            result[net_id] = std::move(ep);
    }
    return result;
}

std::vector<HBundle> HierarchicalBundler::run(int max_depth) {
    _unk_fallback_count = 0;
    _db.infer_pin_dirs_from_cell_pins();

    // ── 1. Index BDB ──────────────────────────────────────────────────────────
    auto all_comps = _db.all_components();
    auto all_nets  = _db.all_nets();
    auto all_pins  = _db.all_pins();

    std::unordered_map<int, ComponentRow> comp_by_id;
    comp_by_id.reserve(all_comps.size());
    for (const auto& c : all_comps) comp_by_id[c.id] = c;

    std::unordered_map<int, std::string> net_name;
    net_name.reserve(all_nets.size());
    for (const auto& n : all_nets) net_name[n.id] = n.name;

    std::unordered_map<int, std::vector<PinRow>> pins_by_net;
    for (const auto& p : all_pins) pins_by_net[p.net_id].push_back(p);

    // Build comp-name → busterm-id map.  Populated only when derive_busterms
    // has been called before run_hier_bundler; empty map = graceful no-op.
    std::unordered_map<std::string, std::string> bt_by_comp_name;
    for (const auto& bt : _db.all_busterms())
        bt_by_comp_name[bt.hier_path] = bt.id;

    std::vector<HBundle> bundles;
    std::unordered_map<int, int> id_to_idx;   // bundle id → index in bundles
    int next_id = 0;

    // ── 1b. Pre-compute per-net leaf (most-specific endpoint) info ────────────
    // Ancestor pin propagation (add_net_pins) inserts pins for every ancestor
    // between the specified endpoint and the common ancestor.  For a cross-level
    // bus (driver and receiver at different hierarchy depths) the propagated pins
    // make the net appear at EVERY ancestor depth, causing it to be merged with
    // same-level buses that happen to share the same ancestor-level component names.
    // Fix: detect cross-level nets by comparing the deepest driver/receiver depths,
    // then process them at their correct bundle_depth using the actual leaf paths.

    struct NetLeafInfo {
        int drv_spec_comp_id = -1;
        int drv_spec_depth   = -1;
        std::string drv_spec_path;
        std::vector<int>         rcv_spec_comp_ids;
        int                      rcv_spec_depth = -1;
        std::vector<std::string> rcv_spec_paths;
        // Extra same-depth OUTPUT drivers (multi-driven net, audit C10-02):
        // attached as additional receiver endpoints after the scan.
        std::vector<int>         extra_drv_comp_ids;
        std::vector<std::string> extra_drv_paths;
        // Deepest INOUT-direction pin (priority: OUTPUT > INOUT > UNKNOWN).
        int inout_spec_depth   = -1;
        int inout_spec_comp_id = -1;
        std::string inout_spec_path;
        std::vector<int>         inout_spec_comp_ids;
        std::vector<std::string> inout_spec_paths;
        // Deepest UNKNOWN-direction pin — used as positional fallback.
        int unk_spec_depth   = -1;
        int unk_spec_comp_id = -1;
        std::string unk_spec_path;
        std::vector<int>         unk_spec_comp_ids;
        std::vector<std::string> unk_spec_paths;
        int  bundle_depth   = 0;
        bool is_cross       = false;
        bool is_degenerate  = false;
    };

    // Count matching leading path segments (e.g. "left/top/hi", "left/top/lo" → 2).
    auto path_common = [](const std::string& a, const std::string& b) -> int {
        size_t ia = 0, ib = 0; int depth = 0;
        while (ia < a.size() && ib < b.size()) {
            size_t pa = a.find('/', ia); if (pa == std::string::npos) pa = a.size();
            size_t pb = b.find('/', ib); if (pb == std::string::npos) pb = b.size();
            size_t la = pa - ia, lb = pb - ib;
            if (la != lb || a.compare(ia, la, b, ib, lb) != 0) break;
            ++depth; ia = pa + 1; ib = pb + 1;
        }
        return depth;
    };

    // True when prefix is a path-prefix of path (i.e. path == prefix or path starts with prefix+'/').
    auto is_prefix = [](const std::string& prefix, const std::string& path) -> bool {
        return path.size() >= prefix.size() &&
               path.compare(0, prefix.size(), prefix) == 0 &&
               (path.size() == prefix.size() || path[prefix.size()] == '/');
    };

    std::unordered_map<int, NetLeafInfo> net_leaf;
    for (const auto& [net_id, pins] : pins_by_net) {
        NetLeafInfo info;
        for (const auto& p : pins) {
            auto it = comp_by_id.find(p.comp_id);
            if (it == comp_by_id.end()) continue;
            int d = it->second.depth;
            if (p.dir == "OUTPUT") {
                if (d > info.drv_spec_depth) {
                    // A deeper OUTPUT supersedes: shallower OUTPUT pins are
                    // the same driver's ancestor interface projections, not
                    // distinct drivers.
                    info.drv_spec_depth   = d;
                    info.drv_spec_comp_id = p.comp_id;
                    info.drv_spec_path    = it->second.name;
                    info.extra_drv_comp_ids.clear();
                    info.extra_drv_paths.clear();
                } else if (d == info.drv_spec_depth &&
                           p.comp_id != info.drv_spec_comp_id) {
                    // Same-depth second OUTPUT = a real distinct driver
                    // (audit C10-02) — collect, attach as endpoint below.
                    info.extra_drv_comp_ids.push_back(p.comp_id);
                    info.extra_drv_paths.push_back(it->second.name);
                }
            } else if (p.dir == "INPUT") {
                if (d > info.rcv_spec_depth) {
                    info.rcv_spec_depth    = d;
                    info.rcv_spec_comp_ids = {p.comp_id};
                    info.rcv_spec_paths    = {it->second.name};
                } else if (d == info.rcv_spec_depth) {
                    info.rcv_spec_comp_ids.push_back(p.comp_id);
                    info.rcv_spec_paths.push_back(it->second.name);
                }
            } else if (p.dir == "INOUT") {
                if (d > info.inout_spec_depth) {
                    info.inout_spec_depth    = d;
                    info.inout_spec_comp_id  = p.comp_id;
                    info.inout_spec_path     = it->second.name;
                    info.inout_spec_comp_ids = {p.comp_id};
                    info.inout_spec_paths    = {it->second.name};
                } else if (d == info.inout_spec_depth) {
                    info.inout_spec_comp_ids.push_back(p.comp_id);
                    info.inout_spec_paths.push_back(it->second.name);
                }
            } else if (p.dir == "UNKNOWN") {
                if (d > info.unk_spec_depth) {
                    info.unk_spec_depth    = d;
                    info.unk_spec_comp_id  = p.comp_id;
                    info.unk_spec_path     = it->second.name;
                    info.unk_spec_comp_ids = {p.comp_id};
                    info.unk_spec_paths    = {it->second.name};
                } else if (d == info.unk_spec_depth) {
                    info.unk_spec_comp_ids.push_back(p.comp_id);
                    info.unk_spec_paths.push_back(it->second.name);
                }
            }
        }
        // INOUT fallback: secondary driver when no OUTPUT exists;
        // otherwise INOUT pins at deepest depth are added as receivers.
        if (info.drv_spec_depth < 0 && info.inout_spec_depth >= 0) {
            info.drv_spec_depth   = info.inout_spec_depth;
            info.drv_spec_comp_id = info.inout_spec_comp_id;
            info.drv_spec_path    = info.inout_spec_path;
            for (size_t i = 1; i < info.inout_spec_comp_ids.size(); ++i) {
                info.rcv_spec_comp_ids.push_back(info.inout_spec_comp_ids[i]);
                info.rcv_spec_paths.push_back(info.inout_spec_paths[i]);
            }
            if (info.rcv_spec_depth < 0 && !info.rcv_spec_comp_ids.empty())
                info.rcv_spec_depth = info.inout_spec_depth;
            std::cerr << "[HierBundler] net_id=" << net_id
                      << ": using INOUT pin as driver\n";
        } else if (info.inout_spec_depth >= 0) {
            for (size_t i = 0; i < info.inout_spec_comp_ids.size(); ++i) {
                info.rcv_spec_comp_ids.push_back(info.inout_spec_comp_ids[i]);
                info.rcv_spec_paths.push_back(info.inout_spec_paths[i]);
            }
            if (info.rcv_spec_depth < 0 && !info.rcv_spec_comp_ids.empty())
                info.rcv_spec_depth = info.inout_spec_depth;
        }
        // Multi-driven net (audit C10-02): extra same-depth OUTPUT drivers
        // used to be dropped entirely — neither driver nor receiver, so
        // their blocks never joined the route (a silent open).  Attach them
        // as additional receiver endpoints, mirroring the INOUT treatment
        // above; warn LOUD so the netlist anomaly is visible.
        if (!info.extra_drv_comp_ids.empty()) {
            for (size_t i = 0; i < info.extra_drv_comp_ids.size(); ++i) {
                info.rcv_spec_comp_ids.push_back(info.extra_drv_comp_ids[i]);
                info.rcv_spec_paths.push_back(info.extra_drv_paths[i]);
            }
            if (info.rcv_spec_depth < 0)
                info.rcv_spec_depth = info.drv_spec_depth;
            std::cerr << "[HierBundler] net_id=" << net_id << ": "
                      << (info.extra_drv_comp_ids.size() + 1)
                      << " OUTPUT drivers — extra driver blocks attached "
                      << "as endpoints\n";
        }
        // Fallback: promote deepest UNKNOWN pins to driver/receiver roles
        // when OUTPUT/INPUT/INOUT pins are absent (e.g. after import_verilog or
        // add_net … unknown).
        if (info.drv_spec_depth < 0 && info.unk_spec_depth >= 0) {
            info.drv_spec_depth   = info.unk_spec_depth;
            info.drv_spec_comp_id = info.unk_spec_comp_id;
            info.drv_spec_path    = info.unk_spec_path;
            // Remaining UNKNOWNs at the same depth become receivers.
            for (size_t i = 1; i < info.unk_spec_comp_ids.size(); ++i) {
                info.rcv_spec_comp_ids.push_back(info.unk_spec_comp_ids[i]);
                info.rcv_spec_paths.push_back(info.unk_spec_paths[i]);
            }
            if (info.rcv_spec_depth < 0 && !info.rcv_spec_comp_ids.empty())
                info.rcv_spec_depth = info.unk_spec_depth;
            ++_unk_fallback_count;
        }
        if (info.drv_spec_depth < 0 || info.rcv_spec_depth < 0) continue;
        info.is_cross = (info.drv_spec_depth != info.rcv_spec_depth);
        if (info.is_cross) {
            int min_common = INT_MAX;
            for (const auto& rp : info.rcv_spec_paths)
                min_common = std::min(min_common, path_common(info.drv_spec_path, rp));
            info.bundle_depth = (min_common == INT_MAX) ? 0 : min_common;
            // Degenerate: one endpoint is an ancestor of the other.
            for (const auto& rp : info.rcv_spec_paths) {
                if (is_prefix(info.drv_spec_path, rp) || is_prefix(rp, info.drv_spec_path)) {
                    info.is_degenerate = true; break;
                }
            }
        }
        net_leaf[net_id] = std::move(info);
    }

    // ── 2. Per-depth bundling ─────────────────────────────────────────────────
    for (int depth = 0; depth <= max_depth; ++depth) {
        auto ep_map = _endpoints_at_depth(pins_by_net, comp_by_id, depth);

        // ── 2a. Cross-level nets whose bundle_depth == depth ──────────────────
        // Use the actual leaf paths as the signature so they can't collide with
        // same-level bundles that share ancestor-level component names.
        //
        // Grouping mirrors the same-level lattice.  STRICT keeps driver+receiver
        // (byte-identical); BIDIRECTIONAL keeps the shared-endpoint-set sig
        // (byte-identical).  CONVERGENT groups by the shared RECEIVER SET so a
        // multi-driver cross-level group forms ONE fan-in bundle (per-net
        // endpoints in net_drivers/net_receivers + a FANIN reason) instead of
        // one STRICT bundle per driver.  COMBINED takes the union-find JOIN of
        // both relations.  set_bundling overrides gate each relation per net.
        {
            std::vector<int> xl_nets;
            for (const auto& [net_id, info] : net_leaf) {
                if (!info.is_cross || info.is_degenerate) continue;
                if (info.bundle_depth != depth) continue;
                xl_nets.push_back(net_id);
            }
            std::sort(xl_nets.begin(), xl_nets.end());

            auto rcv_set_sig = [](const std::vector<std::string>& sorted_rcv) {
                std::string s = "XLCONV:";
                for (const auto& r : sorted_rcv) { s += r; s += ','; }
                return s;
            };

            // As on the same-level path, ANY active set_bundling override
            // switches to the union-find join so a strict-equivalent bus stays
            // bundled (a per-net key would fragment it when a prefix disables
            // one relation for only some of its bits).
            const bool general = (_strategy == Strategy::COMBINED)
                                 || !_overrides.empty();
            std::vector<std::vector<int>> xl_groups;
            if (!general) {
                // Single-relation, override-free strategies: the historical sig
                // map (its key order is the emitted bundle order — every net
                // gets the same relation key, so no fragmentation).
                std::map<std::string, std::vector<int>> xl_sig_to_nets;
                for (int net_id : xl_nets) {
                    const auto& info = net_leaf.at(net_id);
                    auto sorted_rcv = info.rcv_spec_paths;
                    std::sort(sorted_rcv.begin(), sorted_rcv.end());
                    const std::string* nm = nullptr;
                    { auto nit = net_name.find(net_id);
                      if (nit != net_name.end()) nm = &nit->second; }
                    std::string xsig;
                    if (_strategy == Strategy::CONVERGENT &&
                        (!nm || _net_allows(*nm, "conv"))) {
                        xsig = rcv_set_sig(sorted_rcv);
                    } else if (_strategy == Strategy::BIDIRECTIONAL &&
                               (!nm || _net_allows(*nm, "bidir"))) {
                        std::vector<std::string> all = sorted_rcv;
                        all.push_back(info.drv_spec_path);
                        xsig = _bidir_sig(std::move(all));
                    } else {
                        xsig = _strict_sig(info.drv_spec_path, sorted_rcv);
                    }
                    xl_sig_to_nets[xsig].push_back(net_id);
                }
                for (auto& [sig, ids] : xl_sig_to_nets)
                    xl_groups.push_back(std::move(ids));
            } else {
                // Union-find join (COMBINED, or any strategy with an override):
                // the STRICT signature is always unioned, so strictly-identical
                // cross-level bits stay bundled regardless of per-net relation
                // permissions; conv/bidir keys merge only when the strategy
                // enables that relation AND the net permits it.  Mirrors the
                // same-level general path exactly.
                const bool strat_conv  = (_strategy == Strategy::CONVERGENT ||
                                          _strategy == Strategy::COMBINED);
                const bool strat_bidir = (_strategy == Strategy::BIDIRECTIONAL ||
                                          _strategy == Strategy::COMBINED);
                std::map<int, int> parent;
                for (int n : xl_nets) parent[n] = n;
                std::function<int(int)> find = [&](int x) {
                    while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
                    return x;
                };
                std::map<std::string, int> by_sig;
                for (int net_id : xl_nets) {
                    const auto& info = net_leaf.at(net_id);
                    auto sorted_rcv = info.rcv_spec_paths;
                    std::sort(sorted_rcv.begin(), sorted_rcv.end());
                    const std::string nname = net_name.count(net_id)
                                                  ? net_name[net_id] : std::string();
                    std::vector<std::string> sigs;
                    sigs.push_back("S" + _strict_sig(info.drv_spec_path, sorted_rcv));
                    if (strat_conv && _net_allows(nname, "conv"))
                        sigs.push_back("C" + rcv_set_sig(sorted_rcv));
                    if (strat_bidir && _net_allows(nname, "bidir")) {
                        std::vector<std::string> all = sorted_rcv;
                        all.push_back(info.drv_spec_path);
                        sigs.push_back("B" + _bidir_sig(all));
                    }
                    for (const auto& key : sigs) {
                        auto it = by_sig.find(key);
                        if (it == by_sig.end()) by_sig[key] = net_id;
                        else { int ra = find(it->second), rb = find(net_id);
                               if (ra != rb) parent[rb] = ra; }
                    }
                }
                std::map<int, std::vector<int>> by_root;
                for (int n : xl_nets) by_root[find(n)].push_back(n);
                std::vector<std::pair<std::string, std::vector<int>>> tmp;
                for (auto& [root, ids] : by_root) {
                    std::string min_name;
                    for (int nid : ids) {
                        auto it = net_name.find(nid);
                        if (it != net_name.end() &&
                            (min_name.empty() || it->second < min_name))
                            min_name = it->second;
                    }
                    tmp.emplace_back(min_name, std::move(ids));
                }
                std::sort(tmp.begin(), tmp.end(),
                          [](const auto& a, const auto& b) { return a.first < b.first; });
                for (auto& [k, ids] : tmp) xl_groups.push_back(std::move(ids));
            }

            for (auto& net_ids : xl_groups) {
                HBundle b;
                b.id    = ++next_id;
                b.level = depth;
                for (int nid : net_ids) {
                    auto it = net_name.find(nid);
                    if (it != net_name.end()) b.net_names.push_back(it->second);
                }
                std::sort(b.net_names.begin(), b.net_names.end());
                const auto& info0 = net_leaf.at(net_ids[0]);
                b.drv_spec_depth = info0.drv_spec_depth;
                b.rcv_spec_depth = info0.rcv_spec_depth;

                std::set<std::string> drv_set;
                for (int nid : net_ids)
                    drv_set.insert(net_leaf.at(nid).drv_spec_path);

                if (drv_set.size() > 1) {
                    // Cross-level FAN-IN: multiple distinct drivers share the
                    // receiver set.  Root at the shared sink, record per-net
                    // endpoints for the taper, and emit a FANIN reason so
                    // generation (and a resumed session whose net_drivers were
                    // not persisted) rebuilds the multi-driver tree.
                    std::set<std::string> rcv_union;
                    for (int nid : net_ids)
                        for (const auto& r : net_leaf.at(nid).rcv_spec_paths)
                            rcv_union.insert(r);
                    std::vector<std::string> rcv_sorted(rcv_union.begin(),
                                                        rcv_union.end());
                    const std::string root =
                        rcv_sorted.empty() ? std::string() : rcv_sorted.front();
                    std::map<std::string, int> nid_by_name;
                    for (int nid : net_ids) {
                        auto it = net_name.find(nid);
                        if (it != net_name.end()) nid_by_name[it->second] = nid;
                    }
                    for (const auto& nm : b.net_names) {
                        const auto& li = net_leaf.at(nid_by_name[nm]);
                        b.net_drivers.push_back(li.drv_spec_path);
                        b.net_receivers.push_back(li.rcv_spec_paths);
                    }
                    std::vector<std::string> leaves;
                    for (const auto& d : drv_set)
                        if (d != root) leaves.push_back(d);
                    for (const auto& r : rcv_sorted)
                        if (r != root && drv_set.find(r) == drv_set.end())
                            leaves.push_back(r);
                    std::string sig = "FANIN:" + root + "|FROM:";
                    for (const auto& l : leaves) { sig += l; sig += ','; }
                    b.reason         = sig;
                    b.drv_spec_path  = *drv_set.begin();   // compat: first driver
                    b.rcv_spec_paths = rcv_sorted;
                    b.num_terminals  = (int)drv_set.size() + (int)rcv_sorted.size();
                } else {
                    // Single-driver group: historical metadata + the group's
                    // shared signature as the reason (byte-identical for the
                    // STRICT/BIDIRECTIONAL simple-map paths).
                    auto sorted_rcv = info0.rcv_spec_paths;
                    std::sort(sorted_rcv.begin(), sorted_rcv.end());
                    const std::string* nm = nullptr;
                    { auto nit = net_name.find(net_ids[0]);
                      if (nit != net_name.end()) nm = &nit->second; }
                    if (_strategy == Strategy::BIDIRECTIONAL &&
                        (!nm || _net_allows(*nm, "bidir"))) {
                        std::vector<std::string> all = sorted_rcv;
                        all.push_back(info0.drv_spec_path);
                        b.reason = _bidir_sig(std::move(all));
                    } else {
                        b.reason = _strict_sig(info0.drv_spec_path, sorted_rcv);
                    }
                    b.drv_spec_path  = info0.drv_spec_path;
                    b.rcv_spec_paths = info0.rcv_spec_paths;
                    b.num_terminals  = 1 + (int)info0.rcv_spec_paths.size();
                }
                id_to_idx[b.id] = (int)bundles.size();
                bundles.push_back(std::move(b));
            }
        }

        // ── 2b. Same-level nets: exclude cross-level from ep_map ─────────────
        for (auto it = ep_map.begin(); it != ep_map.end(); ) {
            auto li = net_leaf.find(it->first);
            if (li != net_leaf.end() && li->second.is_cross)
                it = ep_map.erase(it);
            else
                ++it;
        }

        // Same-level nets at this depth: per-net endpoint names first.
        std::map<int, std::pair<std::string, std::vector<std::string>>> ep_names;
        for (const auto& [net_id, ep] : ep_map) {
            // Bundle each net exactly once, at its most specific projection
            // available within max_depth.  Pin propagation makes the net
            // visible at every ancestor depth it crosses; those ancestor
            // projections are views of the same physical wires, and bundling
            // them too would route the net once per depth.
            auto li = net_leaf.find(net_id);
            if (li != net_leaf.end() &&
                depth != std::min(li->second.drv_spec_depth, max_depth))
                continue;
            auto drv_it = comp_by_id.find(ep.driver_comp_id);
            if (drv_it == comp_by_id.end()) continue;
            std::vector<std::string> rcv_names;
            for (int rid : ep.receiver_comp_ids) {
                auto rit = comp_by_id.find(rid);
                if (rit != comp_by_id.end()) rcv_names.push_back(rit->second.name);
            }
            std::sort(rcv_names.begin(), rcv_names.end());
            ep_names[net_id] = {drv_it->second.name, std::move(rcv_names)};
        }

        // Grouping: the pure strategies keep the historical sorted-signature
        // map (byte-identical bundles); COMBINED — or any active override —
        // takes the union-find JOIN over the permitted relations, exactly
        // mirroring the flat _generalized_bundles (a merge via a relation
        // needs the strategy AND both nets to permit it; the strict relation
        // is always permitted).  As on the flat side, ANY active override
        // switches even a pure strategy onto the union-find path: the
        // partition is equal but bundles are ordered by smallest net name
        // instead of signature, so bundle IDs can differ.
        std::vector<std::pair<std::string, std::vector<int>>> groups;
        const bool general = (_strategy == Strategy::COMBINED) ||
                             !_overrides.empty();
        if (!general) {
            std::map<std::string, std::vector<int>> sig_to_nets;
            for (const auto& [net_id, dr] : ep_names)
                sig_to_nets[_sig(dr.first, dr.second)].push_back(net_id);
            for (auto& [sig, ids] : sig_to_nets)
                groups.emplace_back(sig, std::move(ids));
        } else {
            std::map<int, int> parent;
            for (const auto& [net_id, dr] : ep_names) parent[net_id] = net_id;
            std::function<int(int)> find = [&](int x) {
                while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
                return x;
            };
            const bool strat_conv  = (_strategy == Strategy::CONVERGENT ||
                                      _strategy == Strategy::COMBINED);
            const bool strat_bidir = (_strategy == Strategy::BIDIRECTIONAL ||
                                      _strategy == Strategy::COMBINED);
            std::map<std::string, int> by_sig;
            for (const auto& [net_id, dr] : ep_names) {
                const std::string& nname = net_name.count(net_id)
                                               ? net_name[net_id] : std::string();
                std::vector<std::string> sigs;
                sigs.push_back("S" + _strict_sig(dr.first, dr.second));
                std::string rec;
                for (const auto& r : dr.second) { rec += r; rec += ','; }
                if (strat_conv && _net_allows(nname, "conv"))
                    sigs.push_back("C REC:" + rec);
                if (strat_bidir && _net_allows(nname, "bidir")) {
                    std::vector<std::string> all = dr.second;
                    all.push_back(dr.first);
                    sigs.push_back("B" + _bidir_sig(std::move(all)));
                }
                for (const auto& key : sigs) {
                    auto it = by_sig.find(key);
                    if (it == by_sig.end()) by_sig[key] = net_id;
                    else {
                        int ra = find(it->second), rb = find(net_id);
                        if (ra != rb) parent[rb] = ra;
                    }
                }
            }
            // Deterministic order: groups sorted by their smallest net name.
            std::map<int, std::vector<int>> by_root;
            for (const auto& [net_id, dr] : ep_names)
                by_root[find(net_id)].push_back(net_id);
            std::vector<std::pair<std::string, std::vector<int>>> tmp;
            for (auto& [root, ids] : by_root) {
                std::string min_name;
                for (int nid : ids) {
                    auto it = net_name.find(nid);
                    if (it != net_name.end() &&
                        (min_name.empty() || it->second < min_name))
                        min_name = it->second;
                }
                tmp.emplace_back(min_name, std::move(ids));
            }
            std::sort(tmp.begin(), tmp.end(),
                      [](const auto& a, const auto& b) { return a.first < b.first; });
            for (auto& [k, ids] : tmp)
                groups.emplace_back(std::string(), std::move(ids));
        }

        for (const auto& [sig_hint, net_ids] : groups) {
            // Per-group endpoint survey: unique drivers/receivers in sorted
            // net-name order (deterministic), fan-in when drivers differ.
            std::vector<std::pair<std::string, int>> name_id;
            for (int nid : net_ids) {
                auto it = net_name.find(nid);
                name_id.emplace_back(it != net_name.end() ? it->second : "", nid);
            }
            std::sort(name_id.begin(), name_id.end());
            std::vector<std::string> drivers;
            std::vector<std::string> receivers;   // unique, first-seen order
            for (const auto& [nm, nid] : name_id) {
                const auto& dr = ep_names.at(nid);
                if (std::find(drivers.begin(), drivers.end(), dr.first) == drivers.end())
                    drivers.push_back(dr.first);
                for (const auto& r : dr.second)
                    if (std::find(receivers.begin(), receivers.end(), r) == receivers.end())
                        receivers.push_back(r);
            }
            // Fan-in = several drivers AND differing endpoint BLOCK SETS.
            // A mixed-direction group over ONE block set (A→B with B→A —
            // the BIDIRECTIONAL case) keeps the historical block-to-block
            // treatment: the single trunk between the same blocks serves
            // every net, no fan-in tree needed.
            bool same_set = true;
            {
                std::set<std::string> first_set;
                bool first = true;
                for (const auto& [nm, nid] : name_id) {
                    const auto& dr = ep_names.at(nid);
                    std::set<std::string> es(dr.second.begin(), dr.second.end());
                    es.insert(dr.first);
                    if (first) { first_set = std::move(es); first = false; }
                    else if (es != first_set) { same_set = false; break; }
                }
            }
            const bool fanin = drivers.size() > 1 && !same_set;

            std::string sig;
            if (fanin) {
                // Fan-in reason: root at the shared sink (the first net's
                // first receiver — CONVERGENT guarantees a shared receiver
                // set; a COMBINED chain root is a deterministic pick), the
                // drivers (+ receivers beyond the root) as leaves.  Hier
                // generation parses this form directly (FANIN:root|FROM:…).
                const std::string root =
                    ep_names.at(name_id[0].second).second.empty()
                        ? receivers[0]
                        : ep_names.at(name_id[0].second).second[0];
                sig = "FANIN:" + root + "|FROM:";
                for (const auto& d : drivers)
                    if (d != root) { sig += d; sig += ','; }
                for (const auto& r : receivers)
                    if (r != root &&
                        std::find(drivers.begin(), drivers.end(), r) == drivers.end()) {
                        sig += r; sig += ',';
                    }
            } else if (!sig_hint.empty() && sig_hint.rfind("REC:", 0) != 0) {
                sig = sig_hint;
            } else {
                // Finest signature the whole group shares.  A driverless
                // "REC:" reason (the pure-CONVERGENT map key, or a shared
                // receiver set on the union-find path) is NEVER emitted:
                // cross-block generation recovers the endpoints by parsing
                // the reason, and it cannot recover a driver from a bare
                // receiver list — the bundle would get 0 candidates.
                std::set<std::string> strict_sigs;
                for (const auto& [nm, nid] : name_id) {
                    const auto& dr = ep_names.at(nid);
                    strict_sigs.insert(_strict_sig(dr.first, dr.second));
                }
                if (strict_sigs.size() == 1) sig = *strict_sigs.begin();
                else {
                    std::vector<std::string> all = receivers;
                    all.insert(all.end(), drivers.begin(), drivers.end());
                    sig = _bidir_sig(std::move(all));
                }
            }

            HBundle b;
            b.id    = ++next_id;
            // Routing-context level: the depth of the endpoints' common
            // ancestor (same convention as cross-level bundle_depth).  A
            // cross-chip net is a depth-0 routing problem even when its
            // endpoints are specified down at leaf pins, and the planner's
            // top-down ordering keys off this level.
            b.level = depth;
            {
                auto li0 = net_leaf.find(net_ids[0]);
                if (li0 != net_leaf.end() && !li0->second.rcv_spec_paths.empty()) {
                    int mc = INT_MAX;
                    for (const auto& rp : li0->second.rcv_spec_paths)
                        mc = std::min(mc, path_common(li0->second.drv_spec_path, rp));
                    if (mc != INT_MAX) b.level = mc;
                }
            }
            b.reason = sig;
            for (int nid : net_ids) {
                auto it = net_name.find(nid);
                if (it != net_name.end()) b.net_names.push_back(it->second);
            }
            std::sort(b.net_names.begin(), b.net_names.end());

            const auto& ep0 = ep_map.at(net_ids[0]);
            b.num_terminals = 1 + (int)ep0.receiver_comp_ids.size();

            // Fan-in metadata: per-net endpoints ALIGNED with the sorted
            // net_names, so the per-bit taper can walk each net's own
            // driver→sink path at generation time.
            if (fanin) {
                for (const auto& nm : b.net_names) {
                    for (const auto& [n2, nid2] : name_id) {
                        if (n2 != nm) continue;
                        const auto& dr = ep_names.at(nid2);
                        b.net_drivers.push_back(dr.first);
                        b.net_receivers.push_back(dr.second);
                        break;
                    }
                }
            }

            // ── Cell context + busterm IDs ─────────────────────────────────
            // The all-drivers emission is reserved for fan-in bundles and
            // for multi-driver groups formed on the general (union-find)
            // path.  A PURE-mode multi-driver same-set group — a plain
            // BIDIRECTIONAL request/response pair — keeps the historical
            // ep0 path verbatim, exactly as before COMBINED existed.
            const bool multi_emit = fanin || (general && drivers.size() > 1);
            if (!multi_emit) {
                // Single-driver group (or pure-mode same-set bidir group):
                // the HISTORICAL ep0-based path, verbatim — entry/exit id
                // ORDER feeds generation, so the pure strategies stay
                // byte-identical.
                auto drv_it = comp_by_id.find(ep0.driver_comp_id);
                if (drv_it != comp_by_id.end()) {
                    int par_id = drv_it->second.parent_id;
                    bool has_parent = (par_id >= 0);
                    bool same_par = false;
                    if (has_parent) {
                        same_par = true;
                        for (int rid : ep0.receiver_comp_ids) {
                            auto rit = comp_by_id.find(rid);
                            if (rit == comp_by_id.end() || rit->second.parent_id != par_id) {
                                same_par = false; break;
                            }
                        }
                    }
                    if (same_par) {
                        // Intra-cell: all endpoints share the same parent component.
                        auto par_it = comp_by_id.find(par_id);
                        if (par_it != comp_by_id.end()) {
                            b.cell_context = par_it->second.cell;
                            b.instances.push_back(par_it->second.name);
                            b.entry_busterm_ids = {"bt:" + drv_it->second.name};
                            for (int rid : ep0.receiver_comp_ids) {
                                auto rit = comp_by_id.find(rid);
                                if (rit != comp_by_id.end())
                                    b.exit_busterm_ids.push_back("bt:" + rit->second.name);
                            }
                        }
                    } else {
                        // Cross-block bundle (or root-level endpoints): look up
                        // busterm IDs from BDB if derive_busterms was called.
                        auto drv_bt = bt_by_comp_name.find(drv_it->second.name);
                        if (drv_bt != bt_by_comp_name.end())
                            b.entry_busterm_ids = {drv_bt->second};
                        for (int rid : ep0.receiver_comp_ids) {
                            auto rit = comp_by_id.find(rid);
                            if (rit == comp_by_id.end()) continue;
                            auto rcv_bt = bt_by_comp_name.find(rit->second.name);
                            if (rcv_bt != bt_by_comp_name.end())
                                b.exit_busterm_ids.push_back(rcv_bt->second);
                        }
                    }
                }
            } else {
                // Multi-driver group (fan-in, or a mixed-direction same-set
                // group): same_par is decided over EVERY endpoint of EVERY
                // net (all drivers must share the parent for the bundle to
                // be a cell-local template); entry ids carry ALL drivers,
                // exit ids the unique receivers.
                int par_id = -2;
                bool same_par = true;
                for (int nid : net_ids) {
                    const auto& epn = ep_map.at(nid);
                    std::vector<int> comp_ids = epn.receiver_comp_ids;
                    comp_ids.push_back(epn.driver_comp_id);
                    for (int cid : comp_ids) {
                        auto cit = comp_by_id.find(cid);
                        if (cit == comp_by_id.end() || cit->second.parent_id < 0) {
                            same_par = false; break;
                        }
                        if (par_id == -2) par_id = cit->second.parent_id;
                        else if (cit->second.parent_id != par_id) {
                            same_par = false; break;
                        }
                    }
                    if (!same_par) break;
                }
                if (same_par && par_id >= 0) {
                    auto par_it = comp_by_id.find(par_id);
                    if (par_it != comp_by_id.end()) {
                        b.cell_context = par_it->second.cell;
                        b.instances.push_back(par_it->second.name);
                        for (const auto& d : drivers)
                            b.entry_busterm_ids.push_back("bt:" + d);
                        for (const auto& r : receivers)
                            b.exit_busterm_ids.push_back("bt:" + r);
                    }
                } else {
                    for (const auto& d : drivers) {
                        auto drv_bt = bt_by_comp_name.find(d);
                        if (drv_bt != bt_by_comp_name.end())
                            b.entry_busterm_ids.push_back(drv_bt->second);
                    }
                    for (const auto& r : receivers) {
                        auto rcv_bt = bt_by_comp_name.find(r);
                        if (rcv_bt != bt_by_comp_name.end())
                            b.exit_busterm_ids.push_back(rcv_bt->second);
                    }
                }
            }

            // Note: cross-depth parent linkage was removed along with
            // ancestor-level duplicate bundles — each net is bundled exactly
            // once, so there is no depth-(D-1) projection to link to.
            // parent_id/child_ids are used only by the multiple-occurrence
            // merge below (template ↔ replicas).

            id_to_idx[b.id] = (int)bundles.size();
            bundles.push_back(std::move(b));
        }
    }

    // ── 3. Multiple-occurrence merging ────────────────────────────────────────
    // Group intra-cell HBundles by (cell_context, cell-local reason).
    std::map<std::string, std::vector<int>> cell_sig_to_idxs;
    for (int i = 0; i < (int)bundles.size(); ++i) {
        const auto& b = bundles[i];
        if (b.cell_context.empty() || b.instances.empty()) continue;
        // Normalize reason to cell-local: strip "<parent>/" prefix
        std::string local = b.reason;
        const std::string prefix = b.instances[0] + "/";
        size_t pos = 0;
        while ((pos = local.find(prefix)) != std::string::npos)
            local.erase(pos, prefix.size());
        cell_sig_to_idxs[b.cell_context + "::" + local].push_back(i);
    }

    for (auto& [cell_sig, idxs] : cell_sig_to_idxs) {
        if (idxs.size() <= 1) continue;
        HBundle& tmpl = bundles[idxs[0]];
        for (size_t k = 1; k < idxs.size(); ++k) {
            HBundle& replica = bundles[idxs[k]];
            for (const auto& inst : replica.instances) tmpl.instances.push_back(inst);
            replica.parent_id = tmpl.id;
            tmpl.child_ids.push_back(replica.id);
        }
    }

    if (_unk_fallback_count > 0)
        std::cerr << "[HierBundler] " << _unk_fallback_count
                  << " net(s) used UNKNOWN-direction pins as positional "
                     "driver/receivers (set pin directions to silence).\n";

    return bundles;
}

}
