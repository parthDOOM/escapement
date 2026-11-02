; modbus_rtu.asm at a 3 MHz timebase for the co-simulation (156 ticks a bit at 19200
; baud, 3.5 characters 6,006 ticks): a transaction takes about 36,000 ticks instead of
; 300,000. The bus model runs with the same 156.25-tick bit. (At 2.5 MHz a bit is too
; short for the per-byte work, and escert does not certify it; from 3 MHz it does.)

.equ modbus_rtu.CLK_KHZ, 3000
.include "../../firmware/modbus_rtu.asm"
