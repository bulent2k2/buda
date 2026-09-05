// Phase-0 top for the LibreLane hierarchical-flow study
// (docs/internal/librelane_hier_flow.md): two hardened reg32 macros chained
// by ONE 32-bit bus, `mid` -- the smallest design with a bus BETWEEN blocks,
// which is what the corridor handoff is measured on.  LibreLane's own
// two-macro vehicle (manual_macro_placement_test) wires each macro only to
// top ports, so it cannot measure that.
//
// Written like that vehicle: no power pins in the RTL; the PDN step connects
// the macros' VPWR/VGND from the top-level grid.
`default_nettype none
module two_reg32 (
    input  wire        clk,
    input  wire        rst,
    input  wire [31:0] d,
    output wire [31:0] q
);
    wire [31:0] mid;
    reg32 u0 (.clk(clk), .rst(rst), .d(d),   .q(mid));
    reg32 u1 (.clk(clk), .rst(rst), .d(mid), .q(q));
endmodule
`default_nettype wire
