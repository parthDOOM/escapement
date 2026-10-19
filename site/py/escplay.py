"""The site's playground: assemble a firmware, run it on the ISS against bus models.

The same module runs in two places. In the browser (Pyodide, in a web worker) it runs
whatever the visitor typed. At build time, site/tools/runs.py calls it with the stock
firmware to produce the waveforms the page shows before Python has loaded. Nothing
here models the chip: the assembler, the simulator, the certificate checker and the
bus models are the ones in sw/, unchanged; this file only wires them together and
turns the results into plain lists for the page.
"""
from __future__ import annotations

import json
from pathlib import Path

import escasm.asm as _asm
from escasm import AsmError, assemble, load_isa
from escasm.disasm import disassemble
from escert import certify
from esciss import Machine
from escmodels import (Board, CanBus, HostScript, I2cTarget, JtagTap, OneWireDevice, Ps2Host,
                       SpiTarget, SplitBoard, SwdTarget, UartRx, UartTx, Ws2812Decoder,
                       crc8_maxim)

TICK_NS = 40.0
TPB = 25e6 / 115200                     # UART ticks per bit at 115,200 baud
MAX_TICKS = 400_000                     # the browser's budget for one run
FW = Path(__file__).resolve().parents[2] / "firmware"


def set_firmware_dir(path: str) -> None:
    global FW
    FW = Path(path)


# --------------------------------------------------------------------------- scenarios
#
# A scenario is the world around the chip: bus models on the pins, a host feeding the
# mailboxes, how long to run, and how to say in one line what the models observed.
# Parameters are what a visitor may change without touching the firmware.

class ParamError(ValueError):
    """A parameter the visitor typed that the scenario cannot use."""


def _hexbytes(s: str, what: str, limit: int = 32) -> list[int]:
    out = []
    for tok in str(s).replace(",", " ").split():
        try:
            v = int(tok, 16)
        except ValueError:
            raise ParamError(f"{what}: '{tok}' is not a hex byte") from None
        if not 0 <= v <= 0xFF:
            raise ParamError(f"{what}: {tok} does not fit in a byte")
        out.append(v)
    if len(out) > limit:
        raise ParamError(f"{what}: at most {limit} bytes")
    return out


def _hex(s, what: str, bits: int) -> int:
    try:
        v = int(str(s).strip() or "x", 16)
    except ValueError:
        raise ParamError(f"{what}: '{s}' is not a hex number") from None
    if not 0 <= v < (1 << bits):
        raise ParamError(f"{what}: {s} does not fit in {bits} bits")
    return v


def _int(s, what: str, lo: int, hi: int) -> int:
    try:
        v = int(str(s).strip())
    except ValueError:
        raise ParamError(f"{what}: '{s}' is not a whole number") from None
    if not lo <= v <= hi:
        raise ParamError(f"{what}: between {lo} and {hi}")
    return v


def _text(b) -> str:
    return " ".join("%02X" % x for x in b) or "nothing"


class World:
    """What a scenario sets up: boards, host and a run plan."""

    def __init__(self, board=None, board_b=None, script=None, ticks=10_000):
        self.board = board or Board()
        self.board_b = board_b
        self.script = script
        self.ticks = ticks
        self.steps = None               # optional callable(machine) for scripted hosts
        self.result = lambda m: ""


def sc_uart_tx(p):
    rx = UartRx(0, TPB)
    w = World(Board(rx), ticks=40_000)
    w.result = lambda m: "Receiver model decoded %s%s" % (
        _text(rx.bytes), " (%s)" % repr(bytes(rx.bytes).decode("latin-1")) if rx.bytes else "")
    return w


def sc_uart_rx(p):
    data = str(p.get("text", "Hi!")).encode("latin-1", "replace")
    if not 1 <= len(data) <= 24:
        raise ParamError("Peer sends: 1 to 24 characters")
    err = _int(p.get("baud_err", 0), "Peer baud error", -2, 2) / 100.0
    tx = UartTx(1, TPB * (1 + err), data, start=500, gap_bits=0.0)
    script = HostScript([], drain=[1], poll=1400)
    board = Board(tx, script)
    w = World(board, script=script, ticks=min(MAX_TICKS, tx.end + 4000))
    w.result = lambda m: "The chip pushed %s to the host (sent: %s)" % (
        _text([v & 0x1FF for v in script.received[1]]), _text(data))
    return w


def sc_i2c(p):
    addr = _hex(p.get("addr", "42"), "Address", 7)
    target = _hex(p.get("target", "42"), "Target address", 7)
    reg = _hex(p.get("reg", "05"), "Register", 8)
    data = _hex(p.get("data", "A5"), "Data", 8)
    read = p.get("op", "write") == "read"
    dev = I2cTarget(scl=2, sda=3, addr7=target, regs={reg: 0x00, 6: 0x5C}, mode="fast",
                    tick_ns=TICK_NS, stretch_ticks=37)
    cmd = (addr << 24) | (reg << 16) | (data << 8) | (2 if read else 1)
    script = HostScript([(50, 0, cmd)], drain=[0], poll=500)
    w = World(Board(dev, script), script=script, ticks=4500)

    def result(m):
        got = script.received[0]
        if not got:
            return "No reply yet"
        r = got[0]
        acks = "ACK" if not (r >> 8) & 0xFF else "NACK in 0b{:b}".format((r >> 8) & 0xFF)
        if read:
            return "Controller read 0x%02X from register 0x%02X (%s)" % (r & 0xFF, reg, acks)
        return "Target register 0x%02X now holds 0x%02X (%s)" % (reg, dev.regs.get(reg, 0), acks)
    w.result = result
    return w


def sc_spi(p):
    mode = _int(p.get("mode", 0), "Mode", 0, 3)
    tx = _hexbytes(p.get("bytes", "A5 5A"), "Send", 8)
    reply = _hexbytes(p.get("reply", "3C C3 81"), "Target answers", 8) or [0]
    if not tx:
        raise ParamError("Send: at least one byte")
    dev = SpiTarget(sck=4, mosi=5, miso=6, cs=7, mode=mode, reply=bytes(reply))
    pushes = [(20, 2, ((mode >> 1) << 1) | (mode & 1))]
    for i, b in enumerate(tx):
        pushes.append((100 + 10 * i, 2, b | (0x100 if i < len(tx) - 1 else 0)))
    script = HostScript(pushes, drain=[2], poll=300)
    w = World(Board(dev, script), script=script, ticks=900 + 500 * len(tx))
    w.result = lambda m: "Target model received %s and answered %s" % (
        _text(dev.frames[0] if dev.frames else []), _text(script.received[2]))
    return w


def sc_ws2812(p):
    start = _hex(p.get("start", "102030"), "First colour", 24)
    step = _hex(p.get("step", "010203"), "Step", 24)
    count = _int(p.get("count", 6), "LEDs", 1, 16)
    dec = Ws2812Decoder(0, TICK_NS)
    script = HostScript([(30, 3, start), (40, 3, step), (2000, 3, count)], drain=[3], poll=97)
    w = World(Board(dec, script), script=script, ticks=4000 + 900 * count + 3000)

    def result(m):
        dec.finish()
        leds = dec.frames[0] if dec.frames else []
        return "Decoder model: %d LED%s, %s" % (len(leds), "" if len(leds) == 1 else "s",
                                                " ".join("%06X" % c for c in leds))
    w.result = result
    w.leds = lambda: (dec.frames[0] if dec.frames else [])
    return w


def sc_onewire(p):
    rom = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
    rom += bytes([crc8_maxim(rom)])
    dev = OneWireDevice(1, rom, TICK_NS)
    script = HostScript([(30, 3, 0x80000033)], drain=[3], poll=500)
    w = World(Board(dev, script), script=script, ticks=50_000)
    w.result = lambda m: ("Device model answered the reset with a presence pulse and "
                          "received command 0x%02X" % dev.written[0]) if dev.written else \
        "Device model saw no command"
    return w


def sc_can(p):
    ident = _hex(p.get("id", "123"), "Identifier", 11)
    data = _hexbytes(p.get("data", "DE AD"), "Data", 8)
    bus = CanBus(0, 1, 100)
    w = World(Board(bus), ticks=0)
    word0 = (ident << 16) | len(data)
    d = data + [0] * (8 - len(data))
    word1 = (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]
    word2 = (d[4] << 24) | (d[5] << 16) | (d[6] << 8) | d[7]

    def steps(m):
        m.run(200, stop_when_idle=False)
        for v in (word0, word1, word2):
            m.host_rx_push(0, v)
            m.run(50, stop_when_idle=False)
        # composing the frame takes the firmware a while (CRC and stuffing, bit by bit),
        # so run until the receiver has it, then a little longer for the ACK and EOF
        while not bus.frames and m.now < 90_000:
            m.run(2000, stop_when_idle=False)
        m.run(2000, stop_when_idle=False)
    w.steps = steps

    def result(m):
        if not bus.frames:
            return "Receiver model saw no frame"
        f = bus.frames[0]
        data = (" " + _text(f["data"])) if f["dlc"] else ""
        return "Receiver model: ID 0x%03X, %d data byte%s%s, CRC %s" % (
            f["id"], f["dlc"], "" if f["dlc"] == 1 else "s", data, "good" if f["crc_ok"] else "bad")
    w.result = result
    return w


def sc_jtag(p):
    idcode = _hex(p.get("idcode", "4BA00477"), "Target IDCODE", 32)
    tap = JtagTap(4, 5, 6, 7, idcode=idcode)
    w = World(Board(tap), ticks=0)
    got = []

    def steps(m):
        m.host_rx_push(2, 0)
        m.run(6000, stop_when_idle=False)
        got.append(m.host_tx_pop(2))
        m.run(1, stop_when_idle=False)
    w.steps = steps
    w.result = lambda m: "TAP model returned IDCODE 0x%08X; the TAP is back in %s" % (
        got[0] or 0, tap.state)
    return w


def sc_swd(p):
    dpidr = _hex(p.get("dpidr", "2BA01477"), "Target DPIDR", 32)
    tgt = SwdTarget(4, 5, dpidr=dpidr)
    w = World(Board(tgt), ticks=0)
    got = []

    def steps(m):
        m.host_rx_push(2, 0x80000004)            # connect, then read DPIDR
        m.run(1, stop_when_idle=False)
        for _ in range(1200):
            m.run(50, stop_when_idle=False)
            v = m.host_tx_pop(2)
            m.run(1, stop_when_idle=False)
            if v is not None:
                got.append(v)
            if len(got) == 2:
                break
    w.steps = steps

    def result(m):
        if len(got) < 2:
            return "No reply from the target model"
        return "Target model: %s, ACK %s, DPIDR 0x%08X" % (
            "switched to SWD" if tgt.mode == "swd" else "still in JTAG",
            "OK" if got[0] == 1 else got[0], got[1])
    w.result = result
    return w


def sc_ps2(p):
    codes = _hexbytes(p.get("codes", "1C F0 1C"), "Scancodes", 6)
    if not codes:
        raise ParamError("Scancodes: at least one")
    host = Ps2Host(4, 5)
    w = World(Board(host), ticks=0)

    def steps(m):
        for c in codes:
            m.host_rx_push(1, c)
            m.run(26_000, stop_when_idle=False)
            m.host_tx_pop(1)
            m.run(1, stop_when_idle=False)
    w.steps = steps
    w.result = lambda m: "Host model received %s, parity %s" % (
        _text(host.bytes), "good" if not host.errors() else "errors")
    return w


def sc_combo(p):
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    script = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A), (30, 0, 0x1C),
                         (40, 1, 0)], drain=[0, 1, 2], poll=300)
    spi_t = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=3, reply=b"\x3C\xC3\x81")
    uart = UartRx(4, TPB)
    w = World(Board(ps2, tap, script), SplitBoard(spi_t, uart, outputs=0b11011),
              script=script, ticks=40_000)
    w.result = lambda m: "PS/2 host got %s; JTAG IDCODE %s; SPI target got %s; UART receiver got %s" % (
        _text(ps2.bytes), "0x%08X" % script.received[1][0] if script.received[1] else "none",
        _text([b for f in spi_t.frames for b in f]), _text(uart.bytes))
    return w


def sc_blank(p):
    w = World(Board(), SplitBoard(outputs=0b11111), ticks=_int(p.get("ticks", 4000), "Run for", 100, MAX_TICKS))
    w.result = lambda m: "No device models on the pins"
    return w


def _sig(name, pin, bank="A"):
    return {"name": name, "pin": pin, "bank": bank}


SCENARIOS = [
    {"id": "uart", "title": "UART transmit", "firmware": "uart_tx.asm", "fn": sc_uart_tx,
     "signals": [_sig("TX", 0)], "params": [],
     "blurb": "115,200 baud, 8N1. One bit is 217 ticks: 8,680 ns, the closest whole-tick "
              "value to 8,681 ns. The bytes are in the MOVI and MOVHI lines; change them "
              "and run again."},
    {"id": "uart_rx", "title": "UART receive", "firmware": "uart_rx.asm", "fn": sc_uart_rx,
     "signals": [_sig("RX", 1)],
     "params": [{"key": "text", "label": "Peer sends", "type": "text", "default": "Hi!"},
                {"key": "baud_err", "label": "Peer baud error %", "type": "select",
                 "default": "0", "options": ["-2", "-1", "0", "1", "2"]}],
     "blurb": "A UART model sends text; the chip finds each start edge with WAITP and "
              "samples every bit at its centre with SAMPLE, at an exact tick."},
    {"id": "i2c", "title": "I2C controller", "firmware": "i2c_ctrl.asm", "fn": sc_i2c,
     "signals": [_sig("SCL", 2), _sig("SDA", 3)],
     "params": [{"key": "op", "label": "Operation", "type": "select", "default": "write",
                 "options": ["write", "read"]},
                {"key": "addr", "label": "Address (hex)", "type": "hex", "default": "42"},
                {"key": "reg", "label": "Register (hex)", "type": "hex", "default": "05"},
                {"key": "data", "label": "Data (hex)", "type": "hex", "default": "A5"},
                {"key": "target", "label": "Target answers at (hex)", "type": "hex",
                 "default": "42"}],
     "blurb": "Fast mode (400 kHz), open-drain: the chip only ever pulls low. The target "
              "stretches SCL after each byte and the controller waits for it. Give the "
              "target another address to see a NACK."},
    {"id": "spi", "title": "SPI controller", "firmware": "spi_ctrl.asm", "fn": sc_spi,
     "signals": [_sig("CS", 7), _sig("SCK", 4), _sig("MOSI", 5), _sig("MISO", 6)],
     "params": [{"key": "mode", "label": "Mode", "type": "select", "default": "0",
                 "options": ["0", "1", "2", "3"]},
                {"key": "bytes", "label": "Send (hex)", "type": "text", "default": "A5 5A"},
                {"key": "reply", "label": "Target answers (hex)", "type": "text",
                 "default": "3C C3 81"}],
     "blurb": "Any of the four SPI modes, chosen at run time by the host's first word. "
              "MISO comes from the target model."},
    {"id": "ws2812", "title": "WS2812 LED chain", "firmware": "ws2812.asm", "fn": sc_ws2812,
     "signals": [_sig("DIN", 0)],
     "params": [{"key": "start", "label": "First colour GRB (hex)", "type": "hex",
                 "default": "102030"},
                {"key": "step", "label": "Step per LED (hex)", "type": "hex",
                 "default": "010203"},
                {"key": "count", "label": "LEDs", "type": "int", "default": "6"}],
     "blurb": "Each bit is 1.24 us: a 0 is a short high pulse, a 1 a long one. The "
              "firmware computes every colour and streams the chain without a gap."},
    {"id": "onewire", "title": "1-Wire", "firmware": "onewire.asm", "fn": sc_onewire,
     "signals": [_sig("DQ", 1)], "params": [],
     "blurb": "Reset pulse, the device's presence pulse, then the Read ROM command, all "
              "on one open-drain wire."},
    {"id": "can", "title": "CAN 2.0A", "firmware": "can_tx.asm", "fn": sc_can,
     "signals": [_sig("TX", 0), _sig("RX", 1)],
     "params": [{"key": "id", "label": "Identifier (hex)", "type": "hex", "default": "123"},
                {"key": "data", "label": "Data (hex, up to 8)", "type": "text",
                 "default": "DE AD"}],
     "blurb": "250 kbit/s. The firmware computes CRC-15 and inserts stuff bits; the "
              "receiver model pulls the ACK slot low and the chip samples it."},
    {"id": "jtag", "title": "JTAG IDCODE", "firmware": "jtag_idcode.asm", "fn": sc_jtag,
     "signals": [_sig("TCK", 4), _sig("TMS", 5), _sig("TDI", 6), _sig("TDO", 7)],
     "params": [{"key": "idcode", "label": "Target IDCODE (hex)", "type": "hex",
                 "default": "4BA00477"}],
     "blurb": "TAP reset, walk to Shift-DR, shift out the 32-bit IDCODE, return to "
              "Run-Test/Idle."},
    {"id": "swd", "title": "ARM SWD", "firmware": "swd_idcode.asm", "fn": sc_swd,
     "signals": [_sig("SWCLK", 4), _sig("SWDIO", 5)],
     "params": [{"key": "dpidr", "label": "Target DPIDR (hex)", "type": "hex",
                 "default": "2BA01477"}],
     "blurb": "Line reset, the JTAG-to-SWD switch sequence, another line reset, then a "
              "debug-port read with turnaround, ACK and parity."},
    {"id": "ps2", "title": "PS/2 keyboard", "firmware": "ps2_device.asm", "fn": sc_ps2,
     "signals": [_sig("CLK", 4), _sig("DATA", 5)],
     "params": [{"key": "codes", "label": "Scancodes (hex)", "type": "text",
                 "default": "1C F0 1C"}],
     "blurb": "The keyboard side: the chip generates the clock, the reverse of the usual "
              "host-clocked buses, and computes odd parity."},
    {"id": "combo", "title": "Four protocols at once", "firmware": "combo_two_banks.asm",
     "fn": sc_combo,
     "signals": [_sig("PS/2 CLK", 0), _sig("PS/2 DATA", 1), _sig("JTAG TCK", 4),
                 _sig("JTAG TMS", 5), _sig("JTAG TDI", 6), _sig("JTAG TDO", 7), _sig("SPI SCK", 0, "B"),
                 _sig("SPI MOSI", 1, "B"), _sig("SPI CS", 3, "B"), _sig("UART TX", 4, "B")],
     "params": [],
     "blurb": "PS/2 and JTAG on bank A, SPI and UART on bank B, one context each. Every "
              "context has its own fixed issue slot, so none can move another's edges."},
    {"id": "blank", "title": "Your own program", "firmware": None, "fn": sc_blank,
     "signals": [], "params": [{"key": "ticks", "label": "Run for (ticks)", "type": "int",
                                "default": "4000"}],
     "blurb": "No device models: every pin the program owns is plotted. Start from the "
              "square wave and see what the queue does when you break the rules."},
]
BY_ID = {s["id"]: s for s in SCENARIOS}

BLANK = """; A square wave on pin 0, one edge every DELAY ticks (40 ns each), from context 0.
; The loop is five instructions, and a context commits one every 4 ticks, so one
; period costs the program 20 ticks. Things to try:
;   DELAY 9:   the deadlines fall behind the program; the context faults ADMIT/PAST.
;   DELAY 400: the program runs ahead; EMIT waits (W) while the 4-entry queue is full.

.equ DELAY, 10
.entry 0, start
.own   0, 0

start:
    ADDT  20              ; first deadline: tick 20, after the first EMIT commits
    LDLC  20
loop:
    EMIT  1, 1, 1         ; pin 0 high at tmr
    ADDT  DELAY
    EMIT  1, 0, 1         ; pin 0 low at tmr
    ADDT  DELAY
    DJNZ  loop
    HALT
"""


def scenarios() -> str:
    return json.dumps([{k: v for k, v in s.items() if k != "fn"} for s in SCENARIOS])


def source_of(sid: str) -> str:
    s = BY_ID[sid]
    return BLANK if s["firmware"] is None else (FW / s["firmware"]).read_text()


# --------------------------------------------------------------------------- the run

class PlayMachine(Machine):
    """The ISS, with each commit's queued event and fault cause kept for the page."""

    def _record_commit(self, cm, t):
        super()._record_commit(cm, t)
        rec = self.commits[-1]
        if cm.outcome == "F":
            rec["cause"], rec["subcode"] = cm.cause, cm.subcode
        if cm.outcome == "C" and cm.append is not None:
            e = cm.append
            rec["ev"] = [e.kind, e.deadline, e.mask, e.value, e.oe, e.level, e.window, cm.lead,
                         e.pin]


def _assemble(source: str, name: str):
    """Assemble, and map each program word back to the line of `name` it came from."""
    seen = []
    real = _asm._expand

    def recording(*a, **k):
        for item in real(*a, **k):
            seen.append(item)
            yield item
    _asm._expand = recording
    try:
        image = assemble(source, base_dir=FW, name=name)
    finally:
        _asm._expand = real
    where = []
    for ns, w, raw, text in seen:
        while True:
            m = _asm._LABEL.match(text)
            if not m:
                break
            text = text[m.end():].strip()
        if text and not text.startswith("."):
            where.append(w)
    lines = {}
    for entry, w in zip(image.listing, where):
        pc = int(entry.split()[0])
        file, _, n = w.rpartition(":")
        lines[pc] = [file, int(n)]
    return image, lines


def _changes(hist, pin, t1):
    out, prev = [], None
    for t in range(min(t1, len(hist))):
        v = (hist[t] >> pin) & 1
        if v != prev:
            out.append([t, v])
            prev = v
    return out


def _cause_names():
    isa = load_isa()
    causes = {v: k for k, v in isa.fault_causes.items()}
    subs = {v: k for k, v in isa.admit_subcodes.items()}
    return causes, subs


LAST = {}


def run(source: str, sid: str, params_json: str = "{}") -> str:
    s = BY_ID[sid]
    params = json.loads(params_json or "{}")
    name = s["firmware"] or "main.asm"
    try:
        image, lines = _assemble(source, name)
    except AsmError as e:
        msg = str(e)
        line = None
        head = msg.split(":")
        if len(head) >= 3 and head[0] == name and head[1].strip().isdigit():
            line = int(head[1])
        return json.dumps({"ok": False, "error": msg, "line": line})

    certs = []
    try:
        for c in certify(image):
            certs.append({"ctx": c.ctx, "status": c.status, "events": c.events,
                          "min_lead": None if c.min_lead == float("inf") else int(c.min_lead),
                          "findings": [[f.pc, f.kind, f.note] for f in c.findings[:6]]})
    except Exception as e:                       # the run is still worth showing
        certs.append({"ctx": -1, "status": "ERROR", "note": str(e)})

    try:
        w = s["fn"](params)
    except ParamError as e:
        return json.dumps({"ok": False, "error": str(e), "line": None})
    m = PlayMachine(image, board=w.board, **({"board_b": w.board_b} if w.board_b else {}))
    if w.script is not None:
        w.script.machine = m
    try:
        if w.steps:
            w.steps(m)
        else:
            m.run(min(w.ticks, MAX_TICKS), stop_when_idle=False)
        result = w.result(m)
    except Exception as e:                       # a model or host step gave up
        return json.dumps({"ok": False, "line": None,
                           "error": f"The run stopped: {type(e).__name__}: {e}"})

    span = len(w.board.history)
    sigs = []
    for g in s["signals"]:
        hist = w.board.history if g["bank"] == "A" else (w.board_b.history if w.board_b else [])
        sigs.append({"name": g["name"], "pin": ("uio%d" if g["bank"] == "A" else "B%d") % g["pin"],
                     "w": _changes(hist, g["pin"], span)})
    named = {x["pin"] for x in sigs}
    for p in range(13):                          # every owned pin the scenario did not name
        if not image.pin_owned[p]:
            continue
        label = "uio%d" % p if p < 8 else "B%d" % (p - 8)
        if label in named:
            continue
        if p < 8:
            hist, bit = w.board.history, p
        elif w.board_b is not None:
            hist, bit = w.board_b.history, p - 8
        else:
            continue
        sigs.append({"name": "ctx%d" % image.pin_owner[p], "pin": label,
                     "w": _changes(hist, bit, span)})

    causes, subs = _cause_names()
    commits = []
    for c in m.commits:
        row = [c["t"], c["ctx"], c["pc"], c["outcome"]]
        if "ev" in c:
            row.append(c["ev"])
        elif "cause" in c:
            row.append([causes.get(c["cause"], c["cause"]), subs.get(c["subcode"], "")])
        commits.append(row)
    fin = m.final_state()
    ff = fin["first_fault"]
    fault = None
    if ff["valid"]:
        fault = {"ctx": ff["ctx"], "pc": ff["pc"], "tick": ff["tick"],
                 "cause": causes.get(ff["cause"], ff["cause"]),
                 "subcode": subs.get(ff["subcode"], "") if causes.get(ff["cause"]) == "ADMIT" else "",
                 "line": lines.get(ff["pc"])}
    ctxs = []
    for k, x in enumerate(fin["contexts"]):
        if not image.ctx_enable[k]:
            continue
        n = {o: 0 for o in "CWF"}
        for c in m.commits:
            if c["ctx"] == k:
                n[c["outcome"]] += 1
        ctxs.append({"ctx": k, "state": x["state"], "pc": x["pc"], "min_lead": x["min_lead"],
                     "max_occ": x["max_occupancy"], "wait_full": x["wait_full"],
                     "C": n["C"], "W": n["W"], "F": n["F"],
                     "bank": "B" if image.ctx_bank[k] else "A"})
    listing = []
    for entry in image.listing:
        pc, word, *_ = entry.split(None, 2)
        pc = int(pc)
        listing.append([pc, word, disassemble(int(word, 16)), lines.get(pc)])

    LAST.clear()
    LAST.update(m=m, lines=lines)
    out = {"ok": True, "scenario": sid, "name": name, "result": result, "span": span,
           "signals": sigs, "commits": commits, "certs": certs, "fault": fault,
           "contexts": ctxs, "digest": "%08X" % fin["digest"], "words": image.used_words,
           "listing": listing, "errors": w.board.errors()[:5]}
    if hasattr(w, "leds"):
        out["leds"] = w.leds()
    return json.dumps(out, separators=(",", ":"))


def regs_at(i: int) -> str:
    """Registers after commit number i of the last run."""
    m = LAST.get("m")
    if m is None or not 0 <= i < len(m.commits):
        return "null"
    c = m.commits[i]
    return json.dumps({"t": c["t"], "ctx": c["ctx"], "pc": c["pc"], "insn": "%08X" % c["insn"],
                       "text": disassemble(c["insn"]), "outcome": c["outcome"],
                       "post": c["post"]})


def encode(line: str) -> str:
    """One instruction, assembled on its own (for the instruction explorer)."""
    try:
        image = assemble(line.strip() + "\n", base_dir=FW, name="line")
    except AsmError as e:
        return json.dumps({"ok": False, "error": str(e).replace("line:1: ", "")})
    if not image.listing:
        return json.dumps({"ok": False, "error": "not an instruction"})
    return json.dumps({"ok": True, "word": image.listing[0].split()[1]})
