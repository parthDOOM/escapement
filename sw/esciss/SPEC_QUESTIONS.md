# Spec questions found while writing the ISS (docs/11-SEMANTICS.md v1.0.1)

Each entry lists the section, the question, and what the ISS does. The ISS uses the most
literal reading. The code marks the reachable cases as `SPEC-Q n`. The RTL must make the
same choice, or the spec must be amended, before the per-tick comparison can be trusted.

## Reachable and observable (these affect the per-tick comparison)

1. **§3 / §8. Instruction in flight when its context stops RUNNING other than by Stop.**
   §9 says that Stop discards instructions in flight. It says nothing about a context
   that becomes FAULTED by LATE_HEAD between fetch and commit.
   *ISS:* a commit whose context is not RUNNING during its commit cycle is discarded,
   and no commit record is produced. (This is only reachable through LATE_HEAD, which
   §6.3 says cannot happen.)

2. **§6.3 / §8. LATE_HEAD at the same edge as a commit of the same context.** This case
   cannot happen.
   *ISS:* the commit's register effects apply and are recorded with outcome C or W.
   The context then becomes FAULTED, and the flush discards anything the commit
   appended. When first-fault is empty and several contexts fault at one edge, the
   lowest context index is recorded. Only LATE_HEAD can make several contexts fault at
   one edge, because only one context commits per edge.

3. **§9 Stop.**
   (a) Does the commit due at the stop edge E_t happen? *ISS:* no. It is treated as in
   flight and discarded: no commit record, no fault. The checker does not evaluate
   tick t, and nothing retires.
   (b) "`now` stops": the ISS leaves `m.now = t+1`, and no run cycles (pin records)
   follow.
   (c) Host mailbox and irq_clear operations at the stop edge are still applied.
   (d) `val` is kept; only `oe` is cleared.

4. **§8 vs §6.4. Checker evaluation at the edge where its context faults.** §8 says
   that the FIFO is flushed, that nothing retires at that edge, and that the checker is
   deactivated. It does not say whether an already-active checker still acts on tick c
   at E_c. With a HOLD mismatch, or at the window end, does pass_count/fail_count (and
   first_fail) update at the fault edge?
   *ISS:* yes. Nothing in §8 suppresses it, so the evaluation of tick c is recorded and
   `active` then ends 0. A checker that would *start* at the fault edge (an EXPECT due
   at E_c) does not start, because retirement is suppressed. This shows up in the final
   state (counts). **Recommend making this explicit.** "Fault wins, no evaluation" is
   the more common RTL coding.

5. **§7.1 SHL/SHR with n = 0.** The text says "no change, C=0", and the Flags column
   lists Z, C. Is Z updated?
   *ISS:* Z = (R[rd] == 0), computed from the unchanged register, following the general
   rule "Z is set from the written … result". If the alternative is that Z is
   unchanged, the two readings differ whenever Z=1 and R[rd]≠0.
   (Test `test_shift_n0_sets_z_from_unchanged_value_specq5`.)

6. **§6.4 meaning of `active` (GETX bit 1, sel 10 bit 1, HALT condition).** "Between d
   and its stop tick" can be read two ways.
   *ISS (register reading):* the checker starts at E_d and stops at E_stop. So `active`
   is 1 during cycles d+1 … stop, and 0 during cycle d (the EXPECT is still in the FIFO).
   A window-0 EXPECT, or an ARRIVE that matches at d, is never visible as active.
   HALT's "checker not active after E_c" includes a checker started and stopped at E_c.

7. **§6.4 / §7.3. A level or value with bits outside the mask.** For
   `m = ((sync & mask) == level)` and WAITP `(sync & mask) == value`, a level/value bit
   outside the mask can never match. There is no ADMIT or ILLEGAL check for this.
   *ISS:* literal. Such a HOLD always fails at d, an ARRIVE always fails at d+w, and the
   WAITP only completes by timeout.

8. **§6.2. EXPECT and SAMPLE have no ownership check.** An EXPECT mask and a SAMPLE pin
   may name any pin, including pins owned by another context or by none.
   *ISS:* literal, so they are admitted. This is probably intended, since observation is
   harmless.

9. **§7.5 / §10. Host mailbox side.**
   (a) "Ignored when full/empty" is evaluated on the state during the cycle (the
   mbx_rx_full and mbx_tx_empty outputs), before the same-edge POP or PUSH. So a host
   push to a full RX is dropped even when a POP frees a slot at the same edge. A host
   pop of an empty TX is ignored even when a PUSH fills it at that edge.
   (b) The ISS API takes at most one host push and one host pop per context per edge.
   A second call before the edge raises RuntimeError. `host_tx_pop` returns the current
   head (mbx_tx_data), or None if TX is empty.
   (c) The spec doesn't say whether mailboxes of FAULTED or HALTED contexts stay
   host-accessible. *ISS:* they do.

10. **§7.4 telemetry for EXPECT and SAMPLE.** "On COMPLETE, telemetry updates" sits
    under the events table. *ISS:* min_lead and max_occupancy are updated for all five
    event instructions, not only for DRIVEs. wait_full counts full-FIFO WAITs of all
    five.

11. **§7 PC_RANGE edge cases (literal "sequential next PC").** The ISS behaves as
    follows:
    - HALT at 255 completes, because its PC is unchanged.
    - JMP, RET, and a taken Jcc or DJNZ at 255 complete.
    - A not-taken Jcc or DJNZ, and NOP, IRQ, data ops, a completing WAITT/WAITP/POP/
      PUSH, and an EMIT that would be admitted, all fault PC_RANGE with no effects. For
      example, DJNZ does not decrement lc, and the EMIT appends nothing and updates no
      telemetry.
    - A WAIT at 255 is allowed and still counts wait_full.
    - An EMIT at 255 that would fail admission reports ADMIT, not PC_RANGE.

12. **§9 / §5. Synchronisers before start.** As instructed, the ISS models one pre-start
    cycle s = −1 with `uio_in = board(-1, 0, 0)`. So sync is 0 during cycle 0, and
    sync during cycle 1 is uio_in during cycle −1. *Note:* this choice is unobservable.
    The earliest sync any instruction can see is GETP/WAITP at c = 3, which reads
    uio_in(1), and the earliest event deadline is 4. So sync during cycles 0–2 never
    reaches architectural state.

13. **§7.2 IRQ while already pending.** *ISS:* literal. A second IRQ overwrites
    irq_code with the newest code.

14. **§7.5 RXREADY/TXREADY polarity.** Z = 1 means "RX empty" or "TX full", which is
    *not ready*. The mnemonic suggests the opposite. *ISS:* literal. This is worth a
    firmware warning in 02-ISA.md.

15. **§10 v1.0.1 irq_clear.**
    (a) "Unless an IRQ instruction of context k commits at that same edge": *ISS* treats
    "commits" as outcome COMPLETE. An IRQ at PC 255 faults PC_RANGE and has no effect,
    so the clear applies.
    (b) The clear resets only irq_pending. irq_code is left as is, so sel 13 still shows
    the old code with bit 8 = 0.
    (c) A fault does not touch IRQ state.

## Unreachable or harness-level

16. **§6.3 LATE_HEAD.** This is implemented as a checked invariant, with PC = PC during
    cycle t. A white-box test injects a stale head to exercise it.

17. **§2 / §10 tick width.** `now` and all comparisons are modulo 2^32. The `t` field
    in the trace records is the unbounded run-cycle number, which is identical for runs
    shorter than 2^32 cycles. RDTIME and the first-fault tick are mod 2^32.

18. **Harness `stop_when_idle`.** The condition is evaluated on the state *during* cycle
    t (all contexts IDLE/HALTED/FAULTED, all FIFOs empty, no checker active). That
    cycle's pins are recorded and edge E_t is still processed, so `m.now = t + 1`.
    Consequence: pin values after the last DRIVE are held, but no records exist past t.
    A waveform consumer must extend the last level (for the UART firmware, the final
    stop bit's mid-sample at 8805 is past the last record at 8700).

19. **02-ISA.md mentions an EXPECT_BUSY fault.** It is not in the normative spec. It is
    ignored, and admission rule 6 (OVERLAP) makes it moot.

20. **§10 dbg `insn` in commit records for a FAULT.** This is the fetched word. `post`
    equals the state before the commit, because a fault has no architectural effect,
    and `post.pc` is the faulting PC.
