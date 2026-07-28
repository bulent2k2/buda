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

from ._options import reject_unknown_options


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
    for COMBINED.  Returns a list of HBundle in FIRST-NET order — the C++
    path emits sorted-signature order, so the two are partition-equal but
    order-different: activating any override flips to this path and can
    reorder bundle IDs even when the partition is unchanged, which may
    perturb downstream width-tie-breaks (same partition, possibly
    different routing)."""
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
        def shared(sig_of):
            vals = {sig_of(*eps[n]) for n in nets}
            return vals.pop() if len(vals) == 1 else None
        r = shared(lambda d, rs: f"DRV:{d}|REC:{','.join(sorted(set(rs)))}")
        if r is None:
            r = shared(lambda d, rs: f"REC:{','.join(sorted(set(rs)))}")
        if r is None:
            r = shared(lambda d, rs: f"BIDIR:{','.join(sorted({d, *rs}))}")
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


def _partition_nets(nets, target, caps, inc_fn):
    """Balanced net partition keeping bus groups together while ENFORCING
    per-block caps per part (the shared core of the flat and hier splitters).

    `target` is the balanced size ceiling, `caps` maps a constrained block to
    its per-part bit cap (empty = no cap), and `inc_fn(net)` returns the blocks
    a net is incident to.  Each part closes before any cap would be exceeded
    (net-level fallback for a group that violates a cap on its own), so a
    single net always fits an empty part (caps are >= 1).  Returns a list of
    parts (each a list of net names, bus groups kept contiguous)."""
    groups, order = {}, []
    for n in nets:
        k = _bus_group_key(n)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(n)

    parts, cur, cur_cnt = [], [], {}

    def _fits(add_nets, base_len, base_cnt):
        if base_len + len(add_nets) > target:
            return False
        if caps:
            cnt = dict(base_cnt)
            for n in add_nets:
                for blk in inc_fn(n):
                    if blk in caps:
                        cnt[blk] = cnt.get(blk, 0) + 1
                        if cnt[blk] > caps[blk]:
                            return False
        return True

    def _close():
        nonlocal cur, cur_cnt
        if cur:
            parts.append(cur)
            cur, cur_cnt = [], {}

    def _add(add_nets):
        for n in add_nets:
            cur.append(n)
            for blk in inc_fn(n):
                if blk in caps:
                    cur_cnt[blk] = cur_cnt.get(blk, 0) + 1

    for k in order:
        g = groups[k]
        if _fits(g, len(cur), cur_cnt):
            _add(g)
            continue
        _close()
        if _fits(g, 0, {}):
            _add(g)
            continue
        # The group alone exceeds the target or a block cap: pack its nets
        # individually, closing the part before any violation.
        for n in g:
            if not _fits([n], len(cur), cur_cnt):
                _close()
            _add([n])
    _close()
    return parts


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

        # Balanced partition keeping bus groups together while ENFORCING the
        # auto per-block caps per part (Codex #273): n_parts alone sizes the
        # balanced target from the TOTAL, which cannot guarantee a part's
        # bits incident to one block fit that block's edge when the incident
        # bits cluster in a bus group smaller than the target — so each part
        # also tracks per-constrained-block incidence and closes before any
        # cap would be exceeded (net-level fallback for a group that
        # violates a cap on its own).
        target = math.ceil(total / n_parts)
        caps = {}
        if auto and pitch and pitch > 0:
            for n in nets:
                e = eps.get(n)
                if e is None:
                    continue
                for blk in {e[0], *e[1]}:
                    if blk in caps or not session.fp.has_block(blk):
                        continue
                    r = session.fp.get_block_bounds(blk)
                    caps[blk] = max(1, int(min(r.x2 - r.x1,
                                               r.y2 - r.y1) / pitch))

        def _inc(net):
            e = eps.get(net)
            return () if e is None else tuple({e[0], *e[1]})

        parts = _partition_nets(nets, target, caps, _inc)

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


def _hier_endpoint_blocks(session, b):
    """Shared endpoint block names for a NON-fan-in hier bundle (every net
    lands on the same blocks): cell-local busterm paths (kept INSTANCE-
    QUALIFIED — e.g. `core_i1/c0` — so the AUTO cap resolves the bundle's own
    reference instance rather than any same-named leaf in another cell, Codex
    #382 P2), cross-level spec paths, or the parsed cross-block reason.  Used
    only for the AUTO bit-bound cap."""
    if b.cell_context and b.entry_busterm_ids:
        ids = list(b.entry_busterm_ids) + list(b.exit_busterm_ids)
        return [e.removeprefix('bt:') for e in ids]
    if b.drv_spec_depth >= 0:
        return [b.drv_spec_path, *b.rcv_spec_paths]
    src, dsts = session._parse_bundle_reason(b.reason)
    return [src, *dsts] if src else []


def _fanin_core(part_drvs, part_rcvs):
    """Rebuild a fan-in part's (reason, unique_drivers, root, extra_receivers)
    from its per-net drivers/receivers, matching the C++ FANIN reason builder
    (root = shared sink; leaves = unique drivers + any receiver beyond root).
    So a split part that no longer spans every original leaf re-scopes to just
    the blocks its member bits touch — no spurious 0-bit leaf stub.  Returns
    (None, [], None, []) when the part has no resolvable sink or no leaves."""
    root = next((rl[0] for rl in part_rcvs if rl), None)
    if root is None:
        return None, [], None, []
    uniq_drv = []
    for d in part_drvs:
        if d and d != root and d not in uniq_drv:
            uniq_drv.append(d)
    extra_rcv = []
    for rl in part_rcvs:
        for r in rl:
            if r and r != root and r not in uniq_drv and r not in extra_rcv:
                extra_rcv.append(r)
    leaves = uniq_drv + extra_rcv
    if not leaves:
        return None, [], None, []
    reason = "FANIN:" + root + "|FROM:" + "".join(x + "," for x in leaves)
    return reason, uniq_drv, root, extra_rcv


def _split_hier_bundles(session, raw_bundles):
    """Hier twin of _split_oversized_bundles (set_max_bundle_bits): split an
    over-limit TEMPLATE bundle into balanced parts BEFORE per-instance
    expansion, so the split propagates identically to every instance through
    the template↔replica linkage (each part is its own template, replicated
    at run_planner hier).  Every HBundle hier field is preserved on each part
    (_clone_hbundle_with_id); the per-net-aligned fan-in arrays
    (net_drivers/net_receivers) are split in the same net partition.

    A cell-local template with multiple occurrences carries its extra
    occurrences as separate REPLICA bundles (`parent_id` → the template id),
    whose nets `_expand_hier_bundles` feeds per instance via the donor keying
    `(template_id, instance)`.  A split must therefore split the template AND
    its replicas in LOCKSTEP and rewire each replica part's `parent_id` to the
    CORRESPONDING template part — else the donor keys collapse onto the
    original template id (last part wins) and later template parts fall back to
    the reference instance's net names (Codex #382 P1).

    Static cap N needs only the net count, so it is fully general across the
    three hier bundle kinds.  AUTO cap (busterm edge): per-net incident blocks
    come from the HBundle metadata (fan-in per-net endpoints, else the shared
    instance-qualified endpoint paths) and block dimensions from the open BDB,
    resolved by exact component name (a '/<leaf>' suffix is only a last-resort
    fallback).  A block whose bounds cannot be resolved is skipped for the AUTO
    cap with a one-line note."""
    max_bits = getattr(session, "_max_bundle_bits", None)
    auto = getattr(session, "_max_bundle_bits_auto", False)
    if (not max_bits and not auto) or not raw_bundles:
        return raw_bundles
    pitch = _min_bit_pitch(session) if auto else None
    comps = list(session.bdb.all_components()) if (auto and session.bdb) else []

    edge_cache = {}
    unresolved = set()

    def _min_edge(blk):
        if blk in edge_cache:
            return edge_cache[blk]
        cand = None
        for c in comps:
            if c.name == blk:
                cand = c
                break
            if cand is None and c.name.endswith('/' + blk):
                cand = c    # last-resort: unqualified leaf name
        edge = None if cand is None else min(cand.x2 - cand.x1,
                                             cand.y2 - cand.y1)
        edge_cache[blk] = edge
        if edge is None:
            unresolved.add(blk)
        return edge

    counter = [max((b.id for b in raw_bundles), default=0)]

    def _new_id():
        counter[0] += 1
        return counter[0]

    def _split_one(b):
        """Return the HBundle parts b splits into (>=1; part 1 keeps b.id).
        Preserves every hier field per part; splits the per-net fan-in arrays
        and re-scopes a fan-in part's reason/busterm ids to the leaves its bits
        touch."""
        nets = list(b.get_net_names())
        total = len(nets)
        drvs = list(b.net_drivers)
        rcvs = [list(r) for r in b.net_receivers]
        fanin = bool(drvs) and len(drvs) == total
        net_idx = {n: i for i, n in enumerate(nets)}

        def _inc(net, shared):
            if fanin:
                i = net_idx.get(net)
                if i is None:
                    return ()
                return tuple({drvs[i], *rcvs[i]} - {""})
            return shared

        n_parts, why = 1, ""
        if max_bits and total > max_bits:
            n_parts = math.ceil(total / max_bits)
            why = f"static limit {max_bits}"

        caps, inc_all = {}, {}
        if auto and pitch and pitch > 0:
            shared = None
            if not fanin:
                shared = tuple(dict.fromkeys(_hier_endpoint_blocks(session, b)))
            at_block = {}
            for n in nets:
                blks = _inc(n, shared)
                inc_all[n] = blks
                for blk in blks:
                    at_block[blk] = at_block.get(blk, 0) + 1
            for blk, bits_at in at_block.items():
                edge = _min_edge(blk)
                if edge is None or edge <= 0:
                    continue
                cap = max(1, int(edge / pitch))
                caps[blk] = cap
                need = math.ceil(bits_at / cap)
                if need > n_parts:
                    n_parts = need
                    why = (f"busterm edge of '{blk}' ({edge:g} units / pitch "
                           f"{pitch:g} = {cap} bits) sees {bits_at} bits")

        if n_parts <= 1:
            return [b]

        target = math.ceil(total / n_parts)
        inc_fn = (lambda n: inc_all.get(n, ())) if caps else (lambda n: ())
        parts = _partition_nets(nets, target, caps, inc_fn)

        sizes = "+".join(str(len(p)) for p in parts)
        print(f"[HierBundler] split bundle {b.id} ({total} bits) into "
              f"{len(parts)} part(s) ({sizes}): {why}")
        result = []
        for k, part in enumerate(parts, start=1):
            nb = session._clone_hbundle_with_id(b, b.id if k == 1 else _new_id())
            nb.net_names = part
            suffix = f"|SPLIT:{k}/{len(parts)}"
            if fanin:
                pd = [drvs[net_idx[n]] for n in part]
                pr = [rcvs[net_idx[n]] for n in part]
                nb.net_drivers = pd
                nb.net_receivers = pr
                core, uniq_drv, root, extra_rcv = _fanin_core(pd, pr)
                nb.reason = (core if core else b.reason) + suffix
                # Cell-local fan-in (case a) reads endpoints from the busterm
                # ids, not the reason — re-scope those to the part too.
                if core and b.cell_context and b.entry_busterm_ids:
                    nb.entry_busterm_ids = ["bt:" + d for d in uniq_drv]
                    nb.exit_busterm_ids = (["bt:" + root]
                                           + ["bt:" + r for r in extra_rcv])
            else:
                nb.reason = b.reason + suffix
            result.append(nb)
        return result

    # Template↔replica grouping: a cell-local replica's parent_id points to its
    # template.  Skip replicas in the main pass and split each with its template
    # in lockstep so the parent linkage can be rewired part-for-part.
    cell_ids = {b.id for b in raw_bundles if b.cell_context}

    def _is_replica(b):
        return bool(b.cell_context and b.parent_id in cell_ids and b.instances)

    replicas_of = {}
    for b in raw_bundles:
        if _is_replica(b):
            replicas_of.setdefault(b.parent_id, []).append(b)

    out = []
    for b in raw_bundles:
        if _is_replica(b):
            continue                       # handled with its template below
        tparts = _split_one(b)
        out.extend(tparts)
        for rb in replicas_of.get(b.id, ()):
            rparts = _split_one(rb)
            if len(rparts) == len(tparts):
                for tp, rp in zip(tparts, rparts):
                    rp.parent_id = tp.id    # part k replica → part k template
            else:
                # Non-congruent split (should not happen for congruent
                # instances): pin every replica part to the first template part
                # and warn LOUD rather than silently mis-map the donor nets.
                for rp in rparts:
                    rp.parent_id = tparts[0].id
                print(f"  Warning: replica bundle {rb.id} split into "
                      f"{len(rparts)} part(s) != template {b.id}'s "
                      f"{len(tparts)} — parent linkage pinned to part 1")
            out.extend(rparts)

    if unresolved:
        shown = sorted(unresolved)[:5]
        more = f" … and {len(unresolved)-5} more" if len(unresolved) > 5 else ""
        print(f"  Note: AUTO bit-bound could not resolve bounds for "
              f"{len(unresolved)} block(s) (skipped for the cap): "
              f"{', '.join(shown)}{more}")
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
          f"at the next run_bundler / run_hier_bundler)")


def cmd_set_max_bundle_bits(session, cmd, args, cmd_line):
    # set_max_bundle_bits <N|auto|off> [auto]
    # Optional bundle bit bound, applied as a split pass after bundling
    # (any strategy).  N = static cap: a bundle over N bits splits into
    # balanced parts (600 @ 512 -> 300+300), bits of one bus kept together.
    # 'auto' = dynamic per-bundle cap from the shortest busterm edge: per
    # endpoint block, the bits incident to it (what the per-bit taper
    # actually lands on its face) must fit floor(min(w,h)/min_bit_pitch).
    # Both may be active (the max part count wins); 'off' clears both.
    # Applies to BOTH run_bundler and run_hier_bundler — a hier TEMPLATE
    # bundle is split before per-instance expansion, so the split propagates
    # identically to every occurrence (each part is its own template).
    if not args:
        print("Error: usage: set_max_bundle_bits <N|auto|off> [auto]")
        return
    # The only valid SECOND token is `auto` (arg0 is a count / auto / off,
    # validated below).  Reject anything else instead of silently dropping it.
    reject_unknown_options("set_max_bundle_bits",
                           [a.lower() for a in args[1:]], ("auto",))
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
          f" (applies at the next run_bundler / run_hier_bundler)")


def cmd_run_hier_bundler(session, cmd, args, cmd_line):
    # run_hier_bundler [depth <N>] [STRICT|CONVERGENT|BIDIRECTIONAL|COMBINED]
    if session.bdb is None:
        print("Error: run_hier_bundler requires an open BDB (use open_bdb first)"); return
    max_depth = 1
    depth_val_idx = -1
    if "depth" in args:
        idx = list(args).index("depth")
        if idx + 1 >= len(args) or not args[idx + 1].isdigit():
            print("Error: run_hier_bundler depth requires an integer value"); return
        max_depth = int(args[idx + 1])
        depth_val_idx = idx + 1
    # Optional strategy token: any arg that isn't `depth` or its value.  Reject
    # an unknown token instead of silently keeping only the first (the old
    # strat_toks[0] dropped a typo like `CONVERGENT foo`).
    strat_toks = [a.upper() for i, a in enumerate(args)
                  if a.lower() != "depth" and i != depth_val_idx]
    reject_unknown_options("run_hier_bundler", strat_toks,
                           ("STRICT", "CONVERGENT", "BIDIRECTIONAL", "COMBINED"))
    if len(strat_toks) > 1:
        print(f"Error: run_hier_bundler: at most one strategy, got "
              f"{', '.join(strat_toks)}"); return
    strat = strat_toks[0] if strat_toks else "STRICT"
    hb = buda.HierarchicalBundler(session.bdb)
    # BIDIRECTIONAL is direction-agnostic and connects the same blocks, so
    # (like the flat run_bundler) it routes correctly — no warning needed.
    # A CONVERGENT/COMBINED multi-driver group becomes a fan-in bundle
    # (reason 'FANIN:root|FROM:leaves', per-net endpoints in
    # net_drivers/net_receivers) that generation routes as a per-bit
    # tapered fan-in tree — same soundness story as the flat flow.  This
    # applies to SAME-LEVEL and CROSS-LEVEL groups alike (a cross-level
    # fan-in roots at the shared sink with each deep driver as a leaf).
    hb.set_strategy(getattr(buda.Strategy, strat))
    overrides = getattr(session, "_bundling_overrides", None) or {}
    if overrides:
        hb.set_bundling_overrides(sorted(overrides.items()))
    if strat == "COMBINED":
        print("[HierBundler] COMBINED: join of CONVERGENT and BIDIRECTIONAL "
              "per bundling depth (same-level AND cross-level; restrict per "
              "prefix with set_bundling).")
    raw_bundles = hb.run(max_depth)
    # set_max_bundle_bits: split over-limit TEMPLATE bundles BEFORE expansion
    # so the split propagates identically to every instance (each part is its
    # own template, replicated at run_planner hier).
    raw_bundles = _split_hier_bundles(session, raw_bundles)
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
    # Options are `expanded` and `depth <N>` — reject anything else (a typo
    # used to silently run the default pre-expansion, unfiltered dump).
    filter_depth = None
    depth_val_idx = -1
    if "depth" in args:
        idx = list(args).index("depth")
        if idx + 1 >= len(args) or not args[idx + 1].lstrip("-").isdigit():
            print("Error: dump_hbundles depth requires an integer value"); return
        filter_depth = int(args[idx + 1])
        depth_val_idx = idx + 1
    opts = [a for i, a in enumerate(args)
            if a != "depth" and i != depth_val_idx]
    reject_unknown_options("dump_hbundles", opts, ("expanded", "depth"))
    use_expanded = "expanded" in args
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
