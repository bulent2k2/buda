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
#include <string>

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
