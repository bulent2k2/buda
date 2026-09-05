r"""Read/write the OpenROAD route-guide file (shared by the measure scripts).

Format, as `write_guides` emits it (the ISPD-contest form TritonRoute reads):

    <net name>
    (
    <x1> <y1> <x2> <y2> <layer>
    ...
    )

Coordinates in DBU.  Parsed strictly -- an unexpected line raises with its
number, because a guide file that is not this shape is itself a finding.

Net NAMES: `write_guides` in OpenROAD spells some of them DEF-escaped --
measured 2026-09-05 on two_reg32's reference route, the 32 bus nets came out
as `mid\[0\]` while the port nets stayed `d[0]`, so the rule is not a
function of the characters alone.  `read_guides` keys its result by the
UNESCAPED name (what def_wires keys by, so the two files agree on a net)
and keeps each name's spelling in `.spelling`; `write_guides` gives a name
back in that spelling, and escapes `[`/`]` for a name it has no record of,
which is what OpenROAD did for every non-port net it wrote.
"""


class Guides(dict):
    """net -> [(x1, y1, x2, y2, layer), ...], plus `.spelling`: net -> the
    name as the source file spelled it."""

    def __init__(self):
        super().__init__()
        self.spelling = {}


def unescape(name):
    return name.replace("\\", "")


def escape(name):
    return name.replace("[", "\\[").replace("]", "\\]")


def read_guides(path):
    guides = Guides()
    net = None
    with open(path) as f:
        for ln_no, raw in enumerate(f, 1):
            ln = raw.strip()
            if not ln:
                continue
            if net is None:
                net = unescape(ln)
                guides.setdefault(net, [])
                guides.spelling[net] = ln
            elif ln == "(":
                continue
            elif ln == ")":
                net = None
            else:
                p = ln.split()
                if len(p) != 5:
                    raise ValueError(f"{path}:{ln_no}: not 'x1 y1 x2 y2 layer': {raw!r}")
                x1, y1, x2, y2 = map(int, p[:4])
                guides[net].append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), p[4]))
    return guides


def write_guides(path, guides, spelling=None):
    spelling = spelling if spelling is not None else getattr(guides, "spelling", {})
    with open(path, "w") as f:
        for net, rects in guides.items():
            f.write(f"{spelling.get(net, escape(net))}\n(\n")
            for x1, y1, x2, y2, layer in rects:
                f.write(f"{x1} {y1} {x2} {y2} {layer}\n")
            f.write(")\n")
