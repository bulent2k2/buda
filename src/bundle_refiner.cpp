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

#include "bundle_refiner.h"

namespace buda {

BundleRefiner::BundleRefiner(BDB& db) : _db(db) {}

RefinementResult BundleRefiner::refine(int /*max_depth*/) { return {}; }

std::vector<std::string> BundleRefiner::refine_bundle(const std::string&) { return {}; }

bool BundleRefiner::_can_refine(const BundleRow&) const { return false; }

}  // namespace buda
