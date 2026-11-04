; WS2812B LED chain driver - a protocol the chip was never designed for, in firmware.
; Context 3, data on pin 0 (push-pull). T0H 0.4 us, T1H 0.8 us, bit period 1.24 us; at
; 25 MHz 10, 20 and 31 ticks (at 33 MHz 13, 26 and 41).
;
; One data bit = three events: rise at T, the DATA BIT ITSELF at T+T0H (EMITX: a 1
; keeps the line high, a 0 drops it), forced low at T+T1H. The pulse width is chosen
; by the data with no branch. The loop is 7 instructions (28 ticks) per bit (31 ticks
; at 25 MHz).
; If the firmware ever fell behind, admission would reject the late edge and fault
; the context: a visible error instead of a silently corrupted LED colour.
;
; Host protocol: POP start colour (GRB in [23:0]), POP per-LED colour step,
; POP LED count; drives the chain, holds the line low >= 80 us (latch), PUSHes 1.

.default CTX, 3              ; module parameters
.default PIN, 0
.default CLK_KHZ, 25000
.equ D, 1 << PIN
.equ T0H, (2 * CLK_KHZ + 2500) // 5000      ; 0.4 us
.equ T1H, (4 * CLK_KHZ + 2500) // 5000      ; 0.8 us
.equ PER, (31 * CLK_KHZ + 12500) // 25000   ; 1.24 us
.assert PER >= 30, 28 ticks a bit plus the work between LEDs: escert certifies from PER 30 (CLK_KHZ >= 23791)

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  20
    EMIT  D, 0, D            ; line low (idle / reset)
frame:
    POP   r1                 ; colour
    POP   r2                 ; step
    POP   r3                 ; count
    RDTIME r0
    ADDI  r0, 40
    SETT  r0
led:
    MOV   r0, r1
    SHL   r0, 8              ; GRB to bits 31:8, MSB (G7) first
    LDSH  r0
    LDLC  24
bit:
    EMIT  D, D, D            ; T     rise
    ADDT  T0H
    EMITX D, MSB, D          ; T+T0H stay high for a 1, fall for a 0
    ADDT  T1H - T0H
    EMIT  D, 0, D            ; T+T1H fall
    ADDT  PER - T1H          ; next bit at T+PER
    DJNZ  bit
    ADD   r1, r2             ; next LED colour
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   led
    ADDT  80 * CLK_KHZ // 1000 ; >= 80 us low: latch
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   frame
