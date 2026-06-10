import sys
import os

# Add src and tools to python path
sys.path.insert(0, '/Users/ben/src/git/buda/agy/src')
sys.path.insert(0, '/Users/ben/src/git/buda/agy/tools')

# Import matplotlib and set backend to Agg before any other matplotlib imports
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Patch plt.show to save the current figure to a file
def mock_show():
    # Save the current active figure
    fig = plt.gcf()
    # Save as png in scratch directory
    out_path = '/Users/ben/.gemini/antigravity-cli/brain/4f65547d-28f7-4217-b2e8-b6f503189082/scratch/01_pipeline_hier.png'
    fig.savefig(out_path, bbox_inches='tight', dpi=150)
    print(f"Saved visualization to {out_path}")

plt.show = mock_show

# Now import buda_cli and run it
import buda_cli

if __name__ == '__main__':
    # Setup argv for buda_cli
    sys.argv = ['buda_cli.py', '/Users/ben/src/git/buda/agy/flow/hbundles/01_pipeline_hier.buda']
    # Execute the CLI main function
    buda_cli.main()
