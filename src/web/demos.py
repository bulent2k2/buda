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

"""Demo catalog for the web client, DERIVED from real `.buda` flows.

The catalog itself is data — `demo/web/demos.json` — and every entry names an
existing flow file.  Nothing about a demo is written twice: this module reads
the flow and splits it at the first pipeline command, which yields all three
things the client needs:

  - `setup`   — the commands ABOVE that split (technology, floorplan, netlist,
                BDB hierarchy).  The client drops them in the command box and
                runs them through /api/command.
  - `stages`  — the flow's OWN spelling of each pipeline stage, so the stage
                buttons drive a hier flow with `run_hier_bundler` /
                `generate_hier_topologies` / `run_planner hier 5` and a flat one
                with the plain commands, without the catalog saying which is
                which.
  - `flow`    — the whole pipeline tail in order, which the client's "Run flow"
                button replays, minus the commands that do not belong in a
                browser (viewers, `exit`, artifact writers — `_SKIP_IN_BROWSER`).

So a demo is added by adding a JSON entry, and a flow that changes takes its
demo with it.  The one thing that cannot be derived is the label.

**Paths.** A flow resolves a relative path against its OWN directory (the
engine's rule for `source`, `import_*`, `open_bdb`, …), but these lines are
replayed one at a time through /api/command with no enclosing script, where the
engine falls back to the CWD.  So every argument that names an existing FILE
relative to the flow's directory is rewritten repo-root-relative (the server's
documented CWD).  Keyed on "is an existing file", not on a list of commands,
because a list would silently miss the next path-taking command; a token that
is not a file (`:memory:`, a block name, a number) is left exactly as written.
"""
import json
import os

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CATALOG = os.path.join(_REPO, "demo", "web", "demos.json")

# Commands that begin the routing PIPELINE — the setup is everything BEFORE the
# first of these, the flow tail everything from it on.
_PIPELINE_CMDS = frozenset({
    "run_bundler", "run_hier_bundler",
    "generate_topologies", "generate_hier_topologies",
    "generate_topologies_for_bundle", "generate_topologies_for_hbundle",
    "run_planner", "run_nuts", "run_nuts_on_layer",
    "run_detailed_nuts", "run_dnuts",
    "check_design", "check_connectivity", "check_template_tracks",
    "ripup_reroute", "negotiate_congestion", "refine_selection",
    "report_wirelength", "report_wl", "report_overhead",
    "dump_topologies", "dump_hbundles", "dump_pins", "dump_ndr",
    "visualize", "visualize_topologies",
})

# Pipeline commands the "Run flow" button drops.  Two kinds, both because a
# browser is not a terminal: a viewer would block the server on a window nobody
# can see and `exit` would end the session; and the artifact WRITERS would
# scribble files into the user's checkout as a side effect of clicking a demo
# button (their relative paths resolve against the server's CWD, not the flow's
# directory, so they would not even land where the flow means them to).  A demo
# is for routing and looking; run the flow itself for its outputs.
_SKIP_IN_BROWSER = frozenset({
    "visualize", "visualize_topologies", "exit",
    "emit_guides", "export_def_blockages", "export_gds", "save_bdb",
})

# Which flow command supplies each stage button, first occurrence winning.
_STAGE_OF = {
    "run_bundler": "bundler",           "run_hier_bundler": "bundler",
    "generate_topologies": "topologies", "generate_hier_topologies": "topologies",
    "run_planner": "planner",
    "run_nuts": "nuts",
    "run_detailed_nuts": "dnuts",       "run_dnuts": "dnuts",
}

# What a demo falls back to when its flow never spells a stage out (a flow that
# stops at NUTS still gets a working dnuts button).
_DEFAULT_STAGES = {
    "bundler": "run_bundler", "topologies": "generate_topologies",
    "planner": "run_planner", "nuts": "run_nuts", "dnuts": "run_detailed_nuts",
}


def _strip(raw):
    """A flow line reduced to its command, or "" for comment/blank."""
    return raw.split("#", 1)[0].strip()


def _reroot(line, flow_dir):
    """`line` with every argument that names an existing file relative to
    `flow_dir` rewritten repo-root-relative (see the module docstring)."""
    out = []
    for i, tok in enumerate(line.split()):
        cand = os.path.join(flow_dir, tok)
        if i and os.path.isfile(cand):
            tok = os.path.relpath(os.path.realpath(cand), _REPO)
        out.append(tok)
    return " ".join(out)


def _missing_inputs(lines, flow_dir):
    """The flow's own precondition, read off its `require_file` declarations:
    the paths it names that do not exist, plus its hint.  A flow with fetched
    or generated inputs (flow/ariane133) is thus listed as unavailable WITH
    the remedy its author wrote, instead of failing when clicked."""
    missing, hint = [], ""
    for line in lines:
        parts = line.split()
        if not parts or parts[0] != "require_file":
            continue
        args = parts[1:]
        if "hint" in args:
            k = args.index("hint")
            hint = " ".join(args[k + 1:])
            args = args[:k]
        for p in args:
            if not os.path.isfile(os.path.join(flow_dir, p.strip('"'))):
                missing.append(p.strip('"'))
    return missing, hint


def _parse_flow(path):
    """Split one flow into (setup, stages, flow_tail, missing, hint)."""
    flow_dir = os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in (_strip(r) for r in fh) if ln]

    setup, tail, stages = [], [], {}
    in_tail = False
    for line in lines:
        verb = line.split()[0].lower()
        if not in_tail and verb in _PIPELINE_CMDS:
            in_tail = True
        rooted = _reroot(line, flow_dir)
        if not in_tail:
            setup.append(rooted)
            continue
        if verb in _STAGE_OF:
            stages.setdefault(_STAGE_OF[verb], rooted)
        if verb not in _SKIP_IN_BROWSER:
            tail.append(rooted)
    missing, hint = _missing_inputs(lines, flow_dir)
    return "\n".join(setup), stages, tail, missing, hint


def catalog():
    """The demo list the client renders in its picker.  Built fresh on every
    call, so editing a flow (or the manifest) is reflected without a server
    restart — the same property the hand-written catalog had."""
    with open(_CATALOG, encoding="utf-8") as fh:
        entries = json.load(fh)["demos"]

    out = []
    for e in entries:
        path = os.path.join(_REPO, e["flow"])
        if not os.path.isfile(path):
            out.append({"key": e["key"], "label": e["label"], "setup": "",
                        "stages": dict(_DEFAULT_STAGES), "flow": [],
                        "note": e.get("note", ""), "flow_path": e["flow"],
                        "unavailable": f"flow not found: {e['flow']}"})
            continue
        setup, stages, tail, missing, hint = _parse_flow(path)
        merged = dict(_DEFAULT_STAGES); merged.update(stages)
        d = {"key": e["key"], "label": e["label"], "setup": setup,
             "stages": merged, "flow": tail, "note": e.get("note", ""),
             "flow_path": e["flow"]}
        if missing:
            d["unavailable"] = (f"missing input(s): {', '.join(missing)}"
                                + (f" — {hint}" if hint else ""))
        out.append(d)
    return out
