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

// bindings_db.cpp — entry point for the buda_db Python extension module.
// Exposes the BDB physical design database layer as a standalone importable module.
// The routing pipeline (buda module) imports buda_db for cross-module type sharing.
#include <pybind11/pybind11.h>

namespace py = pybind11;

void bind_db(py::module_& m);

PYBIND11_MODULE(buda_db, m) {
    bind_db(m);
}
