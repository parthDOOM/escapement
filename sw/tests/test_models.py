"""Self-tests for the bus models (so a model bug cannot hide a firmware bug)."""

from escmodels import Board, I2cTarget, UartRx, UartTx


def test_uart_tx_model_into_rx_model():
    tx = UartTx(0, 217.0, b"\x00\xFF\x55\xA5", start=50)
    rx = UartRx(0, 217.0)
    # tx drives pin 0 from the device side; rx observes the bus
    board = Board(tx, rx)
    for t in range(tx.end + 500):
        board(t, 0, 0)
    assert rx.bytes == [0x00, 0xFF, 0x55, 0xA5] and not board.errors()


class BitBangI2C:
    """Ideal open-drain controller waveform generator for model testing."""

    def __init__(self, half_low=40, half_high=24):
        self.lo, self.hi = half_low, half_high
        self.events = []   # (t, scl, sda)
        self.t = 100
        self.scl = self.sda = 1

    def _set(self, scl=None, sda=None, dt=0):
        self.t += dt
        if scl is not None:
            self.scl = scl
        if sda is not None:
            self.sda = sda
        self.events.append((self.t, self.scl, self.sda))

    def start(self):
        self._set(sda=0, dt=0)
        self._set(scl=0, dt=20)

    def bit(self, b):
        self._set(sda=b, dt=10)
        self._set(scl=1, dt=self.lo - 10)
        self._set(scl=0, dt=self.hi)

    def byte(self, v):
        for i in range(7, -1, -1):
            self.bit((v >> i) & 1)
        self.bit(1)   # release for ACK

    def stop(self):
        self._set(sda=0, dt=10)
        self._set(scl=1, dt=self.lo - 10)
        self._set(sda=1, dt=20)


def drive(board, wave, until):
    i = 0
    scl = sda = 1
    for t in range(until):
        while i < len(wave.events) and wave.events[i][0] <= t:
            _, scl, sda = wave.events[i]
            i += 1
        oe = ((not scl) << 2) | ((not sda) << 3)
        board(t, 0, oe)


def test_i2c_target_register_write():
    target = I2cTarget(2, 3, 0x42, mode="fast")
    w = BitBangI2C()
    w.start(); w.byte(0x84); w.byte(0x07); w.byte(0x3C); w.stop()
    board = Board(target)
    drive(board, w, w.t + 200)
    assert target.regs[7] == 0x3C
    assert not board.errors(), board.errors()


def test_i2c_target_flags_fast_mode_violation():
    target = I2cTarget(2, 3, 0x42, mode="fast")
    w = BitBangI2C(half_low=20, half_high=10)    # 0.8 us low, 0.4 us high: too fast
    w.start(); w.byte(0x84); w.stop()
    board = Board(target)
    drive(board, w, w.t + 200)
    errs = board.errors()
    assert any("tLOW" in e for e in errs) and any("tHIGH" in e for e in errs)


def _nec_drive(*devices, ticks):
    board = Board(*devices)
    for t in range(ticks):
        board(t, 0, 0)
    return board


def test_nec_source_model_into_receiver_model():
    """Remote model (carrier) -> receiver model, and through the TSOP model; the
    receiver flags an inaccurate transmitter, rejects one outside the decoder windows,
    a missing command inverse and a slot violation (400 ns ticks)."""
    from escmodels import NecReceiver, NecSource, Tsop, nec_raw
    slot = 270_000
    items = [(100, nec_raw(0x00, 0xFF)), (100 + slot, None), (100 + 2 * slot, 0xA55A1234)]
    rx = NecReceiver(0, tick_ns=400.0)
    env = NecReceiver(1, tick_ns=400.0, modulated=False, active_low=True)
    board = _nec_drive(NecSource(0, items, tick_ns=400.0, modulated=True), Tsop(0, 1, 400.0),
                       rx, env, ticks=3 * slot)
    assert rx.frames() == [(0x00, 0xFF), "R", (0x1234, 0x5A)] and not rx.violations
    assert env.frames() == rx.frames() and not env.violations
    assert [e.get("ext") for e in rx.events] == [False, None, True]
    assert not board.errors()

    off = NecReceiver(0, tick_ns=400.0)                 # 3% slow: decodes, but flagged
    _nec_drive(NecSource(0, items[:1], tick_ns=400.0, modulated=True, scale=1.03), off,
               ticks=200_000)
    assert off.frames() == [(0x00, 0xFF)] and any("nominal" in v for v in off.violations)

    far = NecReceiver(0, tick_ns=400.0)                 # 20% slow: leader outside 9 +/- 1.125 ms
    _nec_drive(NecSource(0, items[:1], tick_ns=400.0, modulated=True, scale=1.2), far,
               ticks=230_000)
    assert far.frames() == [] and far.violations

    bad = NecReceiver(0, tick_ns=400.0)                 # no inverse; second frame too soon
    _nec_drive(NecSource(0, [(100, nec_raw(1, 2) ^ (1 << 24)), (200_000, nec_raw(1, 2))],
                         tick_ns=400.0, modulated=True), bad, ticks=380_000)
    assert [e["ok"] for e in bad.events] == [False, True]
    assert any("inverse" in v for v in bad.violations) and any("slot" in v for v in bad.violations)
def _run_board(board, dev, limit=200_000):
    t = 0
    while not dev.done and t < limit:
        board(t, 0, 0)
        t += 1
    for _ in range(50):
        board(t, 0, 0)
        t += 1


def test_spi_controller_model_against_spi_target_model():
    """The SPI controller model and the (separately written) SPI target model agree in
    all four modes: bytes both ways, and no timing complaint from either side."""
    from escmodels import SpiTarget
    from escmodels.spi import SpiController
    for mode in range(4):
        tgt = SpiTarget(4, 5, 6, 7, mode=mode, reply=b"\x3C\xC3\x81")
        ctl = SpiController(4, 5, 6, 7, [([0xA5, 0x5A], [0x3C, 0xC3]), ([0xF0], [0x81])],
                            mode=mode, t_high=20, t_low=20, t_ho=0)
        board = Board(tgt, ctl)
        _run_board(board, ctl)
        assert tgt.frames == [[0xA5, 0x5A], [0xF0]], (mode, tgt.frames)
        assert ctl.rx == [[0x3C, 0xC3], [0x81]], (mode, ctl.rx)
        assert not board.errors(), (mode, board.errors())
    ctl = SpiController(4, 5, 6, 7, [([0x00], [0x55])], t_ho=0)   # a wrong expectation
    board = Board(SpiTarget(4, 5, 6, 7, mode=0, reply=b"\x3C"), ctl)
    _run_board(board, ctl)
    assert any("MISO 0x3c (8 bits), expected 0x55" in e for e in board.errors())


def test_spi_controller_model_flags_miso_timing():
    """A target whose MISO moves around the sampling edges is flagged."""
    from escmodels import Device
    from escmodels.spi import SpiController

    class Jitter(Device):
        def step(self, t, bus):
            return (1 << 6, 0) if (t // 7) % 2 else (0, 1 << 6)

    ctl = SpiController(4, 5, 6, 7, [([0x00, 0x00], None)], t_high=32, t_low=32)
    board = Board(Jitter(), ctl)
    _run_board(board, ctl)
    assert any("MISO not held" in e for e in board.errors())
