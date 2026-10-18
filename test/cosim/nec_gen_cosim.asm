; nec_tx_gen (generated from protocols/nec_tx_gen.yaml) at a 2.5 MHz timebase for the
; co-simulation, as nec_ir_cosim.asm does for the hand-written transmitter: a frame and
; its 40 ms gap take about 280,000 ticks instead of 2.8 million.

.equ nec_tx_gen.CLK_KHZ, 2500
.include "../../firmware/nec_tx_gen.asm"
