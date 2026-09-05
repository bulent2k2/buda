#!/usr/bin/env python3
"""Print, one per line, what the measurement scripts need from a run's
resolved.json: RT_MIN_LAYER, RT_MAX_LAYER, the tech LEF for the default
corner, the cell LEFs (space-joined), DESIGN_NAME.

The tech LEF is keyed by CORNER, and the keys are WILDCARDS -- LibreLane's
`TECH_LEFS` is `{"nom_*": ..., "min_*": ..., "max_*": ...}` while
`DEFAULT_CORNER` is a concrete name like `nom_tt_025C_1v80` -- so the lookup
is an fnmatch over the keys, the way LibreLane's own steps pick a corner's
views (Codex #875 P1: an exact lookup raised KeyError inside a process
substitution, which left the shell with a short array and an unbound
element).  A plain string or list is accepted too, so a resolved.json from
another PDK layout still reads.

Usage: read_resolved.py <run_dir>/resolved.json
"""
import fnmatch
import json
import sys


def by_corner(value, corner, what):
    """A view keyed by corner wildcard, or given plainly."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(value)
    if isinstance(value, dict):
        hits = [k for k in value if fnmatch.fnmatch(corner, k)]
        if len(hits) != 1:
            raise SystemExit(
                f"{what}: corner {corner!r} matches {len(hits)} of the keys "
                f"{sorted(value)} -- expected exactly one")
        v = value[hits[0]]
        return " ".join(v) if isinstance(v, list) else v
    raise SystemExit(f"{what}: unexpected shape {type(value).__name__}")


def main(path):
    c = json.load(open(path))
    corner = c.get("DEFAULT_CORNER", "nom_tt_025C_1v80")
    print(c["RT_MIN_LAYER"])
    print(c["RT_MAX_LAYER"])
    print(by_corner(c["TECH_LEFS"], corner, "TECH_LEFS"))
    print(by_corner(c["CELL_LEFS"], corner, "CELL_LEFS"))
    print(c["DESIGN_NAME"])


if __name__ == "__main__":
    main(sys.argv[1])
