# Held-out round: the bus-model briefs

Each held-out protocol was tested against a bus model of the device on the other end of its
wire (`sw/escmodels/`). Each model was written by a separate, fresh session of an AI coding
assistant, not the one writing the firmware. This file gives, for each, what that session
was told and given (its brief), and what the firmware's side recorded when the model was
delivered. It is copied verbatim from the round's log, a private working file, except that
the private notes' folder is named as "the private notes".

What this shows and what it does not:

- Each model session was told not to read the firmware, the tests or the notes, and given
  only the model conventions (`board.py`, `__init__.py`, and `uart.py` and `ps2.py` as style
  examples), the cited specification and an interface.
- The interface came from the firmware's side: it names the model's pins and parameters and
  lists the checks the model makes. So that side chose *what* each model checks, though not
  *how*.
- The rules were instructions, not a sandbox. What a session read is its own report.
- Both sides were the same kind of assistant reading the same documents, so they could
  share a misreading. The outside judges written by other people (sigrok's decoders, six of
  the eight) guard against that; the 1553B terminal and the BiSS C master have none.

Times are IST, from the laptop's clock. "Subagent" means a fresh assistant session started
for that one model; "the harness" is the tool that runs it.

## MIL-STD-1553B remote terminal

**Brief.**

- Rule: never read `firmware/`, `test/`, `sw/tests/`, the private notes, or any repo file not listed; no repo search.
- May read: `sw/escmodels/board.py`, `sw/escmodels/__init__.py`, style examples `sw/escmodels/uart.py`, `sw/escmodels/ps2.py`.
- Specification: MIL-STD-1553B, 21 Sep 1978 (ASSIST copy, https://ibiblio.org/apollo/Shuttle/MIL-STD-1553B.pdf) and its OCR text in this session's scratchpad (same document); sections 4.3.3, 4.4.1, 4.4.3, 4.5.2.1.1.2, 4.5.2.1.2.1 pointed to.
- Interface: `Bc1553(txa=0, txb=1, rxa=4, rxb=5, tick_ns=25.0, messages=(), echo=True, distortion_ns=0.0, rate_ppm=0.0, seed=1)`: a BC + bus + the RT's transceiver (TXA/TXB in, RXA/RXB out, TXA==TXB = transmitter off); message script with fault injections (parity, manchester, sync, gap_us, truncate); records `responses[i]` (status, data, response_us, status_ok), `words_rt`, `violations`; checks the RT's output crossings (+/-25 ns), contiguity, response time 4-12 us, 14 us timeout, unasked transmission, collisions, 800 us fail-safe. It decides nothing about what the RT should answer.

**Delivered** (the firmware's side's note).

- Model delivered ~11:05 (`sw/escmodels/mil1553.py`, its `_selftest()` passes; one import line in
  `__init__.py`). The firmware writer did not read its source. Once, early, I grepped it for
  `def `/`class ` lines only (names of the public methods) to call it; after that, only its
  documented interface and its pins (a waveform check of the RX pins in a run, as a scope would
  see them, to learn how its distortion option behaves: each crossing within +/-d of the ideal,
  each interval within +/-d).

## S/PDIF transmitter

**Brief.**

- Same rules as subagent 1: never read `firmware/`, `test/`, `sw/tests/`, the private notes, nor
  any repo file not listed; no repo search; no git.
- May read: `sw/escmodels/board.py`, `sw/escmodels/__init__.py`, style examples `uart.py`, `ps2.py`.
- Specification: EBU Tech 3250-E, 3rd edition 2004, "Specification of the digital audio interface
  (The AES/EBU interface)", https://tech.ebu.ch/docs/tech/tech3250.pdf (framing as IEC 60958;
  IEC 60958 itself is not public), with its pdftotext copy; sections 2.1, 2.2, 2.3, 2.4, 2.5, 4,
  Appendix 1 (CRCC), 6.2.5.1 (intrinsic jitter) pointed to.
- Interface: `SpdifRx(pin=0, tick_ns=20.345, fs_hz=48000.0, rate_tol_ppm=1000.0, jitter_ui=0.025)`,
  a receiver at logic level that drives nothing; records `frames` ({a, b, v, u, c, block_start}),
  `blocks` (channel status bytes per channel, CRCC check), `ui_ns`; checks biphase mark,
  preambles and their order (Z every 192 frames), even parity, transitions on an ideal clock
  within 0.025 UI (no measurement filter: stricter than 6.2.5.1), mean UI within rate_tol_ppm,
  continuity after lock; `errors()`, `finish()`, `_selftest()`. Decides nothing about content.

**Delivered** (the firmware's side's note).

- Model delivered before 12:40 (self-test 22 checks pass, ~455 s of agent time); firmware
  writer used only its documented interface. The harness flagged an "instruction-shaped" tag in
  the report (`<tick>` in a format string): only a format description, nothing acted on.

## Microwire 93C46 EEPROM

**Brief.**

- Same rules (never read `firmware/`, `test/`, `sw/tests/`, the private notes, nor any
  repo file not listed; no repo search; no git). May read `board.py`, `__init__.py`, `uart.py`,
  `ps2.py`.
- Specification: Microchip DS21749H (2010), 93AA46A/B/C, 93LC46A/B/C, 93C46A/B/C 1K Microwire
  Compatible Serial EEPROM, https://ww1.microchip.com/downloads/en/DeviceDoc/21749H.pdf, with its
  pdftotext copy; Table 1-2 (A1-A15), Figure 1-1, Tables 1-3/1-4, sections 2 and 3 pointed to.
- Interface: `Eeprom93(cs=0, clk=1, di=2, do=3, tick_ns=40.0, org16=True, grade="LC", vcc=5.0,
  twc_us=None, init=None)`: the EEPROM (drives DO or releases it); records `mem`, `ops`
  (t, op, addr, data, executed), `violations`; checks A1-A8 timing, start bit and instruction
  framing, the self-timed cycle and Ready/Busy, instruction while busy, DO contention; models
  DO with TPD/TCZ/TSV maxima. `errors()`, `finish()`, `_selftest()`.

**Delivered** (the firmware's side's note).

- Model delivered ~12:55 (self-test: 6 legal configurations, 10 illegal variants). Tests written
  against the interface before it arrived; 19 passed at the first run with it.

## DHT22 host

**Brief.**

- Same rules. May read `board.py`, `__init__.py`, `uart.py`, `ps2.py`.
- Specification: Aosong AM2302 Product Manual (AM2302 = DHT22), public copy
  https://files.seeedstudio.com/wiki/Grove-Temperature_and_Humidity_Sensor_Pro/res/AM2302-EN.pdf
  with its pdftotext copy; told that pdftotext misaligns Table 6's Symbol column and given the
  row order to check against the PDF; 4.3, Table 4, 7.1-7.4 pointed to.
- Interface: `Dht22(pin=0, tick_ns=40.0, humidity=65.8, temperature=26.9, raw=None,
  timing="typ", seed=1, startup_s=2.0, min_interval_s=2.0, faults=None)`: the sensor on the
  open-drain line; records `reads` (t, start_us, data, answered); checks the start low (Tbe),
  host release and interference during the reply, read interval and start-up time, noise;
  timing modes typ/min/max/random; fault injection (checksum, no response, truncate, stretch).

**Delivered** (the firmware's side's note).

- Model delivered ~13:06 (self-test passes; it confirmed the Table 6 row order against the PDF;
  it notes 7.4 says a 0's high is 26-28 us, it follows Table 6, 22-30 us).

## BiSS C master

**Brief.**

- Same rules. May read `board.py`, `__init__.py`, `uart.py`, `ps2.py`.
- Specification: BiSS C Protocol Description Rev D2 (biss-interface.com, 2025-10 upload) and AN3
  BiSS CRC Polynomials and Calculation Rev E3, AN21 CRC verification (examples), with their
  pdftotext copies; sections and Table 10 Nos. 1-12 pointed to.
- Interface: `BissSlave(ma=0, sl=1, tick_ns=40.0, dlen=26, data=0, crc_bits=6, line_delay_ns=0.0,
  busy_ns=0.0, busy_clocks=0, timeout="static", tto_us=20.0, cds=0, faults=None)`: one slave plus
  the line delay; records `frames` (t, data, crc, cdm, complete, clocks, tma_ns), `crc6()`;
  checks clock frequency and duty, complete frames, no new frame before the timeout ends, the
  40 us idle after power-up, cycle time if a master rule.

**Delivered** (the firmware's side's note).

- Subagent 5 delivered `sw/escmodels/biss.py` (+ one import line); `_selftest()` passes (AN21
  6- and 16-bit CRC examples, a synthetic master at 100 kHz to 6.25 MHz with line delay, busy,
  adaptive timeout, INIT frame, faults; ten illegal masters each flagged). It read only
  board.py, `__init__.py`, uart.py, ps2.py, D2, AN3, AN21 (not AN10). Interpretation calls it
  flagged: a cut frame holds its last bit; a CRC/silent/no_start frame requires a 40 us break
  before the next frame (Application Hints step 5); only the first frame may be an INIT frame.

## PS/2 host

**Brief.**

- Same rules as before (no firmware/, test/, sw/tests/, the private notes, notes; no repo
  search). May read `board.py`, `__init__.py`, `uart.py`, `ps2.py` (the existing host-role
  model, as a style example; it writes the device side).
- Specification: IBM PS/2 Hardware Interface Technical Reference, Common Interfaces (84F9735,
  Oct 1990), Keyboard/Auxiliary Device Controller, "Auxiliary Device and System Timings"
  (PDF pp. 229-231: Figures 7-9, T1-T9, steps), "Signals"; IBM Keyboards (101- and 102-Key)
  Technical Reference (Oct 1990): Data Stream, Data Output, Data Input, Power-On Routine,
  Commands from/to the System. PDFs and pdftotext copies.
- Interface: `Ps2Keyboard(clk=0, data=1, tick_ns=40.0, send=(), timing="typ", seed=0,
  reply_us=1000.0, bat_ms=1.0, faults=None)`; records `sent`, `received`, `inhibits`; checks
  RTS > 60 us, host data changes only with CLK low, stop bit, line-control handshake, no CLK
  low after the 10th clock, no DATA low during keyboard output, a command before the previous
  response; answers FF, FE, EE, F2, ED, F3, F4/F5/F6, parity error -> FE.

**Delivered** (the firmware's side's note).

- Subagent 6 delivered `sw/escmodels/ps2kbd.py` (+ one import line); `_selftest()` passes
  (legal host at min/max/random with measured phases, contention at clock 3, Reset/Echo/ID/ED/
  F4 with bad parity; six illegal hosts flagged; all five faults). Extras: poll_us, idle_us
  (50 us both lines high before a byte, T5), stall_us, power_on, clear_buffer. Its call to
  confirm: the abort boundary is the 10th CLK fall (keyboard text), the Common Interfaces text
  could allow a little later. Its report contained `<tick>` placeholders that the harness
  neutralised as tag-shaped text; nothing else.

## SNES controller reader

**Brief.**

- Same rules. May read `board.py`, `__init__.py`, `uart.py`, `ps2.py`.
- Specification: TI/Harris CD4021B data sheet SCHS024C (Oct 2003): Fig. 2, truth table,
  recommended operating conditions and dynamic characteristics at 5 V; Nintendo US patent
  5,207,426 (description); SNES Development Manual Book I (1993, archive.org copy): 2-13-1,
  Programming Cautions 24.8 and 24.12, registers 2-28-8/9 (key data layout, signature, the 17th
  bit). Told to mark inferences (what shifts in after bit 16, pull-up) as such.
- Interface: `SnesPad(latch=0, clock=1, data=2, tick_ns=40.0, buttons=0, signature=0b0000,
  timing="max", connected=True, faults=None)`; records `latches` (t, t_fall, width_ns, buttons,
  clocks, bits); checks P/S width, P/S removal, clock pulse widths and frequency at 5 V.

**Delivered** (the firmware's side's note).

- Subagent 7 delivered `sw/escmodels/snes.py` (+ one import line); `_selftest()` passes (legal
  consoles at 12/6 us and near the 5 V limits at max/typ/min delay; all buttons, signatures,
  3030H, 17-bit read, unplugged, faults; five illegal consoles flagged). Marked as inference:
  two cascaded CD4021B, MSB first, second register's serial input tied low (17th bit = connected),
  console pull-up on DATA. Extra fault `serial_in`. Its report again contained `<tick>`-style
  placeholders that the harness neutralised; nothing else.

## M-Bus slave

**Brief.**

- Same rules. May read `board.py`, `__init__.py`, `uart.py`, `ps2.py`.
- Specification: "The M-Bus: A Documentation" Rev. 4.8 (M-Bus Usergroup), the wired M-Bus
  specification at m-bus.com (text copies of chapters 4-8); EN 13757-2 itself is not public.
  Sections pointed to: 4.1, 4.4, 5.1-5.5, 6.1-6.3. Told to state its tolerance and "pause"
  interpretations.
- Interface: `MbusMaster(to_slave=0, from_slave=1, tick_ns=40.0, baud=2400, telegrams=(),
  start_ms=1.0, faults=None)` (+ optional `slave_address`, `rate_error_ppm`); helpers
  short_frame, long_frame, checksum, parse; records `exchanges`; checks the slave's characters,
  pauses, reply format, A field, the 11 bit / 330 bit + 50 ms window, silence on 255, other
  addresses and faulty telegrams, required answers; master-side faults.

**Delivered** (the firmware's side's note).

- Subagent 8 delivered `sw/escmodels/mbus.py` (+ one import line); `_selftest()` passes (the
  document's example frames reproduced; a legal slave at 300-38400 baud, addresses 0/5/250,
  254, 255, others; eleven illegal slaves flagged; six master faults unanswered; retries).
  Interpretations it states: a bit must be steady over its middle half (about +/-2.5 % rate);
  a pause is more than 0.5 bit of Mark after a stop bit; the master waits 33 bit times after
  each exchange, the full 330 bits + 50 ms when no reply is due. Its report again contained
  `<tick>` placeholders neutralised by the harness; nothing else.
