"""Guard: BUDA's viewer must drop matplotlib's single-letter default keymaps
that collide with its own shortcuts. Regression for the 'p' bug where pressing
previous-bundle ('p') also toggled matplotlib pan mode (keymap.pan was omitted
from the clear list in viz_window._disable_default_keymaps)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from viz_window import _disable_default_keymaps

# matplotlib's stock single-letter defaults for the keys BUDA shadows. Seeded
# explicitly so the test is hermetic: plt.rcParams is process-global and an
# earlier mid-tier viz test may already have run _disable_default_keymaps()
# (e.g. via a BudaVisualizer), which would otherwise make the sanity checks
# below trivially pass/fail depending on suite order.
_SEED = {
    "keymap.pan": ["p"],
    "keymap.zoom": ["o"],
    "keymap.save": ["s", "ctrl+s"],
    "keymap.fullscreen": ["f", "ctrl+f"],
    "keymap.home": ["h", "r", "home"],
    "keymap.xscale": ["k", "L"],
    "keymap.yscale": ["l"],
    "keymap.grid": ["g"],
}


def test_single_letter_defaults_dropped():
    # rc_context restores rcParams on exit; fresh lists avoid aliasing the
    # global defaults (_disable_default_keymaps mutates the value lists in place).
    with plt.rc_context():
        for key, val in _SEED.items():
            plt.rcParams[key] = list(val)

        assert "p" in plt.rcParams["keymap.pan"]      # sanity: default present
        assert "o" in plt.rcParams["keymap.zoom"]

        _disable_default_keymaps()

        # 'p' (previous bundle/topo) and 'o' must no longer fire matplotlib's
        # pan/zoom modes; the other BUDA-shadowed letters must go too.
        assert "p" not in plt.rcParams["keymap.pan"]
        assert "o" not in plt.rcParams["keymap.zoom"]
        for key in ("keymap.save", "keymap.fullscreen", "keymap.home",
                    "keymap.xscale", "keymap.yscale", "keymap.grid"):
            assert not any(len(v) == 1 for v in plt.rcParams[key]), key
