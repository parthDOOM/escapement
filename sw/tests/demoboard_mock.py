"""A simulated Tiny Tapeout demo board with an Escapement chip on it, for running
sw/rp2350/bringup.py under CPython before silicon exists.

The board side imitates the parts of tt-micropython-firmware (v3.1.1) that bringup.py
uses: the DemoBoard object (`tt`), ttboard.util.platform's byte functions, MicroPython's
machine.time_pulse_us / machine.UART and the time module. Port bits are returned as a
Logic-like object that supports int() and bool() but not `& 1`, like microcotb's Logic.

Every board call costs RP time (`op_ns` for a byte function, `port_ns` for a port-bit
access); the chip advances by the matching number of core clock cycles. Two chip
backends implement the same small interface:

  SimChip      (here) the host interface of src/escapement_host.v modelled cycle by cycle
               (2-flop synchronisers, SCK edge detection, the command decoder), the ISS
               (sw/esciss) as the core, and the two SRAMs, with injectable faults.
  RtlChip      (test/test_bringup_rtl.py) the RTL of the whole chip under cocotb.

Backend interface: set_pwm(hz), set_reset(hold), set_ui(v), uo(), uio() (levels as the
RP sees them), advance(t_ns), wait_uio(pin, level, t_limit_ns) -> t_ns or None,
attach_uart(pin, baud) -> object with .bytes, detach_uart(obj). `pads` holds the RP side
of the uio pins (output enable, value, pull-ups).
"""

from __future__ import annotations

import random

from escasm import Image
from escmodels import UartRx
from esciss import Machine
from esciss.machine import RUNNING

M32 = 0xFFFFFFFF
C_STATUS, C_WI, C_RI, C_WC, C_WT, C_RT = 0x00, 0x10, 0x11, 0x12, 0x13, 0x14
C_START, C_STOP, C_DBG, C_PUSH, C_POP, C_IRQC, C_CLR, C_IGNORE = (
    0x20, 0x21, 0x30, 0x40, 0x41, 0x50, 0x51, 0xFF)
KNOWN = {C_STATUS, C_WI, C_RI, C_WC, C_DBG, C_PUSH, C_POP, C_IRQC, C_WT, C_RT}


class RpTime:
    def __init__(self):
        self.ns = 0


class Pads:
    """The RP2xxx side of uio[7:0]."""

    def __init__(self):
        self.oe = 0
        self.value = 0
        self.pullup = 0

    def level(self, out: int, oe: int, dead: int = 0, ext_low: int = 0) -> tuple[int, int]:
        """(level, contention mask) of the uio pins given the chip's out/oe."""
        chip = oe & ~dead & 0xFF
        lvl = (out & chip) | (self.value & self.oe & ~chip)
        rest = ~(chip | self.oe) & 0xFF
        lvl |= self.pullup & rest & ~ext_low
        return lvl & 0xFF, chip & self.oe


def blank_image() -> Image:
    return Image(words=[0] * 256, entry_pcs=[0] * 4, ctx_enable=[False] * 4,
                 pin_owned=[False] * 8, pin_owner=[0] * 8, pin_od=[False] * 8)


def cfg_image(imem, tape, cfg) -> Image:
    en, entry, owned, owner, od = cfg
    return Image(words=list(imem), entry_pcs=[(entry >> (8 * k)) & 0xFF for k in range(4)],
                 ctx_enable=[bool((en >> k) & 1) for k in range(4)],
                 pin_owned=[bool((owned >> i) & 1) for i in range(8)],
                 pin_owner=[(owner >> (2 * i)) & 3 for i in range(8)],
                 pin_od=[bool((od >> i) & 1) for i in range(8)], tape=list(tape))


class FaultyMachine(Machine):
    """The ISS with an execution fault: 'r0bit31' sets bit 31 of every value context 0
    writes to r0 (a stuck register-file bit); 'digest' flips digest bit 0 when context 0
    commits PC 3 (a fault in the digest path only)."""

    exec_fault = None

    def _apply_commit(self, x, cm):
        before = x.r[0]
        super()._apply_commit(x, cm)
        if self.exec_fault == "r0bit31" and cm.k == 0 and x.r[0] != before:
            x.r[0] |= 1 << 31

    def _digest_edge(self, out, oe, commit):
        super()._digest_edge(out, oe, commit)
        if self.exec_fault == "digest" and commit is not None and commit.k == 0 \
                and commit.pc == 3:
            self.dg ^= 1


class SimChip:
    def __init__(self, *, version=6, clock_scale=1.0, clock_dead=False, miso_stuck=None,
                 mosi_stuck=None, mem_faults=None, uio_out_dead=0, uio_in_stuck=None,
                 uio_ext_low=0, exec_fault=None, seed=1):
        self.version, self.clock_scale, self.clock_dead = version, clock_scale, clock_dead
        self.miso_stuck, self.mosi_stuck = miso_stuck, mosi_stuck
        self.mem_faults = mem_faults or {}
        self.uio_out_dead, self.uio_in_stuck, self.uio_ext_low = uio_out_dead, uio_in_stuck, uio_ext_low
        self.exec_fault = exec_fault
        rng = random.Random(seed)
        self.mem = [[rng.randrange(1 << 32) for _ in range(256)] for _ in range(2)]  # powers up undefined
        self.pads = Pads()
        self.contention = []
        self.timing_violations = 0
        self.uarts = []
        self.ui = 0
        self.pwm = 0
        self.t_base = self.cyc_base = self.cyc = 0
        self.t_now = 0
        self.settle = 0
        self.ui_changed_at = {}
        self.in_reset = False
        self._reset_regs()

    # ------------------------------------------------------------- clock, reset, pins
    @property
    def f_chip(self) -> float:
        return 0.0 if self.clock_dead else self.pwm * self.clock_scale

    def set_pwm(self, hz: float) -> None:
        self.t_base, self.cyc_base = self.t_now, self.cyc
        self.pwm = hz

    def _cycles_at(self, t_ns: int) -> int:
        return self.cyc_base + int((t_ns - self.t_base) * self.f_chip / 1e9)

    def _time_of(self, cyc: int) -> int:
        return self.t_base + int((cyc - self.cyc_base) * 1e9 / self.f_chip + 0.999)

    def set_reset(self, hold: bool) -> None:
        self.in_reset = hold
        if hold:
            self._reset_regs()

    def _reset_regs(self) -> None:
        self.cs_s, self.sck_s, self.mosi_s, self.sck_d = 3, 0, 0, 0
        self.bitcnt = self.rx_shift = self.tx = 0
        self.cmd, self.idx, self.wcnt, self.word = C_IGNORE, 0, 0, 0
        self.host_err = 0
        self.cfg = [0, 0, 0, 0, 0]
        self.mem_addr = self.mem_space = 0
        self.dbg_ctx = self.dbg_sel = 0
        self.running = False
        self.m = Machine(blank_image())
        self.m.run_flag = False

    def set_ui(self, v: int) -> None:
        last = self.ui
        self.ui = v & 0xFF
        if (last ^ self.ui) & 2:               # SCK phase shorter than 4 clocks?
            c0 = self.ui_changed_at.get("sck", -100)
            if self.cyc - c0 < 4:
                self.timing_violations += 1
            self.ui_changed_at["sck"] = self.cyc
        self.settle = 4

    def uo(self) -> int:
        miso = (self.tx >> 7) & 1 if self.miso_stuck is None else self.miso_stuck
        irq = int(bool(self.m.irq_pending) or self.m.fault_valid)
        return miso | (irq << 1) | (int(self.running) << 2)

    def uio(self) -> int:
        out, oe = self.m.outputs()
        return self.pads.level(out, oe, self.uio_out_dead, self.uio_ext_low)[0]

    # ------------------------------------------------------------- time
    def advance(self, t_ns: int) -> None:
        self.t_now = max(self.t_now, t_ns)
        if not self.f_chip:
            return
        target = self._cycles_at(self.t_now)
        while self.cyc < target:
            if self.in_reset:
                self.cyc = target
                break
            if self.settle:
                self._host_cycle()
                self._core(1)
                self.cyc += 1
                self.settle -= 1
            else:
                n = target - self.cyc
                self._core(n)
                self.cyc += n

    def wait_uio(self, pin: int, level: int, t_limit_ns: int):
        """Advance until uio[pin] == level (returns that time) or t_limit_ns (None)."""
        while True:
            if (self.uio() >> pin) & 1 == level:
                return self.t_now
            if not self.f_chip or self.t_now >= t_limit_ns:
                self.advance(t_limit_ns)
                return None
            self.advance(self._time_of(self.cyc + 1))

    def _quiescent(self) -> bool:
        m = self.m
        return (all(x.state != RUNNING and not x.fifo and not x.chk.active for x in m.ctx)
                and not m.cap.stage and m._ld_dg is None and not self.uarts)

    def _core(self, n: int) -> None:
        if not self.running:
            return
        if self._quiescent():
            self.m.now += n
            return
        self.m.run(n, stop_when_idle=False)

    def _board(self, t: int, out: int, oe: int) -> int:
        lvl, cont = self.pads.level(out, oe, self.uio_out_dead, self.uio_ext_low)
        if cont:
            self.contention.append((t, cont))
        for u in self.uarts:
            u.step(self.m.now, lvl)
        if self.uio_in_stuck is not None:
            b, v = self.uio_in_stuck
            lvl = (lvl & ~(1 << b)) | (v << b)
        return lvl

    def attach_uart(self, pin: int, baud: int):
        u = UartRx(pin, self.f_chip / baud if self.f_chip else 217)
        self.uarts.append(u)
        return u

    def detach_uart(self, u) -> None:
        self.uarts.remove(u)

    # ------------------------------------------------------------- memories
    def _phys(self, space: int, a: int) -> int:
        f = self.mem_faults.get(space, {})
        if "addr_stuck" in f:
            b, v = f["addr_stuck"]
            a = (a & ~(1 << b)) | (v << b)
        if "addr_short" in f:                  # wired-AND of two address lines
            i, j = f["addr_short"]
            both = (a >> i) & (a >> j) & 1
            a = (a & ~((1 << i) | (1 << j))) | (both << i) | (both << j)
        return a & 0xFF

    def mem_read(self, space: int, a: int) -> int:
        w = self.mem[space][self._phys(space, a)]
        f = self.mem_faults.get(space, {})
        if "data_stuck" in f:
            b, v = f["data_stuck"]
            w = (w & ~(1 << b)) | (v << b)
        if "data_short" in f:                  # wired-AND of two data lines
            i, j = f["data_short"]
            both = (w >> i) & (w >> j) & 1
            w = (w & ~((1 << i) | (1 << j))) | (both << i) | (both << j)
        return w & M32

    def mem_write(self, space: int, a: int, w: int) -> None:
        self.mem[space][self._phys(space, a)] = w & M32

    # ------------------------------------------------------------- host interface
    def status(self) -> int:
        return ((int(self.running) << 7) | (int(bool(self.m.irq_pending)) << 6)
                | (int(self.m.fault_valid) << 5) | (self.host_err << 4) | self.version)

    def _ctx_state(self) -> int:
        return sum((x.state & 3) << (2 * k) for k, x in enumerate(self.m.ctx))

    def _mbx(self) -> int:
        full = sum(int(len(x.rx) >= 2) << k for k, x in enumerate(self.m.ctx))
        empty = sum(int(not x.tx) << k for k, x in enumerate(self.m.ctx))
        return (full << 4) | empty

    def _ui_eff(self) -> int:
        ui = self.ui
        if self.mosi_stuck is not None:
            ui = (ui & ~4) | (self.mosi_stuck << 2)
        return ui

    def _host_cycle(self) -> None:
        cs1, sck1, mosi1 = (self.cs_s >> 1) & 1, (self.sck_s >> 1) & 1, (self.mosi_s >> 1) & 1
        selected = not cs1
        rise = selected and sck1 and not self.sck_d
        ui = self._ui_eff()
        self.cs_s = ((self.cs_s << 1) | (ui & 1)) & 3
        self.sck_s = ((self.sck_s << 1) | ((ui >> 1) & 1)) & 3
        self.mosi_s = ((self.mosi_s << 1) | ((ui >> 2) & 1)) & 3
        self.sck_d = sck1
        if not selected:
            self.bitcnt = self.idx = self.wcnt = 0
            self.cmd = C_IGNORE
            self.tx = self.status()
        elif rise:
            rx = ((self.rx_shift << 1) | mosi1) & 0xFF
            done = self.bitcnt == 7
            self.bitcnt = (self.bitcnt + 1) & 7
            self.rx_shift = rx & 0x7F
            if done:
                self.tx = self._next_tx(rx)
                self._byte(rx)
            else:
                self.tx = (self.tx << 1) & 0xFF

    def _dbg(self) -> int:
        return self.m.dbg_read(self.dbg_ctx, self.dbg_sel)

    def _next_tx(self, rx: int) -> int:
        idx, cmd, wcnt, word = self.idx, self.cmd, self.wcnt, self.word
        if idx == 0:
            return self._ctx_state() if rx == C_STATUS else 0
        if cmd == C_STATUS:
            return self._mbx() if idx == 1 else (self.m.irq_pending if idx == 2 else 0)
        if cmd in (C_RI, C_RT, C_DBG):
            src = self._dbg() if cmd == C_DBG else self.mem_read(self.mem_space, self.mem_addr)
            if idx == 2:
                return src >> 24
            if idx > 2:
                return ((word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF,
                        0 if cmd == C_DBG else src >> 24)[wcnt]
            return 0
        if cmd == C_POP:
            return 0
        return 0

    def _byte(self, rx: int) -> None:
        idx, cmd = self.idx, self.cmd
        if idx != 15:
            self.idx += 1
        if idx == 0:
            self.cmd, self.wcnt = rx, 0
            self.mem_space = int(rx in (C_WT, C_RT))
            if rx in KNOWN:
                pass
            elif rx == C_START:
                if self.running:
                    self.host_err = 1
                else:
                    self._start()
            elif rx == C_STOP:
                self._stop()
            elif rx == C_CLR:
                self.host_err = 0
            else:
                self.host_err, self.cmd = 1, C_IGNORE
            if rx in (C_WI, C_WC, C_WT) and self.running:
                self.host_err = 1
            return
        if cmd in (C_WI, C_WT):
            if idx == 1:
                self.mem_addr = rx
            else:
                self.word = ((self.word << 8) | rx) & M32
                w, self.wcnt = self.wcnt, (self.wcnt + 1) & 3
                if w == 3:
                    if self.running:
                        self.host_err = 1
                    else:
                        self.mem_write(self.mem_space, self.mem_addr, self.word)
                        self.mem_addr = (self.mem_addr + 1) & 0xFF
        elif cmd in (C_RI, C_RT):
            if idx == 1:
                self.mem_addr = rx
            elif idx == 2:
                self.word = self.mem_read(self.mem_space, self.mem_addr)
                self.mem_addr = (self.mem_addr + 1) & 0xFF
                self.wcnt = 0
            else:
                w, self.wcnt = self.wcnt, (self.wcnt + 1) & 3
                if w == 3:
                    self.word = self.mem_read(self.mem_space, self.mem_addr)
                    self.mem_addr = (self.mem_addr + 1) & 0xFF
        elif cmd == C_WC:
            if not self.running and 1 <= idx <= 9:
                c = self.cfg
                if idx == 1:
                    c[0] = rx & 0xF
                elif idx <= 5:
                    c[1] = (c[1] & ~(0xFF << (8 * (idx - 2)))) | (rx << (8 * (idx - 2)))
                elif idx == 6:
                    c[2] = rx
                elif idx == 7:
                    c[3] = (c[3] & 0xFF) | (rx << 8)
                elif idx == 8:
                    c[3] = (c[3] & 0xFF00) | rx
                else:
                    c[4] = rx
        elif cmd == C_DBG:
            if idx == 1:
                self.dbg_ctx = (rx >> 4) & 3
                self.dbg_sel = (((rx >> 6) & 1) << 4) | (rx & 0xF)
            elif idx == 2:
                self.word, self.wcnt = self._dbg(), 0
            elif self.wcnt != 3:
                self.wcnt += 1
        elif cmd == C_IRQC and idx == 1:
            self.m.host_irq_clear(rx & 0xF)

    def _start(self) -> None:
        image = cfg_image([self.mem_read(0, a) for a in range(256)],
                          [self.mem_read(1, a) for a in range(256)], self.cfg)
        FaultyMachine.exec_fault = self.exec_fault
        self.m = (FaultyMachine if self.exec_fault else Machine)(image, board=self._board)
        self.running = True

    def _stop(self) -> None:
        if not self.running:
            return
        self.m.stop()
        self.m.run(1, stop_when_idle=False)
        for a in range(256):
            self.mem_write(1, a, self.m.tape[a])
        self.running = False


# --------------------------------------------------------------------- board side

class Logic:
    """Like microcotb's Logic: int() and bool() work, `& 1` does not."""

    def __init__(self, v):
        self._v = v & 1

    def __int__(self):
        return self._v

    def __bool__(self):
        return bool(self._v)

    def __eq__(self, other):
        return int(other) == self._v

    def __and__(self, other):
        return NotImplemented


class Port:
    def __init__(self, board, read=None, write=None):
        self.board, self._read, self._write = board, read, write

    @property
    def value(self):
        self.board.cost(self.board.port_ns)
        return self._read()

    @value.setter
    def value(self, v):
        self.board.cost(self.board.port_ns)
        self._write(int(v) & 0xFF)

    def __getitem__(self, i):
        return Logic(self.value >> i)

    def __setitem__(self, i, v):
        cur = self.value
        self.value = (cur & ~(1 << i)) | ((int(v) & 1) << i)


class PinObj:
    """tt.pins.<name>: a StandardPin with .pull, .mode, .gpio_num and .raw_pin."""

    def __init__(self, board, name, gpio, uio=None):
        self.board, self.name, self.gpio_num, self.uio = board, name, gpio, uio
        self.raw_pin = self
        self._pull = -1
        self.mode = 0

    @property
    def pull(self):
        return self._pull

    @pull.setter
    def pull(self, p):
        self._pull = p
        if self.uio is not None:
            pads = self.board.chip.pads
            bit = 1 << self.uio
            pads.pullup = (pads.pullup | bit) if p == MockMachine.Pin.PULL_UP else (pads.pullup & ~bit)


class Pins:
    def __init__(self, board):
        for i in range(8):
            setattr(self, "uio%d" % i, PinObj(board, "uio%d" % i, 25 + i, uio=i))
            setattr(self, "ui_in%d" % i, PinObj(board, "ui_in%d" % i, 17 + i))


class Design:
    def __init__(self, tt, name, clock_hz):
        self.tt, self.name, self.clock_hz = tt, name, clock_hz

    def enable(self):
        # as DemoBoard.apply_user_config without a config.ini section: stop the clock,
        # toggle reset the first time, start the clock at the design's clock_hz
        tt = self.tt
        tt.shuttle.enabled = self
        tt.clock_project_PWM(0)
        tt.reset_project(True)
        tt.board.cost(2_000_000)
        tt.reset_project(False)
        tt.clock_project_PWM(self.clock_hz)

    def __repr__(self):
        return "<Design %s>" % self.name


class Shuttle:
    def __init__(self, tt, projects):
        self.tt, self.enabled, self.run = tt, None, "ttihp-sim"
        self._designs = {n: Design(tt, n, hz) for n, hz in projects.items()}

    def has(self, name):
        return name in self._designs

    def __getattr__(self, name):
        try:
            return self.__dict__["_designs"][name]
        except KeyError:
            raise AttributeError(name)


class MockTT:
    def __init__(self, board, projects, pwm_report=None):
        self.board, self.version = board, "3.1.1-sim"
        self.mode = 0
        self.shuttle = Shuttle(self, projects)
        self.pins = Pins(board)
        self._pwm = 0
        self._pwm_report = pwm_report
        chip = board.chip
        self.ui_in = Port(board, lambda: board.platform.read_ui_in_byte(),
                          lambda v: board.platform.write_ui_in_byte(v))
        self.uo_out = Port(board, lambda: board.platform.read_uo_out_byte())
        self.uio_in = Port(board, lambda: chip.pads.value, lambda v: setattr(chip.pads, "value", v))
        self.uio_oe_pico = Port(board, lambda: chip.pads.oe, lambda v: setattr(chip.pads, "oe", v))

    def clock_project_PWM(self, hz):
        self.board.sync()
        self._pwm = hz
        self.board.chip.set_pwm(hz)

    @property
    def auto_clocking_freq(self):
        if self._pwm_report is not None:
            return self._pwm_report
        return self._pwm

    def reset_project(self, hold):
        self.board.sync()
        self.board.chip.set_reset(bool(hold))


class MockPlatform:
    IsRP2350 = True

    def __init__(self, board, dip_high=0):
        self.board, self.dip_high = board, dip_high

    def get_RP_system_clock(self):
        return 100_000_000

    def write_ui_in_byte(self, v):
        b = self.board
        b.cost(b.op_ns)
        b.chip.set_ui((v | self.dip_high) & 0xFF)
        b.ui_driven = v & 0xFF

    def read_ui_in_byte(self):
        self.board.cost(self.board.op_ns)
        return (self.board.ui_driven | self.dip_high) & 0xFF

    def read_uo_out_byte(self):
        self.board.cost(self.board.op_ns)
        return self.board.chip.uo()

    def read_uio_byte(self):
        self.board.cost(self.board.op_ns)
        return self.board.chip.uio()


class MockUART:
    def __init__(self, board, uart_id, baudrate, rx, tx, timeout=0):
        # rp2 machine_uart.c (RP2350): RX odd, TX even, both on ((pin + 4) & 8) >> 3
        periph = lambda p: (((p + 4) & 8) >> 3) == uart_id  # noqa: E731
        if not (rx.gpio & 1 and periph(rx.gpio)):
            raise ValueError("bad RX pin")
        if not (tx.gpio & 1 == 0 and periph(tx.gpio)):
            raise ValueError("bad TX pin")
        self.board = board
        self.rx_dev = board.chip.attach_uart(rx.gpio - 25, baudrate)
        self.taken = 0

    def any(self):
        self.board.sync()
        return len(self.rx_dev.bytes) - self.taken

    def read(self):
        self.board.sync()
        data = bytes(self.rx_dev.bytes[self.taken:])
        self.taken = len(self.rx_dev.bytes)
        return data or None

    def deinit(self):
        self.board.chip.detach_uart(self.rx_dev)


class MockMachine:
    class Pin:
        IN, OUT, PULL_UP = 0, 1, 1

        def __init__(self, gpio, *a, **k):
            self.gpio = gpio

    def __init__(self, board):
        self.board = board

    def time_pulse_us(self, pin, level, timeout_us):
        """machine_pulse.c: wait (timeout) for the pin to reach level, then time it."""
        b, chip = self.board, self.board.chip
        b.cost(b.op_ns)
        t = chip.wait_uio(pin.uio, level, b.time.ns + timeout_us * 1000)
        if t is None:
            b.time.ns = chip.t_now
            return -2
        t2 = chip.wait_uio(pin.uio, 1 - level, t + timeout_us * 1000)
        b.time.ns = chip.t_now
        if t2 is None:
            return -1
        return (t2 - t) // 1000

    def UART(self, uart_id, baudrate, rx, tx, timeout=0):
        return MockUART(self.board, uart_id, baudrate, rx, tx, timeout)


class MockTime:
    def __init__(self, board):
        self.board = board

    def ticks_us(self):
        self.board.cost(200)
        return self.board.time.ns // 1000

    def ticks_diff(self, a, b):
        return a - b

    def sleep_us(self, n):
        self.board.cost(int(n) * 1000)

    def sleep_ms(self, n):
        self.board.cost(int(n) * 1_000_000)


class RPMode:
    SAFE, ASIC_RP_CONTROL, ASIC_MANUAL_INPUTS = 0, 1, 2


class MockBoard:
    """Holds RP time and the chip; cost() advances both."""

    def __init__(self, chip, op_ns=1000, port_ns=40_000, dip_high=0, in_shuttle=True,
                 pwm_report=None):
        self.chip, self.op_ns, self.port_ns = chip, op_ns, port_ns
        self.time = RpTime()
        self.ui_driven = 0
        self.platform = MockPlatform(self, dip_high)
        self.machine = MockMachine(self)
        self.time_mod = MockTime(self)
        projects = {"tt_um_escapement": 25_000_000} if in_shuttle else {"tt_um_other": 0}
        self.tt = MockTT(self, projects, pwm_report)

    def cost(self, ns):
        self.time.ns += ns
        self.chip.advance(self.time.ns)

    def sync(self):
        self.chip.advance(self.time.ns)
        flush = getattr(self.chip, "flush", None)
        if flush:
            flush()

    def io(self, bringup):
        return bringup.DemoBoardIO(self.tt, self.platform, self.machine, self.time_mod, RPMode)
