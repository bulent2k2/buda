# Open items — command UX (aliases, rc files, key-bindings)

What the command-UX surface deliberately does **not** do yet, and why each
gap was left rather than forgotten. The landed pieces are the built-in
`run_dnuts` alias and the user-defined `alias` / `unalias` command
([`../script_reference/setup.md`](../BUDA_SCRIPT_REFERENCE.md) — the
`alias` / `unalias` section), which add a spelling for an existing command
and resolve at the `do_command` choke point before recording (so a flow
using an alias records CANONICAL and replays anywhere).

Snapshot index — last verified against `main`: **2026-08-25**, after the
`alias` / `unalias` command landed (PR #847) with sourced-flow support,
the no-shadow guard, the did-you-mean pool including aliases, and the
`btcl -i` prompt gate consulting a live `buda::aliases`.

**Neither of these is blocking.** Each waits for demand, and the current
behaviour is the conservative one — a flow's meaning is a property of its
text, and nothing here silently makes the same text route differently on
another machine.

### What is left, in the order it is worth doing

| | open | cost | gate |
|---|---|---|---|
| 1 | per-user rc file (interactive-only aliases) | small | a user who wants their shorthand across sessions |
| 2 | user-remappable key-bindings | medium | a second person asking, on a GUI key that matters |

---

## 1. Per-user rc file — interactive-only alias definitions

**What it is.** A `~/.buda/rc.buda` (or similar) auto-sourced at startup so a
user's own aliases (`alias qr run_detailed_nuts`) are available without
redeclaring them in every flow.

**Why it is not built yet.** This is the ONE piece of the alias feature with
a real portability cost, and it is worth stating precisely because the cost
is subtle. The `alias` command itself is portable: resolution happens at the
`do_command` choke point *before* recording, so a flow using an alias records
the canonical command into `BUDA_RECORD` traces and the flattened replay —
those run anywhere with no alias defined (measured: a `qr` flow records
`run_detailed_nuts`, no `alias`/`qr` line). And a missing alias fails LOUD
(`unknown command 'qr'`, did-you-mean pool included), never a silently
different route.

An rc file breaks that ONLY in one direction: a checked-in flow whose *text*
uses `qr` but never defines it would run for the author (their rc defines it)
and fail for everyone else. That is the exact class of environment-dependence
this project has spent effort eradicating (issue #444 — the `healersAhead`
script-text scan that made the same commands route differently depending on
how they were split across `source` files).

**The shape that keeps it safe** (the design to build, when a user asks):

- The rc file is auto-sourced by the **interactive prompt only** (`btcl -i`,
  `design.tcl`, `hdesign.tcl`), **never** by batch `bin/buda flow.buda` or a
  `source`d script. So a user's shorthand works when they type, and a
  checked-in flow's text never depends on their home directory.
- Because the `alias` command already resolves+records canonical, even an
  interactively-defined alias leaves a portable trace — the rc affects what
  the user can TYPE, not what a recording contains.
- Consider a portability advisory: if a flow's parse depends on an
  rc-defined (not in-flow) alias, say so once with the one-line fix ("add
  `alias qr run_detailed_nuts` to the flow to make it self-contained").
  do_command would track alias provenance (rc vs in-flow) to emit it. This
  is optional polish — the loud "unknown command" already prevents the
  silent-divergence failure; the advisory only makes the remedy obvious
  before another machine hits it.

**What it is NOT.** Not a place to redefine real commands (the `alias`
command already forbids shadowing), and not batch-applied. Both of those
would reintroduce the divergence the interactive-only rule removes.

## 2. User-remappable key-bindings

**What it is.** Letting a user rebind the interactive keys — the matplotlib
GUIs' explorer/floorplanner shortcuts (`s` toggle-pin, `a`/`d` step, ↺
re-run, and the rest in [`../KEY_BINDINGS.md`](../KEY_BINDINGS.md)).

**Why it is not built yet — and why it is the lower-priority of the two.**

- **Documentation would lie.** Every guide, doc, and test names the default
  keys in prose (`s` toggles the pin, `a`/`d` step candidates). A user who
  remaps them then reads documentation that describes keys they no longer
  have. The alias feature does not have this problem — a command's canonical
  name still works and the docs still name it; a remapped key has no such
  fallback.
- **The terminal prompt has no key layer at all.** The `btcl -i` /
  `design.tcl` prompt is line-based stdin; there are no keystroke bindings
  there to remap. Key-bindings live only in the two matplotlib GUIs, so the
  surface is narrower than "the tool's key-bindings" suggests.
- **Low demand.** No field report has asked for it (the alias request came
  from a real `run_dnuts` miss). One person mentioning it in passing is not
  the same as a workflow needing it.

**The shape that keeps it safe** (when a second person asks, on a key that
matters):

- **Additive synonyms only** — a config that maps EXTRA keys to existing
  actions, with the defaults never removed. So the documented key always
  works and the user's preferred key also works; no doc goes stale, and no
  test that presses `s` breaks.
- Scoped to the two GUIs (explorer, floorplanner), read at window open, with
  an unknown action name reported rather than silently dropped — the same
  loud-on-misconfiguration rule the rest of the tool follows.
- Full replacement (removing a default, Tcl-`rename`-style) is deliberately
  out: it is the key-binding twin of shadowing a command, and it makes every
  screenshot and guide wrong for that user.

---

**Why one doc for both.** They are the same request ("let me customize the
tool to my habits") arriving as two features, and they share one governing
principle: a customization may ADD a spelling or a key, never CHANGE what an
existing one means, and it must never make a shared artifact (a checked-in
flow, a screenshot in a guide) mean something different for the author than
for everyone else. The alias command already lives by that rule; these two
extend it to the two remaining surfaces.
