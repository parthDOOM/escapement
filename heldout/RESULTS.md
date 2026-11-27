# Held-out round: results

The held-out round tests a claim at the centre of Escapement: that the chip takes protocols it
was not designed for. Twenty-four protocols, each with a public specification and none of them
already in [`firmware/`](../firmware), were listed in [`pool.txt`](pool.txt), and eight were
drawn from that list at random rather than picked ([`README.md`](README.md)). The draw is
`random.Random("e3839cc985016dea828246d4a44981f5").sample(range(1, 25), 8)`, the seed passed as
a string, which gives pool numbers 19, 9, 14, 24, 1, 21, 16 and 6. The drawn list was sealed in
`sealed.txt`, and its hash was committed before any of the eight was written, so the choice
could not change afterwards.

All eight were written on 24 November 2026 on version 3.1 (commit `cb47436`): the chip, its
assembler and its instruction-set simulator stayed as they were at that commit. The
certificate checker was changed once, to fix the soundness bug the round found (Toolchain
findings, below). Each protocol went through the same steps.

1. **Firmware**, written from the protocol's public specification, with the section of every
   timing number cited in the program's header.
2. **A bus model** of the device on the other end of the wire (`sw/escmodels/`), written
   by a separate, fresh session. It was given the specification, the conventions of
   `sw/escmodels/` and the model's interface (its pins, its parameters and the checks it
   makes), and told not to read the firmware, the tests or the notes. It fails loudly on any
   timing the specification forbids, so a firmware bug cannot pass quietly. The interface
   came from the firmware's side, so that side chose what each model checks, though not how.
   The rules were instructions, not enforced, and what each session read is its own report;
   both sides came from the same kind of AI assistant, so a misreading they share is
   possible, which the outside judges (step 6) guard against where one exists. Each brief, and
   what was recorded when its model was delivered, is in [`BRIEFS.md`](BRIEFS.md).
3. **Tests on the instruction-set simulator (ISS)** against that model, each with negative
   controls: the same test with one timing moved outside the specification, or one bit wrong,
   which must fail.
4. **A timing certificate** from `escert`, the static checker that proves, for every possible
   input, that each of the program's timed events is admitted before its deadline.
5. **Co-simulation**: the chip's RTL in Icarus Verilog runs the same firmware against the same
   model, and its pin trace must match the ISS's.
6. **An outside judge** where one exists: a sigrok protocol decoder, written by other people,
   reads the pin waveform and must decode what the test intended.
7. **The FPGA bench**: the program on the chip's RTL in an FPGA, recorded with a logic
   analyser (below).

The firmware, models and tests were written with an AI coding assistant, so the hours in the
table are its logged working time.

## How to read the table

- **Hours** run from the first reading of the specification to a certified, co-simulated
  program. The bench is a separate step and is not counted.
- **Words** is the program's size. A context's program page holds 256 words; a context that
  *spans* also uses the page of its partner context, which must then be switched off, for up
  to 512.
- **Contexts** counts the chip's four hardware contexts that the program uses. A *paired*
  context also takes its switched-off partner's instruction slots, which doubles its
  instruction rate.
- **escert bounds** gives the certificate's result and its *minimum lead*: the fewest ticks
  by which any timed event is admitted ahead of its deadline, for any input.
- **Negative control** counts the deliberately broken variants of the program and whether
  the tests caught each one.
- **Cosim** reads MATCH when the RTL's pin trace equals the ISS's, cycle for cycle, and
  "protocol OK" when the bus model found no violation in it.
- **What the chip could not do** states each program's limits and the parts of the protocol
  left out, with the reason.

| Protocol | Spec (doc, rev) | Hours | Words | Contexts | escert bounds | ISS | Negative control | Cosim | sigrok judge | Bench | What the chip could not do |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |
| MIL-STD-1553B remote terminal | MIL-STD-1553B, 21 Sep 1978 (notice-free base text): 4.3.3 word formats and timing, 4.4.1 validation, 4.4.3 RT operation, 4.5.2.1.1.2 output crossings, 4.5.2.2.2.1 input tolerance | 2.4 | 462 (of 512: context 0 spans) | 2 (0 paired + span, 1 host) | CHECKED at 40, 42, ..., 50 MHz: min lead 8 (40 MHz) to 14 (50 MHz) ticks; the status SEND proved on time, lead 13 to 62 ticks | 30 tests pass: receive/transmit 1 to 32 words, broadcast, mode codes (legal, illegal, with data), superseding command, invalid words (parity, Manchester, sync, gap, too few, too many), other addresses, response time 8 to 11 us, 1000 ppm rate with 90 ns crossing distortion | 3, all caught: response at 13 us; cells one tick long (crossings 75 to 100 ns off); status parity wrong | MATCH + protocol OK, 36,000 cycles at 50 MHz (receive, transmit, parity fault, status, broadcast) | none (libsigrokdecode has no 1553 decoder) | 25 Nov, FPGA build at 25 MHz: bring-up stage 5 digest = model (tick 54,384). No bus controller on the bench, so no traffic: the terminal makes no edge | Full receiver tolerance: every crossing moved 150 ns at once (4.5.2.2.2.1) is not read (tested: 90 ns with 1000 ppm, 100 ns at the nominal rate; a single crossing moved 150 ns is inside the measured eye, about +/-175 ns at 40 MHz, but the model cannot move one crossing alone, so that is not tested). Samples are timed from each word's mid-sync crossing; re-timing every bit needs a WAITP per crossing (14 ticks apart at 40 MHz, closer than a re-arm), and the line unit's cell decoder, which re-times each bit, cannot start on the non-Manchester sync and ends a packet 1.5 bits after the last mid-bit edge. Not at the 25 MHz default clock (a half bit is 12.5 ticks): 40 to 50 MHz in 2 MHz steps. RT-to-RT receive, vector word, BIT word, dynamic bus control and shutdown not done |
| S/PDIF (IEC 60958) transmitter | EBU Tech 3250-E, 3rd ed. 2004 (the AES/EBU interface; the IEC 60958 framing, IEC 60958 itself not public): 2.2 frame, 2.3 biphase mark, 2.4 preambles, 2.5 validity, 4 channel status, Appendix 1 CRCC, 6.2.5.1 jitter | 0.55 | 255 (one page; built with span) | 1 (0, paired + span) | CHECKED at CELL 8 and 10: every event admitted; the start SENDs on time (lead 2040); the per-frame SEND not provable (MAY DROP, lead >= -671): escert bounds a packet's end only from below | 14 tests pass: 48/44.1/32 kHz and an unnamed rate, CELL 8 and 10, audio in order, validity 1 on underrun and with no host, two blocks with Z every 192 frames, the default channel status (CRCC checked by the model) and a host-written one | 3, all caught: wrong preamble (Y sent as X), odd parity, packet junction one tick late (0.026 UI) | MATCH + protocol OK, 36,000 cycles (30 frames) | sigrok `spdif`: 390+ audio words in order, preambles, B at frame 192; FLIP control fails | 25 Nov: stage 5 state = model (no prediction); analyser + sigrok `spdif`: two pushed words out as valid samples, in order, among about 380 silent sub-frames each capture, UI 320 ns; control refused | Clocks: 128 x fs x CELL exactly, CELL >= 8 (the line unit's one-cell minimum period): 48 kHz needs 49.152 MHz; at the 25 MHz default the frame rate is 24.4 kHz. Consumer channel status (IEC 60958-3) is not built in: no free authoritative source; the host may write any block. 16-bit samples only (one mailbox word a frame) |
| Microwire, 93C46 EEPROM read and write | Microchip DS21749H (2010), 93AA46A/B/C, 93LC46A/B/C, 93C46A/B/C: Table 1-2 A1-A15, Tables 1-3/1-4, sections 2 and 3 | 0.18 | 133 | 1 (0) | CHECKED at 16 to 50 MHz, x16 and x8: min lead 21 to 28 ticks | 19 tests pass: write and read back (x16, x8, 16/25/33/50 MHz, 0.5 to 1.56 MHz CLK), write protection before EWEN and after EWDS, ERASE/ERAL/WRAL, 93C and 93AA parts, a 3 ms busy poll, a timeout | 3, all caught: DI setup 40 ns (A7), CLK high 120 ns (A2), DO sampled before TPD (data wrong) | MATCH + protocol OK, 32,000 cycles (EWEN, writes with busy polls, reads, erase) | sigrok `microwire` + `eeprom93xx`: every instruction, address and word; FLIP control fails | 25 Nov: stage 5 digest = model; analyser (each command captured twice, merged on SK; DO the pull-up's 1) + sigrok `microwire` and `eeprom93xx`: EWEN, WRITE 0x05 0x1234, READ 0x05; every edge as the ISS made it; control refused. No EEPROM fitted | Sequential read (2.7) not used: one READ per word. Needs 16 MHz or more at the default 500 kHz CLK (a bit is 7 unpaired instructions) |
| DHT22 (AM2302) host | Aosong AM2302 Product Manual: Table 5 format, Table 6 timing, 7.1-7.4 | 0.17 | 128 | 1 (0) | CHECKED at 4, 25 and 50 MHz: min lead 20 ticks | 19 tests pass: the 7.2 worked example at 4 and 25 MHz, below zero and the range ends, the sensor at min, max and random timing, the 2 s interval kept (shortened in tests), parity, no response, a cut reply and a bit out of time each reported | 3, all caught: start low 0.7 ms (Tbe), reads closer than the sensor allows (7.1), a 0/1 threshold above TH1 (reading wrong) | MATCH + protocol OK, 90,000 cycles at 4 MHz (two reads, sensor at max timing) | sigrok `am230x`: both readings and checksums; FLIP control fails | 25 Nov: stage 5 digest = model; analyser: start signal 1 ms then released, every edge as the ISS made it; reply "no response" (no sensor; `am230x` prints nothing without one); control refused | Nothing found |
| BiSS C master (point to point) | iC-Haus BiSS C Protocol Description Rev D2: BiSS FRAME, Line Delay, Timeout, Bus Reset/Initialization, Table 10 Nos. 1-12; AN3 Rev E3 (CRC polynomials), AN21 (CRC examples) | 0.52 | 123 | 1 (0) | CHECKED at 25 MHz/250 kHz (min lead 15 ticks), 50 MHz/500 kHz (15), 16 MHz/181 kHz (10), 25 MHz/347 kHz (2) | 29 tests pass: 26-bit position at 3 clock pairs, line delay 0 to 9 us (1 to 3 clocks counted), processing time (10 us, 3 clocks, 39 us + 8 clocks), static 20/40 us and adaptive timeouts, 1/13/31-bit data with CDS, a bad CRC, no slave and no start bit each reported, the 40 us break after each error | 4, all caught: one CRC clock fewer (frame cut short), MA high 80 ns, next frame before the timeout ends, no break after a CRC error | MATCH + protocol OK, 26,000 cycles (line delay 1.3 us, 10 us busy, a CRC error) | none (libsigrokdecode has no BiSS or encoder-SSI decoder) | 25 Nov: stage 5 digest = model; analyser: MA at 250 kHz, 13 clocks with no ACK, MA left high, every edge as the ISS made it; replies "no ACK", 0 (no encoder); control refused | MA at most CLK_KHZ / 72 (347 kHz at 25 MHz, 694 kHz at 50 MHz; BiSS allows 10 MHz): each header clock is a timed look at SL and 8 instructions, and every MA edge is a timed event. Line delay is counted in whole clocks, not compensated within a bit. Control communication (CDM/CDS register access), multi-slave chains, data channels over 31 bits and CRCs other than 6 bits not done |
| PS/2 host (keyboard port) | IBM PS/2 Hardware Interface Technical Reference, Common Interfaces (84F9735, Oct 1990): Keyboard/Auxiliary Device Controller, Auxiliary Device and System Timings, Figures 7-9 (T1-T9); IBM Keyboards (101- and 102-Key), Oct 1990: Data Stream, Data Output, Data Input, Commands | 0.45 | 166 | 1 (0) | CHECKED at 4, 25 and 50 MHz: min lead 12 ticks | 27 tests pass: scan codes at min/typ/max/random keyboard timing and at 4, 25, 50 MHz; Echo, Reset (FA AA), Read ID (FA AB 83), Set indicators with its value; a scan code and a command interleaved; a slow host (mailbox read every 2 ms): CLK held low while the mailbox is full, nothing lost; a parity error, a stop bit 0, a byte stalled part way (reported, then received again), no line-control bit and no clocking for a request each reported | 3, all caught: request to send 45 us (more than 60 us needed), host DATA changed while CLK high, even parity (answered Resend) | MATCH + protocol OK, 24,000 cycles at 4 MHz (two scan codes, Echo) | sigrok `ps2`: ten bytes both ways, parity OK, decoded one window per byte (the decoder closes a byte only on a further CLK fall); FLIP control fails | 25 Nov: stage 5 state = model (no prediction); analyser: request to send (CLK low 105 us, DATA low at 100 us), the 15 ms wait, the 100 us inhibit, every edge as the ISS made it; reply "no device clock" (no keyboard); control refused | Nothing found. The commands themselves are left to the host (it sends the bytes and reads the answers); no resend on its own |
| SNES controller reader | No official public specification. TI (Harris) CD4021B data sheet SCHS024C (Oct 2003): Fig. 2, truth table, 5 V limits (P/S pulse 160 ns, P/S removal 280 ns, clock pulse 180 ns, 3 MHz, propagation 320 ns + transition 200 ns), the shift register in the pad; Nintendo US patent 5,207,426 (structure); SNES Development Manual Book I (Nintendo 1993, a leaked confidential document mirrored on archive.org as item `snes_manual1`, <https://archive.org/details/snes_manual1>): 2-24-8, 2-24-12, 2-28-8/9, used only for the 16-bit layout, signatures and the 17th-bit connection test | 0.20 | 28 | 1 (0) | CHECKED at 4, 25 and 50 MHz and at the fastest legal settings (latch 160 ns, half clock 320 ns at 50 MHz): min lead 28 ticks | 22 tests pass: every button alone and in combinations at the 5 V max/typ/min delays, 3030H (Start, Select, L, R), mouse and Super Scope signatures, 17th bit (connected), unplugged (reads 0, not connected), reads from 4 to 50 MHz and 160 ns latch / 320 ns half clock up to 12 us / 6 us | 3, all caught: latch 120 ns, clock low 120 ns, each bit sampled after its shifting edge (reading wrong) | MATCH + protocol OK, 4,500 cycles (two reads) | sigrok `spi` (no SNES decoder; nes_gamepad is the 8-bit NES pad): three reads, six key bytes; FLIP control fails | 25 Nov: stage 5 digest = model; analyser: latch 12 us, 17 clocks of 6 us phases within the CD4021B's 5 V limits, every edge as the ISS made it; reply 0 (no pad); control refused | Nothing found. One pad per program (a second DATA pin would be a second SAMPLE a bit) |
| M-Bus slave, data link layer | "The M-Bus: A Documentation" Rev. 4.8 (M-Bus Usergroup; the wired M-Bus specification at m-bus.com, after EN 1434-3 and IEC 870-5; EN 13757-2 is not public): 4.1, 4.4, 5.1-5.5, 6.1-6.3 | 0.70 | 313 (context 0 spans) | 1 (0) | CHECKED at 300, 2400, 9600 and 38400 baud, 4 to 50 MHz: min lead 12 ticks | 29 tests pass: SND_NKE and SND_UD answered E5h, REQ_UD2 answered RSP_UD with the host's CI 72h data (and a default header), SND_UD data to the host, test address 254 answered with the own address, broadcast 255 acted on and not answered, other addresses ignored, the address set by the host, six faulty telegrams (parity, stop bit, check sum, stop character, L fields, a 30-bit pause) unanswered and the next one answered, 300 to 38400 baud at 4 to 50 MHz, replies 22 bit times after the telegram | 3, all caught: reply after 4.5 bit times (11 needed), odd parity, a reply to 255 | MATCH + protocol OK, 200,000 cycles at 4 MHz, 9600 baud (SND_NKE, REQ_UD2) | sigrok `uart` (8E1) on both lines: every character in order, no parity or frame error; FLIP control fails | 25 Nov: stage 5 state = model (no prediction); live, with the Pico W as the master at logic level (no TSS721), sigrok `uart` 8E1 on both lines: SND_NKE answered E5h, REQ_UD2 answered with a 27-byte RSP_UD, nothing to address 7 or to a bad checksum, each reply 22.0 bit times after its telegram; control refused | The reply's characters are each started from RDTIME after the last stop bit (escert cannot bound an uncounted run of event-timed characters, and a counted one cannot hold a 128-character reply): each stop bit is about 30 ticks longer than a bit (0.3 % of a bit at 2400 baud and 25 MHz, 5 % at 38400 baud), which the master model accepts (it allows 0.5 bit). The line unit cannot send it (12-bit bit period: 2400 baud at 25 MHz needs 10,417 ticks; 127 stream bits a SEND). Telegrams of at most 64 characters received, responses of at most 120 bytes; 313 words, so the context spans. FCB ignored (allowed for a single-telegram slave); secondary addressing, 253, baud-rate change (CI B8h-BFh) and REQ_UD1 not done |

## Median hours

The median is 0.49 hours, the mean of the middle two of the eight: 0.17, 0.18, 0.20, 0.45,
0.52, 0.55, 0.70 and 2.4 hours. All eight took 5.2 hours together, not counting a ten-hour
pause. MIL-STD-1553B took longest, at 2.4 hours, because its receiver's timing was reworked
four times before escert could prove it and the model's distortion tests passed.

## What the round says about the instruction set

All eight protocols fit, each with stated limits, and no instruction was missing. The timed
events carried every waveform the chip makes and every bit it reads by time. An `EMIT`, `EMITX`
or `SAMPLE` names an absolute deadline tick, and the hardware performs it on exactly that tick,
however long the program took to get there; `WAITP`, a wait for a pin pattern with a
deadline, carried every input timed by its own edges (the 1553B sync, DHT22, PS/2, BiSS-C and
the M-Bus start bits). Each edge was placed where its specification puts it, and escert proved
every timed event of every program admitted on time.

The resources that ran short were registers and program words, not timing. With four
registers, the M-Bus slave keeps its state in the tape (a context's 256-word data memory) and
walks its buffers with a negative index that counts up to zero, so that the zero flag of the
add ends the loop. Because `LDLC`, which loads a loop count, takes only an immediate value, a
loop of variable length cannot be a counted one. Two programs needed more than 256 words and
span into a second page (1553B 462 words, M-Bus 313); the other six took 28 to 255 words,
S/PDIF the most. (Corrected on 27 November 2026: the round recorded 1553B as 458 words and S/PDIF as 262
words spanning two pages. Neither program nor the assembler has changed since the round's
commit `5c2d8df`, so those sizes were already out of date when they were written; S/PDIF
is still built with `.span`, a setting it does not need.)

Speed sets clock floors wherever a bit takes only a few instructions, because a context issues
one instruction every four ticks (its slot). The 1553B terminal needs 40 to 50 MHz, a paired
context and a span; Microwire needs 16 MHz at its default 500 kHz clock; the BiSS-C master
clocks MA at no more than the chip clock divided by 72, which is 347 kHz at 25 MHz against the
10 MHz BiSS allows. S/PDIF needs a clock of exactly 128 × fs × 8, so 49.152 MHz for 48 kHz.

The line unit, the shared serialiser that sends and receives a bit stream by itself, served
S/PDIF (biphase mark, as two cells a bit with NRZI) and the 1553B transmitter. It could
not serve the 1553B receiver, because its cell decoder cannot start on 1553B's sync, which is
not Manchester coded, nor the M-Bus transmitter, whose bit period at 2400 baud and 25 MHz
(10,417 ticks) exceeds its 12-bit period field.

The single link register, which rules out nested calls, never mattered. Calls were avoided
anyway, because escert joins the call sites of a routine and loses precision there (below).

Proving cost more time than writing. In five of the eight (1553B, S/PDIF, Microwire, BiSS-C and
M-Bus), the first firmware that worked on the ISS could not be proved by escert as written, and
was reshaped, with counted loops, timing re-anchored on the current time, and no calls, until
it could.

## The bench

The bench ran on 25 November 2026. The chip's RTL runs in a Tang Primer 25K FPGA (the
bitstream of 22 November), clocked at 25 MHz by a Raspberry Pi Pico W, which is also the
chip's host. A second Pico, running gusmanb's LogicAnalyzer firmware (v6.0), records two of the
chip's pins; it has headers on only two of its inputs. No device is on the other end of any
bus.

First, bring-up passed all six stages, and in its last stage 94 of 94 programs, the eight
held-out ones among them, matched the model and their certificates on the chip. Then
[`heldoutjudge.py`](../fpga/tangprimer25k/heldoutjudge.py) ran each program while the
analyser recorded it, and judged each capture in up to three ways:

- against the ISS, which ran the same program image with the same host words: every edge the
  analyser saw had to be one the ISS made, at the same time within two samples plus 100 ppm;
- against the protocol's own limits, such as the CD4021B's minimum pulse widths for the SNES
  pad;
- by sigrok, where a decoder can judge a run with no device: `spdif` decoded the S/PDIF stream,
  and `microwire` with `eeprom93xx` decoded the Microwire commands, merged from two captures of
  each command because the analyser has only two channels.

The M-Bus slave was tested live: the Pico W, bit-banging 8E1 characters at 2400 baud, was its
bus master at logic level, and sigrok's `uart` decoder read both lines. Every capture also had
a control with one pulse inverted, and every judge refused it. All seven captured programs
passed. The 1553B terminal makes no edge without a bus controller, which the bench does not
have, so it was judged by bring-up alone. The logs and
captures are in [`fpga/tangprimer25k/results/2026-11-25-heldout/`](../fpga/tangprimer25k/results/2026-11-25-heldout). The judge was later restructured and run again,
with the same result ([`results/2026-11-25-heldout-rerun/`](../fpga/tangprimer25k/results/2026-11-25-heldout-rerun)).

The bench does not test any physical layer (the 1553B transformer coupling, the M-Bus line
levels of a TSS721 transceiver, the RS-422 drivers of BiSS), and, M-Bus aside, it tests only
the chip's side of each bus.

## Toolchain findings

The round found no bug in the chip. It met the following limits and bugs in the tools.

**escert, a soundness bug, fixed in this round.** escert did not check that a line-unit
configuration is valid. A draft of the S/PDIF transmitter whose `CONFIG` faults on the ISS
(ADMIT/CONFIG at tick 24) was certified CHECKED. For one cell a bit, §6.6 of the
[semantics](../docs/11-SEMANTICS.md) asks for a bit period of at least 8 ticks and a sampling
point S with 4 ≤ S < the period. When escert learned the line unit (commit `fc6be39`, version
1.4), it modelled a `CONFIG`'s timing, its ownership rules and the pins it claims, but not the two
faults that §6.6 step 1 raises when a `CONFIG` completes: ADMIT/OWNER, for a configuration that
drives a pin its context does not own, and ADMIT/CONFIG, for an invalid one. Its list of faults
never named CONFIG, and no test certified a bad configuration, so the gap went unseen. escert
now checks both at every `CONFIG`; a configuration block that is not constant is checked at its
image value, and the certificate says so. Eleven new tests cover it, valid configurations and
invalid ones; on every invalid one, the escert of v3.1 found nothing while the ISS faults with
CONFIG or OWNER.

**escert, precision limits, worked around in the firmware.** These are cases where escert
cannot prove a program that is correct, so it reports a possible fault. escert was not changed
for them; the firmware was reshaped until it could be proved.

- *Joined call sites (1553B, S/PDIF).* When two call sites reach one routine in the same
  abstract state, escert joins them and loses the relation between the timer and the current
  time; the 1553B terminal was reported `PAST lead in [-187, 3]` on a path whose real lead is
  positive. A routine called from two sites can also join its callers inside it, and `RET`
  then returns to both: a loop that does not exist, never widened, runs into the state limit.
  The firmware refills its sample queue before each decode step, gives its other caller a
  different loop count, and S/PDIF has no calls at all.
- *A reception that may time out (1553B).* After a `GETL` that may time out, the line unit
  stays busy for escert with an unknown packet length, so the next `SEND`'s lead is unbounded.
  The firmware loops on `GETL` until the packet ends.
- *A packet's end (S/PDIF).* escert takes a `SEND`'s packet to end at or after its deadline,
  with no bound from its length, so a program that relies on one packet lasting its length,
  here to finish encoding the next, cannot be proved: escert reports MAY DROP. The ISS and the
  co-simulation show no packet dropped.
- *Loops that exit on an input (BiSS-C).* A loop whose exit depends on an input, such as the
  search for the ACK and the start bit, is widened at its head and loses its lead, whatever
  the clock. The firmware makes these loops counted (`LDLC`, `DJNZ`). Inside a counted loop, a
  `GETI` that waits for its sample also costs escert two ticks of precision a pass, though the
  processor is pinned to the sample each time; a `WAITT` on the queued edge before the `GETI`
  keeps it exact.
- *Long replies (M-Bus).* A run of characters sent by timed events in an uncounted loop, a
  reply of up to 128 characters and too long for a counted one, is widened at its head, and
  the lead is lost. After the reply, up to four queued events also made a later `SAMPLE` a
  possible ORDER fault. The firmware starts each character from the current time after the
  last stop bit, which makes each stop bit about 30 ticks longer, and waits for the reply's
  end.

**escasm.** An `.assert` message may not contain a comma, because the assembler splits it
there (met in the Microwire program).

**The simulated bring-up chip, a bug fixed in this round.** `sw/tests/demoboard_mock.py`, the
simulated chip that the bring-up tests run against, dropped bits 7:4 of `WRITE_CFG` byte 10,
which turn on pair mode. A paired program therefore ran unpaired there, and bring-up stage 5
reported the 1553B terminal, which is paired, as differing from the model. When pair mode
arrived (commit `d595f7d`, host protocol version 9), the simulated chip's version was raised to
9, but byte 10 was still decoded as the bank B bits alone, and the image it built never set
the pair bits. No shipped program had used pair mode before. It now keeps the bits from
version 9 on, and `test_bringup.py` passes all 48 tests.

**Bring-up's predictions.** Stage 5 compares the chip's state digest with the model's, which
needs the program to repeat a pattern within the model's search window. Three held-out programs
are bundled without a prediction and are checked for faults and final state only, as the LIN
and 1-Wire programs already are: the S/PDIF transmitter, whose channel status repeats only
every 192 frames, and the PS/2 host and the M-Bus slave, which look at their mailboxes on a
timer started afresh each time. For the M-Bus slave that timer runs every 4 bit times (41,668
ticks at 2400 baud), so the prediction held only to tick 41,679, while the bench's host link
stopped the chip at tick 91,598. The M-Bus slave is judged live on the bench instead.

**The outside judges.** These are features of sigrok's decoders, not of our tools, and the
judges work around them.

- `ps2` closes a byte only on the clock fall after its eleventh, the inhibit a PC adds, so a
  capture with bytes back to back loses framing for good; the judge decodes one window per
  byte.
- `spdif` marks each sub-frame's validity bit "V" or "E"; the "E" means "not valid", not an
  error. From some starting points in a running stream it never synchronises ("Unknown
  Preamble", then a Python ValueError), so the bench judge then starts it one sub-frame later.
- `am230x` prints nothing for a start signal that no sensor answers.
- sigrok has no decoder for MIL-STD-1553B, BiSS or the SNES pad (`ssi32` is a Bosch SPI
  message protocol, and `nes_gamepad` is the 8-bit NES pad).
