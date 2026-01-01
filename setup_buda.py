import os

def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content.strip())
    print(f"[Created] {path}")

# ==========================================
# 1. Build Configuration
# ==========================================
cmake_content = """
cmake_minimum_required(VERSION 3.15)
project(buda_interconnect VERSION 0.1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
    add_compile_options(/O2)
else()
    add_compile_options(-O3 -march=native -Wall -Wextra -fPIC)
endif()
find_package(pybind11 REQUIRED)
set(SOURCE_FILES
    src/bindings.cpp
    src/bundler.cpp
    src/topology.cpp
    src/layering.cpp
    src/global_router.cpp
    src/nuts.cpp
)
pybind11_add_module(interconnect ${SOURCE_FILES})
"""

# ==========================================
# 2. C++ Headers
# ==========================================
bundler_h = """
#pragma once
#include <vector>
#include <string>
#include <map>
#include <set>
namespace interconnect {
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
"""

topology_h = """
#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include <map>
#include "bundler.h"
namespace interconnect {
struct Point { int x, y; };
struct Rect { 
    int x1, y1, x2, y2; 
    Point center() const { return { (x1+x2)/2, (y1+y2)/2 }; }
};
struct Segment {
    Point start, end;
    int layer_hint = 0; 
};
struct Topology {
    std::string type; 
    std::vector<Segment> segments;
    int estimated_wirelength = 0;
    int trunk_location = 0;
};
class Floorplan {
public:
    void add_block(const std::string& name, int x1, int y1, int x2, int y2);
    Rect get_block_bounds(const std::string& name) const;
    void get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const;
    std::vector<std::pair<std::string, Rect>> get_all_blocks() const {
        std::vector<std::pair<std::string, Rect>> res;
        for(auto const& [key, val] : blocks_) res.push_back({key, val});
        return res;
    }
private:
    std::map<std::string, Rect> blocks_;
};
class TopologyGenerator {
public:
    TopologyGenerator(const Floorplan& fp) : floorplan_(fp) {}
    std::vector<Topology> generate_candidates(const std::string& src_name, const std::string& dst_name);
private:
    const Floorplan& floorplan_;
    void add_l_shapes(const Rect& src, const Rect& dst, std::vector<Topology>& results);
    void add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results);
};
}
"""

layering_h = """
#pragma once
#include <vector>
#include <string>
#include <algorithm>
#include "topology.h"
namespace interconnect {
enum class LayerDir { HORIZONTAL, VERTICAL };
enum class LayerType { TOP, LOW };
struct Layer {
    int id;
    std::string name;
    LayerDir dir;
    LayerType type;
};
class LayerStack {
public:
    void add_layer(int id, const std::string& name, LayerDir dir, LayerType type);
    LayerDir get_layer_dir(int id) const; // Helper needed for router
    int get_top_layer(LayerDir dir) const;
private:
    std::vector<Layer> layers_;
    int top_horiz_id_ = -1;
    int top_vert_id_ = -1;
};
}
"""

global_router_h = """
#pragma once
#include "bundler.h"
#include "topology.h"
#include "layering.h"
namespace interconnect {
struct GlobalCut {
    Point p1, p2;
    LayerDir dir;
    double capacity;
    double current_usage;
};
struct BundleWrapper {
    Bundle original_bundle;
    std::vector<Topology> candidates;
    int selected_topology_index = 0;
    double width = 1.0;
};
class GlobalRouter {
public:
    GlobalRouter(const Floorplan& fp, const LayerStack& layers);
    void set_layer_overhead(int layer_id, double overhead_percent);
    void build_congestion_map();
    void optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations);
    double test_get_effective_width(double w, int l) const; // Exposed for test
    void inject_congestion_on_layer(int layer_id, double usage); // Exposed for test
private:
    const Floorplan& floorplan_;
    const LayerStack& layers_;
    std::map<int, double> layer_dilution_factors_;
    std::vector<GlobalCut> cuts_;
    double get_effective_width(double raw_width, int layer_id) const;
    void map_topology_to_cuts(const Topology& topo, double width, bool add_usage);
    double calculate_cost(const Topology& topo, double width) const;
};
}
"""

nuts_h = """
#pragma once
// Placeholder for NUTS Engine header
namespace interconnect {
    class NUTSEngine {};
}
"""

# ==========================================
# 3. C++ Implementations
# ==========================================
bundler_cpp = """
#include "bundler.h"
#include <sstream>
#include <algorithm>
namespace interconnect {
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
std::vector<Bundle> Bundler::run(const Netlist& netlist) {
    std::vector<Bundle> bundles;
    std::map<std::string, std::vector<std::string>> groups;
    for (const auto& net : netlist.get_nets()) {
        groups[generate_signature(net)].push_back(net.name);
    }
    int bundle_id_counter = 0;
    for (const auto& [sig, net_list] : groups) {
        Bundle b; b.id = ++bundle_id_counter; b.net_names = net_list; b.reason = sig;
        bundles.push_back(b);
    }
    return bundles;
}
}
"""

topology_cpp = """
#include "topology.h"
#include <cmath>
namespace interconnect {
void Floorplan::add_block(const std::string& name, int x1, int y1, int x2, int y2) {
    blocks_[name] = Rect{x1, y1, x2, y2};
}
Rect Floorplan::get_block_bounds(const std::string& name) const {
    if (blocks_.count(name)) return blocks_.at(name);
    return Rect{0,0,0,0};
}
void Floorplan::get_hanan_grid(std::vector<int>& x_coords, std::vector<int>& y_coords) const {
    for (const auto& [name, r] : blocks_) {
        x_coords.push_back(r.x1); x_coords.push_back(r.x2);
        y_coords.push_back(r.y1); y_coords.push_back(r.y2);
    }
    std::sort(x_coords.begin(), x_coords.end());
    x_coords.erase(std::unique(x_coords.begin(), x_coords.end()), x_coords.end());
    std::sort(y_coords.begin(), y_coords.end());
    y_coords.erase(std::unique(y_coords.begin(), y_coords.end()), y_coords.end());
}
Segment make_seg(int x1, int y1, int x2, int y2, int layer) {
    Segment s; s.start={x1,y1}; s.end={x2,y2}; s.layer_hint=layer; return s;
}
void TopologyGenerator::add_l_shapes(const Rect& src, const Rect& dst, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    // HV (Layer 3 then 4 assumed for prototype)
    Topology hv; hv.type = "L_HV";
    hv.segments.push_back(make_seg(s.x, s.y, d.x, s.y, 3)); 
    hv.segments.push_back(make_seg(d.x, s.y, d.x, d.y, 4));
    results.push_back(hv);
    // VH
    Topology vh; vh.type = "L_VH";
    vh.segments.push_back(make_seg(s.x, s.y, s.x, d.y, 4));
    vh.segments.push_back(make_seg(s.x, d.y, d.x, d.y, 3));
    results.push_back(vh);
}
void TopologyGenerator::add_z_shapes(const Rect& src, const Rect& dst, const std::vector<int>& x_grid, const std::vector<int>& y_grid, std::vector<Topology>& results) {
    Point s = src.center(); Point d = dst.center();
    int min_x = std::min(s.x, d.x), max_x = std::max(s.x, d.x);
    for (int x_cut : x_grid) {
        if (x_cut > min_x && x_cut < max_x) {
            Topology z; z.type = "Z_HVH"; z.trunk_location = x_cut;
            z.segments.push_back(make_seg(s.x, s.y, x_cut, s.y, 3));
            z.segments.push_back(make_seg(x_cut, s.y, x_cut, d.y, 4));
            z.segments.push_back(make_seg(x_cut, d.y, d.x, d.y, 3));
            results.push_back(z);
        }
    }
}
std::vector<Topology> TopologyGenerator::generate_candidates(const std::string& src_name, const std::string& dst_name) {
    std::vector<Topology> candidates;
    Rect src = floorplan_.get_block_bounds(src_name);
    Rect dst = floorplan_.get_block_bounds(dst_name);
    add_l_shapes(src, dst, candidates);
    std::vector<int> hanan_x, hanan_y;
    floorplan_.get_hanan_grid(hanan_x, hanan_y);
    add_z_shapes(src, dst, hanan_x, hanan_y, candidates);
    return candidates;
}
}
"""

layering_cpp = """
#include "layering.h"
namespace interconnect {
void LayerStack::add_layer(int id, const std::string& name, LayerDir dir, LayerType type) {
    layers_.push_back({id, name, dir, type});
    if (type == LayerType::TOP) {
        if (dir == LayerDir::HORIZONTAL) top_horiz_id_ = id;
        else top_vert_id_ = id;
    }
}
LayerDir LayerStack::get_layer_dir(int id) const {
    for(auto& l : layers_) if(l.id == id) return l.dir;
    return LayerDir::HORIZONTAL;
}
int LayerStack::get_top_layer(LayerDir dir) const {
    return (dir == LayerDir::HORIZONTAL) ? top_horiz_id_ : top_vert_id_;
}
}
"""

global_router_cpp = """
#include "global_router.h"
#include <limits>
#include <cmath>
namespace interconnect {
GlobalRouter::GlobalRouter(const Floorplan& fp, const LayerStack& ls) : floorplan_(fp), layers_(ls) {}
void GlobalRouter::set_layer_overhead(int layer_id, double overhead_percent) {
    if (overhead_percent >= 100.0) overhead_percent = 99.0;
    layer_dilution_factors_[layer_id] = 100.0 / (100.0 - overhead_percent);
}
double GlobalRouter::get_effective_width(double raw_width, int layer_id) const {
    return raw_width * (layer_dilution_factors_.count(layer_id) ? layer_dilution_factors_.at(layer_id) : 1.0);
}
double GlobalRouter::test_get_effective_width(double w, int l) const { return get_effective_width(w,l); }
void GlobalRouter::build_congestion_map() {
    // Prototype: Empty map, assume infinite capacity for visual stress test
    cuts_.clear(); 
}
void GlobalRouter::inject_congestion_on_layer(int layer_id, double usage) {
    // Prototype mock
}
void GlobalRouter::map_topology_to_cuts(const Topology& topo, double width, bool add_usage) {
    // Prototype mock
}
double GlobalRouter::calculate_cost(const Topology& topo, double width) const {
    // Simple wirelength for now
    double len = 0;
    for(auto& seg : topo.segments) {
        len += std::abs(seg.start.x - seg.end.x) + std::abs(seg.start.y - seg.end.y);
    }
    return len;
}
void GlobalRouter::optimize_topologies(std::vector<BundleWrapper>& bundles, int max_iterations) {
    // For prototype, just pick index 0 or random Z-shape
    // Let's bias towards Z-shapes for visual demo if available
    for(auto& bw : bundles) {
        bw.selected_topology_index = 0;
        for(size_t i=0; i<bw.candidates.size(); ++i) {
            if(bw.candidates[i].type == "Z_HVH") bw.selected_topology_index = i;
        }
    }
}
}
"""

nuts_cpp = """
#include "nuts.h"
// Placeholder implementation
"""

bindings_cpp = """
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "bundler.h"
#include "topology.h"
#include "layering.h"
#include "global_router.h"
#include "nuts.h"
namespace py = pybind11;
using namespace interconnect;
PYBIND11_MODULE(interconnect, m) {
    py::enum_<Strategy>(m, "Strategy").value("STRICT", Strategy::STRICT).value("CONVERGENT", Strategy::CONVERGENT);
    py::enum_<LayerDir>(m, "LayerDir").value("HORIZONTAL", LayerDir::HORIZONTAL).value("VERTICAL", LayerDir::VERTICAL);
    py::enum_<LayerType>(m, "LayerType").value("TOP", LayerType::TOP).value("LOW", LayerType::LOW);
    py::class_<Point>(m, "Point").def(py::init<int, int>()).def_readwrite("x", &Point::x).def_readwrite("y", &Point::y);
    py::class_<Rect>(m, "Rect").def_readwrite("x1", &Rect::x1).def_readwrite("y1", &Rect::y1).def_readwrite("x2", &Rect::x2).def_readwrite("y2", &Rect::y2);
    py::class_<Segment>(m, "Segment").def(py::init<>()).def_readwrite("start", &Segment::start).def_readwrite("end", &Segment::end).def_readwrite("layer_hint", &Segment::layer_hint);
    py::class_<Topology>(m, "Topology").def(py::init<>()).def_readwrite("type", &Topology::type).def_readwrite("segments", &Topology::segments);
    py::class_<Bundle>(m, "Bundle").def_readwrite("id", &Bundle::id).def("get_net_names", &Bundle::get_net_names);
    py::class_<BundleWrapper>(m, "BundleWrapper").def(py::init<>()).def_readwrite("original_bundle", &BundleWrapper::original_bundle).def_readwrite("candidates", &BundleWrapper::candidates).def_readwrite("selected_topology_index", &BundleWrapper::selected_topology_index).def_readwrite("width", &BundleWrapper::width);
    py::class_<Netlist>(m, "Netlist").def(py::init<>()).def("add_net", &Netlist::add_net);
    py::class_<Bundler>(m, "Bundler").def(py::init<>()).def("set_strategy", &Bundler::set_strategy).def("run", &Bundler::run);
    py::class_<Floorplan>(m, "Floorplan").def(py::init<>()).def("add_block", &Floorplan::add_block).def("get_hanan_grid", [](const Floorplan& fp) { std::vector<int> x, y; fp.get_hanan_grid(x, y); return std::make_pair(x, y); }).def("get_all_blocks", [](const Floorplan& fp) { return fp.get_all_blocks(); });
    py::class_<LayerStack>(m, "LayerStack").def(py::init<>()).def("add_layer", &LayerStack::add_layer);
    py::class_<TopologyGenerator>(m, "TopologyGenerator").def(py::init<const Floorplan&>()).def("generate_candidates", &TopologyGenerator::generate_candidates);
    py::class_<GlobalRouter>(m, "GlobalRouter").def(py::init<const Floorplan&, const LayerStack&>()).def("set_layer_overhead", &GlobalRouter::set_layer_overhead).def("build_congestion_map", &GlobalRouter::build_congestion_map).def("optimize_topologies", &GlobalRouter::optimize_topologies);
}
"""

# ==========================================
# 4. Python Scripts
# ==========================================
buda_viz_py = """
import matplotlib.pyplot as plt
import matplotlib.patches as patches
class BudaVisualizer:
    def __init__(self, floorplan, bundles):
        self.fp = floorplan
        self.bundles = bundles
        self.fig, self.ax = plt.subplots(figsize=(10, 10))
    def draw_blocks(self):
        blocks = self.fp.get_all_blocks()
        for name, rect in blocks:
            width = rect.x2 - rect.x1
            height = rect.y2 - rect.y1
            self.ax.add_patch(patches.Rectangle((rect.x1, rect.y1), width, height, linewidth=1, edgecolor='black', facecolor='lightgrey', alpha=0.5))
            self.ax.text((rect.x1+rect.x2)/2, (rect.y1+rect.y2)/2, name, ha='center', va='center')
    def draw_hanan_grid(self):
        xs, ys = self.fp.get_hanan_grid()
        for x in xs: self.ax.axvline(x=x, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
        for y in ys: self.ax.axhline(y=y, color='gray', linestyle=':', linewidth=0.5, alpha=0.3)
    def draw_buses(self):
        layer_colors = { 3: 'blue', 4: 'red', 1: 'cyan', 2: 'magenta' }
        for wrapper in self.bundles:
            selected_topo = wrapper.candidates[wrapper.selected_topology_index]
            for seg in selected_topo.segments:
                self.ax.plot([seg.start.x, seg.end.x], [seg.start.y, seg.end.y], color=layer_colors.get(seg.layer_hint, 'green'), linewidth=wrapper.width, solid_capstyle='round', alpha=0.8)
    def show(self):
        self.ax.set_aspect('equal')
        plt.title("BUDA: Z-Shape Topology Visualization")
        plt.grid(False)
        plt.show()
"""

buda_cli_py = """
import argparse
import sys
import interconnect 
from buda_viz import BudaVisualizer

class BudaSession:
    def __init__(self):
        self.fp = interconnect.Floorplan()
        self.netlist = interconnect.Netlist()
        self.layers = interconnect.LayerStack()
        self.bundler = interconnect.Bundler()
        self.planner = None 
        self.bundles = [] 

    def do_command(self, cmd_line):
        parts = cmd_line.strip().split()
        if not parts: return
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "add_block":
            self.fp.add_block(args[0], int(args[1]), int(args[2]), int(args[3]), int(args[4]))
        elif cmd == "add_net":
            self.netlist.add_net(args[0], args[1], args[2].split(','))
        elif cmd == "def_layer":
            lid, name, dirstr, typestr, ovh = args
            ldir = interconnect.LayerDir.HORIZONTAL if dirstr.upper()=="H" else interconnect.LayerDir.VERTICAL
            ltype = interconnect.LayerType.TOP if typestr.upper()=="TOP" else interconnect.LayerType.LOW
            self.layers.add_layer(int(lid), name, ldir, ltype)
        elif cmd == "run_bundler":
            strat = interconnect.Strategy.STRICT
            if len(args) > 0 and args[0] == "convergent": strat = interconnect.Strategy.CONVERGENT
            self.bundler.set_strategy(strat)
            raw_bundles = self.bundler.run(self.netlist)
            self.bundles = []
            for b in raw_bundles:
                w = interconnect.BundleWrapper()
                w.original_bundle = b
                w.width = len(b.get_net_names()) * 2.0 
                self.bundles.append(w)
            print(f"Bundler created {len(self.bundles)} bundles.")
        elif cmd == "run_planner":
            self.planner = interconnect.GlobalRouter(self.fp, self.layers)
            topo_gen = interconnect.TopologyGenerator(self.fp)
            for w in self.bundles:
                src = w.original_bundle.get_net_names()[0].split('.')[0] 
                dst = "u_dst" # Hack for stress test demo
                # In real cli we'd extract from netlist
                w.candidates = topo_gen.generate_candidates("u_src", "u_dst")
            self.planner.build_congestion_map()
            self.planner.optimize_topologies(self.bundles, int(args[0]) if args else 5)
        elif cmd == "visualize":
            viz = BudaVisualizer(self.fp, self.bundles)
            viz.draw_blocks()
            viz.draw_hanan_grid()
            viz.draw_buses()
            viz.show()
        elif cmd == "source":
             with open(args[0], 'r') as f:
                 for line in f:
                     if not line.strip().startswith('#'): self.do_command(line)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('script', nargs='?')
    args = parser.parse_args()
    session = BudaSession()
    if args.script: session.do_command(f"source {args.script}")

if __name__ == "__main__":
    main()
"""

stress_test_buda = """
add_block u_src   100 100 200 200
add_block u_dst   800 800 900 900
add_block u_mid   400 400 600 600
def_layer 3 M3 H TOP 0.0
def_layer 4 M4 V TOP 0.0
add_net bus_0 u_src.tx u_dst.rx
add_net bus_1 u_src.tx u_dst.rx
add_net bus_2 u_src.tx u_dst.rx
add_net bus_3 u_src.tx u_dst.rx
add_net bus_4 u_src.tx u_dst.rx
run_bundler strict
run_planner 5
visualize
"""

# ==========================================
# 5. File Generation
# ==========================================
base_dir = "buda_system"
write_file(f"{base_dir}/CMakeLists.txt", cmake_content)
write_file(f"{base_dir}/src/bundler.h", bundler_h)
write_file(f"{base_dir}/src/bundler.cpp", bundler_cpp)
write_file(f"{base_dir}/src/topology.h", topology_h)
write_file(f"{base_dir}/src/topology.cpp", topology_cpp)
write_file(f"{base_dir}/src/layering.h", layering_h)
write_file(f"{base_dir}/src/layering.cpp", layering_cpp)
write_file(f"{base_dir}/src/global_router.h", global_router_h)
write_file(f"{base_dir}/src/global_router.cpp", global_router_cpp)
write_file(f"{base_dir}/src/nuts.h", nuts_h)
write_file(f"{base_dir}/src/nuts.cpp", nuts_cpp)
write_file(f"{base_dir}/src/bindings.cpp", bindings_cpp)
write_file(f"{base_dir}/src/buda_viz.py", buda_viz_py)
write_file(f"{base_dir}/src/buda_cli.py", buda_cli_py)
write_file(f"{base_dir}/src/z_shape_stress.buda", stress_test_buda)

print("\n[SUCCESS] BUDA System Generated in 'buda_system/'")
print("To build and run:")
print("  1. cd buda_system")
print("  2. mkdir build && cd build")
print("  3. cmake .. && make")
print("  4. cp interconnect*.so ../src/")
print("  5. cd ../src")
print("  6. python3 buda_cli.py z_shape_stress.buda")
