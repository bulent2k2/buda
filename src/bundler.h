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
}