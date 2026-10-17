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
