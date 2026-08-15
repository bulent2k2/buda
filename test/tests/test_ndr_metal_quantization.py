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

"""R1 metal-shaped NDR quantization (`opens_ndr.md` §2).

An absolute `def_ndr` width used to be quantized by the layer's per-signal-slot
CHANNEL cost, which answers "how much routing channel does this consume" — the
planner's question — and NOT "how much metal does the bit get", which is what a
width declared for EM, resistance or RC means.  The two share no term:
`bit_pitch` counts the power rails, metal does not.

These tests pin the metal reading and the two facts that make it more than a
formula change: a k-slot wire may not span a rail, and when the gaps vary the
NARROWEST window is the only guaranteed one.
"""
import pytest

import buda
from subprocess_env import buda_env


def _pattern(spec):
    """(type, width, space_after) triples -> TrackPattern."""
    return buda.TrackPattern(
        0.0, [buda.TrackSlot(t, "", w, sp) for t, w, sp in spec])


# The two patterns flow/ndr_abs_divisor.buda is built from.
DENSE = [("POWER", 2, 1)] + [("SIGNAL", 1, 1)] * 12 + [("GROUND", 2, 1)]
SPARSE = [("POWER", 10, 2)] + [("SIGNAL", 2, 2)] * 4 + [("GROUND", 10, 2)]


def test_metal_is_not_the_channel_cost():
    """The whole reason the reading had to change.

    Dense: period 30 over 12 signal slots -> bit_pitch 2.5, while a k-slot bit
    gets 2k-1 units of metal.  Sparse: period 40 over 4 -> bit_pitch 10.0,
    metal 4k-2.  Neither pair agrees, and on the sparse layer the channel cost
    is FIVE TIMES the metal one slot delivers."""
    dense, sparse = _pattern(DENSE), _pattern(SPARSE)
    assert dense.unit_pitch() / 12 == pytest.approx(2.5)
    assert sparse.unit_pitch() / 4 == pytest.approx(10.0)
    dg, sg = dense.ndr_geom(), sparse.ndr_geom()
    for k in range(1, 13):
        assert buda.ndr_metal_for_slots(dg, k) == pytest.approx(2 * k - 1)
    for k in range(1, 5):
        assert buda.ndr_metal_for_slots(sg, k) == pytest.approx(4 * k - 2)


def test_a_wire_may_not_span_a_rail():
    """A k-slot wire is k CONSECUTIVE signal slots.  A period whose signal
    slots are split by a rail offers only its longest run, and asking for more
    is unrealizable (R3) rather than a number to round."""
    # RAIL-TERMINATED so the period cannot join its own next copy — that
    # would be the cross-period splice, which is a different (and legal)
    # thing, covered below.
    split = _pattern([("SIGNAL", 1, 1)] * 2 + [("POWER", 2, 1)]
                     + [("SIGNAL", 1, 1)] * 3 + [("GROUND", 2, 1)])
    g = split.ndr_geom()
    assert [len(r) for r in g.runs] == [2, 3]
    assert buda.ndr_max_slots(g) == 3
    assert buda.ndr_metal_for_slots(g, 3) == pytest.approx(5)
    assert buda.ndr_metal_for_slots(g, 4) < 0, "a 4-slot wire would cross the rail"


def test_runs_join_across_the_period_boundary_even_with_an_interior_rail():
    """The tail run of one period abuts the head run of the next whenever no
    rail sits between — which depends on the RAIL, not on how many runs the
    period happens to have (Codex P2 on #717).

    `SIGNAL POWER SIGNAL` is two runs of one, yet a legal TWO-slot wire
    straddles the boundary; guarding on `runs.size() == 1` reported max 1 and
    would have falsely refused it."""
    g = _pattern([("SIGNAL", 1, 1), ("POWER", 2, 1),
                  ("SIGNAL", 1, 1)]).ndr_geom()
    assert buda.ndr_max_slots(g) == 2
    # …and the interior rail still blocks a wire that would cross IT.
    assert buda.ndr_metal_for_slots(g, 3) < 0


def test_runs_splice_across_periods_only_when_no_rail_separates_them():
    """A pattern TILES, so the last run of one period abuts the first run of
    the next — but they are one physical run only when no rail sits between.
    Splicing unconditionally is what would let a wire cross a rail."""
    allsig = _pattern([("SIGNAL", 1, 1)] * 4)
    assert buda.ndr_max_slots(allsig.ndr_geom()) > 4, "abutting periods are one run"
    railed = _pattern([("SIGNAL", 1, 1)] * 4 + [("POWER", 2, 1)])
    assert buda.ndr_max_slots(railed.ndr_geom()) == 4, "the rail ends the run"


def test_the_narrowest_window_wins_when_gaps_vary():
    """Measured on the repo's own patterns: 20 of 345 have non-uniform signal
    gaps (widths agree, trailing gaps differ).  The placer chooses the seat,
    not the rule, so a rule is only guaranteed met at the WORST window — a
    single `k*w + (k-1)*sp` formula would report the wrong number for those."""
    mixed = _pattern([("SIGNAL", 2, 1), ("SIGNAL", 2, 2),
                      ("SIGNAL", 2, 1), ("SIGNAL", 2, 2), ("POWER", 4, 1)])
    g = mixed.ndr_geom()
    # k=2 windows measure 5, 6, 5 — the narrowest is what a rule can rely on.
    assert buda.ndr_metal_for_slots(g, 2) == pytest.approx(5)


def _spec(width_abs=0.0, spacing_abs=0.0, metal=True):
    """Metal-shaped by default HERE because that is what this file is about.

    The engine default is the CHANNEL reading (`metal_quant=False`): the
    metal one is opt-in per rule via `def_ndr ... metal`, because it is
    stricter rather than merely different — a layer whose signal slots are
    isolated between rails can deliver only one slot's metal, so a width the
    channel reading accepted (by silently delivering half of it) becomes an
    R3 refusal.  See `test_the_channel_reading_is_still_the_default`."""
    s = buda.NdrSpec()
    s.width_abs, s.spacing_abs = width_abs, spacing_abs
    s.metal_quant = metal
    return s


def test_the_channel_reading_is_still_the_default():
    """R12: a design that does not ask for metal must be byte-identical.

    The same declaration reads differently under the two, which is the whole
    point — `width 8` on the sparse pattern costs 1 slot as channel (period
    40 over 4 signal slots -> pitch 10) and 3 as metal (4k-2 >= 8)."""
    sg = _pattern(SPARSE).ndr_geom()
    pitch = _pattern(SPARSE).unit_pitch() / 4
    channel = _spec(width_abs=8, metal=False)
    assert not channel.metal_quant
    got, ok = buda.ndr_resolve_on_layer(channel, 6, sg, pitch)
    assert ok and got.width_slots == 1, "the default reading must not move"
    got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=8), 6, sg, pitch)
    assert ok and got.width_slots == 3


def test_metal_is_stricter_where_a_layer_cannot_deliver():
    """Why metal is opt-in: it REFUSES designs the channel reading routes.

    A pattern whose signal slots are isolated between rails offers runs of
    one slot.  `width 4` there is channel-quantized to 1 slot — delivering 2
    units, half what was declared, silently — while metal reports it as
    unrealizable, which is R3's job."""
    iso = _pattern([("POWER", 2, 2), ("SIGNAL", 2, 2),
                    ("GROUND", 2, 2), ("SIGNAL", 2, 2)] * 3)
    g, pitch = iso.ndr_geom(), iso.unit_pitch() / 6
    assert buda.ndr_max_slots(g) == 1 and buda.ndr_metal_for_slots(g, 1) == 2
    got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=4, metal=False), 4, g, pitch)
    assert ok and got.width_slots == 1, "channel accepts it, delivering 2 of 4"
    _got, ok = buda.ndr_resolve_on_layer(_spec(width_abs=4), 4, g, pitch)
    assert not ok, "metal must refuse what the layer cannot deliver"


def test_the_four_divisor_rows_now_deliver_what_they_declared():
    """flow/ndr_abs_divisor.buda's table, re-measured.

    Channel-shaped gave 3 / 2 / 7 / 2 units for declared 3 / 3 / 8 / 8 — three
    of the four short, and the last one so short that the spec read INACTIVE
    and the bus routed ungoverned."""
    dg, sg = _pattern(DENSE).ndr_geom(), _pattern(SPARSE).ndr_geom()
    for declared, geom, layer in ((3, dg, 5), (3, sg, 6), (8, dg, 5), (8, sg, 6)):
        spec, ok = buda.ndr_resolve_on_layer(_spec(width_abs=declared), layer, geom)
        assert ok, f"width {declared} must be realizable here"
        got = buda.ndr_metal_for_slots(geom, spec.width_slots)
        assert got >= declared, f"declared {declared}, delivered {got}"
        # …and it must be the SMALLEST such k: over-delivering is waste.
        assert buda.ndr_metal_for_slots(geom, spec.width_slots - 1) < declared


def test_an_unrealizable_width_is_reported_not_clamped_silently():
    dg = _pattern(DENSE).ndr_geom()
    assert buda.ndr_metal_for_slots(dg, 12) == pytest.approx(23)
    spec, ok = buda.ndr_resolve_on_layer(_spec(width_abs=40), 5, dg)
    assert not ok, "40 units cannot be met by a 12-slot run delivering 23"
    # Still bounded, so a caller that ignores `ok` cannot loop or overflow.
    assert spec.width_slots == 12


def test_a_per_layer_value_overrides_the_rule_default():
    """The half of R1 that phase 1 dropped.  A physical rule's values differ
    per layer — EM limits, sheet resistance and RC all do — so one absolute
    width applied everywhere is over-wide on top or under-wide at the bottom."""
    dg, sg = _pattern(DENSE).ndr_geom(), _pattern(SPARSE).ndr_geom()
    s = _spec(width_abs=3)
    pl = buda.NdrLayerRule()
    pl.width_abs = 8.0
    s.set_layer_rule(6, pl)
    on5, _ = buda.ndr_resolve_on_layer(s, 5, dg)
    on6, _ = buda.ndr_resolve_on_layer(s, 6, sg)
    assert buda.ndr_metal_for_slots(dg, on5.width_slots) >= 3      # inherited
    assert buda.ndr_metal_for_slots(sg, on6.width_slots) >= 8      # overridden


def test_per_layer_cannot_be_mutated_through_the_read_only_view():
    """pybind11 converts std::map BY VALUE.  Exposing `per_layer` writable
    would accept `spec.per_layer[id] = rule`, mutate a copy, and leave the
    rule reading as declared while governing nothing — the silent-no-op class
    this feature keeps producing.  Caught by these tests before it shipped."""
    s = _spec(width_abs=3)
    pl = buda.NdrLayerRule()
    pl.width_abs = 8.0
    s.per_layer[6] = pl                      # a copy: must NOT take effect
    assert 6 not in s.per_layer
    s.set_layer_rule(6, pl)                  # the only way that works
    assert 6 in s.per_layer


def test_a_multiplier_override_wins_over_an_inherited_absolute():
    """The more specific declaration is the one the user made about THIS
    layer, and a multiplier is already quantized — nothing left to resolve."""
    dg = _pattern(DENSE).ndr_geom()
    s = _spec(width_abs=8)
    pl = buda.NdrLayerRule()
    pl.width_slots = 2
    s.set_layer_rule(5, pl)
    on5, ok = buda.ndr_resolve_on_layer(s, 5, dg)
    assert ok and on5.width_slots == 2


def test_multiplier_and_ungoverned_specs_are_untouched():
    """The R12 guarantee: a design declaring no absolute value must be
    byte-identical, so resolution has to be the identity for it."""
    dg = _pattern(DENSE).ndr_geom()
    mult = buda.NdrSpec()
    mult.width_slots = 2
    got, ok = buda.ndr_resolve_on_layer(mult, 5, dg)
    assert ok and got.width_slots == 2 and got.guard_slots == 0
    plain = buda.NdrSpec()
    got, ok = buda.ndr_resolve_on_layer(plain, 5, dg)
    assert ok and not got.active()


def _flow(tmp_path, body):
    """Run a .buda body through the CLI, returning (stdout+stderr, rc)."""
    import subprocess, sys, os
    f = tmp_path / "t.buda"
    f.write_text(body)
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    env = buda_env(root, "build", "src", "tools")
    r = subprocess.run([sys.executable, "src/buda_cli.py", str(f), "--no-viz"],
                       capture_output=True, text=True, cwd=root, env=env)
    return r.stdout + r.stderr, r.returncode


_STACK = """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus w_[2] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
"""
_DENSE = "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
# Every signal slot isolated between rails: runs of ONE, 2 units of metal.
_ISO = "def_track_pattern 4 0 (VDD 2 2 _ 2 2 GND 2 2 _ 2 2)x3\n"


def test_an_unrealizable_metal_rule_is_refused_not_quantized_to_the_clamp(tmp_path):
    """R3 at the declaration.  `ndr_resolve_on_layer` CLAMPS an unrealizable
    value to the layer's longest run, so caching that count would let the
    later check see a number that fits by construction and route the rule
    below its declared width — silently (Codex P1 on #717)."""
    out, rc = _flow(tmp_path, _STACK + _DENSE
                    + "def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
                    + "def_ndr big width 40 metal\nset_ndr w_ big\n"
                      "run_bundler STRICT\n")
    assert rc != 0, out
    assert "cannot realize the declared value" in out, out
    assert "NDR rule 'big'" in out, "the refusal must name the rule"


def test_a_per_layer_override_is_validated_in_the_parent_rules_reading(tmp_path):
    """The `def_ndr_layer` probe left `metal_quant` at its default and passed
    no pitch, so it took the channel branch, returned early and reported OK
    unconditionally — an advertised R3 check that tested nothing."""
    out, _rc = _flow(tmp_path, _STACK + _DENSE + _ISO
                     + "def_ndr em width 2 metal\n"
                       "def_ndr_layer em M4 width 9\n")
    assert "def_ndr_layer 'em'" in out and "cannot realize" in out, out


def test_the_same_override_is_accepted_when_the_layer_can_deliver(tmp_path):
    """Non-vacuity: the refusal above must be about the ARITHMETIC, not about
    per-layer overrides being rejected generally."""
    out, rc = _flow(tmp_path, _STACK + _DENSE
                    + "def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1\n"
                    + "def_ndr em width 2 metal\n"
                      "def_ndr_layer em M4 width 9\n")
    assert rc == 0, out
    assert "cannot realize" not in out, out


# ── v28: per-layer values and the metal flag survive a BDB round trip ──────
# Persisted for the same reason `width_abs` was (v26): without them a reopened
# design restores the rule missing part of its declaration and routes
# DIFFERENTLY from the design that was saved — and the session that could have
# warned is not the session that suffers.

_RT_SETUP = """open_bdb {db}
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
"""


def _session(tmp_path, body):
    import sys as _s
    _s.path[:0] = ["build", "src", "tools"]
    import buda_cli
    sess = buda_cli.BudaSession()
    for raw in body.splitlines():
        line = raw.split("#")[0].strip()
        if line:
            sess.do_command(line)
    return sess


def test_per_layer_and_metal_survive_a_bdb_round_trip(tmp_path):
    from buda_cmds import ndr_cmds
    db = str(tmp_path / "rt.bdb")
    _session(tmp_path, _RT_SETUP.format(db=db) + """def_ndr em width 3 metal
def_ndr_layer em M4 width 5
def_ndr_layer em M3 spacing x2
save_bdb
""")
    # A FRESH session that only re-declares the technology.
    s2 = _session(tmp_path, _RT_SETUP.format(db=db))
    r = ndr_cmds._rules(s2)["em"]
    assert r["width_abs"] == 3.0
    assert r["metal"] == 1, "a metal rule must not come back as a channel rule"
    pl = r["per_layer"]
    assert set(pl) == {3, 4}, pl
    assert pl[4]["width_abs"] == 5.0
    # The TRI-STATE must survive: M4 declared a width and NOT a spacing, M3
    # the reverse.  Collapsing "not declared here" into a 0 would restore a
    # rule that governs differently than the one written.
    assert pl[4]["spacing_abs"] == 0.0 and pl[4]["spacing_x"] == 0.0
    assert pl[3]["spacing_x"] == 2.0 and pl[3]["width_abs"] == 0.0


def test_a_restored_rule_resolves_the_same_as_the_one_that_was_saved(tmp_path):
    """The property that matters — not that the fields round-trip, but that
    the ROUTING does.  Half a declaration routes narrower than what was
    saved, which is the fault this column exists to close."""
    from buda_cmds import ndr_cmds
    db = str(tmp_path / "rt2.bdb")
    body = """def_ndr em width 3 metal
def_ndr_layer em M4 width 9
"""
    s1 = _session(tmp_path, _RT_SETUP.format(db=db) + body + "save_bdb\n")
    s2 = _session(tmp_path, _RT_SETUP.format(db=db))
    a = ndr_cmds._spec_of(s1, "em")
    b = ndr_cmds._spec_of(s2, "em")
    for lid in (3, 4):
        geom = s1.layers.ndr_geom(lid)
        pitch = s1.layers.bit_pitch(lid)
        ra, _ = buda.ndr_resolve_on_layer(a, lid, geom, pitch)
        rb, _ = buda.ndr_resolve_on_layer(b, lid, geom, pitch)
        assert (ra.width_slots, ra.guard_slots) == (rb.width_slots, rb.guard_slots), \
            f"layer {lid}: saved {ra.width_slots} slots, restored {rb.width_slots}"
    # …and the M4 override is really doing something, or the check is vacuous.
    on4, _ = buda.ndr_resolve_on_layer(b, 4, s1.layers.ndr_geom(4),
                                       s1.layers.bit_pitch(4))
    on3, _ = buda.ndr_resolve_on_layer(b, 3, s1.layers.ndr_geom(3),
                                       s1.layers.bit_pitch(3))
    assert on4.width_slots > on3.width_slots


def test_changing_a_per_layer_value_voids_a_restored_plan(tmp_path):
    """The pricing fingerprint must carry the per-layer values and the metal
    flag: both move the quantized demand, so a plan priced under the old
    declaration is not valid under the new one.  Omitting them would let a
    resumed session keep a plan priced for a different width — silently."""
    from buda_cmds import ndr_cmds
    s = _session(tmp_path, _RT_SETUP.format(db=str(tmp_path / "fp.bdb"))
                 + "def_ndr em width 3 metal\ndef_ndr_layer em M4 width 5\n")
    before = ndr_cmds.ndr_pricing_fp(s, "em")
    # Same rule, one per-layer value changed.
    s2 = _session(tmp_path, _RT_SETUP.format(db=str(tmp_path / "fp2.bdb"))
                  + "def_ndr em width 3 metal\ndef_ndr_layer em M4 width 9\n")
    assert ndr_cmds.ndr_pricing_fp(s2, "em") != before
    # And the READING alone is enough to change it, at identical values.
    s3 = _session(tmp_path, _RT_SETUP.format(db=str(tmp_path / "fp3.bdb"))
                  + "def_ndr em width 3\ndef_ndr_layer em M4 width 5\n")
    assert ndr_cmds.ndr_pricing_fp(s3, "em") != before


def test_a_layer_independent_rule_keeps_its_pre_v28_fingerprint(tmp_path):
    """The other half: appended ONLY when present, so a resumed checkpoint of
    a rule that uses neither feature must not VOID on a format change."""
    from buda_cmds import ndr_cmds
    s = _session(tmp_path, _RT_SETUP.format(db=str(tmp_path / "fp4.bdb"))
                 + "def_ndr plain width x2 spacing x2\n")
    fp = ndr_cmds.ndr_pricing_fp(s, "plain")
    assert "|P" not in fp and "|m1" not in fp, fp


@pytest.mark.parametrize("stored", ["[]", '{"4": null}', '"hello"', "5",
                                    "not json", "{", '{"4": [1,2]}'])
def test_a_malformed_stored_per_layer_value_warns_it_never_aborts(stored):
    """A stored value we cannot read is a reason to SAY SO, never a reason to
    fail to open the design.

    "Readable JSON" is not enough (Codex P2 on #719): `[]`, `"x"`, `5` and
    `{"4": null}` all parse, then raise AttributeError on `.items()` /
    `.get()` — so a database carrying one would abort the session that opened
    it rather than restore the rule layer-independent."""
    from buda_cmds import ndr_cmds
    assert ndr_cmds._per_layer_from_json(stored, "em") == {}


def test_a_partially_bad_map_keeps_the_good_entries_and_says_what_it_dropped(capsys):
    """Silently dropping entries is the same fault one level down."""
    from buda_cmds import ndr_cmds
    got = ndr_cmds._per_layer_from_json('{"4":{"w":8},"5":3}', "em")
    assert set(got) == {4} and got[4]["width_abs"] == 8.0
    out = capsys.readouterr().out
    assert "BUDA-1912" in out and "1 unreadable entry" in out, out


# ── R9: the width audit must be able to FAIL ────────────────────────────────

def test_the_declared_width_accessor_picks_the_layer_in_force():
    """What the metal audit compares against: the width the USER declared on
    that layer, not the slot count the tool derived from it."""
    s = _spec(width_abs=3)
    pl = buda.NdrLayerRule()
    pl.width_abs = 8.0
    s.set_layer_rule(6, pl)
    assert buda.ndr_declared_width_on(s, 5) == 3.0      # inherited
    assert buda.ndr_declared_width_on(s, 6) == 8.0      # overridden
    # A MULTIPLIER override replaces the width on that layer, so an inherited
    # absolute no longer describes it and there is nothing physical to check.
    mx = buda.NdrLayerRule()
    mx.width_slots = 2
    s.set_layer_rule(4, mx)
    assert buda.ndr_declared_width_on(s, 4) == 0.0
    # A pure multiplier rule has no physical width at all.
    assert buda.ndr_declared_width_on(buda.NdrSpec(), 5) == 0.0


def test_the_metal_width_audit_fails_on_an_under_width_bit(tmp_path):
    """The property the old check could not have: a FAILING case.

    The channel reading compared covered slot centres against `width_slots`
    — both derived from the same quantization, so they agree by construction
    and the audit could only catch a placement that ignored the spec
    outright.  Measuring placed METAL against the DECLARATION compares two
    independent quantities, so a real shortfall is visible.

    Driven through the audit directly: the pipeline quantizes correctly, so
    an under-width bit is a pipeline BUG, which is precisely what an audit
    exists to catch rather than something a flow can be asked to produce."""
    from buda_cmds import ndr_cmds
    sess = _session(tmp_path, """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus em_[2] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_ndr em width 3 metal
set_ndr em_ em
run_bundler STRICT
generate_topologies
set_track_pitch 3
run_planner 1
run_nuts
run_detailed_nuts
""")
    w = next(x for x in sess.bundles if x.input.ndr.active())
    assert not ndr_cmds.audit_ndr_dnuts(sess, w), "the honest route must be clean"

    # Now narrow one placed bit below the declared 3 — a placement that does
    # not deliver what the rule asked for.
    for ns in sess.detailed_result.net_segments:
        if not ns.is_shield:
            ns.width = 1.0
            break
    msgs = [v.message for v in ndr_cmds.audit_ndr_dnuts(sess, w)]
    hits = [m for m in msgs if m.startswith("NDR_WIDTH")]
    assert hits, msgs
    # The message must carry BOTH numbers — an audit that says "too narrow"
    # without saying narrower than what cannot be acted on.
    assert "1 unit(s) of metal" in hits[0] and "declares 3" in hits[0], hits[0]


# ── The reading follows the WIDTH IN FORCE, not the rule's flag ────────────

_AUDIT_STACK = """add_block a 0 0 200 200
add_block b 900 0 1100 200
add_bus em_[2] a.p b.q
def_layer 3 M3 H TOP 20
def_layer 4 M4 V 20
def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1
def_track_pattern 4 0 VDD 2 1 (_ 1 1)x12 GND 2 1
"""
_AUDIT_RUN = """set_ndr em_ em
run_bundler STRICT
generate_topologies
set_track_pitch 3
run_planner 1
run_nuts
run_detailed_nuts
"""


def _audit_catches_a_narrowed_bit(tmp_path, decl):
    from buda_cmds import ndr_cmds
    sess = _session(tmp_path, _AUDIT_STACK + decl + _AUDIT_RUN)
    w = next(x for x in sess.bundles if x.input.ndr.active())
    assert not ndr_cmds.audit_ndr_dnuts(sess, w), "the honest route must be clean"
    for ns in sess.detailed_result.net_segments:
        if not ns.is_shield:
            ns.width = 1.0
            break
    return [v.message for v in ndr_cmds.audit_ndr_dnuts(sess, w)
            if v.message.startswith("NDR_WIDTH")]


@pytest.mark.parametrize("decl", [
    "def_ndr em width 3 metal\n",                                  # metal, absolute
    "def_ndr em width x2 spacing 3 metal\n",                       # metal rule, MULTIPLIER width
    "def_ndr em width 3 metal\ndef_ndr_layer em M3 width x2\n",    # per-layer multiplier override
    "def_ndr em width x2\n",                                       # plain channel rule
])
def test_the_width_audit_fires_whichever_form_is_in_force(tmp_path, decl):
    """The reading is a property of the WIDTH IN FORCE on the bit's layer, not
    of the rule's `metal_quant` flag.

    A metal rule can carry a MULTIPLIER width — `width x2 spacing 3 metal`, or
    an absolute rule with a per-layer `width x2` override — and a multiplier
    names no physical width, so the metal check has nothing to compare.
    Gating the slot-centre check on `not metal_quant` switched BOTH off for
    those and audited the width not at all (Codex P2 on #721)."""
    assert _audit_catches_a_narrowed_bit(tmp_path, decl)


def test_a_mixed_form_rule_keeps_both_declarations(tmp_path):
    """width and spacing are declared INDEPENDENTLY, so a rule may mix the
    forms and `width x2 spacing 3` means both things.

    Resolving BOTH fields from the absolute quantization whenever EITHER was
    absolute silently dropped the multiplier one: this rule resolved to
    width_slots 1, so a governed bit routed at DEFAULT width while the rule
    said x2 — and the width audit could not see it, because it compared
    against the same 1.  A routing fault, not just an audit gap."""
    from buda_cmds import ndr_cmds
    sess = _session(tmp_path, _AUDIT_STACK
                    + "def_ndr mixed width x2 spacing 3 metal\n"
                      "def_ndr rev   width 3 spacing x2 metal\n")
    mixed = ndr_cmds._spec_of(sess, "mixed")
    assert mixed.width_slots == 2, "the multiplier WIDTH must survive"
    assert mixed.guard_slots >= 1, "the absolute SPACING must survive"
    # …and the mirror, so the fix is not one-directional.
    rev = ndr_cmds._spec_of(sess, "rev")
    assert rev.width_slots == 2 and rev.guard_slots == 1


# ── an all-signal period has no ceiling ──────────────────────────────────

def test_a_period_with_no_rail_hosts_a_run_of_any_length():
    """Rails are what BOUND a contiguous run, so a period with none does not
    end: every tiled copy adds more signal slots to the same stretch.

    Modelling one period plus a single splice across the boundary is exact
    while rails exist and under-reports without limit when they do not — a
    one-slot period reported a longest run of 2, so a metal rule needing 3
    slots was refused as unrealizable on a layer that can host any width.

    Not a corner case: a technology LEF says nothing about which tracks the
    power grid takes (that lives in the DEF's SPECIALNETS), so EVERY layer
    imported from one is all-signal — `flow/ariane133`'s ten included, which
    is how this was found."""
    # flow/ariane133's metal1 once the tech LEF supplies its width:
    # pitch 280 DBU, wire 140, so metal(k) = 140k + 140(k-1) = 280k - 140.
    g = _pattern([("SIGNAL", 140, 140)]).ndr_geom()
    assert g.unbounded
    for k in range(1, 9):
        assert buda.ndr_metal_for_slots(g, k) == pytest.approx(280 * k - 140)
    assert buda.ndr_max_slots(g) > 1000          # i.e. not a real ceiling


def test_a_railed_period_is_still_BOUNDED():
    """The twin that keeps the fix honest: where rails exist they really do
    end the run, and nothing about that changed."""
    g = _pattern(DENSE).ndr_geom()
    assert not g.unbounded
    assert buda.ndr_max_slots(g) == 12
    assert buda.ndr_metal_for_slots(g, 13) == -1.0


def test_the_single_boundary_splice_is_preserved():
    """`SIGNAL POWER SIGNAL` has two runs of one, and a legal TWO-slot wire
    straddles the tiled boundary (the #717 case).  It is still bounded at 2 —
    the unbounded rule keys on the RAIL, not on the run count."""
    g = _pattern([("SIGNAL", 1, 1), ("POWER", 2, 1),
                  ("SIGNAL", 1, 1)]).ndr_geom()
    assert not g.unbounded
    assert buda.ndr_max_slots(g) == 2
