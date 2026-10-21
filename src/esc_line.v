/*
 * esc_line.v - the line unit (docs/11-SEMANTICS.md v1.4.0 section 6.6, D-33..D-37).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * One line unit, owned by the first context whose LINE CONFIG commits. It moves packets
 * between the tape and the owner's pins with NRZI, bit stuffing, a sync prefix, a CRC of
 * any width up to 16 and an end-of-packet, and recovers the clock on receive. esc_core
 * decodes SEND, LINE and GETL, routes the unit's tape accesses and pin writes, and turns
 * a failed CONFIG into a fault of the owner.
 *
 * Every input is its value during cycle t; registers change at edge E_t. The core
 * supplies the owner's logical input view and ownership, the COMPLETE commits that act
 * on the unit, the owner's LINE retirements, HALT completions and faults, and the tape
 * read data.
 *
 * Area: one 12-bit tick counter `el` serves every mode (bit timer in TX and RX, idle
 * counter in ARM, CRC bit index, CONFIG step, end-of-packet wait); a bit lasts P_int or
 * P_int + 1 ticks, so the fractional accumulator is one 8-bit adder; the claimed pins
 * are decoded from the configuration, not stored; a new W0 waits in the buffer until
 * the CONFIG completes, so the old claimed pins are still known then; the CRC width is
 * never computed (the start mask is a prefix OR of the polynomial, and the CRC field
 * ends at its lowest set bit). Power: configuration, command and end tick are
 * clock-gated banks, and every other register shares one gate that is closed while the
 * unit is idle (D-29).
 */
`default_nettype none

// as esc_greg: plain enable flip-flops for the logic proofs and with ESC_NO_CG
`ifdef FORMAL
`ifndef ESC_CG_FORMAL
`define ESC_LINE_PLAIN
`endif
`endif
`ifdef ESC_NO_CG
`define ESC_LINE_PLAIN
`endif

module esc_line (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,          // start at this edge: reset (section 9)
    input  wire [31:0] now,
    input  wire [7:0]  view,           // the owner's logical inputs (sync) during this cycle
    input  wire [7:0]  owns,           // the owner's logical pins (section 5)
    // a COMPLETE commit acting on the unit at this edge; at most one is set
    input  wire        op_send,
    input  wire        op_off,
    input  wire        op_recv,
    input  wire        op_crc,
    input  wire        op_cfg,
    input  wire [1:0]  op_ctx,         // the committing context (a CONFIG may take ownership)
    input  wire [23:0] op_v,           // R[rs][23:0]
    input  wire        line_ret,       // the owner's LINE entry retires at this edge
    input  wire        halt_done,      // the owner's HALT completes at this edge
    input  wire        kill,           // the owner faults, or stop, at this edge: as OFF
    input  wire [31:0] rdata,          // tape data of the unit's read in the previous cycle
    // state during this cycle
    output reg         owned,
    output reg  [1:0]  owner,
    output reg         conf,
    output wire [7:0]  claimed,        // logical pins of the owner the unit claims
    output wire        engaged,
    output wire        busy,
    output wire [31:0] status,
    output wire [31:0] e,
    output reg  [2:0]  mode,
    output wire        flt_owner,      // a CONFIG completing at this edge faults ADMIT/OWNER
    output wire        flt_config,     //   ... or ADMIT/CONFIG
    // a scheduled tape access in this cycle (the commit-cycle reads of SEND, CRC and
    // CONFIG use the committing instruction's address, R[rs][7:0])
    output wire        port,
    output wire        port_we,
    output wire [7:0]  port_addr,
    output wire [31:0] port_wdata,
    // pin writes at this edge, after DRIVE retirement (logical pins of the owner)
    output reg  [7:0]  rel_m,          // oe <= 0
    output reg  [7:0]  drv_m,          // val <= drv_val, oe <= drv_oe
    output reg  [7:0]  drv_val,
    output reg         drv_oe
);
  localparam [2:0] IDLE = 3'd0, CFG = 3'd1, PEND = 3'd2, TX = 3'd3, ARM = 3'd4, RX = 3'd5,
                   CRCM = 3'd6;
  localparam [2:0] OK = 3'd0, VIOL = 3'd1, SE0 = 3'd2, STUFF = 3'd3, FULL = 3'd4,
                   NOSYNC = 3'd5, MISMATCH = 3'd6;

  // ------------------------------------------------------------------ state
  wire [28:0] w0;                          // configuration (gated banks): W0[31:24, 20:0],
  wire [31:0] w1;                          //   W1, W2[24:0] (the bits section 6.6 defines)
  wire [24:0] w2;
  wire [23:0] cmd;                         // SEND command, RECV/CRC/CONFIG operand
  reg  [31:0] buf_q;                       // stream word
  reg         ld;                          // the unit read the tape in the previous cycle
  reg  [15:0] crc;
  reg  [2:0]  job, cause;
  reg  [8:0]  count;
  reg  [11:0] el;                          // tick counter (see header)
  reg  [8:0]  acc;                         // alpha + P_frac of the current bit; [8] = carry
  reg         ext;                         // the current bit is P_int + 1 long: one more tick
  reg  [3:0]  rn;                          // stuffing run; idle bit times in ARM
  reg         rb;                          // the run's bit (any = 1)
  reg  [8:0]  kc;                          // TX: monitored bits; RX: ticks since t0 (to 255)
  reg  [6:0]  pos;                         // TX: bit within the segment
  reg  [2:0]  seg;                         // TX: 0 prefix, 1 header, 2 data, 3 CRC, 4 EOP
  reg         lv;                          // TX: line level L; RX: NRZI reference
  reg         xp;                          // RX: x during the previous cycle
  reg         req;                         // TX read or RX write waiting for an owner cycle
  reg         fin;                         // RX: ended; a last word waits for its write
  reg         eopw;                        // RX: waiting for the end of SE0
  reg         hunt, dec, stor;             // RX: in sync hunt; decoded a bit; storing
  reg  [1:0]  noted;                       // RX: 0 OK, 1 STUFF, 2 FULL
  reg         mon, abt;                    // TX: monitor armed for this bit; mismatch seen

  // ------------------------------------------------------------------ configuration
  wire [2:0]  c_d = w0[2:0], c_n = w0[5:3], c_r = w0[8:6];
  wire        c_diff = w0[9], c_nrzi = w0[10], c_lsb = w0[11];
  wire [2:0]  c_stf = w0[14:12];                        // stuffing run length N
  wire        c_any = w0[15];
  wire [1:0]  c_eop = w0[17:16];
  wire        c_idrv = w0[18], c_skip = w0[19], c_txp = w0[20];
  wire [7:0]  c_pre = w0[28:21];
  wire [11:0] c_P = w1[11:0];
  wire [7:0]  c_F = w1[19:12];
  wire [11:0] c_S = w1[31:20];
  wire [15:0] c_poly = w2[15:0];
  wire        c_init = w2[16], c_inv = w2[17], c_hdr = w2[18], c_fall = w2[19],
              c_abort = w2[20];
  wire [3:0]  c_imin = w2[24:21];

  function [7:0] dec8(input [2:0] i);
    dec8 = 8'd1 << i;
  endfunction
  // M = -(poly & -poly) mod 2^16: bit i is set iff poly has a set bit at or below i
  function [15:0] pfx(input [15:0] p);
    integer i;
    begin
      pfx[0] = p[0];
      for (i = 1; i < 16; i = i + 1) pfx[i] = pfx[i-1] | p[i];
    end
  endfunction
  function [15:0] rev16(input [15:0] x);
    integer i;
    for (i = 0; i < 16; i = i + 1) rev16[i] = x[15-i];
  endfunction
  function [7:0] pattern(input [2:0] d, input [2:0] n, input diff, input l);
    pattern = (l ? dec8(d) : 8'd0) | ((diff & ~l) ? dec8(n) : 8'd0);
  endfunction

  wire [15:0] m_c = pfx(c_poly);
  wire [15:0] lsbm = m_c & ~{m_c[14:0], 1'b0};          // the polynomial's lowest set bit
  wire [15:0] crc_start = c_init ? m_c : 16'd0;
  // the CRC in transmission order (CRC wire flag): bit i = bit 15 - i, below the width
  // (functions take every input as an argument: a simulator re-evaluates a continuous
  // assignment only when an argument changes)
  function [15:0] xf(input [15:0] c, input inv, input [15:0] m);
    xf = rev16(inv ? ~c : c) & rev16(m);
  endfunction
  function [15:0] crc_nx(input [15:0] c, input b, input [15:0] poly);
    crc_nx = {c[14:0], 1'b0} ^ ((c[15] ^ b) ? poly : 16'd0);
  endfunction

  assign claimed = (conf & c_txp) ? (dec8(c_d) | (c_diff ? dec8(c_n) : 8'd0)) : 8'd0;
  assign engaged = (mode == CFG) | (mode == PEND) | (mode == TX) | (mode == RX)
                 | (mode == CRCM);
  assign busy    = (mode != IDLE);
  assign status  = {busy, job, cause, count, crc};

  wire own_cyc = owned & (now[1:0] == owner - 2'd1);     // the owner's commit cycles
  wire x   = c_diff ? view[c_d] : view[c_r];             // line level
  wire se0 = c_diff & ~view[c_d] & ~view[c_n];
  wire anyop = op_send | op_off | op_recv | op_crc | op_cfg;
  wire live  = ~start & ~kill & ~anyop;                  // mode handlers run at this edge

  wire [11:0] el_inc = el + 12'd1;
  wire tb_hit = ((el == c_P) & ~acc[8]) | ext;           // the next timed boundary is now
  wire [8:0]  acc_go = {1'b0, c_F};                      // a bit starting with alpha = 0
  wire [8:0]  acc_nx = {1'b0, acc[7:0]} + {1'b0, c_F};   // ... after a timed boundary

  // stuffing (shared by TX and RX)
  wire due = (c_stf != 3'd0) & (rn >= {1'b0, c_stf});
  wire sb  = c_any & ~rb;                                // the stuff bit
  function [4:0] push(input [3:0] r, input bit_r, input b, input any);   // {run, bit}
    if (any) push = (r != 4'd0 && b == bit_r) ? {r + 4'd1, bit_r} : {4'd1, b};
    else       push = b ? {r + 4'd1, 1'b0} : {4'd0, 1'b0};
  endfunction

  // stream bit of the buffer (TX data, CRC job); a word read in the previous cycle is
  // used straight from the tape, and then always from its bit 0 in stream order
  wire [4:0] sidx = ((mode == CRCM) ? el[4:0] : pos[4:0]) ^ {5{~c_lsb}};
  wire sbit = ld ? (c_lsb ? rdata[0] : rdata[31]) : buf_q[sidx];

  // ------------------------------------------------------------------ CONFIG
  wire        cfg_on   = (mode == CFG);
  wire        cfg_rd   = cfg_on & ((el == 12'd3) | (el == 12'd7));   // cycles c+4, c+8
  wire        cfg_done = cfg_on & (el == 12'd8);                     // E_(c+9)
  wire [2:0]  n_d = buf_q[2:0], n_n = buf_q[5:3];
  wire        n_diff = buf_q[9], n_tx = buf_q[20];
  wire [7:0]  n_cl = n_tx ? (dec8(n_d) | (n_diff ? dec8(n_n) : 8'd0)) : 8'd0;
  wire        n_valid = (w1[11:0] >= 12'd8) & (w1[31:20] >= 12'd4) & (w1[31:20] < w1[11:0]);
  assign flt_owner  = cfg_done & n_tx & (~owns[n_d] | (n_diff & (~owns[n_n] | (n_d == n_n))));
  assign flt_config = cfg_done & ~flt_owner & ~n_valid;
  wire [15:0] n_m = pfx(rdata[15:0]);

  // ------------------------------------------------------------------ CRC job
  wire        crc_on   = (mode == CRCM);
  wire [7:0]  nbits    = cmd[15:8];
  wire        crc_last = crc_on & (el_inc[7:0] == nbits);
  wire        crc_rd   = crc_on & (el[4:0] == 5'd31) & (el_inc[7:0] < nbits);
  wire [15:0] crc_b    = crc_nx(crc, sbit, c_poly);
  wire [15:0] crc_w    = xf(crc_on ? crc_b : crc_start, c_inv, m_c);   // wire-flag result

  // ------------------------------------------------------------------ transmit
  wire        tx_start = (mode == PEND) & line_ret;
  wire        tx_bnd   = tx_start | ((mode == TX) & tb_hit);
  wire [15:0] crc_in   = tx_start ? crc_start : crc;
  wire [6:0]  nb       = cmd[14:8];
  wire [5:0]  sen = {1'b1, c_diff & (c_eop != 2'd0), cmd[15] & m_c[15], nb != 7'd0, c_hdr,
                     c_pre != 8'd0};
  reg  [2:0]  eseg;                                      // first enabled segment >= seg
  integer si;
  always @(*) begin
    eseg = 3'd5;
    for (si = 4; si >= 0; si = si - 1)
      if (si[2:0] >= seg && sen[si]) eseg = si[2:0];
  end
  wire [7:0]  fbyte  = (eseg == 3'd0) ? c_pre : cmd[23:16];
  wire        b_byte = fbyte[pos[2:0] ^ {3{~c_lsb}}];
  wire [15:0] crcx   = c_inv ? ~crc_in : crc_in;
  wire        b_crc  = crcx[~pos[3:0]];
  wire        pbit   = (eseg[2:1] == 2'b00) ? b_byte : (eseg == 3'd2) ? sbit : b_crc;
  wire        s_last = (eseg[2:1] == 2'b00) ? (pos[2:0] == 3'd7)
                     : (eseg == 3'd2) ? (pos + 7'd1 == nb)
                     : (eseg == 3'd3) ? lsbm[~pos[3:0]]
                     : (pos[1:0] == c_eop);
  wire        t_line = ~abt & (due | (eseg <= 3'd3));      // a line bit at this boundary
  wire        t_end  = abt | (~due & (eseg == 3'd5));
  wire        t_pkt  = ~due & (eseg <= 3'd3);                // ... and it is a packet bit
  wire        lb     = due ? sb : pbit;
  wire        lv_nx  = c_nrzi ? (lb ? lv : ~lv) : lb;
  wire        tx_mon = (mode == TX) & mon & ~tb_hit & (el == c_S);
  wire        tx_rd  = (mode == TX) & req & own_cyc;

  // ------------------------------------------------------------------ receive
  wire        arm_on = (mode == ARM);
  wire        idle_t = x & ~se0;
  wire        idle_ok = (c_imin == 4'd0) ? ((rn != 4'd0) | (el != 12'd1)) : (rn >= c_imin);
  wire        arm_go = arm_on & ~x & idle_ok;
  wire        rx_on  = (mode == RX);
  wire        rx_wr  = rx_on & req & own_cyc;
  wire        rx_go  = rx_on & ~fin;
  wire        rs     = (x != xp) & (~c_fall | ~x);             // a resync boundary
  wire        rx_smp = rx_go & ~eopw & ~(rs | tb_hit) & (el == c_S);
  wire        db     = c_nrzi ? (x == lv) : x;                 // decoded bit
  wire        s_dec  = rx_smp & ~se0 & stor;                   // steps 2.. run
  wire        s_viol = s_dec & due & (db != sb);
  wire        s_push = s_dec & ~s_viol;
  wire        s_store = s_dec & ~due & ~hunt;
  wire [8:0]  cnt1   = count + 9'd1;
  wire [4:0]  lim    = cmd[12:8];
  wire        s_full = s_store & (cnt1 == {lim == 5'd0, lim, 3'b000});
  wire        e_eop  = rx_go & eopw & (~se0 | (el == 12'd255));
  wire        f_viol = s_viol & ~c_diff;
  wire        f_full = s_full & ~c_diff;
  wire [2:0]  eop_cause = (hunt & ~dec) ? SE0 : hunt ? NOSYNC
                        : (noted == 2'd1) ? STUFF : (noted == 2'd2) ? FULL : OK;
  wire [8:0]  eop_count = (hunt & ~dec) ? {1'b0, kc[7:0]} : hunt ? 9'd0 : count;
  wire        rx_fin = f_viol | f_full | e_eop;
  wire        rx_lastw = f_full | ((f_viol | (e_eop & ~hunt)) & (count[4:0] != 5'd0));
  // the word of the last stored bit, (count - 1) / 32 (count is 1..256 when it is used)
  wire [2:0]  rx_wi = count[7:5] - {2'd0, count[4:0] == 5'd0};

  // ------------------------------------------------------------------ tape port
  assign port       = cfg_rd | crc_rd | tx_rd | rx_wr;
  assign port_we    = rx_wr;
  wire [2:0] off    = cfg_on ? {1'b0, el[2], ~el[2]} : crc_on ? el_inc[7:5]
                    : (mode == TX) ? {1'b0, pos[6:5]} : rx_wi;
  assign port_addr  = cmd[7:0] + {5'd0, off};
  assign port_wdata = buf_q;
  wire rd_now = cfg_rd | crc_rd | tx_rd;
  wire rd_commit = op_cfg | (op_crc & (op_v[15:8] != 8'd0)) | (op_send & (op_v[14:8] != 7'd0));

  // ------------------------------------------------------------------ banks
  wire tx_endev = live & tx_bnd & t_end;
  wire e_ev = (live & (cfg_done | crc_last | tx_endev | rx_fin))
            | (~start & ~kill & op_crc & (op_v[15:8] == 8'd0));
  esc_greg #(32) g_e (.clk(clk), .rst_n(rst_n), .en(start | e_ev),
                      .d(start ? 32'd0 : now), .q(e));
  // W0 is a latch bank (D-40): the buffer holds still at the CONFIG's last edge, so it is
  // its own dq
  esc_lreg #(29) g_w0 (.clk(clk), .rst_n(rst_n), .en(cfg_done), .d({buf_q[31:24], buf_q[20:0]}),
                       .dq({buf_q[31:24], buf_q[20:0]}), .q(w0));
  esc_greg #(32) g_w1 (.clk(clk), .rst_n(rst_n), .en(cfg_on & ld & (el == 12'd4)),
                       .d(rdata), .q(w1));
  esc_greg #(25) g_w2 (.clk(clk), .rst_n(rst_n), .en(cfg_done), .d(rdata[24:0]), .q(w2));
  esc_greg #(24) g_cmd (.clk(clk), .rst_n(rst_n),
                        .en(op_send | op_recv | op_crc | op_cfg), .d(op_v), .q(cmd));

  // ------------------------------------------------------------------ pin writes
  always @(*) begin
    rel_m = 8'd0; drv_m = 8'd0; drv_val = 8'd0; drv_oe = 1'b0;
    if (!start && !kill) begin
      if (op_off) rel_m = claimed;
      else if (!anyop && cfg_done) begin
        rel_m = claimed & ~n_cl;
        drv_m = n_cl; drv_val = dec8(n_d) & n_cl; drv_oe = buf_q[18];
      end else if (!anyop && tx_bnd) begin
        drv_m = claimed;
        if (t_end)       begin drv_val = pattern(c_d, c_n, c_diff, 1'b1) & claimed; drv_oe = c_idrv; end
        else if (t_line) begin drv_val = pattern(c_d, c_n, c_diff, lv_nx) & claimed; drv_oe = 1'b1; end
        else begin                                       // EOP: SE0 bits, then J
          drv_val = (pos[1:0] == c_eop) ? (pattern(c_d, c_n, c_diff, 1'b1) & claimed) : 8'd0;
          drv_oe = 1'b1;
        end
      end
    end
  end

  // ------------------------------------------------------------------ registers
  // Idle with nothing arriving, no register below can change, so they share one clock
  // gate (D-29: a bank that is not written does not see the clock).
  wire lu_en = busy | anyop | start | kill | ld;
`ifdef ESC_LINE_PLAIN
  wire lclk = clk;
  wire lu_hold = ~lu_en;
`else
  wire lclk;
  esc_icg icg_lu (.clk(clk), .en(lu_en), .gclk(lclk));
  wire lu_hold = 1'b0;
`endif
  always @(posedge lclk or negedge rst_n) begin
    if (!rst_n) begin
      owned <= 1'b0; owner <= 2'd0; conf <= 1'b0; mode <= IDLE;
      buf_q <= 32'd0; ld <= 1'b0; crc <= 16'd0; job <= 3'd0; cause <= 3'd0; count <= 9'd0;
      el <= 12'd0; acc <= 9'd0; ext <= 1'b0; rn <= 4'd0; rb <= 1'b0; kc <= 9'd0;
      pos <= 7'd0; seg <= 3'd0; lv <= 1'b1; xp <= 1'b1; req <= 1'b0; fin <= 1'b0;
      eopw <= 1'b0; hunt <= 1'b0; dec <= 1'b0; stor <= 1'b0; noted <= 2'd0;
      mon <= 1'b0; abt <= 1'b0;
    end else if (lu_hold) begin
    end else if (start) begin
      owned <= 1'b0; owner <= 2'd0; conf <= 1'b0; mode <= IDLE; ld <= 1'b0; req <= 1'b0;
      fin <= 1'b0; crc <= 16'd0; job <= 3'd0; cause <= 3'd0; count <= 9'd0;
    end else if (kill) begin                             // as OFF; fault and stop release pins
      mode <= IDLE; req <= 1'b0; fin <= 1'b0; conf <= 1'b0; ld <= 1'b0;
    end else begin
      ld <= rd_now | rd_commit;
      if (halt_done && arm_on) mode <= IDLE;
      if (op_send) begin
        mode <= PEND; seg <= 3'd0; pos <= 7'd0; rn <= 4'd0; rb <= 1'b0; lv <= 1'b1;
        kc <= 9'd0; abt <= 1'b0; mon <= 1'b0; req <= 1'b0; fin <= 1'b0; eopw <= 1'b0;
      end
      if (op_off) begin
        mode <= IDLE; conf <= 1'b0; req <= 1'b0; fin <= 1'b0; eopw <= 1'b0;
      end
      if (op_recv) begin
        mode <= ARM; el <= 12'd1; rn <= 4'd0;
      end
      if (op_crc) begin
        job <= 3'd2; cause <= OK; count <= 9'd0; el <= 12'd0;
        if (op_v[15:8] == 8'd0) begin
          mode <= IDLE; crc <= op_v[16] ? crc_w : crc_start;
        end else begin
          mode <= CRCM; crc <= crc_start;
        end
      end
      if (op_cfg) begin
        if (!owned) begin owned <= 1'b1; owner <= op_ctx; end
        mode <= CFG; job <= 3'd3; cause <= OK; count <= 9'd0; el <= 12'd0;
      end
      if (!anyop) begin
        // ---------------------------------------------------------- CONFIG
        if (cfg_on) begin
          el <= el_inc;
          if (ld && el == 12'd0) buf_q <= rdata;         // W0 waits in the buffer
          if (cfg_done) begin
            conf <= 1'b1; mode <= IDLE; cause <= OK; count <= 9'd0;
            crc <= rdata[16] ? n_m : 16'd0;
          end
        end
        // ---------------------------------------------------------- CRC job
        if (crc_on) begin
          el <= el_inc;
          if (ld) buf_q <= rdata;
          crc <= crc_b;
          if (crc_last) begin
            mode <= IDLE; count <= {1'b0, nbits};
            if (cmd[16]) crc <= crc_w;
          end
        end
        // ---------------------------------------------------------- PEND, TX
        if ((mode == PEND || mode == TX) && ld) buf_q <= rdata;
        if (tx_start) begin
          mode <= TX; job <= 3'd4; cause <= OK; count <= 9'd0; crc <= crc_start;
        end
        if (tx_bnd) begin
          el <= 12'd1; ext <= 1'b0;
          acc <= tx_start ? acc_go : acc_nx;
          if (t_end) begin
            mode <= IDLE; req <= 1'b0; mon <= 1'b0;
            if (abt) begin cause <= MISMATCH; count <= kc; end
            else begin cause <= OK; count <= 9'd0; end
          end else if (t_line) begin
            {rn, rb} <= push(rn, rb, lb, c_any);
            lv <= lv_nx;
            mon <= c_abort & ~c_diff;
            if (t_pkt) begin
              if (s_last) begin seg <= eseg + 3'd1; pos <= 7'd0; end
              else pos <= pos + 7'd1;
              if (eseg == 3'd2) begin
                crc <= crc_nx(crc_in, sbit, c_poly);
                if (pos[4:0] == 5'd31 && !s_last) req <= 1'b1;
              end
            end
          end else begin                                 // EOP symbol
            if (s_last) begin seg <= eseg + 3'd1; pos <= 7'd0; end
            else pos <= pos + 7'd1;
          end
        end else if (mode == TX) begin
          el <= el_inc;
          ext <= (el == c_P) & acc[8];
        end
        if (tx_mon) begin
          mon <= 1'b0;
          if (view[c_r] != lv) abt <= 1'b1;
          else kc <= kc + 9'd1;
        end
        if (tx_rd) req <= 1'b0;
        // ---------------------------------------------------------- ARM
        if (arm_on && !halt_done) begin
          if (arm_go) begin
            mode <= RX; job <= 3'd1; cause <= OK; count <= 9'd0; crc <= crc_start;
            el <= 12'd1; acc <= acc_go; ext <= 1'b0; xp <= x; lv <= 1'b1;
            rn <= 4'd0; rb <= 1'b0; hunt <= c_skip; dec <= 1'b0; stor <= 1'b1;
            noted <= 2'd0; eopw <= 1'b0; fin <= 1'b0; req <= 1'b0; buf_q <= 32'd0;
            kc <= 9'd1;
          end else if (idle_t) begin
            if (el == c_P) begin el <= 12'd1; if (rn != 4'hF) rn <= rn + 4'd1; end
            else el <= el_inc;
          end else begin
            el <= 12'd1; rn <= 4'd0;
          end
        end
        // ---------------------------------------------------------- RX
        if (rx_wr) begin
          buf_q <= 32'd0; req <= 1'b0;
          if (fin) begin mode <= IDLE; fin <= 1'b0; end
        end
        if (rx_go) begin
          if (kc[7:0] != 8'hFF) kc <= kc + 9'd1;
          xp <= x;
          if (eopw) begin
            el <= el_inc;
          end else if (rs | tb_hit) begin
            el <= 12'd1; ext <= 1'b0;
            acc <= rs ? acc_go : acc_nx;
          end else begin
            el <= el_inc;
            ext <= (el == c_P) & acc[8];
          end
          if (rx_smp && se0) begin eopw <= 1'b1; el <= 12'd1; end
          if (s_dec) begin
            dec <= 1'b1;
            if (c_nrzi) lv <= x;
          end
          if (s_viol && c_diff) begin noted <= 2'd1; stor <= 1'b0; end
          if (s_push) {rn, rb} <= push(rn, rb, db, c_any);
          if (s_push && !due && hunt && db) hunt <= 1'b0;
          if (s_store) begin
            buf_q[count[4:0] ^ {5{~c_lsb}}] <= db;
            if (!c_hdr || count[8:3] != 6'd0) crc <= crc_nx(crc, db, c_poly);
            count <= cnt1;
            if (cnt1[4:0] == 5'd0) req <= 1'b1;
            if (s_full && c_diff) begin noted <= 2'd2; stor <= 1'b0; end
          end
          if (rx_fin) begin
            eopw <= 1'b0;
            if (f_viol)      begin cause <= VIOL; count <= count; end
            else if (f_full) begin cause <= FULL; count <= cnt1; end
            else             begin cause <= eop_cause; count <= eop_count; end
            if (rx_lastw) begin req <= 1'b1; fin <= 1'b1; end
            else mode <= IDLE;
          end
        end
      end
    end
  end
`ifdef FORMAL
  // A CONFIG keeps its phase: it commits in an owner cycle c (now = owner - 1 mod 4) and
  // during cycle c+1+k it has el = k, so it completes at E_(c+9), never at one of the
  // owner's commit edges (the lemma for esc_core's P8 clause).
  always @(*) if (rst_n && mode == CFG) assert (now[1:0] - el[1:0] == owner);  // lemma-group: commit
  // D-40, W0's latch contract: the buffer does not change at the edge W0 is written
  reg f_pv = 1'b0;
  always @(posedge clk) f_pv <= 1'b1;
  always @(posedge clk)
    if (f_pv && rst_n && $past(rst_n) && $past(cfg_done)) assert ($stable(buf_q));
`endif
endmodule

`default_nettype wire
