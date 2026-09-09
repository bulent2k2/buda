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
"""ONE page over every run `snapshots.py` has rendered.

    contact_sheet.py [renders_dir] [-o index.html]

`snapshots.py` writes one directory of stage PNGs per run, which answers
"is THIS run right".  The question a study asks is the other one -- does
this run look like its neighbours -- and that needs them side by side: the
same stage across N, across arms, across the variants of one arm.  Forty-
eight directories in a file manager do not answer it.

Nothing is rendered here and nothing is re-run; this reads the PNGs that
are already on disk and writes an `index.html` beside them whose `img` tags
are RELATIVE, so the page works from the filesystem with no server and no
copies of the images.

Runs are grouped by their slug's leading path (`tier1a_n8_...` -> tier1a),
and within a run the stages keep their flow order, because the ordinal is
the first thing in every filename.
"""
import argparse
import os
import re
import sys

CSS = """
:root { color-scheme: light dark; --bg:#111; --fg:#eee; --dim:#888; --line:#333; }
body { margin:0; padding:1.2rem 1.6rem; background:var(--bg); color:var(--fg);
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
h1 { font-size:1.1rem; margin:0 0 .3rem; }
h2 { font-size:.95rem; margin:2rem 0 .2rem; color:#9cf; border-bottom:1px solid var(--line); }
h3 { font-size:.8rem; margin:1.1rem 0 .35rem; font-weight:600; }
.sub { color:var(--dim); margin:0 0 1rem; }
.row { display:flex; flex-wrap:wrap; gap:.8rem; align-items:flex-start; }
figure { margin:0; }
figcaption { color:var(--dim); font-size:.72rem; padding-top:.2rem; }
img { display:block; max-height:340px; width:auto; background:#000; border:1px solid var(--line); }
a { color:#9cf; }
"""


def sheets(root):
    """[(run_slug, [(ordinal, stage, relative png path)])] in path order."""
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        shots = []
        for f in sorted(os.listdir(p)):
            m = re.match(r"(\d+)-(.+)\.png$", f)
            if m:
                shots.append((int(m.group(1)), m.group(2), f"{d}/{f}"))
        if shots:
            out.append((d, sorted(shots)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "renders"), help="the snapshots output root")
    ap.add_argument("-o", "--out", default=None, help="the page to write (default <root>/index.html)")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.exit(f"contact_sheet: no {root} -- run snapshots.py over the runs first")
    runs = sheets(root)
    if not runs:
        sys.exit(f"contact_sheet: no <NN>-<stage>.png under {root}")
    out = a.out or os.path.join(root, "index.html")

    n_png = sum(len(s) for _r, s in runs)
    body = [f"<h1>LibreLane stage renders — {len(runs)} runs, {n_png} images</h1>",
            '<p class="sub">Rendered from the DEFs each run already wrote '
            "(<code>snapshots.py</code>); nothing was re-run. Click an image for "
            "full resolution.</p>"]
    group = None
    for slug, shots in runs:
        g = slug.split("_")[0]
        if g != group:
            group = g
            body.append(f"<h2>{g}</h2>")
        body.append(f"<h3>{slug.replace('_', ' / ')}</h3><div class=\"row\">")
        for n, stage, rel in shots:
            body.append(f'<figure><a href="{rel}"><img src="{rel}" loading="lazy" '
                        f'alt="{slug} {stage}"></a>'
                        f"<figcaption>{n} {stage}</figcaption></figure>")
        body.append("</div>")
    with open(out, "w") as f:
        f.write("<!doctype html><meta charset=utf-8><title>LibreLane stage renders</title>"
                f"<style>{CSS}</style>" + "\n".join(body) + "\n")
    print(f"contact_sheet: {len(runs)} run(s), {n_png} image(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
