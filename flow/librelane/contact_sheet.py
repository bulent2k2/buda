"""ONE page over every run `snapshots.py` has rendered.

    contact_sheet.py [renders_dir] [-o index.html] [--embed] [--width PX]

`snapshots.py` writes one directory of stage PNGs per run, which answers
"is THIS run right".  The question a study asks is the other one -- does
this run look like its neighbours -- and that needs them side by side: the
same stage across N, across arms, across the variants of one arm.  Forty-
eight directories in a file manager do not answer it.

Nothing is rendered here and nothing is re-run; this reads the PNGs that
are already on disk and writes an `index.html` beside them.  By default the
`img` paths are RELATIVE, so the page works from the filesystem with no
server and no copies of the images.  `--embed` inlines a downscaled copy of
each instead, giving ONE self-contained file that can be shared -- at
`--width 400` the whole set is about a quarter the size of the originals,
because a KLayout render quantizes well.

Runs are grouped by their slug's leading path (`tier1a_n8_...` -> tier1a),
and within a run the stages are laid out left to right in FLOW order: the
ordinal is a real sequence (floorplan, macros, PDN, placement, CTS,
routing), so reading across a row is reading the run forwards.

Each tile is captioned with how much of the stage is PLACED, from the
`stages.json` `snapshots.py` leaves beside the images.  That is the
difference between an empty stage and a broken render: a top DEF at
`floorplan` has its die, its rows and 296 components with no location at
all, so a grey rectangle is the whole truth of it.
"""
import argparse
import base64
import io
import json
import os
import re
import sys

CSS = """
/* Light is the base; every token is declared here before any block below
   redefines it.  The renders are KLayout's own palette -- saturated blue,
   orange, cyan, magenta, yellow on black -- so the page's chrome is
   deliberately achromatic and the one accent (a dusty teal) is a hue the
   renders never use, which is what keeps chrome reading as chrome. */
:root {
  color-scheme: light dark;
  --bg:#eaecee; --panel:#ffffff;
  --fg:#161b1f; --dim:#5d6a72; --faint:#8b979e;
  --line:#cdd3d7; --accent:#2d7d73; --flag:#9a5b32;
  --shadow:0 1px 2px rgba(20,30,35,.10);
  /* The ground a THUMBNAIL sits on, and the one token that does NOT change
     with the theme: a KLayout render is drawn on black, the tiles are a
     fixed size and the images are not, so `object-fit:contain` letterboxes
     every one of them.  On a light ground those bars read as padding
     somebody forgot to remove; on the render's own black they vanish. */
  --shot:#0b0e10;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#0d1013; --panel:#161b20;
    --fg:#dfe5e9; --dim:#8794a0; --faint:#5b6771;
    --line:#252d34; --accent:#7fb0a6; --flag:#c08a5e;
    --shadow:none;
  }
}
:root[data-theme="dark"] {
  --bg:#0d1013; --panel:#161b20;
  --fg:#dfe5e9; --dim:#8794a0; --faint:#5b6771;
  --line:#252d34; --accent:#7fb0a6; --flag:#c08a5e;
  --shadow:none;
}

*, *::before, *::after { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--fg);
  font:400 15px/1.55 "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
}
.wrap { max-width:1500px; margin:0 auto; padding:2rem 1.75rem 4rem; }

header { border-bottom:1px solid var(--line); padding-bottom:1.25rem; }
h1 {
  margin:0; font-size:1.6rem; font-weight:600; letter-spacing:-.015em;
  text-wrap:balance;
}
.lede { margin:.5rem 0 0; max-width:64ch; color:var(--dim); font-size:.95rem; }
.counts {
  display:flex; flex-wrap:wrap; gap:1.75rem; margin-top:1.1rem;
  font-family:"IBM Plex Mono", ui-monospace, Menlo, monospace;
  font-size:.8rem; font-variant-numeric:tabular-nums;
}
.counts b { display:block; font-size:1.35rem; font-weight:600; letter-spacing:-.02em; }
.counts span { color:var(--faint); text-transform:uppercase; letter-spacing:.09em; font-size:.66rem; }

.tools {
  position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:.6rem;
  align-items:center; padding:.85rem 0; margin-bottom:.5rem;
  background:var(--bg); border-bottom:1px solid var(--line);
}
.tools input {
  flex:1 1 15rem; min-width:11rem; padding:.42rem .6rem;
  font:400 .82rem/1.3 "IBM Plex Mono", ui-monospace, Menlo, monospace;
  color:var(--fg); background:var(--panel);
  border:1px solid var(--line); border-radius:3px;
}
.tools input:focus-visible, .chip:focus-visible, .tile:focus-visible {
  outline:2px solid var(--accent); outline-offset:2px;
}
.chip {
  padding:.32rem .62rem; font:500 .72rem/1.2 "IBM Plex Mono", ui-monospace, monospace;
  color:var(--dim); background:transparent; border:1px solid var(--line);
  border-radius:999px; cursor:pointer;
}
.chip[aria-pressed="true"] { color:var(--panel); background:var(--accent); border-color:var(--accent); }
.hit { margin-left:auto; color:var(--faint); font-size:.74rem;
       font-family:"IBM Plex Mono", ui-monospace, monospace; }

h2 {
  margin:2.6rem 0 .2rem; font-size:.7rem; font-weight:600;
  text-transform:uppercase; letter-spacing:.16em; color:var(--accent);
}
.run { padding:1rem 0 .4rem; border-top:1px solid var(--line); }
.run > h3 {
  margin:0 0 .7rem; display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap;
  font:500 .84rem/1.3 "IBM Plex Mono", ui-monospace, Menlo, monospace;
  letter-spacing:-.01em;
}
.kind {
  font-size:.6rem; letter-spacing:.11em; text-transform:uppercase;
  color:var(--faint); border:1px solid var(--line); border-radius:2px;
  padding:.08rem .35rem;
}
/* Fixed tracks so a tile sits in the same place from one run to the next --
   reading DOWN a column compares the same stage across runs. */
.strip { display:grid; grid-template-columns:repeat(auto-fill, 196px); gap:.9rem; }
.tile {
  display:block; padding:0; text-align:left; text-decoration:none; color:inherit;
  background:none; border:0; cursor:zoom-in; font:inherit;
}
.tile img {
  display:block; width:196px; height:150px; object-fit:contain;
  background:var(--shot); border:1px solid var(--line); border-radius:2px;
  box-shadow:var(--shadow);
}
.tile:hover img { border-color:var(--accent); }
.cap {
  display:flex; gap:.4rem; align-items:baseline; padding-top:.32rem;
  font:400 .7rem/1.35 "IBM Plex Mono", ui-monospace, Menlo, monospace;
}
.ord { color:var(--faint); font-variant-numeric:tabular-nums; }
.pl { display:block; color:var(--faint); font-size:.66rem; padding-top:.05rem;
      font-family:"IBM Plex Mono", ui-monospace, Menlo, monospace; }
.pl.empty { color:var(--flag); }

dialog {
  padding:0; border:1px solid var(--line); border-radius:4px;
  background:var(--panel); color:var(--fg); max-width:96vw; max-height:94vh;
}
dialog::backdrop { background:rgba(0,0,0,.82); }
dialog img { display:block; max-width:92vw; max-height:78vh; background:var(--shot); }
dialog .meta {
  padding:.6rem .8rem;
  font:400 .74rem/1.5 "IBM Plex Mono", ui-monospace, Menlo, monospace;
  color:var(--dim); word-break:break-all;
}
dialog .meta b { color:var(--fg); font-weight:600; }
footer { margin-top:3rem; padding-top:1.1rem; border-top:1px solid var(--line);
         color:var(--faint); font-size:.78rem; max-width:70ch; }
code { font-family:"IBM Plex Mono", ui-monospace, Menlo, monospace; font-size:.92em; }
@media (prefers-reduced-motion:reduce) { * { animation:none !important; transition:none !important; } }
"""


def sheets(root):
    """[(run_slug, [(ordinal, stage, relative png path, caption)])] in path order.

    The caption comes from the `stages.json` `snapshots.py` leaves beside
    the images, when there is one.  It carries how much of the stage is
    PLACED, which is the difference between an empty stage and a broken
    render -- at `floorplan` a top DEF has its die, its rows and 296
    components with no location at all, so a grey rectangle is the whole
    truth of it and the caption should say so rather than leave the reader
    to wonder."""
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        placed, run_path = {}, None
        try:
            with open(os.path.join(p, "stages.json")) as f:
                man = json.load(f)
            placed = {s["png"]: (s["components"], s["placed"])
                      for s in man.get("stages", []) if s.get("png")}
            # The run's REAL path, from the manifest.  It cannot be recovered
            # from the slug: `_` is both the separator and a character the
            # directories use (`pe_cell`, `two_reg32`, `hb_hoffset109`), so
            # un-replacing it gives `n8/h/pe/cell/runs/h` -- a path to
            # nowhere.  The slug is a lossy encoding and only travels one way.
            run_path = man.get("run")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        shots = []
        for f in sorted(os.listdir(p)):
            m = re.match(r"(\d+)-(.+)\.png$", f)
            if not m:
                continue
            total, loc = placed.get(f, (None, None))
            if total is None:
                cap = ""
            elif not total:
                cap = "no components"
            elif not loc:
                cap = f"nothing placed yet ({total} to come)"
            else:
                cap = f"{loc} of {total} placed"
            shots.append((int(m.group(1)), m.group(2), f"{d}/{f}", cap))
        if shots:
            out.append((d, run_path, sorted(shots)))
    return out


KIND = [("top/runs", "top"), ("runs/flat", "flat arm F"), ("/runs/", "block")]


def kind_of(p):
    """What sort of run this is, from its path -- a top, a hardened block,
    or the flat arm.  Worth saying on the tile because the same stage means
    different things in each: a block has no macro placement to look at."""
    for needle, label in KIND:
        if needle in p:
            return label
    return "run"


def thumb(path, width):
    """A downscaled, quantized copy as a `data:` URI.

    A KLayout render is flat colour over black, so 16 colours after a
    resample costs nothing visible at contact-sheet size and is what makes
    the whole set embeddable -- measured over 356 images, 33 MB of
    originals become 6.6 MB of thumbnails."""
    from PIL import Image                       # only --embed needs it
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > width:
        im = im.resize((width, max(1, round(h * width / w))), Image.LANCZOS)
    im = im.quantize(colors=16, method=Image.MEDIANCUT)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


JS = """
const q = document.getElementById('q');
const runs = [...document.querySelectorAll('.run')];
const hit = document.getElementById('hit');
const chips = [...document.querySelectorAll('.chip')];
function apply() {
  const t = q.value.trim().toLowerCase();
  const on = chips.filter(c => c.getAttribute('aria-pressed') === 'true')
                  .map(c => c.dataset.stage);
  let shown = 0;
  for (const r of runs) {
    let any = false;
    for (const f of r.querySelectorAll('figure')) {
      const ok = (!on.length || on.includes(f.dataset.stage));
      f.hidden = !ok;
      if (ok) any = true;
    }
    const match = !t || r.dataset.slug.includes(t);
    r.hidden = !(match && any);
    if (!r.hidden) shown++;
  }
  for (const h of document.querySelectorAll('h2')) {
    h.hidden = !runs.some(r => !r.hidden && r.dataset.group === h.dataset.group);
  }
  hit.textContent = shown + ' of ' + runs.length + ' runs';
}
q.addEventListener('input', apply);
for (const c of chips) c.addEventListener('click', () => {
  c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
  apply();
});
const dlg = document.getElementById('zoom');
for (const t of document.querySelectorAll('.tile')) t.addEventListener('click', () => {
  dlg.querySelector('img').src = t.querySelector('img').src;
  dlg.querySelector('.meta').innerHTML =
    '<b>' + t.dataset.run + '</b> &nbsp; ' + t.dataset.stagefull + '<br>' + t.dataset.file;
  dlg.showModal();
});
dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
apply();
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "renders"), help="the snapshots output root")
    ap.add_argument("-o", "--out", default=None,
                    help="the page to write (default <root>/index.html)")
    ap.add_argument("--embed", action="store_true",
                    help="inline a downscaled copy of every image, for ONE shareable file")
    ap.add_argument("--width", type=int, default=400, help="embedded thumbnail width (400)")
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.exit(f"contact_sheet: no {root} -- run snapshots.py over the runs first")
    runs = sheets(root)
    if not runs:
        sys.exit(f"contact_sheet: no <NN>-<stage>.png under {root}")
    out = os.path.abspath(a.out or os.path.join(root, "index.html"))
    # The `img` paths are resolved by the BROWSER, against the PAGE's own
    # directory -- not against the render root the names were built from.
    # With `-o` pointing anywhere else the two differ and every image and
    # link on the page is broken (Codex #911), so the page's own directory
    # is what they are made relative to.
    rel = os.path.relpath(root, os.path.dirname(out))

    def url(p):
        return p if rel == "." else f"{rel}/{p}"

    n_png = sum(len(s) for _r, _rp, s in runs)
    stages = sorted({st for _r, _rp, shots in runs for _n, st, _p, _c in shots})
    empty = sum(1 for _r, _rp, shots in runs for _n, _s, _p, c in shots
                if c.startswith("nothing"))

    head = [f"<h1>{len(runs)} LibreLane runs, stage by stage</h1>",
            '<p class="lede">Every stage each run already wrote, rendered from its own DEF through '
            "KLayout and laid out left to right in flow order &mdash; floorplan, macro placement, "
            "the power grid, placement, CTS, routing. Nothing was re-run. Read <em>across</em> a row "
            "to follow one run forwards; read <em>down</em> a column to compare the same stage across "
            "N, across arms, and across the variants of one arm.</p>",
            '<div class="counts">',
            f"<div><b>{len(runs)}</b><span>runs</span></div>",
            f"<div><b>{n_png}</b><span>renders</span></div>",
            f"<div><b>{len(stages)}</b><span>stages</span></div>",
            f"<div><b>{empty}</b><span>stages with nothing placed</span></div>",
            "</div>"]
    tools = ['<div class="tools">',
             '<input id="q" type="search" placeholder="filter runs&hellip; n8, hb2, pe_cell, flat" '
             'aria-label="filter runs">']
    for st in stages:
        tools.append(f'<button class="chip" data-stage="{st}" aria-pressed="false">{st}</button>')
    tools += ['<span class="hit" id="hit"></span>', "</div>"]

    body, group = [], None
    for slug, run_path, shots in runs:
        g = slug.split("_")[0]
        if g != group:
            group = g
            body.append(f'<h2 data-group="{g}">{g}</h2>')
        path = run_path or slug          # never un-replace the slug: see sheets()
        body.append(f'<section class="run" data-slug="{path.lower()}" data-group="{g}">'
                    f'<h3>{path}<span class="kind">{kind_of(path)}</span></h3>'
                    f'<div class="strip">')
        for n, stage, p, cap in shots:
            src = thumb(os.path.join(root, p), a.width) if a.embed else url(p)
            klass = " empty" if cap.startswith("nothing") else ""
            body.append(
                f'<figure data-stage="{stage}">'
                f'<button class="tile" data-run="{path}" data-stagefull="{n} {stage}" '
                f'data-file="{p}">'
                f'<img src="{src}" alt="{path} {stage}" loading="lazy">'
                f'<figcaption class="cap"><span class="ord">{n}</span><span>{stage}</span>'
                "</figcaption>"
                + (f'<span class="pl{klass}">{cap}</span>' if cap else "")
                + "</button></figure>")
        body.append("</div></section>")

    where = (f"thumbnails inlined at {a.width} px; the full-resolution PNGs are in the render tree"
             if a.embed else
             "click a tile to enlarge; the caption names its file in the render tree")
    foot = ("<footer>Rendered by <code>flow/librelane/snapshots.py</code> from the DEFs on disk and "
            f"collected by <code>contact_sheet.py</code> &mdash; {where}. "
            "A stage marked <em>nothing placed yet</em> is not a failed render: at "
            "<code>floorplan</code> a DEF carries its die and its standard-cell rows and not one "
            "component with a location.</footer>")

    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>LibreLane Stage Sheet</title>"
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
            "family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600"
            '&display=swap">'
            f"<style>{CSS}</style></head><body><div class=\"wrap\">"
            "<header>" + "".join(head) + "</header>"
            + "".join(tools) + "".join(body) + foot + "</div>"
            '<dialog id="zoom"><img alt=""><div class="meta"></div></dialog>'
            f"<script>{JS}</script></body></html>\n")
    with open(out, "w") as f:
        f.write(html)
    print(f"contact_sheet: {len(runs)} run(s), {n_png} image(s) -> {out} "
          f"({os.path.getsize(out) / 1e6:.1f} MB)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
