/*
 * esc_mon.v - Escapement bus timing monitor (v3 prototype, 2026-11-13).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Checks the timing of traffic the chip only watches: RULES interval rules on the
 * synchronised pins, each "after an edge of pin A, the next edge of pin B comes no earlier
 * than MIN and no later than MAX ticks". A rule starts on its from-edge and ends on its
 * to-edge; an interval below MIN, or MAX ticks with no to-edge, is a violation, counted per
 * rule and recorded (the first one since the unit was armed: rule, kind, tick, interval).
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
 * (the same view as CAPT). Prototype: configuration by a write port; the ISA (MON / GETM)
 * and the tape-loaded rule table come with the design note.
 */
`default_nettype none
module esc_mon #(
    parameter integer RULES = 4,
    parameter integer W     = 16                 // interval counter and limit width (ticks)
) (
    input  wire          clk,
    input  wire          rst_n,
    input  wire          start,                  // run initialisation at this edge
    input  wire          run,                    // the chip is running during this cycle
    input  wire [31:0]   now,
    input  wire [12:0]   psync,                  // synchronised physical pins (both banks)
    // configuration: one rule per write, at most one write per edge
    input  wire          cfg_we,
    input  wire [1:0]    cfg_rule,
    input  wire          cfg_en,
    input  wire [3:0]    cfg_from, cfg_to,       // pin index 0..12
    input  wire [1:0]    cfg_fedge, cfg_tedge,   // 1 rise, 2 fall, 3 any (0: never)
    input  wire [W-1:0]  cfg_min, cfg_max,
    input  wire          clr,                    // clear counts and the record at this edge
    // status
    output wire [8*RULES-1:0] counts,            // violations per rule, saturating at 255
    output reg           rec_v,                  // a violation has been recorded
    output reg  [1:0]    rec_rule,
    output reg           rec_late,               // 0: below MIN, 1: MAX passed
    output reg  [31:0]   rec_tick,
    output reg  [W-1:0]  rec_ivl,
    output wire          irq                     // a violation at this edge
);
  reg [12:0] prev;
  wire [12:0] rise = psync & ~prev;
  wire [12:0] fall = ~psync & prev;

  function ev;                                   // the edge of a rule's pin in this cycle
    input [12:0] r, f; input [3:0] pin; input [1:0] kind;
    ev = (kind[0] & r[pin]) | (kind[1] & f[pin]);
  endfunction

  reg           en    [0:RULES-1];
  reg [3:0]     from  [0:RULES-1], to [0:RULES-1];
  reg [1:0]     fedge [0:RULES-1], tedge [0:RULES-1];
  reg [W-1:0]   mn    [0:RULES-1], mx [0:RULES-1];
  reg           armed [0:RULES-1];
  reg [W-1:0]   cnt   [0:RULES-1];
  reg [7:0]     nv    [0:RULES-1];

  wire [RULES-1:0] early, late;
  genvar g;
  generate for (g = 0; g < RULES; g = g + 1) begin : g_r
    wire f_ev = en[g] & ev(rise, fall, from[g], fedge[g]);
    wire t_ev = en[g] & ev(rise, fall, to[g], tedge[g]);
    // an interval ends on the to-edge (checked against MIN before any re-arm at the same
    // edge), or MAX ticks after the from-edge with no to-edge
    assign early[g] = run & armed[g] & t_ev & (mn[g] != {W{1'b0}}) & (cnt[g] < mn[g]);
    assign late[g]  = run & armed[g] & ~t_ev & (mx[g] != {W{1'b0}}) & (cnt[g] == mx[g]);
    assign counts[8*g +: 8] = nv[g];
    always @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
        en[g] <= 1'b0; from[g] <= 4'd0; to[g] <= 4'd0; fedge[g] <= 2'd0; tedge[g] <= 2'd0;
        mn[g] <= {W{1'b0}}; mx[g] <= {W{1'b0}}; armed[g] <= 1'b0; cnt[g] <= {W{1'b0}};
        nv[g] <= 8'd0;
      end else if (start) begin
        en[g] <= 1'b0; armed[g] <= 1'b0; cnt[g] <= {W{1'b0}}; nv[g] <= 8'd0;
      end else begin
        if (run) begin
          if (f_ev) begin armed[g] <= 1'b1; cnt[g] <= {W{1'b0}}; end
          else if (armed[g] & (t_ev | late[g])) armed[g] <= 1'b0;
          else if (armed[g] & (cnt[g] != {W{1'b1}})) cnt[g] <= cnt[g] + 1'b1;
          if ((early[g] | late[g]) && nv[g] != 8'hFF) nv[g] <= nv[g] + 8'd1;
        end
        if (clr) nv[g] <= 8'd0;
        if (cfg_we && cfg_rule == g) begin
          en[g] <= cfg_en; from[g] <= cfg_from; to[g] <= cfg_to;
          fedge[g] <= cfg_fedge; tedge[g] <= cfg_tedge; mn[g] <= cfg_min; mx[g] <= cfg_max;
          armed[g] <= 1'b0; cnt[g] <= {W{1'b0}};
        end
      end
    end
  end endgenerate

  // the first violation since start or clr: the lowest-numbered rule wins a tie
  wire          any_v = |(early | late);
  reg  [1:0]    v_rule;
  integer i;
  always @(*) begin
    v_rule = 2'd0;
    for (i = RULES - 1; i >= 0; i = i - 1) if (early[i] | late[i]) v_rule = i[1:0];
  end
  assign irq = any_v;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prev <= 13'd0; rec_v <= 1'b0; rec_rule <= 2'd0; rec_late <= 1'b0;
      rec_tick <= 32'd0; rec_ivl <= {W{1'b0}};
    end else begin
      prev <= psync;
      if (start || clr) rec_v <= 1'b0;
      else if (any_v && !rec_v) begin
        rec_v <= 1'b1; rec_rule <= v_rule; rec_late <= late[v_rule];
        rec_tick <= now; rec_ivl <= cnt[v_rule];
      end
    end
  end
endmodule
`default_nettype wire
