; dali.asm at a 250 kHz timebase for the co-simulation (half bit = 104 ticks): a query
; and its answer take about 6,000 ticks instead of 600,000. The bus model runs with
; 4 us ticks.

.equ dali.CLK_KHZ, 250
.include "../../firmware/dali.asm"
