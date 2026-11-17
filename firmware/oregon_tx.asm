; Oregon Scientific v2.1 weather sensor: a THGR122NX temperature and humidity sensor, as it
; sends its reading to a weather station over 433.92 MHz. OOK: pin 0 keys an RF transmitter
; module (high = carrier on).
;
; (Oregon Scientific RF Protocol Description, versions 1.0, 2.1 and 3.0, June 2011.)
; Manchester at 1024 bit/s, "normal" polarity: a bit's value is the RF state before the
; transition in its middle, so a 1 is on-to-off and a 0 off-to-on. Version 2.1 sends each
; data bit twice, inverted first (a 1 as "01", a 0 as "10"), and the whole message twice,
; 10.9 ms apart. Nibbles go least significant bit first. The message: a preamble of 16
; ones (nibbles FFFF), the sync nibble A, then the payload: sensor ID 1 D 2 0, channel
; (1 << (ch - 1)), rolling code (2 nibbles), flags (4: battery low), temperature in BCD
; tenths first (3 nibbles), sign (non-zero below zero), humidity in BCD units first (2
; nibbles), one nibble of 0, the checksum (the 8-bit sum of payload nibbles 0 to 14, low
; nibble first) and two post-amble nibbles of 0. That is 96 bits, three words: W0 is
; constant, W1 and W2 are built for each reading.
;
; Host: one word per reading, [11:0] the temperature's three BCD digits (0x235 = 23.5),
; [12] = 1 below zero, [23:16] the humidity's two BCD digits (0x47 = 47 %), [24] = 1 for
; a low battery. The chip builds the message, sums the checksum, sends the message twice
; and PUSHes 1.
;
; One data bit is four half periods of 488.28 us, each a DRIVE event: for a 1 off, on, on,
; off, for a 0 on, off, off, on.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default CH, 1                              ; channel 1 to 3
.default RC, 0xBB                           ; rolling code
.equ RF, 1 << PIN
.equ H, (CLK_KHZ * 1000 + 1024) // 2048     ; half a transmitted bit: 1/2048 s
.equ H2, 2 * H
.equ GAP, CLK_KHZ * 109 // 10               ; 10.9 ms between the two copies
.equ LEAD, 64                               ; RDTIME to the first EMIT: eight instructions
.default TB, 0                              ; tape: W0, W1, W2
.equ W1BASE, (1 << (CH - 1)) << 4 | (RC & 0xF) << 8 | (RC >> 4) << 12   ; 0, ch, rolling code
.equ ID3, 0x1 + 0xD + 0x2                   ; the ID's nibbles in W0, for the checksum

.entry CTX, start
.own   PIN, CTX
.tape  TB, 0x2D1AFFFF                       ; F F F F A 1 D 2, first nibble in bits 3:0

start:
    ADDT  16
    EMIT  RF, 0, RF                         ; carrier off
reading:
    POP   r0
    ; ---- W1 = 0, ch, rolling code, flags, temperature: (host & 0xFFF) << 20 | flags << 16
    MOV   r1, r0
    SHL   r1, 20                            ; the temperature's digits to bits 31..20
    MOVI  r3, W1BASE
    OR    r1, r3
    MOV   r3, r0
    SHR   r3, 24
    TSTI  r3, 1
    JZ    batt_ok
    MOVI  r3, 0
    MOVHI r3, 4                             ; flags = 4 (bit 18): battery low
    OR    r1, r3
batt_ok:
    MOVI  r3, 0
    STT   r1, r3, TB + 1
    ; ---- W2 = sign, humidity, 0, then the checksum in bits 23..16
    MOV   r2, r0
    SHR   r2, 16
    MOVI  r3, 0xFF
    AND   r2, r3                            ; the humidity's digits
    SHL   r2, 4
    MOV   r3, r0
    SHR   r3, 12
    TSTI  r3, 1
    JZ    plus
    ADDI  r2, 8                             ; the sign nibble
plus:
    MOVI  r3, 0
    STT   r2, r3, TB + 2
    ; ---- checksum: the ID's first three nibbles, W1's eight and W2's low four
    MOV   r0, r1                            ; W1
    MOVI  r1, 0xF
    MOVI  r2, ID3
    LDLC  8
cs1:
    MOV   r3, r0
    AND   r3, r1
    ADD   r2, r3
    SHR   r0, 4
    DJNZ  cs1
    MOVI  r3, 0
    LDT   r0, r3, TB + 2
    LDLC  4
cs2:
    MOV   r3, r0
    AND   r3, r1
    ADD   r2, r3
    SHR   r0, 4
    DJNZ  cs2
    MOVI  r1, 0xFF
    AND   r2, r1
    SHL   r2, 16                            ; low nibble then high nibble: bits 19..16, 23..20
    MOVI  r3, 0
    LDT   r0, r3, TB + 2
    OR    r0, r2
    STT   r0, r3, TB + 2
    ; ---- the message twice
    MOVI  r1, 2                             ; copies to send
    RDTIME r3
    ADDI  r3, LEAD
    SETT  r3
copy:
    MOVI  r2, 0
word:
    LDT   r0, r2, TB
    LDLC  16                                ; two data bits a pass
pair:
    SHR   r0, 1
    JC    a_one
    EMIT  RF, RF, RF                        ; 0: on, off, off, on
    ADDT  H
    EMIT  RF, 0, RF
    ADDT  H2
    EMIT  RF, RF, RF
    ADDT  H
    JMP   b_bit
a_one:
    EMIT  RF, 0, RF                         ; 1: off, on, on, off
    ADDT  H
    EMIT  RF, RF, RF
    ADDT  H2
    EMIT  RF, 0, RF
    ADDT  H
b_bit:
    SHR   r0, 1
    JC    b_one
    EMIT  RF, RF, RF
    ADDT  H
    EMIT  RF, 0, RF
    ADDT  H2
    EMIT  RF, RF, RF
    ADDT  H
    JMP   b_done
b_one:
    EMIT  RF, 0, RF
    ADDT  H
    EMIT  RF, RF, RF
    ADDT  H2
    EMIT  RF, 0, RF
    ADDT  H
b_done:
    DJNZ  pair
    ADDI  r2, 1
    CMPI  r2, 3
    JNZ   word
    EMIT  RF, 0, RF                         ; carrier off after the message
    ADDT  GAP
    MOVI  r3, 1
    SUB   r1, r3
    JNZ   copy
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   reading
