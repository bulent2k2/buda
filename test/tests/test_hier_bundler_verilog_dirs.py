"""
BDD contract for Verilog-only HBundle bootstrap.

Current import_verilog records module-instance pins with direction UNKNOWN.
The intended HBundle behavior is to infer those directions from Verilog module
port declarations, write them back into BDB, and then bundle the directed nets.
"""
import pytest
from pytest_bdd import scenario, given, when, then, parsers
import buda


_FEATURE = "features/hier_bundler_verilog_dirs.feature"


@scenario(_FEATURE, "imported Verilog pins start without BDB directions")
def test_imported_verilog_pins_start_unknown():
    pass


@scenario(_FEATURE, "HBundle generation writes inferred pin directions back into BDB")
def test_hbundle_generation_writes_inferred_pin_directions():
    pass


@scenario(_FEATURE, "HBundle generation groups Verilog-imported connectivity after direction inference")
def test_hbundle_generation_groups_after_direction_inference():
    pass


@pytest.fixture
def context(tmp_path):
    return {"tmp_path": tmp_path}


def _write_verilog(tmp_path):
    v_path = tmp_path / "dir_infer.v"
    v_path.write_text(
        """
module producer(output y);
endmodule

module consumer(input a);
endmodule

module top();
  wire sig;
  producer u_src (.y(sig));
  consumer u_dst (.a(sig));
endmodule
""".strip()
        + "\n"
    )
    return str(v_path)


def _pin_dirs_by_endpoint(db, net_name):
    comps = {c.id: c.name for c in db.all_components()}
    nets = {n.id: n.name for n in db.all_nets()}
    result = {}
    for p in db.all_pins():
        if nets.get(p.net_id) != net_name:
            continue
        comp_name = comps.get(p.comp_id)
        if comp_name is None:
            continue
        result[f"{comp_name}.{p.pin_name}"] = p.dir
    return result


@given("a BDB imported from Verilog with declared module port directions")
def given_verilog_import(context):
    db = buda.BDB(":memory:")
    db.import_verilog(_write_verilog(context["tmp_path"]))
    context["db"] = db


@when(parsers.re(r"run_hier_bundler is called with max_depth (?P<d>\d+)"))
def when_run_hier_bundler(context, d):
    hb = buda.HierarchicalBundler(context["db"])
    bundles = hb.run(int(d))
    context["bundles"] = bundles
    context["by_net"] = {n: b for b in bundles for n in b.net_names}


@then(parsers.re(r'net "(?P<net>[^"]+)" has pin "(?P<endpoint>[^"]+)" with direction "(?P<direction>[^"]+)"'))
def then_pin_has_direction(context, net, endpoint, direction):
    dirs = _pin_dirs_by_endpoint(context["db"], net)
    assert endpoint in dirs, (
        f"Net {net!r} has no pin {endpoint!r}; present pins: {sorted(dirs)}"
    )
    assert dirs[endpoint] == direction, (
        f"Expected {endpoint} on {net} to have direction {direction}, "
        f"got {dirs[endpoint]}"
    )


@then(parsers.re(r"there is (?P<n>\d+) hbundle"))
def then_hbundle_count(context, n):
    bundles = context.get("bundles", [])
    assert len(bundles) == int(n), (
        f"Expected {n} hbundle(s), got {len(bundles)}: "
        f"{[(b.id, b.reason, list(b.net_names)) for b in bundles]}"
    )


@then(parsers.re(r'the hbundle for net "(?P<net>[^"]+)" has reason "(?P<reason>[^"]+)"'))
def then_hbundle_reason(context, net, reason):
    bundle = context.get("by_net", {}).get(net)
    assert bundle is not None, f"No hbundle contains net {net!r}"
    assert bundle.reason == reason, (
        f"Expected hbundle reason {reason!r}, got {bundle.reason!r}"
    )
