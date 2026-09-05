"""Read/write the OpenROAD route-guide file (shared by the measure scripts).

Format, as `write_guides` emits it (the ISPD-contest form TritonRoute reads):

    <net name>
    (
    <x1> <y1> <x2> <y2> <layer>
    ...
    )

Coordinates in DBU.  Parsed strictly -- an unexpected line raises with its
number, because a guide file that is not this shape is itself a finding.
"""


def read_guides(path):
    guides = {}          # net -> [(x1, y1, x2, y2, layer), ...]
    net = None
    with open(path) as f:
        for ln_no, raw in enumerate(f, 1):
            ln = raw.strip()
            if not ln:
                continue
            if net is None:
                net = ln
                guides.setdefault(net, [])
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


def write_guides(path, guides):
    with open(path, "w") as f:
        for net, rects in guides.items():
            f.write(f"{net}\n(\n")
            for x1, y1, x2, y2, layer in rects:
                f.write(f"{x1} {y1} {x2} {y2} {layer}\n")
            f.write(")\n")
