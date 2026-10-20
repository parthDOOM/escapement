// Lockstep testbench for the line unit (src/esc_line.v): replays lockstep.py vectors and
// compares every output with the ISS's, every cycle. The tape is modelled here as the
// core's synchronous SRAM. W1 loads early during a CONFIG (no debug select shows it).
`timescale 1ns/1ps
`default_nettype none
module tb_line;
  reg clk = 0, rst_n = 1;
  reg [31:0] now; reg [7:0] view, owns;
  reg start, kill, stop;
  reg [2:0] op; reg [1:0] opctx; reg [23:0] opv;
  reg lret, halt, ewe; reg [7:0] eaddr; reg [31:0] edata;
  reg [31:0] x_owned, x_owner, x_conf, x_claimed, x_engaged, x_busy, x_status, x_e, x_mode,
             x_flto, x_fltc, x_port, x_pwe, x_paddr, x_pwdata, x_w0, x_w1, x_w2, x_rel, x_drv, x_dval, x_doe;
  reg [31:0] mem [0:255];
  reg [31:0] rdata;

  wire owned, conf, engaged, busy, flt_owner, flt_config, port, port_we, drv_oe;
  wire [1:0] owner; wire [2:0] mode; wire [7:0] claimed, port_addr, rel_m, drv_m, drv_val;
  wire [31:0] status, e, port_wdata;

  esc_line dut (
    .clk(clk), .rst_n(rst_n), .start(start), .now(now), .view(view), .owns(owns),
    .op_send(op == 3'd1), .op_off(op == 3'd2), .op_recv(op == 3'd3), .op_crc(op == 3'd4),
    .op_cfg(op == 3'd5), .op_ctx(opctx), .op_v(opv), .line_ret(lret), .halt_done(halt),
    .kill(kill), .rdata(rdata),
    .owned(owned), .owner(owner), .conf(conf), .claimed(claimed), .engaged(engaged),
    .busy(busy), .status(status), .e(e), .mode(mode), .flt_owner(flt_owner),
    .flt_config(flt_config), .port(port), .port_we(port_we), .port_addr(port_addr),
    .port_wdata(port_wdata), .rel_m(rel_m), .drv_m(drv_m), .drv_val(drv_val), .drv_oe(drv_oe));

  integer fd, n, line_no, errors;
  reg [8*512-1:0] vecfile, tapefile;
  wire commit_rd = (op == 3'd5) | ((op == 3'd4) && opv[15:8] != 8'd0)
                 | ((op == 3'd1) && opv[14:8] != 7'd0);

  task check(input [31:0] got, input [31:0] want, input [8*10-1:0] name);
    if (got !== want) begin
      errors = errors + 1;
      if (errors <= 20)
        $display("line %0d t=%0d: %0s got %h want %h", line_no, now, name, got, want);
    end
  endtask

  initial begin
    if (!$value$plusargs("vec=%s", vecfile)) vecfile = "vec.txt";
    if (!$value$plusargs("tape=%s", tapefile)) tapefile = "tape.hex";
    $readmemh(tapefile, mem);
    fd = $fopen(vecfile, "r");
    errors = 0; line_no = 0;
    start = 0; kill = 0; stop = 0; op = 0; opctx = 0; opv = 0; lret = 0; halt = 0;
    now = 0; view = 8'hFF; owns = 0; rdata = 0;
    #1 rst_n = 0;
    #4 rst_n = 1;
    while (!$feof(fd)) begin
      n = $fscanf(fd, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h\n",
                  now, view, owns, start, kill, stop, op, opctx, opv, lret, halt, ewe, eaddr,
                  edata, x_owned, x_owner, x_conf, x_claimed, x_engaged, x_busy, x_status,
                  x_e, x_mode, x_flto, x_fltc, x_port, x_pwe, x_paddr, x_pwdata, x_w0, x_w1, x_w2, x_rel,
                  x_drv, x_dval, x_doe);
      if (n == 36) begin
        line_no = line_no + 1;
        #1;
        check(owned, x_owned, "owned");
        if (x_owned) check(owner, x_owner, "owner");
        check(conf, x_conf, "conf");
        check(claimed, x_claimed, "claimed");
        if (x_conf) begin
          check(dut.w0, {x_w0[31:24], x_w0[20:0]}, "w0"); check(dut.w2, x_w2[24:0], "w2");
          if (x_mode != 1) check(dut.w1, x_w1, "w1");   // W1 loads early (no debug select)
        end
        check(engaged, x_engaged, "engaged");
        check(busy, x_busy, "busy");
        check(status, x_status, "status");
        check(e, x_e, "e");
        check(mode, x_mode, "mode");
        check(flt_owner, x_flto, "flt_owner");
        check(flt_config, x_fltc, "flt_config");
        check(port, x_port, "port");
        if (x_port) begin
          check(port_we, x_pwe, "port_we");
          check(port_addr, x_paddr, "port_addr");
          if (x_pwe) check(port_wdata, x_pwdata, "pwdata");
        end
        if (!start && !stop) begin
          check(rel_m, x_rel, "rel_m");
          check(drv_m, x_drv, "drv_m");
          if (x_drv != 0) begin
            check(drv_val & drv_m, x_dval, "drv_val");
            check(drv_oe, x_doe, "drv_oe");
          end
        end
        // the edge E_t
        #4 clk = 1;
        if (port && !port_we && !stop) rdata <= mem[port_addr];
        else if (commit_rd && !kill && !start) rdata <= mem[opv[7:0]];
        if (port && port_we && !stop && !start) mem[port_addr] <= port_wdata;
        if (ewe) mem[eaddr] <= edata;
        #5 clk = 0;
      end
    end
    $display("%0d edges, %0d errors", line_no, errors);
    $finish;
  end
endmodule
`default_nettype wire
