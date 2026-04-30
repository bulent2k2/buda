* Started using Google's AI (below) and then used Claude Code

* I asked GAI (Gemini): 
I want to build a system to help plan interconnect on a microchip. It has three major components:
1- bundling of nets into buses
2- generating a set of optimal bus topologies
3- layer assignment and track assignment that supports non-uniform width wires/buses

** Here is the complete conversation: 
  https://gemini.google.com/share/293d9e6c10a4

* It said:
This is an exciting challenge. Revitalizing a proven concept with modern algorithms and software architecture is a fantastic way to approach EDA (Electronic Design Automation) tool development. Given your background, you likely remember that 25 years ago, compute constraints dictated simple heuristics. Today, we can leverage graph neural networks (GNNs), massive parallelism, and modern optimization solvers.

Here is a high-level architectural proposal for your Interconnect Planning System.

...


* Some selections:

buda_system/
├── CMakeLists.txt              # The Build Configuration
├── src/
│   ├── bindings.cpp            # The Python Wrapper Code (The Bridge)
│   ├── bundler.h / .cpp        # Engine A
│   ├── topology.h / .cpp       # Engine B
│   ├── layering.h / .cpp       # Engine C
│   ├── global_router.h / .cpp  # Engine 4a
│   ├── nuts.h / .cpp           # Engine 4b
│   ├── buda_cli.py             # CLI Tool
│   └── buda_viz.py             # Visualization
├── tests/
│   ├── features/               # Gherkin .feature files
│   ├── test_bundler.py         # Python BDD tests
│   └── ...
└── extern/
    └── pybind11/               # (Submodule) The binding library
	
Step C: Run the BUDA System Move the compiled library to the source
folder (or add build/ to PYTHONPATH) and run the demo.

* The End
