// bindings_db.cpp — entry point for the buda_db Python extension module.
// Exposes the BDB physical design database layer as a standalone importable module.
// The routing pipeline (buda module) imports buda_db for cross-module type sharing.
#include <pybind11/pybind11.h>

namespace py = pybind11;

void bind_db(py::module_& m);

PYBIND11_MODULE(buda_db, m) {
    bind_db(m);
}
