What is a seg_perp in nuts.h/cpp?
====

• I’ll look at nuts.h and the relevant construction/use sites in nuts.cpp so I can explain it from the actual data flow rather than
  guessing from the name.

• Explored
  └ Read nuts.h
    Search seg_perp|perp in nuts.h
    Read nuts.cpp

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• I found seg_perp is not defined in nuts.h; NUTS consumes it from BundleWrapper. I’m checking that struct now to tie the meaning back
  to planner output.

• Explored
  └ Search seg_perp|struct BundleWrapper|class BundleWrapper in src
    Read congestion_planner.h
    Search seg_perp in congestion_planner.cpp

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• I have the relevant pieces now: planner writes BundleWrapper::seg_perp; NUTS reads it into pull_map as a preferred placement, subject
  to connectivity overrides.

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• seg_perp is a per-segment preferred perpendicular coordinate produced by the congestion planner and consumed by NUTS.

  For a selected topology segment:

  - If the segment is horizontal, its perpendicular coordinate is y.
  - If the segment is vertical, its perpendicular coordinate is x.

  So seg_perp[si] is “put segment si near this y/x band center.”

  Where it lives:

  - Declared on BundleWrapper in src/congestion_planner.h:47
  - Filled by the congestion planner as the center of the Hanan band it charged capacity to
  - Read in NUTS in src/nuts.cpp:104

  Why it exists: the planner may reserve capacity in a specific slide-aware Hanan band. Without passing that band center into NUTS,
  NUTS might prefer the topology’s raw nominal center and pack buses into a different band, leaving the planner’s charged band empty
  and creating mismatches between planning and placement.

  Important nuance: seg_perp is only a preference. NUTS uses it only when there is no stronger connectivity/face semantic:

  else if (n_bt == 0 &&
           si < (int)bw.seg_perp.size() &&
           bw.seg_perp[si] != INT_MIN) {
      pull_map[key] = static_cast<double>(bw.seg_perp[si]);
  }

  So block-terminal stubs and net_pull-driven segments keep their connection-driven placement behavior. INT_MIN means “no planner
  perpendicular preference for this segment.”

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  gpt-5.5 medium · ~/src/git/buda/codex
