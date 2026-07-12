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
   golden-discipline cost.** For a hier-only default this is smaller than
   it sounds: the snapshot goldens (`rnr_mix`, comprehensive_demo, the
   big2 sub-flow, `tc3a_flat`) are all FLAT flows. But the flat path
   refines too, and flat flows CAN change: at refine time the map is
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
