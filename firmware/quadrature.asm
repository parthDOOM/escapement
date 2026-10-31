; Quadrature encoder decoder (v1.6), counting every edge of A and B (x4): the capture unit
; records each change of the two pins with its tick, and the program follows the records.
; A position the host can ask for at any time; no edge is missed or counted twice while
; the program keeps up, and when it cannot, it says so.
;
; A on pin PA, B on pin PA + 1. The pins' Gray code g = B A (00, 01, 11, 10 going forward)
; becomes a phase p = g ^ (g >> 1) (0, 1, 2, 3); each record's step is (p - p_prev) mod 4:
; 1 forward, 3 back, 0 none (an epoch marker), 2 both pins at once (an illegal step: IRQ 3,
; not counted).
;
; Host (POP any word): the program PUSHes the position (32-bit two's complement), if the
; TX mailbox has room (keep at most two requests outstanding). IRQ 1: the encoder ran
; faster than the program for longer than the capture ring absorbs (a step every ~100
; cycles sustained, ~400k steps/s at 40 MHz, bursts of 128 more): counts were lost and
; decoding restarts from the pins' present state. The position starts at 0.

.default CTX, 0
.default PA, 0                    ; A; B is the next pin
.default BANKB, 0
.assert (PA >= 0) & (PA <= 6), A and B are pins PA and PA + 1 of a bank
.assert (BANKB == 0) | (PA <= 3), bank B has pins 0..4
.equ CFG, 0x8100 | (BANKB << 9) | (3 << PA)     ; enable, ring, bank, the two pins

.entry CTX, main

main:
    MOVI  r3, 0                   ; r3 = the position
restart:
    MOVI  r0, 0
    MOVHI r0, CFG
    CAPT  r0                      ; a ring over the whole tape; slot 0: a snapshot
snap:
    GETC  r1
    SHL   r1, 24
    JZ    snap
    MOVI  r0, 0
    LDT   r1, r0, 0
    MOVI  r0, 1                   ; r0: the next slot to read
    SHL   r1, 6 - PA
    SHR   r1, 30                  ; g
    MOV   r2, r1
    SHR   r2, 1
    XOR   r1, r2                  ; p
    LDSH  r1                      ; sh = the phase so far

; Each poll: r1 = (writer - r0) << 25: C = 128 records or more behind, Z = none to read.
poll:
    GETC  r1
    SUB   r1, r0
    SHL   r1, 25
    JC    over
    JZ    idle
    LDT   r1, r0, 0
    ADDI  r0, 1
    SHL   r1, 6 - PA
    SHR   r1, 30                  ; g
    MOV   r2, r1
    SHR   r2, 1
    XOR   r2, r1                  ; p
    STSH  r1                      ; the previous phase
    LDSH  r2
    SUB   r2, r1
    SHL   r2, 30                  ; the step in [31:30]; Z: none
    JZ    poll
    SHL   r2, 1                   ; C = step[1]; then Z = step[0] clear
    JNC   fwd
    JZ    bad
    MOVI  r1, 1
    SUB   r3, r1
    JMP   poll
fwd:
    ADDI  r3, 1
    JMP   poll
bad:
    IRQ   3
    JMP   poll

idle:                             ; nothing to read: a request? a stopped capture unit?
    RXREADY                       ; Z = none
    JZ    idle_c
    TXREADY                       ; Z = full
    JZ    idle_c
    POP   r1
    PUSH  r3
idle_c:
    GETC  r2
    SHL   r2, 2                   ; C = overflow (GETC bit 30)
    JC    over
    JMP   poll
over:
    IRQ   1
    JMP   restart
