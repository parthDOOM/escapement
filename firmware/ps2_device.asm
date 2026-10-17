; PS/2 device (keyboard side) in firmware: the chip GENERATES the clock - a role
; reversal of the usual host-clocked buses - and computes odd parity on-chip.
; Context 1; CLK on pin 4, DATA on pin 5, both open-drain (value 1 = release).
;
; Host: POP a scancode byte; the chip waits for the bus to be idle (CLK high: the PC
; may inhibit by holding CLK low), sends start, 8 data bits LSB first, odd parity and
; stop at 12.5 kHz, and PUSHes 1.
;
; Per bit, from its slot start T: DATA at T (the middle of a CLK-high phase), CLK low
; at T + 500 (20 us later), CLK released at T + 1500; next bit at T + 2000 (80 us
; period, 40 us phases). The host samples DATA on the falling edge, 20 us after it was
; set. Three events per bit, all queued ahead; EMITX picks DATA from the frame word.

.default CTX, 1
.default CLK_PIN, 4
.default DATA_PIN, 5
.equ CLKM, 1 << CLK_PIN
.equ DATM, 1 << DATA_PIN
.equ HALFB, 500                  ; 20 us
.equ LOWB, 1000                  ; 40 us
.equ BITT, 2000                  ; 80 us

.entry CTX, main
.own CLK_PIN, CTX
.own DATA_PIN, CTX
.od CLK_PIN
.od DATA_PIN

main:
    ADDT  12
    EMIT  CLKM | DATM, CLKM | DATM, CLKM | DATM    ; release both
next:
    POP   r0                     ; scancode
    MOV   r1, r0                 ; odd parity: fold the byte to one bit
    MOV   r2, r1
    SHR   r2, 4
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 2
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 1
    XOR   r1, r2                 ; r1[0] = XOR of the data bits
    MOVI  r2, 1
    AND   r1, r2
    XOR   r1, r2                 ; parity bit: 1 when the data has an even number of 1s
    SHL   r1, 9
    MOVI  r2, 0xFF
    AND   r0, r2
    SHL   r0, 1                  ; data at [8:1], start (0) at [0]
    OR    r0, r1
    MOVI  r2, 0x400              ; stop (1) at [10]
    OR    r0, r2
    LDSH  r0                     ; sh = 11-bit frame, sent LSB first
idle:
    WAITP CLKM, CLKM             ; the PC is not inhibiting
    JNZ   idle
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    LDLC  11
bit:
    EMITX DATM, LSB, DATM        ; T: DATA = frame bit
    ADDT  HALFB
    EMIT  CLKM, 0, CLKM          ; T + 500: CLK low (host samples)
    ADDT  LOWB
    EMIT  CLKM, CLKM, CLKM       ; T + 1500: CLK released
    ADDT  BITT - HALFB - LOWB
    DJNZ  bit
    EMIT  DATM, DATM, DATM       ; DATA released after the stop bit
    ADDT  1250                   ; then >= 50 us idle before the next frame, and wait
    WAITT                        ; for it: the loop ran ahead of real time, and a new
    MOVI  r0, 1                  ; frame must not start under the queued old one
    PUSH  r0
    JMP   next
