; cec.asm at a 250 kHz timebase for the co-simulation (1 ms = 250 ticks): a CEC frame
; of three bytes takes about 19,000 ticks instead of 1.9 million. The bus models run
; with 4 us ticks.

.equ cec.CLK_KHZ, 250
.include "../../firmware/cec.asm"
