# Escapement host protocol v1.3 (docs/12-HOST-PROTOCOL.md) - frame builders/parsers.
# Plain Python, MicroPython-compatible (no typing/dataclasses), so the same code drives
# cocotb tests on a PC and the RP2350 loader on the Tiny Tapeout demo board.
#
# Every command is a "plan": (frame_bytes, parser). A transport performs ONE CS_n-low
# frame and returns the MISO bytes. Host (sync) and AsyncHost (async transports, e.g.
# cocotb) both execute the same plans, so there is one encoding of the protocol.

VERSION = 0x9                 # v1.7: WRITE_CFG byte 10 bits 7:4 (pair mode)
STATUS, WRITE_IMEM, READ_IMEM, WRITE_CFG = 0x00, 0x10, 0x11, 0x12
WRITE_TAPE, READ_TAPE, TAPE_ACK = 0x13, 0x14, 0x15
START, STOP, READ_DBG, MBX_PUSH, MBX_POP = 0x20, 0x21, 0x30, 0x40, 0x41
CTX_STOP, CTX_START = 0x22, 0x23    # v1.7 (S3): live reload of one context
IMEM_PAGE = 0x24                    # v2 (S-A): the program-store page WRITE/READ_IMEM use
PAGES_VERSION = 0xA                 # v2 (S-A): the first protocol version with pages
IRQ_CLEAR, CLEAR_ERR = 0x50, 0x51


def _w32(v):
    return bytes(((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))


def _r32(b, i):
    return (b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]


def parse_status(byte):
    return {"running": bool(byte & 0x80), "irq": bool(byte & 0x40),
            "fault": bool(byte & 0x20), "host_err": bool(byte & 0x10),
            "version": byte & 0x0F}


def _st(r):
    return parse_status(r[0])


def check_pair(image, st):
    """v1.7: a paired image needs a chip that pairs (protocol version 9); an older chip
    ignores the bits, would run it unpaired, and its timing certificate would not hold."""
    if getattr(image, "ctx_pair_mask", 0) and st["version"] < 9:
        raise IOError("image uses pair mode, chip reports protocol version %d (needs 9)"
                      % st["version"])


def is_paged(image):
    """v2 (S-A): an image of four pages (1,024 words)."""
    return len(image.words) > 256


def check_pages(image, st):
    """v2: a paged image needs a chip with pages (protocol version 10); an older chip has
    one page and would run every context from it."""
    if is_paged(image) and st["version"] < PAGES_VERSION:
        raise IOError("image uses program pages, chip reports protocol version %d (needs %d)"
                      % (st["version"], PAGES_VERSION))


def plan_status():
    def parse(r):
        s = parse_status(r[0])
        s["ctx_state"] = r[1]
        s["rx_full"] = r[2] >> 4
        s["tx_empty"] = r[2] & 0xF
        s["irq_pending"] = r[3] & 0xF
        return s
    return bytes((STATUS, 0, 0, 0)), parse


def plan_write_imem(addr, words):
    payload = bytearray((WRITE_IMEM, addr & 0xFF))
    for w in words:
        payload += _w32(w)
    return bytes(payload), _st


def plan_read_imem(addr, count):
    return (bytes((READ_IMEM, addr & 0xFF, 0)) + bytes(4 * count),
            lambda r: [_r32(r, 3 + 4 * i) for i in range(count)])


def plan_write_tape(addr, words):
    payload = bytearray((WRITE_TAPE, addr & 0xFF))
    for w in words:
        payload += _w32(w)
    return bytes(payload), _st


def plan_read_tape(addr, count):
    return (bytes((READ_TAPE, addr & 0xFF, 0)) + bytes(4 * count),
            lambda r: [_r32(r, 3 + 4 * i) for i in range(count)])


def plan_tape_ack(rptr):
    # v1.3: the capture records before rptr have been read (streaming capture)
    return bytes((TAPE_ACK, rptr & 0xFF)), _st


def stream_chunks(base, lim, rptr, ptr):
    """The unread words of a stream-mode region [base, lim) from rptr up to ptr, as
    (addr, count) runs split where the region wraps (READ_TAPE reads consecutive
    addresses). At most 256 words, so a ptr outside the region cannot loop forever."""
    runs, a, start, n = [], rptr & 0xFF, rptr & 0xFF, 0
    for _ in range(256):
        if a == ptr:
            break
        n += 1
        a = (a + 1) & 0xFF
        if a == lim:
            a = base
        if a == base or a == ptr:
            runs.append((start, n))
            start, n = a, 0
    return runs


def plan_write_cfg(ctx_enable, entry_pcs, pin_owned, pin_owner, pin_od, ctx_bank=0, ctx_pair=0,
                   ctx_page=None):
    # Always the 13-byte v1.2 frame: a v1.1 chip ignores bytes after the ninth, and a
    # v1.2 chip never keeps a previous program's bank B settings. v1.7: byte 10 bits 7:4 are
    # the pair-mode contexts (an older chip ignores them). v2 (S-A): byte 14, the contexts'
    # pages, when ctx_page is given: load() gives it to every chip with pages, so none keeps
    # a previous program's pages, and an older chip's frame is unchanged.
    e = entry_pcs
    frame = bytes((WRITE_CFG, ctx_enable & 0xF, e & 0xFF, (e >> 8) & 0xFF, (e >> 16) & 0xFF,
                  (e >> 24) & 0xFF, pin_owned & 0xFF, (pin_owner >> 8) & 0xFF,
                  pin_owner & 0xFF, pin_od & 0xFF,
                  (ctx_bank & 0xF) | (ctx_pair & 0xF) << 4, (pin_owned >> 8) & 0x1F, (pin_owner >> 24) & 0x3,
                  (pin_owner >> 16) & 0xFF))
    if ctx_page is not None:
        frame += bytes((ctx_page & 0xFF,))
    return frame, _st


def plan_imem_page(page):
    # v2 (S-A): the page the following WRITE_IMEM and READ_IMEM address (refused while a
    # live word waits, as a new address is)
    return bytes((IMEM_PAGE, page & 3)), _st


def plan_start():
    return bytes((START,)), _st


def plan_stop():
    return bytes((STOP,)), _st


def plan_ctx_stop(mask):
    # v1.7 (S3): stop these contexts at the end of the mask byte (during a run only)
    return bytes((CTX_STOP, mask & 0xF)), _st


def plan_ctx_start(mask):
    # v1.7 (S3): restart these contexts at their first slot (each enabled and not RUNNING)
    return bytes((CTX_START, mask & 0xF)), _st


def plan_read_dbg(ctx, sel):
    # v1.1: select byte {0, sel[4], ctx[1:0], sel[3:0]}
    sb = ((sel & 0x10) << 2) | ((ctx & 3) << 4) | (sel & 0xF)
    return bytes((READ_DBG, sb, 0, 0, 0, 0, 0)), lambda r: _r32(r, 3)


def plan_mbx_push(ctx, word):
    return bytes((MBX_PUSH, ctx & 3)) + _w32(word), _st


def plan_mbx_pop(ctx):
    return (bytes((MBX_POP, ctx & 3, 0, 0, 0, 0, 0)),
            lambda r: _r32(r, 3) if r[2] & 1 else None)


def plan_irq_clear(mask):
    return bytes((IRQ_CLEAR, mask & 0xF)), _st


def plan_clear_err():
    return bytes((CLEAR_ERR,)), _st


def image_extent(image):
    """Number of words to load: up to the last nonzero word (0 is ILLEGAL fill)."""
    last = 0
    for i, w in enumerate(image.words):
        if w:
            last = i + 1
    return last


PLANS = {
    "status": plan_status, "write_imem": plan_write_imem, "read_imem": plan_read_imem,
    "write_cfg": plan_write_cfg, "start": plan_start, "stop": plan_stop,
    "read_dbg": plan_read_dbg, "mbx_push": plan_mbx_push, "mbx_pop": plan_mbx_pop,
    "irq_clear": plan_irq_clear, "clear_err": plan_clear_err,
    "write_tape": plan_write_tape, "read_tape": plan_read_tape,
    "tape_ack": plan_tape_ack, "ctx_stop": plan_ctx_stop, "ctx_start": plan_ctx_start,
    "imem_page": plan_imem_page,
}


def page_loads(image):
    """[(page or None, first word, words)]: what load() writes. A 256-word image is one
    write at 0 with no IMEM_PAGE (as on every older chip); a paged one is one write per
    page holding code, each after IMEM_PAGE."""
    if not is_paged(image):
        return [(None, 0, image.words[:image_extent(image)])]
    out = []
    for p in range(4):
        words = image.words[256 * p:256 * (p + 1)]
        n = 0
        for i, w in enumerate(words):
            if w:
                n = i + 1
        if n:
            out.append((p, 256 * p, words[:n]))
    return out


def reload_writes(paged, runs):
    """[(page or None, address in the page, words)] for reload's runs (store addresses
    from eschost.live.plan_reload, where a run never crosses a page)."""
    return [((a >> 8) if paged else None, a & 0xFF, w) for a, w in runs]


def tape_extent(image):
    """(first, count) covering every word set by .tape, or (0, 0)."""
    used = getattr(image, "tape_used", [])
    if not used:
        return 0, 0
    return min(used), max(used) - min(used) + 1


def _poll_plan(base, lim, rptr, status_word):
    # A ptr outside the region means the capture is not armed in it (yet): nothing new.
    ptr = status_word & 0xFF
    if ((ptr - base) & 0xFF) >= (((lim - base) & 0xFF) or 256):
        return rptr, []
    return ptr, stream_chunks(base, lim, rptr, ptr)


class Host:
    """Synchronous host: xfer(bytes) -> bytes performs one frame."""

    def __init__(self, xfer):
        self.xfer = xfer

    def __getattr__(self, name):
        plan = PLANS.get(name)
        if plan is None:
            raise AttributeError(name)

        def run(*args):
            frame, parse = plan(*args)
            return parse(self.xfer(frame))
        return run

    def load(self, image, verify=True):
        st = self.stop()
        check_pair(image, st)
        check_pages(image, st)
        n = 0
        for page, base, words in page_loads(image):
            if page is not None:
                self.imem_page(page)
            self.write_imem(0, words)
            if verify:
                got = self.read_imem(0, len(words))
                bad = [base + i for i in range(len(words)) if got[i] != words[i]]
                if bad:
                    raise IOError("IMEM verify failed at words %s" % bad[:8])
            n += len(words)
        if is_paged(image):
            self.imem_page(0)
        t0, tn = tape_extent(image)
        if tn:
            self.write_tape(t0, image.tape[t0:t0 + tn])
            if verify and self.read_tape(t0, tn) != image.tape[t0:t0 + tn]:
                raise IOError("tape verify failed")
        self.write_cfg(image.ctx_enable_mask, image.entry_pcs_packed, image.pin_owned_mask,
                       image.pin_owner_packed, image.pin_od_mask,
                       getattr(image, "ctx_bank_mask", 0), getattr(image, "ctx_pair_mask", 0),
                       getattr(image, "ctx_page_packed", 0) if st["version"] >= PAGES_VERSION
                       else None)
        return n

    def reload(self, ctx, runs, paged=False):
        """v1.7 (S3) live reload of context ctx during a run: CTX_STOP, the program words
        `runs` ([(addr, [words])], from eschost.live.plan_reload, which checks they are
        certified and touch no word a running context can reach), CTX_START. The chip
        writes each word in a cycle with no fetch. READ_IMEM reads only while stopped, so
        the words are not read back here; a write the chip could not take sets host error,
        which is checked (CLEAR_ERR returns the status before clearing it)."""
        st = self.status()
        if not st["running"]:
            raise IOError("reload needs a running chip (load() while stopped)")
        self.ctx_stop(1 << ctx)
        for page, addr, words in reload_writes(paged, runs):
            if page is not None:
                self.imem_page(page)
            self.write_imem(addr, words)
        if paged:
            self.imem_page(0)
        self.ctx_start(1 << ctx)
        if self.clear_err()["host_err"]:
            raise IOError("reload of context %d: the chip reported a host error" % ctx)

    def stream_poll(self, base, lim, rptr, retries=3):
        """One round of streaming capture (v1.3): read the capture status, every record
        from rptr up to ptr, then acknowledge them. Returns (words, new rptr, status).
        A word the chip could not fetch in time sets host error; the round then re-reads
        (CLEAR_ERR returns the status byte before clearing it)."""
        st = self.read_dbg(0, 17)
        ptr, runs = _poll_plan(base, lim, rptr, st)
        for _ in range(retries + 1):
            words = []
            for a, n in runs:
                words += self.read_tape(a, n)
            if not self.clear_err()["host_err"]:
                break
        else:
            raise IOError("tape reads kept failing: the tape port is never free")
        if runs:
            self.tape_ack(ptr)
        return words, ptr, st


class AsyncHost:
    """Asynchronous host: `await xfer(bytes)` -> bytes (cocotb drivers)."""

    def __init__(self, xfer):
        self.xfer = xfer

    def __getattr__(self, name):
        plan = PLANS.get(name)
        if plan is None:
            raise AttributeError(name)

        async def run(*args):
            frame, parse = plan(*args)
            return parse(await self.xfer(frame))
        return run

    async def load(self, image, verify=True):
        st = await self.stop()
        check_pair(image, st)
        check_pages(image, st)
        n = 0
        for page, base, words in page_loads(image):
            if page is not None:
                await self.imem_page(page)
            await self.write_imem(0, words)
            if verify:
                got = await self.read_imem(0, len(words))
                bad = [base + i for i in range(len(words)) if got[i] != words[i]]
                if bad:
                    raise IOError("IMEM verify failed at words %s" % bad[:8])
            n += len(words)
        if is_paged(image):
            await self.imem_page(0)
        t0, tn = tape_extent(image)
        if tn:
            await self.write_tape(t0, image.tape[t0:t0 + tn])
            if verify and await self.read_tape(t0, tn) != image.tape[t0:t0 + tn]:
                raise IOError("tape verify failed")
        await self.write_cfg(image.ctx_enable_mask, image.entry_pcs_packed,
                             image.pin_owned_mask, image.pin_owner_packed, image.pin_od_mask,
                             getattr(image, "ctx_bank_mask", 0),
                             getattr(image, "ctx_pair_mask", 0),
                             getattr(image, "ctx_page_packed", 0)
                             if st["version"] >= PAGES_VERSION else None)
        return n

    async def reload(self, ctx, runs, paged=False):
        """As Host.reload."""
        if not (await self.status())["running"]:
            raise IOError("reload needs a running chip (load() while stopped)")
        await self.ctx_stop(1 << ctx)
        for page, addr, words in reload_writes(paged, runs):
            if page is not None:
                await self.imem_page(page)
            await self.write_imem(addr, words)
        if paged:
            await self.imem_page(0)
        await self.ctx_start(1 << ctx)
        if (await self.clear_err())["host_err"]:
            raise IOError("reload of context %d: the chip reported a host error" % ctx)

    async def stream_poll(self, base, lim, rptr, retries=3):
        """As Host.stream_poll."""
        st = await self.read_dbg(0, 17)
        ptr, runs = _poll_plan(base, lim, rptr, st)
        for _ in range(retries + 1):
            words = []
            for a, n in runs:
                words += await self.read_tape(a, n)
            if not (await self.clear_err())["host_err"]:
                break
        else:
            raise IOError("tape reads kept failing: the tape port is never free")
        if runs:
            await self.tape_ack(ptr)
        return words, ptr, st
