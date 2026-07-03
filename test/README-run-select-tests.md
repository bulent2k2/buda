# clean build to prevent stale builds
> bb -c

# run the fast tier
> bb -t

# run one test
> pytest -o addopts="" test/tests/test_flow_scripts.py::test_tc3a_flat_no_perp_range_inversion

# run three tests
> pytest -o addopts="" test/tests/test_planner_signal_tracks.py::test_signal_tracks_reduces_opens_on_mix_repro \
                       test/tests/test_ripup_reroute.py::test_big2_stage_b_clears_opens \
                       test/tests/test_flow_scripts.py::test_tc3a_flat_no_perp_range_inversion
