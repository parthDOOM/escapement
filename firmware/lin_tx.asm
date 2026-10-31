; LIN commander (v1.6): frames whose data the commander publishes, for a LIN transceiver's
; TXD: a break (13 bit times low) and delimiter, the sync byte 0x55, the protected
; identifier (the 6-bit ID and its parity bits P0, P1), 1 to 8 data bytes and the checksum,
; each byte 8N1 LSB first at 19200 baud. The checksum is LIN 2.x's enhanced one (the
; inverted eight-bit sum with carry over the protected identifier and the data), or the
; classic one (the data only) for the diagnostic IDs 0x3C and 0x3D, as LIN 2.x requires.
; Every edge is a DRIVE event at an absolute deadline.
;
; Host (POP): ID | count << 8 (ID 0..63, count 1..8), then two words of data bytes, byte 0
; in [7:0] (bytes past the count are ignored). Host (PUSH): 1 once the frame is out.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default BAUD, 19200
.default TB, 0xF0                           ; tape: the frame's bytes (11 words), scratch
.equ BIT, (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.equ BREAK, (13 * CLK_KHZ * 1000 + BAUD - 1) // BAUD   ; at least 13 nominal bit times
.equ TX, 1 << PIN
.equ BUF, TB                                ; PID, data 1..8, checksum
.equ SCR, TB + 12                           ; count; first byte the checksum covers
.equ LEAD, 64

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  16
    EMIT  TX, TX, TX         ; recessive (high)
frame:
    POP   r0                 ; ID | count << 8
    MOV   r1, r0
    SHR   r1, 8
    MOVI  r3, 0
    STT   r1, r3, SCR        ; count
    SHL   r0, 26
    SHR   r0, 26             ; ID
    MOV   r2, r0             ; the diagnostic IDs 0x3C, 0x3D: classic checksum, from byte 1
    SHR   r2, 1
    MOVI  r1, 0
    CMPI  r2, 0x1E
    JNZ   enh
    MOVI  r1, 1
enh:
    STT   r1, r3, SCR + 1
    MOV   r2, r0             ; P0 = ID0 ^ ID1 ^ ID2 ^ ID4, in bit 0 of r2
    MOV   r3, r0
    SHR   r3, 1
    XOR   r2, r3
    SHR   r3, 1
    XOR   r2, r3
    SHR   r3, 2
    XOR   r2, r3
    MOV   r1, r0             ; ID1 ^ ID3 ^ ID4 ^ ID5, in bit 0 of r1
    SHR   r1, 1
    MOV   r3, r0
    SHR   r3, 3
    XOR   r1, r3
    SHR   r3, 1
    XOR   r1, r3
    SHR   r3, 1
    XOR   r1, r3
    SHL   r2, 31
    SHR   r2, 25
    OR    r0, r2             ; P0 in bit 6
    SHL   r1, 31
    SHR   r1, 24
    MOVI  r2, 0x80
    XOR   r1, r2
    OR    r0, r1             ; P1 = not that, in bit 7
    MOVI  r1, 0
    STT   r0, r1, BUF        ; the protected identifier
    POP   r0                 ; data bytes 0..3, then 4..7, one per tape word
    MOVI  r1, 1
split:
    CMPI  r1, 5
    JNZ   split1
    POP   r0
split1:
    MOV   r3, r0
    SHL   r3, 24
    SHR   r3, 24
    STT   r3, r1, BUF
    SHR   r0, 8
    ADDI  r1, 1
    CMPI  r1, 9
    JNZ   split
    MOVI  r3, 0              ; the checksum over bytes first..count
    LDT   r0, r3, SCR
    LDT   r1, r3, SCR + 1
    ADDI  r0, 1              ; r0: the checksum's place, after the data
    MOVI  r2, 0
sum:
    LDT   r3, r1, BUF
    ADD   r2, r3
    MOV   r3, r2             ; with carry: fold bit 8 back in
    SHR   r3, 8
    SHL   r2, 24
    SHR   r2, 24
    ADD   r2, r3
    ADDI  r1, 1
    CMP   r1, r0
    JNZ   sum
    MOVI  r3, 0xFF
    XOR   r2, r3
    STT   r2, r0, BUF
    ADDI  r0, 1              ; bytes to send after the sync: PID, data, checksum
    RDTIME r3
    SETT  r3
    ADDT  LEAD
    EMIT  TX, 0, TX          ; the break
    ADDT  BREAK
    EMIT  TX, TX, TX         ; its delimiter
    ADDT  BIT
    MOVI  r3, 0x55
    CALL  byte               ; sync
    MOVI  r1, 0
send:
    LDT   r3, r1, BUF
    CALL  byte
    ADDI  r1, 1
    CMP   r1, r0
    JNZ   send
    WAITT                    ; the last stop bit has ended
    MOVI  r1, 1
    PUSH  r1
    JMP   frame

byte:                        ; r3[7:0], 8N1, LSB first, from tmr
    LDSH  r3
    EMIT  TX, 0, TX          ; start bit
    ADDT  BIT
    LDLC  8
bit:
    EMITX TX, LSB, TX
    ADDT  BIT
    DJNZ  bit
    EMIT  TX, TX, TX         ; stop bit
    ADDT  BIT
    RET
