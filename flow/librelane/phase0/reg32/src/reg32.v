// Phase-0 block for the LibreLane hierarchical-flow study
// (docs/internal/librelane_hier_flow.md).  A 32-bit register with a rotate-XOR
// feedback so synthesis keeps 32 flops AND some logic -- a bare register would
// be a routability toy with no cells behind its pins.  66 signal pins: the
// pin-DEF check (gen_pins_def.py) places every one of them.
//
// No power pins: LibreLane adds VPWR/VGND when it hardens the block (the
// powered netlist), exactly as its `spm` example is written.
`default_nettype none
module reg32 (
    input  wire        clk,
    input  wire        rst,
    input  wire [31:0] d,
    output reg  [31:0] q
);
    always @(posedge clk) begin
        if (rst) q <= 32'd0;
        else     q <= d ^ {q[30:0], q[31]};
    end
endmodule
`default_nettype wire
