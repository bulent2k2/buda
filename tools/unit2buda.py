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

"""unit2buda — turn a topology unit test into a runnable/visualizable .buda script.

Many tests under ``test/tests`` set up a ``buda.Floorplan`` (add_block /
add_block_rects / keepouts / margins / feedthru / min-stub) and a
``buda.TopologyGenerator`` (set_layer_ids) and then call
``generate_candidates(driver, [receivers])``.  This tool *runs* the test with the
``Floorplan`` / ``TopologyGenerator`` constructors monkeypatched to recording
subclasses, captures that setup, and emits the equivalent flat ``.buda`` script so
you can drive the same case through the CLI and the visualizer:

    tools/unit2buda.py <test name> -o out.buda
    ./buda out.buda                         # runs + opens the topology explorer

``<test name>`` is the test function name (searched across ``test/tests``) or an
explicit ``path/to/test_file.py::test_func``.

Two capture modes, auto-selected by what the test actually does:

  * **Topology** — the test calls ``TopologyGenerator.generate_candidates``: the
    captured call becomes a single net (driver -> receivers) and the script ends
    with ``generate_topologies`` + ``visualize_topologies`` (as above).
  * **CLI/BDB flow** — the test drives a ``buda.BudaSession`` via ``do_command``
    (e.g. the hier bundler / planner / NUTS flow): the exact command stream is
    recorded and emitted verbatim as the ``.buda`` script.

Fixtures & params: common pytest fixtures are supplied automatically —
``tmp_path`` / ``tmp_path_factory`` (a real, *persistent* temp dir so any BDB the
test writes survives for the emitted ``open_bdb`` to reference), ``capsys`` /
``capfd``, and ``monkeypatch``.  ``@pytest.mark.parametrize`` params are filled
from the first case (or ``--case N``).  Unsupported fixtures are reported.
"""

import argparse
import importlib.util
import inspect
import os
import pathlib
import sys
import tempfile
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(REPO_ROOT, "build"), os.path.join(REPO_ROOT, "src"), REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import buda  # noqa: E402  (after sys.path is set up)


# ── Recording subclasses ─────────────────────────────────────────────────────
# Subclasses (not wrappers) so a recorder still passes pybind11 type checks when a
# test hands it to e.g. ConnTopology.build(cand, fp).

# Every TopologyGenerator.generate_candidates call lands here, in order.
_GEN_CALLS = []  # list of dicts: {driver, receivers, fp, lids}

# Every BudaSession.do_command(cmd_line) lands here, in order (CLI/BDB-flow mode).
_CMD_CALLS = []  # list of str


class _RecordingFloorplan(buda.Floorplan):
    def __init__(self):
        super().__init__()
        self._u2b = []  # ordered (method, *args)

    # mutating setup we know how to serialize -------------------------------
    def add_block(self, name, x1, y1, x2, y2):
        self._u2b.append(("add_block", name, x1, y1, x2, y2))
        return super().add_block(name, x1, y1, x2, y2)

    def add_block_rects(self, name, rects, teg_mode=None):
        rl = [tuple(r) for r in rects]
        self._u2b.append(("add_block_rects", name, rl, teg_mode))
        if teg_mode is None:
            return super().add_block_rects(name, rl)
        return super().add_block_rects(name, rl, teg_mode)

    def add_keepout_zone(self, x1, y1, x2, y2, layers):
        ll = list(layers)
        self._u2b.append(("add_keepout_zone", x1, y1, x2, y2, ll))
        return super().add_keepout_zone(x1, y1, x2, y2, ll)

    def set_global_corner_margin(self, dx, dy):
        self._u2b.append(("set_global_corner_margin", dx, dy))
        return super().set_global_corner_margin(dx, dy)

    def set_block_corner_margin(self, name, dx, dy):
        self._u2b.append(("set_block_corner_margin", name, dx, dy))
        return super().set_block_corner_margin(name, dx, dy)

    def set_container(self, name, is_container=True):
        self._u2b.append(("set_container", name, is_container))
        return super().set_container(name, is_container)

    def set_block_teg_mode(self, name, mode):
        self._u2b.append(("set_block_teg_mode", name, mode))
        return super().set_block_teg_mode(name, mode)

    def set_min_stub_length(self, n):
        self._u2b.append(("set_min_stub_length", n))
        return super().set_min_stub_length(n)

    def set_min_stub_length_dir(self, d, n):
        self._u2b.append(("set_min_stub_length_dir", d, n))
        return super().set_min_stub_length_dir(d, n)

    def set_min_stub_length_layer(self, layer, n):
        self._u2b.append(("set_min_stub_length_layer", layer, n))
        return super().set_min_stub_length_layer(layer, n)

    def set_feedthru_block(self, block, on):
        self._u2b.append(("set_feedthru_block", block, on))
        return super().set_feedthru_block(block, on)

    def set_feedthru_layer(self, layer, on):
        self._u2b.append(("set_feedthru_layer", layer, on))
        return super().set_feedthru_layer(layer, on)

    def set_feedthru_block_layer(self, block, layer, on):
        self._u2b.append(("set_feedthru_block_layer", block, layer, on))
        return super().set_feedthru_block_layer(block, layer, on)

    def set_feedthru(self, on):
        self._u2b.append(("set_feedthru", on))
        return super().set_feedthru(on)


class _RecordingTopologyGenerator(buda.TopologyGenerator):
    def __init__(self, fp):
        super().__init__(fp)
        self._u2b_fp = fp
        self._u2b_lids = None
        self._u2b_multi_trunk = False

    def set_layer_ids(self, h, v):
        self._u2b_lids = (h, v)
        return super().set_layer_ids(h, v)

    def set_multi_trunk(self, on):
        # Opt-in flag for the two-level BITRUNK_HVH/VHV trees; captured so the emitted
        # script drives `generate_topologies multi_trunk` and reproduces those shapes.
        self._u2b_multi_trunk = bool(on)
        return super().set_multi_trunk(on)

    def generate_candidates(self, driver, receivers):
        # generate_candidates accepts a single receiver as a bare string; list("u1")
        # would split it into characters, so keep a str as one endpoint.
        rcv = [receivers] if isinstance(receivers, str) else list(receivers)
        _GEN_CALLS.append({
            "driver": driver,
            "receivers": rcv,
            # Snapshot the floorplan setup recorded SO FAR.  A later generate call may
            # mutate the same floorplan (e.g. change min-stub between calls), and
            # emit_script runs only after the whole test returns -- reading the live
            # list would fold those later mutations into this (earlier) call.
            "setup": list(self._u2b_fp._u2b),
            "lids": self._u2b_lids,
            "multi_trunk": self._u2b_multi_trunk,
        })
        return super().generate_candidates(driver, receivers)


# ── Fixture / parametrize provisioning ───────────────────────────────────────
# Enough of pytest's fixtures to run fixture-taking tests headless.  Assertions
# and captured-output checks in the test are expected to fail and are ignored —
# we only need the test's setup calls (generate_candidates / do_command).

class _CapsysStub:
    """Minimal capsys/capfd: readouterr() returns empty out/err."""
    class _Result:
        out = ""
        err = ""
    def readouterr(self):
        return _CapsysStub._Result()


class _TmpPathFactory:
    def mktemp(self, name="t", numbered=True):
        return pathlib.Path(tempfile.mkdtemp(prefix=f"unit2buda_{name}_"))
    def getbasetemp(self):
        return pathlib.Path(tempfile.gettempdir())


def _make_monkeypatch():
    try:
        from _pytest.monkeypatch import MonkeyPatch
        return MonkeyPatch()
    except Exception:
        class _MP:                       # inert fallback
            def __getattr__(self, _):
                return lambda *a, **k: None
        return _MP()


def _parametrize_values(fn, case):
    """Map @pytest.mark.parametrize param names → the chosen case's values."""
    out = {}
    for m in getattr(fn, "pytestmark", []):
        if getattr(m, "name", None) != "parametrize" or len(m.args) < 2:
            continue
        names = [n.strip() for n in m.args[0].split(",")]
        values = list(m.args[1])
        if not values:
            continue
        chosen = values[min(case, len(values) - 1)]
        if len(names) == 1:
            out[names[0]] = chosen
        else:
            seq = chosen if isinstance(chosen, (tuple, list)) else (chosen,)
            out.update(dict(zip(names, seq)))
    return out


def _provide_fixtures(fn, case, cleanups, notes):
    """Build kwargs for a test's required params from parametrize + known fixtures.
    Exits with a clear message on an unsupported fixture."""
    params = _parametrize_values(fn, case)
    kwargs, unknown = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.default is not inspect.Parameter.empty:
            continue
        if p.kind not in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY, p.KEYWORD_ONLY):
            continue
        name = p.name
        if name in params:
            kwargs[name] = params[name]
        elif name == "tmp_path":
            d = pathlib.Path(tempfile.mkdtemp(prefix="unit2buda_"))
            kwargs[name] = d               # persistent: files the test writes survive
            notes.append(f"tmp_path = {d}  (persistent; holds any BDB the test wrote)")
        elif name == "tmp_path_factory":
            kwargs[name] = _TmpPathFactory()
        elif name in ("capsys", "capfd", "capsysbinary", "capfdbinary"):
            kwargs[name] = _CapsysStub()
        elif name == "monkeypatch":
            mp = _make_monkeypatch()
            kwargs[name] = mp
            cleanups.append(getattr(mp, "undo", lambda: None))
        else:
            unknown.append(name)
    if unknown:
        sys.exit(f"error: {fn.__name__!r} needs unsupported fixture(s) {unknown}. "
                 "Supported: tmp_path, tmp_path_factory, capsys/capfd, monkeypatch, "
                 "and @pytest.mark.parametrize params (choose a case with --case N).")
    return kwargs


# ── Test discovery + execution ───────────────────────────────────────────────

def _resolve_test(spec):
    """Return (module_path, func_name).  Accepts 'func', 'file.py::func', 'file::func'."""
    if "::" in spec:
        path, func = spec.split("::", 1)
        if not os.path.isabs(path):
            cand = os.path.join(REPO_ROOT, path)
            path = cand if os.path.exists(cand) else path
        if not os.path.exists(path):
            sys.exit(f"error: test file not found: {path}")
        return path, func

    # Bare function name: search test/tests for `def <name>(`.
    func = spec
    needle = f"def {func}("
    matches = []
    tests_dir = os.path.join(REPO_ROOT, "test", "tests")
    for root, _, files in os.walk(tests_dir):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                fp = os.path.join(root, f)
                try:
                    with open(fp, encoding="utf-8") as fh:
                        if needle in fh.read():
                            matches.append(fp)
                except OSError:
                    pass
    if not matches:
        sys.exit(f"error: no test named {func!r} found under {tests_dir}")
    if len(matches) > 1:
        rels = "\n  ".join(os.path.relpath(m, REPO_ROOT) + "::" + func for m in matches)
        sys.exit(f"error: {func!r} is defined in multiple files; disambiguate:\n  {rels}")
    return matches[0], func


def _load_func(module_path, func_name):
    mod_name = "u2b_" + os.path.splitext(os.path.basename(module_path))[0]
    spec = importlib.util.spec_from_file_location(mod_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    fn = getattr(mod, func_name, None)
    if fn is None or not callable(fn):
        sys.exit(f"error: {func_name!r} is not a callable in {module_path}")
    return fn


def _run_capturing(fn, kwargs):
    """Run the test with the topology API AND BudaSession.do_command patched to
    record; tolerate its assertions failing (we only want the setup)."""
    _GEN_CALLS.clear()
    _CMD_CALLS.clear()
    orig_fp, orig_tg = buda.Floorplan, buda.TopologyGenerator
    buda.Floorplan = _RecordingFloorplan
    buda.TopologyGenerator = _RecordingTopologyGenerator

    # Record the CLI/BDB-flow command stream, if the test drives a BudaSession.
    buda_cli = orig_do = None
    try:
        import buda_cli  # noqa: E402
        orig_do = buda_cli.BudaSession.do_command

        def _rec_do(self, cmd_line, _orig=orig_do):
            _CMD_CALLS.append(cmd_line.strip())
            return _orig(self, cmd_line)

        buda_cli.BudaSession.do_command = _rec_do
    except Exception:  # noqa: BLE001 — buda_cli optional; topology mode still works
        pass

    try:
        fn(**kwargs)
    except Exception:  # noqa: BLE001 — the test's own asserts/xfail are expected
        pass
    finally:
        buda.Floorplan, buda.TopologyGenerator = orig_fp, orig_tg
        if orig_do is not None:
            buda_cli.BudaSession.do_command = orig_do


# ── .buda emission ───────────────────────────────────────────────────────────

def _teg_word(mode):
    return "over" if mode == buda.TegMode.OVER else "thru"


def _dir_word(d):
    return "H" if d == buda.LayerDir.HORIZONTAL else "V"


def emit_script(call, test_spec, n_calls):
    lids = call["lids"]
    calls = call["setup"]

    # Collect per-block trailers (corner margin / container / teg) to fold into the
    # block's definition line.
    margins = {}     # name -> (dx, dy)
    containers = set()
    teg_overrides = {}  # name -> TegMode
    for c in calls:
        if c[0] == "set_block_corner_margin":
            margins[c[1]] = (c[2], c[3])
        elif c[0] == "set_container" and c[2]:
            containers.add(c[1])
        elif c[0] == "set_block_teg_mode":
            teg_overrides[c[1]] = c[2]

    out = []
    out.append(f"# Generated by tools/unit2buda.py from {test_spec}")
    out.append("# Flat topology-generation case: blocks + one net, then visualize.")
    if n_calls > 1:
        out.append(f"# NOTE: the test made {n_calls} generate_candidates calls; "
                   "emitting the FIRST.")
    out.append("")

    # 1. Layers — set_layer_ids(h, v): first id is the H (trunk) layer, second V.
    if lids:
        h, v = lids
        out.append(f"def_layer {h} M{h} H TOP 50")
        out.append(f"def_layer {v} M{v} V TOP 50")
    else:
        out.append("# WARNING: test never called set_layer_ids; defaulting to M4(H)/M5(V)")
        out.append("def_layer 4 M4 H TOP 50")
        out.append("def_layer 5 M5 V TOP 50")
    out.append("")

    # 2. Global settings (min-stub, global corner margin).
    glob = []
    for c in calls:
        if c[0] == "set_min_stub_length":
            glob.append(f"set_min_stub_length {c[1]}")
        elif c[0] == "set_min_stub_length_dir":
            glob.append(f"set_min_stub_length_dir {_dir_word(c[1])} {c[2]}")
        elif c[0] == "set_min_stub_length_layer":
            # The CLI resolves this layer through _layer_name_map (name only, no
            # numeric fallback), so emit the M<id> name that def_layer defines.
            glob.append(f"set_min_stub_length_layer M{c[1]} {c[2]}")
        elif c[0] == "set_global_corner_margin":
            glob.append(f"corner_margin dx {c[1]} dy {c[2]}")
    if glob:
        out.extend(glob)
        out.append("")

    # 3. Blocks (with folded trailers).
    def trailer(name):
        t = []
        if name in containers:
            t.append("container")
        if name in margins:
            dx, dy = margins[name]
            t.append(f"corner_margin dx {dx} dy {dy}")
        return (" " + " ".join(t)) if t else ""

    for c in calls:
        if c[0] == "add_block":
            _, name, x1, y1, x2, y2 = c
            out.append(f"add_block {name} {x1} {y1} {x2} {y2}{trailer(name)}")
        elif c[0] == "add_block_rects":
            _, name, rects, teg = c
            teg = teg_overrides.get(name, teg)
            parts = [f"add_block {name}"]
            for (rx1, ry1, rx2, ry2) in rects:
                parts.append(f"rect {rx1} {ry1} {rx2} {ry2}")
            if teg is not None:
                parts.append(f"teg_mode {_teg_word(teg)}")
            out.append(" ".join(parts) + trailer(name))
    out.append("")

    # 4. Keepouts.
    kos = [c for c in calls if c[0] == "add_keepout_zone"]
    if kos:
        for c in kos:
            _, x1, y1, x2, y2, layers = c
            out.append(f"add_keepout {x1} {y1} {x2} {y2} " + " ".join(str(l) for l in layers))
        out.append("")

    # 5. Feedthru opt-ins.
    fts = []
    for c in calls:
        if c[0] == "set_feedthru_block":
            fts.append(f"set_feedthru {c[1]} * {'on' if c[2] else 'off'}")
        elif c[0] == "set_feedthru_layer":
            fts.append(f"set_feedthru * {c[1]} {'on' if c[2] else 'off'}")
        elif c[0] == "set_feedthru_block_layer":
            fts.append(f"set_feedthru {c[1]} {c[2]} {'on' if c[3] else 'off'}")
        elif c[0] == "set_feedthru":
            fts.append(f"set_feedthru * * {'on' if c[1] else 'off'}")
    if fts:
        out.extend(fts)
        out.append("")

    # 6. Net from the generator call, then bundle + generate + visualize.
    drv = call["driver"]
    rcvs = call["receivers"]
    rcv_csv = ",".join(f"{r}.i" for r in rcvs)
    out.append(f"add_net net0 {drv}.o {rcv_csv}")
    out.append("run_bundler")
    # multi_trunk opt-in adds the two-level BITRUNK_HVH/VHV trees (else they are
    # suppressed and the case would look identical to the default candidate set).
    gen_cmd = "generate_topologies multi_trunk" if call.get("multi_trunk") else "generate_topologies"
    out.append(gen_cmd)
    out.append("# Swap for `visualize` to see the floorplan + nominal buses instead.")
    out.append("visualize_topologies")
    out.append("")
    return "\n".join(out)


def emit_flow_script(cmds, test_spec, notes):
    """Emit the recorded BudaSession.do_command stream verbatim as a .buda script."""
    out = [f"# Generated by tools/unit2buda.py from {test_spec}",
           "# CLI/BDB-flow case: the exact BudaSession.do_command sequence the test ran."]
    for n in notes:
        out.append(f"#   {n}")
    out.append("")
    out.extend(cmds)
    out.append("")
    firsts = {c.split()[0] for c in cmds if c.split()}
    if not ({"visualize", "visualize_topologies"} & firsts):
        out.append("# The test asserted rather than visualizing; add a step to inspect, e.g.:")
        out.append("#   visualize")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Convert a topology or CLI/BDB-flow unit test into a runnable .buda script.")
    ap.add_argument("test", help="test function name, or path/to/test_file.py::test_func")
    ap.add_argument("-o", "--output", help="output .buda path (default: stdout)")
    ap.add_argument("--case", type=int, default=0,
                    help="which @pytest.mark.parametrize case to use (default: 0)")
    args = ap.parse_args()

    module_path, func_name = _resolve_test(args.test)
    fn = _load_func(module_path, func_name)
    cleanups, notes = [], []
    kwargs = _provide_fixtures(fn, args.case, cleanups, notes)
    _run_capturing(fn, kwargs)
    for c in cleanups:
        try:
            c()
        except Exception:  # noqa: BLE001
            pass

    spec = os.path.relpath(module_path, REPO_ROOT) + "::" + func_name

    if _GEN_CALLS:
        script = emit_script(_GEN_CALLS[0], spec, len(_GEN_CALLS))
        summary = f"{_GEN_CALLS[0]['driver']} -> {_GEN_CALLS[0]['receivers']}"
    elif _CMD_CALLS:
        script = emit_flow_script(_CMD_CALLS, spec, notes)
        summary = f"{len(_CMD_CALLS)} CLI command(s)"
    else:
        sys.exit(f"error: {func_name!r} neither called "
                 "TopologyGenerator.generate_candidates nor drove a BudaSession "
                 "(nothing to convert — is it a pure-API/data test?).")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(script)
        print(f"Wrote {args.output} ({summary}). Run: ./buda {args.output}")
    else:
        sys.stdout.write(script)


if __name__ == "__main__":
    main()
