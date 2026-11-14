# Bring-up host on a bare Raspberry Pi Pico (RP2040) or Pico 2 (RP2350) wired to an FPGA
# build of the chip (fpga/escapement_board.v), instead of the Tiny Tapeout demo board.
#
# bringup.py talks to the board only through DemoBoardIO, which uses a small part of
# tt-micropython-firmware: the DemoBoard object (`tt`) and ttboard.util.platform's byte
# functions. This file provides those on plain MicroPython GPIO, so bringup.py and the
# host library run unchanged:
#
#     import barepico, bringup
#     bringup.run(io=barepico.open_io())
#
# Wiring (Pico GPIO -> FPGA board signal; every pin 3.3 V, grounds connected):
#
#     GP0          clk            (PWM)
#     GP1..GP8     ui_in[0..7]    (CS_n, SCK, MOSI, then bank B inputs; GP8 is also the
#                                  UART TX that the stage 6 smoke test parks on ui_in[7])
#     GP9          uio[0]         (UART1 RX for the stage 6 smoke test)
#     GP10..GP17   uo_out[0..7]   (MISO, IRQ, RUN, then bank B outputs)
#     GP18..GP22   uio[1..5]
#     GP26, GP27   uio[6], uio[7]
#     GP28         rst_n
#
# ui_in and uo_out are each on consecutive GPIOs, so a byte is one register access (the
# SPI link moves a byte per SCK edge, as on the demo board). GPIO_IN (0xd0000004) and
# GPIO_OUT (0xd0000010) of the SIO block are at the same addresses on both chips.
#
# Plain MicroPython (no typing, dataclasses or f-strings), so the same file runs on the
# board and under CPython with a simulated `machine` (sw tests).

CLK_GPIO = 0
UI_SHIFT = 1                 # ui_in[k] on GPIO 1 + k
UO_SHIFT = 10                # uo_out[k] on GPIO 10 + k
UIO_GPIO = (9, 18, 19, 20, 21, 22, 26, 27)
RST_GPIO = 28
PROJECT = "tt_um_escapement"
F_CLK = 25000000
SIO_GPIO_IN = 0xD0000004
SIO_GPIO_OUT = 0xD0000010
UI_MASK = 0xFF << UI_SHIFT


def _uio_byte(g):
    """uio[7:0] from a GPIO_IN word (uio[0] on GP9, uio[1..5] on GP18..22, uio[6..7] on
    GP26..27)."""
    return ((g >> 9) & 0x01) | ((g >> 17) & 0x3E) | ((g >> 20) & 0xC0)


class RPMode:
    SAFE, ASIC_RP_CONTROL, ASIC_MANUAL_INPUTS = 0, 1, 2


class _Platform:
    """ttboard.util.platform's byte functions and clock query."""

    def __init__(self, machine, is_rp2350):
        self.machine, self.mem32 = machine, machine.mem32
        self.IsRP2350 = is_rp2350

    def get_RP_system_clock(self):
        return self.machine.freq()

    def write_ui_in_byte(self, v):
        m = self.mem32
        m[SIO_GPIO_OUT] = (m[SIO_GPIO_OUT] & ~UI_MASK & 0x3FFFFFFF) | ((v & 0xFF) << UI_SHIFT)

    def read_ui_in_byte(self):
        return (self.mem32[SIO_GPIO_IN] >> UI_SHIFT) & 0xFF

    def read_uo_out_byte(self):
        return (self.mem32[SIO_GPIO_IN] >> UO_SHIFT) & 0xFF

    def read_uio_byte(self):
        return _uio_byte(self.mem32[SIO_GPIO_IN])


class _Pin:
    """tt.pins.<name>: .pull, .mode, .gpio_num and .raw_pin, as StandardPin."""

    def __init__(self, machine, gpio, mode, pull=None):
        self.machine, self.gpio_num = machine, gpio
        self._mode, self._pull = mode, pull
        self.raw_pin = machine.Pin(gpio, mode, pull)

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, m):
        self._mode = m
        self.raw_pin.init(m, self._pull)

    @property
    def pull(self):
        return self._pull

    @pull.setter
    def pull(self, p):
        self._pull = p
        self.raw_pin.init(self._mode, p)


class _Pins:
    def __init__(self, machine):
        P = machine.Pin
        for k in range(8):
            setattr(self, "ui_in%d" % k, _Pin(machine, UI_SHIFT + k, P.OUT))
            setattr(self, "uo_out%d" % k, _Pin(machine, UO_SHIFT + k, P.IN))
            setattr(self, "uio%d" % k, _Pin(machine, UIO_GPIO[k], P.IN))


class _Bits:
    """tt.ui_in / tt.uo_out: bit access over a byte read and write."""

    def __init__(self, read, write=None):
        self._read, self._write = read, write

    def __getitem__(self, i):
        return (self._read() >> i) & 1

    def __setitem__(self, i, v):
        b = self._read()
        self._write((b | (1 << i)) if v else (b & ~(1 << i)))


class _Reg:
    def __init__(self, on_set=None):
        self._v, self._on_set = 0, on_set

    @property
    def value(self):
        return self._v

    @value.setter
    def value(self, v):
        self._v = v & 0xFF
        if self._on_set:
            self._on_set()


class _Design:
    """tt.shuttle.tt_um_escapement: enable() as DemoBoard does without a config.ini
    section: clock off, a reset pulse, clock on at F_CLK."""

    name = PROJECT

    def __init__(self, tt):
        self.tt = tt

    def enable(self):
        tt = self.tt
        tt.shuttle.enabled = self
        tt.clock_project_PWM(0)
        tt.reset_project(True)
        tt.time.sleep_ms(2)
        tt.reset_project(False)
        tt.clock_project_PWM(F_CLK)


class _Shuttle:
    run = "fpga"

    def __init__(self, tt):
        self.enabled = None
        self.tt_um_escapement = _Design(tt)

    def has(self, name):
        return name == PROJECT


class _TT:
    """The parts of tt-micropython-firmware's DemoBoard that DemoBoardIO uses."""

    version = "bare-pico"

    def __init__(self, machine, time_mod, platform):
        self.machine, self.time, self.platform = machine, time_mod, platform
        self.mode = RPMode.SAFE
        P = machine.Pin
        self.pins = _Pins(machine)
        self._rst = P(RST_GPIO, P.OUT, value=0)          # in reset until enable()
        self._clk_pin = P(CLK_GPIO, P.OUT, value=0)
        self._pwm, self._hz = None, 0
        platform.write_ui_in_byte(0x01)                   # CS_n high, SCK and MOSI low
        self.shuttle = _Shuttle(self)
        self.ui_in = _Bits(platform.read_ui_in_byte, platform.write_ui_in_byte)
        self.uo_out = _Bits(platform.read_uo_out_byte)
        self.uio_oe_pico = _Reg(self._apply_uio)          # uio pins the RP drives
        self.uio_in = _Reg(self._apply_uio)               # ... and their values

    def _apply_uio(self):
        oe, val = self.uio_oe_pico.value, self.uio_in.value
        for k in range(8):
            p = getattr(self.pins, "uio%d" % k)
            if (oe >> k) & 1:
                p.raw_pin.init(self.machine.Pin.OUT, None, value=(val >> k) & 1)
                p._mode = self.machine.Pin.OUT
            elif p.mode != self.machine.Pin.IN:
                p.mode = self.machine.Pin.IN

    def clock_project_PWM(self, hz):
        m = self.machine
        if self._pwm is not None:
            self._pwm.deinit()
            self._pwm = None
        if not hz:
            self._clk_pin = m.Pin(CLK_GPIO, m.Pin.OUT, value=0)
            self._hz = 0
            return
        self._pwm = m.PWM(m.Pin(CLK_GPIO))
        self._pwm.freq(int(hz))
        self._pwm.duty_u16(32768)
        self._hz = self._pwm.freq()                       # what the divider could make

    @property
    def auto_clocking_freq(self):
        return self._hz

    def reset_project(self, hold):
        self._rst.value(0 if hold else 1)


def open_io(machine=None, time_mod=None, sys_hz=None):
    """A bringup.DemoBoardIO on a bare Pico wired as above. sys_hz sets the RP's system
    clock first: 100 MHz makes 25 and 50 MHz with an exact 50% duty (PWM divides the
    system clock by a whole number; at the default 125 MHz, 25 MHz is high 2 of 5)."""
    if machine is None:
        import machine
    if time_mod is None:
        import time as time_mod
    import bringup
    if sys_hz:
        machine.freq(sys_hz)
    try:
        import os
        is_rp2350 = "RP2350" in os.uname().machine
    except (ImportError, AttributeError):
        is_rp2350 = False
    platform = _Platform(machine, is_rp2350)
    tt = _TT(machine, time_mod, platform)
    return bringup.DemoBoardIO(tt, platform, machine, time_mod, RPMode)
