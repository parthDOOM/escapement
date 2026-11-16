; The receive line check (README.md, Ethernet): the capture unit records every change of RXP
; (uio[2]) into the whole tape, {levels, tick} each, while eth_link.asm keeps the link
; partner's link up, so the partner's frames arrive. The capture starts at the first change,
; which is a frame: the partner's own link pulses only push an idle-high pin higher.
; ethbench.edges() reads the tape back and measures the high and low times: a clean
; Manchester line at 40 MHz shows only 2-tick and 4-tick runs (50 and 100 ns), as many low
; as high. Context 0 halts once the capture is armed, by design.

.default CLK_KHZ, 40000
.default RXP, 2
.equ eth_link.CLK_KHZ, CLK_KHZ
.equ eth_link.CTX, 1
.equ logic_analyzer.CTX, 0
.equ logic_analyzer.MASK, 1 << RXP

.include "eth_link.asm"
.include "../../firmware/logic_analyzer.asm"
