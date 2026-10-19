"""Waveforms for the site, from the instruction-set simulator and the bus models.

    python site/tools/waves.py site/assets/waves.json   (also writes waves.js)

Each protocol runs the real firmware on the ISS against the same independent bus model
the tests use. The pin levels are recorded every tick and stored as change lists. The
result line under each waveform is what the model decoded, not what the firmware meant.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sw"))

from escasm import assemble_file  # noqa: E402
from escasm.disasm import disassemble  # noqa: E402
from escert import certify  # noqa: E402
from esciss import Machine  # noqa: E402
from escmodels import (Board, HostScript, I2cTarget, OneWireDevice, SpiTarget,  # noqa: E402
                       UartRx, Ws2812Decoder, crc8_maxim)

FW = ROOT / "firmware"
TICK_NS = 40.0


def changes(history, pin, t0, t1):
    out, prev = [], None
    for t in range(t0, min(t1, len(history))):
        v = (history[t] >> pin) & 1
        if v != prev:
            out.append([t - t0, v])
            prev = v
    return out


def cert_of(path):
    return [{"ctx": c.ctx, "status": c.status,
             "min_lead": None if c.min_lead == float("inf") else int(c.min_lead)}
            for c in certify(assemble_file(path))]


def entry(pid, title, fw, caption, result, signals, t0, t1, ctxs=None):
    img = assemble_file(FW / fw)
    certs = cert_of(FW / fw)
    if ctxs is not None:
        certs = [c for c in certs if c["ctx"] in ctxs]
    return {"id": pid, "title": title, "firmware": fw, "words": img.used_words,
            "cert": certs, "caption": caption, "result": result, "span": t1 - t0,
            "signals": signals}


def uart():
    rx = UartRx(0, 25e6 / 115200)
    board = Board(rx)
    m = Machine(assemble_file(FW / "uart_tx.asm"), board=board)
    m.run(9200, stop_when_idle=False)
    h = board.history
    t0 = next(t for t in range(len(h)) if not h[t] & 1) - 120
    return entry("uart", "UART transmit", "uart_tx.asm",
                 "115,200 baud, 8N1. One bit is 217 ticks: 8,680 ns, the closest whole-tick "
                 "value to 8,681 ns.",
                 "Receiver model decoded " + " ".join("%02X" % b for b in rx.bytes),
                 [{"name": "TX", "pin": "uio0", "w": changes(h, 0, t0, t0 + 8900)}], t0, t0 + 8900)


def i2c():
    target = I2cTarget(scl=2, sda=3, addr7=0x42, regs={5: 0x00, 6: 0x5C}, mode="fast",
                       tick_ns=TICK_NS, stretch_ticks=37)
    cmd = (0x42 << 24) | (5 << 16) | (0xA5 << 8) | 1
    script = HostScript([(50, 0, cmd)], drain=[0], poll=500)
    board = Board(target, script)
    m = Machine(assemble_file(FW / "i2c_ctrl.asm"), board=board)
    script.machine = m
    m.run(3600, stop_when_idle=False)
    h = board.history
    t0, t1 = 60, 3500
    return entry("i2c", "I2C controller", "i2c_ctrl.asm",
                 "Fast mode (400 kHz), open-drain: the chip only ever pulls low. The target "
                 "stretches SCL for 37 ticks after each byte and the controller waits for it.",
                 "Target model: register 0x05 now holds 0x%02X%s" % (target.regs[5], "" if not board.errors() else ", with errors"),
                 [{"name": "SCL", "pin": "uio2", "w": changes(h, 2, t0, t1)},
                  {"name": "SDA", "pin": "uio3", "w": changes(h, 3, t0, t1)}], t0, t1)


def spi():
    target = SpiTarget(sck=4, mosi=5, miso=6, cs=7, mode=0, reply=b"\x3C\xC3\x81")
    script = HostScript([(20, 2, 0), (100, 2, 0x1A5), (110, 2, 0x05A)], drain=[2], poll=300)
    board = Board(target, script)
    m = Machine(assemble_file(FW / "spi_ctrl.asm"), board=board)
    script.machine = m
    m.run(1500, stop_when_idle=False)
    h = board.history
    t0 = next(t for t in range(len(h)) if not (h[t] >> 7) & 1) - 30
    t1 = t0 + 900
    return entry("spi", "SPI controller", "spi_ctrl.asm",
                 "Mode 0, two bytes in one frame. MISO comes from the target model.",
                 "Target model received %s and answered %s" %
                 (" ".join("%02X" % b for b in target.frames[0]),
                  " ".join("%02X" % b for b in script.received[2])),
                 [{"name": "CS", "pin": "uio7", "w": changes(h, 7, t0, t1)},
                  {"name": "SCK", "pin": "uio4", "w": changes(h, 4, t0, t1)},
                  {"name": "MOSI", "pin": "uio5", "w": changes(h, 5, t0, t1)},
                  {"name": "MISO", "pin": "uio6", "w": changes(h, 6, t0, t1)}], t0, t1)


def ws2812():
    dec = Ws2812Decoder(0, TICK_NS)
    script = HostScript([(30, 3, 0x102030), (40, 3, 0x010203), (2000, 3, 6)], drain=[3], poll=97)
    board = Board(dec, script)
    m = Machine(assemble_file(FW / "ws2812.asm"), board=board)
    script.machine = m
    m.run(12000, stop_when_idle=False)
    dec.finish()
    h = board.history
    drive = next(p["t"] for p in m.pins if p["uio_oe"] & 1)       # the chip takes the pin
    t0 = next(t for t in range(drive + 1, len(h)) if h[t] & 1 and not h[t - 1] & 1) - 40
    t1 = t0 + 1600
    leds = dec.frames[0]
    return entry("ws2812", "WS2812 LED chain", "ws2812.asm",
                 "Each bit is 1.25 us: a 0 is a short high pulse, a 1 a long one. The firmware "
                 "computes six colours and streams them without a gap.",
                 "Decoder model: %d LEDs, %s" % (len(leds), " ".join("%06X" % c for c in leds)),
                 [{"name": "DIN", "pin": "uio0", "w": changes(h, 0, t0, t1)}], t0, t1)


def onewire():
    rom = bytes([0x28, 0xAA, 0x01, 0x02, 0x03, 0x04, 0x05])
    rom += bytes([crc8_maxim(rom)])
    dev = OneWireDevice(1, rom, TICK_NS)
    script = HostScript([(30, 3, 0x80000033)], drain=[3], poll=500)
    board = Board(dev, script)
    m = Machine(assemble_file(FW / "onewire.asm"), board=board)
    script.machine = m
    m.run(60000, stop_when_idle=False)
    h = board.history
    t0, t1 = 0, 46000
    return entry("onewire", "1-Wire", "onewire.asm",
                 "Reset pulse, the device's presence pulse, then the Read ROM command, all on "
                 "one open-drain wire.",
                 "Device model answered the reset with a presence pulse and received command 0x%02X" % dev.written[0],
                 [{"name": "DQ", "pin": "uio1", "w": changes(h, 1, t0, t1)}], t0, t1)


def can():
    from escmodels import CanBus
    bus = CanBus(0, 1, 100)
    board = Board(bus)
    m = Machine(assemble_file(FW / "can_tx.asm"), board=board)
    m.run(200, stop_when_idle=False)
    for w in ((0x123 << 16) | 2, 0xDEAD0000, 0):
        m.host_rx_push(0, w)
        m.run(50, stop_when_idle=False)
    m.run(30000, stop_when_idle=False)
    h = board.history
    t0 = next(t for t in range(len(h)) if not h[t] & 1) - 200
    t1 = t0 + 7600
    f = bus.frames[0]
    return entry("can", "CAN 2.0A", "can_tx.asm",
                 "250 kbit/s. The firmware computes CRC-15 and inserts stuff bits; the receiver "
                 "model pulls the ACK slot low and the chip samples it.",
                 "Receiver model: ID 0x%03X, %d data bytes %s, CRC %s" %
                 (f["id"], f["dlc"], " ".join("%02X" % b for b in f["data"]),
                  "good" if f["crc_ok"] else "bad"),
                 [{"name": "TX", "pin": "uio0", "w": changes(h, 0, t0, t1)},
                  {"name": "RX", "pin": "uio1", "w": changes(h, 1, t0, t1)}], t0, t1)


def jtag():
    from escmodels import JtagTap
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    board = Board(tap)
    m = Machine(assemble_file(FW / "jtag_idcode.asm"), board=board)
    m.host_rx_push(2, 0)
    m.run(6000, stop_when_idle=False)
    got = m.host_tx_pop(2)
    h = board.history
    t0 = next(t for t in range(len(h)) if (h[t] >> 4) & 1 == 0) - 20
    t1 = min(len(h), t0 + 4400)
    return entry("jtag", "JTAG IDCODE", "jtag_idcode.asm",
                 "TAP reset, walk to Shift-DR, shift out the 32-bit IDCODE, return to "
                 "Run-Test/Idle.",
                 "TAP model returned IDCODE 0x%08X" % got,
                 [{"name": "TCK", "pin": "uio4", "w": changes(h, 4, t0, t1)},
                  {"name": "TMS", "pin": "uio5", "w": changes(h, 5, t0, t1)},
                  {"name": "TDI", "pin": "uio6", "w": changes(h, 6, t0, t1)},
                  {"name": "TDO", "pin": "uio7", "w": changes(h, 7, t0, t1)}], t0, t1)


def two_banks():
    from escmodels import JtagTap, Ps2Host, SplitBoard
    ps2 = Ps2Host(0, 1)
    tap = JtagTap(4, 5, 6, 7, idcode=0x4BA00477)
    script = HostScript([(20, 2, 3), (100, 2, 0x1A5), (110, 2, 0x05A), (30, 0, 0x1C),
                         (40, 1, 0)], drain=[0, 1, 2], poll=300)
    spi_t = SpiTarget(sck=0, mosi=1, miso=2, cs=3, mode=3, reply=b"\x3C\xC3\x81")
    uart = UartRx(4, 25e6 / 115200)
    ba = Board(ps2, tap, script)
    bb = SplitBoard(spi_t, uart, outputs=0b11011)
    m = Machine(assemble_file(FW / "combo_two_banks.asm"), board=ba, board_b=bb)
    script.machine = m
    m.run(27000, stop_when_idle=False)
    ha, hb = ba.history, bb.history
    t0, t1 = 0, 26000
    sig = [{"name": "PS/2 CLK", "pin": "uio0", "w": changes(ha, 0, t0, t1)},
           {"name": "PS/2 DATA", "pin": "uio1", "w": changes(ha, 1, t0, t1)},
           {"name": "JTAG TCK", "pin": "uio4", "w": changes(ha, 4, t0, t1)},
           {"name": "JTAG TMS", "pin": "uio5", "w": changes(ha, 5, t0, t1)},
           {"name": "JTAG TDO", "pin": "uio7", "w": changes(ha, 7, t0, t1)},
           {"name": "SPI SCK", "pin": "B0", "w": changes(hb, 0, t0, t1)},
           {"name": "SPI MOSI", "pin": "B1", "w": changes(hb, 1, t0, t1)},
           {"name": "SPI CS", "pin": "B3", "w": changes(hb, 3, t0, t1)},
           {"name": "UART TX", "pin": "B4", "w": changes(hb, 4, t0, t1)}]
    e = entry("combo", "Four protocols at once", "combo_two_banks.asm",
              "PS/2 and JTAG on bank A, SPI and UART on bank B, one context each. Every "
              "context has its own fixed issue slot, so none of them can move another's "
              "edges.",
              "PS/2 host model got %s, SPI target got %s, UART receiver got %s, in this window" % (" ".join("%02X" % b for b in ps2.bytes) or "nothing yet", " ".join("%02X" % b for f in spi_t.frames for b in f) or "nothing yet", " ".join("%02X" % b for b in uart.bytes) or "nothing yet"), sig, t0, t1)
    # the barrel: which context committed what, for the first 160 ticks of the run
    barrel = []
    for c in m.commits:
        if c["t"] >= 160:
            break
        barrel.append([c["t"], c["ctx"], disassemble(c["insn"]).split(";")[0].strip(),
                       c["outcome"]])
    return e, barrel


def main(out):
    protos = [uart(), i2c(), spi(), ws2812(), onewire(), can(), jtag()]
    combo, barrel = two_banks()
    protos.append(combo)
    data = {"tick_ns": TICK_NS, "protocols": protos, "barrel": barrel}
    text = json.dumps(data, separators=(",", ":"))
    Path(out).write_text(text)
    Path(out[:-5] + ".js").write_text("window.ESC = window.ESC || {};\nESC.waves = " + text + ";\n")
    for p in protos:
        print(p["id"], p["result"], sum(len(s["w"]) for s in p["signals"]), "changes",
              [c["status"] for c in p["cert"]])
    print("barrel", len(barrel), "commits")


if __name__ == "__main__":
    main(sys.argv[1])
