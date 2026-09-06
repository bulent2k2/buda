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

"""`set_bus_layers` — which layers a NAMED BUS may route on.

The gap this closes was measured (docs/internal/librelane_hier_flow.md §8
step 3b, 2026-09-06).  The phase-0 toy plans a 32-bit bus between two
hardened macros and writes the block's pin template where each bit LANDS.
Once the macros were moved onto the track period, the planner moved that
bus from met3 to met1 — 4x cheaper per bit under the declared patterns —
and the template's 64 pins followed it off the layer the block-side
handoff is built around.  A 0.8 um placement change was enough to flip it,
and the block's own wire went +26 % -> +49 % as a result.

`TOP` is a PREFERENCE the cost function can outvote, and the two existing
layer constraints do not reach this case: `set_cell_layer_cap` is per CELL
(it governs everything that cell routes, not one bus), and an NDR rule's
`layers` restriction rides on a rule that must also constrain width,
spacing or shielding — `def_ndr` refuses one that "constrains nothing".
So a bus with a required layer had no way to say so.

    set_bus_layers <prefix>|* <layer csv>     # e.g. `set_bus_layers mid met3`
    set_bus_layers <prefix>|* off

Longest matching prefix wins and `*` is the global default, outranked by
any real prefix — the same resolution `set_bundling` and `set_ndr` use, so
a design's three prefix-keyed knobs agree about which net they mean.

The restriction is an INTERSECTION with whatever else governs the bundle
(a cell band, an NDR rule's layers), applied wherever those are — at
bundling for the flat flow, and re-applied after every hier policy
resolution, because `_apply_layer_policies` OWNS `allowed_layers` and
rewrites it at each wrapper-set transition.  An empty intersection is a
hard error naming both constraints rather than a silently unrestricted
bundle: an empty mask means "route anywhere", which is the opposite of
what every one of these knobs was asked for.
"""
import sys


def scopes(session):
    if not hasattr(session, "_bus_layer_scopes"):
        session._bus_layer_scopes = {}
    return session._bus_layer_scopes


def layers_for_net(session, net_name):
    """(prefix, [layer ids]) governing a net, or (None, None).

    Longest matching prefix wins; `*` is outranked by any real prefix."""
    sc = getattr(session, "_bus_layer_scopes", None)
    if not sc:
        return (None, None)
    best = None
    for prefix in sc:
        if prefix == "*":
            continue
        if net_name.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None and "*" in sc:
        best = "*"
    return (best, sc[best]) if best is not None else (None, None)


def _bundle_scope(session, wrapper):
    """The layers governing a BUNDLE: `(prefix, layers)` or `(None, None)`.

    A bundle routes as ONE bus, so every net in it must resolve to the SAME
    restriction.  That one rule covers both ways it can fail (Codex #889):

      * two prefixes naming DIFFERENT layers — the bits cannot take both;
      * a governed net bundled with an UNGOVERNED one, which STRICT does
        whenever two buses share a driver and receiver set.  Skipping the
        ungoverned peer would force it onto the named bus's layers although
        no prefix matched it — silently rerouting a bus nobody scoped.

    Two prefixes naming the SAME layers are fine, since the restriction is
    satisfiable and that is exactly what the refusal's own remedy asks for.
    """
    masks = {}
    for net in wrapper.input.original_bundle.get_net_names():
        prefix, layers = layers_for_net(session, net)
        key = tuple(layers) if layers else None
        masks.setdefault(key, (prefix, net))
    if len(masks) == 1:
        key, (prefix, _net) = next(iter(masks.items()))
        return (prefix, list(key)) if key is not None else (None, None)
    b = wrapper.input.original_bundle
    free = masks.pop(None, None)
    named = ", ".join(f"'{p}' -> {list(k)} ({n})"
                      for k, (p, n) in sorted(masks.items(),
                                              key=lambda kv: str(kv[1][0])))
    if free is not None:
        print(f"Error: set_bus_layers: bundle {b.id} mixes governed and "
              f"ungoverned nets — {named}, while {free[1]} matches no scope "
              f"— but a bundle routes as ONE bus, so restricting it would "
              f"move a bus nobody scoped.  Scope the other net too, widen "
              f"the scope to `*`, or keep them apart (set_bundling).")
    else:
        print(f"Error: set_bus_layers: bundle {b.id} carries nets governed "
              f"by scopes with DIFFERENT layers — {named} — but a bundle "
              f"routes as ONE bus, so its bits cannot take different "
              f"layers.  Give the scopes the same layers, or keep the nets "
              f"apart (set_bundling).")
    sys.exit(1)


def apply_bus_layer_restrictions(session, wrappers=None):
    """Intersect each governed bundle's bus-layer restriction into
    `allowed_layers`, and RESTORE a bundle that is no longer governed.

    Idempotent and re-runnable, which it has to be: a scope declared (or
    cleared) after `run_bundler` reaches the wrappers only because the
    planner re-applies this (Codex #889), and re-applying must not
    intersect an already-intersected mask with itself forever.  So the
    mask each bundle had BEFORE any bus-layer restriction is remembered
    (`_bus_layer_base`, keyed by bundle id) and every application starts
    from it — the base being whatever else governs the bundle, a cell band
    or an NDR rule's layers."""
    wrappers_given = wrappers is not None
    if wrappers is None:
        wrappers = getattr(session, "bundles", []) or []
    base = getattr(session, "_bus_layer_base", None)
    if base is None:
        base = session._bus_layer_base = {}
    if not getattr(session, "_bus_layer_scopes", None) and not base:
        return 0
    n = 0
    for w in wrappers:
        bid = w.input.original_bundle.id
        prefix, layers = _bundle_scope(session, w)
        if bid not in base and layers:
            base[bid] = list(w.input.allowed_layers)
        start = base.get(bid, list(w.input.allowed_layers))
        if not layers:
            # No longer governed: give back what governed it before.
            if bid in base:
                w.input.allowed_layers = base.pop(bid)
            continue
        eff = sorted(set(layers) & set(start)) if start else sorted(layers)
        if start and not eff:
            b = w.input.original_bundle
            print(f"Error: set_bus_layers '{prefix}' restricts bundle {b.id} "
                  f"to layers {sorted(layers)}, but what already governs it "
                  f"allows only {sorted(start)} — the two have no layer in "
                  f"common.  Widen one of them.")
            sys.exit(1)
        w.input.allowed_layers = eff
        n += 1
    if not wrappers_given and not getattr(session, "_bus_layer_scopes", None):
        base.clear()
    return n
