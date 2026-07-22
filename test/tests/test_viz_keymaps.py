"""Guard: BUDA's viewer must drop matplotlib's single-letter default keymaps
that collide with its own shortcuts. Regression for the 'p' bug where pressing
previous-bundle ('p') also toggled matplotlib pan mode (keymap.pan was omitted
from the clear list in viz_window._disable_default_keymaps)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from viz_window import _disable_default_keymaps


def test_single_letter_defaults_dropped():
    # matplotlib ships these single-letter interactive-mode toggles by default;
    # BUDA binds several of the same letters and drives pan/zoom itself.
    assert "p" in plt.rcParams["keymap.pan"]      # sanity: default is present
    assert "o" in plt.rcParams["keymap.zoom"]

    _disable_default_keymaps()

    # 'p' (previous bundle/topo) and 'o' must no longer fire matplotlib's
    # pan/zoom modes; the other BUDA-shadowed letters must go too.
    assert "p" not in plt.rcParams["keymap.pan"]
    assert "o" not in plt.rcParams["keymap.zoom"]
    for key in ("keymap.save", "keymap.fullscreen", "keymap.home",
                "keymap.xscale", "keymap.yscale", "keymap.grid"):
        assert not any(len(v) == 1 for v in plt.rcParams[key]), key
