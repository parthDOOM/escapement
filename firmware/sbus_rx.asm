; S.BUS receiver (v1.6): the 16 proportional channels, two digital channels and the
; frame-lost and failsafe flags that an RC receiver sends a flight controller, decoded
; into the tape for the host to read at any time.
;
; Line (Futaba S.BUS, as the open implementations decode it, e.g. bolderflight/sbus):
; 100000 baud, 8 data bits LSB first, even parity, two stop bits, inverted (idle low, a
; start bit high; INV 0 for a line already inverted back). A frame is 25 bytes: the
; header 0x0F, 16 channels of 11 bits packed LSB first from byte 1 (channel 0 in byte 1
; and the low three bits of byte 2, ...), the flags byte (bit 0 channel 17, bit 1 channel
; 18, bit 2 frame lost, bit 3 failsafe) and the footer 0x00 (S.BUS2: low nibble 0x4).
; Frames come every 7 to 20 ms; a frame ends after GAPC character times of silence.
; At 25 MHz a frame is published 0.80 ms after its last stop bit (0.36 ms of silence,
; then 0.43 ms to check and unpack it), and the next may start from then on: S.BUS
; leaves 4 ms or more between frames.
;
; A frame with a parity or stop-bit error, other than 25 bytes, or a wrong header or
; footer is dropped and counted (ERRS). A good one is published under a sequence count:
; SEQ is odd while the channels are being written, so a host reads SEQ, the channels and
; SEQ again, and keeps the channels if both reads are the same even number.
;
; Tape: CH + k channel k (0..15, 11 bits), FLAGS the flags byte, SEQ, ERRS.
; Host (PUSH): frames << 0 | flags << 16 after each good frame, if the mailbox has room.

.default CTX, 0
.default PIN, 0
.default INV, 1                             ; 1: the raw S.BUS line (idle low)
.default CLK_KHZ, 25000
.default BAUD, 100000
.default GAPC, 3                            ; silence that ends a frame, in characters
.default TB, 0xC0                           ; tape: 20 words of results, 32 of frame
.assert 0 <= INV <= 1, INV is 0 or 1
.equ BIT, (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.assert BIT >= 40, a character's processing needs a stop bit and a half of 60 ticks
.equ FIRST, (3 * BIT) // 2 - 8              ; data bit 0's centre from the RDTIME after WAITP
.equ GAP, (3 * BIT) // 2 + GAPC * 12 * BIT  ; from the first stop bit's centre
.equ IN, 1 << PIN
.equ START, IN * INV                        ; the start bit's level
.equ STOPX, IN * (1 - INV)                  ; the stop bit's level
.equ DINV, 0x1FF * INV                      ; received data and parity, inverted back
.equ CH, TB
.equ FLAGS, TB + 16
.equ SEQ, TB + 17
.equ ERRS, TB + 18
.equ BUF, TB + 0x20                         ; one byte per word, | 0x100
.equ MAXLEN, 26

.entry CTX, main

main:
    MOVI  r0, 0
    STT   r0, r0, SEQ
    STT   r0, r0, ERRS
idle:
    MOVI  r1, 0              ; bytes received
    RDTIME r3
    SETT  r3
    ADDT  0xFFFFFF
    WAITP IN, START
    JNZ   idle
rx_byte:                     ; 8 data bits and parity at their centres, the stop bit raw
    RDTIME r3
    ADDI  r3, FIRST
    SETT  r3
    LDLC  9
rbit:
    SAMPLE PIN, LSB
    ADDT  BIT
    DJNZ  rbit
    WAITT                    ; the first stop bit's centre
    GETP  r2
    GETI  r0                 ; the 9 samples in [31:23]
    OR    r0, r2
    STT   r0, r1, BUF
    CMPI  r1, MAXLEN
    JZ    rfull
    ADDI  r1, 1
rfull:
    ADDT  GAP
    WAITP IN, START
    JZ    rx_byte

    CMPI  r1, 25             ; the frame has ended: 25 bytes
    JNZ   drop
    MOVI  r0, 0
check:                       ; stop bits and parity; each word becomes its byte | 0x100
    LDT   r2, r0, BUF
    MOVI  r3, STOPX
    XOR   r3, r2
    TSTI  r3, IN
    JNZ   drop
    SHR   r2, 23
    MOVI  r3, DINV
    XOR   r2, r3
    MOVI  r3, 0xFF
    AND   r3, r2
    ADDI  r3, 0x100
    STT   r3, r0, BUF
    MOVI  r3, 0              ; even: the 1 bits of data and parity
    LDLC  9
par:
    SHR   r2, 1
    JNC   par1
    ADDI  r3, 1
par1:
    DJNZ  par
    TSTI  r3, 1
    JNZ   drop
    ADDI  r0, 1
    CMPI  r0, 25
    JNZ   check
    MOVI  r0, 0
    LDT   r2, r0, BUF        ; the header
    CMPI  r2, 0x10F
    JNZ   drop
    LDT   r2, r0, BUF + 24   ; the footer: 0x00, or 0x?4
    CMPI  r2, 0x100
    JZ    good
    MOVI  r3, 0x0F
    AND   r2, r3
    CMPI  r2, 0x04
    JNZ   drop
good:
    LDT   r2, r0, SEQ        ; odd: writing
    ADDI  r2, 1
    STT   r2, r0, SEQ
    MOVI  r0, 1              ; the channels, 11 bits each from byte 1's bit 0
    LDT   r1, r0, BUF        ; r1: the byte's bits left, above them the 0x100 marker
    MOVI  r3, 0
chan:
    MOVI  r2, 0
    LDLC  11
cbit:
    SHR   r2, 1              ; built from bit 10 down
    SHR   r1, 1
    JNC   cbit1
    ADDI  r2, 0x400
cbit1:
    CMPI  r1, 1              ; only the marker left: the next byte
    JNZ   cbit2
    ADDI  r0, 1
    LDT   r1, r0, BUF
cbit2:
    DJNZ  cbit
    STT   r2, r3, CH
    ADDI  r3, 1
    CMPI  r3, 16
    JNZ   chan
    MOVI  r0, 0
    LDT   r2, r0, BUF + 23   ; the flags
    MOVI  r3, 0xFF
    AND   r2, r3
    STT   r2, r0, FLAGS
    LDT   r3, r0, SEQ        ; even: done
    ADDI  r3, 1
    STT   r3, r0, SEQ
    TXREADY                  ; Z: the mailbox is full
    JZ    idle
    SHR   r3, 1              ; frames | flags << 16
    SHL   r3, 16
    SHR   r3, 16
    SHL   r2, 16
    OR    r3, r2
    PUSH  r3
    JMP   idle
drop:
    MOVI  r0, 0
    LDT   r2, r0, ERRS
    ADDI  r2, 1
    STT   r2, r0, ERRS
    JMP   idle
