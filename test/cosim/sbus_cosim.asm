; sbus_rx.asm at a 5 MHz timebase for the co-simulation (50 ticks a bit at 100000 baud):
; a frame takes 15,000 ticks instead of 75,000. The source model runs with the same bit.

.equ sbus_rx.CLK_KHZ, 5000
.include "../../firmware/sbus_rx.asm"
