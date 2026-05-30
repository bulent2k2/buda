#include "busterm.h"

namespace buda {

BustermGen::BustermGen(BDB& db) : _db(db) {}

void BustermGen::derive(int /*max_depth*/) {}
void BustermGen::refine()                  {}
std::vector<Busterm> BustermGen::all() const { return {}; }

Busterm BustermGen::_from_component(const ComponentRow& comp, int depth) const {
    Busterm bt;
    bt.hier_path  = comp.name;
    bt.depth      = depth;
    bt.x1 = comp.x1; bt.y1 = comp.y1;
    bt.x2 = comp.x2; bt.y2 = comp.y2;
    bt.resolution = comp.is_leaf ? BustermResolution::SPATIAL_CLUSTER
                                 : BustermResolution::BLOCK;
    return bt;
}

}  // namespace buda
