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

"""
tools/bdb_serialize.py — round-trip a binary BDB (SQLite) to/from diffable text.

A BDB is a plain SQLite file, so `sqlite3.iterdump()` gives a deterministic SQL
text rendering (schema first, then one INSERT per row in rowid order per table).
That text form is what we check into git as `*.bdb.sql`: `git diff` shows exactly
which components / nets / busterms / bundles changed, and merges resolve like any
other text file. The binary `.bdb` is rebuilt on demand and never committed.

Why not commit the binary directly (the `.b-db` rename trick)? Because git cannot
diff or merge a binary SQLite blob, and any pipeline run mutates the file in place
(WAL sidecars, `derive_busterms` rewriting the busterm table, …), so a committed
binary shows up as a spurious unstaged change. The text dump plus a copy-to-temp
test fixture removes both problems.

Usage:
  python3 tools/bdb_serialize.py dump <in.bdb>      <out.bdb.sql>
  python3 tools/bdb_serialize.py load <in.bdb.sql>  <out.bdb>
  python3 tools/bdb_serialize.py verify <a.bdb>          # dump→load→dump is stable

`dump` is deterministic: two dumps of BDBs built from the same steps are byte
identical, so a regenerated fixture produces a no-op `git diff`.
"""

import os
import sqlite3
import sys
import urllib.parse

# A single sentinel line so a partial/interrupted dump is obvious in review.
_HEADER = "-- BUDA BDB text dump (sqlite3 iterdump); regenerate via tools/bdb_serialize.py\n"


def dump(bdb_path: str, sql_path: str) -> None:
    """Serialize the binary BDB at `bdb_path` to deterministic SQL text at `sql_path`."""
    if not os.path.exists(bdb_path):
        raise FileNotFoundError(bdb_path)
    # Open read-only so serializing never mutates the source (no WAL sidecars).
    # Percent-encode the path (audit T1-04): a raw f-string let URI
    # metacharacters in the filename be misparsed — a '?' silently opened an
    # EMPTY db (dumping nothing, overwriting a fixture), a '%' raised
    # OperationalError. quote() keeps '/' so the path structure survives.
    uri = "file:" + urllib.parse.quote(os.path.abspath(bdb_path), safe="/") \
        + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        # iterdump() captures schema + rows but NOT PRAGMA user_version, so emit
        # it explicitly — otherwise the schema version is lost on load and the DB
        # would re-migrate from 0. Keeping it in the text also makes the version
        # visible in the diff.
        user_version = con.execute("PRAGMA user_version").fetchone()[0]
        with open(sql_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_HEADER)
            f.write(f"PRAGMA user_version={int(user_version)};\n")
            for line in con.iterdump():
                f.write(line)
                f.write("\n")
    finally:
        con.close()


def load(sql_path: str, bdb_path: str) -> str:
    """Rebuild a fresh binary BDB at `bdb_path` from the SQL text at `sql_path`.

    Any existing file at `bdb_path` (and its WAL/shm sidecars) is removed first so
    the result is a clean materialization, not an overlay. Returns `bdb_path`.
    """
    if not os.path.exists(sql_path):
        raise FileNotFoundError(sql_path)
    for p in (bdb_path, bdb_path + "-wal", bdb_path + "-shm"):
        if os.path.exists(p):
            os.unlink(p)
    with open(sql_path, "r", encoding="utf-8") as f:
        script = f.read()
    con = sqlite3.connect(bdb_path)
    try:
        con.executescript(script)
        con.commit()
    finally:
        con.close()
    return bdb_path


def materialize_if_sql(path: str) -> str:
    """If `path` is a serialized BDB (*.sql), load it into a throwaway temp binary
    and return that path; otherwise return `path` unchanged.

    Lets any BDB consumer (CLI, floorplanner) accept a committed `*.bdb.sql` /
    `*.b_db.sql` text fixture transparently. The temp binary lives for the process
    lifetime; changes to it are NOT written back through this helper. (The CLI's
    `open_bdb <file>.sql writeback` has an opt-in write-back path of its own; this
    helper stays read-only.)
    """
    import tempfile
    if path is None or path == ':memory:' or not path.endswith('.sql'):
        return path
    base = os.path.basename(path)[:-len('.sql')]  # mix.b_db.sql -> mix.b_db
    out = os.path.join(tempfile.mkdtemp(prefix='buda_bdb_'), base)
    load(path, out)
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def verify(bdb_path: str) -> bool:
    """Round-trip check: dump → load → dump is byte-stable. Returns True on success."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        sql1 = os.path.join(d, "a.bdb.sql")
        rebuilt = os.path.join(d, "rebuilt.bdb")
        sql2 = os.path.join(d, "b.bdb.sql")
        dump(bdb_path, sql1)
        load(sql1, rebuilt)
        dump(rebuilt, sql2)
        ok = _read(sql1) == _read(sql2)
        if not ok:
            sys.stderr.write("verify FAILED: dump→load→dump is not stable\n")
        return ok


def main(argv) -> int:
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "dump" and len(argv) == 4:
        dump(argv[2], argv[3])
        print(f"dumped {argv[2]} -> {argv[3]}")
        return 0
    if cmd == "load" and len(argv) == 4:
        load(argv[2], argv[3])
        print(f"loaded {argv[2]} -> {argv[3]}")
        return 0
    if cmd == "verify" and len(argv) == 3:
        return 0 if verify(argv[2]) else 1
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
