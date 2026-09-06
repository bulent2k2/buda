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
    """The scope governing a BUNDLE: its nets must agree, since a bundle is
    routed as one bus.  Returns (prefix, layers) or (None, None); a bundle
    whose nets resolve to DIFFERENT scopes is a hard error — the bundler
    put them on one bus, so a per-net answer cannot be honoured."""
    seen = {}
    for net in wrapper.input.original_bundle.get_net_names():
        prefix, layers = layers_for_net(session, net)
        if prefix is None:
            continue
        seen.setdefault(prefix, (layers, net))
    if not seen:
        return (None, None)
    if len(seen) > 1:
        b = wrapper.input.original_bundle
        parts = ", ".join(f"'{p}' ({seen[p][1]})" for p in sorted(seen))
        print(f"Error: set_bus_layers: bundle {b.id} carries nets governed by "
              f"different scopes — {parts} — but a bundle routes as ONE bus, "
              f"so its bits cannot take different layers.  Give the scopes "
              f"the same layers, or keep the nets apart (set_bundling).")
        sys.exit(1)
    prefix = next(iter(seen))
    return (prefix, seen[prefix][0])


def apply_bus_layer_restrictions(session, wrappers=None):
    """Intersect each governed bundle's bus-layer restriction into
    `allowed_layers`.  Idempotent; a no-op scan when nothing is declared."""
    if not getattr(session, "_bus_layer_scopes", None):
        return 0
    if wrappers is None:
        wrappers = getattr(session, "bundles", []) or []
    n = 0
    for w in wrappers:
        prefix, layers = _bundle_scope(session, w)
        if not layers:
            continue
        cur = list(w.input.allowed_layers)
        eff = sorted(set(layers) & set(cur)) if cur else sorted(layers)
        if cur and not eff:
            b = w.input.original_bundle
            print(f"Error: set_bus_layers '{prefix}' restricts bundle {b.id} "
                  f"to layers {sorted(layers)}, but what already governs it "
                  f"allows only {sorted(cur)} — the two have no layer in "
                  f"common.  Widen one of them.")
            sys.exit(1)
        w.input.allowed_layers = eff
        n += 1
    return n
