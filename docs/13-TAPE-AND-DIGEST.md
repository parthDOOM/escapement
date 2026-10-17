# The tape, the execution digest and a waiting GETI

Version 1.1 of the machine adds three things (D-24, D-25, D-26 in
[design decisions](06-DECISIONS.md)). The normative text is
[11-SEMANTICS](11-SEMANTICS.md) §6.5, §7.3, §7.6 and §10.1; this page explains why.

## The problem

Version 1.0 could drive, sample and check pins at exact ticks, but it had **nowhere to
put anything**. There was no data memory, so an emulated I2C device could keep only a
few bytes in a register. A capture buffer and a replay mechanism were separate needs
again. Building data storage, capture and replay separately in flip-flops would cost
about 1,000 flops for each 16-entry buffer, and routing congestion with them.

## The idea: one timestamped tape

A second `RM_IHPSG13_1P_256x32` macro (49,488 µm², 5.4% of the 6×4 die) becomes the
**tape**. Firmware, the capture unit and the host share it:

| User | Access | When |
| --- | --- | --- |
| Firmware | `LDT ra, rb, imm8` / `STT ra, rb, imm8` (address `R[rb] + imm8`) | In its commit cycle |
| Capture unit | One record `{levels[7:0], tick[23:0]}` per watched-pin change | Any cycle the port is free |
| Host | `WRITE_TAPE` / `READ_TAPE` (host protocol v1.1) | Only while stopped |

One memory then covers four jobs:

- **Device emulation.** [`i2c_eeprom.asm`](../firmware/i2c_eeprom.asm) is a 24C02
  whose 256 bytes live in the tape. It runs at Standard and Fast mode against a
  scripted controller with timing checks. [`spi_target.asm`](../firmware/spi_target.asm)
  serves the same kind of memory as a 25xx-style SPI part.
- **Logic analyser.** [`logic_analyzer.asm`](../firmware/logic_analyzer.asm) is four
  instructions. Capture keeps running after every context halts, until the region
  fills (or forever in ring mode, which keeps pre-trigger history).
- **Record and replay.** A capture record has the same shape as a DRIVE event (a tick
  and levels), so `sw/esctape.build_replay()` turns a capture straight into
  [`replay.asm`](../firmware/replay.asm) plus tape contents. The replay is tick-exact
  (tested), or refused if it is too dense.
- **Exact input timestamps.** Firmware can read capture records too, so the exact tick
  of an input edge is available without quantising to the 4-tick slot grid.

## Why it stays deterministic

The single port has three users and no stalls that firmware can see:

- **Firmware owns the port only in its own commit cycle**, and one context commits per
  cycle, so LDT/STT never conflict with each other and never WAIT. `STT` writes at the
  commit edge. `LDT` reads at the commit edge, and the register is written one edge
  later. That is architecturally invisible, because the same context's next commit is
  four ticks away.
- **Capture fills the gaps.** A two-record staging FIFO drains into any cycle without
  an LDT/STT. A burst that would need a third slot does not drop a record silently.
  It latches `ovf` and `ovf_tick`, stops generation, and still writes the records
  already staged, so the prefix is complete and the failure is visible. This is the
  reject-not-late rule applied to observation.
- **The host waits** until the run is stopped and staged records have drained.

Whether a record overflows is therefore a pure function of program and inputs, and
both models predict it. The random co-simulation exercises it on purpose:
LDT/STT bursts collide with capture drains, and all 256 tape words are compared after
every run.

### Record format choices

- **Ticks mod 2^24 plus epoch markers.** A record is one word, so the 256-word tape
  holds 256 edges. A record is written at every 2^24-tick boundary while capture runs,
  so absolute time can be recovered from the words alone (0.67 s epochs at 25 MHz).
- **All eight levels, triggered by a mask.** A record is a full snapshot (it replays
  without context), but only watched pins create records.
- **A snapshot at arming**, so a trace always starts from known levels.

## The execution digest: a 32-bit witness of a run

Every edge of a run that commits an instruction, writes a loaded register or changes
an output folds `{pins, commit outcome/context/PC, register write data}` into a 32-bit
MISR (CRC-32 polynomial). See [§10.1](11-SEMANTICS.md) for the exact rule.

Why it matters:

- **Silicon against model in one read.** Both models are tick-exact, so after a
  program reaches HALT the host reads one word (`READ_DBG` sel 16). The ISS predicts
  the same word from the same image and input trace. Equal words mean the chip executed
  the same commits, outcomes, register values and pin states as the reference model, at
  a confidence of about 1 − 2^−32 per run.
- **Inputs come from the tape.** For input-dependent programs the input trace is the
  capture. So capture, the ISS and the digest together give full observability of a
  silicon run without a debug port: replay the captured inputs in the ISS, and the
  digest confirms the replay is faithful.
- **It costs 48 flops** (32 for the digest, 16 for the previous outputs).

The digest folds only on edges where something happens, so it freezes once every
context has stopped. A host STOP that arrives at an unknowable SPI time therefore
cannot change it.

## GETI waits for in-flight SAMPLEs (D-20, D-26)

In version 1.0, a `GETI` that committed at the edge where the last `SAMPLE` retired
returned the word without that bit, and the bit went into the next word. Firmware had to
avoid this with an explicit `WAITT`, and an SPI routine once lost a bit by missing one.
That is a silent failure in a design whose rule is "never silently", so `GETI` is now
wait-class: it waits while any SAMPLE is in the FIFO, including one due that cycle. The
cost is one OR over four FIFO kind fields.

## Cost

| Item | Flops | Area |
| --- | ---: | --- |
| Tape macro | – | 49,488 µm² (top-right corner, [physical design](03-PHYSICAL-DESIGN.md)) |
| Capture unit (`esc_cap.v`) | ~150 | mostly staging (64) and the overflow tick (32) |
| Digest | 48 | |
| LDT write-back, GETI wait | 5 | |
| Host tape commands | 1 | |

**About 200 flops in total**, against roughly 1,000 for a 16-entry flip-flop capture
buffer, which would have 1/16 of the depth, no data memory and no replay.

## Evidence

- **Hand-derived simulator tests** (`sw/tests/test_v11.py`) cover addressing, ring,
  full, overflow on a busy port, the epoch marker, CAPT disable and re-arm, GETC timing,
  and a digest recomputed independently from the observation records.
- **ISS against RTL, tick for tick.** 300 random programs with tape traffic,
  21,055 commits, 0 mismatches, comparing every commit, every pin on every tick, the
  digest, the capture state and all 256 tape words. Every protocol firmware also
  matches, including the EEPROM (27,500 commits).
- **Formal.** The cover task reached all 12 goals, including capture overflow, region
  full, ring wrap and a drain plus push with two records staged. The new safety
  properties are: capture never writes outside its region, staging ≤ 2, and generation
  only during a run.
- **Mutation** (`test/mutation/run_mutants.py`). Mutants of the digest, tape port, LDT
  forwarding, GETI wait and capture rules are all killed
  ([verification](04-VERIFICATION.md)).
- **Record → replay** (`sw/tests/test_tape_replay.py`). A UART capture replays with
  identical edge spacing, and a too-dense trace is refused with ADMIT, not replayed late.

## Limits

- **Replay** covers one 2^24-tick epoch (0.67 s) and costs 40 ticks per record in
  firmware. A trace denser than that faults (ADMIT) at the first edge it cannot meet.
- **Capture** sees the synchronised vector: the 2-tick synchroniser lag, and pulses
  shorter than a tick, are not resolved.
- **The EEPROM** clock-stretches before each ACK, which a real 24C02 does not. Fast
  mode depends on the 28-tick fall-to-stretch path inside the 33-tick minimum tLOW,
  which is measured in simulation against our controller model only.
