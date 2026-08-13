# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""NDR commands — def_ndr / set_ndr / dump_ndr (phase 1, path A).

Non-default rules: named width / spacing / shield constraint sets attached
to nets by longest-prefix scope (docs/NDR_REQUIREMENTS.md R1-R3;
docs/NDR_UI.md decisions 1-2; docs/internal/ndr_architecture.md).

Phase-1 shape (the recorded leaning, slot-quantized consumption):
- values are MULTIPLIER form only (`x2`, `x1.5`): width_slots = ceil(xN),
  guard_slots = ceil(xN) - 1 — quantization is pattern-independent, so a
  rule means the same thing on every layer (absolute um values are a later
  phase, they need per-layer slot geometry at declaration time);
- attachment precedes bundling (set_ndr, longest prefix, '*' = default);
  the bundler splits a mixed-rule bundle by rule class LOUDLY (the R8
  fallback position — phase 1 keeps every governed bundle RULE-UNIFORM);
- the resolved buda.NdrSpec rides BundleInput.ndr into the planner /
  abstract NUTS (group-demand charging) and BusSegment.ndr into DNUTS
  (k-slot + guard + shield placement);
- R3 realizability (a contiguous SIGNAL run of width_slots must exist on a
  governed layer's pattern) is checked at first resolution against the
  declared routing grid — run_detailed_nuts refuses with the arithmetic;
- flat flow only: run_hier_bundler refuses when scopes are declared (hier
  template propagation is the next increment).
"""
import bisect
import math
import sys

import buda
import buda_diag

from ._options import require_distance


_SHIELD_MODES = {"none": 0, "bus": 1, "bit": 2}


def _rules(session):
    if not hasattr(session, "_ndr_rules"):
        session._ndr_rules = {}
    return session._ndr_rules


def _scopes(session):
    if not hasattr(session, "_ndr_scopes"):
        session._ndr_scopes = {}
    return session._ndr_scopes


# ── v21 BDB persistence (docs/internal/ndr_architecture.md §4) ─────────────
# Rules/scopes write through to an open BDB and restore on open_bdb /
# load_pipeline, the v20 layer-policy pattern verbatim: entries the user
# TYPED this session win on collision, entries a PREVIOUS restore added are
# dropped before another BDB's merge in (a BDB switch must not carry the
# old design's rules along).

# ── v28: per-layer values as a compact JSON object ──────────────────────
# Keys are layer ids as strings (JSON has no integer keys); the four value
# slots keep the declaration's TRI-STATE, which is the whole subtlety: 0.0
# absolute / 0 multiplier-slots / -1 guard-slots each mean "not declared
# here, inherit", and 0 guards IS a real declaration.  Collapsing them on the
# way through would restore a rule that governs differently than the one
# written.

def _per_layer_to_json(per_layer):
    import json
    if not per_layer:
        return ""
    return json.dumps({str(lid): {"w":  e.get("width_abs", 0.0),
                                  "s":  e.get("spacing_abs", 0.0),
                                  "wx": e.get("width_x", 0.0),
                                  "sx": e.get("spacing_x", 0.0)}
                       for lid, e in sorted(per_layer.items())},
                      sort_keys=True, separators=(",", ":"))


def _per_layer_from_json(txt, rule_name=""):
    """Decode the stored per-layer values, tolerating anything.

    Every malformed shape routes through BUDA-1912 and restores the rule
    LAYER-INDEPENDENT.  "Readable JSON" is not enough: `[]`, `"x"`, `5` and
    `{"4": null}` all parse and then raise AttributeError on `.items()` /
    `.get()`, so a database carrying one would ABORT the session that opened
    it rather than warn (Codex P2 on #719).  A stored value we cannot read is
    a reason to say so, never a reason to fail to open the design.

    A partially-bad map warns too, with a count: dropping entries silently is
    the same fault one level down."""
    import json
    if not txt:
        return {}
    who = f" on rule '{rule_name}'" if rule_name else ""

    def _warn(why):
        print(f"BUDA-1912: WARNING: the stored per-layer NDR values{who} "
              f"{why} — the rule restores LAYER-INDEPENDENT.  Re-declare "
              f"with def_ndr_layer.", flush=True)

    try:
        raw = json.loads(txt)
    except ValueError:
        _warn("are not readable JSON and were DROPPED")
        return {}
    if not isinstance(raw, dict):
        _warn(f"are a {type(raw).__name__}, not an object, and were DROPPED")
        return {}
    out, bad = {}, 0
    for lid, e in raw.items():
        if not isinstance(e, dict):
            bad += 1
            continue
        try:
            out[int(lid)] = {"width_abs":   float(e.get("w", 0.0) or 0.0),
                             "spacing_abs": float(e.get("s", 0.0) or 0.0),
                             "width_x":     float(e.get("wx", 0.0) or 0.0),
                             "spacing_x":   float(e.get("sx", 0.0) or 0.0)}
        except (TypeError, ValueError):
            bad += 1
    if bad:
        _warn(f"had {bad} unreadable entr{'y' if bad == 1 else 'ies'}, DROPPED "
              f"({len(out)} kept)")
    return out


def _write_rule_through(session, name):
    if getattr(session, "bdb", None) is None:
        return
    r = _rules(session)[name]
    row = buda.NdrRuleRow()
    row.name         = name
    row.width_x      = r["width_x"]
    row.spacing_x    = r["spacing_x"]
    row.shield_mode  = r["shield_mode"]
    row.shield_per_n = r["shield_per_n"]
    row.shield_net   = r["shield_net"]
    row.layers       = ",".join(str(l) for l in (r["layers"] or []))
    row.credit       = int(r.get("credit", 0))
    row.bond         = int(r.get("bond", 0))
    # v26: an absolute declaration leaves the MULTIPLIER at 1.0, so without
    # these the rule reloads as DEFAULT width — usually inactive, silently
    # dropping the constraint the design was routed under (Codex P1 #682).
    row.width_abs    = float(r.get("width_abs", 0.0))
    row.spacing_abs  = float(r.get("spacing_abs", 0.0))
    # v28: without this a reopened design restores the rule missing half its
    # declaration and routes NARROWER than what was saved — and the session
    # that could warn is not the session that suffers.
    row.per_layer    = _per_layer_to_json(r.get("per_layer"))
    row.metal        = int(r.get("metal", 0))
    session.bdb.set_ndr_rule(row)


def _write_scope_through(session, prefix, rule_or_none):
    if getattr(session, "bdb", None) is None:
        return
    if rule_or_none is None:
        session.bdb.delete_ndr_scope(prefix)
    else:
        session.bdb.set_ndr_scope(prefix, rule_or_none)


def restore_ndr_from_bdb(session):
    """Merge the open BDB's rules + scopes into the session (session-typed
    entries win; a previous restore's entries are dropped first).  Also
    writes through any session-typed entries the BDB lacks, so declare-
    then-open and open-then-declare converge on the same persisted state.
    Returns (n_rules, n_scopes) restored."""
    if getattr(session, "bdb", None) is None:
        return (0, 0)
    rules, scopes = _rules(session), _scopes(session)
    typed_rules  = getattr(session, "_ndr_rules_typed", None) or set()
    typed_scopes = getattr(session, "_ndr_scopes_typed", None) or set()
    for name in (getattr(session, "_ndr_rules_restored", None) or set()):
        if name in rules and name not in typed_rules:
            del rules[name]
    for pfx in (getattr(session, "_ndr_scopes_restored", None) or set()):
        if pfx in scopes and pfx not in typed_scopes:
            del scopes[pfx]
    restored_rules, restored_scopes = set(), set()
    for row in session.bdb.ndr_rules():
        if row.name in rules:            # session-typed wins
            continue
        rules[row.name] = {
            "width_x": row.width_x, "spacing_x": row.spacing_x,
            "shield_mode": row.shield_mode, "shield_per_n": row.shield_per_n,
            "shield_net": row.shield_net,
            "layers": ([int(t) for t in row.layers.split(",")]
                       if row.layers else None),
            "credit": row.credit,
            "bond": row.bond,
            "width_abs": row.width_abs,
            "spacing_abs": row.spacing_abs,
            "per_layer": _per_layer_from_json(row.per_layer, row.name),
            "metal": row.metal,
            "name": row.name,
        }
        if row.width_abs > 0.0 or row.spacing_abs > 0.0:
            # The quantization is a function of the CURRENT grid, so it is
            # re-derived rather than stored: the same rule against a
            # different stack is a different slot count, and a stale one
            # would be charged against geometry it never measured.
            if not ensure_abs_quantization(session, rules[row.name]):
                # No grid yet — the common case, since rules restore at
                # `open_bdb` and the script declares its patterns after.
                # DEFERRED, not lost: the quantization is derived at the
                # rule's first use (_spec_of), by which time the patterns
                # exist.  Note rather than warn; the LOUD failure lives at
                # first use, where a still-unpatterned layer is a real
                # error instead of a script-order artifact.
                print(f"[NDR] restored absolute rule '{row.name}': "
                      f"quantization deferred — its governed layers have "
                      f"no track pattern yet, so it is derived when the "
                      f"rule is first used (declare def_track_pattern "
                      f"before bundling)")
        restored_rules.add(row.name)
    for prefix, rule in session.bdb.ndr_scopes():
        if prefix in scopes:             # session-typed wins
            continue
        if rule not in rules:
            print(f"WARNING: [NDR] restored scope '{prefix}' names unknown "
                  f"rule '{rule}' — ignored")
            continue
        scopes[prefix] = rule
        restored_scopes.add(prefix)
    session._ndr_rules_restored  = restored_rules
    session._ndr_scopes_restored = restored_scopes
    # Converge: session-typed entries the BDB lacks write through now.
    for name in typed_rules:
        if name in rules:
            _write_rule_through(session, name)
    for pfx in typed_scopes:
        if pfx in scopes:
            _write_scope_through(session, pfx, scopes[pfx])
    if restored_rules or restored_scopes:
        print(f"[NDR] restored {len(restored_rules)} rule(s) and "
              f"{len(restored_scopes)} scope(s) from the open BDB")
    return (len(restored_rules), len(restored_scopes))


def ndr_pricing_fp(session, rule_name):
    """Canonical PRICING-BASIS fingerprint of a rule: the QUANTIZED spec
    (slots/guards/shield arrangement — exactly what the planner charged and
    DNUTS placed) plus the layer restriction.  Stamped into
    bundle.ndr_rule at persist time and compared verbatim by
    audit_restored_ndr, so the VOID decision is self-contained in the
    bundle row — it survives the rule row itself being overwritten by a
    later session's converge write-through (Codex on #620: a re-declared
    same-name rule + exit-before-load destroyed the only stored copy of
    the checkpoint's definition).  A content change that does NOT move the
    quantized demand (e.g. width x1.8 -> x2.0, both 2 slots) correctly
    fingerprints identically: the plan's pricing is unchanged.  None for
    an unknown rule."""
    r = _rules(session).get(rule_name)
    if r is None:
        return None
    if r.get("width_abs", 0) > 0 or r.get("spacing_abs", 0) > 0:
        # An absolute rule's PRICING basis is its conservative quantization,
        # not ceil(width_x) — that is 1.0 for every absolute rule and would
        # fingerprint them all as DEFAULT, so a changed absolute value could
        # never VOID a restored plan.  The declared values join the stamp
        # too: two absolutes can quantize alike on today's grid and differ
        # on tomorrow's.
        ensure_abs_quantization(session, r)   # restored-before-patterns
        ws = r.get("width_slots_max", 1)
        gs = r.get("guard_slots_max", 0)
    else:
        ws = max(1, math.ceil(r["width_x"]))
        gs = max(0, math.ceil(r["spacing_x"]) - 1)
    lay = ",".join(str(l) for l in (r["layers"] or []))
    # The credit field appends ONLY when set, so every v21 stamp (which
    # predates the field) still compares equal for a non-credit rule — a
    # resumed v21 checkpoint must not VOID on a fingerprint-format change.
    # `bond` is deliberately ABSENT: bonding emits extra output vias and
    # moves neither the demand nor the placement, so toggling it must not
    # VOID a restored plan (this is the PRICING basis, not the rule text).
    cr = "|c1" if r.get("credit") else ""
    # Absolute values append ONLY when declared, so every pre-R1 stamp of a
    # multiplier rule still compares equal.
    ab = ""
    if r.get("width_abs", 0) > 0 or r.get("spacing_abs", 0) > 0:
        ab = f"|a{r.get('width_abs', 0):g},{r.get('spacing_abs', 0):g}"
    # v28: BOTH move the priced demand, so both join the stamp.  Per-layer
    # values change the slot count on the layers they name; the metal
    # reading changes it on every layer.  Appended ONLY when present, the
    # same convention `credit` and the absolutes use, so every earlier stamp
    # still compares equal and a resumed pre-v28 checkpoint does not VOID on
    # a fingerprint-FORMAT change.
    pl = ("|P" + _per_layer_to_json(r["per_layer"])) if r.get("per_layer") else ""
    mq = "|m1" if r.get("metal") else ""
    return (f"{rule_name}|w{ws}|g{gs}|s{r['shield_mode']}"
            f"|p{r['shield_per_n']}|n{r['shield_net']}|L{lay}{cr}{ab}{pl}{mq}")


def stamp_bundle_ndr(session, row, wrapper):
    """Stamp the wrapper's governing rule PRICING FINGERPRINT onto a
    BundleRow about to be persisted (v21 provenance —
    audit_restored_ndr's comparison basis; "" = default rule)."""
    spec = wrapper.input.ndr
    row.ndr_rule = (ndr_pricing_fp(session, spec.rule_name) or ""
                    if spec.active() else "")


def audit_restored_ndr(session, bid_to_stamp):
    """The v21 VOID-on-change audit (load_pipeline): a restored bundle whose
    persisted PRICING FINGERPRINT (bundle.ndr_rule, stamped at persist
    time) differs from the FRESH resolution — the rules or scopes changed
    since the checkpoint was routed, so the restored plan was priced under
    a different demand — has its selection VOIDED (LOUD; continuing
    requires an explicit re-plan; the persisted rows are untouched, a
    re-run of the planner rewrites them).  EVERY net of the bundle is
    resolved (Codex on #620: a scope change matching only a non-leading
    net turns a checkpoint's rule-uniform bundle MIXED — nets[0] alone
    would miss it), and a now-mixed bundle voids with a re-BUNDLE notice:
    the rule-class split itself is stale, not just the plan.  Matching
    governed bundles get their specs (re)stamped so the resumed session
    stays governed.  Returns the set of voided bundle ids (ints)."""
    if not getattr(session, "_ndr_scopes", None) and not bid_to_stamp:
        return set()
    voided = set()
    for w in session.bundles:
        b = w.input.original_bundle
        nets = list(b.get_net_names())
        resolutions = ({ndr_rule_for_net(session, n) for n in nets}
                       if nets else {None})
        stamped = bid_to_stamp.get(int(b.id), "") or ""
        reason, fresh = None, None
        if len(resolutions) > 1:
            names = sorted((r or "default") for r in resolutions)
            reason = (f"its nets now resolve to MIXED rules "
                      f"[{', '.join(names)}] — the checkpoint's rule-class "
                      f"split is stale; re-run the bundler and planner")
        else:
            fresh = next(iter(resolutions))
            fresh_fp = (ndr_pricing_fp(session, fresh) or "") if fresh else ""
            if fresh_fp != stamped:
                old = stamped.split("|", 1)[0] if stamped else "default"
                reason = (f"persisted under rule '{old}' "
                          f"[{stamped or 'default'}] but now resolves to "
                          f"'{fresh or 'default'}' "
                          f"[{fresh_fp or 'default'}] — demand was priced "
                          f"under the old rule; re-run the planner")
        if reason and w.plan.selected_topology_index >= 0:
            name = nets[0] if nets else "?"
            print(f"WARNING: [NDR] bundle {b.id} ({name}): {reason} — "
                  f"restored plan VOIDED")
            w.plan.selected_topology_index = -1
            w.plan.seg_layers = []
            voided.add(int(b.id))
        if fresh is not None and len(resolutions) == 1:
            w.input.ndr = _spec_of(session, fresh)
            layers = _rules(session)[fresh]["layers"]
            if layers:
                cur = list(w.input.allowed_layers)
                w.input.allowed_layers = sorted(
                    set(layers) & set(cur)) if cur else sorted(layers)
    return voided


def ndr_rule_for_net(session, net_name):
    """Resolved rule name for a net (longest matching set_ndr prefix; '*'
    is the global default), or None for the default rule.  Mirrors
    set_bundling's longest-prefix-wins semantics."""
    scopes = getattr(session, "_ndr_scopes", None)
    if not scopes:
        return None
    best, best_len = None, -1
    for prefix, rule in scopes.items():
        if prefix == "*":
            if best_len < 0:
                best, best_len = rule, 0
        elif net_name.startswith(prefix) and len(prefix) > best_len:
            best, best_len = rule, len(prefix)
    return best


def _spec_of(session, rule_name):
    """buda.NdrSpec for a declared rule (phase-1 quantization)."""
    r = _rules(session)[rule_name]
    spec = buda.NdrSpec()
    spec.rule_name    = rule_name
    spec.width_abs    = r.get("width_abs", 0.0)
    spec.spacing_abs  = r.get("spacing_abs", 0.0)
    if spec.width_abs > 0.0 or spec.spacing_abs > 0.0:
        # Absolute: the CONSERVATIVE resolution (max over the governed
        # layers); consumers narrow it per layer.  Derived at declaration
        # when the patterns were already there, HERE when the rule was
        # restored from a BDB before the script declared them.
        if not ensure_abs_quantization(session, r):
            # This is the same refusal `def_ndr` makes, at the first moment
            # it can be made for a restored rule: the alternative is
            # charging default width for a rule the design persisted, which
            # is a silently unconstrained route.
            _, bare = _abs_layer_pitches(session, r)
            missing = (", ".join(f"L{l}" for l in bare) if bare
                       else "no layer in this session")
            print(f"Error: NDR rule '{rule_name}' declares an absolute "
                  f"width/spacing but {missing} has no def_track_pattern, "
                  f"so the rule cannot be quantized against the grid it "
                  f"would route on.  Declare the patterns before bundling "
                  f"(a restored rule cannot be re-declared — def_ndr "
                  f"refuses a duplicate name).")
            sys.exit(1)
        spec.width_slots = r.get("width_slots_max", 1)
        spec.guard_slots = r.get("guard_slots_max", 0)
    else:
        spec.width_slots  = max(1, math.ceil(r["width_x"]))
        spec.guard_slots  = max(0, math.ceil(r["spacing_x"]) - 1)
    spec.shield_mode  = r["shield_mode"]
    spec.shield_per_n = r["shield_per_n"]
    spec.shield_net   = r["shield_net"]
    spec.credit_shields = bool(r.get("credit", 0))
    spec.bond_stride    = int(r.get("bond", 0))
    spec.metal_quant    = bool(r.get("metal", 0))
    # R1 per-layer declared values (`def_ndr_layer`).  Carried on the spec so
    # every consumer resolves the value the user declared FOR THE LAYER it is
    # working on, rather than one value stretched across the stack.
    for lid, pl in sorted(r.get("per_layer", {}).items()):
        lr = buda.NdrLayerRule()
        lr.width_abs   = pl.get("width_abs", 0.0)
        lr.spacing_abs = pl.get("spacing_abs", 0.0)
        # A multiplier is pattern-independent, so it is quantized here and
        # there is nothing left for the per-layer resolution to do.
        lr.width_slots = (max(1, math.ceil(pl["width_x"]))
                          if pl.get("width_x") else 0)
        lr.guard_slots = (max(0, math.ceil(pl["spacing_x"]) - 1)
                          if pl.get("spacing_x") else -1)
        spec.set_layer_rule(lid, lr)
    return spec


def _parse_value(tok, what, name, session=None):
    """Parse a width/spacing value in either R1 form.

    `xN` is the MULTIPLIER: pattern-independent, N signal slots on every
    layer.  A bare number is the ABSOLUTE form in LAYOUT UNITS — one
    physical width whose slot cost depends on the layer's pitch, which is
    the whole reason R1 asks for it.  Returns (multiplier, absolute) with
    exactly one of them set.

    Both are rejected below 0: a zero-or-negative width is not a weaker
    rule, it is a meaningless one."""
    if tok.lower().startswith("x"):
        try:
            v = float(tok[1:])
        except ValueError:
            v = -1.0
        if v < 1.0:
            print(f"Error: def_ndr '{name}': {what} multiplier must be >= 1 "
                  f"(got '{tok}')")
            sys.exit(1)
        return (v, 0.0)
    # Absolute: the SHARED distance parser, so an NDR value spells a
    # distance the way every other script-declared distance does — bare =
    # layout units, `um` = microns converted through the declared import
    # scale at parse time (opens_interchange item 6).  Rolling our own
    # float() here would have refused `width 0.2um` and forced every
    # DBU-scale flow to hand-convert (Codex P2 on #682).
    a = require_distance(
        "def_ndr", f"{what} for rule '{name}'", tok, session,
        usage="def_ndr <name> [width x<N>|<dist>] [spacing x<N>|<dist>] ...",
        why="an absolute NDR width/spacing is a distance")
    if a <= 0.0:
        print(f"Error: def_ndr '{name}': absolute {what} must be > 0 "
              f"(got '{tok}')")
        sys.exit(1)
    return (1.0, a)


def ensure_abs_quantization(session, rule):
    """Derive an absolute rule's CONSERVATIVE quantization (`width_slots_max`
    / `guard_slots_max` — the largest slot count over the layers it may use)
    if it does not have one yet.  Returns True when the rule now carries a
    quantization, False when the grid still cannot supply one.

    Idempotent and LAZY on purpose.  A declaration has its patterns by
    construction (`def_ndr` refuses otherwise), but a rule RESTORED from a
    BDB usually does not: `open_bdb` runs before the script re-declares
    `def_track_pattern`.  Quantizing only at restore left such a rule with
    no slot count and no way to acquire one — `_spec_of` then fell back to
    1/0, which for a width/spacing-only rule is an INACTIVE spec, so the
    design routed with the persisted constraint silently dropped.  (The
    obvious workaround does not exist: re-declaring the name is a hard
    error.)  Deriving it at first use instead means the value is always
    taken from the grid that will actually be routed on.

    Per-layer resolution can only REDUCE this maximum, so a consumer that
    forgets to resolve over-charges rather than under-charges."""
    if rule.get("width_abs", 0.0) <= 0.0 and rule.get("spacing_abs", 0.0) <= 0.0:
        return True                       # multiplier rule: nothing to derive
    if "width_slots_max" in rule:
        return True
    pitches, bare = _abs_layer_pitches(session, rule)
    if not pitches or bare:
        # A max over a SUBSET is not conservative — a pattern declared later
        # on an omitted layer can need more slots — so an incomplete grid
        # yields no quantization at all rather than an optimistic one.
        return False
    ws, gs = 1, 0
    layers = getattr(session, "layers", None)
    for lid in pitches:
        probe = buda.NdrSpec()
        probe.width_abs, probe.spacing_abs = (rule["width_abs"],
                                              rule["spacing_abs"])
        for l2, pl in sorted(rule.get("per_layer", {}).items()):
            lr = buda.NdrLayerRule()
            lr.width_abs   = pl.get("width_abs", 0.0)
            lr.spacing_abs = pl.get("spacing_abs", 0.0)
            lr.width_slots = (max(1, math.ceil(pl["width_x"]))
                              if pl.get("width_x") else 0)
            lr.guard_slots = (max(0, math.ceil(pl["spacing_x"]) - 1)
                              if pl.get("spacing_x") else -1)
            probe.set_layer_rule(l2, lr)
        geom = layers.ndr_geom(lid) if layers is not None else None
        if geom is None:
            geom = buda.NdrLayerGeom()
        if rule.get("metal") and geom.empty():
            return False          # metal needs geometry, not just a pitch
        # METAL-shaped, so the stored maximum bounds what the consumers will
        # actually ask for.  Derived from the CHANNEL reading it would be
        # SMALLER than the per-layer answer, and the conservative-fallback
        # property (a consumer that forgets to resolve over-charges) would
        # invert into under-charging.
        probe.metal_quant = bool(rule.get("metal", 0))
        r, ok = buda.ndr_resolve_on_layer(probe, lid, geom, pitches[lid])
        if not ok:
            # An unrealizable value is CLAMPED to the layer's longest run, so
            # caching it would make the later R3 check see a count that fits
            # by construction and route the rule below its declared width --
            # silently (Codex P1 on #717).  R3 says the tool never degrades
            # an NDR, so refuse here, where the arithmetic is still nameable.
            ceiling = buda.ndr_max_slots(geom)
            best = buda.ndr_metal_for_slots(geom, ceiling)
            # Reverse-lookup only on the error path: the rule dict does not
            # carry its own name, and a refusal that cannot name the rule is
            # half a message.
            nm = (rule.get("name")
                  or next((k for k, v in _rules(session).items()
                           if v is rule), "?"))
            print(f"Error: NDR rule '{nm}': layer "
                  f"L{lid} cannot realize the declared value — its longest "
                  f"contiguous signal run is {ceiling} slot(s), delivering at "
                  f"most {best:g} unit(s) of metal (a wire may not span a "
                  f"power rail).  R3: the tool never silently degrades an "
                  f"NDR.", flush=True)
            sys.exit(1)
        ws, gs = max(ws, r.width_slots), max(gs, r.guard_slots)
    rule["width_slots_max"], rule["guard_slots_max"] = ws, gs
    return True


def ndr_layer_pitch(session, layer, wrapper=None):
    """The per-signal-slot pitch an absolute rule quantizes against on
    `layer`: the SHARED CELL's derived view when `wrapper` belongs to a
    `set_cell_layer_share` cell (that cell's own interconnect is planned,
    NUTS-solved and DNUTS-placed on a thinned pattern whose real cost is
    `unit_pitch / n_kept`), otherwise the global stack's.

    One resolver so a reporting path cannot read a different pitch than the
    stage it reports on — the R4 single-sourcing rule applied to the pitch
    itself.  Passing no wrapper always yields the global pitch, which is
    what every flow without fractional shares uses anyway."""
    if wrapper is not None:
        share_pitch = getattr(session, "_shared_cell_ndr_pitch", None)
        if share_pitch is not None:
            p = share_pitch(wrapper, layer)
            if p is not None:
                return p
    layers = getattr(session, "layers", None)
    return layers.bit_pitch(layer) if layers is not None else 0.0


def ndr_spec_for_layer(session, spec, layer, wrapper=None):
    """`spec` as it resolves on ONE layer — the R1 per-layer quantization
    of an absolute rule, identity for a multiplier rule (and for an unknown
    or unpatterned layer, whose pitch is 0).

    THE conversion for anything reading a spec against a DECIDED layer:
    the routing stages resolve in C++ (ndr_group_demand_on / the
    make_bus_segments pre-resolve), and every Python consumer that knows a
    layer — the R3 realizability check, the R9 audit, `dump_ndr`'s routed
    view — must resolve identically or it reports on a rule the engine did
    not place.  Pass `wrapper` wherever one is in hand so a shared cell's
    derived pitch is used (see ndr_layer_pitch).  A consumer with NO layer
    keeps the spec's stored quantization, which is the conservative MAXIMUM
    over the rule's governed layers: over-strict, never permissive."""
    if (spec.width_abs <= 0.0 and spec.spacing_abs <= 0.0
            and not spec.per_layer):
        return spec
    # METAL-shaped, against the layer's own slot geometry — the same call the
    # planner / abstract NUTS / DNUTS make, so a report can never describe a
    # different quantization than the one that was routed.
    geom = _ndr_geom_for(session, layer, wrapper)
    if geom is None or geom.empty():
        return spec              # no pattern: keep the conservative maximum
    resolved, _ok = buda.ndr_resolve_on_layer(
        spec, layer, geom, ndr_layer_pitch(session, layer, wrapper))
    return resolved


def _ndr_geom_for(session, layer, wrapper=None):
    """The signal-slot geometry `layer` is quantized against.

    Mirrors `ndr_layer_pitch`: a `set_cell_layer_share` cell routes on a
    THINNED pattern, so its rule must resolve against what it was leased
    rather than the parent's full grid."""
    if wrapper is not None:
        share_geom = getattr(session, "_shared_cell_ndr_geom", None)
        if share_geom is not None:
            g = share_geom(wrapper, layer)
            if g is not None:
                return g
    layers = getattr(session, "layers", None)
    return layers.ndr_geom(layer) if layers is not None else None


def _abs_layer_pitches(session, rule):
    """Per-signal-slot pitches of the layers an absolute rule may use —
    its `layers` restriction, or every declared layer when unrestricted.

    Returns (patterned, unpatterned): a layer with bit_pitch 0 has no slot
    geometry to quantize against and is reported SEPARATELY rather than
    filtered away.  Dropping it would break the conservative-max property
    outright — a `def_track_pattern` declared later on an omitted layer can
    need MORE slots than the stored maximum, and routing there would then
    UNDER-charge the declared width (Codex P1 on #682).  The max is only
    conservative if it was taken over every layer the rule can reach."""
    layers = session.layers
    if layers is None:
        return ({}, [])
    ids = rule["layers"]
    if ids is None:
        ids = list(layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL)) + \
              list(layers.get_layer_ids_by_dir(buda.LayerDir.VERTICAL))
    patterned, bare = {}, []
    for lid in sorted(set(ids)):
        p = layers.bit_pitch(lid)
        if p > 0:
            patterned[lid] = p
        else:
            bare.append(lid)
    return (patterned, bare)


def cmd_def_ndr(session, cmd, args, cmd_line):
    # def_ndr <name> [width x<N>] [spacing x<N>]
    #                [shield bus|bit|per:<N> [net <label>]] [credit] [bond]
    #                [layers <csv>]
    # Declare-once (duplicate = hard error), unknown token = hard error,
    # and a rule that constrains nothing is refused — the def_layer /
    # def_track_pattern LOUD-declaration convention.
    if not args:
        print("Error: usage: def_ndr <name> [width x<N>] [spacing x<N>] "
              "[shield bus|bit|per:<N> [net <label>]] [credit] [bond] "
              "[layers <csv>]")
        return
    name = args[0]
    if name in _rules(session):
        print(f"Error: NDR rule '{name}' already declared — a duplicate "
              f"def_ndr is a hard error")
        sys.exit(1)
    rule = {"width_x": 1.0, "spacing_x": 1.0, "shield_mode": 0,
            "shield_per_n": 0, "shield_net": "GND", "layers": None,
            "credit": 0, "bond": 0, "width_abs": 0.0,
            "spacing_abs": 0.0, "metal": 0, "name": name}
    i = 1
    while i < len(args):
        tok = args[i].lower()
        if tok == "width" and i + 1 < len(args):
            rule["width_x"], rule["width_abs"] = _parse_value(
                args[i + 1], "width", name, session); i += 2
        elif tok == "spacing" and i + 1 < len(args):
            rule["spacing_x"], rule["spacing_abs"] = _parse_value(
                args[i + 1], "spacing", name, session); i += 2
        elif tok == "shield" and i + 1 < len(args):
            mode = args[i + 1].lower()
            if mode in _SHIELD_MODES:
                rule["shield_mode"] = _SHIELD_MODES[mode]
            elif mode.startswith("per:"):
                try:
                    n = int(mode[4:])
                except ValueError:
                    n = 0
                if n < 1:
                    print(f"Error: def_ndr '{name}': shield per:<N> needs "
                          f"N >= 1 (got '{args[i + 1]}')")
                    sys.exit(1)
                rule["shield_mode"], rule["shield_per_n"] = 3, n
            else:
                print(f"Error: def_ndr '{name}': unknown shield mode "
                      f"'{args[i + 1]}' (bus | bit | per:<N>)")
                sys.exit(1)
            i += 2
            if i + 1 < len(args) and args[i].lower() == "net":
                rule["shield_net"] = args[i + 1]; i += 2
        elif tok == "layers" and i + 1 < len(args):
            ids = []
            for t in args[i + 1].split(","):
                lid = _layer_id(session, t)
                if lid is None:
                    print(f"Error: def_ndr '{name}': unknown layer '{t}' "
                          f"(declare def_layer first)")
                    sys.exit(1)
                ids.append(lid)
            rule["layers"] = sorted(set(ids)); i += 2
        elif tok == "metal":
            # R1: quantize an ABSOLUTE value by the METAL a run of slots
            # delivers rather than by the layer's channel cost.  Opt-in
            # because the honest reading is STRICTER: see ndr.h.
            rule["metal"] = 1; i += 1
        elif tok == "credit":
            # R5a: an END shield may be satisfied by an adjacent pattern
            # rail electrically identical to the shield net.  Opt-in.
            rule["credit"] = 1; i += 1
        elif tok == "bond":
            # R6: strap each EMITTED shield to the power grid where it
            # crosses an identity-matching rail on an adjacent perpendicular
            # layer.  Opt-in (docs/internal/opens_ndr.md §1).  The stored
            # value IS the stride — 1 (the bare token) = every crossing,
            # N = every Nth, both extremes always anchored.
            rule["bond"] = 1; i += 1
            if i + 1 < len(args) and args[i].lower() == "stride":
                try:
                    n = int(args[i + 1])
                except ValueError:
                    n = 0
                if n < 1:
                    print(f"Error: def_ndr '{name}': bond stride needs "
                          f"N >= 1 (got '{args[i + 1]}'); 1 = strap every "
                          f"crossing")
                    sys.exit(1)
                rule["bond"] = n; i += 2
        else:
            print(f"Error: def_ndr '{name}': unknown or incomplete token "
                  f"'{args[i]}' — a typo would silently weaken the rule")
            sys.exit(1)
    spec_probe = (rule["width_x"] > 1.0 or rule["spacing_x"] > 1.0
                  or rule["width_abs"] > 0.0 or rule["spacing_abs"] > 0.0
                  or rule["shield_mode"] != 0)
    if not spec_probe:
        print(f"Error: def_ndr '{name}' constrains nothing (default width, "
              f"spacing, no shield) — declare at least one constraint")
        sys.exit(1)
    if rule["credit"] and rule["shield_mode"] == 0:
        print(f"Error: def_ndr '{name}': 'credit' needs a shield "
              f"arrangement to credit against — declare shield "
              f"bus|bit|per:<N> with it")
        sys.exit(1)
    if rule["bond"] and rule["shield_mode"] == 0:
        print(f"Error: def_ndr '{name}': 'bond' needs a shield arrangement "
              f"to bond — declare shield bus|bit|per:<N> with it")
        sys.exit(1)
    # R1 ORDERING: an absolute value has no meaning without slot geometry
    # — it is a physical width, and how many slots that costs is a property
    # of the layer's pattern.  So the governed layers' track patterns must
    # already be declared.  Refusing here is the LOUD form: resolving later
    # against whatever grid happened to exist would silently charge a
    # DIFFERENT width than the one declared, and the multiplier form is
    # right there for anyone who wants order-independence.
    if rule["width_abs"] > 0.0 or rule["spacing_abs"] > 0.0:
        pitches, bare = _abs_layer_pitches(session, rule)
        if not pitches or bare:
            # EVERY governed layer must be patterned, not merely one: the
            # stored quantization is a MAXIMUM, and a max taken over a
            # subset is not conservative — a pattern declared later on an
            # omitted layer can need more slots, and routing there would
            # under-charge the declared width.
            missing = (", ".join(f"L{l}" for l in bare) if bare
                       else ("layers " + ",".join(str(l) for l in rule["layers"])
                             if rule["layers"] else "any layer"))
            print(f"Error: def_ndr '{name}': an absolute width/spacing needs "
                  f"the slot geometry it quantizes against, on EVERY layer "
                  f"the rule can reach — {missing} has no def_track_pattern "
                  f"yet.  Declare the patterns BEFORE this rule, restrict it "
                  f"with `layers <csv>`, or use the multiplier form (xN), "
                  f"which is pattern-independent")
            sys.exit(1)
        ensure_abs_quantization(session, rule)
    _rules(session)[name] = rule
    if not hasattr(session, "_ndr_rules_typed"):
        session._ndr_rules_typed = set()
    session._ndr_rules_typed.add(name)
    _write_rule_through(session, name)       # v21: persists when a BDB is open
    if rule["width_abs"] > 0.0 or rule["spacing_abs"] > 0.0:
        ws = rule.get("width_slots_max", 1)
        gs = rule.get("guard_slots_max", 0)
    else:
        ws = max(1, math.ceil(rule["width_x"]))
        gs = max(0, math.ceil(rule["spacing_x"]) - 1)
    sh = {0: "none", 1: "bus", 2: "bit", 3: f"per:{rule['shield_per_n']}"}[
        rule["shield_mode"]]
    lay = ("any" if rule["layers"] is None
           else ",".join(str(l) for l in rule["layers"]))
    cr = " credit" if rule["credit"] else ""
    bo = ("" if not rule["bond"] else
          " bond" if rule["bond"] == 1 else f" bond stride {rule['bond']}")
    if rule["width_abs"] > 0.0 or rule["spacing_abs"] > 0.0:
        # An absolute rule has NO single slot count — report the declared
        # value, the conservative resolution everything is charged at, and
        # the per-layer spread, so the layer dependence is visible rather
        # than implied.
        wd = (f"width {rule['width_abs']:g}" if rule["width_abs"] > 0
              else f"width x{rule['width_x']:g}")
        sd = (f"spacing {rule['spacing_abs']:g}" if rule["spacing_abs"] > 0
              else f"spacing x{rule['spacing_x']:g}")
        per = ", ".join(
            f"L{lid}:{buda.ndr_resolve_for_pitch(_spec_of(session, name), p).width_slots}"
            f"/{buda.ndr_resolve_for_pitch(_spec_of(session, name), p).guard_slots}"
            for lid, p in sorted(_abs_layer_pitches(session, rule)[0].items()))
        print(f"[NDR] rule '{name}': {wd}, {sd} (ABSOLUTE, layout units) -> "
              f"{ws} slot(s)/bit + {gs} guard(s)/gap charged (the max over "
              f"governed layers; per layer slots/guards {per}), "
              f"shield {sh} (net {rule['shield_net']}){cr}{bo}, layers {lay}")
    else:
        print(f"[NDR] rule '{name}': width x{rule['width_x']:g} -> {ws} slot(s)/"
              f"bit, spacing x{rule['spacing_x']:g} -> {gs} guard slot(s)/gap, "
              f"shield {sh} (net {rule['shield_net']}){cr}{bo}, layers {lay}")


def cmd_def_ndr_layer(session, cmd, args, cmd_line):
    """def_ndr_layer <rule> <layer> [width x<N>|<dist>] [spacing x<N>|<dist>]

    R1's per-layer half.  The requirement always asked for "per-layer OR
    layer-independent values" — its cited precedent, LEF/DEF NONDEFAULTRULE,
    is per-layer — and phase 1 shipped only the layer-independent half without
    listing the other among its non-goals.

    It matters most for exactly the rules the absolute form exists to serve:
    EM limits, sheet resistance and RC all differ per layer, so one width
    applied to the whole stack is over-wide on top or under-wide at the
    bottom.  This is the `add_grid_override` shape — a global declaration plus
    per-scope overrides — which is the idiom the script language already uses.
    """
    if len(args) < 3:
        print("Error: usage: def_ndr_layer <rule> <layer> "
              "[width x<N>|<dist>] [spacing x<N>|<dist>]")
        sys.exit(1)
    name = args[0]
    rules = _rules(session)
    if name not in rules:
        print(f"Error: def_ndr_layer: unknown NDR rule '{name}' — declare it "
              f"with def_ndr first")
        sys.exit(1)
    rule = rules[name]
    lid = _layer_id(session, args[1])
    if lid is None:
        print(f"Error: def_ndr_layer '{name}': unknown layer '{args[1]}' "
              f"(declare def_layer first)")
        sys.exit(1)
    # A value for a layer the rule may never use is a mistake, not a
    # harmless extra: it reads as governing and can never take effect.
    if rule.get("layers") and lid not in rule["layers"]:
        allowed = ", ".join(f"L{l}" for l in rule["layers"])
        print(f"Error: def_ndr_layer '{name}': layer '{args[1]}' is outside "
              f"the rule's own `layers` restriction ({allowed}), so the value "
              f"could never take effect")
        sys.exit(1)
    per_layer = rule.setdefault("per_layer", {})
    if lid in per_layer:
        print(f"Error: def_ndr_layer '{name}': layer '{args[1]}' already "
              f"declared — a duplicate is a hard error (def_ndr's "
              f"declare-once convention)")
        sys.exit(1)

    entry = {"width_x": 0.0, "width_abs": 0.0,
             "spacing_x": 0.0, "spacing_abs": 0.0}
    i = 2
    while i < len(args):
        tok = args[i].lower()
        if tok == "width" and i + 1 < len(args):
            # `_parse_value` returns (multiplier, absolute) with the
            # multiplier defaulting to 1.0 for the absolute form.  Per layer
            # we need a THIRD state -- not declared here at all -- so only
            # the form actually given is recorded; 0.0 means "inherit".
            mx, ab = _parse_value(args[i + 1], "width", name, session)
            if ab > 0.0: entry["width_abs"] = ab
            else:        entry["width_x"]   = mx
            i += 2
        elif tok == "spacing" and i + 1 < len(args):
            mx, ab = _parse_value(args[i + 1], "spacing", name, session)
            if ab > 0.0: entry["spacing_abs"] = ab
            else:        entry["spacing_x"]   = mx
            i += 2
        else:
            print(f"Error: def_ndr_layer '{name}': unknown token "
                  f"'{args[i]}' (width | spacing)")
            sys.exit(1)
    if not any(entry.values()):
        print(f"Error: def_ndr_layer '{name}': declares nothing for layer "
              f"'{args[1]}' — give a width and/or a spacing")
        sys.exit(1)

    # R3, at declaration: an ABSOLUTE value is quantized against this layer's
    # own geometry, so the layer must HAVE one, and the value must be
    # realizable on it.  Both are reported here rather than surfacing six
    # stages later as stranded bits.
    if entry["width_abs"] > 0.0 or entry["spacing_abs"] > 0.0:
        geom = (session.layers.ndr_geom(lid)
                if getattr(session, "layers", None) is not None else None)
        if geom is None or geom.empty():
            print(f"Error: def_ndr_layer '{name}': layer '{args[1]}' has no "
                  f"def_track_pattern, so an absolute value cannot be "
                  f"quantized against the grid it would route on — declare "
                  f"the pattern first")
            sys.exit(1)
        probe = buda.NdrSpec()
        probe.width_abs, probe.spacing_abs = entry["width_abs"], entry["spacing_abs"]
        # The parent rule's READING, and its pitch.  Left at the default the
        # probe took the CHANNEL branch with pitch 0, returned early and
        # reported ok=True unconditionally -- an advertised R3 check that
        # tested nothing (Codex P1 on #717).
        probe.metal_quant = bool(rule.get("metal", 0))
        _, ok = buda.ndr_resolve_on_layer(
            probe, lid, geom, ndr_layer_pitch(session, lid))
        if not ok:
            ceiling = buda.ndr_max_slots(geom)
            best = buda.ndr_metal_for_slots(geom, ceiling)
            print(f"Error: def_ndr_layer '{name}': layer '{args[1]}' cannot "
                  f"realize the declared value — its longest contiguous "
                  f"signal run is {ceiling} slot(s), delivering at most "
                  f"{best:g} unit(s) of metal (a wire may not span a power "
                  f"rail).  R3: the tool never silently degrades an NDR.")
            sys.exit(1)

    per_layer[lid] = entry
    # The conservative MAXIMUM is cached at first use and is a max over the
    # RESOLVED per-layer values, so a later override invalidates it.  Without
    # this the rule keeps reporting — and any consumer that cannot resolve
    # keeps charging — the maximum computed before this layer was declared,
    # which is no longer conservative.
    rule.pop("width_slots_max", None)
    rule.pop("guard_slots_max", None)
    _write_rule_through(session, name)
    what = []
    if entry["width_x"]:    what.append(f"width x{entry['width_x']:g}")
    if entry["width_abs"]:  what.append(f"width {entry['width_abs']:g}")
    if entry["spacing_x"]:  what.append(f"spacing x{entry['spacing_x']:g}")
    if entry["spacing_abs"]: what.append(f"spacing {entry['spacing_abs']:g}")
    print(f"[NDR] rule '{name}' on layer {args[1]}: {', '.join(what)}")


def cmd_set_ndr(session, cmd, args, cmd_line):
    # set_ndr <prefix>|* <rule|off>
    # Longest prefix wins; '*' = global default; 'off' clears one scope.
    if len(args) != 2:
        print("Error: usage: set_ndr <prefix>|* <rule|off>")
        return
    prefix, rule = args[0], args[1]
    scopes = _scopes(session)
    if not hasattr(session, "_ndr_scopes_typed"):
        session._ndr_scopes_typed = set()
    if rule.lower() == "off":
        scopes.pop(prefix, None)
        session._ndr_scopes_typed.discard(prefix)
        _write_scope_through(session, prefix, None)   # v21: delete the row
        print(f"[NDR] scope '{prefix}' cleared "
              f"({len(scopes)} scope(s) active)")
        return
    if rule not in _rules(session):
        print(f"Error: set_ndr: unknown rule '{rule}' (declare def_ndr "
              f"first)")
        sys.exit(1)
    scopes[prefix] = rule
    session._ndr_scopes_typed.add(prefix)
    _write_scope_through(session, prefix, rule)       # v21: persists
    print(f"[NDR] scope '{prefix}' -> rule '{rule}' ({len(scopes)} scope(s) "
          f"active; applies at the next run_bundler / run_hier_bundler)")


def _layer_id(session, tok):
    # Numeric id or declared layer name — the same resolver contract the
    # BDB layer-policy commands use.
    from buda_cmds.bdb_cmds import _resolve_layer_id
    return _resolve_layer_id(session, tok)


def _clear_imported_ndrs(session):
    """Drop the rules/scopes a PREVIOUS import_def_lef translated.

    `clear_design()` replaces the design tables but knows nothing of session
    NDR state, so a re-import in the same session used to hit `def_ndr`'s
    duplicate-rule hard error AFTER the old design was already gone (Codex
    P2 on #667).  Only what translation itself added is cleared — a
    user-typed declaration is never touched, except that a typed scope
    still naming a cleared imported rule cannot outlive the rule (a
    dangling scope resolves nets to a rule that no longer exists) and is
    dropped with a warning."""
    imp_rules = getattr(session, "_ndr_rules_imported", None) or set()
    imp_scopes = getattr(session, "_ndr_scopes_imported", None) or set()
    if not imp_rules and not imp_scopes:
        return
    rules, scopes = _rules(session), _scopes(session)
    for pfx in imp_scopes:
        if pfx in scopes:
            del scopes[pfx]
            _write_scope_through(session, pfx, None)
    for name in imp_rules:
        if name not in rules:
            continue
        del rules[name]
        for pfx in [p for p, rl in scopes.items() if rl == name]:
            print(f"Warning: [NDR] scope '{pfx}' names imported rule "
                  f"'{name}', which this re-import replaces — scope "
                  f"dropped (re-declare it against a current rule)")
            del scopes[pfx]
            if hasattr(session, "_ndr_scopes_typed"):
                session._ndr_scopes_typed.discard(pfx)
            _write_scope_through(session, pfx, None)
        if getattr(session, "bdb", None) is not None:
            session.bdb.delete_ndr_rule(name)
    session._ndr_rules_imported = set()
    session._ndr_scopes_imported = set()


def translate_def_ndrs(session, st, lef_path):
    """DEF NONDEFAULTRULES -> def_ndr/set_ndr (opens_interchange.md item 7).

    A translation job, not a lookup: DEF states ABSOLUTE per-layer widths in
    DBU, BUDA's rule model states MULTIPLIERS of each layer's default — so
    each rule's clauses are divided by the LEF defaults, and only a rule
    whose layers AGREE on the multiplier (within 1%) is expressible.  One
    that is not is skipped LOUD (BUDA-1613) with the per-layer values —
    approximating with a max would claim a rule the DEF never stated.

    Attachment is per NET NAME through set_ndr, whose scopes are PREFIXES
    (longest wins).  An attached name that is a strict prefix of another
    net's name would silently govern that net too, so the collision is
    reported when it exists.
    """
    # A re-import replaces the design, so it replaces the previous import's
    # translated rules too — BEFORE the empty-rules early-out, since the new
    # DEF having no rules of its own is exactly the case where the old
    # design's rules must not linger.
    _clear_imported_ndrs(session)
    if not st.ndrs:
        return
    try:
        lef = buda.read_lef(lef_path)
    except RuntimeError as e:
        buda_diag.emit("BUDA-1613",
                       f"cannot re-read the LEF for rule translation: {e}")
        return
    defaults = {l.name: (l.width, l.spacing) for l in lef.layers
                if l.type == "ROUTING"}
    units = st.def_units or 1.0
    translated = set()
    for r in st.ndrs:
        why = None
        wms, sms, layer_toks = [], [], []
        if not r.layers:
            why = "no LAYER clause"
        for L in (r.layers if not why else []):
            base = defaults.get(L.layer)
            if base is None or base[0] <= 0:
                why = f"layer {L.layer} has no LEF default WIDTH"; break
            if _layer_id(session, L.layer) is None:
                why = (f"layer {L.layer} is not declared "
                       f"(def_layer/import_lef_tech first)"); break
            layer_toks.append(L.layer)
            if L.width_dbu >= 0:
                wms.append(L.width_dbu / units / base[0])
            if L.spacing_dbu >= 0:
                if base[1] <= 0:
                    why = (f"layer {L.layer} states SPACING but the LEF has "
                           f"no default SPACING to scale against"); break
                sms.append(L.spacing_dbu / units / base[1])
        if not why:
            # A BUDA rule states ONE width and ONE spacing multiplier for
            # ALL its layers.  A DEF rule that states WIDTH or SPACING on
            # only SOME of its LAYER clauses leaves the rest at their
            # defaults — emitting the multiplier anyway would broaden the
            # rule onto layers the DEF never constrained, silently changing
            # capacity (Codex P1 on #667).  Not expressible, so refused.
            for vals, what in ((wms, "WIDTH"), (sms, "SPACING")):
                if vals and len(vals) != len(layer_toks):
                    why = (f"{what} is stated on only {len(vals)} of "
                           f"{len(layer_toks)} LAYER clauses — one "
                           f"multiplier per rule cannot leave the others "
                           f"at their default")
                    break
        if not why:
            for vals, what in ((wms, "WIDTH"), (sms, "SPACING")):
                if vals and (max(vals) - min(vals)) > 0.01 * max(vals):
                    why = (f"per-layer {what} multipliers disagree "
                           f"({', '.join(f'{v:g}' for v in vals)}) — one "
                           f"multiplier cannot state them")
                    break
        if not why and not wms and not sms:
            why = "states neither WIDTH nor SPACING"
        if not why and r.name in _rules(session):
            # The user typed a rule of this name in this session (an
            # imported one of the same name was cleared above).  Session-
            # typed wins, the restore path's convention — and def_ndr's
            # duplicate hard error must not kill the import.
            why = ("the name is already declared in this session — the "
                   "session declaration wins, the DEF's content is not "
                   "applied")
        if why:
            buda_diag.emit("BUDA-1613", f"rule '{r.name}': {why}")
            continue
        args = [r.name]
        if wms:
            args += ["width", f"x{wms[0]:.6g}"]
        if sms:
            args += ["spacing", f"x{sms[0]:.6g}"]
        args += ["layers", ",".join(layer_toks)]
        cmd_def_ndr(session, "def_ndr", args, "")
        translated.add(r.name)
        # Imported, not typed: tracked so the NEXT import replaces it, and
        # kept out of the typed set cmd_def_ndr stamps (typed is the
        # session-wins marker; a rule the design file supplied is not one
        # the user wrote).
        if not hasattr(session, "_ndr_rules_imported"):
            session._ndr_rules_imported = set()
        session._ndr_rules_imported.add(r.name)
        if hasattr(session, "_ndr_rules_typed"):
            session._ndr_rules_typed.discard(r.name)
        for u in r.unmodelled:
            print(f"[NDR] note: rule '{r.name}' clause '{u}' has no BUDA "
                  f"model — translated without it")
    if not st.net_ndrs:
        return
    all_nets = sorted(n.name for n in session.bdb.all_nets())
    for net, rule in st.net_ndrs:
        if rule not in translated:
            print(f"[NDR] note: net '{net}' asks for rule '{rule}', which "
                  f"was not translated — the net stays on default rules")
            continue
        shadows = [m for m in all_nets if m != net and m.startswith(net)]
        if shadows:
            shown = ", ".join(shadows[:4])
            more = f" (+{len(shadows)-4} more)" if len(shadows) > 4 else ""
            print(f"Warning: set_ndr scopes are PREFIXES — attaching "
                  f"'{rule}' to '{net}' also governs {shown}{more}")
        cmd_set_ndr(session, "set_ndr", [net, rule], "")
        if not hasattr(session, "_ndr_scopes_imported"):
            session._ndr_scopes_imported = set()
        session._ndr_scopes_imported.add(net)
        if hasattr(session, "_ndr_scopes_typed"):
            session._ndr_scopes_typed.discard(net)


def split_mixed_ndr_bundles(session, raw_bundles):
    """Rule-class split (phase 1 R8 position): a bundle whose nets resolve
    to DIFFERENT rules is split into rule-uniform parts, reported LOUD —
    constraints are never silently dropped or merged.  No scopes declared =
    input returned untouched (byte-identity)."""
    if not getattr(session, "_ndr_scopes", None):
        return raw_bundles
    out = []
    next_id = max((b.id for b in raw_bundles), default=0)
    for b in raw_bundles:
        nets = list(b.get_net_names())
        classes = {}
        for n in nets:
            classes.setdefault(ndr_rule_for_net(session, n), []).append(n)
        if len(classes) <= 1:
            out.append(b)
            continue
        sizes = "+".join(str(len(v)) for v in classes.values())
        names = ", ".join(str(k) for k in classes)
        print(f"[NDR] split bundle {b.id} ({len(nets)} bits) into "
              f"{len(classes)} rule-uniform part(s) ({sizes}): rules "
              f"[{names}] — a mixed-rule bundle cannot ride one spec in "
              f"phase 1")
        for k, (rule, part) in enumerate(sorted(
                classes.items(), key=lambda kv: (kv[0] is not None,
                                                 kv[0] or "")), start=1):
            nb = buda.HBundle()
            if k == 1:
                nb.id = b.id
            else:
                next_id += 1
                nb.id = next_id
            nb.net_names = part
            nb.reason = f"{b.reason}|NDR:{rule or 'default'}"
            nb.num_terminals = b.num_terminals
            out.append(nb)
    return out


def split_mixed_ndr_hbundles(session, raw_bundles):
    """Hier rule-class split (R2d): the `split_mixed_ndr_bundles` twin for
    run_hier_bundler, applied to TEMPLATE bundles BEFORE per-instance
    expansion so the split propagates identically to every occurrence —
    the `_split_hier_bundles` (set_max_bundle_bits) pattern: every HBundle
    hier field is preserved per part (`_clone_hbundle_with_id`), per-net
    fan-in arrays split in the same net partition (with the fan-in
    reason/busterm re-scope), and a cell-local template's REPLICAS split
    in LOCKSTEP with their template, parent_id rewired part-for-part.

    Occurrences of one cell-local class carry INSTANCE-SPECIFIC net names
    (`d1_mp3_u1_*` on the template, `d1_mp3_u2_*` on a replica), so the
    class is CONGRUENT only when every occurrence's nets partition into
    the SAME (rule, bit-index) classes — the template's routing is solved
    once and copied, so per-occurrence rule differences cannot ride one
    template.  A scope that governs the class inconsistently (e.g. naming
    only one instance's prefix) is a HARD ERROR naming the bundle and the
    diverging partitions — refusing beats silently splitting the class
    apart or routing one occurrence ungoverned.

    No scopes declared = input returned untouched (byte-identity)."""
    if not getattr(session, "_ndr_scopes", None):
        return raw_bundles
    from buda_cmds.bundling_cmds import _fanin_core

    counter = [max((b.id for b in raw_bundles), default=0)]

    def _new_id():
        counter[0] += 1
        return counter[0]

    def _classes(b):
        """The bundle's (rule, [net indices]) partition, stably ordered
        (default class first, then by rule name — the flat split's
        order)."""
        cls = {}
        for i, n in enumerate(b.get_net_names()):
            cls.setdefault(ndr_rule_for_net(session, n), []).append(i)
        return sorted(cls.items(), key=lambda kv: (kv[0] is not None,
                                                   kv[0] or ""))

    def _split_one(b, classes):
        nets = list(b.get_net_names())
        if len(classes) <= 1:
            return [b]
        drvs = list(b.net_drivers)
        rcvs = [list(r) for r in b.net_receivers]
        fanin = bool(drvs) and len(drvs) == len(nets)
        sizes = "+".join(str(len(idxs)) for _, idxs in classes)
        names = ", ".join(str(r or "default") for r, _ in classes)
        print(f"[NDR] split hbundle {b.id} ({len(nets)} bits) into "
              f"{len(classes)} rule-uniform part(s) ({sizes}): rules "
              f"[{names}] — a mixed-rule bundle cannot ride one spec")
        parts = []
        for k, (rule, idxs) in enumerate(classes, start=1):
            nb = session._clone_hbundle_with_id(
                b, b.id if k == 1 else _new_id())
            nb.net_names = [nets[i] for i in idxs]
            suffix = f"|NDR:{rule or 'default'}"
            if fanin:
                pd = [drvs[i] for i in idxs]
                pr = [rcvs[i] for i in idxs]
                nb.net_drivers = pd
                nb.net_receivers = pr
                core, uniq_drv, root, extra_rcv = _fanin_core(pd, pr)
                nb.reason = (core if core else b.reason) + suffix
                # Cell-local fan-in reads endpoints from the busterm ids —
                # re-scope them to the part's own leaves (the bit-cap
                # split's rule).
                if core and b.cell_context and b.entry_busterm_ids:
                    nb.entry_busterm_ids = ["bt:" + d for d in uniq_drv]
                    nb.exit_busterm_ids = (["bt:" + root]
                                           + ["bt:" + r for r in extra_rcv])
            else:
                nb.reason = b.reason + suffix
            parts.append(nb)
        return parts

    cell_ids = {b.id for b in raw_bundles if b.cell_context}

    def _is_replica(b):
        return bool(b.cell_context and b.parent_id in cell_ids
                    and b.instances)

    replicas_of = {}
    for b in raw_bundles:
        if _is_replica(b):
            replicas_of.setdefault(b.parent_id, []).append(b)

    out = []
    for b in raw_bundles:
        if _is_replica(b):
            continue                       # handled with its template
        reps = replicas_of.get(b.id, ())
        tcls = _classes(b)
        for rb in reps:
            rcls = _classes(rb)
            if ([(r, idxs) for r, idxs in rcls]
                    != [(r, idxs) for r, idxs in tcls]):
                tp = ", ".join(f"{r or 'default'}:{len(i)}"
                               for r, i in tcls)
                rp = ", ".join(f"{r or 'default'}:{len(i)}"
                               for r, i in rcls)
                print(f"Error: NDR scopes govern cell-level bundle class "
                      f"{b.id} INCONSISTENTLY across its occurrences: the "
                      f"template partitions as [{tp}] but occurrence "
                      f"bundle {rb.id} ({rb.instances[0] if rb.instances else '?'}) "
                      f"as [{rp}] — a cell-local template is solved once "
                      f"and copied, so every occurrence must resolve to "
                      f"the same rules on the same bits.  Scope the whole "
                      f"class (a prefix covering every instance's nets) "
                      f"or clear the per-instance scope (set_ndr <prefix> "
                      f"off).")
                sys.exit(1)
        tparts = _split_one(b, tcls)
        out.extend(tparts)
        for rb in reps:
            rparts = _split_one(rb, _classes(rb))
            for tp, rp in zip(tparts, rparts):
                rp.parent_id = tp.id       # part k replica → part k template
            out.extend(rparts)
    return out


def reapply_ndr_layer_restrictions(session, wrappers=None):
    """Intersect each governed wrapper's rule LAYER restriction into
    `allowed_layers`.  The hier layer-policy resolver (`
    _apply_layer_policies`) OWNS allowed_layers on hier wrappers — it
    clears or rewrites the mask at every wrapper-set transition (templates,
    expanded instances, restored sessions) — so the NDR restriction is
    RE-APPLIED after each resolution rather than assumed to survive it.
    An empty intersection (the rule's layers all outside the cell's band)
    is a hard error naming both constraints.  The gate is each WRAPPER's
    active spec, not the scope table: specs resolve at bundling and stay
    on the wrappers, so clearing the last scope AFTER run_hier_bundler
    must not strip the still-governed bundles' restrictions (Codex #626 —
    an empty mask means unrestricted routing).  No governed wrappers =
    a cheap no-op scan; the flat flow never passes through the resolver
    and keeps apply_ndr_specs' one-shot intersection."""
    if wrappers is None:
        wrappers = session.bundles
    for w in wrappers:
        spec = w.input.ndr
        if not spec.active():
            continue
        r = _rules(session).get(spec.rule_name)
        layers = r["layers"] if r else None
        if not layers:
            continue
        cur = list(w.input.allowed_layers)
        eff = sorted(set(layers) & set(cur)) if cur else sorted(layers)
        if cur and not eff:
            b = w.input.original_bundle
            print(f"Error: NDR rule '{spec.rule_name}' restricts bundle "
                  f"{b.id} to layers {sorted(layers)}, but the cell layer "
                  f"policy allows only {sorted(cur)} — the two constraints "
                  f"have no layer in common.  Widen the band "
                  f"(set_cell_layer_cap) or the rule's layers.")
            sys.exit(1)
        w.input.allowed_layers = eff


def apply_ndr_specs(session):
    """Stamp each wrapper's resolved rule onto BundleInput.ndr (post-split
    every bundle is rule-uniform) and intersect an explicit rule layer
    restriction into allowed_layers.  Inactive everywhere when no scope is
    declared (R12)."""
    if not getattr(session, "_ndr_scopes", None):
        return
    n_gov = 0
    for w in session.bundles:
        nets = list(w.input.original_bundle.get_net_names())
        rule = ndr_rule_for_net(session, nets[0]) if nets else None
        if rule is None:
            continue
        w.input.ndr = _spec_of(session, rule)
        n_gov += 1
        layers = _rules(session)[rule]["layers"]
        if layers:
            cur = list(w.input.allowed_layers)
            w.input.allowed_layers = sorted(
                set(layers) & set(cur)) if cur else sorted(layers)
    if n_gov:
        print(f"[NDR] {n_gov} bundle(s) governed by declared rules")


def validate_ndr_realizability(session):
    """R3 at first resolution: every governed bundle's rule must find a
    PHYSICALLY CONTIGUOUS run of width_slots SIGNAL slots on each layer it
    may be assigned (checked on the layer's global pattern over 3 tiled
    periods, so cross-period runs count).  Hard error naming the layer,
    the rule, and the arithmetic."""
    if not getattr(session, "_ndr_scopes", None):
        return
    from buda_cmds.bdb_cmds import _all_layer_ids
    grid = session.routing_grid
    checked = set()
    for w in session.bundles:
        spec = w.input.ndr
        if not spec.active():
            continue
        # An ABSOLUTE rule has no single width: it resolves per layer, so
        # the check must ask each layer for ITS OWN slot count.  Comparing
        # every layer against the conservative MAXIMUM would hard-error a
        # coarse layer whose per-layer width genuinely fits — a FALSE
        # REJECTION of a legal design, not a conservative charge (Codex P1
        # on #682).  A multiplier rule resolves to the same number on every
        # layer, so its behaviour is unchanged.
        if spec.width_slots <= 1 and not (spec.width_abs > 0):
            continue
        # Every layer this bundle MAY be assigned: the explicit restriction,
        # else ALL declared layers (the layer stack's real id set — a
        # hard-coded id range silently omitted user-defined ids outside it,
        # Codex on #616).
        restricted = list(w.input.allowed_layers)
        lids = restricted or _all_layer_ids(session)
        for lid in lids:
            # The pitch joins the key: with fractional layer SHARES two
            # bundles can resolve the same (rule, layer) differently, so
            # deduping on the pair alone would check one and skip the other.
            key = (spec.rule_name, lid, ndr_layer_pitch(session, lid, w))
            if key in checked:
                continue
            checked.add(key)
            if not grid.has_layer(lid):
                # No track pattern declared for this layer.  A rule
                # EXPLICITLY restricted to it can never be realized — R3
                # hard error.  An unrestricted rule falls through to the
                # engine's existing LOUD per-run strand ("Layer N has no
                # track pattern defined"), which hits every bundle equally
                # and is a flow-configuration issue, not a rule issue.
                if restricted:
                    print(f"Error: NDR rule '{spec.rule_name}' is restricted "
                          f"to layer {lid}, which has no track pattern "
                          f"(def_track_pattern) — the rule cannot be "
                          f"realized there (R3).")
                    sys.exit(1)
                continue
            # Per-layer width: the rule's own resolution against THIS
            # layer's pitch (identity for a multiplier rule).
            lspec = ndr_spec_for_layer(session, spec, lid, w)
            if lspec.width_slots <= 1:
                continue                   # fits in one slot on this layer
            pat = grid.get_layer_grid(lid).global_pattern()
            period = pat.unit_pitch()
            if period <= 0 or not pat.slots:
                continue
            tracks = pat.tracks_in_range(0.0, 3.0 * period)
            best_run, run = 0, 0
            prev = None
            for pos, slot in tracks:
                if slot.type != "SIGNAL":
                    prev = None
                    continue
                if prev is None:
                    run = 1
                else:
                    # physically adjacent iff no non-SIGNAL slot between —
                    # tracks_in_range enumerates every slot in order, so
                    # consecutive SIGNAL entries with no reset are adjacent.
                    run += 1
                prev = pos
                best_run = max(best_run, run)
            if best_run < lspec.width_slots:
                print(f"Error: NDR rule '{spec.rule_name}' needs "
                      f"{lspec.width_slots} physically contiguous SIGNAL "
                      f"slots per bit, but layer {lid}'s pattern offers "
                      f"runs of at most {best_run} — the rule is not "
                      f"realizable there (R3).  Restrict the rule with "
                      f"'layers', or re-declare the pattern.")
                sys.exit(1)


def _min_placed_widths(session):
    """{(bundle_id, layer): narrowest placed bit width} over the whole
    detailed result, built in ONE pass.

    `DetailedNUTSResult.net_segments` is a plain pybind vector property, so
    every access rebuilds the list — rescanning it per governed bundle is
    O(bundles x rows) with a copy on each, the cost `build_ndr_audit_index`
    exists to avoid.  Shield rows are excluded: they are the rule's own
    metal, not a governed bit."""
    dr = getattr(session, "detailed_result", None)
    if dr is None:
        return {}
    out = {}
    for ns in dr.net_segments:
        if ns.is_shield:
            continue
        key = (ns.bundle_id, ns.layer)
        cur = out.get(key)
        if cur is None or ns.width < cur:
            out[key] = ns.width
    return out


def _report_abs_metal(w, spec, metal):
    """After DNUTS, report an ABSOLUTE rule's DELIVERED metal beside the
    declared width, per layer, whenever it falls short.

    An absolute value is quantized by the per-signal-slot CHANNEL cost
    (`unit_pitch / n_signal_slots`), which answers "how much routing
    channel does this width consume" — the question the planner books.
    The metal a k-slot bit actually gets is a different quantity,
    `k*w + (k-1)*sp` over the layer's own slots.  The two coincide on some
    (pattern, declared value) pairs and not others, and nothing else in
    the flow says which case you are in: `check_design`'s NDR_WIDTH counts
    covered SIGNAL slot CENTRES, so it agrees with the channel reading by
    construction and stays silent here.

    Called for EVERY governed bundle, including one whose spec is
    INACTIVE.  That case is not empty, it is the worst one: a width that
    quantizes to a single slot with no guards or shields on every layer it
    can reach reads as "no rule", so the bus routes ungoverned and the
    shortfall is total.  The line therefore names its own bundle — no
    demand header precedes it there.

    Report-only, and printed only when the numbers disagree: the gap is a
    documented semantic limit (docs/internal/opens_ndr.md §2), not a
    violation.  Vehicle: flow/ndr_abs_divisor.buda.

    WIDTH only.  Declared SPACING has the same shape (a gap of g guards
    clears `(g+1)*sp + g*w`, not `g*bit_pitch`), but the delivered
    clearance is a property of neighbouring tracks rather than of a placed
    row, so it needs the run's geometry rather than one width — left to
    whoever settles the open, since a metal-shaped spacing rule and a
    metal-shaped spacing report have to arrive together."""
    if spec.width_abs <= 0.0 or not metal:
        return
    bid = w.input.original_bundle.id
    eps = 1e-9
    ungoverned = ("" if spec.active() else
                  ", and the spec reads INACTIVE there so the bus routes "
                  "UNGOVERNED")
    for (b, lid), got in sorted(metal.items()):
        if b != bid or got >= spec.width_abs - eps:
            continue
        print(f"[NDR]   bundle {bid} on layer {lid}: rule "
              f"'{spec.rule_name}' declares width {spec.width_abs:g} but "
              f"the placed bits are {got:g} wide — the value was "
              f"quantized by the layer's per-signal-slot CHANNEL cost, "
              f"which is not its metal{ungoverned} (opens_ndr.md §2)")


def cmd_dump_ndr(session, cmd, args, cmd_line):
    # dump_ndr — rules, quantization, scopes, and per-bundle governance.
    rules = getattr(session, "_ndr_rules", None) or {}
    scopes = getattr(session, "_ndr_scopes", None) or {}
    if not rules and not scopes:
        print("[NDR] no rules declared")
        return
    r_rest = getattr(session, "_ndr_rules_restored", None) or set()
    s_rest = getattr(session, "_ndr_scopes_restored", None) or set()
    for name, r in sorted(rules.items()):
        if r.get("width_abs", 0) > 0 or r.get("spacing_abs", 0) > 0:
            ensure_abs_quantization(session, r)   # restored-before-patterns
            ws, gs = r.get("width_slots_max", 1), r.get("guard_slots_max", 0)
        else:
            ws = max(1, math.ceil(r["width_x"]))
            gs = max(0, math.ceil(r["spacing_x"]) - 1)
        sh = {0: "none", 1: "bus", 2: "bit",
              3: f"per:{r['shield_per_n']}"}[r["shield_mode"]]
        lay = ("any" if r["layers"] is None
               else ",".join(str(l) for l in r["layers"]))
        src = "  (restored from BDB)" if name in r_rest else ""
        cr = " credit" if r.get("credit") else ""
        bo = ("" if not r.get("bond") else
              " bond" if r["bond"] == 1 else f" bond stride {r['bond']}")
        if r.get("width_abs", 0) > 0 or r.get("spacing_abs", 0) > 0:
            wd = (f"width {r['width_abs']:g}" if r.get("width_abs", 0) > 0
                  else f"width x{r['width_x']:g}")
            sd = (f"spacing {r['spacing_abs']:g}"
                  if r.get("spacing_abs", 0) > 0
                  else f"spacing x{r['spacing_x']:g}")
            print(f"[NDR] rule '{name}': {wd}, {sd} (ABSOLUTE) "
                  f"-> {ws} slot(s)/bit, {gs} guard(s)/gap charged (max "
                  f"over governed layers), shield {sh} net "
                  f"{r['shield_net']}{cr}{bo}, layers {lay}{src}")
        else:
            print(f"[NDR] rule '{name}': width x{r['width_x']:g} "
                  f"({ws} slot(s)/bit), spacing x{r['spacing_x']:g} "
                  f"({gs} guard(s)/gap), shield {sh} net {r['shield_net']}"
                  f"{cr}{bo}, layers {lay}{src}")
    for prefix, rule in sorted(scopes.items()):
        src = "  (restored from BDB)" if prefix in s_rest else ""
        print(f"[NDR] scope '{prefix}' -> '{rule}'{src}")
    if getattr(session, "bundles", None):
        # One pass over the detailed rows for the whole dump.  Per-bundle
        # rescans are O(bundles x rows) AND `net_segments` is a plain
        # pybind vector property, so each access rebuilds the list — the
        # cost build_ndr_audit_index exists to avoid (Codex P2 on #689).
        metal = _min_placed_widths(session)
        for w in session.bundles:
            spec = w.input.ndr
            nets = w.input.original_bundle.get_net_names()
            nb = len(nets)
            if spec.active():
                du = buda.ndr_group_demand(spec, nb)
                print(f"[NDR] bundle {w.input.original_bundle.id} "
                      f"('{nets[0] if nets else '?'}' x{nb}) rule "
                      f"'{spec.rule_name}': demand {du} slot(s) "
                      f"(layout {buda.ndr_run_layout(spec, nb)})")
                # R1: once layers are assigned, an ABSOLUTE rule's REAL
                # charge is per layer — report the ones that differ from
                # the maximum above, so a dump can never claim a demand
                # the engine did not place (silent on multiplier rules
                # and before planning).
                if spec.width_abs > 0.0 or spec.spacing_abs > 0.0:
                    seen = {}
                    for lid in getattr(w.plan, "seg_layers", ()) or ():
                        if lid < 0 or lid in seen:
                            continue
                        ls = ndr_spec_for_layer(session, spec, lid, w)
                        seen[lid] = buda.ndr_group_demand(ls, nb)
                    for lid in sorted(k for k, v in seen.items() if v != du):
                        print(f"[NDR]   on layer {lid}: demand {seen[lid]} "
                              f"slot(s) — the absolute width resolves to "
                              f"{ndr_spec_for_layer(session, spec, lid, w).width_slots}"
                              f" slot(s)/bit at that layer's pitch")
            # OUTSIDE the active() guard, deliberately: an absolute width
            # that quantizes to one slot with no guards or shields on
            # every layer it can reach IS an inactive spec — and that is
            # the WORST case, not an empty one.  Skipping it printed
            # nothing at all for the bundle with the largest shortfall
            # (Codex P2 on #689).
            _report_abs_metal(w, spec, metal)


COMMANDS = {
    "def_ndr":  cmd_def_ndr,
    "def_ndr_layer": cmd_def_ndr_layer,
    "set_ndr":  cmd_set_ndr,
    "dump_ndr": cmd_dump_ndr,
}


# ── R9 typed audit (NDR_WIDTH / NDR_SPACING / NDR_SHIELD) ──────────────────
# Defense-in-depth at the dnuts stage: phase-1 placement emits correct
# k-slot/guard/shield geometry BY CONSTRUCTION, so these checks exist to
# catch what construction cannot promise forever — a healer move, a hand
# edit, or the keepout cull removing a shield — and to give NDR designs a
# violation vocabulary beside BIT_SHORT/KEEPOUT_CROSS.  Report-only, typed,
# consumed by _check_design's normal summary machinery via the duck-typed
# violation shape (_FidelityViolation precedent).  No governed bundles =
# zero output (byte-identity).
#
# The shield NET-IDENTITY predicate (buda.ndr_shield_net_matches) ships
# here as the audit's contract piece; its in-audit consumer — verifying a
# CREDITED pattern rail really is the rule's shield net — arrives with R5a
# crediting (phase 2), which must use the same predicate by requirement.

class _NdrKind:
    def __init__(self, name):
        self.name = name


class _NdrViolation:
    """Duck-typed to the C++ ConnViolation interface the reporting paths
    consume (kind.name / seg indices / block_name / bit_index / message)."""
    seg_idx2 = -1
    block_name = ""

    def __init__(self, kind, seg_idx, bit_index, message):
        self.kind = _NdrKind(kind)
        self.seg_idx = seg_idx
        self.bit_index = bit_index
        self.message = message


# Rail slot types that are CLEARANCE-NEUTRAL inside a governed run: static
# supply/shield metal satisfies the clearance intent (the documented phase-1
# straddle model — a run's guard/shield gaps may cross these rails, which
# only ADD isolation).  Everything else (CLOCK, CUSTOM) is a potential
# AGGRESSOR whose presence inside the run the audit must flag: a switching
# rail is exactly the coupling the rule's clearance exists to exclude, and
# an unknown-identity CUSTOM rail cannot be verified quiet.
_NDR_NEUTRAL_RAILS = ("POWER", "GROUND", "SHIELD")


def build_ndr_audit_index(session):
    """Detailed rows materialized and indexed ONCE for a whole check_design
    pass (Codex #622): `dr.net_segments` is a pybind vector that converts to
    a fresh Python list on EVERY property access, and a per-segment scan
    over it made the audit quadratic in placed-row count.  One
    materialization, grouped by bundle (own-row lookup) and by layer sorted
    on track position with a parallel key array (foreign-wire lookup via
    bisect on the run window).  Also counts the R6 shield BOND straps per
    (bundle, seg, shield ordinal) — one more single pass over the via
    vector, skipped entirely when no strap was emitted.  None when no
    detailed result exists."""
    dr = session.detailed_result
    if dr is None:
        return None
    rows = list(dr.net_segments)          # the ONE pybind conversion
    by_bundle, by_layer = {}, {}
    for r in rows:
        by_bundle.setdefault(r.bundle_id, []).append(r)
        by_layer.setdefault(r.layer, []).append(r)
    layer_sorted = {}
    for lid, lst in by_layer.items():
        lst.sort(key=lambda r: r.track_position)
        layer_sorted[lid] = (lst, [r.track_position for r in lst])
    straps = {}
    if getattr(dr, "n_shield_bond_vias", 0) > 0:
        for v in dr.net_vias:             # negative to_seg = a bond strap
            if v.to_seg < 0:
                key = (v.bundle_id, v.from_seg, v.bit_index)
                straps[key] = straps.get(key, 0) + 1
    return {"by_bundle": by_bundle, "by_layer": layer_sorted,
            "straps": straps}


def _ndr_effective_period(g, mid, perp):
    """Unit pitch of the pattern actually governing a placed row: the
    EFFECTIVE pattern at the row's real coordinates (an override's pitch
    may exceed the global one — Codex #624: a global-period search window
    can miss an override rail the engine legitimately credited), mapped
    per layer orientation (the C11-02 rule).  Falls back to the global
    pattern's pitch."""
    pat = (g.effective_pattern_at(mid, perp) if g.is_horizontal()
           else g.effective_pattern_at(perp, mid))
    period = pat.unit_pitch() if pat.slots else 0.0
    if period <= 0:
        gp = g.global_pattern()
        period = gp.unit_pitch() if gp.slots else 0.0
    return period


def _ndr_end_credit(spec, grid_stack, layer, rows, s_lo, s_hi, low_end,
                    allow_gap=False):
    """True when the run's low/high END is legitimately RAIL-CREDITED
    (R5a): the outermost placed row is a signal bit whose immediately
    adjacent pattern slot outside the run — no empty SIGNAL slot between —
    is a rail matching the rule's shield identity (buda.ndr_rail_credits,
    THE predicate the DNUTS seat search used) whose metal covers the run's
    span.  The audit checks the PROPERTY, not the provenance: every end
    must either carry an emitted shield or be flanked by a matching rail,
    and an outermost-bit end with no matching rail fails back to the
    uncredited expectation — the missing-shield count check fires.

    `allow_gap` (set when the segment placed FEWER bits than intended —
    the keepout cull removed some): skip the no-empty-slot-between test,
    since the culled outer bit's now-empty slot would otherwise read as an
    intervening gap and turn a correctly credited end into a spurious
    NDR_SHIELD on top of the honest UNPLACED.  Under a culled run the
    inference is deliberately permissive — identity and span coverage
    still apply, and the cull itself is already LOUD."""
    if not spec.credit_shields or spec.shield_mode == 0:
        return False
    if grid_stack is None or not grid_stack.has_layer(layer):
        return False
    outer = (min if low_end else max)(rows, key=lambda r: r.track_position)
    if outer.is_shield:
        return False
    g = grid_stack.get_layer_grid(layer)
    mid = 0.5 * (s_lo + s_hi)
    period = _ndr_effective_period(g, mid, outer.track_position)
    if period <= 0:
        return False
    eps = 1e-6
    edge = outer.track_position + (-0.5 if low_end else 0.5) * outer.width
    win = (edge - period, edge - eps) if low_end else (edge + eps,
                                                       edge + period)
    rails = grid_stack.preroutes(layer, win[0], win[1], s_lo, s_hi)
    if not rails:
        return False
    pos = (max if low_end else min)(r.track_position for r in rails)
    lo, hi = (pos + eps, edge - eps) if low_end else (edge + eps, pos - eps)
    if not allow_gap and lo < hi and g.signal_tracks_in(mid, lo, hi):
        return False                     # an empty signal slot intervenes
    pieces = [r for r in rails if abs(r.track_position - pos) < eps]
    if not buda.ndr_rail_credits(spec, pieces[0].label, pieces[0].slot_type):
        return False
    cov = s_lo                           # keepout-broken rail = no credit
    for a, b in sorted((min(p.span_lo, p.span_hi),
                        max(p.span_lo, p.span_hi)) for p in pieces):
        if a > cov + eps:
            return False
        cov = max(cov, b)
    return cov >= s_hi - eps


def audit_ndr_dnuts(session, wrapper, index=None):
    """R9 violations for ONE governed wrapper against the placed detailed
    result.  Checks per governed segment (rows grouped by seg_idx):

    - NDR_SHIELD: the placed shield count must match the rule's layout for
      the segment's INTENDED membership (`_seg_member_bits` — the same
      accounting DNUTS admission uses), not the surviving placed bits: a
      keepout-culled SIGNAL bit is already reported UNPLACED and must not
      re-shape the expected shield arrangement into a spurious mismatch,
      while a culled/skipped SHIELD is LOUD, not silent.  On a FULL run
      (no cull) the ARRANGEMENT is checked too, for every mode: the placed
      rows in ascending track order must play the roles the credited layout
      declares, so a right-COUNT shield sitting in the wrong gap is caught
      — the case `bit` and `per:N` were blind to before (opens_ndr.md).
    - NDR_WIDTH: measured in the reading the rule was WRITTEN in.  A
      METAL rule (`def_ndr ... metal`) compares each bit's placed METAL
      against the width DECLARED on its layer — an independent quantity,
      so the check can actually fail.  A CHANNEL rule compares covered
      SIGNAL slot centres against `width_slots`; that is the right
      quantity for a consumption claim, but note both sides come from the
      same quantization, so it agrees by construction and catches only a
      placement that ignored the spec outright (a default-width placement
      of a governed bit).  Auditing a metal rule that way would inherit
      that blindness on exactly the rules written for EM and resistance.
    - NDR_SPACING: the group's claimed run is EXCLUSIVE — a foreign wire
      (another bundle) whose track centre falls inside it with an
      overlapping span sits on a guard or between bits, violating the
      rule's clearance.  For an unshielded rule the audited run extends
      over the END GUARD slots beyond the outermost placed wires (the
      layout reserves them, so metal there is just as foreign), and
      AGGRESSOR pre-routes (CLOCK/CUSTOM pattern rails) inside the run are
      flagged too — static supply/shield rails stay exempt per the
      documented straddle-neutral phase-1 model (`_NDR_NEUTRAL_RAILS`).

    `index` is a `build_ndr_audit_index` result shared across wrappers by
    `_check_design`; built on the fly when omitted.
    """
    base_spec = wrapper.input.ndr
    dr = session.detailed_result
    if not base_spec.active() or dr is None:
        return []
    if index is None:
        index = build_ndr_audit_index(session)
    bid = wrapper.input.original_bundle.id
    by_seg = {}
    for ns in index["by_bundle"].get(bid, ()):
        by_seg.setdefault(ns.seg_idx, []).append(ns)
    out = []
    grid_stack = session.routing_grid
    sel = wrapper.plan.selected_topology_index
    for seg_idx, rows in sorted(by_seg.items()):
        bits    = [r for r in rows if not r.is_shield]
        shields = [r for r in rows if r.is_shield]
        if not bits:
            continue                      # fully stranded: UNPLACED covers it
        nb_intended = len(bits)
        if sel >= 0:
            nb_intended = max(nb_intended,
                              session._seg_member_bits(wrapper, sel, seg_idx))
        seg_lo = min(min(r.span_lo, r.span_hi) for r in rows)
        seg_hi = max(max(r.span_lo, r.span_hi) for r in rows)
        seg_layer = rows[0].layer
        # R1: audit against the rule as it resolves on the layer this
        # segment was PLACED on — the same quantization make_bus_segments
        # handed the engine.  Auditing the rule's conservative MAXIMUM
        # instead would report every correctly-placed governed bit on a
        # coarser layer as NDR_WIDTH: the maximum is a charging basis, not
        # a geometric requirement (identity for a multiplier rule).
        spec = ndr_spec_for_layer(session, base_spec, seg_layer, wrapper)
        if not spec.active():
            continue          # the rule is the default width on this layer
        # R5a: a credit-enabled rule's END may be rail-credited — expected
        # emission drops that end's 'S'.  _ndr_end_credit verifies the
        # rail's identity + coverage, so a bit-at-the-end run with NO
        # matching rail keeps the uncredited expectation and fails LOUD.
        # A culled run (fewer placed bits than intended) relaxes the
        # no-gap adjacency test — the culled bit's empty slot must not
        # turn a correctly credited end into a spurious NDR_SHIELD.
        gap_ok = len(bits) < nb_intended
        c_lo = _ndr_end_credit(spec, grid_stack, seg_layer, rows,
                               seg_lo, seg_hi, True, allow_gap=gap_ok)
        c_hi = _ndr_end_credit(spec, grid_stack, seg_layer, rows,
                               seg_lo, seg_hi, False, allow_gap=gap_ok)
        layout = buda.ndr_run_layout_credited(spec, nb_intended, c_lo, c_hi)
        exp_shields = layout.count("S")
        credited = (" (%d end(s) rail-credited)" % (c_lo + c_hi)
                    if (c_lo or c_hi) else "")
        if len(shields) != exp_shields:
            out.append(_NdrViolation(
                "NDR_SHIELD", seg_idx, -1,
                f"NDR_SHIELD: seg {seg_idx} rule '{spec.rule_name}' expects "
                f"{exp_shields} shield wire(s) (net {spec.shield_net})"
                f"{credited} but {len(shields)} are placed — the bus is "
                f"not shielded as declared"))
        elif len(bits) == nb_intended:
            # ARRANGEMENT: the placed rows, read in ascending track order,
            # must play the roles the layout declares.  The layout is the
            # run slot by slot ('B' a bit's first slot, 'b' a continuation
            # slot of the same wire, 'S' a shield, 'G' a reserved-empty
            # guard), so the rows it predicts are its 'B' and 'S' entries in
            # order — one row each, 'b'/'G' materializing none.  Comparing
            # that against the sorted rows' is_shield flags catches a
            # right-COUNT shield sitting in the wrong gap, which the count
            # test above cannot see: under `bit` and `per:N` a shield
            # swapped with an adjacent bit keeps every total intact
            # (opens_ndr.md, raised in review of #636).  For `shield bus`
            # this subsumes the old outermost-wires check exactly — its
            # layout is S…S, so role 0 and role -1 are the shields.
            #
            # Guarded on a FULL run: a keepout cull removes bits, and the
            # surviving rows then legitimately fail a positional match
            # against the intended layout.  The cull is already LOUD as
            # UNPLACED, and the count test above stays on intended
            # membership either way.
            expect = [ch for ch in layout if ch in "BS"]
            placed = sorted(rows, key=lambda r: r.track_position)
            if len(expect) == len(placed):
                bad = next((i for i, (ch, r) in enumerate(zip(expect, placed))
                            if (ch == "S") != r.is_shield), -1)
                if bad >= 0:
                    got = "".join("S" if r.is_shield else "B" for r in placed)
                    out.append(_NdrViolation(
                        "NDR_SHIELD", seg_idx, -1,
                        f"NDR_SHIELD: seg {seg_idx} rule '{spec.rule_name}' "
                        f"expects the run to read {''.join(expect)} in "
                        f"ascending track order but it reads {got} (first "
                        f"mismatch at position {bad}){credited} — the "
                        f"shields are placed, but not in the gaps the rule "
                        f"declares"))
        # R6 bonding: every EMITTED shield of a `bond` rule must carry at
        # least one strap to the power grid.  A shield with none is metal
        # at the right track and the right net NAME with nothing tying it
        # to that net — the floating-shield failure the opt-in exists to
        # rule out (docs/internal/opens_ndr.md §1).  Not a placement fault:
        # it means no identity-matching rail crosses the shield on an
        # adjacent perpendicular layer, so the fix is the grid, not the
        # route.  (A CREDITED end emits no shield and needs no strap — it
        # never reaches this loop.)
        if spec.bond_stride > 0:
            for sh in shields:
                if index["straps"].get((bid, seg_idx, sh.bit_index), 0):
                    continue
                out.append(_NdrViolation(
                    "NDR_BOND", seg_idx, sh.bit_index,
                    f"NDR_BOND: seg {seg_idx} shield {-sh.bit_index} at "
                    f"track {sh.track_position:g} (net {spec.shield_net}, "
                    f"rule '{spec.rule_name}') has NO bond via — no "
                    f"identity-matching rail crosses it on an adjacent "
                    f"perpendicular layer, so the shield is floating "
                    f"metal, not grid-tied"))
        # Width.  TWO readings, and the audit must use the one the rule was
        # written in — otherwise it measures the placement against a number
        # derived from the same quantization the placement used, and agrees
        # by construction.
        #
        # METAL rule: compare the placed METAL against the DECLARED width on
        # that layer.  The declaration is an INDEPENDENT quantity — it is
        # what the user asked for, not what the tool computed — so this check
        # can actually fail, which is the whole point of an audit written for
        # EM and resistance rules.
        for r in bits:
            declared = (buda.ndr_declared_width_on(spec, r.layer)
                        if spec.metal_quant else 0.0)
            if declared <= 0.0:
                continue
            if r.width + 1e-9 < declared:
                out.append(_NdrViolation(
                    "NDR_WIDTH", seg_idx, r.bit_index,
                    f"NDR_WIDTH: seg {seg_idx} bit {r.bit_index} on layer "
                    f"{r.layer} is {r.width:g} unit(s) of metal, rule "
                    f"'{spec.rule_name}' declares {declared:g} — "
                    f"under-width wire"))
        # CHANNEL rule (the default): the slot-count reading, unchanged.  A
        # channel-declared width is a claim about CONSUMPTION, so covered
        # slot centres is the right quantity for it.
        if spec.width_slots > 1 and grid_stack is not None and not spec.metal_quant:
            for r in bits:
                if not grid_stack.has_layer(r.layer):
                    continue
                g = grid_stack.get_layer_grid(r.layer)
                mid = 0.5 * (r.span_lo + r.span_hi)
                eps = 1e-6
                covered = len(g.signal_tracks_in(
                    mid, r.track_position - r.width / 2.0 - eps,
                    r.track_position + r.width / 2.0 + eps))
                if covered < spec.width_slots:
                    out.append(_NdrViolation(
                        "NDR_WIDTH", seg_idx, r.bit_index,
                        f"NDR_WIDTH: seg {seg_idx} bit {r.bit_index} covers "
                        f"{covered} SIGNAL slot(s), rule '{spec.rule_name}' "
                        f"requires {spec.width_slots} — under-width wire"))
        # Spacing: the run is exclusive — no foreign wire inside it.
        run_lo = min(r.track_position - r.width / 2.0 for r in rows)
        run_hi = max(r.track_position + r.width / 2.0 for r in rows)
        s_lo, s_hi, layer = seg_lo, seg_hi, seg_layer
        have_grid = grid_stack is not None and grid_stack.has_layer(layer)
        # An UNSHIELDED rule's layout reserves guard_slots SIGNAL slots
        # beyond the outermost wires too (the run's end guards) — the
        # placed rows alone stop at the outer bit edges, so extend the
        # audited extent over the nearest guard_slots signal slot centres
        # on each side (a shielded rule's ends ARE placed rows already).
        if spec.shield_mode == 0 and spec.guard_slots > 0 and have_grid:
            g = grid_stack.get_layer_grid(layer)
            mid = 0.5 * (s_lo + s_hi)
            period = _ndr_effective_period(
                g, mid, 0.5 * (run_lo + run_hi))
            if period > 0:
                reach = (spec.guard_slots + 1) * period
                eps = 1e-9
                below = g.signal_tracks_in(mid, run_lo - reach, run_lo - eps)
                if below:
                    pos, slot = below[max(0, len(below) - spec.guard_slots)]
                    run_lo = min(run_lo, pos - slot.width / 2.0)
                above = g.signal_tracks_in(mid, run_hi + eps, run_hi + reach)
                if above:
                    pos, slot = above[min(len(above), spec.guard_slots) - 1]
                    run_hi = max(run_hi, pos + slot.width / 2.0)
        # Foreign detailed wires, via the shared by-layer index (bisect on
        # the sorted track positions keeps this local to the run window).
        lst, keys = index["by_layer"].get(layer, ((), ()))
        i0 = bisect.bisect_right(keys, run_lo)
        i1 = bisect.bisect_left(keys, run_hi)
        for o in lst[i0:i1]:
            if o.bundle_id == bid:
                continue
            o_lo, o_hi = min(o.span_lo, o.span_hi), max(o.span_lo, o.span_hi)
            if o_lo < s_hi and o_hi > s_lo:
                out.append(_NdrViolation(
                    "NDR_SPACING", seg_idx, o.bit_index,
                    f"NDR_SPACING: bundle {o.bundle_id} bit {o.bit_index} "
                    f"sits at track {o.track_position:g} INSIDE bundle "
                    f"{bid} seg {seg_idx}'s reserved run "
                    f"[{run_lo:g}..{run_hi:g}] (rule '{spec.rule_name}') — "
                    f"the rule's clearance is violated"))
        # Aggressor pre-routes: a CLOCK (or unknown-identity CUSTOM) rail
        # whose track runs inside the reserved window is fixed metal the
        # detailed rows can never show — the pre-routes-vs-spacing case of
        # R6.  Static supply/shield rails are exempt (straddle-neutral).
        if have_grid:
            for pr in grid_stack.preroutes(layer, run_lo, run_hi, s_lo, s_hi):
                if pr.slot_type in _NDR_NEUTRAL_RAILS:
                    continue
                if not (run_lo < pr.track_position < run_hi):
                    continue
                out.append(_NdrViolation(
                    "NDR_SPACING", seg_idx, -1,
                    f"NDR_SPACING: {pr.slot_type} pre-route "
                    f"'{pr.label}' at track {pr.track_position:g} runs "
                    f"INSIDE bundle {bid} seg {seg_idx}'s reserved run "
                    f"[{run_lo:g}..{run_hi:g}] (rule '{spec.rule_name}') — "
                    f"a switching rail is an aggressor the rule's "
                    f"clearance must exclude"))
    return out
