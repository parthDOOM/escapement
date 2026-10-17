; WS2812B LED chain driver - a protocol the chip was never designed for, in firmware.
; Context 3, data on pin 0 (push-pull). 25 MHz: T0H 10 ticks (0.4 us), T1H 20 ticks
; (0.8 us), bit period 31 ticks (1.24 us).
;
; One data bit = three events: rise at T, the DATA BIT ITSELF at T+10 (EMITX: a 1
; keeps the line high, a 0 drops it), forced low at T+20. The pulse width is chosen
; by the data with no branch. The loop is 7 instructions (28 ticks) per 31-tick bit.
; If the firmware ever fell behind, admission would reject the late edge and fault
; the context: a visible error instead of a silently corrupted LED colour.
;
; Host protocol: POP start colour (GRB in [23:0]), POP per-LED colour step,
; POP LED count; drives the chain, holds the line low >= 80 us (latch), PUSHes 1.

.default CTX, 3              ; module parameters
.default PIN, 0
.equ D, 1 << PIN

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
    ADDT  10
    EMITX D, MSB, D          ; T+10  stay high for a 1, fall for a 0
    ADDT  10
    EMIT  D, 0, D            ; T+20  fall
    ADDT  11                 ; next bit at T+31
    DJNZ  bit
    ADD   r1, r2             ; next LED colour
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   led
    ADDT  2000               ; >= 80 us low: latch
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   frame
