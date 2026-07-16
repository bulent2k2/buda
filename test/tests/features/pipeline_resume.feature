@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in the BDB persist/round-trip tests (test_bdb_*persist*,
# test_bdb_route_snapshot.py) and the load_pipeline resume tests. Design:
# docs/BDB_REFERENCE.md and docs/internal/bdb_test_data.md.
Feature: Pipeline persistence & resume (checkpoint, reopen, continue)
  As a chip planner running a long flow
  I want each pipeline stage persisted into the BDB and rehydrated on reopen,
  So that a fresh session continues from where a previous one stopped.

  # Each stage persists when a BDB is open: bundler -> bundle/bundle_net/bundle_busterm;
  # topology -> topology/topology_segment; planner -> is_selected/assigned_layer;
  # NUTS -> bus_segment/bus_via + route_snapshot; DNUTS -> net_segment/net_via.

  Scenario: open_bdb materializes a diffable .sql fixture to a temp binary
    When I run open_bdb <path.bdb.sql>
    Then a throwaway binary copy is opened read-only (writeback dumps changes back to the .sql on save/exit)

  Scenario: save_bdb writes the working BDB back to its .sql source
    Given a BDB opened with writeback
    When I run save_bdb
    Then the working state is written back to the *.bdb.sql text source

  Scenario: save_bdb <path> takes a one-shot save-as snapshot
    When I run save_bdb checkpoint.bdb.sql
    Then a snapshot of the current state is written (.sql = diffable text, else binary via the backup API), not re-flushed at exit

  Scenario: load_pipeline rehydrates as deep as was persisted
    # bundles + candidate topologies (seg_busterms restored from the links, never
    # re-derived from geometry), the planner's selection + layers + pins, and the
    # abstract-NUTS result.
    Given a BDB checkpointed after run_planner
    When I re-declare the setup and run load_pipeline
    Then the bundles, candidates, and planner decision are restored so the flow continues

  Scenario: load_pipeline expanded restores the hier post-expansion view
    # Bottom-up TEMPLATE wrappers are restored too: a pre-run_nuts checkpoint
    # re-runs the cell-local solve and keeps uniform copies; a post-run_nuts
    # checkpoint keeps the persisted routing.
    Given a hier BDB checkpointed before run_nuts
    When I run load_pipeline expanded
    Then the template wrappers are restored and the cell-local solve re-runs with uniform copies

  Scenario: a route_snapshot fingerprint records the routed output
    Given a routed BDB
    Then run_nuts / run_detailed_nuts persist a route_snapshot content-hash of the routed output (stage-tagged)
