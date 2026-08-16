#!/usr/bin/env python3
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

"""Fetch OpenROAD's pdngen regression goldens — real generator output.

These are the DEFs `pdngen` writes and diffs against in its own test suite,
so they are the answer to "what does a PDN generator actually emit?" — a
question this repo got wrong for as long as the only power-routed DEFs
available were the two we typed by hand (`opens_interchange.md` item 15,
which they measured: 685 metal paths, of which the reader read **zero**).

Not vendored, for the reason `flow/ariane133/fetch.py` is not: they are
somebody else's work under somebody else's licence (BSD 3-Clause, The
OpenROAD Project). Checksum-pinned for the reason that one is — upstream
moving under us must fail LOUDLY rather than silently swap the fixture.

`test_def_specialnets.py` skips the golden-backed checks when these are
absent, so the fast tier does not need the network. What always runs is
`pdngen_excerpt.def` beside this script: real upstream bytes, small enough
to check in (see ReadMe.md).

    python3 test/tests/data/pdn_goldens/fetch.py            # fetch what is missing
    python3 test/tests/data/pdn_goldens/fetch.py --check    # verify only
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

# `master` rather than a commit sha, as in flow/ariane133/fetch.py: the
# digests below are the real pin, and a sha would rot into a 404 instead of
# into the mismatch we want to see.
BASE = ("https://raw.githubusercontent.com/"
        "The-OpenROAD-Project/OpenROAD/master/src/pdn/test")

# (name, sha256, bytes, what it exercises)
FILES = [
    ("core_grid.defok",
     "c226fc1bddde62c44d0591b07aaae9efec57cc4afae5de6b5d4c52b17351b641",
     63724,
     "gcd, followpin rails only — 57 metal paths, no vias.  The simplest "
     "real generator output there is"),
    ("macros.defok",
     "0bb0323a05ecf55616764b2e43f117ec8dfb589bf3dc7c4b7efed8b81a72b223",
     297618,
     "RocketTile: a core grid plus per-macro M5/M6 grids over two SRAMs — "
     "254 metal paths and 1686 via placements.  The shape flow/ariane133 "
     "would have, scaled down"),
    ("existing.defok",
     "392c98856a9a661b2c9861a57586f95c801661726198055fb00da529f78dd490",
     309949,
     "the same design with rings: the only one of the four carrying "
     "`+ SHAPE RING`"),
    ("core_grid_snap.defok",
     "0680da0530bc6015fb32be97eaf670d8f19894ddf5c0098a11a036695469cd6c",
     328251,
     "gcd with snapped stripes — 3289 via placements against 96 metal "
     "paths, the most lopsided of the four"),
]

_HERE = Path(__file__).resolve().parent


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path, sha, size):
    """Return None if the file is exactly what we pinned, else why not."""
    actual_size = path.stat().st_size
    if actual_size != size:
        return f"expected {size} bytes, found {actual_size}"
    actual = _digest(path)
    if actual != sha:
        return f"sha256 mismatch\n    expected {sha}\n    found    {actual}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="verify present files and exit; fetch nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even when the file is already correct")
    args = ap.parse_args(argv)

    bad = 0
    for name, sha, size, what in FILES:
        dest = _HERE / name
        if dest.exists() and not args.force:
            why = _verify(dest, sha, size)
            if why is None:
                print(f"  ok       {name}  ({size:,} bytes)")
                continue
            print(f"  BAD      {name}: {why}")
            bad += 1
            if args.check:
                continue
        elif args.check:
            print(f"  MISSING  {name}  — {what}")
            bad += 1
            continue

        url = f"{BASE}/{name}"
        print(f"  fetching {name}  <- {url}")
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
        except Exception as exc:                       # noqa: BLE001
            print(f"  FAILED   {name}: {exc}")
            bad += 1
            continue
        dest.write_bytes(data)
        why = _verify(dest, sha, size)
        if why is not None:
            print(f"  BAD      {name}: {why}")
            print("           upstream moved.  Re-pin deliberately after "
                  "looking at what changed — do not just update the digest.")
            bad += 1
        else:
            print(f"  ok       {name}  ({size:,} bytes)")

    if bad:
        print(f"\n{bad} file(s) missing or wrong.")
        return 1
    print(f"\nAll {len(FILES)} goldens present and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
