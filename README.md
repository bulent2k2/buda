History
======

python3 setup_buda.py
brew install cmake
cd buda_system/
mkdir build && cd build
cmake .. && make
pip3 install pybind11 pytest matplotlib
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m pip install --upgrade pip
   
# added pybind11 to path for cmake to work in ~/.zshrc:
export PATH=".:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin:/Users/ben/Library/Application Support/Coursier/bin:/Users/ben/bin:$HOME/.cargo/bin:/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages/pybind11/share/cmake/pybind11"
# start a new shell:
zsh 
cmake ..
make -j4
cd ../
cp build/interconnect.cpython-313-darwin.so src/
cd src/
python3 ./buda_cli.py ~/src/buda/design_demo.buda &
python3 ./buda_cli.py ~/src/buda/z_shape.buda &

# Assuming we are in the root folder
cp build/interconnect*.so src/
cd src
python3 buda_cli.py design_demo.buda
