; BiSS C master, point to point: reads one sensor's process data (one data channel with its
; 6-bit CRC) on each host request. Held-out round, written 2026-11-24 on v3.1 (cb47436), from
; iC-Haus "BiSS C Protocol Description" Rev D2 and "AN3: BiSS CRC polynomials and
; calculation" Rev E3.
;
;   pin MA_PIN (0)   out: MA, the clock to the slave (an RS-422 driver after it), idle high
;   pin SL_PIN (1)   in:  SL, the slave's data (from an RS-422 receiver), idle high
; Context CTX (0). Clock CLK_KHZ (25 MHz by default). MA at MA_KHZ, at most CLK_KHZ / 72 (each
; header clock is a look at SL and 8 instructions: 347 kHz at 25 MHz, 694 kHz at 50 MHz);
; 250 kHz by default, in the range every slave supports (CHARACTERISTICS No. 2; No. 1 allows
; 80 kHz to 10 MHz); duty 50 % (No. 3, 4).
;
; A frame (BiSS FRAME, Figures 6, 7, 13):
; - MA falls, then rises (LAT: the slave latches its data, Latch Point).
; - Header: MA keeps clocking; SL is sampled just before each rising edge. The first 0 is the
;   acknowledge bit (ACK). Further clocks before it are the line delay (Line Delay: whole
;   clocks; No. 7 allows 40 us); more than ACK_MAX clocks without it: no slave. Then the
;   start bit (1) may be delayed by the slave's processing time (No. 10a/10b, at most 40 us
;   plus 8 clocks): up to STR_MAX clocks.
; - CDS, then DLEN data bits, MSB first, then the 6-bit CRC, inverted (AN3: polynomial 0x43,
;   start value 0, over every data bit, MSB first).
; - One more rising edge (STP): the slave's stop bit (0). MA then stays high, so the slave
;   reads CDM = 0 (no control communication: the CDM bit is sent inverted, CONTROL
;   COMMUNICATION), and the master waits for SL to rise at the end of the slave's timeout
;   (Timeout, No. 5/6) before the frame counts as done.
; Every MA edge and SL sample is a timed event: MA never pauses within a frame (a pause
; longer than the slave's timeout would end the frame). The next clock is always queued
; before the master looks at a sample.
; Not done: control communication (register access, commands), multi-slave chains, data
; channels longer than 31 bits, CRCs other than 6 bits.
;
; Host (mailbox, context CTX), per request (any word), two words back:
;   status: [0] CRC good, [1] no ACK, [2] no start bit, [3] no timeout end, [4] the CDS
;           bit, [5] no stop bit, [15:8] the clocks before ACK (line delay)
;   data:   the DLEN data bits (DMSB at bit DLEN-1), 0 when there was no frame

.default CTX, 0
.default CLK_KHZ, 25000
.default MA_KHZ, 250
.default DLEN, 26                  ; e.g. 24-bit position, error and warning (BP3)
.default MA_PIN, 0
.default SL_PIN, 1
.equ MA, 1 << MA_PIN
.equ HT, (CLK_KHZ + 2 * MA_KHZ - 1) // (2 * MA_KHZ)   ; half a clock period, rounded up
.equ T, 2 * HT
.assert 80 <= MA_KHZ <= 10000, No. 1: 80 kHz to 10 MHz
; a header clock is a look at SL and 8 instructions, so MA_KHZ is at most CLK_KHZ / 72
.assert HT >= 36, a half period of 36 ticks or more
.assert 1 <= DLEN <= 31, one data word of 1 to 31 bits
.equ US, CLK_KHZ // 1000
.equ ACK_MAX, (40 * US + T - 1) // T + 3             ; No. 7: line delay up to 40 us
.equ STR_MAX, (40 * US + T - 1) // T + 8 + 3         ; No. 10a, 10b
.equ TO_WAIT, 100 * US                               ; the slave's timeout: up to 40 us
.equ BRK, 41 * US                                    ; the break after an error frame
.equ NA, DLEN + 1                                    ; CDS and the data
.equ NB, 6                                           ; the CRC

.entry CTX, main
.own MA_PIN, CTX

main:
    ADDT  40
    EMIT  MA, MA, MA           ; MA idle high
    ADDT  50 * US
    WAITT                      ; 40 us with MA idle before the first frame (Bus Reset)
loop:
    POP   r0
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    EMIT  MA, 0, MA            ; MA falls: a frame starts
    ADDT  HT
    EMIT  MA, MA, MA           ; LAT
    MOVI  r2, 0                ; header clocks (counted loops: escert unrolls them exactly)
    LDLC  ACK_MAX
ack:                           ; tmr = the last rising edge R
    ADDT  HT
    EMIT  MA, 0, MA            ; R + T/2
    ADDT  HT - 2
    SAMPLE SL_PIN, MSB         ; R + T - 2: SL as set by the slave after R
    ADDT  2
    EMIT  MA, MA, MA           ; R + T: the next rising edge (queued before the look)
    WAITT                      ; (at R + T the sample has been taken: GETI does not wait;
    GETI  r3                   ; escert loses 2 ticks a clock on a GETI wait, not on WAITT)
    TSTI  r3, 1
    JZ    got_ack
    ADDI  r2, 1
    DJNZ  ack
    MOVI  r0, 2                ; no ACK
    JMP   abort
got_ack:
    MOV   r0, r2
    SHL   r0, 8                ; status: the line delay in clocks
    LDLC  STR_MAX
str:
    ADDT  HT
    EMIT  MA, 0, MA
    ADDT  HT - 2
    SAMPLE SL_PIN, MSB
    ADDT  2
    EMIT  MA, MA, MA
    WAITT
    GETI  r3
    TSTI  r3, 1
    JNZ   got_str              ; the start bit
    DJNZ  str
    ADDI  r0, 4                ; no start bit
    JMP   abort
got_str:
    LDLC  NA                   ; CDS and the data
da:
    ADDT  HT
    EMIT  MA, 0, MA
    ADDT  HT - 2
    SAMPLE SL_PIN, MSB
    ADDT  2
    EMIT  MA, MA, MA
    DJNZ  da
    GETI  r1                   ; [DLEN] CDS, [DLEN-1:0] the data
    LDLC  NB                   ; the CRC; the last rising edge queued is STP
db:
    ADDT  HT
    EMIT  MA, 0, MA
    ADDT  HT - 2
    SAMPLE SL_PIN, MSB
    ADDT  2
    EMIT  MA, MA, MA
    DJNZ  db
    ADDT  T - 2                ; just before where a next rising edge would be: SL shows
    SAMPLE SL_PIN, MSB         ; the stop bit (MA stays high: CDM = 0)
    GETI  r2                   ; [6:1] the CRC as sent (inverted), [0] the stop bit
    ADDT  2
    WAITT
    ADDT  TO_WAIT
    WAITP 1 << SL_PIN, 1 << SL_PIN   ; the slave's timeout ends: SL rises
    JZ    to_ok
    ADDI  r0, 8                ; no timeout end
to_ok:
    TSTI  r2, 1
    JZ    stp_ok
    ADDI  r0, 32               ; no stop bit: SL was not 0 after STP
stp_ok:
    MOV   r3, r1
    SHR   r3, DLEN
    SHL   r3, 4
    OR    r0, r3               ; status bit 4: CDS
    SHL   r1, 32 - DLEN
    SHR   r1, 32 - DLEN        ; the data
    SHR   r2, 1                ; the CRC as sent
    LDSH  r0                   ; (sh is free: keep the status there)
    ; CRC over the data bits, MSB first: crc = ((crc << 1) ^ (bit ^ crc5 ? 0x03 : 0)) & 0x3F
    MOV   r0, r1
    SHL   r0, 32 - DLEN        ; the data, left-aligned
    MOVI  r3, 0
    STT   r1, r3, DATAW
    STT   r2, r3, CRCW
    MOVI  r1, 0                ; crc
    LDLC  DLEN
cb:
    MOV   r3, r0
    SHR   r3, 31               ; the bit
    SHL   r0, 1
    MOV   r2, r1
    SHR   r2, 5
    XOR   r3, r2               ; feedback
    SHL   r1, 27
    SHR   r1, 26               ; (crc << 1) & 0x3F
    CMPI  r3, 0
    JZ    cb1
    MOVI  r2, 0x03
    XOR   r1, r2
cb1:
    DJNZ  cb
    MOVI  r3, 0x3F
    XOR   r1, r3               ; inverted, as sent
    MOVI  r3, 0
    LDT   r2, r3, CRCW
    STSH  r0                   ; the status
    CMP   r1, r2
    JZ    crc_ok
    RDTIME r1                  ; an error: 40 us with MA high before the next frame (Bus
    ADDI  r1, BRK              ; Reset/Initialization, Application Hints step 5), counted
    SETT  r1                   ; from now, after the timeout's end
    WAITT
    JMP   reply
crc_ok:
    ADDI  r0, 1                ; CRC good
reply:
    PUSH  r0
    LDT   r1, r3, DATAW
    PUSH  r1
    JMP   loop
abort:                         ; no frame: stop clocking, MA high; wait out the timeout
    ADDT  TO_WAIT
    WAITT
    PUSH  r0
    MOVI  r1, 0
    PUSH  r1
    JMP   loop

.equ DATAW, 0x00               ; tape scratch
.equ CRCW, 0x01
