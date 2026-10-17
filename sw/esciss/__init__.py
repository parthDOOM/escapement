"""Escapement instruction-set simulator (cycle-by-cycle model of docs/11-SEMANTICS.md v1.0.1).

Written independently of the RTL; shares only the encoding tables (isa/isa.yaml via escasm).
"""

from escasm import Image

from .machine import (ARRIVE, FAULTED, HALTED, HOLD, IDLE, RUNNING, Machine, ahead,
                      default_board, reached, shift_in)

__all__ = ["Machine", "Image", "ahead", "reached", "shift_in", "default_board",
           "IDLE", "RUNNING", "HALTED", "FAULTED", "HOLD", "ARRIVE"]
