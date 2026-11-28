# On the bench Pico: two known square waves into the chip's inputs from PIO, then the SUMP
# bridge. uio[2] (GP19) at 10 kHz, uio[3] (GP20) at 1 kHz; each PIO period is 64 cycles.
# The bridge sets every uio pin to an input with a pull-up before each capture (on the demo
# board the signals come from outside), so the waves are started again right after that.
import barepico
import rp2
import sump
from machine import Pin

io = barepico.open_io(sys_hz=125000000)


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def sq():
    set(pins, 1)[31]
    set(pins, 0)[31]


def waves():
    sm0 = rp2.StateMachine(0, sq, freq=640000, set_base=Pin(19))
    sm1 = rp2.StateMachine(1, sq, freq=64000, set_base=Pin(20))
    sm0.active(1)
    sm1.active(1)


_pullups = io.uio_pullups


def _pullups_then_waves():
    _pullups()
    waves()


io.uio_pullups = _pullups_then_waves
sump.serve(io=io)
