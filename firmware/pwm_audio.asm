; PWM audio: 8-bit PCM samples from the host, played as pulse-width modulation on one pin at an
; exact sample period. Context 1, output on pin 6 (push-pull). A resistor-capacitor low-pass after
; the pin turns the pulse train into the sound (the carrier is far above hearing).
;
; Timing, in ticks of the chip's clock (CLK_KHZ): a sample lasts SP = CLK_KHZ * 1000 / RATE ticks
; (2,268 at 25 MHz for 11,025 samples a second: 11,023 Hz, 0.02% slow), split into eight PWM
; periods played as four pairs of PA and PB ticks (283 and 284 at 25 MHz), so the carrier is
; RATE * 8 (88 kHz) with no pattern below it; any remainder of SP / 4 is added at the end of the
; sample. In each period the pin is high for OFFS + s ticks and low for the rest, s the sample
; (0..255): both edges exist in every period, and the mean level is linear in s. Every edge is an
; event at an absolute deadline: a period starts at its own tick (r1, advanced by a constant), and
; the high time is added to it a bit at a time (ADDT 1, 2, 4 ... 128 on the bits of s), so every
; path adds a constant and every edge is certified.
;
; Host (mailbox): each word is four samples, first in [31:24]. The context moves at most one word
; a sample from its mailbox into a ring of 2^RING_LOG words in the tape; with the ring full it
; leaves the word in the mailbox, which holds the host back (a push into a full mailbox is lost,
; so the host pushes only into room). An empty ring plays the mid level, 128, as silence, and
; counts it in tape word UNDER: a host that falls behind is heard, never late.
;
; Registers: r0 scratch and the sample in the period subroutine; r1 the next period's start (a
; time); r2 the ring's read position, in samples (0 .. 4 * 2^RING_LOG - 1); r3 scratch. sh holds
; the sample being played. Tape: the ring at RING, then COUNT (words in the ring), WRP (the next
; word to write) and UNDER (samples played as silence), all from VARS.

.default CTX,      1         ; module parameters
.default PIN,      6
.default CLK_KHZ,  25000
.default RATE,     11025
.default OFFS,     8         ; the shortest high time, ticks
.default RING,     0x00      ; tape: the sample ring
.default RING_LOG, 7         ; 128 words = 512 samples = 46 ms at 11,025 Hz
.default VARS,     0x80      ; tape: COUNT, WRP, UNDER
.equ P,     1 << PIN
.equ SP,    (CLK_KHZ * 1000 + RATE // 2) // RATE   ; sample period, ticks
.equ PAIR,  SP // 4                                ; two PWM periods
.equ PA,    PAIR // 2
.equ PB,    PAIR - PA
.equ REM,   SP - 4 * PAIR                          ; added at the end of a sample (0 at 25 MHz)
.equ RW,    1 << RING_LOG
.equ COUNT, VARS
.equ WRP,   VARS + 1
.equ UNDER, VARS + 2
; RDTIME to the first period's start. The falling edge is queued up to 18 instructions (72 ticks)
; after the rising one, but may be due OFFS ticks after it, so a rise must be queued at least
; 72 - OFFS ticks ahead; from the first period on the program then runs ahead of the pin (a period
; takes at most 34 instructions, 136 ticks, to queue), until the event queue holds it back.
.equ LEAD,  100
.assert OFFS >= 4, the shortest pulse must be at least one instruction slot
.assert OFFS + 255 + 8 <= PA, a period must fit the longest high time and a low time of 8 ticks
.assert RING_LOG <= 7, the read position in samples must fit TSTI's 16 bits with room to wrap
.assert RING + RW <= VARS, the ring and the variables must not overlap
.assert VARS + 3 <= 256, the variables must fit the tape

.entry CTX, main
.own   PIN, CTX
.tape  VARS, 0, 0, 0         ; COUNT, WRP, UNDER

main:
    ADDT  20
    EMIT  P, 0, P            ; the pin driven low
    MOVI  r2, 0              ; read position: the ring's first sample
    MOVI  r0, 128
    LDSH  r0                 ; silence until samples arrive
    RDTIME r1
    ADDI  r1, LEAD           ; the first period's start
sample:
    LDLC  4
pair:
    CALL  pwm
    ADDI  r1, PA
    CALL  pwm
    ADDI  r1, PB
    DJNZ  pair
    ADDI  r1, REM
    ; ---- one word from the mailbox into the ring, if there is one and room for it
    RXREADY                  ; Z = 1: the mailbox is empty
    JZ    fetch
    MOVI  r3, 0
    LDT   r3, r3, COUNT
    CMPI  r3, RW - 1
    JNC   fetch              ; full (one word kept free): leave the word in the mailbox
    POP   r0
    MOVI  r3, 0
    LDT   r3, r3, WRP
    STT   r0, r3, RING
    ADDI  r3, 1
    SHL   r3, 32 - RING_LOG
    SHR   r3, 32 - RING_LOG  ; (WRP + 1) mod RW
    MOVI  r0, 0
    STT   r3, r0, WRP
    LDT   r3, r0, COUNT
    ADDI  r3, 1
    STT   r3, r0, COUNT
    ; ---- the next sample: the ring's, or silence if it is empty
fetch:
    MOVI  r3, 0
    LDT   r3, r3, COUNT
    CMPI  r3, 0
    JZ    silent
    MOV   r3, r2
    SHR   r3, 2              ; the word holding sample r2
    LDT   r0, r3, RING
    TSTI  r2, 2
    JZ    f1
    SHL   r0, 16             ; samples 2 and 3 of the word
f1: TSTI  r2, 1
    JZ    f0
    SHL   r0, 8              ; samples 1 and 3
f0: SHR   r0, 24
    LDSH  r0                 ; the sample to play
    ADDI  r2, 1
    TSTI  r2, 4 * RW
    JZ    f2
    MOVI  r2, 0              ; the read position wraps with the ring
f2: TSTI  r2, 3
    JNZ   sample             ; the word still has samples
    MOVI  r0, 0              ; its last sample read: one word fewer in the ring
    LDT   r3, r0, COUNT
    MOVI  r0, 1
    SUB   r3, r0
    MOVI  r0, 0
    STT   r3, r0, COUNT
    JMP   sample
silent:
    MOVI  r0, 128
    LDSH  r0
    MOVI  r0, 0
    LDT   r3, r0, UNDER
    ADDI  r3, 1
    STT   r3, r0, UNDER
    JMP   sample

; pwm: one PWM period from r1: high for OFFS + sh ticks, then low until the next period's start.
; Every path adds a constant to tmr, so both edges are at certified ticks. Uses r0.
pwm:
    SETT  r1
    EMIT  P, P, P            ; the period's rising edge
    STSH  r0                 ; r0 = the sample
    ADDT  OFFS
    TSTI  r0, 1
    JZ    p1
    ADDT  1
p1: TSTI  r0, 2
    JZ    p2
    ADDT  2
p2: TSTI  r0, 4
    JZ    p3
    ADDT  4
p3: TSTI  r0, 8
    JZ    p4
    ADDT  8
p4: TSTI  r0, 16
    JZ    p5
    ADDT  16
p5: TSTI  r0, 32
    JZ    p6
    ADDT  32
p6: TSTI  r0, 64
    JZ    p7
    ADDT  64
p7: TSTI  r0, 128
    JZ    p8
    ADDT  128
p8: EMIT  P, 0, P            ; the falling edge, OFFS + s ticks after the rise
    RET
