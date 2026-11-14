; DCF77 time code (PTB, "DCF77 time code"), at logic level on pin PIN for a 77.5 kHz carrier
; modulator: low while the carrier is reduced. At the start of every second the carrier is
; reduced for 0.1 s (a 0) or 0.2 s (a 1); second 59 has no mark, which marks the minute.
; One minute carries 59 bits, bit k in second k: 0 the minute start (always 0), 1-14 warning
; and weather bits, 15 the call bit, 16 A1, 17-18 Z1 Z2, 19 A2, 20 the start of time (always 1),
; 21-27 minutes, 28 P1, 29-34 hours, 35 P2, 36-41 day of month, 42-44 day of week (Monday 1),
; 45-49 month, 50-57 year (00-99), 58 P3; BCD, least significant bit first. P1, P2 and P3
; make bits 21-28, 29-35 and 36-58 hold an even number of ones.
;
; Host (POP), one frame per minute: bits 0..31, then bits 32..58 in [26:0]. The chip sets
; bit 0 to 0, bit 20 to 1 and computes the three parity bits. The first frame starts at
; once. Each next one is taken during the minute before, one word per second right after a
; mark, and only when the mailbox already holds it (no wait, so the seconds stay exact):
; send it by second 56. If it is not complete at second 59, the last frame is sent again and
; the chip raises IRQ 0xD7.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default TB, 0xF8                           ; tape: the frame, second 59's start
.equ SEC, CLK_KHZ * 1000
.equ T0, CLK_KHZ * 100                      ; 0.1 s
.equ T1, CLK_KHZ * 200                      ; 0.2 s
.equ LEAD, 64                               ; wake this long before a second starts
.equ R0, SEC - T0 - LEAD                    ; the rest of a 0's second, in four waits
.equ R1, SEC - T1 - LEAD
.equ RM, SEC - LEAD                         ; second 59
.equ D, 1 << PIN
.equ W0, TB
.equ W1, TB + 1
.equ NX, TB + 2                             ; words of the next frame taken so far
.equ NEW, TB + 3                            ; the next frame (2 words)
.assert RM // 4 < 1 << 24, "a quarter second must fit ADDT"
.assert T0 > 4 * LEAD, "the clock is too slow for the marks"

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  16
    EMIT  D, D, D            ; full carrier
    POP   r2
    POP   r3
    RDTIME r1                ; a time base before fix (escert joins fix's two callers)
    SETT  r1
    CALL  fix
    MOVI  r0, 0
    STT   r0, r0, NX
    WAITT
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1                 ; tmr = second 0
minute:
    MOVI  r0, 0
    STT   r2, r0, W0
    STT   r3, r0, W1
    LDLC  32
s0:
    SHR   r2, 1              ; C = the bit of this second
    CALL  second
    DJNZ  s0
    MOVI  r0, 0
    LDT   r2, r0, W1
    LDLC  27
s1:
    SHR   r2, 1
    CALL  second
    DJNZ  s1
    WAITT                    ; second 59: no mark; the next frame, if it is all here
    MOVI  r0, 0
    LDT   r1, r0, NX
    CMPI  r1, 2
    JZ    fresh
    IRQ   0xD7               ; not here: the last frame again
    LDT   r2, r0, W0
    LDT   r3, r0, W1
    JMP   next
fresh:
    LDT   r2, r0, NEW
    LDT   r3, r0, NEW + 1
    STT   r0, r0, NX
    CALL  fix
next:
    ADDT  RM // 4            ; tmr is still second 59's start: the minute stays exact
    ADDT  RM // 4
    ADDT  RM // 4
    ADDT  RM - 3 * (RM // 4)
    WAITT
    ADDT  LEAD               ; tmr = the next second 0
    JMP   minute

; ---- one second: C = its bit, tmr = its start (now LEAD before it)
second:
    EMIT  D, 0, D            ; carrier reduced
    JC    one
    ADDT  T0
    EMIT  D, D, D
    WAITT
    MOVI  r3, 0              ; a word of the next frame, if one is due and waiting
    LDT   r1, r3, NX
    CMPI  r1, 2
    JZ    w0                 ; (a whole frame is taken already)
    RXREADY
    JZ    w0
    POP   r0                 ; (RXREADY saw it: no wait)
    STT   r0, r1, NEW
    ADDI  r1, 1
    STT   r1, r3, NX
w0:
    ADDT  R0 // 4
    ADDT  R0 // 4
    ADDT  R0 // 4
    ADDT  R0 - 3 * (R0 // 4)
    WAITT
    ADDT  LEAD
    RET
one:
    ADDT  T1
    EMIT  D, D, D
    WAITT
    MOVI  r3, 0              ; a word of the next frame, if one is due and waiting
    LDT   r1, r3, NX
    CMPI  r1, 2
    JZ    w1                 ; (a whole frame is taken already)
    RXREADY
    JZ    w1
    POP   r0                 ; (RXREADY saw it: no wait)
    STT   r0, r1, NEW
    ADDI  r1, 1
    STT   r1, r3, NX
w1:
    ADDT  R1 // 4
    ADDT  R1 // 4
    ADDT  R1 // 4
    ADDT  R1 - 3 * (R1 // 4)
    WAITT
    ADDT  LEAD
    RET

; ---- r2 = bits 0..31, r3 = bits 32..58: bit 0 = 0, bit 20 = 1, P1, P2, P3 (uses r0, r1).
; Each parity by folding (x ^= x >> k), with no loops: escert keeps its time bound through it.
fix:
    SHR   r2, 1
    SHL   r2, 1              ; bit 0 = 0
    MOVI  r0, 0
    MOVHI r0, 0x0010
    OR    r2, r0             ; bit 20 = 1
    MOV   r0, r2             ; P1: bits 21..28 even
    SHL   r0, 3
    SHR   r0, 24
    MOV   r1, r0
    SHR   r1, 4
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 2
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 1
    XOR   r0, r1
    TSTI  r0, 1
    JZ    p2
    MOVI  r0, 0
    MOVHI r0, 0x1000
    XOR   r2, r0             ; flip bit 28
p2:                          ; P2: bits 29..35 even (r2[31:29], r3[3:0])
    MOV   r0, r2
    SHR   r0, 29
    MOV   r1, r3
    SHL   r1, 28
    SHR   r1, 25
    OR    r0, r1
    MOV   r1, r0
    SHR   r1, 4
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 2
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 1
    XOR   r0, r1
    TSTI  r0, 1
    JZ    p3
    MOVI  r0, 8
    XOR   r3, r0             ; flip bit 35
p3:                          ; P3: bits 36..58 even (r3[26:4])
    MOV   r0, r3
    SHL   r0, 5
    SHR   r0, 9
    MOV   r1, r0
    SHR   r1, 16
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 8
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 4
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 2
    XOR   r0, r1
    MOV   r1, r0
    SHR   r1, 1
    XOR   r0, r1
    TSTI  r0, 1
    JZ    pdone
    MOVI  r0, 0
    MOVHI r0, 0x0400
    XOR   r3, r0             ; flip bit 58
pdone:
    RET
