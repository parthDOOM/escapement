/*
 * esc_line.v - the line unit (docs/11-SEMANTICS.md v1.6.0 section 6.6, D-33..D-37).
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
    input  wire [29:0] op_v,           // R[rs][29:0]
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
    output wire        send_rd,        // v1.6: a SEND with operand op_v reads stream word 0
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
  wire [31:0] w1;                          //   W1, W2[25:0], and with ext (v1.6) W3[28:0],
  wire [25:0] w2;                          //   W4[20:0] (the bits section 6.6 defines)
  wire [28:0] w3;
  wire [20:0] w4;
  wire [29:0] cmd;                         // SEND command, RECV/CRC/CONFIG operand
  reg  [31:0] buf_q;                       // stream word
  reg  [31:0] nxt_q;                       // v1.6 ext transmit: the next stream word
  reg         ld;                          // the unit read the tape in the previous cycle
  reg         ldn;                         // ... into the next-word buffer
  reg  [31:0] crc;                         // v1.6: 32 bits, the polynomial left-aligned
  reg  [2:0]  job, cause;
  reg  [8:0]  count;
  reg  [11:0] el;                          // tick counter (see header)
  reg  [8:0]  acc;                         // alpha + P_frac of the current bit; [8] = carry
  reg         ext;                         // the current bit is P_int + 1 long: one more tick
  reg  [3:0]  rn;                          // stuffing run; idle bit times in ARM
  reg         rb;                          // the run's bit (any = 1)
  reg  [8:0]  kc;                          // TX: monitored bits; RX: ticks since t0 (to 255)
  reg  [12:0] pos;                         // TX: bit within the segment
  reg  [2:0]  cel;                         // TX: cell of the symbol in progress (v1.6)
  reg  [1:0]  sk;                          // TX: that symbol: 0 a line bit, 1 SE0, 2 L = 1
  reg  [2:0]  seg;                         // TX: 0 prefix, 1 header, 2 data, 3 CRC, 4 EOP
  reg         lv;                          // TX: line level L; RX: NRZI reference
  reg         xp;                          // RX: x during the previous cycle
  reg         req;                         // TX read or RX write waiting for an owner cycle
  reg         reqn;                        // ... a TX read for the next-word buffer (ext)
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
  wire        c_init = w2[16], c_inv = w2[17], c_hdr = w2[18], c_fall = w2[19],
              c_abort = w2[20];
  wire [3:0]  c_imin = w2[24:21];
  // v1.6 extended configuration; without ext the legacy values (one cell, NRZ patterns)
  wire        c_ext   = w2[25];
  wire [2:0]  c_cells = c_ext ? w3[2:0] : 3'd0;         // cells per symbol, minus one
  wire [7:0]  c_p0    = c_ext ? w3[10:3] : 8'h00;       // cell levels of a line bit 0
  wire [7:0]  c_p1    = c_ext ? w3[18:11] : 8'h01;      //   ... and of a line bit 1
  wire        c_clk   = c_ext & w3[19];                 // n carries the clock pattern
  wire [7:0]  c_pc    = w3[27:20];
  wire        c_cidle = w3[28];                         // n while idle, with clk
  wire [31:0] c_poly  = c_ext ? {w4[15:0], w2[15:0]} : {w2[15:0], 16'd0};
  wire [2:0]  c_prep  = c_ext ? w4[18:16] : 3'd0;       // the prefix goes prep + 1 times
  wire        c_eopl  = c_ext & w4[19];                 // EOP symbols are L = 1, no J
  wire        c_ilow  = c_ext & w4[20];                 // the idle state is L = 0

  function [7:0] dec8(input [2:0] i);
    dec8 = 8'd1 << i;
  endfunction
  // M = -(poly & -poly) mod 2^32: bit i is set iff poly has a set bit at or below i
  function [31:0] pfx(input [31:0] p);
    integer i;
    begin
      pfx[0] = p[0];
      for (i = 1; i < 32; i = i + 1) pfx[i] = pfx[i-1] | p[i];
    end
  endfunction
  function [15:0] rev16(input [15:0] x);
    integer i;
    for (i = 0; i < 16; i = i + 1) rev16[i] = x[15-i];
  endfunction
  // levels dl on d and, with diff, nl on n
  function [7:0] pins2(input [2:0] d, input [2:0] n, input diff, input dl, input nl);
    pins2 = (dl ? dec8(d) : 8'd0) | ((diff & nl) ? dec8(n) : 8'd0);
  endfunction

  wire [31:0] m_c = pfx(c_poly);
  wire [31:0] lsbm = m_c & ~{m_c[30:0], 1'b0};          // the polynomial's lowest set bit
  wire [31:0] crc_start = c_init ? m_c : 32'd0;
  // the CRC in transmission order (CRC wire flag): bit 16 + i = bit 31 - i, for i below the
  // width and below 16 (where the 16-bit status field shows it)
  // (functions take every input as an argument: a simulator re-evaluates a continuous
  // assignment only when an argument changes)
  function [31:0] xf(input [15:0] c, input inv, input [15:0] m);    // c, m: bits 31:16
    xf = {rev16(inv ? ~c : c) & rev16(m), 16'd0};
  endfunction
  function [31:0] crc_nx(input [31:0] c, input b, input [31:0] poly);
    crc_nx = {c[30:0], 1'b0} ^ ((c[31] ^ b) ? poly : 32'd0);
  endfunction

  assign claimed = (conf & c_txp) ? (dec8(c_d) | (c_diff ? dec8(c_n) : 8'd0)) : 8'd0;
  assign engaged = (mode == CFG) | (mode == PEND) | (mode == TX) | (mode == RX)
                 | (mode == CRCM);
  assign busy    = (mode != IDLE);
  assign status  = {busy, job, cause, count, crc[31:16]};

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
  // (v1.6: not a load of the next-word buffer, which fills nxt_q, not buf_q)
  wire sbit = (ld & ~ldn) ? (c_lsb ? rdata[0] : rdata[31]) : buf_q[sidx];

  // ------------------------------------------------------------------ CONFIG
  wire        cfg_on   = (mode == CFG);
  // cycles c+4 and c+8, and with the new W2's ext (written at el = 8) c+12 and c+16;
  // done at E_(c+9), or E_(c+17) with ext
  wire        cfg_rd   = cfg_on & ((el == 12'd3) | (el == 12'd7)
                                   | (w2[25] & ((el == 12'd11) | (el == 12'd15))));
  wire        cfg_x    = cfg_on & (el == 12'd16);                    // an ext CONFIG's last edge
  wire        cfg_done = cfg_on & (((el == 12'd8) & ~rdata[25]) | (el == 12'd16));
  wire [2:0]  n_d = buf_q[2:0], n_n = buf_q[5:3];
  wire        n_diff = buf_q[9], n_tx = buf_q[20];
  wire [7:0]  n_cl = n_tx ? (dec8(n_d) | (n_diff ? dec8(n_n) : 8'd0)) : 8'd0;
  wire        n_valid = (cfg_x & (w3[2:0] != 3'd0)) ? (w1[11:0] >= 12'd2)
                      : (w1[11:0] >= 12'd8) & (w1[31:20] >= 12'd4) & (w1[31:20] < w1[11:0]);
  assign flt_owner  = cfg_done & n_tx & (~owns[n_d] | (n_diff & (~owns[n_n] | (n_d == n_n))));
  assign flt_config = cfg_done & ~flt_owner & ~n_valid;
  wire [31:0] n_m    = pfx(cfg_x ? {rdata[15:0], w2[15:0]} : {rdata[15:0], 16'd0});
  wire        n_init = cfg_x ? w2[16] : rdata[16];
  // the new configuration's idle levels: d = L (0 with ilow), n = not L, or cidle with clk
  wire        n_idl  = ~(cfg_x & rdata[20]);
  wire        n_idn  = (cfg_x & w3[19]) ? w3[28] : ~n_idl;

  // ------------------------------------------------------------------ CRC job
  wire        crc_on   = (mode == CRCM);
  wire [7:0]  nbits    = cmd[15:8];
  wire        crc_last = crc_on & (el_inc[7:0] == nbits);
  wire        crc_rd   = crc_on & (el[4:0] == 5'd31) & (el_inc[7:0] < nbits);
  wire [31:0] crc_b    = crc_nx(crc, sbit, c_poly);
  wire [15:0] crc_xw   = crc_on ? crc_b[31:16] : crc_start[31:16];
  wire [31:0] crc_w    = xf(crc_xw, c_inv, m_c[31:16]);                // wire-flag result

  // ------------------------------------------------------------------ transmit
  wire        tx_start = (mode == PEND) & line_ret;
  // v1.6: a symbol lasts cells timed boundaries; a symbol boundary starts the next one
  wire        lastc    = (cel == c_cells);
  wire        tx_bnd   = tx_start | ((mode == TX) & tb_hit & lastc);
  wire        tx_cb    = (mode == TX) & tb_hit & ~lastc;     // the next cell of a symbol
  wire [12:0] nb       = {c_ext ? cmd[29:24] : 6'd0, cmd[14:8]};
  wire [5:0]  sen = {1'b1, c_diff & (c_eop != 2'd0), cmd[15] & m_c[31], nb != 13'd0, c_hdr,
                     c_pre != 8'd0};
  wire [1:0]  eopn = c_eop - {1'b0, c_eopl};            // the last EOP symbol's index
  // first enabled segment at or after a segment
  function [2:0] first_seg(input [2:0] from, input [5:0] en);
    integer si;
    begin
      first_seg = 3'd5;
      for (si = 4; si >= 0; si = si - 1)
        if (si[2:0] >= from && en[si]) first_seg = si[2:0];
    end
  endfunction
  // v1.6: the line bit of a boundary starts from flip-flops. The packet bit, its
  // last-of-segment flag and its segment depend on seg, pos, the stream buffer and crc,
  // which change only at a boundary, at a SEND, and when a stream word loads, at least
  // three cycles before the next boundary (sec. 6.6: usable from u + 1 <= B + 5, and
  // P_int >= 8). So they are registered in every cycle (pb_q, sl_q, es_q), and in every
  // boundary cycle but the first they hold that cycle's values (asserted below). The first
  // boundary has seg = pos = 0 (set by the SEND) and uses direct values, which select
  // fixed bits only.
  wire [2:0]  es_c   = first_seg(seg, sen);              // registered as es_q
  wire [7:0]  fbyte  = (es_c == 3'd0) ? c_pre : cmd[23:16];
  wire        b_byte = fbyte[pos[2:0] ^ {3{~c_lsb}}];
  wire [31:0] crcx   = c_inv ? ~crc : crc;
  wire        b_crc  = crcx[~pos[4:0]];
  wire        pb_c   = (es_c[2:1] == 2'b00) ? b_byte : (es_c == 3'd2) ? sbit : b_crc;
  wire        sl_c   = (es_c == 3'd0) ? (pos[5:0] == {c_prep, 3'd7})
                     : (es_c == 3'd1) ? (pos[2:0] == 3'd7)
                     : (es_c == 3'd2) ? (pos + 13'd1 == nb)
                     : (es_c == 3'd3) ? lsbm[~pos[4:0]]
                     : (pos[1:0] == eopn);
  reg  [2:0]  es_q;
  reg         pb_q, sl_q;
  wire [2:0]  es0    = first_seg(3'd0, sen);             // the first boundary: seg = pos = 0
  wire        crcx0  = c_inv ? ~crc_start[31] : crc_start[31];   // first CRC bit
  wire        sb0    = ld ? (c_lsb ? rdata[0] : rdata[31]) : (c_lsb ? buf_q[0] : buf_q[31]);
  wire        pb0    = (es0 == 3'd0) ? (c_lsb ? c_pre[0] : c_pre[7])
                     : (es0 == 3'd1) ? (c_lsb ? cmd[16] : cmd[23])
                     : (es0 == 3'd2) ? sb0 : crcx0;
  wire        sl0    = (es0[2:1] == 2'b00) ? 1'b0
                     : (es0 == 3'd2) ? (nb == 13'd1)
                     : (es0 == 3'd3) ? lsbm[31]
                     : (eopn == 2'd0);
  wire [2:0]  eseg   = tx_start ? es0 : es_q;
  wire        pbit   = tx_start ? pb0 : pb_q;
  wire        s_last = tx_start ? sl0 : sl_q;
  wire        t_line = ~abt & (due | (eseg <= 3'd3));      // a line bit at this boundary
  wire        t_end  = abt | (~due & (eseg == 3'd5));
  wire        t_pkt  = ~due & (eseg <= 3'd3);                // ... and it is a packet bit
  wire        lb     = due ? sb : pbit;
  wire        lv_nx  = c_nrzi ? (lb ? lv : ~lv) : lb;
  wire [1:0]  k_s    = t_line ? 2'd0 : (c_eopl | (pos[1:0] == c_eop)) ? 2'd2 : 2'd1;  // its kind
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
  // CONFIG words 1..4 at el = 3, 7, 11, 15; a TX read: the word of pos, or with ext the one
  // after it (requested mid-word, while pos is still in the current word)
  wire [7:0] off    = cfg_on ? {6'd0, el[3:2]} + 8'd1 : crc_on ? {5'd0, el_inc[7:5]}
                    : (mode == TX) ? (pos[12:5] + {7'd0, c_ext}) : {5'd0, rx_wi};
  assign port_addr  = cmd[7:0] + off;
  assign port_wdata = buf_q;
  wire rd_now = cfg_rd | crc_rd | tx_rd;
  assign send_rd = (op_v[14:8] != 7'd0) | (c_ext & (op_v[29:24] != 6'd0));
  wire rd_commit = op_cfg | (op_crc & (op_v[15:8] != 8'd0)) | (op_send & send_rd);

  // ------------------------------------------------------------------ banks
  wire tx_endev = live & tx_bnd & t_end;
  wire rx_none = op_recv & (c_cells != 3'd0);           // v1.6: no receiver for cells > 1
  wire e_ev = (live & (cfg_done | crc_last | tx_endev | rx_fin))
            | (~start & ~kill & ((op_crc & (op_v[15:8] == 8'd0)) | rx_none));
  esc_greg #(32) g_e (.clk(clk), .rst_n(rst_n), .en(start | e_ev),
                      .d(start ? 32'd0 : now), .q(e));
  // W0 is a latch bank (D-40): the buffer holds still at the CONFIG's last edge, so it is
  // its own dq
  esc_lreg #(29) g_w0 (.clk(clk), .rst_n(rst_n), .en(cfg_done), .d({buf_q[31:24], buf_q[20:0]}),
                       .dq({buf_q[31:24], buf_q[20:0]}), .q(w0));
  esc_greg #(32) g_w1 (.clk(clk), .rst_n(rst_n), .en(cfg_on & ld & (el == 12'd4)),
                       .d(rdata), .q(w1));
  // v1.6: W2 is written when it arrives (its ext decides whether W3 and W4 follow); the
  // unit is in CFG, so nothing uses the configuration until the CONFIG completes
  esc_greg #(26) g_w2 (.clk(clk), .rst_n(rst_n), .en(cfg_on & ld & (el == 12'd8)),
                       .d(rdata[25:0]), .q(w2));
  esc_greg #(29) g_w3 (.clk(clk), .rst_n(rst_n), .en(cfg_on & ld & (el == 12'd12)),
                       .d(rdata[28:0]), .q(w3));
  esc_greg #(21) g_w4 (.clk(clk), .rst_n(rst_n), .en(cfg_x), .d(rdata[20:0]), .q(w4));
  esc_greg #(30) g_cmd (.clk(clk), .rst_n(rst_n),
                        .en(op_send | op_recv | op_crc | op_cfg), .d(op_v), .q(cmd));

  // ------------------------------------------------------------------ pin writes
  // v1.6: a transmit write is decided in the cycle before its edge and registered (dq_*),
  // so the pins are a few gates from flip-flops. Everything it depends on is known a cycle
  // ahead: the next timed boundary from el, acc and ext (a cell lasts at least 2 ticks, so
  // a boundary never follows a boundary); at a cell boundary the symbol's level, kind and
  // cell, which change only at symbol boundaries; at a symbol boundary the packet bit,
  // segment and last flag (pb_c, es_c, sl_c, registered as pb_q, es_q, sl_q), the stuffing
  // run and the line level, which change only at boundaries, and the monitor's mismatch,
  // seen at the latest in the cycle before. Asserted equal to the definitions (below).
  // the levels of cell c of a symbol: a line bit L shows its pattern's bit c on d; with
  // diff n shows not d, or with clk the clock pattern's bit c
  function [1:0] levels(input [1:0] k, input l, input [2:0] c,
                        input [7:0] p0, input [7:0] p1, input ck, input [7:0] pc);
    reg dl;
    begin
      dl = (k == 2'd0) ? (l ? p1[c] : p0[c]) : (k == 2'd2);
      levels = {dl, (k == 2'd1) ? 1'b0 : ck ? pc[c] : ~dl};
    end
  endfunction
  // (an op holds el and ext, so a boundary due under one comes in the cycle after)
  wire       tb_n   = (mode == TX) & (anyop ? tb_hit : ~tb_hit
                    & (((el_inc == c_P) & ~acc[8]) | ((el == c_P) & acc[8])));
  wire       abt_n  = abt | (tx_mon & (view[c_r] != lv));
  wire       lb_n   = due ? sb : pb_c;
  wire       lvn    = c_nrzi ? (lb_n ? lv : ~lv) : lb_n;
  wire       tln    = ~abt_n & (due | (es_c <= 3'd3));
  wire       ten    = abt_n | (~due & (es_c == 3'd5));
  wire [1:0] kn     = tln ? 2'd0 : k_s;
  wire [1:0] lc_n   = levels(sk, lv, cel + 3'd1, c_p0, c_p1, c_clk, c_pc);   // the next cell
  wire [1:0] ls_n   = levels(kn, lvn, 3'd0, c_p0, c_p1, c_clk, c_pc);        // the next symbol
  wire [7:0] idle_v = pins2(c_d, c_n, c_diff, ~c_ilow, c_clk ? c_cidle : c_ilow) & claimed;
  wire       sym_n  = (cel == c_cells);
  wire [7:0] dq_vn  = (sym_n & ten) ? idle_v
                    : pins2(c_d, c_n, c_diff, sym_n ? ls_n[1] : lc_n[1], sym_n ? ls_n[0] : lc_n[0])
                      & claimed;
  wire       dq_on  = ~(sym_n & ten) | c_idrv;
  reg        dq_go, dq_oe;
  reg  [7:0] dq_val;
  // A packet's first boundary: its segment and its first bit when that is not a stream bit
  // are registered at the SEND (the configuration cannot change while the unit is engaged);
  // a first stream bit comes from the buffer, or from the tape in the cycle it arrives.
  wire [12:0] nb_v  = {c_ext ? op_v[29:24] : 6'd0, op_v[14:8]};
  wire [5:0]  sen_v = {1'b1, c_diff & (c_eop != 2'd0), op_v[15] & m_c[31], nb_v != 13'd0, c_hdr,
                       c_pre != 8'd0};
  wire [2:0]  es0_v = first_seg(3'd0, sen_v);
  wire        pbx_v = (es0_v == 3'd0) ? (c_lsb ? c_pre[0] : c_pre[7])
                    : (es0_v == 3'd1) ? (c_lsb ? op_v[16] : op_v[23]) : crcx0;
  reg  [2:0]  es0_q;
  reg         pbx_q;
  wire        b0    = (es0_q == 3'd2) ? sb0 : pbx_q;      // the first packet bit (L = it)
  wire [1:0]  k0    = (es0_q <= 3'd3) ? 2'd0 : c_eopl ? 2'd2 : 2'd1;
  wire [1:0]  l0    = levels(k0, b0, 3'd0, c_p0, c_p1, c_clk, c_pc);
  wire [7:0]  v0    = (es0_q == 3'd5) ? idle_v : pins2(c_d, c_n, c_diff, l0[1], l0[0]) & claimed;
  always @(*) begin
    rel_m = 8'd0; drv_m = 8'd0; drv_val = 8'd0; drv_oe = 1'b0;
    if (!start && !kill) begin
      if (op_off) rel_m = claimed;
      else if (!anyop && cfg_done) begin
        rel_m = claimed & ~n_cl;
        drv_m = n_cl; drv_val = pins2(n_d, n_n, n_diff, n_idl, n_idn) & n_cl; drv_oe = buf_q[18];
      end else if (!anyop && tx_start) begin
        drv_m = claimed; drv_val = v0; drv_oe = (es0_q != 3'd5) | c_idrv;
      end else if (!anyop && mode == TX && dq_go) begin
        drv_m = claimed; drv_val = dq_val; drv_oe = dq_oe;
      end
    end
  end
`ifdef FORMAL
  // the definitions the registered writes replace (section 6.6)
  wire [2:0] f_cn  = tx_cb ? cel + 3'd1 : 3'd0;
  wire       f_bl  = tx_cb ? lv : lv_nx;
  wire [1:0] f_ks  = tx_cb ? sk : k_s;
  wire [1:0] f_lv  = levels(f_ks, f_bl, f_cn, c_p0, c_p1, c_clk, c_pc);
  wire [7:0] f_val = (tx_bnd & t_end) ? idle_v : pins2(c_d, c_n, c_diff, f_lv[1], f_lv[0]) & claimed;
  wire       f_oe  = ~(tx_bnd & t_end) | c_idrv;
  always @(*) if (rst_n && !start && !kill && !anyop) begin
    assert (((mode == TX) & dq_go) == ((mode == TX) & tb_hit));
    if ((tx_bnd || tx_cb) && claimed != 8'd0) assert (drv_val == f_val && drv_oe == f_oe);
  end
`endif

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
  // v1.6: the next stream word loads outside the start/kill/op priority below, from
  // flip-flops only (mode, ld, ldn): a start never finds the unit in PEND or TX (a stop
  // kills it first), and a word loaded at a kill or OFF is never read, since the next
  // packet's prefetch refills nxt_q before its first word ends.
  wire nx_we = (mode == PEND || mode == TX) & ld & ldn;
  always @(posedge lclk or negedge rst_n) begin
    if (!rst_n)                  nxt_q <= 32'd0;
    else if (!lu_hold && nx_we)  nxt_q <= rdata;
  end
  always @(posedge lclk or negedge rst_n) begin
    if (!rst_n) begin
      owned <= 1'b0; owner <= 2'd0; conf <= 1'b0; mode <= IDLE;
      buf_q <= 32'd0; ld <= 1'b0; ldn <= 1'b0; crc <= 32'd0;
      job <= 3'd0; cause <= 3'd0; count <= 9'd0;
      el <= 12'd0; acc <= 9'd0; ext <= 1'b0; rn <= 4'd0; rb <= 1'b0; kc <= 9'd0;
      pos <= 13'd0; cel <= 3'd0; sk <= 2'd0; seg <= 3'd0; lv <= 1'b1; xp <= 1'b1;
      req <= 1'b0; reqn <= 1'b0; fin <= 1'b0;
      eopw <= 1'b0; hunt <= 1'b0; dec <= 1'b0; stor <= 1'b0; noted <= 2'd0;
      mon <= 1'b0; abt <= 1'b0; es_q <= 3'd5; pb_q <= 1'b0; sl_q <= 1'b0;
      dq_go <= 1'b0; dq_oe <= 1'b0; dq_val <= 8'd0; es0_q <= 3'd5; pbx_q <= 1'b0;
    end else if (lu_hold) begin
    end else if (start) begin
      owned <= 1'b0; owner <= 2'd0; conf <= 1'b0; mode <= IDLE; ld <= 1'b0; ldn <= 1'b0;
      req <= 1'b0; reqn <= 1'b0; fin <= 1'b0; crc <= 32'd0; job <= 3'd0; cause <= 3'd0;
      count <= 9'd0;
    end else if (kill) begin                             // as OFF; fault and stop release pins
      mode <= IDLE; req <= 1'b0; reqn <= 1'b0; fin <= 1'b0; conf <= 1'b0; ld <= 1'b0;
      ldn <= 1'b0;
    end else begin
      es_q <= es_c; pb_q <= pb_c; sl_q <= sl_c;
      dq_go <= tb_n; dq_val <= dq_vn; dq_oe <= dq_on;
      if (op_send) begin es0_q <= es0_v; pbx_q <= pbx_v; end
      ld <= rd_now | rd_commit;
      ldn <= tx_rd & reqn;
      if (halt_done && arm_on) mode <= IDLE;
      if (op_send) begin
        mode <= PEND; seg <= 3'd0; pos <= 13'd0; rn <= 4'd0; rb <= 1'b0; lv <= 1'b1;
        kc <= 9'd0; abt <= 1'b0; mon <= 1'b0; req <= 1'b0; reqn <= 1'b0; fin <= 1'b0;
        eopw <= 1'b0; cel <= 3'd0;
      end
      if (op_off) begin
        mode <= IDLE; conf <= 1'b0; req <= 1'b0; fin <= 1'b0; eopw <= 1'b0;
      end
      if (op_recv && !rx_none) begin
        mode <= ARM; el <= 12'd1; rn <= 4'd0;
      end
      if (rx_none) begin
        mode <= IDLE; job <= 3'd1; cause <= VIOL; count <= 9'd0;
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
            crc <= n_init ? n_m : 32'd0;
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
        if ((mode == PEND || mode == TX) && ld && !ldn) buf_q <= rdata;
        if (tx_start) begin
          mode <= TX; job <= 3'd4; cause <= OK; count <= 9'd0; crc <= crc_start;
        end
        if (tx_cb) begin                                 // v1.6: the next cell
          el <= 12'd1; ext <= 1'b0; acc <= acc_nx;
          cel <= cel + 3'd1;
        end
        if (tx_bnd) begin
          el <= 12'd1; ext <= 1'b0;
          acc <= tx_start ? acc_go : acc_nx;
          cel <= 3'd0; sk <= k_s;
          if (t_end) begin
            mode <= IDLE; req <= 1'b0; reqn <= 1'b0; mon <= 1'b0;
            if (abt) begin cause <= MISMATCH; count <= kc; end
            else begin cause <= OK; count <= 9'd0; end
          end else if (t_line) begin
            {rn, rb} <= push(rn, rb, lb, c_any);
            lv <= lv_nx;
            mon <= c_abort & ~c_diff & (c_cells == 3'd0);
            if (t_pkt) begin
              if (s_last) begin seg <= eseg + 3'd1; pos <= 13'd0; end
              else pos <= pos + 13'd1;
              if (eseg == 3'd2) begin
                crc <= crc_nx(tx_start ? crc_start : crc, pbit, c_poly);
                if (!c_ext && pos[4:0] == 5'd31 && !s_last) req <= 1'b1;
                // v1.6 ext: the next word is fetched mid-word, and used from bit 0 on
                if (c_ext && pos[4:0] == 5'd15 && pos + 13'd17 < nb) begin
                  req <= 1'b1; reqn <= 1'b1;
                end
                if (c_ext && pos[4:0] == 5'd31 && !s_last) buf_q <= nxt_q;
              end
            end
          end else begin                                 // EOP symbol
            if (s_last) begin seg <= eseg + 3'd1; pos <= 13'd0; end
            else pos <= pos + 13'd1;
          end
        end else if (mode == TX && !tx_cb) begin
          el <= el_inc;
          ext <= (el == c_P) & acc[8];
        end
        if (tx_mon) begin
          mon <= 1'b0;
          if (view[c_r] != lv) abt <= 1'b1;
          else kc <= kc + 9'd1;
        end
        if (tx_rd) begin req <= 1'b0; reqn <= 1'b0; end
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
  // v1.6: a transmit boundary other than the first uses registered values equal to the
  // values of its own cycle (the timing argument above).
  always @(*) if (rst_n && mode == TX && tb_hit && lastc && !kill && !start && !anyop)
    assert (es_q == es_c && pb_q == pb_c && sl_q == sl_c);
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
