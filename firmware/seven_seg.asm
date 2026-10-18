; Seven-segment animation for the Tiny Tapeout demo board's display (bank B, v1.2).
; The board's display is common cathode, segment a..g on uo_out[0..6] and the decimal
; point on uo_out[7], active high. uo_out[2:0] are the host link (MISO, IRQ, RUN), so
; a, b and c show the link: c stays lit while the chip runs. Bank B is uo_out[7:3], so
; this context draws with d (B0), e (B1), f (B2), g (B3) and dp (B4).
;
; A dot runs down the left side and back along the bottom: f, g, e, d, dp, one frame
; every 160 ms. Every frame is a DRIVE event at an absolute deadline, so the animation
; never drifts. All timing derives from CLK_KHZ (the firmware test runs it at 25 kHz).

.default CTX, 0
.default CLK_KHZ, 25000                ; core clock in kHz
.equ FRAME, CLK_KHZ * 160              ; 160 ms
.equ D, 1 << 0                         ; bank B logical pins
.equ E, 1 << 1
.equ F, 1 << 2
.equ G, 1 << 3
.equ DP, 1 << 4
.equ ALL, D | E | F | G | DP

.bank  CTX
.own   0, CTX
.own   1, CTX
.own   2, CTX
.own   3, CTX
.own   4, CTX
.entry CTX, main

main:
    ADDT  16
frame:
    EMIT  ALL, F, ALL
    ADDT  FRAME
    EMIT  ALL, G, ALL
    ADDT  FRAME
    EMIT  ALL, E, ALL
    ADDT  FRAME
    EMIT  ALL, D, ALL
    ADDT  FRAME
    EMIT  ALL, DP, ALL
    ADDT  FRAME
    JMP   frame
