; Stepper motor motion: STEP and DIR for a step/direction driver (Allegro A4988 timing: STEP
; high and low at least 1 us, DIR set up and held 200 ns around a rising STEP), with a
; linear speed ramp (Atmel AVR446, "Linear speed control of stepper motor"): the first
; step interval c0 = 0.676 x sqrt(2 / acceleration) (the host computes it, in us), then
;     c_n = c_(n-1) - (2 c_(n-1) + rest) / (4n + 1),   rest = (2 c_(n-1) + rest) mod (4n + 1)
; until the interval reaches CMIN (top speed), the ramp table is full (RMAX steps), or half
; the move is done; then the same intervals backwards to stop. The chip computes the ramp
; into the tape before a move, then plays it: every step interval is PMIN us plus constant
; delays (one ADDT for each set bit of the rest), so escert certifies any ramp.
;
; Host (POP), [31:28] the command:
;   1: c0 [23:0] in us (at least PMIN)
;   2: CMIN [23:0] in us, the top speed's interval (at least PMIN)
;   3: move [23:0] steps (1 or more), DIR level [24]; reply when done: the steps made
; Intervals are in units of US = CLK_KHZ // 1000 ticks (1 us at a whole-MHz clock), up to
; PMIN + 65,535 us. DIR is set at least LAT ticks plus the ramp computation before the first
; STEP, and held until the next move.

.default CTX, 0
.default STEP_PIN, 0
.default DIR_PIN, 1
.default CLK_KHZ, 25000
.default PMIN, 50                           ; the shortest step interval, us (20,000 steps/s)
.default RMAX, 128                          ; the longest ramp, steps
.default TB, 0x40                           ; tape: the ramp, then its variables
.equ ST, 1 << STEP_PIN
.equ DR, 1 << DIR_PIN
.equ US, CLK_KHZ // 1000
.equ PW, 2 * US                             ; STEP high 2 us (tA 1 us)
.equ RAMP, TB
.equ C0, TB + RMAX
.equ CMIN, TB + RMAX + 1
.equ N, TB + RMAX + 2                       ; steps still to make
.equ N0, TB + RMAX + 3                      ; steps in this move
.equ K, TB + RMAX + 4                       ; ramp length
.equ CR, TB + RMAX + 5                      ; the cruise interval
.equ REST, TB + RMAX + 6
.equ J, TB + RMAX + 7                       ; ramp index, then intervals done
.assert TB + RMAX + 8 <= 256, "the tape area does not fit"
.assert PMIN >= 4, "STEP high 2 us and low 2 us at least"
.assert CLK_KHZ >= 5000, "escert certifies stepper from 5 MHz"
.equ LAT, 64

.tape C0, 20000
.tape CMIN, 1000

.entry CTX, main
.own   STEP_PIN, CTX
.own   DIR_PIN, CTX

main:
    ADDT  LAT
    EMIT  ST|DR, 0, ST|DR    ; STEP low, DIR low
cmd:
    POP   r0
    MOV   r2, r0             ; (DIR in [24])
    MOV   r1, r0
    SHR   r1, 28
    SHL   r0, 8
    SHR   r0, 8              ; [23:0]
    MOVI  r3, 0
    CMPI  r1, 3
    JZ    move
    MOVI  r2, PMIN           ; 1, 2: at least PMIN
    CMP   r0, r2
    JNC   big
    MOV   r0, r2
big:
    CMPI  r1, 1
    JNZ   cm
    STT   r0, r3, C0
    JMP   cmd
cm:
    CMPI  r1, 2
    JNZ   cmd
    STT   r0, r3, CMIN
    JMP   cmd

; ---- 3: a move. DIR first; then the ramp, computed while nothing is due.
move:
    CMPI  r0, 0
    JZ    cmd
    STT   r0, r3, N
    STT   r0, r3, N0
    WAITT
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    SHR   r2, 24
    TSTI  r2, 1
    JZ    dlow
    EMIT  DR, DR, DR
    JMP   ramp
dlow:
    EMIT  DR, 0, DR
ramp:                        ; k at most min(RMAX, (N - 1) / 2)
    MOVI  r3, 0
    LDT   r0, r3, N
    MOVI  r1, 1
    SUB   r0, r1
    SHR   r0, 1
    MOVI  r1, RMAX
    CMP   r0, r1             ; borrow: below RMAX
    JC    kok
    MOV   r0, r1
kok:
    STT   r0, r3, K
    LDT   r2, r3, C0         ; r2 = the interval c
    STT   r3, r3, REST
    STT   r3, r3, J
rl:
    MOVI  r3, 0
    LDT   r1, r3, J
    LDT   r0, r3, K
    CMP   r1, r0
    JZ    rdone              ; the limit
    LDT   r0, r3, CMIN
    CMP   r0, r2             ; no borrow: c <= CMIN, top speed
    JNC   rdone
    STT   r2, r1, RAMP       ; RAMP[j] = c
    ADDI  r1, 1
    STT   r1, r3, J
    SHL   r1, 2
    ADDI  r1, 1              ; 4n + 1, n = j + 1
    MOV   r0, r2
    SHL   r0, 1
    LDT   r3, r3, REST
    ADD   r0, r3             ; 2c + rest
    MOVI  r3, 0              ; r0 / r1: quotient r0, remainder r3 (restoring division)
    LDLC  32
rdv:
    SHL   r3, 1
    SHL   r0, 1
    JNC   rd0
    ADDI  r3, 1
rd0:
    CMP   r3, r1
    JC    rd1
    SUB   r3, r1
    ADDI  r0, 1
rd1:
    DJNZ  rdv
    SUB   r2, r0             ; c -= quotient
    MOVI  r0, 0
    STT   r3, r0, REST
    JMP   rl
rdone:                       ; r1 = j = k; the cruise interval max(c, CMIN)
    MOVI  r3, 0
    STT   r1, r3, K
    LDT   r0, r3, CMIN
    CMP   r2, r0
    JNC   crok
    MOV   r2, r0
crok:
    STT   r2, r3, CR
    STT   r3, r3, J          ; intervals done
    WAITT                    ; a fresh time base after the computation
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
; ---- the steps (tmr = this STEP's rising edge)
stp:
    EMIT  ST, ST, ST
    ADDT  PW
    EMIT  ST, 0, ST
    WAITT                    ; (the pulse is out: the next interval is worked out from here)
    MOVI  r3, 0
    LDT   r0, r3, N
    MOVI  r1, 1
    SUB   r0, r1
    STT   r0, r3, N
    JZ    mdone              ; that was the last
    LDT   r1, r3, J          ; interval i: RAMP[i] if i < k, RAMP[N0 - 2 - i] if that is < k,
    LDT   r2, r3, K          ; else the cruise interval
    CMP   r1, r2
    JC    pa
    LDT   r0, r3, N0
    SUB   r0, r1
    MOVI  r1, 2
    SUB   r0, r1
    CMP   r0, r2
    JC    pm
    LDT   r0, r3, CR
    JMP   pv
pa:
    LDT   r0, r1, RAMP
    JMP   pv
pm:
    LDT   r0, r0, RAMP
pv:
    LDT   r1, r3, J
    ADDI  r1, 1
    STT   r1, r3, J
    MOVI  r1, PMIN           ; the interval: PMIN, plus each set bit of the rest
    SUB   r0, r1
    ADDT  PMIN * US - PW
    TSTI  r0, 1
    JZ    b1
    ADDT  US
b1:
    TSTI  r0, 2
    JZ    b2
    ADDT  2 * US
b2:
    TSTI  r0, 4
    JZ    b3
    ADDT  4 * US
b3:
    TSTI  r0, 8
    JZ    b4
    ADDT  8 * US
b4:
    TSTI  r0, 0x10
    JZ    b5
    ADDT  0x10 * US
b5:
    TSTI  r0, 0x20
    JZ    b6
    ADDT  0x20 * US
b6:
    TSTI  r0, 0x40
    JZ    b7
    ADDT  0x40 * US
b7:
    TSTI  r0, 0x80
    JZ    b8
    ADDT  0x80 * US
b8:
    TSTI  r0, 0x100
    JZ    b9
    ADDT  0x100 * US
b9:
    TSTI  r0, 0x200
    JZ    b10
    ADDT  0x200 * US
b10:
    TSTI  r0, 0x400
    JZ    b11
    ADDT  0x400 * US
b11:
    TSTI  r0, 0x800
    JZ    b12
    ADDT  0x800 * US
b12:
    TSTI  r0, 0x1000
    JZ    b13
    ADDT  0x1000 * US
b13:
    TSTI  r0, 0x2000
    JZ    b14
    ADDT  0x2000 * US
b14:
    TSTI  r0, 0x4000
    JZ    b15
    ADDT  0x4000 * US
b15:
    TSTI  r0, 0x8000
    JZ    bx
    ADDT  0x8000 * US
bx:
    JMP   stp
mdone:
    WAITT
    LDT   r0, r3, N0
    PUSH  r0
    JMP   cmd
