# Host protocol (normative)

The SPI command set between a host and Escapement. Protocol version 9. Implemented by
`src/escapement_host.v`, and driven by `sw/eschost` (PC and cocotb) and the RP2350
MicroPython host on the Tiny Tapeout demo board (`sw/rp2350`). Version 1.1 adds
WRITE_TAPE and READ_TAPE (0x13, 0x14) for the tape ([11-SEMANTICS](11-SEMANTICS.md) §7.6)
and a 5-bit READ_DBG select that reaches the digest and capture registers (sel 16–19).
Version 1.2 adds four optional WRITE_CFG bytes for pin bank B
([11-SEMANTICS](11-SEMANTICS.md) §5). Version 1.3 lets READ_TAPE run during a run and
adds TAPE_ACK (0x15), for streaming capture ([11-SEMANTICS](11-SEMANTICS.md) §6.5).
Versions 1.4 to 1.6 change no command and still report protocol version 8: the line unit
is used from firmware, and the host sees it only through READ_DBG selects 21, 25 and 26.
Version 1.7 reports protocol version 9: WRITE_CFG byte 10 bits 7:4 set `ctx_pair`, pair
mode ([11-SEMANTICS](11-SEMANTICS.md) §3). An older chip ignores those bits and would run a
paired image unpaired, at half its context's rate, so its timing certificate would not
hold: `sw/eschost` refuses to load a paired image onto a chip that reports a version below 9.
Version 1.7 also adds live reload: CTX_STOP and CTX_START (0x22, 0x23) and WRITE_IMEM during
a run, so one context can be stopped, given a new program and restarted while the others
keep running (below).
Version 2 (program pages, S-A) reports protocol version 10. WRITE_CFG byte 14 sets each
context's page, and IMEM_PAGE (0x24) selects the page that WRITE_IMEM and READ_IMEM
address ([11-SEMANTICS](11-SEMANTICS.md) §3). `sw/eschost` refuses to load a paged image
onto a chip that reports a version below 10, and sends byte 14 and IMEM_PAGE only to chips
with pages, so the frames an older chip receives are unchanged.

## Physical layer

| Pin | Signal |
| --- | --- |
| `ui_in[0]` | CS_n (active low, frames a transaction) |
| `ui_in[1]` | SCK |
| `ui_in[2]` | MOSI |
| `uo_out[0]` | MISO |
| `uo_out[1]` | IRQ: any context IRQ pending, or a fault recorded |
| `uo_out[2]` | RUN: the core is running |

`ui_in[7:3]` and `uo_out[7:3]` are not part of the host interface: they are protocol pin
bank B.

SPI **mode 0** (CPOL 0, CPHA 0): MSB first; the host samples MISO and the chip samples
MOSI on SCK rising edges. The chip oversamples CS_n, SCK and MOSI through 2-flop
synchronisers in the core clock domain (no second clock domain).

Host timing requirements, in core clock periods T (40 ns at 25 MHz):

- SCK high ≥ 4T and SCK low ≥ 4T, so f_SCK ≤ f_clk/8 (3.125 MHz at 25 MHz; 1 MHz is typical).
- CS_n falling to the first SCK rise ≥ 4T. The last SCK fall to CS_n rising ≥ 4T.
- CS_n high between frames ≥ 4T.

MISO changes within 4T after the SCK rising edge that ends a bit. Its first bit is valid
4T after CS_n falls.

## Framing

A frame is CS_n low, then one command byte, then command-specific bytes. Byte n of the
MISO stream is the response to byte n−1 of the MOSI stream; **byte 0 of every response
is the status byte**:

| Bit | Meaning |
| --- | --- |
| 7 | running |
| 6 | IRQ pending on any context |
| 5 | first-fault register valid |
| 4 | host error (sticky; see CLEAR_ERR) |
| 3:0 | protocol version: `0x9` (v1.7; v1.3 to v1.6 chips report `0x8`, v1.2 `0x7`, v1.1 `0x6`, v1.0 `0x5`) |

Raising CS_n ends the frame at any point; a partial word is discarded. Bytes after the
end of a command's payload are ignored (response 0x00). An unknown command sets host
error and ignores the rest of the frame.

## Commands

Multi-byte values are big-endian. "dummy" is a byte the host sends (any value) while
the chip prepares data; its response is 0x00.

| Code | Name | MOSI after command | MISO (after the status byte) | Notes |
| --- | --- | --- | --- | --- |
| 0x00 | STATUS | 3 × dummy | ctx_state, {rx_full[3:0], tx_empty[3:0]}, irq_pending[3:0] | |
| 0x10 | WRITE_IMEM | addr, then words (4 bytes each) | zeros | Writes addr, addr+1, … in the host's page (v2, IMEM_PAGE; the address wraps inside the page). While stopped as in v1.6; during a run (v1.7) each word is written in a free fetch slot (live reload, below) |
| 0x11 | READ_IMEM | addr, dummy, then 4 dummy bytes per word | words from addr, addr+1, … in the host's page (v2) | Readback verification of a loaded image; while stopped (during a run the words read are undefined) |
| 0x12 | WRITE_CFG | ctx_enable, entry0, entry1, entry2, entry3, pin_owned[7:0], pin_owner[15:8], pin_owner[7:0], pin_od, then optionally ctx_bank (bits 3:0) and ctx_pair (bits 7:4, v1.7), pin_owned[12:8], pin_owner[25:24], pin_owner[23:16], then optionally (v2) ctx_page (bits 2k+1:2k: context k's page) | zeros | 9, 13 or 14 bytes; only while stopped. Takes effect at the next START. Bytes 10–13 (v1.2) configure bank B, and byte 10's high nibble pair mode (v1.7); a 9-byte frame leaves them unchanged (0 after reset), a v1.1 chip ignores them, and a v1.2 to v1.6 chip ignores byte 10 bits 7:4. Byte 14 (v2): a shorter frame leaves it unchanged (0 after reset: every context on page 0), and an older chip ignores it |
| 0x13 | WRITE_TAPE | addr, then words (4 bytes each) | zeros | As WRITE_IMEM, into the tape; only while stopped, else host error and no write |
| 0x14 | READ_TAPE | addr, dummy, then 4 dummy bytes per word | words from addr, addr+1, … | As READ_IMEM, from the tape, at any time (v1.3; below) |
| 0x15 | TAPE_ACK | rptr | zeros | v1.3: the host has read the capture records before `rptr`; sets the capture unit's `rptr` at the end of the byte ([11-SEMANTICS](11-SEMANTICS.md) §6.5). Allowed while running or stopped |
| 0x20 | START | — | — | One-cycle core `start` pulse at the end of the command byte; ignored (host error) while running |
| 0x21 | STOP | — | — | One-cycle core `stop` pulse |
| 0x22 | CTX_STOP | ctx mask (bits 3:0) | zeros | v1.7: stops the masked contexts at the end of the mask byte; during a run only, else host error |
| 0x23 | CTX_START | ctx mask (bits 3:0) | zeros | v1.7: restarts the masked contexts from their entry points; during a run only, and each masked context enabled and not RUNNING, else host error and none starts |
| 0x24 | IMEM_PAGE | page (bits 1:0) | zeros | v2: the program-store page the following WRITE_IMEM and READ_IMEM address (0 after reset). Running or stopped; while a live word waits, host error and no change (as for a new address) |
| 0x30 | READ_DBG | {1'b0, sel[4], ctx[1:0], sel[3:0]}, dummy, 4 dummy | dbg_rdata (4 bytes) | Captured at the end of the select byte; the map is in 11-SEMANTICS §10 (sel 16 digest, 17 capture status, 18 capture config, 19 overflow tick, 20 capture `rptr`; v1.4: 21 line unit status, 25 its end tick, 26 its owner and mode). Selects 0–8 read during a run show the context's registers as of its last commit slot, at most four cycles old |
| 0x40 | MBX_PUSH | ctx, 4-byte word | zeros | Push to that context's RX mailbox; host error if full (word dropped) |
| 0x41 | MBX_POP | ctx, dummy, 4 dummy | valid (0x01 or 0x00), word (4 bytes) | Pops the TX mailbox head if nonempty |
| 0x50 | IRQ_CLEAR | ctx mask (bits 3:0) | zeros | Clears irq_pending for the masked contexts |
| 0x51 | CLEAR_ERR | — | — | Clears host error |

## Tape reads during a run (v1.3)

READ_TAPE fetches each word in a cycle in which neither the core nor the capture unit
uses the tape port ([11-SEMANTICS](11-SEMANTICS.md) §10, `tape_busy`), so a host read
never delays an instruction or a capture record. A word's fetch is requested when its
address is set (the address byte, then each prefetch) and must happen before the word is
sent, at least one SPI byte later (64 core cycles at the fastest SCK). The port is free
in almost every cycle; only firmware that does nothing but LDT and STT in all four
contexts for that whole time could keep it busy. If a word was not fetched in time,
**host error** is set and the word sent is undefined: the host clears the error and
reads again. Reads while stopped behave as in v1.2.

**Streaming capture.** The firmware arms the capture unit with CAPT bit 26 (stream) over
a region `[base, lim)`. The host then repeats, during the run:

1. READ_DBG sel 17 (capture status) for `ptr`, and the flags `ovf`, `full`, `wrapped`.
2. READ_TAPE of the words from its own `rptr` up to `ptr`, around the region.
3. TAPE_ACK with the new `rptr` (the old `rptr` plus the number of words read, around
   the region).

The capture unit never writes over a word between `rptr` and `ptr`. If the host falls
behind for long enough that the region and the staging FIFO fill, generation stops with
`ovf` set and `ovf_tick` the tick of the first lost record ([11-SEMANTICS](11-SEMANTICS.md)
§6.5). The rate a host can sustain is set by its SPI link: each record costs 4 bytes of
READ_TAPE plus a share of the status read and TAPE_ACK.

## Live reload (v1.7)

One context `k` gets a new program while the others run on, untouched:

1. CTX_STOP with bit `k`. At the end of the mask byte `k` stops as at STOP: its commit at
   that edge is discarded, its FIFO and checker are cleared, its pins are released, a line
   unit job it owns ends. No fault is recorded and no IRQ is raised.
2. WRITE_IMEM of the changed words. During a run a completed word waits in the host
   interface and is written in the next cycle whose fetch slot belongs to a context that is
   not RUNNING, so no running context's fetch is delayed. With `k` stopped that is within 4
   cycles, long before the next word (64 cycles at the fastest SCK). A word can wait longer
   only while every context is RUNNING; then a second word, or a command that moves the
   address (READ_TAPE, another WRITE_IMEM), sets host error and is not carried out, and the
   waiting word is still written where it was aimed. WRITE_TAPE stays stopped-only.
3. CTX_START with bit `k`. The start waits for the edge before `k`'s first slot of a frame
   (slot `k`; a paired context, slot min(k, k^2)): `k` restarts there with its registers
   reset, its timer at the frame's first tick, PC at its entry point. It sees exactly what a
   run started at that frame shows it, so its timing certificate holds unchanged.
4. STATUS: host error clear, `k` RUNNING.

Pins, bank and pairing stay as configured: the new program must use the same configuration
(WRITE_CFG stays stopped-only). The program words of running contexts must not change.
`sw/eschost` checks both before sending anything (`eschost.live.plan_reload`: the changed
words are outside every other enabled context's reach, and escert certifies the new program),
and `Host.reload` sends steps 1 to 4. READ_IMEM cannot verify the words during the run; the
host checks host error instead, and can READ_IMEM after the next STOP. The execution digest
folds every commit, so it depends on when the reload happened.

## Typical session

1. STATUS, and check version 0x9 (0x8 also runs any image without pair mode).
2. STOP.
3. WRITE_IMEM with the image, then READ_IMEM to compare (verification is in host software).
   WRITE_TAPE / READ_TAPE likewise for any initial tape contents (the SRAM powers up
   undefined).
4. WRITE_CFG.
5. START.
6. During the run: MBX_PUSH/POP, READ_DBG for telemetry and checker status, and
   IRQ_CLEAR when IRQ asserts.
7. STOP. Then READ_TAPE for capture records (or stream them during step 6; above), and
   READ_DBG sel 16 for the execution
   digest: once every context has stopped, it is final and can be compared with the
   ISS's prediction for the same image (11-SEMANTICS §10.1).

A deselected Tiny Tapeout design is powered down and loses everything, so the host
repeats the session after every reselect.
