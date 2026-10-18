; The protocols-as-data transmitters with long frames, at reduced clocks for the
; co-simulation: dmx512_tx at 5 MHz (a 512-slot packet takes 113,000 ticks instead of
; 565,000) and the seven-segment animation at 25 kHz (a frame is 4,000 ticks instead of
; 4 million). The full-speed programs are checked on the ISS and by escert.

.equ dmx512_tx.CLK_KHZ, 5000
.equ seven_seg.CLK_KHZ, 25
.equ seven_seg.CTX, 1
.include "../../firmware/dmx512_tx.asm"
.include "../../firmware/seven_seg.asm"
