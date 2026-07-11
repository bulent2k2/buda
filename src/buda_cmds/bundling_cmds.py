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

"""Stage 1 — bundler commands.

Command handlers extracted verbatim from BudaSession.do_command
(the CLI registry split; self -> session was the only body change).
Each handler takes (session, cmd, args, cmd_line) and is registered
in this module's COMMANDS dict; the buda_cmds package assembles the
full registry that buda_cli.do_command dispatches through.
"""
import buda


def cmd_run_bundler(session, cmd, args, cmd_line):
    # run_bundler [STRICT|CONVERGENT|BIDIRECTIONAL]  (default STRICT)
    strat_arg = args[0].upper() if args else "STRICT"
    if strat_arg not in ("STRICT", "CONVERGENT", "BIDIRECTIONAL"):
        print(f"Error: run_bundler strategy must be STRICT, CONVERGENT "
              f"or BIDIRECTIONAL, got '{args[0]}'"); return
    if strat_arg == "CONVERGENT":
        # CONVERGENT groups nets by shared receiver only, so a bundle can
        # span several DIFFERENT driver blocks at different locations.
        # Topology generation (a single src->dst per bundle) then routes
        # from one driver and leaves the others unrouted.  Warn rather
        # than silently misroute.  See docs/internal/convergent_bundling.md.
        session.bundler.set_strategy(buda.Strategy.CONVERGENT)
        print("Warning: run_bundler CONVERGENT groups nets by shared "
              "receiver only; bundles that span multiple driver blocks "
              "are routed from a single driver (the others are left "
              "unrouted). See docs/internal/convergent_bundling.md.")
    elif strat_arg == "BIDIRECTIONAL":
        # BIDIRECTIONAL bundles nets that connect the SAME set of blocks
        # in any direction (A->B with B->A, a->b,c with b->c,a / c->b,a).
        # Routing is block-to-block and direction-agnostic, so the single
        # trunk serves every net — no warning needed.  (In the visualizer
        # such a busterm is both a driver and a receiver; it gets its own
        # symbol.)
        session.bundler.set_strategy(buda.Strategy.BIDIRECTIONAL)
    else:
        session.bundler.set_strategy(buda.Strategy.STRICT)
    raw_bundles = session.bundler.run(session.netlist)
    session.bundles = []
    for b in raw_bundles:
        w = buda.BundleWrapper()
        w.input.original_bundle = b
        w.input.width = len(b.get_net_names()) * 1.5 # 1.5 layout-units per bit
        session.bundles.append(w)
    session._bu_clone_from = {}   # fresh ids: drop stale clone provenance
    print(f"Bundler created {len(session.bundles)} hbundles.")
    session._bundler_strategy = strat_arg
    n = session._persist_bundles(strat_arg)
    if n:
        print(f"[BDB] persisted {n} bundle(s) to the open BDB.")


def cmd_run_hier_bundler(session, cmd, args, cmd_line):
    # run_hier_bundler [depth <N>] [STRICT|BIDIRECTIONAL]
    if session.bdb is None:
        print("Error: run_hier_bundler requires an open BDB (use open_bdb first)"); return
    max_depth = 1
    if "depth" in args:
        idx = list(args).index("depth")
        if idx + 1 < len(args):
            max_depth = int(args[idx + 1])
    # Optional strategy token (anything that isn't 'depth'/its value).
    strat_toks = [a.upper() for a in args
                  if a.lower() != "depth" and not a.isdigit()]
    strat = strat_toks[0] if strat_toks else "STRICT"
    if strat not in ("STRICT", "BIDIRECTIONAL"):
        print(f"Error: run_hier_bundler strategy must be STRICT or "
              f"BIDIRECTIONAL, got '{strat}'"); return
    hb = buda.HierarchicalBundler(session.bdb)
    # BIDIRECTIONAL is direction-agnostic and connects the same blocks, so
    # (like the flat run_bundler) it routes correctly — no warning needed.
    hb.set_strategy(buda.Strategy.BIDIRECTIONAL if strat == "BIDIRECTIONAL"
                    else buda.Strategy.STRICT)
    raw_bundles = hb.run(max_depth)
    session.bundles = []
    for b in raw_bundles:
        w = buda.BundleWrapper()
        w.input.original_bundle = b
        w.input.width = len(b.get_net_names()) * 1.5
        session.bundles.append(w)
    session._hier_bundles_orig = list(session.bundles)  # snapshot for dump_hbundles
    # Fresh bundles reuse small integer ids: stale id-keyed clone provenance
    # from a previous split would stamp a bogus cloned_from on an unrelated
    # bundle at the next persist (Codex #253).  The NAME registry survives,
    # so a re-split reuses the same clone name.  Template dogleg slots are
    # equally id-keyed and index into the OLD wrappers' pools — drop them.
    session._bu_clone_from = {}
    session._bu_dogleg_slot = {}
    session._bu_dogleg_originals = {}
    counts = {}
    for b in raw_bundles:
        counts[b.level] = counts.get(b.level, 0) + 1
    summary = ", ".join(f"D{d}: {n}" for d, n in sorted(counts.items()))
    print(f"HierBundler: {len(raw_bundles)} hbundles ({summary})")
    # Warn about nets that had pins in BDB but ended up in no bundle.
    bundled_nets: set[str] = set()
    for b in raw_bundles:
        bundled_nets.update(b.get_net_names())
    all_bdb_nets = {r.name for r in session.bdb.all_nets()}
    dropped = sorted(all_bdb_nets - bundled_nets)
    if dropped:
        shown = dropped[:5]
        ellipsis_str = f" … and {len(dropped)-5} more" if len(dropped) > 5 else ""
        print(f"  Warning: {len(dropped)} net(s) not placed in any bundle "
              f"(possibly UNKNOWN direction or missing receiver): "
              f"{', '.join(shown)}{ellipsis_str}")
    session._bundler_strategy = strat
    n = session._persist_bundles(strat)
    if n:
        print(f"[BDB] persisted {n} bundle(s) to the open BDB.")


def cmd_dump_hbundles(session, cmd, args, cmd_line):
    # Usage: dump_hbundles [expanded] [depth N]
    # Without 'expanded': prints the pre-expansion HBundle list (from _hier_bundles_orig).
    # With 'expanded':    prints the current session.bundles (post-expansion after run_planner hier).
    # With 'depth N':     filters to bundles at level N only.
    use_expanded = "expanded" in args
    filter_depth = None
    if "depth" in args:
        idx = list(args).index("depth")
        if idx + 1 < len(args):
            filter_depth = int(args[idx + 1])
    source = session.bundles if use_expanded else session._hier_bundles_orig
    if not source:
        label = "expanded bundles" if use_expanded else "original HBundles"
        print(f"  (no {label} — run run_hier_bundler first)")
    else:
        for w in source:
            b = w.input.original_bundle
            if filter_depth is not None and b.level != filter_depth:
                continue
            if b.drv_spec_depth >= 0:
                kind = "cross-level"
            elif b.cell_context:
                kind = f"cell:{b.cell_context}"
            else:
                kind = "cross-block"
            short_reason = b.reason[:50].rstrip(',')
            cands = len(w.input.candidates)
            inst_str = ""
            if b.instances:
                insts = list(b.instances)
                shown = insts[:3]
                ellipsis = "…" if len(insts) > 3 else ""
                inst_str = f"  [{', '.join(shown)}{ellipsis}]"
            print(f"hb-{b.id:<3}  D{b.level}  {kind:<24}  \"{short_reason}\"  "
                  f"nets={len(b.get_net_names())}  cands={cands}{inst_str}")


COMMANDS = {
    "run_bundler": cmd_run_bundler,
    "run_hier_bundler": cmd_run_hier_bundler,
    "dump_hbundles": cmd_dump_hbundles,
}
