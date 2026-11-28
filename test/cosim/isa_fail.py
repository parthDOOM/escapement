"""Failures of the instruction checker (formal/esc_isa_check.v) in a co-simulation's output.

The co-simulation builds the checker in by default (test/cosim/Makefile, ISA=yes), and a
failed assertion of it is reported by the simulator, not by cocotb:

- Icarus Verilog prints ``ERROR: <path>/esc_isa_check.v:<line>:`` and a ``Time: ... Scope: ...``
  line, and carries on: the cocotb test still passes.
- Verilator prints ``[<time>] %Error: esc_isa_check.v:<line>: Assertion failed in <scope>``
  and stops the simulation, so the cocotb test fails too.

So every harness that runs the co-simulation searches its output with ``failures`` and counts
a nonzero result as a failure: test/cosim/run_parallel.py (``make cosim-parallel``, which CI
runs) and the mutation campaign (test/mutation/run_mutants.py). A plain ``make cosim`` prints
the failures but does not fail on them.
"""

from __future__ import annotations

import re

ISA_FAIL = re.compile(
    r"^(?:\[\d+\] )?(?:ERROR|%Error): \S*esc_isa_check\.v:\d+:.*(?:\n\s+Time: .*)?", re.M)


def failures(out: str) -> list[str]:
    """Each failed check in a simulator's output, as printed (one or two lines each)."""
    return ISA_FAIL.findall(out)
