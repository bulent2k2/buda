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
afterwards; its flow logs are kept (`<flow_dir>/log/.bundling_ab_*`, where
the CLI writes every run's detail, and where the counts are read from, since
the terminal carries one summary line per command) for anyone checking a row.

A flow with no bundler in its own text is refused — one reached through
`source` cannot be injected before — and so is a flow that sets
`set_max_bundle_bits` anywhere in its source tree, whose own cap (or a
scoped rule, which outranks the global one for its prefix) would make "one
net per bundle" untrue for part of the design while the table still said
it.  A run whose report records a command error fails rather than being
tabled: the CLI prints `Error:` and carries on, exit 0.

Exit 0 on a measurement, 2 on a refusal, 1 when a run fails.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from buda_script import sole_path_arg, strip_inline_comment  # noqa: E402

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


def _command(line):
    """The command word of one `.buda` line, or '' for a blank/comment."""
    text = strip_inline_comment(line).strip()
    return text.split()[0] if text else ""


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


def _source_tree(text, base_dir, name, seen=None):
    """Yield (file, line number, command word) for every line of `text` and,
    recursively, of every file it `source`s.  A sourced file that cannot be
    read refuses: a cap declared in it could not be ruled out."""
    seen = set() if seen is None else seen
    for n, line in enumerate(text.splitlines(), 1):
        word = _command(line)
        yield name, n, word
        if word != "source":
            continue
        raw = sole_path_arg(strip_inline_comment(line).strip())
        path = _resolve_source(base_dir, raw) if raw else ""
        if not path or not os.path.isfile(path):
            raise Refused(f"{name}:{n} sources {raw!r}, which cannot be "
                          f"read, so a bundle-bit cap in it cannot be ruled "
                          f"out")
        if path in seen:
            continue
        seen.add(path)
        yield from _source_tree(Path(path).read_text(), os.path.dirname(path),
                                path, seen)


def unbundled_text(text, base_dir=None, name="flow"):
    """`text` with INJECTED placed right before its first bundler.

    Raises Refused when there is no bundler to inject before, or when the
    flow — its own text or any file it sources, when `base_dir` says where
    those resolve from — already declares a bundle-bit cap."""
    lines = text.splitlines(keepends=True)
    words = [_command(ln) for ln in lines]
    if base_dir is None:
        tree = ((name, i + 1, w) for i, w in enumerate(words))
    else:
        tree = _source_tree(text, base_dir, name)
    for where, n, w in tree:
        if w == "set_max_bundle_bits":
            raise Refused(
                f"{where}:{n} already sets set_max_bundle_bits; its cap, or "
                f"a scoped rule that outranks the global one for its prefix, "
                f"would leave part of the design bundled while the table said "
                f"'one net per bundle'")
    for i, w in enumerate(words):
        if w in BUNDLERS:
            nl = "\n" if not lines[i].endswith("\r\n") else "\r\n"
            inject = [f"# bundling_ab: every net its own bundle{nl}",
                      f"{INJECTED}{nl}"]
            return "".join(lines[:i] + inject + lines[i:])
    raise Refused(
        "no run_bundler / run_hier_bundler in the flow's own text; a "
        "bundler reached through `source` cannot be injected before")


def _sum_stage(commands, words):
    return round(sum(c["seconds"] for c in commands
                     if c["command"].split()[0] in words), 2)


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
    for k in range(len(heads) - 1, -1, -1):
        if heads[k].group(1).split()[:1] and \
                heads[k].group(1).split()[0] in GENERATORS:
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


def run_arm(flow, text, tag):
    """Run `text` as a sibling of `flow`; return (report, stdout, log)."""
    flow = Path(flow).resolve()
    variant = flow.with_name(f".bundling_ab_{flow.stem}_{tag}.buda")
    log_path = variant.parent / "log" / f"{variant.stem}_flow.log"
    report_path = variant.with_suffix(".json")
    variant.write_text(text)
    try:
        env = {**os.environ,
               "PYTHONPATH": os.pathsep.join(
                   [str(ROOT / "build"), str(ROOT / "src"),
                    str(ROOT / "tools")])}
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, str(ROOT / "src" / "buda_cli.py"), "--no-viz",
             "--report-json", str(report_path), str(variant)],
            capture_output=True, text=True, env=env)
        wall = time.time() - t0
        if r.returncode != 0 or not report_path.exists():
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
        report["wall_seconds"] = round(wall, 1)
        log = log_path.read_text() if log_path.exists() else ""
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
                                 name=args.flow)
    except Refused as e:
        print(f"bundling_ab: refused: {e}", file=sys.stderr)
        return 2

    rows = {}
    try:
        for tag, t in (("bundled", text), ("unbundled", variant)):
            print(f"[bundling_ab] running {tag}...", flush=True)
            report, stdout, log = run_arm(args.flow, t, tag)
            rows[tag] = summarize(report, stdout, log)
            rows[tag]["wall_seconds"] = report["wall_seconds"]
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
