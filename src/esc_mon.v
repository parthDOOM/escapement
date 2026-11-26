/*
 * esc_mon.v - Escapement bus timing monitor (docs/11-SEMANTICS.md §6.7, §7.8).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Checks the timing of traffic the chip only watches: RULES interval rules on the
 * synchronised pins, each "after an edge of pin A, the next edge of pin B comes no earlier
 * than MIN and no later than MAX ticks". A rule starts on its from-edge and ends on its
 * to-edge; the interval is the ticks between them (cnt is 1 in the cycle after the
 * from-edge). An interval below MIN, or MAX ticks with no to-edge, is a violation, counted
 * per rule and recorded (the first one since the unit was armed: rule, kind, tick, interval).
 * EXPECT checks the answers to our own edges; this unit checks edges between other devices.
 *
 *   pulse width   from = to = pin p, rise -> fall        (MIN and/or MAX)
 *   setup         from = data, any edge -> to = clock rise (MIN)
 *   hold          from = clock rise -> to = data, any edge (MIN)
 *   period        from = to = clock, rise -> rise          (MIN and/or MAX)
 *   timeout       any pair, MAX only (a stuck clock, a missing answer)
 *
 * MIN = 0 disables the early check, MAX = 0 the late check. Both checks use the same
 * per-rule tick counter, so a violation is exact to the tick of the synchronised pins
 * (the same view as CAPT).
 *
 * MON rd, rs (at E_c) writes one rule: R[rd] [31] enable, [30:29] rule, [28:25] from pin,
 * [24:23] from edge, [22:19] to pin, [18:17] to edge, [15:0] MIN (edges: 1 rise, 2 fall,
 * 3 any), [16] keep (write no rule: with clear, a clear alone); R[rs] [31] clear every
 * count and the record, [15:0] MAX. Pins 0..12 are psync;
 * 13..15 never change. The unit evaluates at every edge of a run except a stop edge
 * (`run` = run & ~stop); `prev` follows the pins at every edge. Reference model:
 * sw/esciss/monitor.py.
 *
 * Narrow to the core: the unit sits where its pins are, far from the core's
 * write-back mux, so it sends GETM one 32-bit word (rdata: counts or the record, by rsel)
 * and not both, and the record's tick is not kept here: rec_load says the record is taken at
 * this edge and the core loads its own copy of now (no 32-bit now in, no tick out). A
 * trial layout with the three words and now crossing the core did not route.
 *
 * Area: a rule's pins, edges, MIN and MAX change only at a MON write, so they
 * are latch banks (esc_lreg, D-40) written through one shared word, cfg_q; the record's
 * body is a gated bank. 31.2k -> 26.0k um2 standalone, the same behaviour: esc_lreg's
 * contract is M7 here, the latch build is formal/esc_lreg.sby's, and the chip is
 * sequentially equivalent to the flip-flop version (formal/equiv.sh, flop model).
 */
`default_nettype none
module esc_mon #(
    parameter integer RULES = 4,                 // (the MON encoding names rules 0..3)
    parameter integer W     = 16                 // interval counter and limit width (ticks)
) (
    input  wire          clk,
    input  wire          rst_n,
    input  wire          start,                  // run initialisation at this edge
    input  wire          run,                    // evaluate at this edge (a run, not its stop)
    input  wire [12:0]   psync,                  // synchronised physical pins (both banks)
    input  wire          mon_we,                 // a MON completes at this edge
    input  wire [31:0]   mon_a, mon_b,           // its R[rd], R[rs]
    input  wire          rsel,                   // GETM sel[0]: 0 counts, 1 the record
    output wire [31:0]   rdata,                  // counts (rule k in [8k+7:8k]) or the record
    output wire          rec_load,               // the record is taken at this edge (the core
                                                 // keeps its tick: GETM sel 2)
    output wire          irq                     // a violation at this edge
);
  wire [31:0] counts, rec;
  assign rdata = rsel ? rec : counts;
  reg  [12:0] prev;
  wire [15:0] rise = {3'd0, psync & ~prev};
  wire [15:0] fall = {3'd0, ~psync & prev};

  function ev;                                   // the edge of a rule's pin in this cycle
    input [15:0] r, f; input [3:0] pin; input [1:0] kind;
    ev = (kind[0] & r[pin]) | (kind[1] & f[pin]);
  endfunction

  wire        clr    = mon_we & mon_b[31];
  wire [1:0]  w_rule = mon_a[30:29];

  // A rule's configuration (pins, edges, MIN, MAX; 12 + 2W bits) changes only at a MON
  // write, so it is a latch bank (esc_lreg, D-40: 27.2 um2 a bit against 49.0 for the
  // flip-flop and 18.1 for its hold mux), written through one shared flip-flop word
  // (cfg_q, esc_lreg's dq: what cfg_d was at the last write edge). en stays a flip-flop:
  // a start clears it.
  localparam integer CW = 12 + 2 * W;
  wire          wr    = ~start & mon_we & ~mon_a[16];          // a rule write at this edge
  wire [CW-1:0] cfg_d = {mon_a[28:17], mon_a[W-1:0], mon_b[W-1:0]};
  wire [CW-1:0] cfg_q;
  esc_greg #(CW) g_cq (.clk(clk), .rst_n(rst_n), .en(wr), .d(cfg_d), .q(cfg_q));

  // v3.2: each rule's state (enable, armed, interval counter, violation count) is a
  // clock-gated bank (esc_greg, D-74), open only at an edge that can change it: a start, a
  // write of the rule, a clear, or a run edge while the rule is armed or sees its from-edge.
  // A rule that is off, or on and waiting for its from-edge, sees no clock.
  wire          en    [0:RULES-1];
  wire          armed [0:RULES-1];
  wire [W-1:0]  cnt   [0:RULES-1];
  wire [7:0]    nv    [0:RULES-1];

  wire [RULES-1:0] early, late;
  genvar g;
`ifdef ESC_MON_CHECK
  reg f_pv = 1'b0;                               // formal/esc_mon.sby (properties M1-M6)
  always @(posedge clk) f_pv <= 1'b1;
  always @(*) if (!f_pv) assume (!rst_n);        // from reset; then every input is free
`endif
  generate for (g = 0; g < RULES; g = g + 1) begin : g_r
    wire          cw = wr & (w_rule == g);                    // this rule is written
    wire [CW-1:0] cfg;
    esc_lreg #(CW) g_cfg (.clk(clk), .rst_n(rst_n), .en(cw), .d(cfg_d), .dq(cfg_q), .q(cfg));
    wire [3:0]    from  = cfg[CW-1 -: 4],  to    = cfg[CW-7 -: 4];
    wire [1:0]    fedge = cfg[CW-5 -: 2],  tedge = cfg[CW-11 -: 2];
    wire [W-1:0]  mn    = cfg[2*W-1 -: W], mx    = cfg[W-1:0];
    wire f_ev = en[g] & ev(rise, fall, from, fedge);
    wire t_ev = en[g] & ev(rise, fall, to, tedge);
    // an interval ends on the to-edge (checked against MIN before any re-arm at the same
    // edge), or MAX ticks after the from-edge with no to-edge
    assign early[g] = run & armed[g] & t_ev & (mn != {W{1'b0}}) & (cnt[g] < mn);
    assign late[g]  = run & armed[g] & ~t_ev & (mx != {W{1'b0}}) & (cnt[g] == mx);
    assign counts[8*g +: 8] = nv[g];
    reg          en_d, armed_d;
    reg [W-1:0]  cnt_d;
    reg [7:0]    nv_d;
    always @(*) begin
      en_d = en[g]; armed_d = armed[g]; cnt_d = cnt[g]; nv_d = nv[g];
      if (start) begin
        en_d = 1'b0; armed_d = 1'b0; cnt_d = {W{1'b0}}; nv_d = 8'd0;
      end else begin
        if (run) begin
          if (f_ev) begin armed_d = 1'b1; cnt_d = {{(W-1){1'b0}}, 1'b1}; end
          else if (armed[g] & (t_ev | late[g])) armed_d = 1'b0;
          else if (armed[g] & (cnt[g] != {W{1'b1}})) cnt_d = cnt[g] + 1'b1;
          if ((early[g] | late[g]) && nv[g] != 8'hFF) nv_d = nv[g] + 8'd1;
        end
        if (clr) nv_d = 8'd0;
        if (cw) begin                              // (the rest of the rule: g_cfg)
          en_d = mon_a[31]; armed_d = 1'b0; cnt_d = {W{1'b0}};
        end
      end
    end
    wire          st_en = start | cw | clr | (run & (f_ev | armed[g]));
    wire          en_q, armed_q;
    wire [W-1:0]  cnt_q;
    wire [7:0]    nv_q;
    esc_greg #(2 + W + 8) g_st (.clk(clk), .rst_n(rst_n), .en(st_en),
        .d({en_d, armed_d, cnt_d, nv_d}), .q({en_q, armed_q, cnt_q, nv_q}));
    assign en[g] = en_q; assign armed[g] = armed_q; assign cnt[g] = cnt_q; assign nv[g] = nv_q;
`ifdef ESC_MON_CHECK
    // M1 an armed rule is enabled and has counted at least the tick after its from-edge
    always @(*) if (rst_n && armed[g]) assert (en[g] && cnt[g] != {W{1'b0}});
    always @(posedge clk) if (f_pv && rst_n && $past(rst_n) && !$past(start)) begin
      // M2 a count never wraps: it moves by at most one, or to 0 at a clear
      assert (nv[g] == $past(nv[g]) || nv[g] == $past(nv[g]) + 8'd1
              || ($past(clr) && nv[g] == 8'd0));
      if ($past(nv[g]) == 8'hFF && !$past(clr)) assert (nv[g] == 8'hFF);
      // M3 a keep write (R[rd][16]) changes no rule
      if ($past(mon_we) && $past(mon_a[16]) && !$past(run))
        assert (en[g] == $past(en[g]) && cfg == $past(cfg)
                && armed[g] == $past(armed[g]) && cnt[g] == $past(cnt[g]));
      // M7 esc_lreg's contract (D-40): after every edge that writes the bank, its dq holds
      // what d was at that edge
      if ($past(cw)) assert (cfg_q == $past(cfg_d));
      // M4 the counter counts: armed through a run edge with no edge of either pin and no
      // write, it is one more (saturating)
      if ($past(run) && $past(armed[g]) && !$past(f_ev) && !$past(t_ev)
          && !$past(late[g]) && !$past(cw))
        assert (armed[g] && cnt[g] == (($past(cnt[g]) == {W{1'b1}}) ? {W{1'b1}}
                                                                      : $past(cnt[g]) + 1'b1));
    end
`endif
  end endgenerate

  // the first violation since start or a clear: the lowest-numbered rule wins a tie
  wire          any_v = |(early | late);
  reg  [1:0]    v_rule;
  integer i;
  always @(*) begin
    v_rule = 2'd0;
    for (i = RULES - 1; i >= 0; i = i - 1) if (early[i] | late[i]) v_rule = i[1:0];
  end
  assign irq = any_v;

  reg          rec_v;
  wire         rec_late;
  wire [1:0]   rec_rule;
  wire [W-1:0] rec_ivl;
  assign rec_load = ~(start | clr) & any_v & ~rec_v;   // the first violation is recorded
  assign rec = {rec_v, rec_late, 12'd0, rec_rule, rec_ivl};
  // the record's body changes only when it is taken: a gated bank, no hold muxes
  esc_greg #(W + 3) g_rec (.clk(clk), .rst_n(rst_n), .en(rec_load),
      .d({late[v_rule], v_rule, cnt[v_rule]}), .q({rec_late, rec_rule, rec_ivl}));
  wire _unused = &{1'b0, mon_b[30:W]};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prev <= 13'd0; rec_v <= 1'b0;
    end else begin
      prev <= psync;
      if (start || clr) rec_v <= 1'b0;
      else if (rec_load) rec_v <= 1'b1;
    end
  end

`ifdef ESC_MON_CHECK
  // formal/esc_mon.sby: unbounded, every input free (any pins, runs, starts, MON writes).
  // M5 a record holds until a start or a clear
  always @(posedge clk) if (f_pv && rst_n && $past(rst_n) && $past(rec_v)
                            && !$past(start) && !$past(clr))
    assert (rec_v && rec_rule == $past(rec_rule) && rec_late == $past(rec_late)
            && rec_ivl == $past(rec_ivl) && !$past(rec_load));
  // M6 a violation at an edge with no record and no start or clear is recorded, lowest rule
  always @(posedge clk) if (f_pv && rst_n && $past(rst_n) && $past(any_v) && !$past(rec_v)
                            && !$past(start) && !$past(clr))
    assert (rec_v && $past(rec_load)
            && (($past(early) | $past(late)) & ((4'd1 << rec_rule) - 4'd1)) == 4'd0);
  always @(posedge clk) if (f_pv) begin
    cover (rec_v && rec_late);
    cover (nv[0] == 8'd2);                       // (a saturated count is beyond any depth)
    cover ($past(rec_v) && $past(clr) && $past(mon_a[16]) && !rec_v);   // a clear alone
  end
`endif
endmodule
`default_nettype wire
