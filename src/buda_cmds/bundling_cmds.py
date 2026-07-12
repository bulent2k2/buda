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
import math
import re

import buda


# ── generalized bundling (COMBINED + per-prefix overrides) ────────────────────
#
# Bundling is a partition by an equivalence relation.  The three pure
# strategies form a lattice: STRICT (finest) is refined by both CONVERGENT
# (shared receiver set) and BIDIRECTIONAL (shared endpoint set), which are
# incomparable; their JOIN — merge nets connected by a CHAIN of either
# relation — is COMBINED, the only genuinely new combination.  The join is
# computed by union-find over relation signatures, with per-net-prefix
# permission overrides (set_bundling): a merge via relation R happens only
# when the strategy enables R and BOTH nets permit it.  The pure C++ path
# stays byte-identical whenever neither COMBINED nor an override is in play.

_OVERRIDE_MODES = {
    "strict":           frozenset(),
    "no_convergent":    frozenset({"bidir"}),
    "no_bidirectional": frozenset({"conv"}),
    "combined":         frozenset({"conv", "bidir"}),
}

_STRATEGY_RELATIONS = {
    "STRICT":        frozenset(),
    "CONVERGENT":    frozenset({"conv"}),
    "BIDIRECTIONAL": frozenset({"bidir"}),
    "COMBINED":      frozenset({"conv", "bidir"}),
}


def _net_allowed_relations(session, net_name):
    """Relations the net permits, by the longest matching set_bundling
    prefix ('*' = global default; no match = fully permissive)."""
    ovr = getattr(session, "_bundling_overrides", None) or {}
    best, best_len = None, -1
    for prefix, mode in ovr.items():
        if prefix == "*":
            if best_len < 0:
                best, best_len = mode, 0
        elif net_name.startswith(prefix) and len(prefix) > best_len:
            best, best_len = mode, len(prefix)
    return _OVERRIDE_MODES[best] if best else _OVERRIDE_MODES["combined"]


def _generalized_bundles(session, strategy):
    """Union-find partition of the netlist under the strategy's relations ∩
    per-net permissions.  Reproduces the pure C++ partitions exactly when
    unrestricted (the equivalence tests pin this), and computes the JOIN
    for COMBINED.  Returns a list of HBundle in first-net order."""
    eps = session._net_endpoints
    names = list(eps)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    strat_rels = _STRATEGY_RELATIONS[strategy]
    by_sig = {}
    for n in names:
        drv, rcvs = eps[n]
        allowed = strat_rels & _net_allowed_relations(session, n)
        rec_sig = ",".join(sorted(set(rcvs)))
        sigs = [("strict", f"DRV:{drv}|REC:{rec_sig}")]
        if "conv" in allowed:
            sigs.append(("conv", f"REC:{rec_sig}"))
        if "bidir" in allowed:
            all_sig = ",".join(sorted({drv, *rcvs}))
            sigs.append(("bidir", f"BIDIR:{all_sig}"))
        for key in sigs:
            first = by_sig.setdefault(key, n)
            if first is not n:
                union(first, n)

    groups = {}
    for n in names:                       # first-net order
        groups.setdefault(find(n), []).append(n)

    out = []
    for i, (root, nets) in enumerate(groups.items(), start=1):
        b = buda.HBundle()
        b.id = i
        b.net_names = nets
        # Reason: the finest single signature the whole group shares (so
        # familiar STRICT/REC/BIDIR reasons survive), else COMBINED.
        def shared(kind, sig_of):
            vals = {sig_of(*eps[n]) for n in nets}
            return vals.pop() if len(vals) == 1 else None
        r = shared("strict", lambda d, rs: f"DRV:{d}|REC:{','.join(sorted(set(rs)))}")
        if r is None:
            r = shared("conv", lambda d, rs: f"REC:{','.join(sorted(set(rs)))}")
        if r is None:
            r = shared("bidir", lambda d, rs: f"BIDIR:{','.join(sorted({d, *rs}))}")
        if r is None:
            insts = sorted({i for n in nets for i in (eps[n][0], *eps[n][1])})
            r = "COMBINED:" + ",".join(insts)
        b.reason = r
        d0, r0 = eps[nets[0]]
        b.num_terminals = 1 + len(set(r0))
        out.append(b)
    return out


# ── bundle bit bound (static + auto busterm-edge) ─────────────────────────────

def _bus_group_key(net_name):
    """Bus identity for keep-buses-together splitting: '<bus>_<idx>' /
    '<bus>_b<idx>' (the add_bus/add_net forms — same regex as
    _bundle_net_summary) fold to '<bus>'; other names are their own group."""
    m = re.match(r'^(.*?)_([A-Za-z]*)(\d+)$', net_name)
    return m.group(1) if m else net_name


def _min_bit_pitch(session):
    """Smallest per-bit pitch over all pattern layers (the densest layer a
    stub could land on) — eff_bus_width(1, 0.0, lid) is bit_pitch on a
    pattern layer and 0.0 otherwise.  Falls back to the NUTS track pitch
    when no layer has a pattern."""
    pitches = []
    for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL):
        for lid in session.layers.get_layer_ids_by_dir(d):
            p = session.layers.eff_bus_width(1, 0.0, lid)
            if p > 0:
                pitches.append(p)
    return min(pitches) if pitches else float(session._nuts_pitch or 1.0)


def _split_oversized_bundles(session, raw_bundles):
    """Optional bundle bit bound (set_max_bundle_bits): split any bundle
    over the limit into balanced parts, keeping bits of the same bus
    together when possible.

    Static bound N: a bundle of `total` bits needs ceil(total/N) parts —
    600 bits at N=512 become two ~300-bit parts, not 512+88.

    AUTO bound (busterm edge): per endpoint block B, the bits that
    physically land on B's face are the bundle's nets INCIDENT to B (the
    per-bit taper places exactly those), and the shortest edge of B can
    host floor(min(w,h) / min_bit_pitch) bits — so the bundle needs
    ceil(bits_at_B / cap_B) parts for every B.  The final part count is
    the max over all constraints.  Every split is reported LOUD with the
    binding constraint."""
    max_bits = getattr(session, "_max_bundle_bits", None)
    auto = getattr(session, "_max_bundle_bits_auto", False)
    if not max_bits and not auto:
        return raw_bundles
    eps = session._net_endpoints
    pitch = _min_bit_pitch(session) if auto else None

    out = []
    next_id = max((b.id for b in raw_bundles), default=0)

    for b in raw_bundles:
        nets = list(b.get_net_names())
        total = len(nets)
        n_parts, why = 1, ""
        if max_bits and total > max_bits:
            n_parts = math.ceil(total / max_bits)
            why = f"static limit {max_bits}"
        if auto and pitch and pitch > 0:
            at_block = {}
            for n in nets:
                e = eps.get(n)
                if e is None:
                    continue
                for blk in {e[0], *e[1]}:
                    at_block[blk] = at_block.get(blk, 0) + 1
            for blk, bits_at in at_block.items():
                if not session.fp.has_block(blk):
                    continue
                r = session.fp.get_block_bounds(blk)
                edge = min(r.x2 - r.x1, r.y2 - r.y1)
                cap = max(1, int(edge / pitch))
                need = math.ceil(bits_at / cap)
                if need > n_parts:
                    n_parts = need
                    why = (f"busterm edge of '{blk}' ({edge} units / pitch "
                           f"{pitch:g} = {cap} bits) sees {bits_at} bits")
        if n_parts <= 1:
            out.append(b)
            continue

        # Balanced partition keeping bus groups together: fill parts to the
        # balanced target, cutting only at bus-group boundaries when the
        # group fits; a group larger than the target is chunked evenly.
        target = math.ceil(total / n_parts)
        groups, order = {}, []
        for n in nets:
            k = _bus_group_key(n)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(n)
        parts, cur = [], []
        for k in order:
            g = groups[k]
            if len(g) > target:
                # Oversized bus: chunk evenly (sizes differ by <= 1).
                if cur:
                    parts.append(cur)
                    cur = []
                n_chunks = math.ceil(len(g) / target)
                base, extra = divmod(len(g), n_chunks)
                pos = 0
                for c in range(n_chunks):
                    size = base + (1 if c < extra else 0)
                    parts.append(g[pos:pos + size])
                    pos += size
                continue
            if cur and len(cur) + len(g) > target:
                parts.append(cur)
                cur = []
            cur.extend(g)
        if cur:
            parts.append(cur)

        sizes = "+".join(str(len(p)) for p in parts)
        print(f"[Bundler] split bundle {b.id} ({total} bits) into "
              f"{len(parts)} part(s) ({sizes}): {why}")
        for k, part in enumerate(parts, start=1):
            nb = buda.HBundle()
            if k == 1:
                nb.id = b.id
            else:
                next_id += 1
                nb.id = next_id
            nb.net_names = part
            nb.reason = f"{b.reason}|SPLIT:{k}/{len(parts)}"
            nb.num_terminals = b.num_terminals
            out.append(nb)
    return out


def cmd_run_bundler(session, cmd, args, cmd_line):
    # run_bundler [STRICT|CONVERGENT|BIDIRECTIONAL|COMBINED]  (default STRICT)
    strat_arg = args[0].upper() if args else "STRICT"
    if strat_arg not in ("STRICT", "CONVERGENT", "BIDIRECTIONAL", "COMBINED"):
        print(f"Error: run_bundler strategy must be STRICT, CONVERGENT, "
              f"BIDIRECTIONAL or COMBINED, got '{args[0]}'"); return
    if strat_arg == "CONVERGENT":
        # CONVERGENT groups nets by shared receiver only, so a bundle can
        # span several DIFFERENT driver blocks at different locations (a
        # many-to-one fan-in).  Topology generation models such a bundle
        # as a fan-in tree rooted at the shared sink with every driver
        # block as a leaf (_bundle_endpoints), and check_design's
        # net-driver fidelity check verifies every endpoint block is
        # attached.  See docs/internal/convergent_bundling.md.
        session.bundler.set_strategy(buda.Strategy.CONVERGENT)
        print("[Bundler] CONVERGENT: nets grouped by shared receiver; a "
              "multi-driver bundle routes as a fan-in tree rooted at the "
              "shared sink (see docs/internal/convergent_bundling.md).")
    elif strat_arg == "BIDIRECTIONAL":
        # BIDIRECTIONAL bundles nets that connect the SAME set of blocks
        # in any direction (A->B with B->A, a->b,c with b->c,a / c->b,a).
        # Routing is block-to-block and direction-agnostic, so the single
        # trunk serves every net — no warning needed.  (In the visualizer
        # such a busterm is both a driver and a receiver; it gets its own
        # symbol.)
        session.bundler.set_strategy(buda.Strategy.BIDIRECTIONAL)
    elif strat_arg == "COMBINED":
        # COMBINED = the JOIN of CONVERGENT and BIDIRECTIONAL: nets merge
        # when connected by a CHAIN of shared-receiver-set or shared-
        # endpoint-set relations (union-find).  Routing is sound because
        # the fan-in machinery is direction-agnostic and per-bit tapered:
        # every net rides only its own driver→sink path, verified by
        # NET_DRIVER_OPEN / BIT_SHORT.  Restrict per net-prefix with
        # set_bundling; bound bundle size with set_max_bundle_bits.
        print("[Bundler] COMBINED: join of CONVERGENT and BIDIRECTIONAL "
              "(transitive; see docs/internal/convergent_bundling.md). "
              "Restrict per prefix with set_bundling.")
    else:
        session.bundler.set_strategy(buda.Strategy.STRICT)

    overrides = getattr(session, "_bundling_overrides", None) or {}
    if strat_arg == "COMBINED" or overrides:
        # Generalized Python path: COMBINED needs the union-find join, and
        # per-prefix overrides need per-net permissions the C++ signature
        # grouping has no channel for.  Equivalence to the pure C++ modes
        # when unrestricted is pinned by test_bundler_combined.py.
        raw_bundles = _generalized_bundles(session, strat_arg)
    else:
        raw_bundles = session.bundler.run(session.netlist)
    raw_bundles = _split_oversized_bundles(session, raw_bundles)
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


def cmd_set_bundling(session, cmd, args, cmd_line):
    # set_bundling <prefix>|* <strict|no_convergent|no_bidirectional|combined>
    # Per-net-prefix bundling permission (longest prefix wins; '*' = global
    # default).  A merge via a relation happens only when BOTH nets permit
    # it, so `set_bundling clk_ strict` keeps clock nets out of every
    # convergent/bidirectional merge under any strategy.
    if len(args) != 2 or args[1].lower() not in _OVERRIDE_MODES:
        print("Error: usage: set_bundling <prefix>|* "
              "<strict|no_convergent|no_bidirectional|combined>")
        return
    prefix, mode = args[0], args[1].lower()
    if not hasattr(session, "_bundling_overrides"):
        session._bundling_overrides = {}
    if mode == "combined" and prefix == "*":
        # Global 'combined' = fully permissive = no override at all.
        session._bundling_overrides.pop("*", None)
    else:
        session._bundling_overrides[prefix] = mode
    print(f"[Bundler] bundling override: '{prefix}' -> {mode} "
          f"({len(session._bundling_overrides)} override(s) active; applies "
          f"at the next run_bundler)")


def cmd_set_max_bundle_bits(session, cmd, args, cmd_line):
    # set_max_bundle_bits <N|auto|off> [auto]
    # Optional bundle bit bound, applied as a split pass after bundling
    # (any strategy).  N = static cap: a bundle over N bits splits into
    # balanced parts (600 @ 512 -> 300+300), bits of one bus kept together.
    # 'auto' = dynamic per-bundle cap from the shortest busterm edge: per
    # endpoint block, the bits incident to it (what the per-bit taper
    # actually lands on its face) must fit floor(min(w,h)/min_bit_pitch).
    # Both may be active (the max part count wins); 'off' clears both.
    if not args:
        print("Error: usage: set_max_bundle_bits <N|auto|off> [auto]")
        return
    a0 = args[0].lower()
    if a0 == "off":
        session._max_bundle_bits = None
        session._max_bundle_bits_auto = False
        print("[Bundler] bundle bit bound: off")
        return
    if a0 == "auto":
        session._max_bundle_bits = None
        session._max_bundle_bits_auto = True
    else:
        try:
            n = int(args[0])
        except ValueError:
            print(f"Error: set_max_bundle_bits expects a bit count, 'auto' "
                  f"or 'off', got '{args[0]}'")
            return
        if n < 1:
            print("Error: set_max_bundle_bits bound must be >= 1")
            return
        session._max_bundle_bits = n
        session._max_bundle_bits_auto = (len(args) > 1
                                         and args[1].lower() == "auto")
    static = session._max_bundle_bits
    print(f"[Bundler] bundle bit bound: "
          f"{'static ' + str(static) if static else ''}"
          f"{' + ' if static and session._max_bundle_bits_auto else ''}"
          f"{'auto (busterm edge)' if session._max_bundle_bits_auto else ''}"
          f" (applies at the next run_bundler)")


def cmd_run_hier_bundler(session, cmd, args, cmd_line):
    # run_hier_bundler [depth <N>] [STRICT|BIDIRECTIONAL]
    if session.bdb is None:
        print("Error: run_hier_bundler requires an open BDB (use open_bdb first)"); return
    if (getattr(session, "_bundling_overrides", None)
            or getattr(session, "_max_bundle_bits", None)
            or getattr(session, "_max_bundle_bits_auto", False)):
        print("Warning: set_bundling / set_max_bundle_bits apply to the FLAT "
              "run_bundler only — run_hier_bundler ignores them (COMBINED and "
              "the bit bound are flat-flow features, like CONVERGENT).")
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
    "set_bundling": cmd_set_bundling,
    "set_max_bundle_bits": cmd_set_max_bundle_bits,
}
