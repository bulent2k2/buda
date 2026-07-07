Generator (for historical purposes. This step is not needed for repro)
===
> python3 tools/build_hier_demo.py /tmp/mix.bdb --path flow --cells dnuts1,dnuts2,dogleg1,dogleg2 --instances dnuts2=6,dnuts1=6,dogleg1=4,dogleg2=4 --optimize sa --param time=1m --bloat 10%

copied over mix.bdb which is serialized to mix.bdb.sql
> python3 tools/bdb_serialize.py dump mix.bdb mix.bdb.sql

Repro
===
> buda mix.buda

mix2
==
python3 tools/build_hier_demo.py mix2.bdb --path flow --cells dnuts1,dnuts2,dogleg1,dogleg2 --instances dnuts2=6,dnuts1=6,dogleg1=4,dogleg2=4 --optimize sa --param time=2m --bloat 30%
python3 tools/bdb_serialize.py dump mix2.bdb mix2.bdb.sql
fp mix2.bdb.sql
# Run: Export flow
> buda mix2.buda
