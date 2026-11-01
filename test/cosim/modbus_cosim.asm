; modbus_rtu.asm at a 2.5 MHz timebase for the co-simulation (130 ticks a bit at 19200
; baud, 3.5 characters 5,005 ticks): a transaction takes about 30,000 ticks instead of
; 300,000. The bus model runs with the same 130.2-tick bit.

.equ modbus_rtu.CLK_KHZ, 2500
.include "../../firmware/modbus_rtu.asm"
