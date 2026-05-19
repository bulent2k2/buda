How does the user set min stub lengths?
====

✦ Users can configure the minimum stub lengths using three new commands in their `.buda` scripts, or directly via the Python API.

  The settings follow a hierarchy: Layer Override > Direction Override > Global Default.

  1. In `.buda` Scripts
  You can add these commands to your script before running `generate_topologies`:

```
   1 # Set a global default for all stubs (e.g., 25 units)
   2 set_min_stub_length 25
   3
   4 # Override specifically for Vertical stubs (H-trunks/spines)
   5 set_min_stub_length_dir V 40
   6
   7 # Override for a specific metal layer by its name (e.g., M5)
   8 set_min_stub_length_layer M5 60
```

  2. Via Python API
  If you are using the `interconnect` module directly in Python, you can configure the `Floorplan` object:

```python
    1 import interconnect
    2
    3 fp = interconnect.Floorplan()
    4
    5 # Global default
    6 fp.set_min_stub_length(25)
    7
    8 # Directional override (0=HORIZONTAL, 1=VERTICAL)
    9 fp.set_min_stub_length_dir(interconnect.LayerDir.VERTICAL, 40)
   10
   11 # Layer-specific override
   12 # (Assuming M5 has layer_id 5)
   13 fp.set_min_stub_length_layer(5, 60)
```

  How it works:
   * Topology Generation: When constructing L, Z, or U shapes, the generator checks these settings. If a candidate's bend would result in a stub shorter than the required minimum (relative to physical block face), that specific candidate is either skipped or its bend is pushed further out to satisfy the constraint.
   * NUTS Placement: During track assignment, the solver uses these same rules to calculate the "slide range" for spines. This prevents the solver from sliding a spine too close to a connected block, ensuring the final track position preserves the requested stub length.



✦ I have successfully implemented, verified, and added exhaustive tests for the hierarchical configurable MIN_STUB_LEN settings.

  Key Enhancements:
   1. Full Topology Coverage: Minimum stub length enforcement is now active across all routing shapes:
       * 2-pin: I, L, Z, U, and UU shapes.
       * Multicast: Standard Trunks (TRUNK_H/TRUNK_V) and Multi-trunk (BITRUNK_H).
       * Best-effort: Minimum Spanning Tree (MST_HV/MST_VH) candidates.
   2. Spread-Z Connectivity Fix: Discovered and resolved a critical bug in add_z_shapes where the segment overlap for spread-Z topologies (forced bends for overlapping block ranges) was calculated with
      the wrong sign when source blocks were to the right of the trunk. This fix ensures that ConnTopology always sees a valid T-junction.
   3. Exhaustive Test Suite:
       * test/tests/test_min_stub_lengths_exhaustive.py: A new Python test that verifies the hierarchy (Layer > Direction > Global) and ensures no candidate for any shape type has a stub shorter than
         the required minimum.
       * buda_system_v2/flow/min_stub_exhaustive.buda: A corresponding regression script for the BUDA CLI.
   4. Advanced API Exposure: Exposed ConnSeg.layer_id in the Python bindings, allowing tests to verify layer-specific routing rules directly.

  Verification Results:
   - Topology Tests: All 11 tests in the suite are passing.
   - Hierarchical Overrides: Verified that setting M5 (vertical) to 80 units correctly pushes bends further out than the global default of 25.
   - Spread-Z: Confirmed that trunks now have bounded slide ranges and correct segment connections.

  The system now robustly prevents "pinched" or "tiny" stubs that could lead to illegal physical layouts after track assignment. Ready for the next task!
