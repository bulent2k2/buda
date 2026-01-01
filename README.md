# Assuming we are in the root folder
cp build/interconnect*.so src/
cd src
python3 buda_cli.py design_demo.buda
