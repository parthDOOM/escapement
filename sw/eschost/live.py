"""Live reload: the checks a host makes before it (CPython: uses escert).

plan_reload(running, new, ctx) returns the program-store runs that turn `running` (the image
the chip runs) into `new` for context ctx, after checking that the reload cannot disturb any
other context or break ctx's own timing:

- the configuration is unchanged (enables, entry PCs, pins, banks, pairing): a reload keeps
  the context's pins and slots, so the new program must be built for the same ones;
- no changed word is in a page another enabled context fetches from: its own page (v2, S-A)
  and, for a spanning context (3.1), its partner's. This is the assumption of the
  non-interference proof (formal/esc_ni_env.v: a live write never lands in a page j fetches
  from), and with it the proof covers the reload. A program store of one page (256 words)
  is shared by every context, so it can be reloaded only while no other context runs;
  give each context its own page instead (escasm.paged);
- no other enabled context can reach an instruction of a shared unit (the capture unit, the
  line unit, the bus timing monitor), the proof's other assumption;
- escert certifies ctx in `new` (CHECKED): a restarted context sees what a run start shows
  it, so the certificate holds for the reloaded program as it would after START.
"""

from __future__ import annotations

from escasm import page_view, spans
from escert import _decode, _reach, analyse

# The instructions of the capture unit, the line unit and the bus timing monitor: a unit
# belongs to its first user in a run, so a context that uses one can be shaped by another.
# The non-interference proof assumes none of them in the words of the context it protects
# (ni_ok_insn in formal/esc_ni_env.v). GETM stays allowed: it reads 0 in a context that
# owns neither unit.
SHARED_UNIT_OPS = frozenset({"CAPT", "GETC", "SEND", "LINE", "GETL", "MON"})

CONFIG = ("ctx_enable_mask", "entry_pcs_packed", "pin_owned_mask", "pin_owner_packed",
          "pin_od_mask", "ctx_bank_mask", "ctx_pair_mask", "ctx_page_packed", "ctx_span_mask")


def plan_reload(running, new, ctx: int) -> list:
    for f in CONFIG:
        if getattr(running, f, 0) != getattr(new, f, 0):
            raise ValueError(f"reload changes the configuration ({f}); load() it while stopped")
    if not running.ctx_enable[ctx]:
        raise ValueError(f"context {ctx} is not enabled")
    if len(running.words) != len(new.words):
        raise ValueError("reload changes the program store's size; load() it while stopped")
    changed = [a for a in range(len(new.words)) if running.words[a] != new.words[a]]
    if not changed:
        return []
    paged = len(new.words) > 256
    for k in range(4):
        if k != ctx and running.ctx_enable[k]:
            # the pages k fetches from: its own, and a spanning k's partner's
            pages = {running.ctx_page[k] if paged else 0}
            if paged and spans(running, k):
                pages.add(running.ctx_page[k ^ 2])
            hit = sorted(a for a in changed if a // 256 in pages)
            if hit:
                raise ValueError(f"reload writes page {hit[0] // 256}, which context {k} fetches "
                                 f"from (words {hit[:8]}); give each context its own page, or "
                                 f"load() it while stopped")
            view = page_view(running, k)
            units = sorted({n for n in (_decode(view.words[pc], len(view.words) - 1)[0]
                                        for pc in _reach(running, k)) if n in SHARED_UNIT_OPS})
            if units:
                raise ValueError(f"context {k} uses a shared unit ({', '.join(units)}), which "
                                 f"the reloaded context could own or configure; load() it while stopped")
    cert = analyse(new, ctx)
    if cert.status != "CHECKED":
        raise ValueError(f"context {ctx} of the new image is not certified: {cert.status}")
    runs, start = [], None
    for a in changed + [None]:
        if start is not None and a == prev + 1 and a % 256:      # a run stays in one page
            prev = a
            continue
        if start is not None:
            runs.append((start, new.words[start:prev + 1]))
        start = prev = a
    return runs
