; XY2-100: the galvanometer scanner protocol of laser systems, as the controller sends it.
; A 2 MHz clock, a frame sync, and one data line per axis, X and Y, sent together. A
; frame is 20 bits, MSB first, each set at the clock's rising edge and taken by the
; scanner at its falling edge: the three control bits 001 (a 16-bit position), the
; 16-bit position (two's complement) and a parity bit that makes the 20 bits even. SYNC
; is high for the first 19 bits and low for the parity bit. Frames follow back to back,
; 10 us each, so each axis gets a new position at 100 kHz.
;
; (XY2-100 interface descriptions: SCANLAB, "XY2-100 Standard", and Raylase, "XY2-100
; protocol"; the receiving side's figures in the sigrok decoder are not used here.)
;
;   ctx 0  CLK and SYNC: every frame the same 40 edges
;   ctx 1  X: one 20-bit frame per point, shifted out with EMITX at the rising edges
;   ctx 2  Y: likewise
;   ctx 3  before the first frame, turns every point into its two 20-bit frames, parity
;          included (frame20), in a table per axis; then halts
;
; The figure is a list of points on the tape: word PTS holds n (1 to 64; 0 is taken as
; 64), and words PTS + 1 .. PTS + n hold x << 16 | y. From T0 each axis plays its table in
; a loop, one point per frame, so the beam traces the figure once every n * 10 us. Both
; axes count the same frames, so they stay on the same point. A new figure is a new run:
; write the tape, then START.
;
; The clock needs four instructions per bit, two EMITs and two ADDTs, and a context has
; one slot every four ticks, so a 500 ns bit needs at least 16 ticks: the program runs
; from 40 MHz (20 ticks a bit) and needs a whole number of ticks per 500 ns. At 40 MHz
; the clock is 10 ticks high and 10 low; at 50 MHz, 12 and 13.

.default CC, 0                              ; clock and sync
.default XC, 1                              ; X axis
.default YC, 2                              ; Y axis
.default BC, 3                              ; builds the frame tables, then halts
.default CLK_KHZ, 40000
.assert CLK_KHZ >= 40000, XY2-100 needs at least 40 MHz: four instructions per 500 ns bit
.assert CLK_KHZ % 2000 == 0, XY2-100 needs a whole number of ticks per 500 ns bit
.default PTS, 0                             ; tape: n, then up to 64 points
.default FX, 65                             ; tape: X frames, 64 words
.default FY, 129                            ; tape: Y frames, 64 words
.equ BIT, CLK_KHZ // 2000                   ; ticks per bit
.equ HI, BIT // 2                           ; clock high, then low for the rest
.equ LO, BIT - HI
.equ T0, 16384                              ; the first frame: after both tables are built
.equ LEADIN, 4 * BIT                        ; each axis waits until this long before it

.equ CLK, 1 << 0
.equ SYNC, 1 << 1
.equ X, 1 << 2
.equ Y, 1 << 3

.entry CC, clock
.entry XC, xaxis
.entry YC, yaxis
.entry BC, build
.own   0, CC
.own   1, CC
.own   2, XC
.own   3, YC
; a square of side 0x8000 about the centre, until the host writes its own figure
.tape  PTS, 4, 0xC000C000, 0x4000C000, 0x40004000, 0xC0004000

; ---- CC: the clock and the frame sync. Bit 0 raises SYNC, bit 19 drops it; bits 1 to 18
; run as nine pairs, nine instructions a pair.
clock:
    ADDT  16
    EMIT  CLK | SYNC, SYNC, CLK | SYNC      ; idle: clock low, sync high
    ADDT  T0 - 16
c_frame:
    EMIT  CLK | SYNC, CLK | SYNC, CLK | SYNC   ; bit 0
    ADDT  HI
    EMIT  CLK, 0, CLK
    ADDT  LO
    LDLC  9
c_pair:                                     ; bits 1 to 18
    EMIT  CLK, CLK, CLK
    ADDT  HI
    EMIT  CLK, 0, CLK
    ADDT  LO
    EMIT  CLK, CLK, CLK
    ADDT  HI
    EMIT  CLK, 0, CLK
    ADDT  LO
    DJNZ  c_pair
    EMIT  CLK | SYNC, CLK, CLK | SYNC       ; bit 19, the parity bit: sync low
    ADDT  HI
    EMIT  CLK, 0, CLK
    ADDT  LO
    JMP   c_frame

; ---- frame20: r1 = a 16-bit position (in r1[15:0], zero above) -> r1 = its frame, the
; first bit at bit 31: 001, the position, the parity bit (bits 31 to 12). Uses r0 and r3.
frame20:
    MOV   r3, r1
    SHR   r3, 8
    XOR   r3, r1                 ; r3[7:0]: the position folded to 8 bits
    MOV   r0, r3
    SHR   r0, 4
    XOR   r3, r0
    MOV   r0, r3
    SHR   r0, 2
    XOR   r3, r0
    MOV   r0, r3
    SHR   r0, 1
    XOR   r3, r0                 ; r3[0]: the parity of the position's 16 bits
    SHL   r1, 13                 ; position to bits 28..13
    MOVI  r0, 0
    MOVHI r0, 0x2000             ; the control bits' one 1, bit 29
    OR    r1, r0
    TSTI  r3, 1
    JNZ   f20_done               ; odd position + the control 1: even already, parity 0
    MOVI  r0, 1 << 12            ; even position + the control 1: parity 1
    OR    r1, r0
f20_done:
    RET

; ---- BC: both tables, then halt. It queues no events, so the axes' certificates do not
; depend on it; it takes about 6,700 ticks (1,670 instructions), well before the axes read
; the tables at T0 - LEADIN.
build:
    MOVI  r2, 0
    LDLC  64
b_point:
    LDT   r1, r2, PTS + 1
    SHR   r1, 16                 ; x
    CALL  frame20
    STT   r1, r2, FX
    LDT   r1, r2, PTS + 1
    SHL   r1, 16
    SHR   r1, 16                 ; y
    CALL  frame20
    STT   r1, r2, FY
    ADDI  r2, 1
    DJNZ  b_point
    HALT

; ---- XC: from T0, the first n X frames in a loop.
xaxis:
    MOVI  r3, 0                  ; r1 = n, 1 to 64 (0 is 64)
    LDT   r1, r3, PTS
    MOVI  r0, 1
    SUB   r1, r0
    MOVI  r0, 63
    AND   r1, r0
    ADDI  r1, 1
    MOVI  r2, 0
    ADDT  T0 - LEADIN
    WAITT                        ; the tables are built; the stream starts from a known lead
    ADDT  16
    EMIT  X, 0, X
    ADDT  LEADIN - 16
x_frame:
    LDT   r0, r2, FX
    LDSH  r0
    LDLC  5                      ; four bits a pass
x_bit:
    EMITX X, MSB, X
    ADDT  BIT
    EMITX X, MSB, X
    ADDT  BIT
    EMITX X, MSB, X
    ADDT  BIT
    EMITX X, MSB, X
    ADDT  BIT
    DJNZ  x_bit
    ADDI  r2, 1
    CMP   r2, r1
    JNZ   x_frame
    MOVI  r2, 0
    JMP   x_frame

; ---- YC: the same for Y.
yaxis:
    MOVI  r3, 0
    LDT   r1, r3, PTS
    MOVI  r0, 1
    SUB   r1, r0
    MOVI  r0, 63
    AND   r1, r0
    ADDI  r1, 1
    MOVI  r2, 0
    ADDT  T0 - LEADIN
    WAITT
    ADDT  16
    EMIT  Y, 0, Y
    ADDT  LEADIN - 16
y_frame:
    LDT   r0, r2, FY
    LDSH  r0
    LDLC  5
y_bit:
    EMITX Y, MSB, Y
    ADDT  BIT
    EMITX Y, MSB, Y
    ADDT  BIT
    EMITX Y, MSB, Y
    ADDT  BIT
    EMITX Y, MSB, Y
    ADDT  BIT
    DJNZ  y_bit
    ADDI  r2, 1
    CMP   r2, r1
    JNZ   y_frame
    MOVI  r2, 0
    JMP   y_frame
