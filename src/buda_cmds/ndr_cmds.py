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
import math
import sys

import buda


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
        }
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
    ws = max(1, math.ceil(r["width_x"]))
    gs = max(0, math.ceil(r["spacing_x"]) - 1)
    lay = ",".join(str(l) for l in (r["layers"] or []))
    return (f"{rule_name}|w{ws}|g{gs}|s{r['shield_mode']}"
            f"|p{r['shield_per_n']}|n{r['shield_net']}|L{lay}")


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
    spec.width_slots  = max(1, math.ceil(r["width_x"]))
    spec.guard_slots  = max(0, math.ceil(r["spacing_x"]) - 1)
    spec.shield_mode  = r["shield_mode"]
    spec.shield_per_n = r["shield_per_n"]
    spec.shield_net   = r["shield_net"]
    return spec


def _parse_x(tok, what, name):
    """Parse the multiplier form `xN` (phase 1 accepts only this form)."""
    if not tok.lower().startswith("x"):
        print(f"Error: def_ndr '{name}': {what} must be the multiplier "
              f"form xN in phase 1 (got '{tok}'; absolute um values need "
              f"per-layer slot geometry and are a later phase)")
        sys.exit(1)
    try:
        v = float(tok[1:])
    except ValueError:
        v = -1.0
    if v < 1.0:
        print(f"Error: def_ndr '{name}': {what} multiplier must be >= 1 "
              f"(got '{tok}')")
        sys.exit(1)
    return v


def cmd_def_ndr(session, cmd, args, cmd_line):
    # def_ndr <name> [width x<N>] [spacing x<N>]
    #                [shield bus|bit|per:<N> [net <label>]] [layers <csv>]
    # Declare-once (duplicate = hard error), unknown token = hard error,
    # and a rule that constrains nothing is refused — the def_layer /
    # def_track_pattern LOUD-declaration convention.
    if not args:
        print("Error: usage: def_ndr <name> [width x<N>] [spacing x<N>] "
              "[shield bus|bit|per:<N> [net <label>]] [layers <csv>]")
        return
    name = args[0]
    if name in _rules(session):
        print(f"Error: NDR rule '{name}' already declared — a duplicate "
              f"def_ndr is a hard error")
        sys.exit(1)
    rule = {"width_x": 1.0, "spacing_x": 1.0, "shield_mode": 0,
            "shield_per_n": 0, "shield_net": "GND", "layers": None}
    i = 1
    while i < len(args):
        tok = args[i].lower()
        if tok == "width" and i + 1 < len(args):
            rule["width_x"] = _parse_x(args[i + 1], "width", name); i += 2
        elif tok == "spacing" and i + 1 < len(args):
            rule["spacing_x"] = _parse_x(args[i + 1], "spacing", name); i += 2
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
        else:
            print(f"Error: def_ndr '{name}': unknown or incomplete token "
                  f"'{args[i]}' — a typo would silently weaken the rule")
            sys.exit(1)
    spec_probe = (rule["width_x"] > 1.0 or rule["spacing_x"] > 1.0
                  or rule["shield_mode"] != 0)
    if not spec_probe:
        print(f"Error: def_ndr '{name}' constrains nothing (default width, "
              f"spacing, no shield) — declare at least one constraint")
        sys.exit(1)
    _rules(session)[name] = rule
    if not hasattr(session, "_ndr_rules_typed"):
        session._ndr_rules_typed = set()
    session._ndr_rules_typed.add(name)
    _write_rule_through(session, name)       # v21: persists when a BDB is open
    ws = max(1, math.ceil(rule["width_x"]))
    gs = max(0, math.ceil(rule["spacing_x"]) - 1)
    sh = {0: "none", 1: "bus", 2: "bit", 3: f"per:{rule['shield_per_n']}"}[
        rule["shield_mode"]]
    lay = ("any" if rule["layers"] is None
           else ",".join(str(l) for l in rule["layers"]))
    print(f"[NDR] rule '{name}': width x{rule['width_x']:g} -> {ws} slot(s)/"
          f"bit, spacing x{rule['spacing_x']:g} -> {gs} guard slot(s)/gap, "
          f"shield {sh} (net {rule['shield_net']}), layers {lay}")


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
          f"active; applies at the next run_bundler)")


def _layer_id(session, tok):
    # Numeric id or declared layer name — the same resolver contract the
    # BDB layer-policy commands use.
    from buda_cmds.bdb_cmds import _resolve_layer_id
    return _resolve_layer_id(session, tok)


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
        if not spec.active() or spec.width_slots <= 1:
            continue
        # Every layer this bundle MAY be assigned: the explicit restriction,
        # else ALL declared layers (the layer stack's real id set — a
        # hard-coded id range silently omitted user-defined ids outside it,
        # Codex on #616).
        restricted = list(w.input.allowed_layers)
        lids = restricted or _all_layer_ids(session)
        for lid in lids:
            key = (spec.rule_name, lid)
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
            if best_run < spec.width_slots:
                print(f"Error: NDR rule '{spec.rule_name}' needs "
                      f"{spec.width_slots} physically contiguous SIGNAL "
                      f"slots per bit, but layer {lid}'s pattern offers "
                      f"runs of at most {best_run} — the rule is not "
                      f"realizable there (R3).  Restrict the rule with "
                      f"'layers', or re-declare the pattern.")
                sys.exit(1)


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
        ws = max(1, math.ceil(r["width_x"]))
        gs = max(0, math.ceil(r["spacing_x"]) - 1)
        sh = {0: "none", 1: "bus", 2: "bit",
              3: f"per:{r['shield_per_n']}"}[r["shield_mode"]]
        lay = ("any" if r["layers"] is None
               else ",".join(str(l) for l in r["layers"]))
        src = "  (restored from BDB)" if name in r_rest else ""
        print(f"[NDR] rule '{name}': width x{r['width_x']:g} "
              f"({ws} slot(s)/bit), spacing x{r['spacing_x']:g} "
              f"({gs} guard(s)/gap), shield {sh} net {r['shield_net']}, "
              f"layers {lay}{src}")
    for prefix, rule in sorted(scopes.items()):
        src = "  (restored from BDB)" if prefix in s_rest else ""
        print(f"[NDR] scope '{prefix}' -> '{rule}'{src}")
    if getattr(session, "bundles", None):
        for w in session.bundles:
            spec = w.input.ndr
            if not spec.active():
                continue
            nets = w.input.original_bundle.get_net_names()
            nb = len(nets)
            du = buda.ndr_group_demand(spec, nb)
            print(f"[NDR] bundle {w.input.original_bundle.id} "
                  f"('{nets[0] if nets else '?'}' x{nb}) rule "
                  f"'{spec.rule_name}': demand {du} slot(s) "
                  f"(layout {buda.ndr_run_layout(spec, nb)})")


COMMANDS = {
    "def_ndr":  cmd_def_ndr,
    "set_ndr":  cmd_set_ndr,
    "dump_ndr": cmd_dump_ndr,
}
