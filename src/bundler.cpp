#include "bundler.h"
#include <sstream>
#include <algorithm>
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
}
