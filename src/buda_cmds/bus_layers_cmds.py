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

"""`set_bus_layers` — the command surface; the rule is
`buda_session.bus_layers` (see its docstring for what it is for)."""
from buda_session.bus_layers import scopes


def cmd_set_bus_layers(session, cmd, args, cmd_line):
    # Usage: set_bus_layers <prefix>|* <layer csv>|off
    if not args:
        sc = getattr(session, "_bus_layer_scopes", None) or {}
        if not sc:
            print("set_bus_layers: no bus layer scopes declared")
            return
        names = {lid: n for n, lid in session._layer_name_map.items()}
        for prefix in sorted(sc):
            got = ", ".join(names.get(l, str(l)) for l in sc[prefix])
            print(f"set_bus_layers {prefix} -> {got}")
        return
    if len(args) < 2:
        print("Error: usage: set_bus_layers <prefix>|* <layer csv>|off")
        return
    prefix, spec = args[0], args[1]
    sc = scopes(session)
    if spec.lower() == "off":
        if prefix == "*" and len(args) == 2 and "*" not in sc:
            # `* off` clears every scope, like set_cell_layer_cap * off.
            n, sc_cleared = len(sc), dict(sc)
            sc.clear()
            print(f"set_bus_layers: cleared {n} scope(s) "
                  f"({', '.join(sorted(sc_cleared)) or 'none'})")
            return
        if sc.pop(prefix, None) is None:
            print(f"Error: set_bus_layers: no scope '{prefix}' to clear")
            return
        print(f"set_bus_layers: cleared scope '{prefix}'")
        return
    lids = []
    for name in spec.split(","):
        name = name.strip()
        if not name:
            continue
        lid = session._layer_name_map.get(name)
        if lid is None:
            known = ", ".join(sorted(session._layer_name_map)) or "none"
            print(f"Error: set_bus_layers: unknown layer '{name}' "
                  f"(declared: {known})")
            return
        lids.append(lid)
    if not lids:
        print("Error: set_bus_layers: no layer named")
        return
    sc[prefix] = sorted(set(lids))
    names = {lid: n for n, lid in session._layer_name_map.items()}
    print(f"set_bus_layers: '{prefix}' -> "
          f"{', '.join(names.get(l, str(l)) for l in sc[prefix])}")


COMMANDS = {"set_bus_layers": cmd_set_bus_layers}
