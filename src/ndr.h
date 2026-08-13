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
#include <algorithm>
#include <cctype>
#include <cmath>
#include <map>
#include <string>
#include <vector>

namespace buda {

// ── Non-default rule (NDR), phase 1 ──────────────────────────────────────
// Path A of docs/internal/ndr_architecture.md: SLOT-QUANTIZED consumption.
// A rule is resolved per net (longest-prefix scope) session-side; after the
// bundler's rule-class split every governed bundle is RULE-UNIFORM (the R8
// fallback position, chosen for phase 1), so one spec rides the whole
// bundle: BundleInput::ndr (planner charging + abstract width) and
// BusSegment::ndr (detailed placement) carry copies of the same resolved
// spec.  An inactive spec (the default) is byte-identical everywhere — every
// consumer short-circuits on !active(), which is the R12 guarantee.
// ── R1 per-layer declared values ─────────────────────────────────────────
// One layer's override of a rule's width/spacing.  Either form may be given
// independently, and either may be absent (inherit the rule's own value):
// an absolute distance in layout units, or a multiplier already quantized to
// a slot count (a multiplier is pattern-independent, so there is nothing left
// to resolve later).  The sentinels differ because 0 means different things:
// no width_abs is 0 distance, but 0 guard slots is a real declaration.
struct NdrLayerRule {
    double width_abs   = 0.0;   // 0 = not declared here
    double spacing_abs = 0.0;   // 0 = not declared here
    int    width_slots = 0;     // multiplier form, quantized; 0 = not declared
    int    guard_slots = -1;    // multiplier form, quantized; -1 = not declared
};

// ── The signal-slot geometry of ONE layer's track pattern ────────────────
// What metal-shaped quantization needs and a single scalar pitch cannot
// supply.  A k-slot wire is k CONSECUTIVE signal slots, and it may not span
// a power rail — so the period is modelled as maximal CONTIGUOUS runs of
// signal slots, not one flat list.  Each entry of a run is that slot's
// (width, gap to the next signal slot IN THE SAME run).  The last entry's
// gap is unused.
//
// Cached onto LayerStack beside `bit_pitch` (the same push-model
// `set_bit_pitch` uses), so ndr.h stays free of any routing_grid.h
// dependency and the planner's hot loop keeps its scalar.
struct NdrLayerGeom {
    std::vector<std::vector<std::pair<double, double>>> runs;
    bool empty() const { return runs.empty(); }
};

struct NdrSpec {
    // Whole SIGNAL slots consumed per bit — the path-A quantization of the
    // declared width (a 1.5x wire pays 2 slots: conservative, never
    // illegal).  1 = default width.
    int width_slots = 1;
    // Empty slots kept on each UNSHIELDED gap around a bit (interior gaps
    // between adjacent bits and the run's two ends) — the quantized
    // clearance (spacing x2 = 1 guard slot at default-pitch patterns).
    // 0 = default spacing.  A shielded gap carries the shield INSTEAD of
    // guards: the grounded wire both occupies the adjacent slot and
    // satisfies the clearance intent (the documented phase-1 model).
    int guard_slots = 0;
    // Shield arrangement: 0 = none, 1 = flank-the-bus (one shield at each
    // end of the run), 2 = flank-every-bit (a shield in every gap incl.
    // both ends), 3 = per-N (a shield after every shield_per_n bits, plus
    // both ends).
    int shield_mode  = 0;
    int shield_per_n = 0;
    // Net identity of emitted shield wires (R5a/R9: credit and audit key on
    // this; phase 1 always EMITS — crediting is phase 2).
    std::string shield_net = "GND";
    // R5a crediting (phase 2, OPT-IN — the `credit` token): an END shield
    // may be satisfied by an adjacent pattern rail that is electrically
    // identical to shield_net (ndr_shield_net_matches) instead of an
    // emitted wire.  Off = phase-1 behavior byte-for-byte.
    bool credit_shields = false;
    // R6 shield BONDING (opt-in — the `bond` token): strap each EMITTED
    // shield wire to the power grid with a via where it crosses an
    // identity-matching rail on a perpendicular ADJACENT layer.  Without
    // it a shield is labeled metal that reserves its track but is not
    // connected to anything — see docs/internal/opens_ndr.md §1.  A
    // CREDITED end needs no bonding: the rail IS grid metal.
    //
    // The value is a STRIDE, not a flag: 0 = no bonding, 1 = every
    // crossing (the `bond` default), N = every Nth crossing along the
    // shield.  A stride is what a real grid wants — every crossing is
    // hundreds of vias on a long shield over a dense grid — and one field
    // rather than a flag-plus-count keeps the two from disagreeing.
    int bond_stride = 0;
    // R1 ABSOLUTE values, in LAYOUT UNITS (0 = not declared, use the
    // multiplier quantization above).  A multiplier is PATTERN-INDEPENDENT
    // — `x2` is two signal slots on every layer — while an absolute value
    // names one PHYSICAL width whose slot cost depends on the layer's
    // pitch, which is exactly why R1 asks for the form.
    //
    // width_slots/guard_slots above stay the AUTHORITATIVE quantization
    // and, for an absolute rule, hold the CONSERVATIVE resolution: the
    // largest slot count over the layers the rule may use.  Per-layer
    // resolution (ndr_resolve_for_pitch) can then only REDUCE them, so a
    // consumer that forgets to resolve OVER-charges rather than under-
    // charges — the same direction R5a takes when the planner prices the
    // uncredited worst case and DNUTS credits at the seat.
    double width_abs   = 0.0;
    double spacing_abs = 0.0;
    // R1 METAL-shaped quantization (opt-in — the `metal` token).  Default
    // false keeps the CHANNEL reading: an absolute value is quantized by
    // `unit_pitch / n_signal_slots`, which answers how much routing channel
    // the width consumes.  True asks instead how much METAL a run of slots
    // delivers, which is what a width declared for EM, resistance or RC
    // means.
    //
    // Opt-in because the honest answer is STRICTER, not merely different: a
    // layer whose signal slots are isolated between rails can deliver only
    // one slot's metal, so a width the channel reading accepted (by silently
    // delivering half of it) becomes an R3 refusal.  That is correct and it
    // rejects designs that route today, so it follows the same opt-in +
    // measured-default-flip path every other semantic change here took.
    bool metal_quant = false;
    // R1 PER-LAYER declared values (`def_ndr_layer`), keyed by layer id.
    // R1 always asked for "per-layer or layer-independent values" — the
    // industry precedent it cites, LEF/DEF NONDEFAULTRULE, is per-layer —
    // and phase 1 shipped only the layer-independent half.  The values a
    // physical rule is written against genuinely differ per layer: EM
    // limits, sheet resistance and RC all do, so one absolute width applied
    // to every layer is over-wide on top or under-wide at the bottom.
    //
    // An entry OVERRIDES the rule's own value on that layer alone; layers
    // with no entry inherit.  Empty = a layer-independent rule, which is
    // byte-identical to the pre-per-layer behaviour.
    std::map<int, NdrLayerRule> per_layer;
    // Declared rule name (reporting/provenance).
    std::string rule_name;

    bool active() const {
        return width_slots > 1 || guard_slots > 0 || shield_mode != 0;
    }
};

// ── R1 per-layer resolution of an ABSOLUTE rule ──────────────────────────
// Quantize the declared absolute width/spacing against ONE layer's
// per-signal-slot pitch (LayerStack::bit_pitch — the same per-bit channel
// cost eff_bus_width charges, so a rule and the width model agree by
// construction).  Rounds UP: a value between slot counts pays the larger,
// the convention the multiplier form already uses (`x1.5` pays 2 slots —
// conservative, never illegal).  The epsilon keeps a value landing EXACTLY
// on a boundary from paying an extra slot to floating-point error.
//
// IDENTITY for a rule with no absolute values, and for a non-positive
// pitch (an unpatterned layer has no slot cost to divide by) — so every
// multiplier rule, and every ungoverned segment, is byte-identical.
inline NdrSpec ndr_resolve_for_pitch(const NdrSpec& s, double slot_pitch) {
    if ((s.width_abs <= 0.0 && s.spacing_abs <= 0.0) || slot_pitch <= 0.0)
        return s;
    const double eps = 1e-9;
    NdrSpec o = s;
    if (s.width_abs > 0.0)
        o.width_slots = std::max(1,
            (int)std::ceil(s.width_abs / slot_pitch - eps));
    if (s.spacing_abs > 0.0)
        o.guard_slots = std::max(0,
            (int)std::ceil(s.spacing_abs / slot_pitch - eps) - 1);
    return o;
}

// ── R1 METAL-shaped quantization ─────────────────────────────────────────
// `ndr_resolve_for_pitch` above answers "how much routing CHANNEL does this
// width consume" — the layer's whole period amortized over its signal slots,
// rails and gaps included.  That is the right question for the planner's
// books and the wrong one for a width declared for a PHYSICAL reason: EM
// current density, sheet resistance, RC.  Those care about the metal, and
// the metal a k-slot bit gets is a different quantity that shares no term
// with the channel cost:
//
//     metal(k) = sum of the k slot widths + the k-1 gaps BETWEEN them
//
// so a declaration of 8 could buy 2 units of wire (opens_ndr.md §2, and
// flow/ndr_abs_divisor.buda measured exactly that).  These functions ask
// the metal question instead.
//
// CHARGING is unaffected and must stay so: a resolved spec still reports a
// SLOT COUNT, and every consumer still charges slots x bit_pitch.  Only the
// declared-value -> slot-count conversion moves, so the planner's books keep
// agreeing with `eff_bus_width` by construction (the no-double-count
// property).  Metal-shaped simply asks for MORE slots than channel-shaped
// wherever the two differ.

// Metal delivered by the NARROWEST window of `k` consecutive signal slots
// anywhere in the period, or -1 when no run is that long (the wire would
// have to span a power rail, which is not a legal signal wire — R3).
//
// The narrowest window is the honest answer whenever the gaps vary: the
// seat is chosen by the placer, not by the rule, so a rule is only
// guaranteed met if it is met at the WORST window.  Measured: 20 of 345
// declared patterns have non-uniform signal gaps, and in each the slot
// widths agree while the trailing gaps differ — exactly the case a single
// `k*w + (k-1)*sp` formula gets wrong.
inline double ndr_metal_for_slots(const NdrLayerGeom& g, int k) {
    if (k <= 0) return 0.0;
    double best = -1.0;
    for (const auto& run : g.runs) {
        if ((int)run.size() < k) continue;
        for (int i = 0; i + k <= (int)run.size(); ++i) {
            double m = 0.0;
            for (int j = 0; j < k; ++j) {
                m += run[i + j].first;                 // the slot's metal
                if (j + 1 < k) m += run[i + j].second; // the gap it bridges
            }
            if (best < 0.0 || m < best) best = m;
        }
    }
    return best;
}

// Clearance delivered by `gu` guard slots between two governed bits: the
// guards' own widths plus the gu+1 gaps around them.  Same narrowest-window
// reading, and the same -1 when the period cannot host that many.
inline double ndr_clearance_for_guards(const NdrLayerGeom& g, int gu) {
    if (gu < 0) return -1.0;
    double best = -1.0;
    for (const auto& run : g.runs) {
        // gu guards sit BETWEEN two bits, so the window is gu+2 slots wide.
        const int need = gu + 2;
        if ((int)run.size() < need) continue;
        for (int i = 0; i + need <= (int)run.size(); ++i) {
            double c = 0.0;
            for (int j = 0; j < gu; ++j) c += run[i + 1 + j].first;
            for (int j = 0; j <= gu; ++j) c += run[i + j].second;
            if (best < 0.0 || c < best) best = c;
        }
    }
    return best;
}

// The largest k any run in the period can host — the realizability ceiling.
inline int ndr_max_slots(const NdrLayerGeom& g) {
    int m = 0;
    for (const auto& run : g.runs) m = std::max(m, (int)run.size());
    return m;
}

// The ABSOLUTE width in force on `layer_id` — that layer's `def_ndr_layer`
// override if it declares one, else the rule's own.  0 when neither declares
// an absolute (a multiplier rule has no physical width to check against).
//
// This is what an audit must compare PLACED METAL against.  Comparing against
// `width_slots` instead compares the placement to a number derived from the
// same quantization the placement used, so the two agree by construction and
// the check cannot fail — see the R9 note in ndr_architecture.md.
inline double ndr_declared_width_on(const NdrSpec& s, int layer_id) {
    auto it = s.per_layer.find(layer_id);
    if (it != s.per_layer.end()) {
        // A multiplier override REPLACES the width on this layer, so an
        // inherited absolute no longer describes it and there is nothing
        // physical to check.
        if (it->second.width_slots > 0) return 0.0;
        if (it->second.width_abs > 0.0) return it->second.width_abs;
    }
    return s.width_abs;
}

// Resolve a rule ON ONE LAYER: pick that layer's declared values (its
// `def_ndr_layer` override, else the rule's own) and quantize any absolute
// among them METAL-shaped against the layer's geometry.
//
// IDENTITY when the layer has no geometry and no per-layer entry — so an
// unpatterned layer, and every caller with no layer in hand, keeps the
// spec's stored conservative quantization and over-charges rather than
// under-charges, exactly as before.
//
// `ok` (optional) reports realizability: false when a declared absolute
// cannot be met by ANY window in the period, which R3 turns into a hard
// error naming the layer, the rule and the arithmetic.  The slot count is
// then clamped to the ceiling so a caller that ignores `ok` still produces
// a bounded, conservative number rather than looping.
inline NdrSpec ndr_resolve_on_layer(const NdrSpec& s, int layer_id,
                                    const NdrLayerGeom& geom,
                                    double bit_pitch = 0.0,
                                    bool* ok = nullptr) {
    if (ok) *ok = true;
    NdrSpec o = s;

    // 1. The values THIS layer declares, falling back to the rule's own.
    double w_abs = s.width_abs, sp_abs = s.spacing_abs;
    auto it = s.per_layer.find(layer_id);
    if (it != s.per_layer.end()) {
        const NdrLayerRule& pl = it->second;
        if (pl.width_abs   > 0.0) { w_abs  = pl.width_abs;   }
        if (pl.spacing_abs > 0.0) { sp_abs = pl.spacing_abs; }
        // A multiplier override is already quantized: nothing to resolve,
        // and it WINS over an inherited absolute, since the more specific
        // declaration is the one the user made about this layer.
        if (pl.width_slots > 0)  { o.width_slots = pl.width_slots; w_abs  = 0.0; }
        if (pl.guard_slots >= 0) { o.guard_slots = pl.guard_slots; sp_abs = 0.0; }
    }
    if (w_abs <= 0.0 && sp_abs <= 0.0) return o;

    const double eps = 1e-9;
    // CHANNEL reading (the default): quantize by the per-signal-slot pitch.
    // Per-layer VALUES still apply — the two questions are orthogonal, so
    // `def_ndr_layer` works whichever reading the rule declares.
    if (!s.metal_quant) {
        if (bit_pitch <= 0.0) return o;
        if (w_abs > 0.0)
            o.width_slots = std::max(1, (int)std::ceil(w_abs / bit_pitch - eps));
        if (sp_abs > 0.0)
            o.guard_slots = std::max(0,
                (int)std::ceil(sp_abs / bit_pitch - eps) - 1);
        return o;
    }
    if (geom.empty()) return o;
    const int ceiling = ndr_max_slots(geom);
    if (w_abs > 0.0) {
        int k = 1;
        while (k <= ceiling && ndr_metal_for_slots(geom, k) < w_abs - eps) ++k;
        if (k > ceiling) { if (ok) *ok = false; k = std::max(1, ceiling); }
        o.width_slots = std::max(1, k);
    }
    if (sp_abs > 0.0) {
        int gu = 0;
        while (gu + 2 <= ceiling &&
               ndr_clearance_for_guards(geom, gu) < sp_abs - eps) ++gu;
        if (gu + 2 > ceiling) { if (ok) *ok = false; gu = std::max(0, ceiling - 2); }
        o.guard_slots = std::max(0, gu);
    }
    return o;
}

// True when a spec was declared in ABSOLUTE (layout-unit) terms and so
// resolves differently per layer.  A multiplier rule is pitch-independent,
// which is what lets every consumer take the allocation-free path below.
inline bool ndr_has_abs(const NdrSpec& s) {
    return s.width_abs > 0.0 || s.spacing_abs > 0.0;
}

// True when a spec must be resolved against the LAYER it is priced on: it
// declares an absolute (whose slot cost depends on the geometry) or carries
// any per-layer override.  False keeps the allocation-free fast path a
// multiplier rule has always taken.  Both halves matter -- a rule with no
// absolute at all can still carry a per-layer MULTIPLIER override.
inline bool ndr_is_layer_dependent(const NdrSpec& s) {
    return ndr_has_abs(s) || !s.per_layer.empty();
}

// True when the interior gap after ascending-local-bit j (0-based; gaps
// j = 0 .. nbits-2) carries a SHIELD rather than guard slots.
inline bool ndr_gap_is_shield(const NdrSpec& s, int j) {
    if (s.shield_mode == 2) return true;
    if (s.shield_mode == 3 && s.shield_per_n > 0)
        return ((j + 1) % s.shield_per_n) == 0;
    return false;
}

// ── The single-sourced GROUP demand conversion (requirement R4) ──────────
// Total SIGNAL slots a rule-uniform group of nbits bits consumes: bit
// footprints + per-gap shields/guards + the run ends.  EVERY consumer —
// planner band charging (width and signal_tracks modes), abstract-NUTS
// footprint, and DNUTS admission/placement — must go through this one
// function (or the layout below, which is definitionally lockstep with it):
// two stages rounding differently is the #536 silent-strand class.
// Shared resources are counted at the GROUP level (an 8-bit flanked group
// pays 2 end shields; two 4-bit groups pay 4), which is why the conversion
// takes the group size, never a per-bit cost.  Mixed-rule members would
// need per-member specs here; phase 1's rule-class split guarantees
// uniformity, so (spec, nbits) IS the member list.
inline int ndr_group_demand(const NdrSpec& s, int nbits) {
    if (nbits <= 0 || !s.active()) return nbits;
    int du = nbits * s.width_slots;
    for (int j = 0; j + 1 < nbits; ++j)              // interior gaps
        du += ndr_gap_is_shield(s, j) ? 1 : s.guard_slots;
    du += (s.shield_mode != 0) ? 2                    // end shields…
                               : 2 * s.guard_slots;   // …or end guards
    return du;
}

// ── R1 per-layer form of the group demand ────────────────────────────────
// The demand of `nbits` governed bits on a layer whose per-signal-slot
// channel cost is `bit_pitch` (LayerStack::bit_pitch).  THIS is what every
// routing consumer calls: an ABSOLUTE rule resolves against the layer it is
// being priced on — one declaration, different slot counts on layers of
// different pitch, which is the whole point of the form — while a multiplier
// rule is pitch-independent and takes the same allocation-free path it
// always did (no NdrSpec copy in the planner's hot scoring loop).
//
// A non-positive pitch (an unpatterned layer, or a caller with no layer in
// hand) falls back to the spec's stored quantization, which for an absolute
// rule is the CONSERVATIVE MAXIMUM over the layers it may use — so the
// fallback over-charges rather than under-charges.
inline int ndr_group_demand_on(const NdrSpec& s, int nbits, double bit_pitch) {
    if (nbits <= 0 || !s.active()) return nbits;
    if (ndr_has_abs(s) && bit_pitch > 0.0)
        return ndr_group_demand(ndr_resolve_for_pitch(s, bit_pitch), nbits);
    return ndr_group_demand(s, nbits);
}

// ── Shield NET-IDENTITY predicate (R5a/R9) ───────────────────────────────
// True when a rail/label is ELECTRICALLY IDENTICAL to a rule's requested
// shield net: case-insensitive label equality, or membership in the same
// supply family (GND/VSS/GROUND are one ground net for shielding purposes;
// VDD/VCC/POWER are one power net).  A POWER rail can never satisfy a
// GROUND spec.  This is THE predicate — shared by the R9 mis-connected-
// shield audit and (phase 2) the R5a pattern-rail crediting, so credit and
// audit cannot disagree (the review-pinned requirement).
inline bool ndr_shield_net_matches(const std::string& requested,
                                   const std::string& label) {
    auto up = [](std::string s) {
        for (char& c : s) c = (char)std::toupper((unsigned char)c);
        return s;
    };
    const std::string a = up(requested), b = up(label);
    if (a == b) return true;
    auto family = [](const std::string& s) {
        if (s == "GND" || s == "VSS" || s == "GROUND") return 1;
        if (s == "VDD" || s == "VCC" || s == "POWER")  return 2;
        return 0;
    };
    const int fa = family(a), fb = family(b);
    return fa != 0 && fa == fb;
}

// True when a pattern rail with the given label/type satisfies the rule's
// shield-net identity for CREDITING purposes (R5a): the rule opted in, has
// a shield arrangement to credit against, and the rail's label (or its
// slot type, when the label is empty or non-identifying) resolves to the
// rule's requested net under THE predicate above.  Used by the DNUTS seat
// search and the R9 audit — one rule, so credit and audit cannot disagree.
inline bool ndr_rail_credits(const NdrSpec& s, const std::string& label,
                             const std::string& type) {
    if (!s.credit_shields || s.shield_mode == 0) return false;
    if (!label.empty() && ndr_shield_net_matches(s.shield_net, label))
        return true;
    return ndr_shield_net_matches(s.shield_net, type);
}

// ── R5a-credited variants of the demand/layout pair ──────────────────────
// c_lo / c_hi: the run's low/high END shield is CREDITED to an adjacent
// matching rail — the 'S' at that end is neither emitted nor charged a
// SIGNAL slot.  Only meaningful for a shielded spec (ends are 'S'); both
// false, or an uncredited spec, reduces to the base pair exactly.  Same
// lockstep guarantee: layout size == demand for every credit combination.
inline int ndr_group_demand_credited(const NdrSpec& s, int nbits,
                                     bool c_lo, bool c_hi) {
    int du = ndr_group_demand(s, nbits);
    // The opt-in is enforced HERE, not just at the call sites: a spec that
    // never opted into crediting is identical under any flag combination,
    // so no API caller can bypass the rule's declaration.
    if (nbits <= 0 || !s.active() || s.shield_mode == 0 ||
        !s.credit_shields) return du;
    return du - (c_lo ? 1 : 0) - (c_hi ? 1 : 0);
}

// Slot-role layout of the ascending run, size == ndr_group_demand():
//   'B' first slot of a bit, 'b' continuation slot of a wide bit,
//   'S' shield wire, 'G' guard (kept empty, reserved).
// This is the placement-side rendering of the SAME arithmetic — DNUTS walks
// it to emit bits/shields and reserve guards, so demand and layout cannot
// drift (test-pinned: layout.size() == ndr_group_demand for all shapes).
inline std::string ndr_run_layout(const NdrSpec& s, int nbits) {
    std::string out;
    if (nbits <= 0 || !s.active()) {
        out.assign((size_t)std::max(nbits, 0), 'B');
        return out;
    }
    auto ends = [&](std::string& o) {
        if (s.shield_mode != 0) o += 'S';
        else o.append((size_t)s.guard_slots, 'G');
    };
    ends(out);
    for (int b = 0; b < nbits; ++b) {
        out += 'B';
        out.append((size_t)(s.width_slots - 1), 'b');
        if (b + 1 < nbits) {
            if (ndr_gap_is_shield(s, b)) out += 'S';
            else out.append((size_t)s.guard_slots, 'G');
        }
    }
    ends(out);
    return out;
}

// Credited layout: the base layout with the low/high end 'S' dropped when
// that end is credited (definitionally lockstep with
// ndr_group_demand_credited; interior shields are NEVER credited — phase-2
// scope is end shields, the parked-against-a-rail case).
inline std::string ndr_run_layout_credited(const NdrSpec& s, int nbits,
                                           bool c_lo, bool c_hi) {
    std::string out = ndr_run_layout(s, nbits);
    if (nbits <= 0 || !s.active() || s.shield_mode == 0 ||
        !s.credit_shields) return out;
    if (c_hi && !out.empty() && out.back() == 'S')  out.pop_back();
    if (c_lo && !out.empty() && out.front() == 'S') out.erase(out.begin());
    return out;
}

} // namespace buda
