; DShot600 for four ESCs: the digital throttle protocol of brushless motor controllers, as
; a flight controller drives it. Four motors on pins 0-3 (push-pull), all four frames in
; lockstep from one context; a second context takes throttle values from the host.
;
; Timing (brushlesswhoop.com, "DSHOT - the missing Handbook"): 600 kbit/s, bit 1.67 us,
; high 1.25 us for a 1 and 0.625 us for a 0. A frame is 16 bits, MSB first: an 11-bit
; value (0 disarmed, 1-47 commands, 48-2047 throttle), a telemetry request bit and a
; 4-bit CRC = (v ^ v >> 4 ^ v >> 8) & 0xF over the 12-bit v. Bidirectional DShot
; (BIDIR = 1) inverts the line (idle high, active low) and the CRC, and releases the
; line between frames (a pull-up holds it high) so that the ESC can reply.
;
; At 25 MHz a bit is 41 2/3 ticks. Three bits take exactly 125 ticks (5 us) as 42, 42
; and 41, so the bit rate is exactly 600 kbit/s: bit k starts ceil(k * 125 / 3) ticks
; after its frame. High times are 16 ticks (640 ns) and 31 ticks (1.24 us). One bit is
; three events for all four pins: the pulses start, each motor's own bit decides whether
; its pulse ends at 16 ticks (EMITR), and all end at 31. Frames repeat every FRAME ticks
; (default 3125: 8 kHz) from the start of the run, disarmed (v = 0) until the host sets
; a value. The output context never waits on anything but its own events.
;
; The frames live in the tape as lanes: word g of a buffer holds bits 3g, 3g+1 and 3g+2
; of all four frames, one nibble per bit, motor m in bit m of each nibble. There are two
; buffers. The feeder builds the inactive one, switches the pointer, then waits one frame
; period, so that no frame still reads the old buffer when it is next rebuilt.
;
; Host (FEED context): POP words; [11:0] = v (value << 1 | telemetry), [13:12] = motor,
; [14] = 1: all four motors. Every word waiting is taken before the next build, so a
; burst of four values reaches the motors in the same frame. Latency: under 0.2 ms.

.default OUT, 0                             ; context driving the pins
.default FEED, 1                            ; context taking host values
.default BIDIR, 0
.default CLK_KHZ, 25000
.default FRAME, CLK_KHZ // 8                ; frame period in ticks (8 kHz); <= 65535
.default TB, 0xE0                           ; tape: 17 words from here
.equ M, 0xF                                 ; motors 0-3 on pins 0-3
.equ ACT, M * (1 - BIDIR)                   ; pulse level
.equ IDLE, M * BIDIR                        ; line level between pulses
.equ REST, M * (1 - BIDIR)                  ; between frames: driven, or released (BIDIR)
.equ FLIP, 0xFFF0 * BIDIR                   ; bidirectional: inverted line, inverted CRC
.equ TRI, CLK_KHZ // 200                    ; three bits: 5 us
.equ B1, (TRI + 2) // 3                     ; bit periods 42, 42, 41: bit k starts at
.equ B2, (2 * TRI + 2) // 3 - B1            ;   ceil(k * TRI / 3)
.equ B3, TRI - B1 - B2
.equ T0H, (CLK_KHZ * 625 + 500000) // 1000000      ; 0.625 us
.equ T1H, (CLK_KHZ * 1250 + 500000) // 1000000     ; 1.25 us
.equ LEAD, 40                               ; RDTIME to the first EMIT: 8 instructions
.equ VB, TB                                 ; v of motors 0-3
.equ PTR, TB + 4                            ; address of the buffer being played
.equ BUF0, TB + 5                           ; two buffers of six lane words
.equ BUF1, TB + 11

.entry OUT, out
.entry FEED, feed
.own   0, OUT
.own   1, OUT
.own   2, OUT
.own   3, OUT
.tape  PTR, BUF0
.tape  BUF0, 0xFFF * BIDIR, 0xFFF * BIDIR, 0xFFF * BIDIR, 0xFFF * BIDIR, 0, 0   ; v = 0 (line 0xFFF0 if BIDIR)

; ---- OUT: one frame every FRAME ticks, all four motors at once.
out:
    ADDT  20
    EMIT  M, IDLE, REST
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1
frame:
    MOVI  r3, 0
    LDT   r0, r3, PTR        ; the buffer to play
    LDLC  5
grp:                         ; three bits, 125 ticks
    LDT   r2, r0, 0
    ADDI  r0, 1
    EMIT  M, ACT, M
    ADDT  T0H
    EMITR M, r2, M           ; a 1 stays active, a 0 returns to idle, per motor
    SHR   r2, 4
    ADDT  T1H - T0H
    EMIT  M, IDLE, M
    ADDT  B1 - T1H
    EMIT  M, ACT, M
    ADDT  T0H
    EMITR M, r2, M
    SHR   r2, 4
    ADDT  T1H - T0H
    EMIT  M, IDLE, M
    ADDT  B2 - T1H
    EMIT  M, ACT, M
    ADDT  T0H
    EMITR M, r2, M
    ADDT  T1H - T0H
    EMIT  M, IDLE, M
    ADDT  B3 - T1H
    DJNZ  grp
    LDT   r2, r0, 0          ; bit 15
    EMIT  M, ACT, M
    ADDT  T0H
    EMITR M, r2, M
    ADDT  T1H - T0H
    EMIT  M, IDLE, REST      ; (bidirectional: released for the ESCs' replies)
    ADDT  FRAME - 5 * TRI - T1H   ; the next frame, FRAME after this one
    JMP   frame

; ---- FEED: host values into the inactive buffer, then switch.
feed:
    POP   r0
    MOV   r1, r0
    SHR   r1, 12
    MOVI  r2, 0xFFF
    AND   r0, r2             ; v
    MOVI  r3, 0
    TSTI  r1, 4
    JNZ   all
    MOVI  r2, 3
    AND   r1, r2             ; motor
    STT   r0, r1, VB
    JMP   more
all:
    STT   r0, r3, VB
    STT   r0, r3, VB + 1
    STT   r0, r3, VB + 2
    STT   r0, r3, VB + 3
more:
    RXREADY
    JNZ   feed               ; another word waiting: take it first
    LDT   r1, r3, PTR
    MOVI  r2, BUF0 + BUF1
    SUB   r2, r1             ; the inactive buffer
    STT   r3, r2, 0
    STT   r3, r2, 1
    STT   r3, r2, 2
    STT   r3, r2, 3
    STT   r3, r2, 4
    STT   r3, r2, 5
    LDT   r0, r3, VB + 3     ; motors 3 to 0: each shifts the lanes left by one
    CALL  lanes
    LDT   r0, r0, VB + 2     ; (lanes returns r0 = 0)
    CALL  lanes
    LDT   r0, r0, VB + 1
    CALL  lanes
    LDT   r0, r0, VB
    CALL  lanes
    LDT   r1, r0, PTR        ; switch
    MOVI  r2, BUF0 + BUF1
    SUB   r2, r1
    STT   r2, r0, PTR
    RDTIME r1                ; one frame period: no frame still reads the old buffer
    ADDI  r1, FRAME
    SETT  r1
    WAITT
    JMP   feed

lanes:                       ; r0 = v: shift its frame into the inactive buffer's lanes
    MOV   r2, r0
    SHR   r2, 4
    XOR   r2, r0
    MOV   r3, r0
    SHR   r3, 8
    XOR   r2, r3
    MOVI  r3, 0xF
    AND   r2, r3             ; CRC
    SHL   r0, 4
    OR    r0, r2
    MOVI  r3, FLIP
    XOR   r0, r3
    SHL   r0, 16             ; the frame as line bits, MSB at bit 31
    MOVI  r3, 0
    LDT   r2, r3, PTR
    MOVI  r1, BUF0 + BUF1
    SUB   r1, r2
    LDLC  6
lane:                        ; three bits into nibbles 0, 1, 2 of word g
    MOVI  r2, 0
    SHL   r0, 1
    JNC   l1
    ADDI  r2, 0x1
l1: SHL   r0, 1
    JNC   l2
    ADDI  r2, 0x10
l2: SHL   r0, 1
    JNC   l3
    ADDI  r2, 0x100
l3: LDT   r3, r1, 0
    SHL   r3, 1
    OR    r3, r2
    STT   r3, r1, 0
    ADDI  r1, 1
    DJNZ  lane
    RET
