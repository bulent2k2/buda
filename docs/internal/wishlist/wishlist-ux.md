# Wishlist — UX (web client, command surface, key-bindings)

Deferred follow-ups on how a person DRIVES BUDA, as opposed to what it
computes: the browser client's read-the-route affordances, and the command
surface's per-user customization. Part of the wishlist set indexed by
[`wishlist.md`](wishlist.md).

Every item here was offered during the work that created its neighbourhood
and deliberately not built, because each one is a convenience whose value
depends on somebody actually wanting it. None is blocking, and none is
load-bearing for correctness — which is exactly why they are written down
rather than guessed at.

| | open | cost | gate |
|---|---|---|---|
| 1 | palette / marker legend in the web client | small | a second person asking what a colour means |
| 2 | click-to-focus a bundle in the nuts / dnuts views | small | reaching for `[` / `]` on a many-bundle design |
| 3 | `check_design` violations as clickable markers | medium | a violation whose LOCATION is the question |
| 4 | per-user rc file (interactive-only aliases) | small | a user who wants their shorthand across sessions |
| 5 | user-remappable key-bindings | medium | a second person asking, on a GUI key that matters |

Items 4 and 5 are **not restated here** — they have a full treatment with
the portability reasoning that deferred them in
[`opens_ux.md`](../opens_ux.md), and a second copy would be the drift this
index exists to prevent. The governing rule recorded there is worth
repeating in one line, because it constrains anything built for either:
a customization ADDS a spelling or a key, never CHANGES what an existing
one means.

---

## 1. Palette / marker legend in the web client

**What it is.** A small key in the viewer saying what the colours and the
two ring kinds mean: H = orange, V = red, jog = gold; a solid magenta ring
is a block the candidate TAPS, a dashed teal one a block it passes
THROUGH. Today a reader learns this from the SVG `<title>` tooltips (which
every marker carries) or from `docs/internal/web_frontend.md`.

**Why it is not built yet.** No one has asked, and the tooltips already
answer the question a hover at a time — a legend is a convenience for
scanning, not a missing fact.

**The constraint to respect when building it.** The palette is currently
written in THREE places — the inline `<style>` of
`src/web/static/index.html`, `src/web/static/scala/index.html` and
`web/index.html` — kept in sync by hand, which is already one copy too
many (Codex caught the third going stale on #852). A legend that spells
the colours out again makes it four, and the fourth is the one that lies
first, because it is prose rather than the CSS the browser actually
renders. Build it by DERIVING the swatches from the live CSS (read the
class off a hidden probe element, or emit the legend from the same rule
set), so a palette change updates the legend by construction.

## 2. Click-to-focus a bundle in the nuts / dnuts views

**What it is.** Clicking a placed wire focuses its bundle — the same
dimming `[` / `]` already give, reached by pointing at the thing you mean
instead of stepping to it.

**Why it is not built yet.** The keyboard path exists and is adequate on
the designs shipped as demos; the ask is comfort on a large one.

**Where to start — this is genuinely small.** Every piece but the pointer
already exists: `BUNDLE_FOCUS` is the state (null = show all), `#focusbar`
and `#focuslbl` are the display, `stepBundle` is the stepper, and
`bundleAlpha(s.bundle_id)` is applied per drawn element in BOTH views — so
each wire already knows its bundle. What is missing is only the way in:
carry the id onto the SVG element and set `BUNDLE_FOCUS` from a click.
Note `refresh()` resets `BUNDLE_FOCUS` to null on every stage and view
change, deliberately — a new stage starts by showing everything — so a
click-set focus is per-view like the keyboard one, not sticky.

## 3. `check_design` violations as clickable markers

**What it is.** The audit's typed violations (`SEG_OPEN`, `BUSTERM_OPEN`,
`KEEPOUT_CROSS`, `ANTENNA`, `TEG_OPEN`, …) drawn ON the route at the
segment or block they name, instead of read as text in the log pane.

**Why it is medium, not small — the server half does not exist.** The
marker is the easy part. `runCheck()` posts `check_design` and renders its
**log lines**: the design-level audit reaches the browser as TEXT. The
structured `_violation` serializer in `src/web/serialize.py` is wired only
to `serialize_verdict`, i.e. to the interactive EDIT session's verdict, so
there is today no JSON carrying a design-stage violation's `kind`,
`seg_idx` and `block_name` out to a client. Building this means exposing
the audit's typed violations as data first, and drawing them second.

**The rule that makes it worth doing properly.** A violation's identity is
already single-sourced in `verify.cpp` — the marker must READ that, never
re-derive "is this segment open" in JavaScript, or the viewer and
`check_design` will eventually disagree about the same geometry, which is
the failure mode `passthru_blocks` was moved into `buda_session/util.py`
to avoid.

## 4–5. Per-user rc file · user-remappable key-bindings

See [`opens_ux.md`](../opens_ux.md), which carries what each would look
like, the portability cost that deferred it, and the additive-only rule
both must obey.
