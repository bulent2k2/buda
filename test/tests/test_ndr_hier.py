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

"""NDR × hierarchical flows (the real hbundles vehicles, 06-10).

R2d landed: run_hier_bundler PROPAGATES declared rules — the rule-class
split runs on TEMPLATE bundles before per-instance expansion (replicas in
lockstep), specs ride expansion to every occurrence, and a class governed
inconsistently across occurrences hard-errors.  These tests clone the
canonical hier vehicles (flow/hbundles/06..10) and pin the contract:

1. a governed hier flow runs END-TO-END on every real vehicle — the rule
   resolves, the bundler reports governance, and the pipeline completes
   (the old phase-1 refusal tests, flipped to must-route);
2. a cell-level template class governed by a class-wide scope emits
   shields at EVERY instance (the propagation the refusal used to block);
3. a scope that governs a class inconsistently (naming one occurrence's
   prefix) refuses LOUDLY — a template is solved once and copied, so
   per-occurrence rules cannot ride it;
4. per-occurrence CONGRUENT scopes split template and replicas in
   lockstep (the |NDR: suffix parts, parent linkage rewired);
5. v21/v22 rules/scopes persist into and restore from a HIER design BDB —
   the tables ride beside the full bundle/topology/routing population,
   and an ungoverned hier flow leaves them empty (no silent rule
   creation);
6. the flows themselves still run to completion under the current schema.

Mid tier, like the other full-flow integration tests (test_flow_scripts).
"""
import contextlib
import io
import pathlib

import pytest

import buda
import buda_cli
from buda_cmds import ndr_cmds

pytestmark = pytest.mark.mid

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FLOW = _ROOT / "flow" / "hbundles"

_HIER_FLOWS = [
    "06_multipin_stress.buda",
    "07_wide_fan_stress.buda",
    "08_cross_level.buda",
    "09_local_global_compete.buda",
    "10_chip_units_blocks_leaf.buda",
]


def _flow_lines(name, bdb_path=None):
    """The flow's command lines, cleaned for session-level replay: comments
    dropped, `source ../tracks/...` made absolute (replay has no script CWD),
    `visualize` dropped (headless), and — when bdb_path is given — the
    `:memory:` BDB redirected to a real file so persistence is observable
    across sessions."""
    lines = []
    for raw in open(_FLOW / name):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("visualize"):
            continue
        if line.startswith("source "):
            rel = line.split(None, 1)[1]
            line = f"source {(_FLOW / rel).resolve()}"
        if bdb_path is not None and line.startswith("open_bdb"):
            line = f"open_bdb {bdb_path}"
        lines.append(line)
    return lines


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


# A real net prefix per vehicle (cell-level template classes where the
# vehicle has one, top-level buses otherwise) — the governed-run tests
# resolve against actual nets, not a dead prefix.
_GOV_PREFIX = {
    "06_multipin_stress.buda": "d1_mp3_",       # cell-level class, 6 occ.
    "07_wide_fan_stress.buda": "mp7_",
    "08_cross_level.buda": "b_lohi_",           # cell-level class, 4 occ.
    "09_local_global_compete.buda": "loc",
    "10_chip_units_blocks_leaf.buda": "b_fw_",
}


def _governed_run(flow, ndr_lines):
    """Replay the vehicle with the NDR declarations injected just before
    run_hier_bundler; returns (session, full output)."""
    s = _session()
    out = []
    for line in _flow_lines(flow):
        if line.startswith("run_hier_bundler"):
            for c in ndr_lines:
                out.append(_run(s, c))
        out.append(_run(s, line))
    return s, "".join(out)


@pytest.mark.parametrize("flow", _HIER_FLOWS)
def test_hier_governed_flow_completes(flow):
    # R2d flip of the phase-1 refusal test: a shield-only rule on a real
    # net prefix must be ACCEPTED by run_hier_bundler, reported as
    # governance, and the vehicle must still run to a detailed result.
    s, out = _governed_run(flow, [
        "def_ndr shieldr shield bus net GND",
        f"set_ndr {_GOV_PREFIX[flow]} shieldr"])
    assert "governed by declared rules" in out
    assert s.detailed_result is not None
    assert any(w.input.ndr.active() for w in s.bundles)


def test_hier_cell_template_class_shields_every_instance():
    # The heart of R2d: a class-wide scope on vehicle 06's 6-occurrence
    # cell-level template governs template + 5 replicas (6 wrappers), and
    # the expanded result carries EMITTED SHIELD rows — the constraint
    # physically propagated to the instances, where the refusal used to
    # stand.  (Baseline 06 emits zero shields.)
    s, out = _governed_run("06_multipin_stress.buda", [
        "def_ndr shieldr shield bus net GND",
        "set_ndr d1_mp3_ shieldr"])
    assert "[NDR] 6 bundle(s) governed by declared rules" in out
    shields = [ns for ns in s.detailed_result.net_segments if ns.is_shield]
    assert len(shields) > 0
    gov = [w for w in s.bundles if w.input.ndr.active()]
    assert len(gov) == 6                  # one expanded wrapper per instance
    gov_ids = {w.input.original_bundle.id for w in gov}
    assert {ns.bundle_id for ns in shields} <= gov_ids
    # Every governed INSTANCE carries shields (propagation, not just the
    # reference).
    assert len({ns.bundle_id for ns in shields}) == 6


def test_hier_layer_restriction_survives_scope_clearing():
    # Codex #626: specs resolve at bundling and stay on the wrappers, so
    # clearing the last scope AFTER run_hier_bundler must not strip the
    # still-governed bundles' layer restrictions — the resolver's NDR
    # re-application gates on each wrapper's active spec, not the scope
    # table.  Rule restricted to M5/M6; scope cleared post-bundling; the
    # expanded governed wrappers must still carry exactly {5, 6}.
    s = _session()
    for line in _flow_lines("06_multipin_stress.buda"):
        if line.startswith("run_hier_bundler"):
            _run(s, "def_ndr shieldr shield bus net GND layers M5,M6")
            _run(s, "set_ndr d1_mp3_ shieldr")
        _run(s, line)
        if line.startswith("run_hier_bundler"):
            _run(s, "set_ndr d1_mp3_ off")     # last scope cleared
        if line.startswith("run_planner"):
            break
    gov = [w for w in s.bundles if w.input.ndr.active()]
    assert len(gov) == 6                       # expansion kept the specs
    for w in gov:
        assert list(w.input.allowed_layers) == [5, 6]


def test_hier_noncongruent_scope_refuses():
    # Vehicle 05's `is_lr` bus is a REAL cell-level template class (one
    # sub_cell template, 23 replicas).  A scope naming only ONE
    # occurrence's prefix governs that replica but not the template — the
    # class would be solved once under one rule and copied to occurrences
    # resolving another.  Hard error naming the diverging partitions.
    with pytest.raises(SystemExit):
        _governed_run("05_stress_grid.buda", [
            "def_ndr shieldr shield bus net GND",
            "set_ndr is_lr_u1s2_ shieldr"])


def test_hier_class_scope_governs_template_and_replicas():
    # The congruent form on the same REAL template class: one class-wide
    # prefix governs the template and every replica identically — no
    # split, 24 governed occurrences, flow completes with shields.
    s, out = _governed_run("05_stress_grid.buda", [
        "def_ndr shieldr shield bus net GND",
        "set_ndr is_lr_ shieldr"])
    assert "governed by declared rules" in out
    assert "split hbundle" not in out          # uniform class: no split
    assert s.detailed_result is not None
    gov = [w for w in s.bundles if w.input.ndr.active()]
    assert len(gov) == 24                      # one expanded wrapper per occ.
    shields = [ns for ns in s.detailed_result.net_segments if ns.is_shield]
    assert len({ns.bundle_id for ns in shields}) == 24


def test_hier_congruent_per_bit_scopes_split_in_lockstep():
    # Per-occurrence scopes on the SAME bit of every occurrence partition
    # the whole class identically — the template splits into rule-uniform
    # parts, its replicas split in LOCKSTEP (parent linkage rewired
    # part-for-part), and the flow completes with every occurrence's
    # governed 1-bit part carrying the rule.
    ndr = ["def_ndr shieldr shield bus net GND"]
    ndr += [f"set_ndr is_lr_u{u}s{k}_0 shieldr"
            for u in range(1, 7) for k in range(1, 5)]
    s, out = _governed_run("05_stress_grid.buda", ndr)
    assert "[NDR] split hbundle" in out
    assert s.detailed_result is not None
    gov = [w for w in s.bundles if w.input.ndr.active()]
    # 24 occurrences × the 1-bit governed part.
    assert len(gov) == 24
    assert all(len(w.input.original_bundle.get_net_names()) == 1
               for w in gov)


@pytest.mark.parametrize("flow", _HIER_FLOWS)
def test_v21_rules_persist_beside_a_hier_design(flow, tmp_path):
    # Run the full hier flow against a DISK BDB (the flows use :memory:),
    # then persist rules/scopes into the populated design DB and restore
    # them in a fresh session — the v21 tables coexist with the whole
    # bundle/topology/routing row population.
    bdb_path = tmp_path / f"{flow}.bdb"
    s = _session()
    for line in _flow_lines(flow, bdb_path=bdb_path):
        _run(s, line)
    # The hier flow itself created no rules (no silent rule creation).
    assert list(s.bdb.ndr_rules()) == []
    assert list(s.bdb.ndr_scopes()) == []
    # Declare AFTER the hier pipeline ran (scopes now would not re-enter
    # run_hier_bundler): write-through lands in the hier BDB.
    _run(s, "def_ndr shielded width x2 spacing x2 shield bus net GND")
    _run(s, "set_ndr fut_ shielded")
    assert [r.name for r in s.bdb.ndr_rules()] == ["shielded"]
    del s
    s2 = _session()
    out = _run(s2, f"open_bdb {bdb_path}")
    assert "restored 1 rule(s) and 1 scope(s)" in out
    assert ndr_cmds.ndr_rule_for_net(s2, "fut_x") == "shielded"
    r = s2._ndr_rules["shielded"]
    assert r["width_x"] == 2.0 and r["shield_mode"] == 1


def test_hier_flow_completes_clean_under_v21(tmp_path):
    # One representative end-to-end sanity: the deepest vehicle (08, the
    # cross-level design) runs to a detailed result against a disk BDB at
    # schema v21, and the DB reports the current version.
    bdb_path = tmp_path / "cross_level.bdb"
    s = _session()
    for line in _flow_lines("08_cross_level.buda", bdb_path=bdb_path):
        _run(s, line)
    assert s.detailed_result is not None
    assert s.bdb.schema_version() == buda.BDB.SCHEMA_VERSION


# ── the governing rule on EXPANDED per-instance rows ───────────────────────
#
# `bundle.ndr_rule` is the comparison basis `load_pipeline` audits against.
# It was stamped on the two NON-expanded persist sites but not on
# `_add_expanded_bundle`, so every per-instance row of a governed template
# persisted as ungoverned.  That is not merely lost provenance: the expanded
# view compares the stamp against a FRESH resolution, so an unstamped
# instance read as "persisted under the default rule" and had its restored
# plan VOIDED — on a design whose rules never changed.

def _governed_hier_checkpoint(bdb_path, width="x1.5", net="GND"):
    """Run 08_cross_level with a rule governing its cell-level class, against
    a disk BDB, and return the session."""
    s = _session()
    decls = [f"def_ndr cls {width} shield bus net {net} credit".replace(
                 " cls ", " cls width "),
             "set_ndr b_lohi_ cls"]
    for line in _flow_lines("08_cross_level.buda", bdb_path=bdb_path):
        if line.startswith("run_hier_bundler"):
            for c in decls:
                _run(s, c)
        _run(s, line)
    return s


def _resume_expanded(bdb_path, pre=()):
    """Re-open the checkpoint and restore the POST-expansion view, replaying
    only the setup a resume needs (the hier construction is in the BDB)."""
    s = _session()
    out = []
    for c in pre:
        out.append(_run(s, c))
    for line in _flow_lines("08_cross_level.buda", bdb_path=bdb_path):
        if line.startswith(("open_bdb", "def_layer", "def_track_pattern",
                            "corner_margin", "set_min_stub", "source",
                            "add_blocks_from_bdb")):
            out.append(_run(s, line))
    out.append(_run(s, "load_pipeline expanded"))
    return s, "".join(out)


def test_an_expanded_instance_records_the_rule_that_governed_it(tmp_path):
    bdb_path = tmp_path / "gov.bdb"
    s = _governed_hier_checkpoint(bdb_path)
    expanded = [b for b in s.bdb.all_bundles() if b.is_expanded]
    assert expanded, "vehicle produced no expanded per-instance rows"
    governed = [b for b in expanded if b.ndr_rule]
    assert governed, ("no expanded row carries a governing rule — the stamp "
                      "is missing from the expansion persist path")
    # It is the TEMPLATE's rule, verbatim: an instance is the template's
    # routing copied, so a differing stamp would mean they were priced apart.
    assert {b.ndr_rule for b in governed} == {
        b.ndr_rule for b in s.bdb.all_bundles()
        if not b.is_expanded and b.ndr_rule and "cls" in b.ndr_rule}


def test_an_unchanged_rule_does_not_void_the_expanded_restore(tmp_path):
    """The false alarm.  Nothing about the design changed between the two
    sessions, so every restored instance plan must survive."""
    bdb_path = tmp_path / "gov.bdb"
    _governed_hier_checkpoint(bdb_path)
    _s2, out = _resume_expanded(bdb_path)
    assert "VOIDED" not in out, out
    assert "persisted under rule 'default'" not in out, out


def test_a_repriced_rule_still_voids_the_expanded_restore(tmp_path):
    """…and the guard it was hiding behind still fires.

    Fixing a false alarm must not disarm the check: a rule whose DEMAND
    changed since the checkpoint has to void, or a plan priced for one width
    is silently reused for another.  Declared before `open_bdb` so the
    session-typed rule wins over the restored one.

    The change must move the PRICING fingerprint, not merely the declared
    text — x1.5 and x2 both quantize to 2 slots + 1 guard on this grid, so
    swapping them is correctly a no-op to this audit.
    """
    bdb_path = tmp_path / "gov.bdb"
    _governed_hier_checkpoint(bdb_path)
    _s2, out = _resume_expanded(bdb_path, pre=[
        "def_ndr cls width x3 spacing x3 shield bus net VSS credit",
        "set_ndr b_lohi_ cls"])
    assert "VOIDED" in out, out
    # And it names the rule it was PERSISTED under, not "default" — the
    # message was the visible half of this defect.
    assert "persisted under rule 'cls'" in out, out
