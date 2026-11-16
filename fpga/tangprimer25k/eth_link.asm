; 10BASE-T normal link pulses with timed pin events, for a bench that receives with the line
; unit (firmware/eth_rx.asm): the chip has one line unit, so the pulses that keep the link
; partner's link up come from EMIT instead. Each pulse is one bit time (100 ns) with TD+ high
; and TD- low, then both pins are released; the pulses are 16 ms apart, as eth_tx sends them.

.default CTX, 1
.default TDP, 0
.default TDN, 1
.default CLK_KHZ, 40000
.assert CLK_KHZ % 10000 == 0, a link pulse is a whole number of ticks (100 ns)
.equ W, CLK_KHZ // 10000                    ; 100 ns
.equ GAP, CLK_KHZ * 16                      ; 16 ms from one pulse to the next
.equ M, (1 << TDP) | (1 << TDN)

.entry CTX, main
.own   TDP, CTX
.own   TDN, CTX

main:
    ADDT  64
pulse:
    EMIT  M, 1 << TDP, M     ; TD+ high, TD- low
    ADDT  W
    EMIT  M, 0, 0            ; released
    ADDT  GAP - W
    JMP   pulse
