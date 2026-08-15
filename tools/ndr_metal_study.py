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

"""Should METAL become the default reading for an absolute NDR value?

`opens_ndr.md` §2 landed the metal-shaped quantization as opt-in (`def_ndr …
metal`) and owed the flip a measurement, on the path `kSegsRel`,
`spine_relays` and `band_span_charge` took.  This is that measurement.

THE TWO READINGS.  `def_ndr r width W` says a governed bit should be W units
wide, and path A realizes it as a whole number of SIGNAL slots:

    channel:  k = ceil(W / bit_pitch),  bit_pitch = unit_pitch / n_signal
    metal:    smallest k with (sum of k slot widths + the k-1 gaps) >= W

They share no term.  `bit_pitch` amortizes the power rails across the signal
slots — the right question for the planner's books, and what `eff_bus_width`
charges.  Metal counts neither the rails nor the gaps outside the run — the
right question for a width declared for EM, resistance or RC.

WHY THE FLIP IS NOT FREE.  Metal asks for >= as many slots as channel
wherever the two differ, so it costs demand; and where no run in the period
is long enough it is an R3 REFUSAL, which turns a design that routes today
into a hard error.  That is correct behaviour — R3 says the tool never
silently degrades an NDR — and it is exactly why the default cannot move on
an argument alone.

TWO HALVES, because one of them cannot answer the question:

  PATTERNS (--patterns) sweeps every `def_track_pattern` declared in the tree
  against a range of declared widths and reports how often, and by how much,
  the two readings disagree.  This is the population-level answer, measured
  on real declared technology rather than on demo designs.

  FLOWS (--flows) runs every vehicle that declares an absolute value under
  both readings and reports what MOVES: R3 refusals, governed demand,
  wirelength, the QoR triple, and how many rules stop being silent no-ops.

Run `--patterns` first: if the readings rarely differ, the flow half is
measuring noise; if they differ often, the flow half says what that costs.
"""
import argparse
import collections
import math
import os
import re
import subprocess
import sys

sys.path[:0] = ["build", "src", "tools"]

import buda                                              # noqa: E402
from slot_groups import expand_slot_groups               # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The declared widths swept per pattern, as multiples of that pattern's own
# MINIMUM SIGNAL SLOT — the technology's minimum wire.
#
# The anchor is the whole measurement, so it is worth saying why.  A rule
# written for EM, resistance or RC says "this net needs N times minimum
# width"; nobody declares a width as a fraction of the TRACK PITCH, which is
# a routing-resource quantity that happens to include the power rails.
# Anchoring the sweep to pitch instead makes the two readings disagree almost
# everywhere by construction — metal counts no rails and pitch does — which
# measures the existence of power rails rather than the choice under study.
_WIDTH_MULTIPLES = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)


def _iter_pattern_decls(root):
    """Every `def_track_pattern` / `add_grid_override` slot list in the tree.

    Yields (path, layer_id, tokens).  Both commands share the slot-list
    grammar and the same quantization question, so both count."""
    pat = re.compile(r"^\s*(def_track_pattern|add_grid_override)\s+(.*)$")
    for sub in ("flow", "demo", "test"):
        base = os.path.join(root, sub)
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".buda"):
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, errors="replace") as fh:
                    for line in fh:
                        line = line.split("#")[0]
                        m = pat.match(line)
                        if not m:
                            continue
                        cmd, rest = m.group(1), m.group(2).split()
                        # def_track_pattern <layer> <origin> <slots…>
                        # add_grid_override <layer> x1 y1 x2 y2 <origin> <slots…>
                        skip = 2 if cmd == "def_track_pattern" else 6
                        if len(rest) <= skip:
                            continue
                        try:
                            lid = int(rest[0])
                        except ValueError:
                            continue
                        yield (os.path.relpath(path, root), lid, rest[skip:])


def _pattern_of(tokens):
    """A `buda.TrackPattern` from a declared slot list, or None if the line is
    not one this study can read (a malformed or exotic declaration is skipped
    LOUDLY in the count, never silently treated as empty)."""
    try:
        toks = expand_slot_groups("def_track_pattern", list(tokens))
    except SystemExit:
        return None
    except Exception:
        return None
    if len(toks) % 3:
        return None
    alias = {"_": "SIGNAL", "GND": "GROUND", "CLK": "CLOCK",
             "VDD": "POWER", "VSS": "GROUND"}
    slots = []
    for i in range(0, len(toks), 3):
        t = toks[i].upper()
        t = alias.get(t, t)
        if t not in ("POWER", "GROUND", "CLOCK", "SHIELD", "SIGNAL", "CUSTOM"):
            return None
        try:
            w, sp = float(toks[i + 1]), float(toks[i + 2])
        except ValueError:
            return None
        if w <= 0 or sp < 0:
            return None
        slots.append(buda.TrackSlot(t, "", w, sp))
    if not slots:
        return None
    return buda.TrackPattern(0.0, slots)


def study_patterns(verbose=False):
    """How often, and by how much, do the two readings disagree?"""
    seen, skipped = {}, 0
    for path, lid, toks in _iter_pattern_decls(_ROOT):
        pat = _pattern_of(toks)
        if pat is None:
            skipped += 1
            continue
        # Dedup by CONTENT: the corpus declares the same stack in many flows,
        # and counting one technology once per file would weight a popular
        # fixture as if it were many technologies.
        key = tuple((s.type, s.width, s.space_after) for s in pat.slots)
        seen.setdefault(key, (pat, path, lid))

    rows, n_sig_zero = [], 0
    for key, (pat, path, lid) in seen.items():
        sig = [s for s in pat.slots if s.type == "SIGNAL"]
        if not sig:
            n_sig_zero += 1
            continue
        geom = pat.ndr_geom()
        pitch = pat.unit_pitch() / len(sig)
        w_min = min(s.width for s in sig)
        ceiling = buda.ndr_max_slots(geom)
        rail = 1.0 - sum(s.width for s in sig) / pat.unit_pitch()
        for mult in _WIDTH_MULTIPLES:
            W = mult * w_min
            k_ch = max(1, math.ceil(W / pitch - 1e-9))
            k_me, realizable = None, True
            for k in range(1, ceiling + 1):
                if buda.ndr_metal_for_slots(geom, k) >= W - 1e-9:
                    k_me = k
                    break
            if k_me is None:
                k_me, realizable = ceiling, False
            # -1 means the period cannot host a run that long, so the CHANNEL
            # reading has charged slots that cannot form a legal wire — a
            # different fault from delivering too little, and reported as
            # such rather than folded into the shortfall as a huge number.
            got = buda.ndr_metal_for_slots(geom, k_ch)
            rows.append(dict(path=path, layer=lid, pitch=pitch, mult=mult,
                             W=W, k_ch=k_ch, k_me=k_me, rail=rail,
                             realizable=realizable, ceiling=ceiling,
                             ch_unhostable=(got < 0.0),
                             metal_at_k_ch=(None if got < 0.0 else got)))

    print(f"=== PATTERNS: {len(seen)} distinct track pattern(s) "
          f"({skipped} declaration(s) unreadable, {n_sig_zero} with no signal "
          f"slot), {len(_WIDTH_MULTIPLES)} declared widths each "
          f"= {len(rows)} (pattern, width) pairs\n")

    agree = [r for r in rows if r["k_ch"] == r["k_me"] and r["realizable"]]
    costs = [r for r in rows if r["k_me"] > r["k_ch"] and r["realizable"]]
    refuse = [r for r in rows if not r["realizable"]]
    cheaper = [r for r in rows if r["k_me"] < r["k_ch"] and r["realizable"]]

    n = len(rows) or 1
    print(f"  the readings AGREE            {len(agree):5d}  "
          f"({100.0 * len(agree) / n:.1f}%)")
    print(f"  metal costs MORE slots        {len(costs):5d}  "
          f"({100.0 * len(costs) / n:.1f}%)")
    print(f"  metal is UNREALIZABLE (R3)    {len(refuse):5d}  "
          f"({100.0 * len(refuse) / n:.1f}%)  <- a design that routes today")
    if cheaper:
        print(f"  metal costs FEWER slots       {len(cheaper):5d}  "
              f"(unexpected — metal should never ask for less)")

    if costs:
        extra = collections.Counter(r["k_me"] - r["k_ch"] for r in costs)
        print("\n  extra slots per bit where metal costs more:")
        for d in sorted(extra):
            print(f"    +{d} slot(s): {extra[d]} pair(s)")

    # What the CHANNEL reading actually delivers when it is the one in force:
    # the shortfall is the quantity the flip exists to remove.
    short = [r for r in rows if r["metal_at_k_ch"] is not None
             and r["metal_at_k_ch"] < r["W"] - 1e-9]
    unhostable = [r for r in rows if r["ch_unhostable"]]
    if short:
        worst = max(short, key=lambda r: r["W"] / r["metal_at_k_ch"])
        print(f"\n  under CHANNEL, {len(short)} of {len(rows)} pairs deliver "
              f"LESS metal than declared ({100.0 * len(short) / n:.1f}%)")
        print(f"    worst: declared {worst['W']:g} on {worst['path']} "
              f"L{worst['layer']} -> {worst['k_ch']} slot(s) delivering "
              f"{worst['metal_at_k_ch']:g} "
              f"({worst['W'] / worst['metal_at_k_ch']:.2f}x under)")
    if unhostable:
        print(f"  under CHANNEL, {len(unhostable)} pair(s) charge a run the "
              f"period cannot host at all (R3 catches these already)")

    # THE CRUX.  "Metal refuses 9% of declarations that route today" is only
    # damning if those declarations were being HONOURED today.  They are not:
    # the channel reading accepts them and delivers whatever its slot count
    # happens to buy.  So the question is not refuse-vs-work, it is
    # refuse-vs-silently-deliver-less — and this measures by how much.
    if refuse:
        ratios = [r["W"] / r["metal_at_k_ch"] for r in refuse
                  if r["metal_at_k_ch"]]
        if ratios:
            print(f"\n  of the {len(refuse)} pairs metal REFUSES, channel "
                  f"accepts every one and delivers")
            print(f"    {min(ratios):.2f}x - {max(ratios):.2f}x under the "
                  f"declared width (median {sorted(ratios)[len(ratios)//2]:.2f}x)")
            print(f"    — so the flip does not break working designs there, "
                  f"it refuses silently-wrong ones")

    if verbose:
        print("\n  per-width breakdown (multiples of the pattern's MINIMUM "
              "signal slot):")
        for mult in _WIDTH_MULTIPLES:
            sel = [r for r in rows if r["mult"] == mult]
            a = sum(1 for r in sel if r["k_ch"] == r["k_me"] and r["realizable"])
            c = sum(1 for r in sel if r["k_me"] > r["k_ch"] and r["realizable"])
            u = sum(1 for r in sel if not r["realizable"])
            print(f"    {mult:>4}x min wire: agree {a:4d}  more {c:4d}  "
                  f"refuse {u:4d}")
    return rows


# Every vehicle that declares an ABSOLUTE width or spacing — the only designs
# the flip can move, since a multiplier is pattern-independent.
_ABS_FLOWS = (
    "flow/ndr_abs_divisor.buda",
    "flow/ndr_abs_um.buda",
    "flow/ndr_abs_shared_bottomup.buda",
    "flow/ndr_noop_rule.buda",
    "flow/ndr_per_layer_em.buda",
)

_RE_DEMAND = re.compile(r"rule '([^']+)': demand (\d+) slot")
_RE_ABSWL = re.compile(r"[Aa]bstract.*?wirelength[^0-9]*([0-9]+)")
_RE_DETWL = re.compile(r"[Dd]etailed.*?wirelength[^0-9]*([0-9]+)")


def _run_flow(path, metal):
    """One flow under one reading: the TEXT (verdicts, demand, refusals) from
    a subprocess, and the QoR triple from `qor_corpus.run_flow` — the same
    numbers the corpus gate compares, so the cost of the flip is quoted in
    the currency every other default flip here was judged in.

    A subprocess for the text because an R3 refusal is `sys.exit(1)`, which
    would take the study process with it."""
    env = dict(os.environ)
    env["BUDA_NDR_METAL"] = "1" if metal else "0"
    p = subprocess.run([os.path.join(_ROOT, "bin", "buda"), "--no-viz", path],
                       cwd=_ROOT, env=env, capture_output=True, text=True,
                       timeout=900)
    out = p.stdout + p.stderr
    log = os.path.join(_ROOT, "flow", "log",
                       os.path.basename(path)[:-5] + "_flow.log")
    if os.path.exists(log):
        with open(log, errors="replace") as fh:
            out += fh.read()
    demand = {}
    for rule, slots in _RE_DEMAND.findall(out):
        demand[rule] = int(slots)
    r3 = ("cannot realize the declared value" in out
          or "not realizable there (R3)" in out)
    qor = {}
    if not r3 and p.returncode == 0:
        import qor_corpus
        prev = os.environ.get("BUDA_NDR_METAL")
        os.environ["BUDA_NDR_METAL"] = "1" if metal else "0"
        # At the FILE DESCRIPTOR, not just `sys.stdout`: the engine prints
        # from C++, which writes to fd 1 directly and walks straight past
        # `redirect_stdout` — that is why the planner's capacity tables were
        # landing in the middle of this report.
        saved = os.dup(1)
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            sys.stdout.flush()
            os.dup2(devnull, 1)
            qor = qor_corpus.run_flow(os.path.relpath(path, _ROOT))
        except Exception as e:                   # never let QoR kill the study
            qor = {"err": str(e)}
        finally:
            sys.stdout.flush()
            os.dup2(saved, 1)
            os.close(saved)
            os.close(devnull)
            if prev is None:
                os.environ.pop("BUDA_NDR_METAL", None)
            else:
                os.environ["BUDA_NDR_METAL"] = prev
    return dict(rc=p.returncode, out=out, demand=demand, qor=qor,
                noop=out.count("BUDA-1913"),
                partial=out.count("BUDA-1914"),
                r3=r3,
                short=out.count("but the placed bits are"))


def study_flows():
    print("\n=== FLOWS: every vehicle declaring an absolute value, "
          "both readings\n")
    for rel in _ABS_FLOWS:
        path = os.path.join(_ROOT, rel)
        if not os.path.exists(path):
            print(f"  {rel}: MISSING")
            continue
        ch = _run_flow(path, metal=False)
        me = _run_flow(path, metal=True)
        name = os.path.basename(rel)
        print(f"  {name}")
        print(f"    exit          {ch['rc']} -> {me['rc']}"
              + ("   <- R3 REFUSAL under metal" if me["r3"] and not ch["r3"]
                 else ""))
        keys = sorted(set(ch["demand"]) | set(me["demand"]))
        for k in keys:
            a, b = ch["demand"].get(k), me["demand"].get(k)
            if a == b:
                continue
            fa = "inactive" if a is None else f"{a} slot(s)"
            fb = "inactive" if b is None else f"{b} slot(s)"
            print(f"    demand '{k}'  {fa} -> {fb}")
        if (ch["noop"], ch["partial"]) != (me["noop"], me["partial"]):
            print(f"    no-op verdicts  {ch['noop']}W/{ch['partial']}I -> "
                  f"{me['noop']}W/{me['partial']}I")
        if ch["short"] != me["short"]:
            print(f"    metal-shortfall lines  {ch['short']} -> {me['short']}")
        # The COST, in the corpus gate's own currency.  Demand rising is not
        # itself a regression — it is the flip doing its job — so what has to
        # be watched is whether the extra slots strand bits.
        a, b = ch["qor"], me["qor"]
        if a and b and "err" not in a and "err" not in b:
            trip = [(k, a.get(k), b.get(k))
                    for k in ("overlaps", "unplaced", "viol_bundles")]
            moved = [t for t in trip if t[1] != t[2]]
            base = "  ".join(f"{k}={a.get(k)}" for k, _, _ in trip)
            if moved:
                print("    QoR  " + base + "  ->  "
                      + "  ".join(f"{k}={y}" for k, _, y in moved))
            else:
                print(f"    QoR  {base}  (unchanged)")
            stranded = (b.get("unplaced") or 0) > (a.get("unplaced") or 0)
            for k in ("abstract_wl", "detailed_wl"):
                x, y = a.get(k), b.get(k)
                if x and y and x != y:
                    # A WL DROP next to a rise in unplaced is not a win: a
                    # stranded bit lays no wire, so the metric improves by
                    # losing the very nets it was measuring.  Say so, or the
                    # table reads like a free speed-up.
                    trap = ("  <- bits went MISSING, not shorter"
                            if y < x and stranded and k == "detailed_wl"
                            else "")
                    print(f"    {k}  {x} -> {y}  "
                          f"({100.0 * (y - x) / x:+.1f}%){trap}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", action="store_true",
                    help="sweep the declared track patterns (default)")
    ap.add_argument("--flows", action="store_true",
                    help="run the absolute-rule vehicles under both readings")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if not a.patterns and not a.flows:
        a.patterns = a.flows = True
    if a.patterns:
        study_patterns(a.verbose)
    if a.flows:
        study_flows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
