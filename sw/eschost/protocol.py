# Escapement host protocol v1.3 (docs/12-HOST-PROTOCOL.md) - frame builders/parsers.
# Plain Python, MicroPython-compatible (no typing/dataclasses), so the same code drives
# cocotb tests on a PC and the RP2350 loader on the Tiny Tapeout demo board.
#
# Every command is a "plan": (frame_bytes, parser). A transport performs ONE CS_n-low
# frame and returns the MISO bytes. Host (sync) and AsyncHost (async transports, e.g.
# cocotb) both execute the same plans, so there is one encoding of the protocol.

VERSION = 0x8
STATUS, WRITE_IMEM, READ_IMEM, WRITE_CFG = 0x00, 0x10, 0x11, 0x12
WRITE_TAPE, READ_TAPE, TAPE_ACK = 0x13, 0x14, 0x15
START, STOP, READ_DBG, MBX_PUSH, MBX_POP = 0x20, 0x21, 0x30, 0x40, 0x41
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


def plan_write_cfg(ctx_enable, entry_pcs, pin_owned, pin_owner, pin_od, ctx_bank=0):
    # Always the 13-byte v1.2 frame: a v1.1 chip ignores bytes after the ninth, and a
    # v1.2 chip never keeps a previous program's bank B settings.
    e = entry_pcs
    return bytes((WRITE_CFG, ctx_enable & 0xF, e & 0xFF, (e >> 8) & 0xFF, (e >> 16) & 0xFF,
                  (e >> 24) & 0xFF, pin_owned & 0xFF, (pin_owner >> 8) & 0xFF,
                  pin_owner & 0xFF, pin_od & 0xFF,
                  ctx_bank & 0xF, (pin_owned >> 8) & 0x1F, (pin_owner >> 24) & 0x3,
                  (pin_owner >> 16) & 0xFF)), _st


def plan_start():
    return bytes((START,)), _st


def plan_stop():
    return bytes((STOP,)), _st


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
    "tape_ack": plan_tape_ack,
}


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
        self.stop()
        n = image_extent(image)
        self.write_imem(0, image.words[:n])
        if verify:
            got = self.read_imem(0, n)
            bad = [i for i in range(n) if got[i] != image.words[i]]
            if bad:
                raise IOError("IMEM verify failed at words %s" % bad[:8])
        t0, tn = tape_extent(image)
        if tn:
            self.write_tape(t0, image.tape[t0:t0 + tn])
            if verify and self.read_tape(t0, tn) != image.tape[t0:t0 + tn]:
                raise IOError("tape verify failed")
        self.write_cfg(image.ctx_enable_mask, image.entry_pcs_packed, image.pin_owned_mask,
                       image.pin_owner_packed, image.pin_od_mask,
                       getattr(image, "ctx_bank_mask", 0))
        return n

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
        await self.stop()
        n = image_extent(image)
        await self.write_imem(0, image.words[:n])
        if verify:
            got = await self.read_imem(0, n)
            bad = [i for i in range(n) if got[i] != image.words[i]]
            if bad:
                raise IOError("IMEM verify failed at words %s" % bad[:8])
        t0, tn = tape_extent(image)
        if tn:
            await self.write_tape(t0, image.tape[t0:t0 + tn])
            if verify and await self.read_tape(t0, tn) != image.tape[t0:t0 + tn]:
                raise IOError("tape verify failed")
        await self.write_cfg(image.ctx_enable_mask, image.entry_pcs_packed,
                             image.pin_owned_mask, image.pin_owner_packed, image.pin_od_mask,
                             getattr(image, "ctx_bank_mask", 0))
        return n

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
