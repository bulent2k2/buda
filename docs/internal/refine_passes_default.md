# `refine_passes` default-on: benefits, costs, and the staged path

The decision study for flipping `set_planner_param refine_passes` from its
current default of 0 (off) to a default of 1. Written when the knob landed
(PR #274, 2026-07-12); companion to
[../congestion_planner.md](../congestion_planner.md) → *"Level ordering"*
(mechanism + the measured A/B table) and the opens.md line item that tracks
this decision.

**TL;DR:** the strictly-better-than-keeping accept rule makes this the
strongest default-on candidate among the planner's opt-in knobs — zero
corpus regressions, neutral designs provably exact no-ops — but the kPeak
lesson applies verbatim: the corpus that justified the knob is not the
corpus a default runs on, and big2/mix haven't voted yet. Measure the
listed gaps first; consider a hier-only default as the low-blast-radius
middle step.

## Benefits

1. **Measured QoR that users currently forfeit silently.** Every hier
   design pays the phantom-reservation tax by default today: hbundles/01
   ships +21% detailed WL, 02 +32%, 05 ships 47 DNUTS opens, 10 ships
   1 overlap / 7 opens — all fixed or drastically improved by
   `refine_passes 1–2`. A default-off QoR knob only helps people who know
   it exists.
2. **An unusually safe accept rule for a default.** Unlike kPeak (which
   regressed big2 at every tested value — see
   [kpeak_measurements.md](kpeak_measurements.md)), the
   strictly-better-than-keeping rule adopted **zero** regressions on the
   hier corpus: every move is a strict improvement in the planner's own
   model against full information (all reservations released, all demand
   real), and neutral designs are provably exact no-ops (0 moves,
   fixpoint early-out). The deep-first failure mode is excluded by
   construction, not by luck.
3. **Less downstream heal work.** Better pre-heal states shrink what
   negotiate_congestion / ripup_reroute must grind through — 05 handing
   ripup 8 opens instead of 47 is a real wall-clock and quality win.
4. **Composes cleanly with what's shipped**: locked bottom-up templates
   and user-pinned wrappers are never revisited, and the pass runs in
   both flat and hier planning.

## Costs and risks

1. **Every affected design's results change at once — the
   golden-discipline cost.** (Correction from the measurement campaign:
   `rnr_mix` is a HIER flow — `run_planner hier` — so even a hier-only
   default re-baselines that one golden; the other snapshot goldens are
   flat.) The flat path refines too, and flat flows CAN change: at refine time the map is
   strictly more congested than at a bundle's original plan time, so
   `keep` can become infeasible (the adopt-any-found arm fires) or a
   strictly-better escape can appear. A both-paths default means a
   deliberate re-baseline of the full golden corpora plus mix/big2
   re-measurement.
2. **An unmeasured interaction with ripup_reroute's trial machinery.**
   Session planner params persist and are applied at planner
   construction, so ripup's FALLBACK full-replan paths (`run_planner …`
   inside a trial; `_rr_replan_hier`) would run refinement **inside every
   trial** — multiplying trial cost and changing what a "trial" means.
   The cheap incremental path (`replan_bundle`) is unaffected, but flows
   like mix that lean on the fallback are exactly the unmeasured ones.
   Either measure this or gate ripup-internal replans from inheriting the
   knob before any default flip (the gate is arguably correct
   regardless).
3. **Runtime.** Each pass costs up to two extra `plan_bundle` calls per
   committed, unlocked bundle (the keep-probe + the unrestricted replan) —
   roughly one additional full planning sweep per pass. The pipeline is
   NUTS/DNUTS-dominated so this is likely minor, but it is unmeasured on
   the big designs (mix2, big2).
4. **Host-dependence at the margin.** The adopt decision is a strict FP
   comparison (`np.score + 1e-9 < keep.score`); a borderline move could
   flip across `-march=native` hosts — the same family of caveats the
   goldens already carry (`-ffp-contract=off`, the golden-host notes),
   with the refinement adding new members. The `stable_sort` revisit
   order removed the *ordering* member of this family (PR #274 review);
   the *score* member is inherent to any score-gated default.
5. **Unmeasured surfaces generally:** the demo corpus, `signal_tracks`
   mode, kPeak + refine composition, bottom-up-marked flows end-to-end,
   and `load_pipeline` checkpoint flows (persist happens after planning,
   so resumes should be safe by construction — but "should be" is not a
   measurement).

## The staged path (if/when the flip is wanted)

1. **Measure first**: the flat + demo corpus and the mix/big2 healed
   endpoints at `refine_passes 1`, plus the ripup-trial interaction
   (gating ripup-internal replans from inheriting the knob if
   disruptive).
2. **Consider hier-only default-on** as the middle step (`run_planner
   hier` defaults to 1 unless the user explicitly set 0): that is where
   all the evidence lives, the snapshot goldens don't churn, and the
   blast radius is the hier test expectations only.
3. Whatever the scope, **the flip is its own PR** containing nothing but
   the default change and the deliberately re-baselined expectations —
   so the diff *is* the measurement record.

## Decision (2026-07-12): HIER-ONLY default-on at 1 pass — SHIPPED

The gating measurements were run the same day; every gap listed above is
now closed with data:

- **Demo corpus (11 flows)**: exact no-ops at `refine_passes 1` — zero
  moves, identical metrics, no measurable runtime cost.
- **Flat corpus**: exact no-ops everywhere EXCEPT `big2_noviz`, which
  regressed exactly as kPeak did there (5 overlaps / 0 opens →
  3 / **60**): strictly-better-in-model moves land a wide trunk in a
  shared DNUTS window — the pre-charge-horizon class no plan-time
  criterion can see. **This kills the both-paths default** and settles
  the flat path at 0.
- **Hier corpus**: all wins or exact no-ops (the PR #274 table), plus the
  rnr family: `mix` pre-heal 15/190 → 12/156, **healed endpoint 1/0 →
  0/0**, WL −1.7%, and the flow's heal loops converge **9× faster**
  (91s → 10s — refinement hands negotiate/ripup a far better start);
  `slowdown_rnr` identical; `mix2` healed 5/39 → 2/40 (a wash: +1 open,
  −3 overlaps); `mix2_fast` a no-op (all wrappers locked).
- **Ripup-trial inheritance**: measured rather than gated — the mix
  numbers above INCLUDE trials inheriting refinement (the fallback
  full-replan paths), and the fidelity argument won: trials should
  re-derive state under the same configuration that produced it. The
  default is applied at every hier planner construction site
  (`run_planner hier`, `_rr_replan_hier`, the bottom-up cell-local
  template planner) for exactly this reason.
- **Runtime**: the refinement itself is noise; on mix it *saves* 80+
  seconds by shrinking the heal loops' workload.

**As shipped**: `run_planner hier` (and the other hier planner sites)
default `refine_passes` to 1 when the user never set it; an explicit
`set_planner_param refine_passes <n>` — including 0 — always wins; the
flat `run_planner` keeps the C++ default 0. The one churned golden
(`rnr_mix`) was re-baselined deliberately and records the improvement
(overlaps 1 → 0). Tests: `test_planner_refine.py`
(`test_hier_defaults_to_one_pass`, `test_explicit_zero_opts_out`,
`test_flat_planner_stays_default_off`).

**Not chosen**: default 2 — pass 2's only corpus win is hbundles/05
(opens 32 → 8), mix at 2 passes is unmeasured, and "one built-in
negotiation iteration" is the conservative default; stress designs can
opt into 2.
