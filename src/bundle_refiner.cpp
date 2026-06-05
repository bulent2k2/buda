#include "bundle_refiner.h"

namespace buda {

BundleRefiner::BundleRefiner(BDB& db) : _db(db) {}

RefinementResult BundleRefiner::refine(int /*max_depth*/) { return {}; }

std::vector<std::string> BundleRefiner::refine_bundle(const std::string&) { return {}; }

bool BundleRefiner::_can_refine(const BundleRow&) const { return false; }

}  // namespace buda
