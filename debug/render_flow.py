"""Render a .buda flow's visualize window to a PNG (headless)."""
import sys, os

_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_root, 'build'))
sys.path.insert(0, os.path.join(_root, 'src'))
sys.path.insert(0, os.path.join(_root, 'tools'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

out_path = sys.argv[2] if len(sys.argv) > 2 else '/tmp/buda_render.png'

def mock_show():
    fig = plt.gcf()
    fig.savefig(out_path, bbox_inches='tight', dpi=130)
    print(f"Saved visualization to {out_path}")

plt.show = mock_show

import buda_cli
sys.argv = ['buda_cli.py', sys.argv[1]]
buda_cli.main()
