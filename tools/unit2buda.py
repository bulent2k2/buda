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
explicit ``path/to/test_file.py::test_func``.  The captured generator call becomes a
single net (driver -> receivers); the script ends with ``generate_topologies`` +
``visualize_topologies``.

Limitations: only the flat topology-generation API is captured (Floorplan +
TopologyGenerator.generate_candidates).  Tests that need pytest fixtures, drive the
CLI/BDB flow, or never call ``generate_candidates`` are reported and skipped.
"""

import argparse
import importlib.util
import inspect
import os
import sys
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


class _RecordingTopologyGenerator(buda.TopologyGenerator):
    def __init__(self, fp):
        super().__init__(fp)
        self._u2b_fp = fp
        self._u2b_lids = None

    def set_layer_ids(self, h, v):
        self._u2b_lids = (h, v)
        return super().set_layer_ids(h, v)

    def generate_candidates(self, driver, receivers):
        _GEN_CALLS.append({
            "driver": driver,
            "receivers": list(receivers),
            "fp": self._u2b_fp,
            "lids": self._u2b_lids,
        })
        return super().generate_candidates(driver, receivers)


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
    params = [p for p in inspect.signature(fn).parameters.values()
              if p.default is inspect.Parameter.empty
              and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]
    if params:
        sys.exit(f"error: {func_name!r} takes fixtures/parameters {[p.name for p in params]} "
                 "— unit2buda only supports fixtureless topology tests.")
    return fn


def _run_capturing(fn):
    """Run the test with patched constructors; tolerate its assertions failing."""
    _GEN_CALLS.clear()
    orig_fp, orig_tg = buda.Floorplan, buda.TopologyGenerator
    buda.Floorplan = _RecordingFloorplan
    buda.TopologyGenerator = _RecordingTopologyGenerator
    try:
        fn()
    except Exception:  # noqa: BLE001 — the test's own asserts/xfail are expected
        pass
    finally:
        buda.Floorplan, buda.TopologyGenerator = orig_fp, orig_tg


# ── .buda emission ───────────────────────────────────────────────────────────

def _teg_word(mode):
    return "over" if mode == buda.TegMode.OVER else "thru"


def _dir_word(d):
    return "H" if d == buda.LayerDir.HORIZONTAL else "V"


def emit_script(call, test_spec, n_calls):
    fp = call["fp"]
    lids = call["lids"]
    calls = getattr(fp, "_u2b", [])

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
            glob.append(f"set_min_stub_length_layer {c[1]} {c[2]}")
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
    if fts:
        out.extend(fts)
        out.append("")

    # 6. Net from the generator call, then bundle + generate + visualize.
    drv = call["driver"]
    rcvs = call["receivers"]
    rcv_csv = ",".join(f"{r}.i" for r in rcvs)
    out.append(f"add_net net0 {drv}.o {rcv_csv}")
    out.append("run_bundler")
    out.append("generate_topologies")
    out.append("# Swap for `visualize` to see the floorplan + nominal buses instead.")
    out.append("visualize_topologies")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Convert a topology unit test into a runnable .buda script.")
    ap.add_argument("test", help="test function name, or path/to/test_file.py::test_func")
    ap.add_argument("-o", "--output", help="output .buda path (default: stdout)")
    args = ap.parse_args()

    module_path, func_name = _resolve_test(args.test)
    fn = _load_func(module_path, func_name)
    _run_capturing(fn)

    if not _GEN_CALLS:
        sys.exit(f"error: {func_name!r} never called TopologyGenerator.generate_candidates "
                 "— nothing to convert (is it a CLI/BDB-flow or non-topology test?).")

    spec = os.path.relpath(module_path, REPO_ROOT) + "::" + func_name
    script = emit_script(_GEN_CALLS[0], spec, len(_GEN_CALLS))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(script)
        print(f"Wrote {args.output} "
              f"({_GEN_CALLS[0]['driver']} -> {_GEN_CALLS[0]['receivers']}). "
              f"Run: ./buda {args.output}")
    else:
        sys.stdout.write(script)


if __name__ == "__main__":
    main()
