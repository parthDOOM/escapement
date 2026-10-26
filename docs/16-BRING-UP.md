# Silicon bring-up

One checklist and one script take a Tiny Tapeout demo board with the Escapement chip from
power-on to "every firmware's execution digest matches the simulator". The script is
`sw/rp2350/bringup.py`. It runs in stages, prints PASS or FAIL for each with a
diagnosis, and stops at the first failure. This page says what each stage checks, what a
failure means, and what to measure next.

Nothing here has run on silicon yet. The script has run against a simulated demo board
(the ISS behind a model of the host interface, with injected faults) and against the RTL
of the whole chip; see [Before silicon](#7-before-silicon).

## 1. Equipment

| Item | Notes |
| --- | --- |
| Tiny Tapeout demo board with the Escapement chip on its carrier | ttDBv3 (RP2350) with tt-micropython-firmware v3.1.1, or an RP2040 board with v2.0.4, the last RP2040 release. The board API the script uses was checked in the source of both (section 9) |
| USB cable and a PC | Python 3.10 or later with this repository (`pip install -r test/requirements.txt`), and `pip install mpremote` |
| Oscilloscope | For the clock pin, `uo[0]` and the uio pins when a stage fails |
| Logic analyser, 8 channels or more | For the host link (`ui[2:0]`, `uo[0]`) and to compare uio traces with the model. A sample rate of 100 MS/s or more resolves single 40 ns ticks |

Before power-on:

- Unplug everything from the bidirectional (uio) Pmod. Stages 4 and 5 assume that each
  uio pin reads 1 when the chip does not drive it, through the RP's pull-up.
- Switch every input DIP switch off. The SDK refuses to drive a ui pin that reads high.
- Leave the input and output Pmods empty.

## 2. Build the bundles (on the PC)

A bundle is one firmware image plus the model's prediction of what the chip will show.

```sh
python sw/rp2350/make_bundle.py bundles            # one JSON file per firmware, for the board
python sw/rp2350/make_bundle.py traces --trace     # PC only: also <name>.pins.csv, the model's uio trace
```

Every `firmware/*.asm` except the replay template gets a bundle. The ISS runs each one in
the demo board's environment, nothing on uio and the RP's pull-ups on, and records one of
three predictions:

| Kind | When | What the board checks |
| --- | --- | --- |
| `halt` | Every context halts or faults | The digest (`READ_DBG` sel 16), context states, PCs, r0–r3, the first-fault register and the uio levels |
| `steady` | The program settles into a repeating pattern, usually waiting for a mailbox word or an input edge | The same state while it waits; then STOP, and the digest must lie on the model's orbit (below) |
| `none` | Neither within 2,000,000 ticks | State and first fault only |

A waiting context commits a WAIT outcome every 4 ticks, and each one folds into the
digest, so the digest of a waiting program depends on when the host stops it. The
bundle records the digest at a tick `t0` after the pattern began and the values folded in
one period. After STOP, the board starts from that digest and applies the period's folds
until it reaches the chip's digest, within the run length it measured. A match means the
chip did exactly what the model did up to `t0` and then repeated the same pattern until
it was stopped.

For example, `uart_tx` and `logic_analyzer` halt, and firmware that waits for host
commands or for a bus peer (`i2c_ctrl`, `spi_ctrl`, `i2c_eeprom`, `uart_rx`, `usb_hid`,
...) is `steady`. Seven never settle into a short repeating pattern and are `none`: the
generated transmitters `dmx512_tx` and `ws2812_gen` re-anchor every packet, `seven_seg`
waits 160 ms between frames, `dshot_tx` and `sent_tx` repeat over more than 8,192 ticks,
and `cec` and `dali` look at the host every millisecond while the bus is idle.
`westminster` (a 14 s tune) is not bundled. `make_bundle.py` prints the kind and the predicted digest for each.
Rebuild the bundles whenever a firmware, the assembler or the ISS changes: a stale
bundle fails stage 5, as it should.

## 3. Copy the files to the board

```sh
mpremote fs cp sw/eschost/protocol.py :protocol.py
mpremote fs cp sw/rp2350/bringup.py :bringup.py
mpremote fs cp sw/rp2350/escapement_demo.py :escapement_demo.py    # optional
mpremote fs cp -r bundles :
```

## 4. Run

```sh
mpremote repl
>>> import bringup
>>> bringup.run()
```

| Call | Effect |
| --- | --- |
| `bringup.run()` | Stages 0–5; stops at the first failure |
| `bringup.run(keep_going=True)` | Runs every stage from 3 on even after a failure (0–2 always stop, because later stages need them) |
| `bringup.run(optional=True)` | Adds stage 6, the UART smoke test |
| `bringup.run(stages=(3,))` | Stages 0–2, then only the listed ones |
| `bringup.run(quick=True)` | Fewer repetitions and patterns, for a fast re-check |
| `bringup.run(bundles="dir")` | Bundles from another directory |

`bringup.LAST.results` keeps the last run's results. `escapement_demo.run("bundles/uart_tx.json")`
runs a single bundle with telemetry.

A passing run on the simulated board looks like this. Times, the RP system clock and the
measured clock rate will differ on hardware; the stage lines and verdicts are what to
compare.

```text
[0] board and project ...
[0] PASS  RP2350, SDK 3.1.1, shuttle ...
[1] clock and reset ...
[1] PASS  PWM 25000000 Hz (RP 100000000 Hz), uo = 11111000 in and after reset, uio released
[2] host link ...
[2] PASS  STATUS 08 00 0f 00 x 201; one pin write ... ns; SCK half period about ... ns (fast) and ... ns (slow)
[3] memories ...
[3] PASS  data lines at 2 addresses, 4 patterns x 256 words, in both SRAMs; spaces independent; tape reads during a run while the core uses the port
[4] pins and clock rate ...
[4] PASS  uio[7:0] toggle as driven; chip clock 25.0000 MHz from a 25000-tick period; STOP releases pins; all 8 inputs read correctly; bank B: 5 outputs toggle and release high, 5 inputs read correctly; streaming capture: 16 edges through an 8-word ring
[5] firmware digests ...
    can_rx.asm             steady ok    digest 0x2c2bc8cb = model at tick 160448
    can_tx.asm             steady ok    digest 0xffaf382b = model at tick 160444
    cec.asm                none   ok    no digest prediction; state compared
    ...
    usb_hid.asm            steady ok    digest 0x394c297d = model at tick 160446
    uart_tx.asm            halt   ok    digest 0x63114ed9 model 0x63114ed9
[5] PASS  30 of 30 firmware match the model
[6] UART smoke test (optional) ...
    uart_tx.asm: RP UART received 55 4b 21 0a
[6] PASS  1 UART smoke test(s) received exactly the model's bytes
bring-up PASSED
```

## 5. The stages

### Stage 0: board and project

Sets the SDK to `ASIC_RP_CONTROL`, checks that `tt_um_escapement` is in the board's
shuttle index and enables it, then drives `ui[2:0]` with four patterns and reads the pins
back.

| FAIL message | Meaning | Next |
| --- | --- | --- |
| not in this board's shuttle index | The SDK's index does not list the design | Update tt-micropython-firmware; check which chip is on the carrier |
| enable() did not select | The mux did not take the selection | Power-cycle; check `tt.shuttle.enabled` at the REPL |
| ui[2:0] reads ... after driving ... | An input DIP switch is on, or something else drives the pin | Switch the DIP switches off; scope the named ui pin |

### Stage 1: clock and reset

Starts the project clock with `tt.clock_project_PWM(25000000)` and checks the frequency
the SDK reports. Holds `rst_n` low and checks that `uo_out` reads `0xF8`: MISO, IRQ and
RUN low, and pin bank B (`uo[7:3]`) released, which is high. Then it releases reset and
checks again (status bit 7, "running", drives MISO while CS_n is high and is 0 after
reset). Then turns on the RP pull-ups on uio[7:0] and checks that every pin reads 1: after
reset the chip drives no uio pin.

| FAIL message | Meaning | Next |
| --- | --- | --- |
| clock_project_PWM(25000000) reports ... Hz | The SDK set a different clock | Look for a `[tt_um_escapement]` section in `config.ini` that sets `clock_frequency` or `rp_clock_frequency` |
| uo_out = ... while rst_n is low | An output differs from `0xF8` in reset | Scope the named `uo` pins: stuck or shorted, or another design is selected |
| uo_out = ... after reset | Same, out of reset | As above; MISO (`uo[0]`) high here means status bit 7 is set |
| uio pins ... read 0 with only the RP pull-ups | Something pulls the pin low | Unplug the uio Pmod; measure the pin. If it is still low, the chip drives it or it is shorted |

The PWM check only proves that the RP was asked for 25 MHz. Whether the chip sees that
clock is checked by stages 2 (no clock, no answer) and 4 (measured rate).

### Stage 2: host link

Everything else goes through this SPI link (`ui[0]` CS_n, `ui[1]` SCK, `ui[2]` MOSI,
`uo[0]` MISO; [host protocol](12-HOST-PROTOCOL.md)). The RP bit-bangs it with
tt-micropython-firmware's byte functions and samples MISO just before each SCK rise.

1. Times 500 raw pin writes. The SCK high phase is one pin write and must be at least 4
   core clocks (160 ns); the script requires 320 ns.
2. Reads STATUS right after reset. The answer is known exactly: `08 00 0f 00` (version 8,
   all contexts IDLE, all TX mailboxes empty, no IRQ).
3. Sends an unknown command (0x99), which must set host error, then CLEAR_ERR, which must
   clear it. This proves MOSI reaches the chip.
4. Reads `READ_DBG` values that are known after reset: the digest `0xFFFFFFFF` and
   `min_lead` `0xFFFF0000` in every context, through the 5-bit select.
5. Repeats STATUS and a debug read 200 times with the fast byte functions, and 10 times
   with the per-bit port API plus 50 µs per pin change.

| FAIL message | Meaning | Next |
| --- | --- | --- |
| one pin write takes ... ns | The board is faster than the margin | Use `Link(io, fast=False)` or a lower RP clock |
| MISO is always 0 | No answer at all | Scope the clk pin (25 MHz square wave); check `rst_n` is high; check CS_n and SCK toggle at the chip; check `uo[0]` |
| MISO is always 1 | `uo[0]` stuck high or undriven | Scope `uo[0]`; compare with stage 1 |
| one bit late / one bit early | Sampling phase | Logic analyser on all four lines: MISO must change within 4 clocks after an SCK rise, and the RP must sample before the next rise |
| protocol version 5, 6 or 7 / a different design | An older Escapement (v1.0, v1.1 or v1.2) or another design is answering | Check stage 0's selection |
| did not decode STATUS / unknown command did not set host error | MOSI stuck high / low | Scope `ui[2]` at the chip |
| N of M frames wrong | Intermittent | Errors only at the fast rate: SCK phase or MISO slew is marginal; at both rates: noise, ground or a loose connection |

### Stage 3: memories

Both 256 × 32 SRAMs (program store and tape), through WRITE/READ_IMEM and WRITE/READ_TAPE:

1. **Data lines.** Twelve words, written and read one at a time at address 0x00 and 0xFF.
   Every data bit is seen at 0 and 1, and every pair of bits differs in at least one word,
   so an address fault cannot fake or hide a data fault.
2. **Address lines.** "Address in data": word `a` holds `a`, `~a`, `a`, `~a` in its four
   bytes, written in ascending order. When an address line has no effect, address `a`
   reads the word written to `a ^ 2^b`, and the word itself names `b`.
3. **Cells.** Walking one, and (full run) walking zero and a checkerboard.

The program store gets each pattern and the tape its inverse, which also shows that the
two address spaces are separate. A failing pattern is read a second time to separate a
stored fault from an unstable read.

4. **Tape reads during a run (v1.3).** A four-word program (STORM) uses the tape port in
   two of every three of its commits while the host reads 32 words with READ_TAPE. Every
   word must be right, and no host error may be set: each word is fetched in a cycle the
   core leaves free, before it is sent.

| FAIL message | Meaning | Next |
| --- | --- | --- |
| data bits ... never read 1 / never read 0 | A data line is stuck | If both memories show the same bits, the fault is in the shared host path, not a macro |
| data bits always equal (shorted) | Two data lines are bridged | Same |
| address bit(s) ... have no effect | An address line is stuck or open | It appears in one memory only, so it is that macro's address path |
| addresses alias across bit groups ... | Two address lines are bridged | Same |
| some words are not any address's word | Cell faults | Rerun with `quick=False`; note which words; check the supply |
| two reads of the same contents differ | Unstable reads | Supply voltage, temperature or read timing: rerun after a power cycle and compare chips |
| tape read during a run: N of 32 words wrong | The words read correctly while stopped, so the fault is in the v1.3 port arbitration | Compare with a READ_TAPE of the same words after STOP; report the chip |
| a tape read during a run was not fetched before it was due | The host's fetch request is never granted | Same |

Both macros are the IHP 1P 256×32 SRAM. Their behaviour here has only been simulated with
the vendor's behavioural model.

### Stage 4: pins and clock rate

Two small programs, hand-assembled in the script (the tests check them against the
assembler), run first on bank A and then on bank B:

- **SQUARE** owns all eight uio pins and drives `0x55` and `0xAA` alternately, 12,500
  ticks each (500 µs at 25 MHz), with DRIVE events at exact ticks. The RP reads the uio
  levels 400 times: only `0x55` and `0xAA` may appear. It then times 8 periods on uio[0]
  with `machine.time_pulse_us`; one period is 25,000 core clocks, which gives the chip's
  clock rate to better than 0.1%. After STOP every pin must read 1 again.
- **GETP** owns no pin and copies the synchronised inputs into r0 every 8 ticks. The RP
  drives 20 patterns on uio[7:0] and reads r0 back with `READ_DBG`. The chip cannot
  drive a pin it does not own (formal property P3, proved unbounded), and the script
  checks that every pin reads 1 before the RP drives anything.
- **Bank B.** The same two programs with context 0 on bank B. SQUARE drives `10101` and
  `01010` on `uo[7:3]`, which must read high again after STOP. GETP reads `ui[7:3]` as
  logical pins 0–4 while the RP drives 14 patterns on them; logical pins 5–7 must read 0.
  The host link keeps the pattern on `ui[7:3]` during its own transfers.
- **Streaming capture (v1.3).** The logic analyser streams uio[0] into an 8-word ring
  while the RP drives 16 edges on it, reading and acknowledging the records after each
  edge. All 17 records (the snapshot and 16 edges) must arrive in order, with no
  overflow: more than twice what the ring holds without the acknowledgements.

| FAIL message | Meaning | Next |
| --- | --- | --- |
| Pins ... never read 1 / never read 0 | Output driver dead, or a pin held by something stronger | Scope the pin: a 1 kHz square wave, in antiphase with its neighbours |
| Pins i and i+1 always read the same | Neighbours shorted | Inspect the carrier and the Pmod header |
| the chip runs at ... MHz | The clock reaching the chip is not 25 MHz | Scope the clk pin; check what reprograms the RP clock |
| uio[0] did not toggle | No output at all | Check the program loaded (stage 3) and RUN (`uo[2]`) is high |
| after STOP, uio pins ... still read 0 | STOP did not release the pins | Scope the pin across the STOP frame |
| Input i stuck at 0/1, follows pad j | Input path fault | Scope the pad while the RP drives it |
| bank B levels ... Outputs Bi (uo[n]) never read 1 / 0 | A bank B output is dead or held | Scope `uo[n]`. Stuck low is already caught by stage 1 |
| after STOP, bank B outputs ... read 0 | A released bank B output is not high | Scope the pin across the STOP frame |
| wrong bank B levels ... Input Bi stuck, follows pad Bj | A bank B input path fault, or an input DIP switch is on | Switch the DIP switches off; scope `ui[i+3]` |
| Logical pins 5..7 read 1 | The context did not switch to bank B | WRITE_CFG byte 10 did not arrive: check that stage 2 reported version 8 |
| streaming capture overflowed | The ring filled although every record was acknowledged | TAPE_ACK is not reaching the capture unit: READ_DBG sel 20 shows its `rptr` |
| streaming capture: uio[0] levels ..., records lost | Edges missing | Compare with a stopped capture (logic_analyzer.asm without STREAM) of the same edges |

### Stage 5: firmware digests

For each bundle: STOP, load all 256 program words (zero-filled, as in the model), verify,
load all 256 tape words (zero-filled plus the image's `.tape` words), verify, WRITE_CFG,
START. Then, by kind:

- `halt`: poll STATUS until no context is RUNNING, read the state, the digest and the
  first fault, and compare them with the model.
- `steady`: wait until the run is past `t0`, read the state while the program waits,
  STOP, read the digest and search the model's orbit.

Each firmware prints one line; the stage fails if any firmware differs.

| Result | Meaning | Next |
| --- | --- | --- |
| LOAD FAILED | Readback differs | Stage 3 should have failed; rerun it with `quick=False` |
| chip and model differ at the end: ctx k PC / rN / state / first fault / uio levels | The run diverged | Capture uio[7:0] from START with the logic analyser and compare it with `traces/<name>.pins.csv` (ticks are 40 ns): the first differing edge shows where |
| same end state, different history | A commit, register value or pin change differed during the run but the end state agrees, or the digest path itself is faulty | Same capture; if the pins agree everywhere, suspect the digest logic |
| not on the model orbit | A steady firmware's digest never matched within the measured run length | As above; also check the clock rate from stage 4, since the search window depends on it |
| uio levels ..., model ... | The chip's registers agree but a pad does not | Stage 4's pin results; a load on the pin |

### Stage 6 (optional): UART smoke test

Runs `uart_tx` and receives its bytes with the RP's hardware UART on uio[0] at
115,200 baud. MicroPython always assigns a TX pin to a UART, so the script uses `ui[7]`,
bank B's B4 input, which `uart_tx` (a bank A program) never reads. The RP's UART must accept the pins: by the GPIO maps in the
SDK and MicroPython's rule for the RP2 UART pins, uio[0] and ui[7] are UART1 RX and TX
on the ttDBv3 board (GPIO 25 and 24) and on the RP2040 boards of SDK v2.0.4 (GPIO 21 and
20). A board where MicroPython refuses the pins reports "could not open an RP UART".
Garbled bytes point at bit timing or the pad; no bytes at an undriven uio[0].

## 6. Known limits

- **Pads are uncharacterised.** The Tiny Tapeout clock specification gives a 66 MHz
  input limit, but for the sky130 pads it describes, not for this IHP shuttle. Drive
  strength, slew and input thresholds of the pads are unmeasured. The script sees only
  logic levels as the RP reads them; analogue checks need a scope.
- **The host link is slow.** The protocol allows SCK up to f_clk / 8 (3.125 MHz). A
  MicroPython pin write takes far longer than a core clock, so loads and memory tests
  take seconds. The script measures and prints the pin write time and the SCK half period
  instead of assuming them.
- **Digests assume an empty uio Pmod.** The predictions use the ISS default board, which
  is the RP pull-ups and nothing else. With protocol peers attached, input-dependent
  firmware needs its input trace (capture and replay, [tape and digest](13-TAPE-AND-DIGEST.md)).
- **Steady-state digests depend on a measured run length.** The orbit search runs up to
  the wall-clock run length at the stage 4 clock rate plus 3%. A clock far from nominal
  is caught by stage 4 first.
- **What only silicon can confirm:** that the design is selected and powered by the
  mux, the real clock path and duty cycle at the chip, pad behaviour on every pin, SRAM
  behaviour across supply and temperature, MicroPython's actual pin-write timing on each
  board, and that the RP's UART accepts the stage 6 pins.

## 7. Before silicon

The same script runs under CPython against two simulated chips:

| Command | Chip | What it shows |
| --- | --- | --- |
| `make sw-test` (`sw/tests/test_bringup.py`) | `sw/tests/demoboard_mock.py`: the host interface modelled cycle by cycle after `escapement_host.v`, the ISS as the core, both SRAMs, and faults | A correct chip passes stages 0–6 with every firmware, without the RP and the chip ever driving the same pin and with every SCK phase at least 4 clocks. 22 injected board and chip faults (selection, DIP switch, clock, reset state, MISO/MOSI stuck, a v1.0 or v1.1 chip, a too-fast host, SRAM address and data lines stuck or shorted, a dead output, a stuck input, a half-rate clock, a bank B output stuck low or high, a bank B input stuck) each fail the stage meant to catch them with the expected diagnosis; so do a stuck register bit, a fault in the digest path only, and stale bundles (stage 5) |
| `make bringup-test` (`test/bringup/`, about 15 minutes) | The RTL of the whole chip under cocotb, with the board's uio wiring in `tb_bringup.v` | Stages 0–6 pass on the RTL with four firmware (`BRINGUP_FIRMWARE=all` for every one); a stale bundle fails stage 5 |

The simulated board imitates the parts of tt-micropython-firmware the script uses,
including microcotb's `Logic` type, which has no `&` with an integer.

## 8. Clock, timing and the board

From the tt-micropython-firmware source (section 9):

- The project clock is a PWM output of the RP. `clock_project_PWM(hz)` picks an RP system
  clock that divides to `hz` with an even PWM divisor. For 25 MHz its search settles on a
  100 MHz system clock divided by 4, so the clock is exact with a 50% duty cycle. The
  limit is half the maximum RP clock: 133 MHz by default, raised automatically up to
  250 MHz, so up to 125 MHz can be requested.
- `enable()` of a design without a `config.ini` section stops the clock, toggles reset the
  first time the design is loaded, and starts the clock at the design's `clock_hz` from
  the shuttle index (25 MHz, from `info.yaml`).
- Per-bit port access (`tt.ui_in[1] = 1`) reads, modifies and writes the whole port
  through microcotb's `LogicArray`; the byte functions in `ttboard.util.platform`
  (`write_ui_in_byte`, `read_uo_out_byte`) are single register accesses. The fast link
  uses the byte functions; the slow link uses the port API.
- `tt.uo_out[0]` is a microcotb `Logic`: `int()` works, `& 1` raises `TypeError`. The
  earlier demo script used `& 1`; `bringup.py` uses `int()` and the byte functions.

## 9. Sources checked

On 2026-10-17:

- tt-micropython-firmware, release v3.1.1 (main at `d485c7a`, 2026-09-08):
  <https://github.com/TinyTapeout/tt-micropython-firmware> — `README.md`, `config.md`,
  `src/ttboard/demoboard.py`, `src/ttboard/project_mux.py`, `src/ttboard/pins/pins.py`,
  `src/ttboard/pins/standard.py`, `src/ttboard/pins/gpio_map_dbv3.py`,
  `src/ttboard/util/platform/rp2040.py` and `rp2350.py`; release v2.0.4 for the RP2040
  boards (`demoboard.py`, `util/platform.py`, `pins/gpio_map.py`, `pins/standard.py`).
- microcotb at `81f2498` (the firmware's submodule): <https://github.com/psychogenic/microcotb>
  — `src/microcotb/types/logic.py`, `handle.py`, `ports/io.py`.
- MicroPython: <https://docs.micropython.org/en/latest/library/machine.html>
  (`time_pulse_us`), `ports/rp2/machine_uart.c` and `extmod/machine_pulse.c` in
  <https://github.com/micropython/micropython>, and
  <https://docs.micropython.org/en/latest/reference/mpremote.html>.
- Tiny Tapeout clock specification: <https://www.tinytapeout.com/specs/clock/>.
