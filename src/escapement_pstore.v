/*
 * escapement_pstore.v - the program store (v2, S-A): 1,024 words in four pages of 256,
 * one IHP 1P 256x32 SRAM macro per page (owner's choice, 11-10: today's energy per read
 * and today's clock to data out, for area the 8x4 tile gives).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * The same timing contract as escapement_sram.v, over a 10-bit address {page, word}:
 *   read   `re` and `addr` in cycle N -> `rdata` = mem[addr] from cycle N+1, held until
 *          the next read completes. Only the addressed page's macro is enabled (A_MEN);
 *          the other three make no access. `rpage`, the page of the last read, selects
 *          the macro whose held output is `rdata`.
 *   write  `we` in cycle N stores `wdata` at `addr` on the N->N+1 edge. No read happens,
 *          so `rpage` and every macro's output hold, and `rdata` keeps its value.
 *   reset  no access; `rpage` is 0.
 *
 * PAGES (1 or 4) is the feature switch, set once in project.v. With PAGES = 1 this is one
 * escapement_sram on addr[7:0] (addr[9:8] unused, and 0 from the host and the core): the
 * v1.7 store, cycle for cycle. Instance names (`g_one.u0.sram`, `g_pages.u_p<k>.sram`)
 * are the flattened paths src/config.json names.
 */
`default_nettype none
module escapement_pstore #(
    parameter integer PAGES = 4
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [9:0]  addr,
    input  wire        re,
    input  wire        we,
    input  wire [31:0] wdata,
    output wire [31:0] rdata
);
  generate
    if (PAGES == 1) begin : g_one
      escapement_sram u0 (.clk(clk), .rst_n(rst_n), .addr(addr[7:0]), .re(re), .we(we),
                          .wdata(wdata), .rdata(rdata));
      wire _unused = &{addr[9:8], 1'b0};
    end else begin : g_pages
      wire [3:0]   sel = 4'd1 << addr[9:8];
      wire [127:0] d;
      reg  [1:0]   rpage;
      // the page whose macro read at this edge (escapement_sram reads when re & ~we)
      always @(posedge clk or negedge rst_n)
        if (!rst_n)         rpage <= 2'd0;
        else if (re && !we) rpage <= addr[9:8];
      escapement_sram u_p0 (.clk(clk), .rst_n(rst_n), .addr(addr[7:0]), .re(re & sel[0]),
                            .we(we & sel[0]), .wdata(wdata), .rdata(d[31:0]));
      escapement_sram u_p1 (.clk(clk), .rst_n(rst_n), .addr(addr[7:0]), .re(re & sel[1]),
                            .we(we & sel[1]), .wdata(wdata), .rdata(d[63:32]));
      escapement_sram u_p2 (.clk(clk), .rst_n(rst_n), .addr(addr[7:0]), .re(re & sel[2]),
                            .we(we & sel[2]), .wdata(wdata), .rdata(d[95:64]));
      escapement_sram u_p3 (.clk(clk), .rst_n(rst_n), .addr(addr[7:0]), .re(re & sel[3]),
                            .we(we & sel[3]), .wdata(wdata), .rdata(d[127:96]));
      assign rdata = d[32*rpage +: 32];
    end
  endgenerate
endmodule
`default_nettype wire
