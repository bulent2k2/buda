// bindings.cpp — pybind11 module entry point.
// Delegates all class registrations to per-domain bind_*() functions so that
// each translation unit includes only the headers it needs, keeping
// pybind11 template instantiation minimal per compilation unit.
#include <pybind11/pybind11.h>
#include <pybind11/iostream.h>

namespace py = pybind11;

void bind_db(py::module_& m);
void bind_bundler(py::module_& m);
void bind_routing(py::module_& m);
void bind_nuts(py::module_& m);

PYBIND11_MODULE(buda, m) {
    py::add_ostream_redirect(m, "ostream_redirect");

    // Registration order matters: each domain may reference types from earlier ones.
    bind_db(m);       // BDB, row types, BustermGen
    bind_bundler(m);  // Strategy, HBundle, BundleWrapper, Bundler, HierarchicalBundler
    bind_routing(m);  // geometry, Floorplan, LayerStack, TopologyGenerator, CongestionPlanner
    bind_nuts(m);     // NUTS, DetailedNUTS, RoutingGrid, ConnTopology, verify
}
