// Copyright 2026 Ben Bulent Basaran
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#pragma once
// Opaque std::vector<BundleWrapper> (rnr runtime P2 —
// docs/internal/rnr_runtime_parallelism.md): a bound `BundleWrapperVec`
// passes by REFERENCE across every engine call, ending the per-call deep
// copy of all wrappers + candidate pools that pybind's default sequence
// caster performs (directly measured: ~6.7 ms per NUTSEngine.run crossing,
// ~9 ms per make_bus_segments, on the mix2 rnr flows — per healer TRIAL).
//
// MUST be included FIRST (before pybind11/stl.h can instantiate the default
// caster for this type) in EVERY binding TU that binds a function with a
// vector<BundleWrapper> parameter — a TU missing it silently falls back to
// the copying caster.  Today that is bind_routing.cpp (CongestionPlanner +
// the BundleWrapperVec registration) and bind_nuts.cpp (NUTSEngine,
// make_bus_segments).
//
// Every affected binding keeps a py::sequence FALLBACK overload (defined
// after the native one, so a BundleWrapperVec always takes the zero-copy
// path): plain Python lists of wrappers — hand-built test fixtures, tools,
// resumed sessions — keep today's copy semantics unchanged.
// `wrappers_from_seq` is that fallback's converter.
#include <pybind11/pybind11.h>

#include "congestion_planner.h"   // BundleWrapper

PYBIND11_MAKE_OPAQUE(std::vector<buda::BundleWrapper>);

inline std::vector<buda::BundleWrapper>
wrappers_from_seq(pybind11::sequence seq) {
    std::vector<buda::BundleWrapper> v;
    v.reserve(pybind11::len(seq));
    for (auto item : seq)
        v.push_back(item.cast<buda::BundleWrapper&>());
    return v;
}
