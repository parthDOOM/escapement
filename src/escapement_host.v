/*
 * escapement_host.v - host SPI interface (docs/12-HOST-PROTOCOL.md, normative).
 *
 * Copyright (c) 2026 Escapement contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * SPI mode 0, oversampled: CS_n/SCK/MOSI go through 2-flop synchronisers in the
 * core clock domain (no second clock domain; host keeps SCK high/low >= 4 clocks).
 * MOSI is sampled at each detected SCK rise; MISO advances right after the same
 * detected rise, so the host sees a stable bit for a whole SCK period.
 * Byte n of the MISO stream answers byte n-1 of the MOSI stream; byte 0 is status.
 * v1.1 (protocol version 6): WRITE_TAPE/READ_TAPE reuse the IMEM word engine with
 * mem_space = 1, and READ_DBG takes a 5-bit select ({1'b0, sel[4], ctx, sel[3:0]}).
 */
`default_nettype none
module escapement_host (
    input  wire         clk,
    input  wire         rst_n,
    // SPI pins (asynchronous)
    input  wire         cs_n_pin,
    input  wire         sck_pin,
    input  wire         mosi_pin,
    output wire         miso,
    // core control and configuration (held here, sampled by the core at start)
    output reg          start,
    output reg          stop,
    output reg  [3:0]   ctx_enable,
    output reg  [31:0]  entry_pcs,
    output reg  [7:0]   pin_owned,
    output reg  [15:0]  pin_owner,
    output reg  [7:0]   pin_od,
    output reg  [3:0]   irq_clear,
    input  wire         running,
    input  wire [7:0]   ctx_state,
    input  wire [3:0]   irq_pending,
    input  wire         fault_valid,
    // program memory host port (the top muxes it in while the core is stopped)
    output reg          mem_we,
    output reg  [7:0]   mem_addr,
    output reg  [31:0]  mem_wdata,
    output reg          mem_space,   // v1.1: 0 program store, 1 tape
    output wire         mem_re,      // D-29: a READ_IMEM/READ_TAPE is in progress
    input  wire [31:0]  mem_rdata,
    // debug read port of the core
    output reg  [1:0]   dbg_ctx,
    output reg  [4:0]   dbg_sel,
    input  wire [31:0]  dbg_rdata,
    // mailboxes (core host side)
    output reg  [3:0]   mbx_rx_push,
    output wire [127:0] mbx_rx_data,
    input  wire [3:0]   mbx_rx_full,
    output reg  [3:0]   mbx_tx_pop,
    input  wire [127:0] mbx_tx_data,
    input  wire [3:0]   mbx_tx_empty
);
  localparam [7:0] C_STATUS = 8'h00, C_WRITE_IMEM = 8'h10, C_READ_IMEM = 8'h11,
                   C_WRITE_CFG = 8'h12, C_WRITE_TAPE = 8'h13, C_READ_TAPE = 8'h14,
                   C_START = 8'h20, C_STOP = 8'h21,
                   C_READ_DBG = 8'h30, C_MBX_PUSH = 8'h40, C_MBX_POP = 8'h41,
                   C_IRQ_CLEAR = 8'h50, C_CLEAR_ERR = 8'h51, C_IGNORE = 8'hFF;
  localparam [3:0] VERSION = 4'h6;

  // ---------------------------------------------------------------- synchronisers
  reg [1:0] cs_s, sck_s, mosi_s;
  reg       sck_d;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cs_s   <= 2'b11;
      sck_s  <= 2'b00;
      mosi_s <= 2'b00;
      sck_d  <= 1'b0;
    end else begin
      cs_s   <= {cs_s[0], cs_n_pin};
      sck_s  <= {sck_s[0], sck_pin};
      mosi_s <= {mosi_s[0], mosi_pin};
      sck_d  <= sck_s[1];
    end
  end
  wire selected = ~cs_s[1];
  wire sck_rise = selected & sck_s[1] & ~sck_d;

  // ---------------------------------------------------------------- bit level
  reg  [2:0] bitcnt;
  reg  [6:0] rx_shift;
  reg  [7:0] tx_shift;
  wire       byte_done = sck_rise && bitcnt == 3'd7;
  wire [7:0] rx_byte   = {rx_shift, mosi_s[1]};

  reg        host_err;
  wire [7:0] status = {running, |irq_pending, fault_valid, host_err, VERSION};
  assign miso = tx_shift[7];

  // ---------------------------------------------------------------- frame state
  reg [7:0]  cmd;
  // The SRAMs need to read for the host only while a read command runs; the first word is
  // sampled a whole SPI byte (>= 64 clocks) after its address is set, every later one 4
  // bytes after the prefetch address. Outside a read the stopped chip's macros idle.
  assign mem_re = (cmd == C_READ_IMEM) | (cmd == C_READ_TAPE);
  reg [3:0]  idx;        // byte index within the frame, saturating at 15
  reg [1:0]  wcnt;       // byte position within a 32-bit word
  reg [31:0] word;       // assembled MOSI word, or MISO word being sent
  reg [1:0]  mctx;       // mailbox context

  assign mbx_rx_data = {4{word}};

  // MISO byte for the next byte slot, decided at byte_done (combinational).
  reg [7:0] next_tx;
  always @(*) begin
    next_tx = 8'h00;
    if (idx == 4'd0) begin
      if (rx_byte == C_STATUS) next_tx = ctx_state;
    end else begin
      case (cmd)
        C_STATUS: begin
          if (idx == 4'd1) next_tx = {mbx_rx_full, mbx_tx_empty};
          else if (idx == 4'd2) next_tx = {4'b0, irq_pending};
        end
        C_READ_IMEM, C_READ_TAPE, C_READ_DBG: begin
          if (idx == 4'd2) next_tx = (cmd != C_READ_DBG) ? mem_rdata[31:24] : dbg_rdata[31:24];
          else if (idx > 4'd2) begin
            case (wcnt)
              2'd0: next_tx = word[23:16];
              2'd1: next_tx = word[15:8];
              2'd2: next_tx = word[7:0];
              default: next_tx = (cmd != C_READ_DBG) ? mem_rdata[31:24] : 8'h00;
            endcase
          end
        end
        C_MBX_POP: begin
          if (idx == 4'd1) next_tx = {7'b0, ~mbx_tx_empty[rx_byte[1:0]]};
          else if (idx == 4'd2) next_tx = word[31:24];
          else if (idx == 4'd3) next_tx = word[23:16];
          else if (idx == 4'd4) next_tx = word[15:8];
          else if (idx == 4'd5) next_tx = word[7:0];
        end
        default: next_tx = 8'h00;
      endcase
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bitcnt <= 3'd0; rx_shift <= 7'd0; tx_shift <= 8'h00;
      cmd <= C_IGNORE; idx <= 4'd0; wcnt <= 2'd0; word <= 32'd0; mctx <= 2'd0;
      host_err <= 1'b0;
      start <= 1'b0; stop <= 1'b0; irq_clear <= 4'd0;
      ctx_enable <= 4'd0; entry_pcs <= 32'd0; pin_owned <= 8'd0; pin_owner <= 16'd0;
      pin_od <= 8'd0;
      mem_we <= 1'b0; mem_addr <= 8'd0; mem_wdata <= 32'd0; mem_space <= 1'b0;
      dbg_ctx <= 2'd0; dbg_sel <= 5'd0;
      mbx_rx_push <= 4'd0; mbx_tx_pop <= 4'd0;
    end else begin
      // single-cycle strobes
      start <= 1'b0; stop <= 1'b0; irq_clear <= 4'd0; mem_we <= 1'b0;
      mbx_rx_push <= 4'd0; mbx_tx_pop <= 4'd0;
      // a word was written at this edge: advance for the next one
      if (mem_we) mem_addr <= mem_addr + 8'd1;

      if (!selected) begin
        bitcnt   <= 3'd0;
        idx      <= 4'd0;
        wcnt     <= 2'd0;
        cmd      <= C_IGNORE;
        tx_shift <= status;          // first MISO byte of the next frame
      end else if (sck_rise) begin
        bitcnt   <= bitcnt + 3'd1;
        rx_shift <= rx_byte[6:0];
        tx_shift <= byte_done ? next_tx : {tx_shift[6:0], 1'b0};
        if (byte_done) begin
          if (idx != 4'd15) idx <= idx + 4'd1;
          if (idx == 4'd0) begin
            // ------------------------------------------------ command byte
            cmd  <= rx_byte;
            wcnt <= 2'd0;
            mem_space <= (rx_byte == C_WRITE_TAPE) || (rx_byte == C_READ_TAPE);
            case (rx_byte)
              C_STATUS, C_WRITE_IMEM, C_READ_IMEM, C_WRITE_CFG, C_READ_DBG,
              C_MBX_PUSH, C_MBX_POP, C_IRQ_CLEAR, C_WRITE_TAPE, C_READ_TAPE: ;
              C_START: if (running) host_err <= 1'b1; else start <= 1'b1;
              C_STOP: stop <= 1'b1;
              C_CLEAR_ERR: host_err <= 1'b0;
              default: begin host_err <= 1'b1; cmd <= C_IGNORE; end
            endcase
            if ((rx_byte == C_WRITE_IMEM || rx_byte == C_WRITE_CFG ||
                 rx_byte == C_WRITE_TAPE) && running)
              host_err <= 1'b1;
          end else begin
            // ------------------------------------------------ payload bytes
            case (cmd)
              C_WRITE_IMEM, C_WRITE_TAPE: begin
                if (idx == 4'd1) mem_addr <= rx_byte;
                else begin
                  word <= {word[23:0], rx_byte};
                  wcnt <= wcnt + 2'd1;
                  if (wcnt == 2'd3) begin
                    if (running) host_err <= 1'b1;
                    else begin
                      mem_we    <= 1'b1;
                      mem_wdata <= {word[23:0], rx_byte};
                    end
                  end
                end
              end
              C_READ_IMEM, C_READ_TAPE: begin
                if (idx == 4'd1) mem_addr <= rx_byte;          // read starts now
                else if (idx == 4'd2) begin
                  word     <= mem_rdata;                        // first word
                  mem_addr <= mem_addr + 8'd1;                  // prefetch next
                  wcnt     <= 2'd0;
                end else begin
                  wcnt <= wcnt + 2'd1;
                  if (wcnt == 2'd3) begin
                    word     <= mem_rdata;
                    mem_addr <= mem_addr + 8'd1;
                  end
                end
              end
              C_WRITE_CFG: if (!running) begin
                case (idx)
                  4'd1: ctx_enable       <= rx_byte[3:0];
                  4'd2: entry_pcs[7:0]   <= rx_byte;
                  4'd3: entry_pcs[15:8]  <= rx_byte;
                  4'd4: entry_pcs[23:16] <= rx_byte;
                  4'd5: entry_pcs[31:24] <= rx_byte;
                  4'd6: pin_owned        <= rx_byte;
                  4'd7: pin_owner[15:8]  <= rx_byte;
                  4'd8: pin_owner[7:0]   <= rx_byte;
                  4'd9: pin_od           <= rx_byte;
                  default: ;
                endcase
              end
              C_READ_DBG: begin
                if (idx == 4'd1) begin
                  dbg_ctx <= rx_byte[5:4];
                  dbg_sel <= {rx_byte[6], rx_byte[3:0]};
                end else if (idx == 4'd2) begin
                  word <= dbg_rdata;
                  wcnt <= 2'd0;
                end else if (wcnt != 2'd3) wcnt <= wcnt + 2'd1;
              end
              C_MBX_PUSH: begin
                if (idx == 4'd1) mctx <= rx_byte[1:0];
                else if (idx <= 4'd5) begin
                  word <= {word[23:0], rx_byte};
                  if (idx == 4'd5) begin
                    if (mbx_rx_full[mctx]) host_err <= 1'b1;
                    else mbx_rx_push[mctx] <= 1'b1;
                  end
                end
              end
              C_MBX_POP: begin
                if (idx == 4'd1) begin
                  mctx <= rx_byte[1:0];
                  word <= mbx_tx_data[32*rx_byte[1:0] +: 32];
                  if (!mbx_tx_empty[rx_byte[1:0]]) mbx_tx_pop[rx_byte[1:0]] <= 1'b1;
                end
              end
              C_IRQ_CLEAR: if (idx == 4'd1) irq_clear <= rx_byte[3:0];
              default: ;
            endcase
          end
        end
      end
    end
  end
endmodule
`default_nettype wire
