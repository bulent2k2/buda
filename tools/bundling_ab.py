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

"""bundling_ab — what bundling buys, measured on one design.

    tools/bundling_ab.py flow/soc_small.buda [--json out.json]

Runs a `.buda` flow twice through the CLI: as written, and with
`set_max_bundle_bits 1` injected immediately before its bundler, which
splits every bundle into one-net parts.  Everything else — floorplan,
layers, planner knobs, healers — is the flow's own text, so the one thing
that differs between the two runs is whether nets are planned as buses.

It reports what the planner had to reason about (bundles, candidate paths),
where the time went (per stage, from `--report-json`), and what came out
(the first and final `check_design` verdicts, detailed wirelength).  It
states no verdict of its own: the first measurement (2026-09-23, NQ = 8
SoC) found bundling ~100x FASTER for ~4 % MORE wire, and a table that only
printed the ratio that flatters the idea would be the wrong instrument.

The variant is written BESIDE the flow, not in a temp directory: every
relative path in a flow resolves against the script's own directory, so a
copy anywhere else would run a different design (or none).  It is removed
afterwards.  Its names are unique per run (`mkstemp`), so two invocations
on one flow cannot collide; its flow log is kept and its path printed
(`<flow_dir>/log/.bundling_ab_*_flow.log`, where the CLI writes every run's
detail, and where the counts are read from, since the terminal carries one
summary line per command) for anyone checking a row.

The two arms run one after the other, so each gets its OWN copy of every
file the flow opens or writes (the owner's review of #953).  A named
`open_bdb` target is copied into the arm as it stood before the A/B — the
second arm used to inherit the first arm's rows, which kills a flow that
builds its design into a named file — and every file the flow writes
(`save_bdb`, `emit_*`, `export_*`, a `derive_* file`) is renamed into the
arm, so the A/B never writes into the user's tree: it used to leave a
checked-in fixture holding the one-net arm's route.  A `:memory:` or
`.sql` open writes nothing of the user's and is left as it is.

When the flow opens a BDB, the arm also appends `save_bdb` to a checkpoint
in its own temp directory — after the flow's last line and before every
top-level `exit` — and the judge (`tools/independent_audit.py`) scores that
checkpoint in a row of its own beside `check_design`'s.  The snapshot is
the tool's, not the flow's, so its seconds are left out of the arm's times.

A flow with no bundler in its own text is refused — one reached through
`source` cannot be injected before — and so is a flow that sets
`set_max_bundle_bits` anywhere in its source tree, whose own cap (or a
scoped rule, which outranks the global one for its prefix) would make "one
net per bundle" untrue for part of the design while the table still said
it.  A run whose report records a command error fails rather than being
tabled: the CLI prints `Error:` and carries on, exit 0.  Also refused is
what cannot be kept apart: a `.sql` opened with `writeback`, and a named
database open or a file write in a SOURCED file, whose text is not the
tool's to rewrite.  Every refusal is decided before either arm runs.

Exit 0 on a measurement, 2 on a refusal, 1 when a run fails.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from buda_script import (leading_path_and_options, sole_path_arg,  # noqa: E402
                         strip_inline_comment)

BUNDLERS = ("run_hier_bundler", "run_bundler")
INJECTED = "set_max_bundle_bits 1"

# Stage -> the command words whose seconds it sums.  First word only, so
# `run_nuts_on_layer` is not `run_nuts` and `run_planner post_nuts` IS the
# planner (it re-plans after NUTS).
STAGES = (
    ("bundler", ("run_hier_bundler", "run_bundler")),
    ("path generation", ("generate_hier_topologies", "generate_topologies")),
    ("planner", ("run_planner",)),
    ("track fitting (bus)", ("run_nuts",)),
    ("track fitting (bit)", ("run_detailed_nuts", "run_dnuts")),
    ("repair", ("negotiate_congestion", "ripup_reroute", "refine_selection")),
)


class Refused(Exception):
    """The flow cannot be measured by this tool as it stands."""


def _resolve_source(base_dir, raw):
    """Where `source <raw>` lands from a script in `base_dir` — the engine's
    own rule (`cmd_source`): relative to the sourcing script, with the
    `.buda` suffix tried when the bare name is not a file."""
    full = os.path.normpath(os.path.join(base_dir, raw))
    if not os.path.isfile(full) and not raw.endswith(".buda"):
        cand = os.path.normpath(os.path.join(base_dir, raw + ".buda"))
        if os.path.isfile(cand):
            return cand
    return full


def _canonical(toks, aliases):
    """The command `toks` runs, as `BudaSession.do_command` dispatches it —
    lower-cased, then resolved through `aliases` — and the alias table
    updated by it, as `cmd_alias`/`cmd_unalias` would (a target is stored
    already resolved, so a chain collapses).  ONE rule for every reader
    here: the flow walk, the report's command list and the flow log's
    headers all record a command as it was TYPED (Codex P1 on #953, twice)."""
    word = toks[0].lower()
    word = aliases.get(word, word)
    if word == "alias" and len(toks) >= 3:
        target = toks[2].lower()
        aliases[toks[1].lower()] = aliases.get(target, target)
    elif word == "unalias":
        for a in toks[1:]:
            aliases.pop(a.lower(), None)
    return word


def _canonical_words(commands):
    """Canonical first words of command lines in the order they RAN."""
    aliases, out = {}, []
    for c in commands:
        toks = c.split()
        out.append(_canonical(toks, aliases) if toks else "")
    return out


def _source_tree(text, base_dir, name, aliases=None, stack=None,
                 top=True):
    """Yield (file, line number, command, top-level line index or None,
    the line with its comment stripped) for every line of `text` and,
    recursively, of every file it `source`s, in the order the engine runs
    them.

    The command is CANONICAL, the way `BudaSession.do_command` dispatches
    it: lower-cased, then resolved through the `alias` table as the walk
    has built it so far — so `SET_MAX_BUNDLE_BITS` and `alias cap
    set_max_bundle_bits` + `cap 4 for pc_` read as the command they run
    (Codex P1 on #953).  `alias <name> <target>` stores the target already
    resolved, like `cmd_alias`, and `unalias` removes names; a real command
    cannot be aliased away, so no registry lookup is needed to mirror the
    engine.  A sourced file that cannot be read refuses: a cap declared in
    it could not be ruled out.

    A file sourced TWICE is walked twice, because the engine runs it twice
    and an alias redefined in between can make the second run declare a
    cap the first did not (Codex P2 on #953); only a file sourcing itself,
    directly or through others, is refused — `cmd_source` has no guard, so
    the engine would recurse until Python stops it."""
    aliases = {} if aliases is None else aliases
    stack = [] if stack is None else stack
    for i, line in enumerate(text.splitlines()):
        n = i + 1
        bare = strip_inline_comment(line).strip()
        toks = bare.split()
        if not toks:
            continue
        word = _canonical(toks, aliases)
        yield name, n, word, (i if top else None), bare
        if word == "source":
            raw = sole_path_arg(bare)
            path = _resolve_source(base_dir, raw) if raw else ""
            if not path or not os.path.isfile(path):
                raise Refused(f"{name}:{n} sources {raw!r}, which cannot "
                              f"be read, so a bundle-bit cap in it cannot "
                              f"be ruled out")
            if os.path.realpath(path) in stack:
                raise Refused(f"{name}:{n} sources {raw!r}, which is "
                              f"already being sourced: the flow sources "
                              f"itself and the engine would not finish")
            stack.append(os.path.realpath(path))
            try:
                yield from _source_tree(Path(path).read_text(),
                                        os.path.dirname(path), path,
                                        aliases, stack, top=False)
            finally:
                stack.pop()


def _walk_root(base_dir, name):
    """(base_dir, recursion stack) to start a walk of the flow `name` from.
    With no `base_dir` no sourced file can be read, so a `source` refuses;
    the flow itself is on the stack when it is a real file, so a flow that
    sources itself is caught at the first hop."""
    if base_dir is None:
        return os.path.join(os.sep, "nonexistent-bundling-ab"), []
    top = os.path.join(base_dir, name)
    return base_dir, ([os.path.realpath(top)] if os.path.isfile(top) else [])


def unbundled_text(text, base_dir=None, name="flow"):
    """`text` with INJECTED placed right before its first bundler.

    Raises Refused when the first bundler the flow runs is not in its own
    text (nothing to inject before), or when the flow — its own text or,
    when `base_dir` says where those resolve from, any file it sources —
    already declares a bundle-bit cap."""
    lines = text.splitlines(keepends=True)
    base_dir, stack = _walk_root(base_dir, name)
    first = None
    for where, n, w, top_i, _ in _source_tree(text, base_dir, name,
                                              stack=stack):
        if w == "set_max_bundle_bits":
            raise Refused(
                f"{where}:{n} already sets set_max_bundle_bits; its cap, or "
                f"a scoped rule that outranks the global one for its prefix, "
                f"would leave part of the design bundled while the table said "
                f"'one net per bundle'")
        if w in BUNDLERS and first is None:
            first = (where, n, top_i)
    if first is None:
        raise Refused("the flow runs no run_bundler / run_hier_bundler")
    where, n, i = first
    if i is None:
        raise Refused(
            f"the flow's first bundler is at {where}:{n}, reached through "
            f"`source`; a bundler there cannot be injected before")
    nl = "\n" if not lines[i].endswith("\r\n") else "\r\n"
    inject = [f"# bundling_ab: every net its own bundle{nl}",
              f"{INJECTED}{nl}"]
    return "".join(lines[:i] + inject + lines[i:])


# ── Each arm's own database ───────────────────────────────────────────────
#
# The two arms run one after the other against the flow's OWN `open_bdb`
# target, so without this the second inherits whatever the first left there.
# A flow that BUILDS its design into a named file (`add_cell` / `add_inst`)
# then fails in the second arm on rows the first arm wrote — an engine FATAL
# after the first arm has run to completion — and either way the user's
# checkpoint is left holding one arm's route with nothing saying which (the
# owner's review of #953).  So each arm gets its own copy of every database
# the flow opens, as the file stood BEFORE the A/B, and the flow's own files
# are never written.  The same machinery gives each arm a routed checkpoint
# the judge (`tools/independent_audit.py`) can score.

# Every command that writes a file the flow names, and where the name is:
# "lead" = the first argument, "rest" = the whole rest of the line (a
# command whose one argument IS a path), and option words whose value is a
# path.  A flow writing through any of these would have the second arm
# overwrite the first arm's output in the user's tree — measured: the A/B
# of `flow/hier_user_topos.buda` rewrote the checked-in
# `flow/hier_user_topos.bdb.sql` through the flow's own `save_bdb`.
OUTPUT_WRITERS = {
    "save_bdb": ("rest", ()),
    "emit_guides": ("lead", ("tcl", "csv")),
    "emit_pin_def": ("lead", ()),
    "emit_block_size": ("lead", ()),
    "export_def_blockages": ("lead", ()),
    "export_gds": ("lead", ()),
    "derive_cell_layer_shares": (None, ("file",)),
    "derive_cell_layer_reserves": (None, ("file",)),
    "derive_top_plan": (None, ("file",)),
}

# Where each arm's files are made.  None = the system temp directory; a
# test points it at its own tmp_path.
WORK_ROOT = None

# A token the way the engine's tokenizer reads one: a quote is honoured only
# where a token BEGINS (buda_script.split_quoted_args).
_TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')


SNAPSHOT_TAG = "# bundling_ab: the judge's checkpoint"


class Isolation:
    """How one arm runs apart from the other: its flow text (every file the
    flow opens or writes renamed into the arm's own directory, and a
    `save_bdb` snapshot for the judge after its last line), the pre-A/B
    databases to copy in, and the checkpoint the judge scores (None, with
    the reason in `no_judge`)."""

    def __init__(self):
        self.text = None
        self.copies = []            # (flow's file, the arm's copy)
        self.checkpoint = None
        self.snapshot = None        # the appended command, as the report has it
        self.no_judge = "the flow opens no BDB"


def _quote(path):
    if not any(c.isspace() for c in path):
        return path
    for q in ('"', "'"):
        if q not in path:
            return f"{q}{path}{q}"
    raise Refused(f"no quoting can carry the path {path!r}")


def _unquote(tok):
    return tok[1:-1] if len(tok) >= 2 and tok[0] == tok[-1] in "\"'" else tok


def _path_spans(line, slot, opts):
    """(start, end) of every path argument on `line` for a command whose
    paths sit at `slot` ("lead", "rest" or None) and after `opts`."""
    body = strip_inline_comment(line).rstrip()
    toks = [(m.start(), m.end()) for m in _TOKEN.finditer(body)][1:]
    if not toks:
        return []
    if slot == "rest":
        return [(toks[0][0], len(body))]
    spans = [toks[0]] if slot == "lead" else []
    for k in range(len(toks) - 1):
        if body[toks[k][0]:toks[k][1]].lower() in opts:
            spans.append(toks[k + 1])
    return spans


def isolate(text, base_dir, name, arm_dir):
    """An Isolation for running `text` (the flow `name`, resolving from
    `base_dir`) with every file it opens or writes in `arm_dir`.

    A durable database the flow opens is COPIED into the arm as it stood
    before the A/B, so both arms start from the same state and neither
    writes the flow's own file; a file the flow writes is renamed into the
    arm; a later open of a file the arm itself wrote follows it there.
    A `:memory:` or `.sql` open is never written to the user's tree and is
    left as it is.
    Refused: a `.sql` opened with `writeback` (each arm would dump its
    route into the fixture), and a durable open or a write in a SOURCED
    file — its text is not ours to rewrite."""
    base_dir, stack = _walk_root(base_dir, name)
    iso = Isolation()
    lines = text.splitlines(keepends=True)
    mapped, opens = {}, []

    def arm_path(real):
        if real not in mapped:
            mapped[real] = os.path.join(
                arm_dir, f"{len(mapped)}_{os.path.basename(real)}")
        return mapped[real]

    def rewrite(i, spans):
        line = lines[i]
        new = [_quote(arm_path(os.path.realpath(os.path.join(
            base_dir, _unquote(line[lo:hi].strip()))))) for lo, hi in spans]
        for (lo, hi), path in reversed(list(zip(spans, new))):
            line = line[:lo] + path + line[hi:]
        lines[i] = line

    exits = []
    for where, n, w, top_i, bare in _source_tree(text, base_dir, name,
                                                 stack=stack):
        if w == "exit" and top_i is not None and opens:
            exits.append(top_i)     # an exit before any open needs none
        if w == "open_bdb":
            path, opts = leading_path_and_options(bare)
            writeback = bool(opts) and opts[0] == "writeback"
            opens.append(path)
            real = (os.path.realpath(os.path.join(base_dir, path))
                    if path and path != ":memory:" else None)
            if path.endswith(".sql") and writeback:
                raise Refused(
                    f"{where}:{n} opens {path} with `writeback`, so each arm "
                    f"would write its route back into that file")
            durable = real is not None and not path.endswith(".sql")
            if not (durable or real in mapped):
                continue            # :memory: / a .sql input: never written
            if top_i is None:
                raise Refused(
                    f"{where}:{n} opens the database {path}, which both arms "
                    f"would share and the first would leave its route in; an "
                    f"open in a sourced file cannot be given to each arm "
                    f"separately (move it into the flow's own text)")
            if real not in mapped and os.path.exists(real):
                iso.copies.append((real, arm_path(real)))
            rewrite(top_i, _path_spans(lines[top_i], "lead", ()))
        elif w in OUTPUT_WRITERS:
            if top_i is None:
                if _path_spans(bare, *OUTPUT_WRITERS[w]):
                    raise Refused(
                        f"{where}:{n} `{w}` writes a file, which the second "
                        f"arm would overwrite in your tree; a write in a "
                        f"sourced file cannot be given to each arm "
                        f"separately (move it into the flow's own text)")
                continue
            rewrite(top_i, _path_spans(lines[top_i], *OUTPUT_WRITERS[w]))
    if opens:
        # The judge's checkpoint is a SNAPSHOT of whatever database is open
        # when the flow ends — the one holding its final route — taken by
        # the engine's own save-as after the flow's last line and before
        # every top-level `exit`.  Not a redirect of the open itself: moving
        # a `:memory:` database onto disk makes every command that writes
        # it commit to disk, and on soc_small that added ~9 s of setup to a
        # 3.7 s arm, which is the column the table exists to compare.  The
        # snapshot's own seconds are taken back out of the arm's total.
        ckpt = os.path.join(arm_dir, "checkpoint.bdb")
        iso.snapshot = f"save_bdb {_quote(ckpt)}"
        nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
        snap = [f"{SNAPSHOT_TAG}{nl}", f"{iso.snapshot}{nl}"]
        for i in sorted(exits, reverse=True):
            lines[i:i] = snap
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += nl
        lines += snap
        iso.checkpoint = ckpt
        iso.no_judge = None
    iso.text = "".join(lines)
    return iso


def _copy_bdb(src, dst):
    """A consistent copy of the SQLite database `src` (its WAL included),
    through SQLite's own backup rather than a byte copy of the main file."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def judge(checkpoint):
    """The judge's verdict on one arm's checkpoint: "clean", "dirty (N)",
    or "unjudgeable" — `tools/independent_audit.py` run as its own process,
    since it imports no engine and must not share one with anything."""
    if not checkpoint or not os.path.isfile(checkpoint):
        return None
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "independent_audit.py"),
             checkpoint, "--json", out, "--quiet"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": ""})
        if r.returncode in (0, 1):
            res = json.loads(Path(out).read_text())
            return "clean" if res["clean"] else f"dirty ({res['total']})"
        return "unjudgeable"
    finally:
        os.unlink(out)


def _sum_stage(commands, words):
    canon = _canonical_words([c["command"] for c in commands])
    return round(sum(c["seconds"] for c, w in zip(commands, canon)
                     if w in words), 2)


def _last_int(pattern, text):
    hits = re.findall(pattern, text)
    return int(hits[-1]) if hits else None


GENERATORS = ("generate_hier_topologies", "generate_topologies")
_HEADER = re.compile(r"^━━━ (.*?) ━━━$", re.M)


def _last_generation(log):
    """The text of the LAST topology-generation command in a flow log.

    The terminal carries one summary line per command, so a flat flow's
    per-bundle `Generated N topologies` lines are only all there in the log
    (one per bundle); and a flow that generates twice must not count both."""
    heads = list(_HEADER.finditer(log))
    canon = _canonical_words([h.group(1) for h in heads])
    for k in range(len(heads) - 1, -1, -1):
        if canon[k] in GENERATORS:
            end = heads[k + 1].start() if k + 1 < len(heads) else len(log)
            return log[heads[k].end():end]
    return ""


def summarize(report, stdout, log=""):
    """One arm's row: counts from the run's flow log (its terminal summary
    when there is none), times and verdicts from its --report-json."""
    cmds = report.get("commands", [])
    text = log or stdout
    bundles = _last_int(r"HierBundler: (\d+) hbundles", text)
    if bundles is None:
        bundles = _last_int(r"Bundler created (\d+) hbundles", text)
    gen = _last_generation(log) if log else stdout
    cands = _last_int(r"(\d+) total candidates", gen)
    if cands is None:
        per = re.findall(r"Generated (\d+) topologies for bundle", gen)
        cands = sum(int(n) for n in per) if per and log else None
    audits = report.get("audits", [])
    nuts = [a for a in audits if a.get("stage") == "nuts"]
    dnuts = [a for a in audits if a.get("stage") == "dnuts"]
    return {
        "bundles": bundles,
        "candidates": cands,
        "stages": {name: _sum_stage(cmds, words) for name, words in STAGES},
        "engine_seconds": (round(report["total_seconds"], 2)
                           if report.get("total_seconds") is not None
                           else None),
        "first_nuts_violations": nuts[0]["violations"] if nuts else None,
        "first_dnuts_violations": dnuts[0]["violations"] if dnuts else None,
        "final_violations": audits[-1]["violations"] if audits else None,
        "detailed_wl": _last_int(r"total detailed WL = (\d+)", text),
    }


def run_arm(flow, text, tag, iso=None):
    """Run `text` as a sibling of `flow`; return (report, stdout, log).

    The variant and its report are EXCLUSIVE files this call creates
    (`mkstemp`), so two invocations on one flow cannot truncate or delete
    each other's, nor a file that happened to carry a fixed name (Codex P2
    on #953); only those two are removed afterwards.  With `iso`, the arm
    runs `iso.text` against its own databases (see `isolate`), and the
    report carries the judge's verdict on its checkpoint."""
    flow = Path(flow).resolve()
    if iso is not None:
        text = iso.text
        for src, dst in iso.copies:
            try:
                _copy_bdb(src, dst)
            except sqlite3.Error as e:
                raise RuntimeError(f"the {tag} arm could not copy {src} "
                                   f"({e})") from e
    stem = f".bundling_ab_{flow.stem}_{tag}_"
    fd, vpath = tempfile.mkstemp(prefix=stem, suffix=".buda", dir=flow.parent)
    variant = Path(vpath)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    rfd, rpath = tempfile.mkstemp(prefix=stem, suffix=".json",
                                  dir=flow.parent)
    os.close(rfd)
    report_path = Path(rpath)     # kept, empty: the CLI overwrites it
    log_path = variant.parent / "log" / f"{variant.stem}_flow.log"
    try:
        env = {**os.environ,
               "PYTHONPATH": os.pathsep.join(
                   [str(ROOT / "build"), str(ROOT / "src"),
                    str(ROOT / "tools")])}
        for k in ("BUDA_BDB_MEMORY_TO", "BUDA_BDB_MATERIALIZE_TO"):
            # A redirect left in the caller's shell names ONE file, which
            # the second arm would find already written.
            env.pop(k, None)
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, str(ROOT / "src" / "buda_cli.py"), "--no-viz",
             "--report-json", str(report_path), str(variant)],
            capture_output=True, text=True, env=env)
        wall = time.time() - t0
        if r.returncode != 0 or report_path.stat().st_size == 0:
            sys.stderr.write(r.stdout[-2000:] + r.stderr[-1000:])
            raise RuntimeError(f"the {tag} run failed (exit {r.returncode})")
        report = json.loads(report_path.read_text())
        # The CLI prints `Error:` and carries on (exit 0 without
        # --strict-check), so a command that refused its arguments would
        # otherwise read as a measurement of a flow that did not run.
        failed = [c["command"] for c in report.get("commands", [])
                  if c.get("errors")]
        if failed or report.get("exit_status"):
            sys.stderr.write(r.stdout[-2000:])
            raise RuntimeError(
                f"the {tag} run reported errors (exit status "
                f"{report.get('exit_status')}; commands: "
                f"{', '.join(failed) or 'none'})")
        if iso is not None and iso.snapshot:
            # The judge's snapshot is the tool's, not the flow's: out of the
            # arm's commands and its total.
            snap = [c for c in report.get("commands", [])
                    if c["command"] == iso.snapshot]
            report["commands"] = [c for c in report.get("commands", [])
                                  if c["command"] != iso.snapshot]
            if snap and report.get("total_seconds") is not None:
                report["total_seconds"] -= sum(c["seconds"] for c in snap)
        report["wall_seconds"] = round(wall, 1)
        log = log_path.read_text() if log_path.exists() else ""
        if log:
            print(f"[bundling_ab] {tag} flow log: {log_path}", flush=True)
        if iso is not None:
            if iso.checkpoint and os.path.isfile(iso.checkpoint):
                print(f"[bundling_ab] {tag} checkpoint: {iso.checkpoint}",
                      flush=True)
                report["judge"] = judge(iso.checkpoint)
            else:
                report["judge"] = None
        return report, r.stdout, log
    finally:
        for p in (variant, report_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def _ratio(a, b):
    if a in (None, 0) or b is None:
        return ""
    return f"{b / a:.1f}x"


def _delta(a, b):
    """A change too small for a ratio to show, as a signed percent."""
    if a in (None, 0) or b is None:
        return ""
    return f"{100.0 * (b - a) / a:+.1f} %"


def render(flow, rows):
    b, u = rows["bundled"], rows["unbundled"]
    out = [f"bundling A/B on {flow}", "",
           "| | bundled | one net per bundle | ratio |",
           "|---|---|---|---|"]

    def row(label, x, y, compare=_ratio):
        fx = "?" if x is None else f"{x:,}" if isinstance(x, int) else x
        fy = "?" if y is None else f"{y:,}" if isinstance(y, int) else y
        out.append(f"| {label} | {fx} | {fy} | "
                   f"{compare(x, y) if compare else ''} |")

    row("bundles planned", b["bundles"], u["bundles"])
    row("candidate paths", b["candidates"], u["candidates"])
    for name, _ in STAGES:
        row(f"{name} (s)", b["stages"][name], u["stages"][name])
    row("engine total (s)", b["engine_seconds"], u["engine_seconds"])
    row("first bus-level check (violations)", b["first_nuts_violations"],
        u["first_nuts_violations"], compare=None)
    row("first detailed check (violations)", b["first_dnuts_violations"],
        u["first_dnuts_violations"], compare=None)
    row("final check (violations)", b["final_violations"],
        u["final_violations"], compare=None)
    row("detailed wirelength", b["detailed_wl"], u["detailed_wl"],
        compare=_delta)
    if b.get("judge") or u.get("judge"):
        row("judge (independent_audit)", b.get("judge"), u.get("judge"),
            compare=None)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flow", help="a .buda flow with a bundler in its own text")
    ap.add_argument("--json", metavar="PATH",
                    help="also write both arms' rows as JSON")
    args = ap.parse_args(argv)

    flow = Path(args.flow).resolve()
    text = flow.read_text()
    try:
        variant = unbundled_text(text, base_dir=str(flow.parent),
                                 name=str(flow))
    except Refused as e:
        print(f"bundling_ab: refused: {e}", file=sys.stderr)
        return 2

    # Every refusal is decided here, BEFORE either arm runs: a flow the
    # second arm cannot run must not cost the first arm's minutes.
    arms, isos, dirs = (("bundled", text), ("unbundled", variant)), {}, []
    try:
        for tag, t in arms:
            dirs.append(tempfile.mkdtemp(
                prefix=f"bundling_ab_{flow.stem}_{tag}_", dir=WORK_ROOT))
            isos[tag] = isolate(t, str(flow.parent), str(flow), dirs[-1])
    except Refused as e:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        print(f"bundling_ab: refused: {e}", file=sys.stderr)
        return 2
    try:
        return _measure(args, arms, isos)
    finally:
        for d in dirs:              # an arm that kept no database
            try:
                os.rmdir(d)
            except OSError:
                pass


def _measure(args, arms, isos):
    iso = isos["bundled"]
    if iso.no_judge:
        print(f"[bundling_ab] no judge column: {iso.no_judge}", flush=True)

    rows = {}
    try:
        for tag, t in arms:
            print(f"[bundling_ab] running {tag}...", flush=True)
            report, stdout, log = run_arm(args.flow, t, tag, iso=isos[tag])
            rows[tag] = summarize(report, stdout, log)
            rows[tag]["wall_seconds"] = report["wall_seconds"]
            rows[tag]["judge"] = report.get("judge")
    except RuntimeError as e:
        print(f"bundling_ab: {e}", file=sys.stderr)
        return 1

    print()
    print(render(args.flow, rows))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
