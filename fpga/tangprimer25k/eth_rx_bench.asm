; The 10BASE-T receive bench (README.md, Ethernet): firmware/eth_rx.asm receives on RXP with
; the line unit, and eth_link.asm keeps the link partner's link up with link pulses on the
; transmit pair, so the partner sends its frames. One program load, two contexts, 40 MHz.
;
;   ctx 0  eth_rx    RXP pin 2 (uio[2]), both-edge sampling unless BOTH = 0
;   ctx 1  eth_link  TD+ pin 0, TD- pin 1

.default CLK_KHZ, 40000
.default BOTH, 1
.equ eth_rx.CLK_KHZ, CLK_KHZ
.equ eth_rx.BOTH, BOTH
.equ eth_rx.CTX, 0
.equ eth_link.CLK_KHZ, CLK_KHZ
.equ eth_link.CTX, 1

.include "../../firmware/eth_rx.asm"
.include "eth_link.asm"
