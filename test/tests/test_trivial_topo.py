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

import buda
import pytest

def test_no_pinched_z_shape():
    fp = buda.Floorplan()
    # Two blocks touching at y=600
    # u0: x=[200, 300], y=[400, 600]
    # u1: x=[550, 600], y=[600, 1000]
    fp.add_block("u0", 200, 400, 300, 600)
    fp.add_block("u1", 550, 600, 600, 1000)
    fp.set_global_corner_margin(15, 20)

    gen = buda.TopologyGenerator(fp)
    candidates = gen.generate_candidates("u0", "u1")
    
    # All candidates must have non-zero slide ranges for all segments.
    # The robust filter_pinched already ensures this.
    # Specifically, Z_VHV@y600 is now allowed because its slide is [580, 620].
    for cand in candidates:
        ct = buda.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            assert cs.perp_lo != cs.perp_hi, f"Found pinched segment in {cand.type}: slide=[{cs.perp_lo}, {cs.perp_hi}]"

def test_non_pinched_z_shape_allowed():
    fp = buda.Floorplan()
    # Two blocks with a gap in Y
    fp.add_block("u0", 200, 400, 300, 600)
    fp.add_block("u1", 550, 700, 600, 1000)
    # Add a block to create a Hanan point at y=650
    fp.add_block("u_mid", 0, 650, 10, 651)
    fp.set_global_corner_margin(15, 20)

    gen = buda.TopologyGenerator(fp)
    candidates = gen.generate_candidates("u0", "u1")
    
    found_z = False
    for cand in candidates:
        if "Z_VHV@y650" in cand.type:
            found_z = True
            break
    assert found_z, "Should still generate non-pinched Z_VHV at y=650"

def test_no_pinched_u_shape():
    fp = buda.Floorplan()
    # u0 top edge at 600
    fp.add_block("u0", 200, 400, 300, 600)
    # u1 top edge also at 600
    fp.add_block("u1", -150, 500, -100, 600)
    fp.set_global_corner_margin(15, 20)
    
    gen = buda.TopologyGenerator(fp)
    candidates = gen.generate_candidates("u0", "u1")
    
    for cand in candidates:
        # Check all segments of all candidates for 0-width slide
        ct = buda.ConnTopology()
        ct.build(cand, fp)
        for cs in ct.segs():
            assert cs.perp_lo != cs.perp_hi, f"Found pinched segment in {cand.type}: slide=[{cs.perp_lo}, {cs.perp_hi}]"

if __name__ == "__main__":
    test_no_pinched_z_shape()
    test_non_pinched_z_shape_allowed()
    test_no_pinched_u_shape()
    print("Manual tests passed!")
