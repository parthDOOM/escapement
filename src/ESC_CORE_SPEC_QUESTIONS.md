# esc_core: spec questions and the choices the RTL makes

Against `docs/11-SEMANTICS.md` v1.0.1. For each question the RTL takes the most literal
reading it can find and records the choice here. **Reachable** means a legal program
(or host stimulus) can tell the difference, so the ISS must make the same choice for the
per-tick comparison to agree. "Test" names the case in `test/rtl_unit/test_core.py` that
pins the behaviour.

## Reachable (the ISS must match these)

| # | Section | Question | RTL choice | Test |
| --- | --- | --- | --- | --- |
| Q2 | §6.4, §8, §9 | A context faults at E_u, or `stop` is high during cycle u, while its checker is active, or while an EXPECT is due at E_u. Is tick u still evaluated (pass/fail count, `first_fail`) before the checker is deactivated? | **No.** The fault or stop deactivates the checker and suppresses that edge's evaluation, like "no entry retires at that edge" and "nothing due at E_t retires". An EXPECT due at that edge does not start. | `test_checker_at_kill_edge` |
| Q3 | §6.4, §7.3 GETX, §10 sel 10 | Is the checker "active" during cycle d, the cycle whose edge retires the EXPECT? | **No.** `active` is a register: it is 1 during cycles d+1..u, where u is the stop tick, and 0 during cycle d and from cycle u+1. GETX and sel 10 read this register during cycle c. HALT uses "not active after E_c", which includes the start and evaluation at E_c. | `test_expect_checker`, `test_halt_waits_checker` |
| Q5 | §9 | `start` and `stop` are both high in the same cycle. | **`start` wins**: the run is initialised and `stop` is ignored. | `test_checker_at_kill_edge` |
| Q7 | §9 | Is the instruction whose commit edge is the stop edge E_t discarded? | **Yes.** No commit record is produced and the instruction has no effects. Pipeline valid bits are cleared by both `stop` and `start`, so a commit at E_s (start edge) is also discarded. | `test_stop` |
| Q9 | §7.1 SHL/SHR | With n = 0 ("no change, C = 0"), is Z written? | **Yes.** Z = (R[rd] == 0), taken from the unchanged value written back, because the Flags column says "Z, C" and Z "is set from the written result". | `test_flags_shifts` (SHL r2, 0 / SHL r0, 0) |
| Q10 | §7.2 IRQ, §10 irq_clear | (a) A second IRQ while one is pending: is the code overwritten? (b) Does `irq_clear` clear the code? (c) What counts as "an IRQ instruction commits": does IRQ at PC 255, which faults PC_RANGE, still set the IRQ? | (a) **Overwritten**: `irq_code = code` on every COMPLETE IRQ. (b) **No**: only `irq_pending` is cleared, so sel 13 shows the old code with [8] = 0. (c) **Only a COMPLETE IRQ sets**; at PC 255 the clear wins. | `test_irq_and_clear` (a, b) |
| Q11 | §7.5, §10 mailboxes | (a) The host pushes into a full RX at the same edge as a POP. (b) The host pops an empty TX at the same edge as a PUSH. (c) Host access at the start edge. (d) What `mbx_tx_data` shows when TX is empty. | (a) **Push ignored**, because fullness is judged during the cycle. (b) **Pop ignored.** (c) **Start wins**: the mailboxes are emptied and that edge's push or pop is lost. (d) A **stale value** (the old head register); it is only meaningful when `mbx_tx_empty` is 0. Stop and faults do not touch the mailboxes. | `test_mailboxes` (full and empty during the cycle, same-edge invisibility) |
| Q12 | §2, §9, §10 `running` | When does a run end, and what does `running` show? | `running` is the run flag. It stays 1 after every context has halted or faulted, until `stop` (or a new `start`). `now` keeps counting meanwhile. | `test_four_context_slots` |
| Q16 | §7, §7.4 | Telemetry at PC 255. | A full-FIFO WAIT at PC 255 still counts in `wait_full`, because the outcome is WAIT and not COMPLETE. An admission that would COMPLETE at PC 255 becomes PC_RANGE: no append and no `min_lead`/`max_occupancy` update. | `test_fault_suppresses_same_edge` |
| Q17 | §6.2 step 5 | Q = {one entry} and that entry retires at E_c. | Q after retirement is empty, so ORDER is **not** checked (literal). | — (follows from the step 1 case in `test_fifo_full_wait`) |
| Q14 | §11 vs task test plan | The task suggested a loopback driver `uio_in = oe ? out : 1`. With that driver the released pin reads 1 from reset, so `sync[0]` does not "rise during cycle 13". | §11 says "`uio_in[0]` = output". The worked example is tested with `uio_in = oe ? out : 0`, and sync = 0 at tick 12 and 1 at tick 13 are checked through GETP in contexts 1 and 2. The pull-up form is used for the open-drain test. | `test_worked_example`, `test_open_drain` |

## Not observable at the ports (choice recorded for completeness)

| # | Section | Question | RTL choice |
| --- | --- | --- | --- |
| Q6 | §9 | Does `now` advance at the stop edge? | No. `now` keeps its cycle-t value. No port exposes `now` after a stop. |
| Q8 | §9 Reset | Reset values for state that §9 does not list: r0–r3, sh, lc, tmr, PC, lr, flags, ish, telemetry, checker counts and record, EXPECT history, mailbox data. | All 0, except `min_lead` = 0xFFFF (its start value) and PC = 0 (`entry_pcs` may be invalid at reset). Every flop has an asynchronous reset. |
| Q13 | §3 | `imem_addr` in cycles with no fetch (empty slot, or run = 0). | Always the PC of context `now mod 4`. The value is irrelevant because no commit uses it. |
| Q20 | §10 | `dbg_commit_ctx/pc/insn/outcome` while `dbg_commit_valid` = 0. | They are re-registered every cycle and are meaningful only when valid = 1. |

## Unreachable by construction (admission makes LATE_HEAD impossible)

| # | Section | Question | RTL choice |
| --- | --- | --- | --- |
| Q1 | §3, §8 | A context stops being RUNNING between fetch (t) and commit (t+3). This is only possible through LATE_HEAD at E_t..E_(t+2). | The commit is squashed: no record and no effects. `commit_valid` requires state RUNNING during cycle c. |
| Q4 | §6.3, §8 | LATE_HEAD of the committing context at its commit edge, and several contexts faulting at one edge. | The commit outcome is F with no effects. The first-fault cause is the commit's own fault if it has one, else LATE_HEAD (PC = that context's PC during cycle c). Between contexts, the commit fault wins, then the lowest-numbered context's LATE_HEAD. A LATE_HEAD at a stop edge is ignored. |

Why LATE_HEAD cannot happen: an appended entry has d >= c+1. Entries are strictly increasing
under `ahead`. Each head is examined at every edge while `now` steps by 1. When the head
with d = t retires at E_t, the next head has d' >= t+1. So `reached(t, head) && head != t`
never holds. `stop` and faults flush the FIFO, and `start` empties it. The check is
implemented but cannot be exercised by a legal program.
