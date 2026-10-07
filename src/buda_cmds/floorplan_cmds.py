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

"""`auto_floorplan` — the command surface of `buda_session.autofp`: a first
hierarchical floorplan (sizes AND placement, every level) from the open
BDB's netlist and a PDK area model."""
from buda_session.util import resolve_script_path

from ._options import reject_unknown_options

_OPTS = ("pdk", "util", "gap", "margin", "top_margin", "place", "seed",
         "keep", "wl", "snap", "bitpitch", "pad", "aspect", "grow", "cols",
         "sa_iter", "fixed")
_PLACE = ("auto", "slice", "grid", "sa")


def _kv_list(cmd, key, text, kind):
    """`a=1.5,b=2` -> {a: 1.5, b: 2}; None (and an error printed) on a
    malformed entry."""
    out = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            print(f"Error: {cmd}: {key} wants <cell>=<value>[,...], got "
                  f"'{item}'")
            return None
        c, v = item.split("=", 1)
        try:
            out[c.strip()] = kind(v)
        except ValueError:
            print(f"Error: {cmd}: {key} value for '{c}' must be a number, "
                  f"got '{v}'")
            return None
    return out


def cmd_auto_floorplan(session, cmd, args, cmd_line):
    # Usage: auto_floorplan [pdk <file>] [util <f>] [gap <n>] [margin <n>]
    #        [top_margin <n>] [place auto|slice|grid|sa] [seed <n>]
    #        [keep <n>] [wl <f>] [snap <px> <py>] [bitpitch <f>] [pad <n>]
    #        [aspect <f>] [grow <cell>=<f>,...] [cols <cell>=<n>,...]
    #        [sa_iter <n>] [fixed <cell>,...]
    kw = dict(pdk_path=None, gap=16, margin=16, place="auto", seed=1,
              keep=5, wl_weight=0.5, snap=(1, 1), bit_pitch=None, pad=None,
              aspect_cap=2.0, grow=None, cols=None, sa_iter=0,
              top_margin=None, util=None, fixed=())
    i = 0
    keys = []
    while i < len(args):
        k = args[i].lower()
        keys.append(k)
        if k not in _OPTS:
            reject_unknown_options(cmd, [args[i]], _OPTS)
            return
        if k == "snap":
            if i + 2 >= len(args):
                print(f"Error: {cmd}: snap wants <px> <py>")
                return
            try:
                kw["snap"] = (int(args[i + 1]), int(args[i + 2]))
            except ValueError:
                print(f"Error: {cmd}: snap wants two integers, got "
                      f"'{args[i + 1]} {args[i + 2]}'")
                return
            if kw["snap"][0] < 1 or kw["snap"][1] < 1:
                print(f"Error: {cmd}: snap periods must be >= 1")
                return
            i += 3
            continue
        if i + 1 >= len(args):
            print(f"Error: {cmd}: {k} wants a value")
            return
        v = args[i + 1]
        i += 2
        if k == "pdk":
            kw["pdk_path"] = resolve_script_path(session, v, is_read=True)
        elif k == "place":
            if v.lower() not in _PLACE:
                print(f"Error: {cmd}: place must be one of "
                      f"{', '.join(_PLACE)} (got '{v}')")
                return
            kw["place"] = v.lower()
        elif k in ("grow",):
            d = _kv_list(cmd, k, v, float)
            if d is None:
                return
            kw["grow"] = d
        elif k == "cols":
            d = _kv_list(cmd, k, v, int)
            if d is None:
                return
            kw["cols"] = d
        elif k == "fixed":
            # cells whose size and template offsets the BDB already holds
            kw["fixed"] = tuple(c.strip() for c in v.split(",") if c.strip())
        else:
            try:
                fv = float(v)
            except ValueError:
                print(f"Error: {cmd}: {k} must be a number, got '{v}'")
                return
            if k in ("gap", "margin", "top_margin", "pad", "seed", "keep",
                     "sa_iter"):
                if fv < 0 or fv != int(fv):
                    print(f"Error: {cmd}: {k} must be a non-negative "
                          f"integer, got '{v}'")
                    return
                fv = int(fv)
            if k == "keep" and fv < 1:
                print(f"Error: {cmd}: keep must be >= 1")
                return
            if k == "util" and not (0 < fv <= 1):
                print(f"Error: {cmd}: util is a fraction in (0, 1], got "
                      f"'{v}'")
                return
            if k in ("bitpitch", "aspect") and fv <= 0:
                print(f"Error: {cmd}: {k} must be positive")
                return
            if k == "wl" and fv < 0:
                print(f"Error: {cmd}: wl must be >= 0")
                return
            name = {"wl": "wl_weight", "bitpitch": "bit_pitch",
                    "aspect": "aspect_cap"}.get(k, k)
            kw[name] = fv
    session._auto_floorplan(**kw)


COMMANDS = {
    "auto_floorplan": cmd_auto_floorplan,
}
