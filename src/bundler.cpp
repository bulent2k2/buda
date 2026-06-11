#include "bundler.h"
#include <sstream>
#include <algorithm>
#include <climits>
#include <iostream>
namespace buda {
std::string extract_instance(const std::string& pin) {
    size_t last_dot = pin.find_last_of('.');
    if (last_dot == std::string::npos) return "top";
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
    std::stringstream signature;
    std::set<std::string> recv_insts = net.get_receiver_instances();
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

std::unordered_map<int, HierarchicalBundler::NetEndpoints>
HierarchicalBundler::_endpoints_at_depth(
        const std::unordered_map<int, std::vector<PinRow>>& pins_by_net,
        const std::unordered_map<int, ComponentRow>& comp_by_id,
        int depth) const {
    std::unordered_map<int, NetEndpoints> result;
    for (const auto& [net_id, pins] : pins_by_net) {
        NetEndpoints ep;
        std::vector<int> unknown_comp_ids;
        for (const auto& p : pins) {
            auto it = comp_by_id.find(p.comp_id);
            if (it == comp_by_id.end() || it->second.depth != depth) continue;
            if (p.dir == "OUTPUT" && ep.driver_comp_id < 0)
                ep.driver_comp_id = p.comp_id;
            else if (p.dir == "INPUT")
                ep.receiver_comp_ids.push_back(p.comp_id);
            else if (p.dir == "UNKNOWN")
                unknown_comp_ids.push_back(p.comp_id);
        }
        // Fallback: use UNKNOWN pins positionally when OUTPUT/INPUT are absent.
        if (ep.driver_comp_id < 0 && !unknown_comp_ids.empty()) {
            ep.driver_comp_id = unknown_comp_ids[0];
            for (size_t i = 1; i < unknown_comp_ids.size(); ++i)
                ep.receiver_comp_ids.push_back(unknown_comp_ids[i]);
            std::cerr << "[HierBundler] net_id=" << net_id
                      << " at depth " << depth
                      << ": using UNKNOWN-direction pins as positional driver/receivers\n";
        }
        if (ep.driver_comp_id >= 0 && !ep.receiver_comp_ids.empty())
            result[net_id] = std::move(ep);
    }
    return result;
}

std::vector<HBundle> HierarchicalBundler::run(int max_depth) {
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

    std::vector<HBundle> bundles;
    std::unordered_map<int, int> id_to_idx;   // bundle id → index in bundles
    int next_id = 0;

    // Depth-0 bundle index for parent linkage: sig → bundle id
    std::unordered_map<std::string, int> depth0_by_sig;

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
                    info.drv_spec_depth   = d;
                    info.drv_spec_comp_id = p.comp_id;
                    info.drv_spec_path    = it->second.name;
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
        // Fallback: promote deepest UNKNOWN pins to driver/receiver roles
        // when OUTPUT/INPUT pins are absent (e.g. after import_verilog or
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
            std::cerr << "[HierBundler] net_id=" << net_id
                      << ": using UNKNOWN-direction pins as positional driver/receivers\n";
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
        {
            std::map<std::string, std::vector<int>> xl_sig_to_nets;
            for (const auto& [net_id, info] : net_leaf) {
                if (!info.is_cross || info.is_degenerate) continue;
                if (info.bundle_depth != depth) continue;
                auto sorted_rcv = info.rcv_spec_paths;
                std::sort(sorted_rcv.begin(), sorted_rcv.end());
                xl_sig_to_nets[_strict_sig(info.drv_spec_path, sorted_rcv)].push_back(net_id);
            }
            for (const auto& [sig, net_ids] : xl_sig_to_nets) {
                HBundle b;
                b.id    = ++next_id;
                b.level = depth;
                b.reason = sig;
                for (int nid : net_ids) {
                    auto it = net_name.find(nid);
                    if (it != net_name.end()) b.net_names.push_back(it->second);
                }
                std::sort(b.net_names.begin(), b.net_names.end());
                const auto& info0 = net_leaf.at(net_ids[0]);
                b.num_terminals  = 1 + (int)info0.rcv_spec_paths.size();
                b.drv_spec_depth = info0.drv_spec_depth;
                b.rcv_spec_depth = info0.rcv_spec_depth;
                b.drv_spec_path  = info0.drv_spec_path;
                b.rcv_spec_paths = info0.rcv_spec_paths;
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

        // Group same-level nets by STRICT signature at this depth.
        std::map<std::string, std::vector<int>> sig_to_nets;
        for (const auto& [net_id, ep] : ep_map) {
            auto drv_it = comp_by_id.find(ep.driver_comp_id);
            if (drv_it == comp_by_id.end()) continue;
            std::vector<std::string> rcv_names;
            for (int rid : ep.receiver_comp_ids) {
                auto rit = comp_by_id.find(rid);
                if (rit != comp_by_id.end()) rcv_names.push_back(rit->second.name);
            }
            std::sort(rcv_names.begin(), rcv_names.end());
            sig_to_nets[_strict_sig(drv_it->second.name, rcv_names)].push_back(net_id);
        }

        for (const auto& [sig, net_ids] : sig_to_nets) {
            HBundle b;
            b.id    = ++next_id;
            b.level = depth;
            b.reason = sig;
            for (int nid : net_ids) {
                auto it = net_name.find(nid);
                if (it != net_name.end()) b.net_names.push_back(it->second);
            }
            std::sort(b.net_names.begin(), b.net_names.end());

            const auto& ep0 = ep_map.at(net_ids[0]);
            b.num_terminals = 1 + (int)ep0.receiver_comp_ids.size();

            // ── Cell context: set when all endpoints share the same parent ──
            auto drv_it = comp_by_id.find(ep0.driver_comp_id);
            if (drv_it != comp_by_id.end() && drv_it->second.parent_id >= 0) {
                int par_id = drv_it->second.parent_id;
                bool same_par = true;
                for (int rid : ep0.receiver_comp_ids) {
                    auto rit = comp_by_id.find(rid);
                    if (rit == comp_by_id.end() || rit->second.parent_id != par_id) {
                        same_par = false; break;
                    }
                }
                if (same_par) {
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
                }
            }

            // ── Depth linkage: cross-block depth-D bundles link to depth-(D-1) parents ──
            if (depth > 0 && b.cell_context.empty()) {
                auto drv2 = comp_by_id.find(ep0.driver_comp_id);
                if (drv2 != comp_by_id.end() && drv2->second.parent_id >= 0) {
                    auto par_drv = comp_by_id.find(drv2->second.parent_id);
                    if (par_drv != comp_by_id.end()) {
                        std::vector<std::string> par_rcv_names;
                        for (int rid : ep0.receiver_comp_ids) {
                            auto rit = comp_by_id.find(rid);
                            if (rit != comp_by_id.end() && rit->second.parent_id >= 0) {
                                auto prv = comp_by_id.find(rit->second.parent_id);
                                if (prv != comp_by_id.end())
                                    par_rcv_names.push_back(prv->second.name);
                            }
                        }
                        std::sort(par_rcv_names.begin(), par_rcv_names.end());
                        std::string d0_sig = _strict_sig(par_drv->second.name, par_rcv_names);
                        auto it = depth0_by_sig.find(d0_sig);
                        if (it != depth0_by_sig.end()) {
                            b.parent_id = it->second;
                            auto pidx = id_to_idx.find(it->second);
                            if (pidx != id_to_idx.end())
                                bundles[pidx->second].child_ids.push_back(b.id);
                        }
                    }
                }
            }

            if (depth == 0) depth0_by_sig[sig] = b.id;

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

    return bundles;
}

}
