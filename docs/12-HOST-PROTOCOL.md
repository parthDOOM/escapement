# Host protocol v1.1 (normative)

The SPI command set between a host and Escapement. Protocol version 6. Implemented by
`src/escapement_host.v`, and driven by `sw/eschost` (PC and cocotb) and the RP2350
MicroPython host on the Tiny Tapeout demo board (`sw/rp2350`). Version 1.1 adds
WRITE_TAPE and READ_TAPE (0x13, 0x14) for the tape ([11-SEMANTICS](11-SEMANTICS.md) §7.6)
and a 5-bit READ_DBG select that reaches the digest and capture registers (sel 16–19).

## Physical layer

| Pin | Signal |
| --- | --- |
| `ui_in[0]` | CS_n (active low, frames a transaction) |
| `ui_in[1]` | SCK |
| `ui_in[2]` | MOSI |
| `uo_out[0]` | MISO |
| `uo_out[1]` | IRQ: any context IRQ pending, or a fault recorded |
| `uo_out[2]` | RUN: the core is running |

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
| 3:0 | protocol version, `0x6` (v1.0 chips report `0x5`) |

Raising CS_n ends the frame at any point; a partial word is discarded. Bytes after the
end of a command's payload are ignored (response 0x00). An unknown command sets host
error and ignores the rest of the frame.

## Commands

Multi-byte values are big-endian. "dummy" is a byte the host sends (any value) while
the chip prepares data; its response is 0x00.

| Code | Name | MOSI after command | MISO (after the status byte) | Notes |
| --- | --- | --- | --- | --- |
| 0x00 | STATUS | 3 × dummy | ctx_state, {rx_full[3:0], tx_empty[3:0]}, irq_pending[3:0] | |
| 0x10 | WRITE_IMEM | addr, then words (4 bytes each) | zeros | Writes addr, addr+1, …; only while stopped, else host error and no write |
| 0x11 | READ_IMEM | addr, dummy, then 4 dummy bytes per word | words from addr, addr+1, … | Readback verification of a loaded image |
| 0x12 | WRITE_CFG | ctx_enable, entry0, entry1, entry2, entry3, pin_owned, pin_owner[15:8], pin_owner[7:0], pin_od | zeros | 9 bytes; only while stopped. Takes effect at the next START |
| 0x13 | WRITE_TAPE | addr, then words (4 bytes each) | zeros | As WRITE_IMEM, into the tape; only while stopped, else host error and no write |
| 0x14 | READ_TAPE | addr, dummy, then 4 dummy bytes per word | words from addr, addr+1, … | As READ_IMEM, from the tape (e.g. capture records after STOP) |
| 0x20 | START | — | — | One-cycle core `start` pulse at the end of the command byte; ignored (host error) while running |
| 0x21 | STOP | — | — | One-cycle core `stop` pulse |
| 0x30 | READ_DBG | {1'b0, sel[4], ctx[1:0], sel[3:0]}, dummy, 4 dummy | dbg_rdata (4 bytes) | Captured at the end of the select byte; the map is in 11-SEMANTICS §10 (sel 16 digest, 17 capture status, 18 capture config, 19 overflow tick) |
| 0x40 | MBX_PUSH | ctx, 4-byte word | zeros | Push to that context's RX mailbox; host error if full (word dropped) |
| 0x41 | MBX_POP | ctx, dummy, 4 dummy | valid (0x01 or 0x00), word (4 bytes) | Pops the TX mailbox head if nonempty |
| 0x50 | IRQ_CLEAR | ctx mask (bits 3:0) | zeros | Clears irq_pending for the masked contexts |
| 0x51 | CLEAR_ERR | — | — | Clears host error |

## Typical session

1. STATUS, and check version 0x6.
2. STOP.
3. WRITE_IMEM with the image, then READ_IMEM to compare (verification is in host software).
   WRITE_TAPE / READ_TAPE likewise for any initial tape contents (the SRAM powers up
   undefined).
4. WRITE_CFG.
5. START.
6. During the run: MBX_PUSH/POP, READ_DBG for telemetry and checker status, and
   IRQ_CLEAR when IRQ asserts.
7. STOP. Then READ_TAPE for capture records, and READ_DBG sel 16 for the execution
   digest: once every context has stopped, it is final and can be compared with the
   ISS's prediction for the same image (11-SEMANTICS §10.1).

A deselected Tiny Tapeout design is powered down and loses everything, so the host
repeats the session after every reselect.
