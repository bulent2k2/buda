#pragma once
#include <vector>
#include <string>
#include <map>
#include <set>
namespace buda {
struct Net {
    std::string name;
    std::string driver_pin;
    std::vector<std::string> receiver_pins;
    std::string get_driver_instance() const;
    std::set<std::string> get_receiver_instances() const;
};
struct Bundle {
    int id;
    std::vector<std::string> net_names;
    std::string reason;
    int num_terminals = 0;
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
    std::vector<Bundle> run(const Netlist& netlist);
private:
    Strategy current_strategy_;
    int depth_ = 0;
    std::string generate_signature(const Net& net) const;
};
}