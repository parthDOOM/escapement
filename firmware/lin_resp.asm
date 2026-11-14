; LIN responder (LIN 2.x): the node that answers a commander's header on the single-wire
; bus, open-drain on pin PIN (recessive high, dominant low), 8N1 LSB first at BAUD.
;
; Header: a break (dominant for at least 13 nominal bits; a responder takes one of at least
; 11), a delimiter, the sync byte 0x55 and the protected identifier, ID[5:0] with parity
; P0 = ID0^ID1^ID2^ID4 and P1 = not(ID1^ID3^ID4^ID5) in bits 6 and 7. For an ID the host
; has published, the response follows one bit after the identifier's stop bit: the data
; bytes, then the checksum, the inverted eight-bit sum with carry over the protected
; identifier and the data (LIN 2.x enhanced), or over the data only for the diagnostic IDs
; 0x3C and 0x3D (classic). The identifier byte must equal the stored one, parity included,
; so a header with a parity error is not answered. Any other header leaves the bus alone.
;
; Host to chip (POP), [31:30] the command, [13:12] a slot (0-3) where it names one:
;   01: publish ID [5:0] from slot, count [11:8] (1-8 data bytes); the chip computes the
;       protected identifier. Not answered until 11.
;   10: data byte [7:0] at index [10:8] of slot.
;   11: enable slot: the chip computes the checksum and answers its ID from now on.
;   00: silence ID [5:0].
; Chip to host (PUSH, when the mailbox has room): each header seen, the protected
; identifier in [7:0], [8] set when this node answered it.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default BAUD, 19200
.default TB, 0x80                           ; tape: LUT (64 words), 4 slots of 12
.equ BIT, (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.equ D, 1 << PIN
.equ LUT, TB                                ; LUT[ID]: the slot's address, or 0
.equ SL, TB + 64                            ; slot k at SL + 12k: PID, count, data..., checksum
.equ RS, BIT                                ; response space: one bit after the stop bit
.assert TB + 64 + 48 <= 256, "the tape table does not fit"
.assert BIT >= 64, "a bit must leave the receive loop time"

.entry CTX, main
.own   PIN, CTX
.od    PIN

main:
    ADDT  20
    EMIT  D, D, D            ; released
    MOVI  r0, 0
    MOVI  r3, 0
clr:                         ; the tape is not cleared at power-up: no ID answered yet
    STT   r0, r3, LUT
    ADDI  r3, 1
    CMPI  r3, 64
    JNZ   clr
idle:                        ; drain, then wait for the bus to be recessive
    WAITT
    RDTIME r1
    SETT  r1
    ADDT  0xFFFFFF
    WAITP D, D
    JNZ   idle
look:
    RDTIME r1
    SETT  r1
    ADDT  CLK_KHZ            ; a falling edge, or look at the host every 1 ms
    WAITP D, 0
    JZ    fall
    RXREADY
    JZ    look
    POP   r0                 ; (RXREADY saw a word: no wait)
    JMP   cfg

; ---- a falling edge: a break if the bus stays dominant for 11 bit times
fall:
    RDTIME r1                ; the edge was about 12 ticks earlier
    SETT  r1
    ADDT  11 * BIT - 12
    WAITP D, D
    JZ    idle               ; rose before 11 bits: not a break
    ADDT  0xFFFFFF
    WAITP D, D               ; the break's end (the delimiter starts)
    JNZ   idle
    RDTIME r1
    SETT  r1
    ADDT  14 * BIT
    WAITP D, 0               ; the sync byte's start bit
    JNZ   idle
    CALL  rxbyte
    JZ    idle               ; framing error
    CMPI  r0, 0x55
    JNZ   idle
    ADDT  4 * BIT
    WAITP D, 0               ; the identifier's start bit
    JNZ   idle
    CALL  rxbyte
    JZ    idle
    MOV   r2, r0             ; r0 = the protected identifier
    SHL   r2, 26
    SHR   r2, 26             ; ID
    LDT   r3, r2, LUT
    CMPI  r3, 0
    JZ    report             ; not published
    LDT   r1, r3, 0
    CMP   r1, r0
    JNZ   report             ; a parity error: not answered
    LDT   r1, r3, 1
    ADDI  r1, 1              ; data bytes and the checksum
    ADDI  r3, 2
    ADDT  BIT // 2 + RS      ; tmr was the stop bit's centre
txb:
    LDT   r2, r3, 0
    SHL   r2, 1              ; start bit 0 in bit 0, the data in bits 8:1
    ADDI  r2, 0x200          ; stop bit 1 in bit 9
    LDSH  r2
    LDLC  10
txbit:
    EMITX D, LSB, D          ; a 1 releases the bus, a 0 pulls it low
    ADDT  BIT
    DJNZ  txbit
    ADDI  r3, 1
    MOVI  r2, 1
    SUB   r1, r2
    JNZ   txb
    WAITT                    ; the last stop bit has ended
    MOVI  r1, 0x100
    OR    r0, r1             ; [8]: answered
report:
    TXREADY
    JZ    idle               ; no room: the report is dropped, the bus is not
    PUSH  r0
    JMP   idle

; ---- one byte from a falling edge seen by the WAITP three instructions back: r0 = the
; byte, Z = 1 if the stop bit was dominant (a framing error); tmr = the stop bit's centre
rxbyte:
    RDTIME r1                ; edge about 16 ticks earlier (WAITP's lag + 3 slots)
    ADDI  r1, (3 * BIT) // 2 - 16
    SETT  r1                 ; tmr = the centre of data bit 0
    LDLC  8
rbit:
    SAMPLE PIN, LSB
    ADDT  BIT
    DJNZ  rbit
    WAITT                    ; the stop bit's centre
    GETP  r2
    GETI  r0
    SHR   r0, 24             ; the 8 samples sit in ish[31:24]
    TSTI  r2, D
    RET

; ---- host words
cfg:
    MOV   r1, r0
    SHR   r1, 30
    CMPI  r1, 1
    JZ    c_pub
    CMPI  r1, 2
    JZ    c_dat
    CMPI  r1, 3
    JZ    c_en
    SHL   r0, 26             ; 00: silence ID
    SHR   r0, 26
    MOVI  r1, 0
    STT   r1, r0, LUT
    JMP   look
c_pub:                       ; 01: ID [5:0], count [11:8], slot [13:12]
    CALL  slotk
    MOV   r1, r0
    SHL   r1, 20
    SHR   r1, 28
    STT   r1, r2, SL + 1     ; count
    MOV   r1, r0
    SHL   r1, 26
    SHR   r1, 26             ; ID
    MOV   r0, r1             ; P0 = ID0 ^ ID1 ^ ID2 ^ ID4, in bit 0 of r0
    MOV   r3, r1
    SHR   r3, 1
    XOR   r0, r3
    SHR   r3, 1
    XOR   r0, r3
    SHR   r3, 2
    XOR   r0, r3
    MOVI  r3, 1
    AND   r0, r3
    SHL   r0, 6
    STT   r0, r2, SL         ; P0 << 6, for now
    MOV   r0, r1             ; P1 = not(ID1 ^ ID3 ^ ID4 ^ ID5), in bit 0 of r0
    SHR   r0, 1
    MOV   r3, r1
    SHR   r3, 3
    XOR   r0, r3
    SHR   r3, 1
    XOR   r0, r3
    SHR   r3, 1
    XOR   r0, r3
    MOVI  r3, 1
    XOR   r0, r3
    AND   r0, r3
    SHL   r0, 7
    LDT   r3, r2, SL
    OR    r0, r3
    OR    r0, r1             ; the protected identifier
    STT   r0, r2, SL
    JMP   look
c_dat:                       ; 10: byte [7:0] at index [10:8] of slot [13:12]
    CALL  slotk
    MOV   r1, r0
    SHL   r1, 21
    SHR   r1, 29
    ADD   r2, r1
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r2, SL + 2
    JMP   look
c_en:                        ; 11: the checksum, then answer the slot's ID
    CALL  slotk
    LDT   r1, r2, SL         ; the protected identifier
    MOV   r0, r1
    SHL   r0, 26
    SHR   r0, 26             ; ID
    MOV   r3, r2
    ADDI  r3, SL
    STT   r3, r0, LUT        ; LUT[ID] = the slot's address
    SHR   r0, 1
    CMPI  r0, 0x1E
    JNZ   enh
    MOVI  r1, 0              ; IDs 0x3C, 0x3D: classic, the data only
enh:
    LDT   r0, r2, SL + 1     ; count
cs:
    LDT   r3, r2, SL + 2
    ADD   r1, r3
    CMPI  r1, 256
    JC    nocarry            ; (C: r1 < 256)
    MOVI  r3, 255
    SUB   r1, r3             ; the carry goes back in
nocarry:
    ADDI  r2, 1
    MOVI  r3, 1
    SUB   r0, r3
    JNZ   cs
    MOVI  r3, 0xFF
    XOR   r1, r3
    STT   r1, r2, SL + 2     ; the checksum after the data
    JMP   look

slotk:                       ; r2 = 12 * slot [13:12] of r0
    MOV   r2, r0
    SHL   r2, 18
    SHR   r2, 30
    MOV   r3, r2
    SHL   r3, 3
    SHL   r2, 2
    ADD   r2, r3
    RET
