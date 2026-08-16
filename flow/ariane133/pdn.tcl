# Generate a power grid on the ariane133 floorplan.
#   openroad -exit flow/ariane133/pdn.tcl
# Inputs: fetch.py must have run (tech LEF + SRAM LEF).

read_lef flow/ariane133/NangateOpenCellLibrary.tech.lef
read_lef flow/ariane133/fakeram45_256x16.lef
read_def demo/ariane/ariane.def

# --- global connections (nangate45 platform defaults) ---
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$}   -power
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDPE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDCE$}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$}   -ground
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSSE$}
global_connect
set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}

# --- cut core rows around the macros ---
# The ariane.def floorplan lays rows across the whole die, uncut under the
# 133 SRAMs; pdngen's macro-grid legality check (PDN-0008) then refuses the
# macro grid because the grid halo overlaps a row. Remove rows within 2 um of
# each macro so the M5/M6 macro grids can be built.
cut_rows -halo_width_x 2 -halo_width_y 2

# --- standard-cell grid: M1 followpins, M4 + M7 stripes ---
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {metal7}
add_pdn_stripe  -grid {grid} -layer {metal1} -width {0.17} -pitch {2.4}  -offset {0} -followpins
add_pdn_stripe  -grid {grid} -layer {metal4} -width {0.48} -pitch {56.0} -offset {2}
add_pdn_stripe  -grid {grid} -layer {metal7} -width {1.40} -pitch {30.0} -offset {2}
add_pdn_connect -grid {grid} -layers {metal1 metal4}
add_pdn_connect -grid {grid} -layers {metal4 metal7}

# --- macro grids: M5/M6 over the SRAMs, by orientation class ---
define_pdn_grid -name {macro_r0} -voltage_domains {CORE} -macro \
  -orient {R0 R180 MX MY} -halo {0.1 0.1 0.1 0.1} -default
add_pdn_stripe  -grid {macro_r0} -layer {metal5} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_stripe  -grid {macro_r0} -layer {metal6} -width {0.93} -pitch {10.0} -offset {2}
add_pdn_connect -grid {macro_r0} -layers {metal4 metal5}
add_pdn_connect -grid {macro_r0} -layers {metal5 metal6}
add_pdn_connect -grid {macro_r0} -layers {metal6 metal7}

define_pdn_grid -name {macro_r90} -voltage_domains {CORE} -macro \
  -orient {R90 R270 MXR90 MYR90} -halo {0.1 0.1 0.1 0.1} -default
add_pdn_stripe  -grid {macro_r90} -layer {metal6} -width {0.93} -pitch {40.0} -offset {2}
add_pdn_connect -grid {macro_r90} -layers {metal4 metal6}
add_pdn_connect -grid {macro_r90} -layers {metal6 metal7}

pdngen
write_def flow/ariane133/ariane_pdn.def
