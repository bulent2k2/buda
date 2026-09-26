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

#pragma once
#include "conn_topology.h"
#include "topology.h"
#include "layering.h"
#include "nuts.h"
#include "detailed_nuts.h"
#include <map>
#include <set>
#include <string>
#include <vector>

namespace buda {

enum class ViolationKind {
    SEG_OPEN,     // two connected segments fail to touch after placement
    BUSTERM_OPEN, // a required block has no BUSTERM conn and no pass-through segment
    BUSTERM_FACE, // a BUSTERM conn's perp position is outside the block face
    UNPLACED,     // a bit has no concrete track assignment after DetailedNUTS
    LAYER_DIR,    // a segment is assigned to a layer whose routing direction
                  // does not match the segment's orientation (unbuildable wire)
    FEEDTHRU_RELAY, // a (single-rect) block is used as a feedthrough relay: two
                  // segments connect to it (BUSTERM) but are not joined by any
                  // wire path, so they rely on the block's internal routing
    BIT_SHORT,    // two wires of DIFFERENT NETS overlap on one layer — a
                  // physical short.  Enumerated in two halves that partition
                  // the pairs, because the two were found at different times
                  // and see different data: check_dnuts takes the pairs
                  // WITHIN its bundle (two different bits, co-located on one
                  // track over an extent — the abstract same-bundle
                  // track-sharing exemption assumed "per-bit they are the
                  // same nets" and the tapered fan-in (Topology::seg_bits)
                  // makes that conditional; predicate lifted from
                  // tools/show_detailed_shorts.py), and
                  // check_dnuts_cross_shorts takes every pair of wires from
                  // two DIFFERENT bundles, which no per-bundle audit can see
                  // (issue #948).  The bundle is how the pairs are
                  // enumerated, not part of what a short is
    KEEPOUT_CROSS, // a placed wire lies ON a keepout that overlaps its span —
                  // nuts: the bus segment's physical extent [pos ± w/2]
                  // strictly overlaps the zone (the exhausted-window fallback
                  // commit, same semantics as NUTSResult::num_keepout_conflicts);
                  // dnuts: a bit's track centre is inside the zone (same
                  // predicate as DetailedNUTS's cull_keepout_crossers —
                  // defense-in-depth: the cull prevents this in production)
    ANTENNA,      // a segment is attached to the rest of the wire graph at
                  // FEWER THAN TWO points — it has at most one conn (BUSTERM
                  // tap or SEG junction), so everything beyond that single
                  // attachment is a dangling wire that terminates in nothing.
                  // Electrically inert metal (an "antenna"): it adds
                  // capacitance and can violate antenna rules, and it means
                  // the generator emitted a segment no route needs — the
                  // canonical case is an MST edge leg laid collinear on top
                  // of a trunk stub out of the same block face, whose
                  // demoted (nullopt) landing then has no inferable junction
                  // (issue #482).  Structural, so one detector serves the
                  // placed stages; generation's own knob for candidate-level
                  // dangles is `set_drop_dangling`
    TEG_OPEN,     // a `teg_mode over` multi-rect block has a rect touched by
                  // NO placed metal of the bundle.  OVER declares the rects
                  // NOT internally connected, so every rect needs external
                  // attachment (a tap, a crossing, or realized bridge metal)
                  // — but bridges are generation-only today (never planned or
                  // placed), so a selected bridged candidate routed with the
                  // bridge dropped audited CLEAN while the net was open at
                  // the un-bridged rect.  Placed-stage audit (nuts/dnuts)
                  // only: check_topo feeds generation gates and healer
                  // metrics, which must not start dropping candidates over
                  // this.  Contact is per-rect and inclusive (a face landing
                  // or an edge-lying bridge counts), the same reading the
                  // §1.3 bridge-geometry defect needs: a bridge on the union
                  // face that never touches the un-spanned rect does not
                  // discharge it.  See docs/internal/teg_multirect_status.md
                  // §1.1/§1.3, open 1(b)
    DISCONNECTED, // the topology's wire graph splits into 2+ islands: SEG
                  // junctions + same-tapped-block continuity (a through-block
                  // joint is either a declared feedthru or flagged separately
                  // as FEEDTHRU_RELAY) leave some segments unreachable from
                  // the rest — the net cannot be electrically complete even
                  // though every block is tapped and every junction touches
                  // (e.g. a TopoEdit session that removed the only bridging
                  // stub and committed with comps=2)
    OFF_GRID,     // a placed bit-wire (or shield) whose METAL is not on the
                  // SIGNAL slots of its layer's track pattern in force at its
                  // along-midpoint: the extent [pos - w/2, pos + w/2] must
                  // begin at the low edge of one SIGNAL slot and end at the
                  // high edge of one, with every slot between them SIGNAL
                  // (no rail inside the wire).  A plain bit is a run of one;
                  // an NDR bit of width_slots k is a run of k, its centre
                  // between slot centres by construction — so the rule is
                  // the run's EDGES, not the centre (the judge's centre rule
                  // flags every even-k NDR wire, #954); whether the run has
                  // the RIGHT k is NDR_WIDTH's question, not this one.
                  // Unbuildable in the same way LAYER_DIR is: metal the
                  // technology has no track for.  dnuts only (abstract NUTS
                  // positions are not track-snapped), and only when
                  // check_dnuts is handed the routing grid (issue #947 — the
                  // gap that let #946's off-grid copies audit clean)
};

struct ConnViolation {
    ViolationKind kind;
    int           bundle_id  = -1;
    int           seg_idx    = -1;
    int           seg_idx2   = -1;  // for SEG_OPEN: the other segment
    int           bit_index  = -1;  // -1 for topo/nuts; bit position for dnuts
    // The OTHER wire of a two-wire violation (BIT_SHORT).  bundle_id2 is set
    // only when that wire belongs to a different bundle (a cross-bundle
    // short, check_dnuts_cross_shorts) and is -1 when the pair is inside
    // bundle_id; seg_idx2 is then the other wire's segment IN bundle_id2.
    // bit_index2 is the other wire's bit (an NDR shield's negative ordinal
    // included), -1 where there is no second wire.
    int           bundle_id2 = -1;
    int           bit_index2 = -1;
    std::string   block_name;       // for BUSTERM_OPEN / BUSTERM_FACE
    std::string   message;
};

// Report-only census of a `teg_mode thru` multi-rect block's rects left to
// the block's INTERNAL routing (touched by no placed metal of the bundle) —
// teg_multirect_status.md open 5.  THRU means "internally connected", so an
// untouched rect is NOT a violation: it is the declared meaning.  But when
// that assumption is wrong there is nothing to discover it by short of
// reading the topology dump, so the placed-stage audit says which rects the
// route relies on the block to join — computed by the SAME contact scan
// (`teg_touches`) the OVER audit's TEG_OPEN verdict uses, so the two
// readings of "touched" cannot drift.  Surfaced by the CLI as an INFO
// diagnostic (BUDA-1907), verdict-memoized so repeats stay quiet.
struct TegThruCensusEntry {
    int         bundle_id = -1;
    std::string block_name;
    int         n_rects     = 0;   // rects the block declares
    int         n_untouched = 0;   // rects no placed metal of the bundle touches
    std::string detail;            // "rect#i (x1,y1)-(x2,y2), ..." — the untouched ones
};

struct ConnResult {
    std::vector<ConnViolation> violations;
    // Placed-stage THRU-block census (see TegThruCensusEntry) — report-only,
    // never part of the ok() verdict.
    std::vector<TegThruCensusEntry> thru_census;
    bool ok() const { return violations.empty(); }
};

// Topology-level check (nominal perp_pos from ConnTopology).
// Checks SEG connection continuity, BUSTERM face validity, and block coverage
// (including pass-through blocks that have no explicit BUSTERM endpoint).
ConnResult check_topo(const ConnTopology& ct, const Topology& topo,
                      const Floorplan& fp, int bundle_id);

// Declared-feedthru scoping for generation's DISCONNECTED gate
// (TopologyGenerator::filter_uncovered).  True iff the wire graph splits into
// 2+ islands under detect_disconnected's island model (SEG junctions +
// same-block tap continuity — the SAME union-find, shared implementation) and
// EVERY island touches one of topo.feedthru_blocks (a BUSTERM conn to it, or
// inclusive geometric overlap with its rects — a stub landing in the split
// gap): each island then reaches the declared block, whose internal routing
// is the declared bridge.  An island touching NO declared feedthru block is a
// genuine open the exemption must not cover (Codex P2 on #335).  False when
// no feedthru is declared or the graph is not split.
bool disconnected_islands_bridged(const ConnTopology& ct, const Topology& topo,
                                  const Floorplan& fp);

// How `cs` is attached to the rest of the route — the ANTENNA predicate's
// working set, exposed so the generator's own gates test the SAME property the
// checker reports instead of re-deriving it (the #483 review found a
// hand-rolled conn-RECORD count drifting from this; issue #485 found a second
// copy in the seed-trunk gate).  `count() < 2` IS the antenna condition.
struct SegAttachment {
    std::set<int>         positions;  // distinct busterm face coords + junction at_pos
    std::set<std::string> through;    // connected blocks the segment merely crosses
    int n_busterm = 0, n_seg = 0;     // raw record counts, for reporting only
    int count() const { return (int)(positions.size() + through.size()); }
};
SegAttachment seg_attachment(const ConnSeg& cs, const Topology& topo,
                             const Floorplan& fp);

// NUTS-level check: same topology structure but positions from TrackSegments.
// Includes block-coverage check for pass-through blocks at placed positions,
// layer-direction validity (H segment on H layer, V on V), and the
// KEEPOUT_CROSS audit.
//
// zone_fp: the floorplan whose keepout zones the NUTS engine actually placed
// against — in the hier flow `fp` may be a bundle's cell-local generation
// floorplan (right coordinate/name space for the busterm-face checks) whose
// zone list is EMPTY, while placements are global; testing keepouts against
// it would silently pass a segment the engine itself counted as a conflict.
// nullptr = use fp (the flat flow, where they are the same object).
ConnResult check_nuts(const ConnTopology& ct, const NUTSResult& nuts,
                      const Topology& topo, const Floorplan& fp,
                      const LayerStack& layers, int bundle_id,
                      const Floorplan* zone_fp = nullptr);

// Detailed-NUTS-level: per-bit connectivity check using NetSegment positions.
// Includes block-coverage check for pass-through blocks at placed positions,
// layer-direction validity (H segment on H layer, V on V), and the
// KEEPOUT_CROSS audit (zone_fp as in check_nuts).
//
// grid: the routing grid the bits were placed on; when given, every placed
// bit-wire and shield is also audited OFF_GRID (see ViolationKind).
// nullptr = no on-grid audit (the historical behaviour, and what every
// caller that is not `check_design` still gets).
ConnResult check_dnuts(const ConnTopology& ct, const DetailedNUTSResult& dnuts,
                       const Topology& topo, const Floorplan& fp,
                       const LayerStack& layers, int bundle_id, int num_bits,
                       const Floorplan* zone_fp = nullptr,
                       const RoutingGridStack* grid = nullptr);

// BIT_SHORT across bundles (issue #948): every pair of placed wires from two
// DIFFERENT bundles whose metal overlaps on one layer while they carry
// different nets.  check_dnuts is per bundle — it is handed one bundle id
// and filters the result to it — so a short between two bundles was outside
// every audit: a flow could end with two nets' metal on one track and a
// clean check_design.  This is the other half of BIT_SHORT, run ONCE per
// design; it takes no same-bundle pair, so the two halves never report one
// short twice.
//
// The metal is the rectangle persistence writes for the wire and
// tools/independent_audit.py reads back: the span along the LAYER's
// direction, [track_position +- width/2] across it.  Both wires of a pair
// are on one layer, so the test is two intervals in layer-local
// coordinates and no orientation enters into it.  A pair shorts when both
// intervals overlap by more than 1e-6 — a positive AREA, as the judge
// counts it: two wires that only abut (side by side at zero spacing, or
// end to end) share a boundary and no metal.
//
// Net identity: `bit_nets[bundle][bit]` names a signal bit's net and
// `shield_nets[bundle]` the net an NDR shield wire of that bundle carries —
// the names persistence stores, so the judge and this audit key on the same
// identity.  Two wires with one name are one net and never short, whichever
// bundles carry them.  A wire whose name cannot be resolved (no entry, an
// empty name, a bit outside the list) is NOT folded onto a shared name — it
// is its own net, identified by (bundle, bit), because identity is what a
// short is about and collapsing unknowns onto one would hide exactly the
// fault this looks for.  Unplaced rows are skipped.  Violations come back
// sorted, one per shorted PAIR, oriented so (bundle_id, seg_idx,
// bit_index) is the smaller wire; the kind is BIT_SHORT.
ConnResult check_dnuts_cross_shorts(
    const DetailedNUTSResult& dnuts,
    const std::map<int, std::vector<std::string>>& bit_nets,
    const std::map<int, std::string>& shield_nets);

// The on-grid predicate itself, on one wire: does the metal extent
// [lo, hi] (perpendicular axis) coincide with the outer edges of a run of
// one or more consecutive SIGNAL slots of `pat`?  Exposed so the rule has
// ONE statement a test can drive directly.
bool metal_on_signal_run(const TrackPattern& pat, double lo, double hi);

} // namespace buda
