"""v1.7 (S3) live reload, the checks a host makes before it (CPython: uses escert).

plan_reload(running, new, ctx) returns the program-store runs that turn `running` (the image
the chip runs) into `new` for context ctx, after checking that the reload cannot disturb any
other context or break ctx's own timing:

- the configuration is unchanged (enables, entry PCs, pins, banks, pairing): a reload keeps
  the context's pins and slots, so the new program must be built for the same ones;
- every changed word is outside what any other enabled context can reach in `running`
  (escert's control-flow reach), so no other context ever fetches a changed word. v2
  (S-A): a context fetches only from its own page, so only words in its page can matter
  to it; a context alone in its page can be reloaded whatever the others reach;
- escert certifies ctx in `new` (CHECKED): a restarted context sees what a run start shows
  it, so the certificate holds for the reloaded program as it would after START.
"""

from __future__ import annotations

from escert import _reach, analyse

CONFIG = ("ctx_enable_mask", "entry_pcs_packed", "pin_owned_mask", "pin_owner_packed",
          "pin_od_mask", "ctx_bank_mask", "ctx_pair_mask", "ctx_page_packed")


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
            base = 256 * running.ctx_page[k] if paged else 0
            mine = {a - base for a in changed if base <= a < base + 256}   # in k's page
            shared = mine & _reach(running, k)
            if shared:
                raise ValueError(f"reload changes words context {k} can reach: "
                                 f"{sorted(a + base for a in shared)[:8]}")
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
