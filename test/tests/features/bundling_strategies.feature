@landed
# Narrative spec (not pytest-bdd bound — see features/README.md). Executable
# coverage lives in test_bundler.py, test_hier_bundler.py, and the fan-in taper /
# fidelity tests. Engine design: docs/internal/convergent_bundling.md and
# docs/bundling_strategies.md. The classic strict/convergent cases are also in the
# bound bundler_logic.feature. This file specs the full strategy lattice + bounds.
Feature: Net-bundling strategies (the STRICT..COMBINED lattice + bounds)
  As a chip planner
  I want to choose how nets are grouped into buses,
  So that related signals route together without over- or under-bundling.

  # The strategy lattice: STRICT subset {CONVERGENT, BIDIRECTIONAL} subset COMBINED.
  # STRICT = driver + sorted receivers. CONVERGENT = shared receivers (fan-in).
  # BIDIRECTIONAL = direction-agnostic endpoint set. COMBINED = union-find merging
  # nets connected by a CHAIN of either relation (the one genuinely new lattice point).

  Scenario: STRICT groups only exact driver+receiver matches
    Given nets that share a driver and receivers exactly
    When I run run_bundler STRICT
    Then only exact-signature nets bundle together

  Scenario: CONVERGENT groups nets by shared receiver (fan-in)
    Given nets with different drivers but a shared sink instance
    When I run run_bundler CONVERGENT
    Then they form one bundle with reason "REC:<sink>"

  Scenario: BIDIRECTIONAL groups direction-agnostically
    # A->B bundles with B->A, and cyclic a->b,c / b->c,a / c->b,a group together.
    Given nets A->B and B->A and a cyclic group
    When I run run_bundler BIDIRECTIONAL
    Then one bundle mixes the bidirectional pairs and one-way nets over the same block set

  Scenario: COMBINED merges via a chain of either relation
    Given nets connected transitively by convergent OR bidirectional relations
    When I run run_bundler COMBINED
    Then union-find merges the whole chain into one bundle

  Scenario: set_bundling restricts a strategy per net prefix (longest prefix wins)
    # A merge happens only when the strategy enables it AND both nets permit it.
    Given clock nets under prefix clk_
    When I set_bundling clk_ strict and run run_bundler COMBINED
    Then clk_ nets stay out of every convergent/bidirectional merge while other nets still combine

  Scenario: a CONVERGENT multi-driver bundle routes as a per-bit tapered fan-in tree
    # Generation roots the shape at the shared sink with every driver as a leaf, so
    # every driver block is physically attached. Topology::seg_bits scopes each
    # segment to its own member bits (planner charging / NUTS widths / DNUTS emission).
    Given a CONVERGENT bundle spanning multiple driver blocks
    When I generate topologies
    Then the shape is a fan-in tree and each segment carries only its member bits (per-bit taper)

  Scenario: check_design flags a dropped net driver (NET_DRIVER_OPEN)
    # Every net endpoint block of a bundle must appear in the topology's
    # connected_block_names contract; a bit with no derivable driver->sink path is flagged.
    Given a topology whose block contract drops an endpoint
    When I run check_design
    Then a NET_DRIVER_OPEN violation is reported

  Scenario: set_max_bundle_bits splits an over-limit bundle into balanced parts
    # 600 bits at limit 512 -> 300+300 (never 512+88); bits of one bus kept together.
    Given a bundle of 600 bits
    When I set_max_bundle_bits 512 and run run_bundler
    Then the bundle splits into balanced parts, reported LOUD with the binding constraint

  Scenario: set_max_bundle_bits auto derives the cap from the shortest busterm edge
    # Per endpoint block, incident bits must fit floor(min(w,h)/min_bit_pitch); the
    # partitioner enforces every block cap PER PART. Static + auto combine (max part count).
    Given a bundle whose incident bits exceed a block face capacity
    When I set_max_bundle_bits auto and run run_bundler
    Then each part fits every block cap and the split reason carries |SPLIT:k/n

  Scenario: the hier bundler accepts every strategy per bundling depth
    Given an open BDB hier design
    When I run run_hier_bundler CONVERGENT
    Then same-level multi-driver groups become FANIN:root|FROM:leaves fan-in bundles while cross-level nets keep STRICT/BIDIRECTIONAL grouping
