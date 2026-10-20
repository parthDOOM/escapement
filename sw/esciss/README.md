# esciss: Escapement instruction-set simulator

This is a cycle-by-cycle Python model of `docs/11-SEMANTICS.md` (v1.4.0). It is
written independently of the RTL for N-version comparison. Only the encoding tables are
shared, via `escasm.load_isa()` reading `isa/isa.yaml`. All behaviour is hand-written
from the semantics document.

```python
from escasm import assemble_file
from esciss import Machine

m = Machine(assemble_file("firmware/uart_tx.asm"))   # board=None -> pull-up loopback
                                                     # board_b=None -> bank B inputs low
m.run(20000)                                         # stops when idle by default
m.commits    # [{"t","ctx","pc","insn","outcome","post":{r0..r3,sh,lc,tmr,z,c,pc}}]
m.pins       # [{"t","uio_out","uio_oe","ob"}] per run cycle (ob = uo_out[7:3])
m.dbg_read(0, 9)                                     # §10 dbg_sel map
m.write_trace("out.jsonl")                           # commits, pins, {"final": {...}}
```

CLI: `python -m esciss image.json --ticks N [--trace out.jsonl]`. An `.asm` path is also
accepted.

## Model structure (`machine.py`)

Each loop iteration is one run cycle t, ending with edge E_t (`Machine._cycle`):

1. Outputs during t come from the pin registers (§5, `_pin_outputs`, `_ob`). They are
   recorded, and then `uio_in(t) = board(t, uio_out, uio_oe)` and, for bank B,
   `ui_in[7:3](t) = board_b(t, ob)`. Bus models keep the 8-bit `board` interface;
   `escmodels.SplitBoard` puts the same models on bank B.
2. The 13 synchronised physical inputs during t are the s2 register (§5). Each context
   sees its bank of them as its 8 logical pins (`view`), and the capture unit the bank
   its CAPT named.
3. Fetch (§3, `_fetch`): context `t mod 4` latches `(PC, IMEM[PC])` for commit at t+3.
4. Edge E_t (`_edge`) runs in two phases, so every decision uses state during t.
   - The **plan** phase evaluates the event engine for each context (§6.3 retirement,
     LATE_HEAD, and the §6.4 checker, in `_event_plan`) and the commit of context
     `(t-3) mod 4` (§7, `_commit`, with §6.2 admission in `_admit`).
   - The **apply** phase applies, in order: a fault (§8, `_fault`, which suppresses
     retirement), or else the GETI clear, retirement, the checker, and the commit
     effects. Mailboxes and host ports (`_apply_mailboxes`) and irq_clear
     (`_apply_irq_clear`) follow.
5. Synchroniser shift, then `now += 1`.

The line unit (§6.6, v1.4) is `line.py`, a `LineUnit` stepped once per edge in the apply
phase after the contexts' commits (`LineUnit.step`), with two plan-phase queries: whether
the edge is one of its tape access cycles (`access_scheduled`, which makes the owner's
LDT/STT wait) and whether a CONFIG completing now faults. Its pin writes go through the
owner's bank after DRIVE retirement, so only the unit drives the pins it claims. Its tests
are `sw/tests/test_line.py`.

Reset and start (§9) run in the constructor. They include one pre-start cycle s = −1
whose `uio_in = board(-1, 0, 0)` feeds the synchroniser (see SPEC_QUESTIONS.md #12).

Host side (applied at the next edge, never visible to that edge's commit):
- `host_rx_push(ctx, v)` and `host_tx_pop(ctx)` work on the mailboxes.
- `host_irq_clear(mask)` drives irq_clear (§10 v1.0.1).
- `stop()` asserts §9 Stop.

Status properties are `irq_pending` (4 bits) and `fault_valid`. `outputs()` returns the
current `(uio_out, uio_oe)`.

Where the spec is silent or ambiguous, see `SPEC_QUESTIONS.md`.

Tests are in `sw/tests/test_esciss.py`, with expected values derived by hand from the
spec. Run them from `sw/` with `python -m pytest tests/test_esciss.py`.
