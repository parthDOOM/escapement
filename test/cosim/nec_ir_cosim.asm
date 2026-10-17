; nec_tx.asm (context 0, carrier on pin 0) and nec_rx.asm (context 1, receiver module
; output on pin 1) at a 2.5 MHz timebase, for the co-simulation: the same programs
; with their timing constants divided by ten, so a frame and a repeat code (one
; 108 ms slot plus 11.8 ms) take 300,000 ticks instead of 3 million. The full-length
; frame at 25 MHz is checked on the ISS (sw/tests/test_firmware.py) and by escert.

.equ nec_tx.CLK_KHZ, 2500
.equ nec_rx.CLK_KHZ, 2500
.include "../../firmware/nec_tx.asm"
.include "../../firmware/nec_rx.asm"
