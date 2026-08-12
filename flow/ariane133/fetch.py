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

"""Fetch the two upstream files `ariane133.buda` needs.

Neither is checked in.  The netlist is 12 MB — larger than anything in this
repo, `src/sqlite3.c` included — and both are somebody else's work under
somebody else's licence, so this repo points at them rather than vendoring
them.

**Every file is checksum-pinned, and that is the point rather than a
formality.**  This design exists because `demo/ariane` got a DEF and a LEF
from two different technologies and nothing recorded where either came from
(`opens_interchange.md` item 9): the mismatch was invisible for as long as
nobody tried to import the pair, and the ReadMe beside them described a
third design again.  A fetch that names its source and verifies its bytes
cannot reproduce that failure — if upstream moves, this fails loudly with a
digest mismatch instead of silently handing the flow a different design.

    python3 flow/ariane133/fetch.py            # fetch what is missing
    python3 flow/ariane133/fetch.py --check    # verify only, fetch nothing
    python3 flow/ariane133/fetch.py --force    # re-fetch even if present
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

# TILOS-AI-Institute/MacroPlacement, BSD 3-Clause (Regents of the University
# of California).  `main` rather than a commit sha because the digests below
# are the real pin — a branch that moves under us fails the check, which is
# the outcome we want, and a sha would rot silently into a 404.
BASE = ("https://raw.githubusercontent.com/"
        "TILOS-AI-Institute/MacroPlacement/main")

FILES = [
    # (local name, upstream path, sha256, bytes, what it is)
    ("ariane.v",
     "Flows/NanGate45/ariane133/netlist/ariane.v",
     "0be18a8ed746b6cd9b043e3021112d9ba9ccf7f98e16726d7c783eb73b0c559b",
     11927624,
     "gate-level netlist, 127 modules — hierarchical, which is what makes "
     "this a hier-pipeline vehicle rather than a big flat one"),
    ("fakeram45_256x16.lef",
     "Enablements/NanGate45/lef/fakeram45_256x16.lef",
     "9765349e8d204e1a2ef868740bcfc780162fe0a8f06ebfad0de22b6b3ebc7f34",
     15329,
     "the SRAM macro: 57.57 x 133.0 um.  THE missing LEF of item 9 — the "
     "one that actually describes demo/ariane/ariane.def"),
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
    for name, remote, sha, size, what in FILES:
        dest = _HERE / name
        if dest.exists() and not args.force:
            why = _verify(dest, sha, size)
            if why is None:
                print(f"  ok       {name}  ({size:,} bytes)")
                continue
            if args.check:
                print(f"  BAD      {name}: {why}")
                bad += 1
                continue
            print(f"  stale    {name}: {why}\n           re-fetching")
        elif args.check:
            print(f"  MISSING  {name} — run without --check to fetch it")
            bad += 1
            continue

        url = f"{BASE}/{remote}"
        print(f"  fetch    {name}  <- {url}")
        print(f"           {what}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(url) as r, open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        except Exception as e:                       # noqa: BLE001
            tmp.unlink(missing_ok=True)
            print(f"  ERROR    {name}: {e}")
            bad += 1
            continue

        why = _verify(tmp, sha, size)
        if why is not None:
            # Do NOT leave it in place.  A wrong file that looks right is the
            # exact failure this whole vehicle exists to have caught.
            tmp.unlink(missing_ok=True)
            print(f"  ERROR    {name}: {why}\n"
                  f"           upstream has changed; nothing was written.")
            bad += 1
            continue
        tmp.replace(dest)
        print(f"  ok       {name}  ({size:,} bytes)")

    if bad:
        print(f"\n{bad} file(s) not in the expected state.")
        return 1
    print("\nAll inputs verified.  Run:  bin/buda flow/ariane133/ariane133.buda")
    return 0


if __name__ == "__main__":
    sys.exit(main())
