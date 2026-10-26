; SENT (SAE J2716) sensor: the single-edge nibble transmission of automotive sensors
; (throttle, pedal and pressure sensors), as the sensor sends it. Context 0, output on
; pin 0 (push-pull, idle high); context 1 takes frame values from the host.
;
; Format (NXP AN4219, "SENT/SPC Driver for MPC5510"; Microchip, SENT module, "Recommended
; J2716 CRC Implementation"): unit time 3 us. Every pulse starts with a falling edge and
; stays low for 5 unit times; its length, falling edge to falling edge, carries it. A
; frame is a 56-unit sync pulse, a status nibble, six data nibbles and a CRC nibble, each
; nibble 12 + value units long, then optionally a pause pulse. The CRC is x^4 + x^3 +
; x^2 + 1 with seed 5 over the six data nibbles, followed by one zero nibble (the
; recommended method), by table.
;
; At 25 MHz a unit is exactly 75 ticks. Frames follow each other with no gap: each pulse
; ends at the next one's falling edge, so every edge is at its exact tick. FRAME_UT = 0
; sends no pause (the frame length follows the data); otherwise a pause pulse makes every
; frame FRAME_UT units long (284 to 920). The output context reads the frame word once
; per frame, at the start of its sync pulse, and computes the CRC during it; it never
; waits on anything but its own events.
;
; Host (FEED context): POP words; [27:24] status, [23:0] the six data nibbles, first
; nibble in [23:20]. A word changes the next frame to start; one word in the tape is
; the whole frame, so a frame never mixes two values. Before the first word the sensor
; sends zeros.

.default OUT, 0
.default FEED, 1
.default PIN, 0
.default CLK_KHZ, 25000
.default FRAME_UT, 0                        ; 0: no pause pulse
.default TB, 0xD0                           ; tape: 33 words from here
.equ P, 1 << PIN
.equ UT, CLK_KHZ * 3 // 1000                ; unit time, ticks (3 us)
.equ LOWT, 5 * UT                           ; low part of every pulse
.equ LEAD, 24                               ; RDTIME to the first EMIT: 5 instructions
.equ DW, TB                                 ; frame word from the host
.equ CRCT, TB + 1                           ; CRC table, 16 words
.equ PAUSEU, FRAME_UT - 56 - 8 * 12         ; pause units with all nibbles 0
.equ HASP, (FRAME_UT + 1023) // 1024        ; 1 with a pause pulse (FRAME_UT <= 1023)

.entry OUT, out
.entry FEED, feed
.own   PIN, OUT
.tape  CRCT, 0, 13, 7, 10, 14, 3, 9, 4, 1, 12, 6, 11, 15, 2, 8, 5

; ---- OUT: frames back to back, every falling edge at its exact tick. A pulse of n
; units is timed by n's bits (ADDT 1, 2, 4, 8 ... units), so every path adds a constant.
out:
    ADDT  20
    EMIT  P, P, P            ; idle high
    RDTIME r0
    ADDI  r0, LEAD
    SETT  r0
frame:
    MOVI  r3, 0
    LDT   r0, r3, DW         ; this frame's word, read once
    EMIT  P, 0, P            ; sync pulse
    ADDT  LOWT
    EMIT  P, P, P
    ADDT  56 * UT - LOWT
    MOV   r2, r0             ; CRC over the six data nibbles, during the sync pulse
    SHL   r2, 8
    MOVI  r1, 5              ; seed
    LDLC  6
crc:
    LDT   r1, r1, CRCT
    MOV   r3, r2
    SHR   r3, 28
    XOR   r1, r3
    SHL   r2, 4
    DJNZ  crc
    LDT   r1, r1, CRCT       ; and one zero nibble
    SHL   r0, 4
    OR    r0, r1             ; status, six data nibbles, CRC: eight nibbles from [31:28]
    MOVI  r3, 0              ; sum of the nibble values
    LDLC  8
nib:
    MOV   r1, r0
    SHR   r1, 28
    SHL   r0, 4
    ADD   r3, r1
    EMIT  P, 0, P
    ADDT  LOWT
    EMIT  P, P, P
    ADDT  12 * UT - LOWT
    TSTI  r1, 1
    JZ    n2
    ADDT  UT
n2: TSTI  r1, 2
    JZ    n4
    ADDT  2 * UT
n4: TSTI  r1, 4
    JZ    n8
    ADDT  4 * UT
n8: TSTI  r1, 8
    JZ    n16
    ADDT  8 * UT
n16:
    DJNZ  nib
    JMP   frame + HASP * (pause - frame)   ; to the pause pulse only if FRAME_UT
pause:
    EMIT  P, 0, P            ; pause pulse: the frame is FRAME_UT units long
    ADDT  LOWT
    EMIT  P, P, P
    ADDT  12 * UT - LOWT
    MOVI  r2, (PAUSEU - 12) & 0xFFFF   ; pause units past 12 (at most 756: ten bits)
    SUB   r2, r3
    TSTI  r2, 1
    JZ    p1
    ADDT  1 * UT
p1:
    TSTI  r2, 2
    JZ    p2
    ADDT  2 * UT
p2:
    TSTI  r2, 4
    JZ    p3
    ADDT  4 * UT
p3:
    TSTI  r2, 8
    JZ    p4
    ADDT  8 * UT
p4:
    TSTI  r2, 16
    JZ    p5
    ADDT  16 * UT
p5:
    TSTI  r2, 32
    JZ    p6
    ADDT  32 * UT
p6:
    TSTI  r2, 64
    JZ    p7
    ADDT  64 * UT
p7:
    TSTI  r2, 128
    JZ    p8
    ADDT  128 * UT
p8:
    TSTI  r2, 256
    JZ    p9
    ADDT  256 * UT
p9:
    TSTI  r2, 512
    JZ    pend
    ADDT  512 * UT
pend:
    JMP   frame

; ---- FEED: the host's word into the tape, one STT: a frame never mixes two words.
feed:
    POP   r0
    MOVI  r1, 0
    STT   r0, r1, DW
    JMP   feed
