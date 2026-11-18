; FlexRay frames, as a node sends them in the static segment: the automotive bus at
; 2.5 Mbit/s (5 Mbit/s from 50 MHz), output on pin 0 to a FlexRay bus driver's TxD (high =
; idle, low = dominant).
;
; (FlexRay Communications System Protocol Specification v2.1 rev. A / v3.0.1: coding and
; frame format.) On the wire a frame is the transmission start sequence (TSS, low for
; TSS bits), the frame start sequence (FSS, one high bit), then every byte as its byte
; start sequence (BSS, high then low) and its 8 bits MSB first, then the frame end
; sequence (FES, low then high); the line then stays idle (high). The bytes: a 5-byte
; header (reserved bit, payload preamble, null frame, sync frame and startup frame
; indicators, 11-bit frame ID, 7-bit payload length in 2-byte words, the 11-bit header CRC,
; 6-bit cycle count), the payload, and the 24-bit frame CRC. The header CRC (x^11 + x^9 +
; x^8 + x^7 + x^2 + 1, start 0x01A) covers the sync and startup indicators, the frame ID
; and the payload length; the frame CRC (polynomial 0x5D6DCB, start 0xFEDCBA on channel A)
; covers the header and the payload. Both are computed here, bit by bit, MSB first.
;
; Every frame carries PLEN 2-byte payload words (PLEN even, up to 16): in FlexRay's static
; segment every slot has the cluster's one payload length.
;
; Host: one header word, [31:16] = reserved, payload preamble, null frame, sync frame and
; startup frame indicators and the frame ID, as they go on the wire (0x2000 | id: a normal
; frame), [5:0] the cycle count; then PLEN / 2 payload words, four bytes each, the first
; byte in [31:24]. The chip computes both CRCs, sends the frame and PUSHes 1.
;
; The frame is laid out first as 10-bit units, MSB first: TSS and FSS (TSS = 9 here, so 9
; zeros and a one), each byte as 1 0 b7..b0, FES and idle (0 1 1 1 1 1 1 1 1 1), then idle
; units to fill; three units to a word, so the sending loop is one EMITX and one ADDT a bit.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default KBPS, 2500                         ; kbit/s: 2500, or 5000 at 50 MHz (10000 needs 100)
.default PLEN, 4                            ; payload: 2-byte words (8 bytes)
.equ TX, 1 << PIN
.equ TB, CLK_KHZ // KBPS                    ; ticks a bit
.assert CLK_KHZ % KBPS == 0, FlexRay needs a whole number of ticks a bit
.assert TB >= 10, FlexRay needs at least 10 ticks a bit: one EMITX and one ADDT and room between words
.assert PLEN % 2 == 0, PLEN is even: a host word holds two payload words
.assert PLEN >= 2, PLEN is at least 2 (with 0 the payload loop would run 65536 times)
.assert PLEN <= 16, PLEN is at most 16
.equ NB, 8 + 2 * PLEN                       ; bytes: header 5, payload, frame CRC 3
.equ NU, NB + 2                             ; units: TSS and FSS, bytes, FES
.equ NW, (NU + 2) // 3                      ; words of three units
.equ LEAD, 48
.default UNITS, 0                           ; tape: the units, from TSS and FSS
.default WORDS, 64                          ; tape: the packed frame
.equ TSSU, 0b0000000001                     ; TSS 9 low, FSS high
.equ FESU, 0b0111111111                     ; FES low, high, then idle
.equ IDLEU, 0b1111111111

.entry CTX, start
.own   PIN, CTX
.tape  UNITS, TSSU
.tape  UNITS + NU - 1, FESU, IDLEU, IDLEU

start:
    ADDT  16
    EMIT  TX, TX, TX                        ; idle
frame:
    POP   r0                                ; the header word
    MOVI  r3, 0
    STT   r0, r3, WORDS                     ; (kept here until the frame is packed)
    ; ---- header CRC over sync, startup, frame ID (word [28:16]) and PLEN: 20 bits
    MOV   r2, r0
    SHR   r2, 16
    MOVI  r3, 0x1FFF
    AND   r2, r3
    SHL   r2, 7
    ADDI  r2, PLEN
    SHL   r2, 12                            ; the first bit at bit 31
    MOVI  r0, 0x1A << 5                     ; CRC-11 held in bits 31..21: start 0x01A
    SHL   r0, 16
    MOVI  r3, 0x385 << 5                    ; and its polynomial there
    SHL   r3, 16
    LDLC  20
h_bit:
    SHL   r0, 1                             ; C = the CRC's top bit
    JC    h_top
    SHL   r2, 1                             ; feedback = the data bit
    JNC   h_next
    XOR   r0, r3
    JMP   h_next
h_top:
    SHL   r2, 1                             ; feedback = not the data bit
    JC    h_next
    XOR   r0, r3
h_next:
    DJNZ  h_bit
    SHR   r0, 21                            ; r0 = the header CRC
    ; ---- the header's five bytes, as units 1 to 5 (0x200 | byte: BSS 1 0, then the byte)
    MOVI  r3, 0
    LDT   r1, r3, WORDS                     ; the header word
    MOV   r2, r1
    SHR   r2, 24
    ADDI  r2, 0x200
    STT   r2, r3, UNITS + 1                 ; R PPI NFI SFI STFI ID[10:8]
    MOV   r2, r1
    SHR   r2, 16
    MOVI  r3, 0xFF
    AND   r2, r3
    ADDI  r2, 0x200
    MOVI  r3, 0
    STT   r2, r3, UNITS + 2                 ; ID[7:0]
    MOV   r2, r0
    SHR   r2, 10
    ADDI  r2, 0x200 | PLEN << 1
    STT   r2, r3, UNITS + 3                 ; PLEN, HCRC[10]
    MOV   r2, r0
    SHR   r2, 2
    MOVI  r3, 0xFF
    AND   r2, r3
    ADDI  r2, 0x200
    MOVI  r3, 0
    STT   r2, r3, UNITS + 4                 ; HCRC[9:2]
    MOVI  r3, 3
    AND   r0, r3
    SHL   r0, 6
    MOVI  r3, 0x3F
    AND   r1, r3
    OR    r0, r1
    ADDI  r0, 0x200
    MOVI  r3, 0
    STT   r0, r3, UNITS + 5                 ; HCRC[1:0], cycle count
    ; ---- the payload, four bytes a host word, as units 6 on
    MOVI  r2, UNITS + 6
    LDLC  PLEN // 2
p_word:
    POP   r0
    MOV   r1, r0
    SHR   r1, 24
    ADDI  r1, 0x200
    STT   r1, r2, 0
    MOV   r1, r0
    SHL   r1, 8
    SHR   r1, 24
    ADDI  r1, 0x200
    STT   r1, r2, 1
    MOV   r1, r0
    SHL   r1, 16
    SHR   r1, 24
    ADDI  r1, 0x200
    STT   r1, r2, 2
    SHL   r0, 24
    SHR   r0, 24
    ADDI  r0, 0x200
    STT   r0, r2, 3
    ADDI  r2, 4
    DJNZ  p_word
    ; ---- frame CRC over the header and the payload (units 1 .. NB - 3), MSB first, held
    ; in bits 31..8; a unit << 24 is its byte there (the BSS bits fall off the top)
    MOVI  r0, 0xBA00                        ; start 0xFEDCBA
    MOVHI r0, 0xFEDC
    MOVI  r2, 0xCB00                        ; polynomial 0x5D6DCB
    MOVHI r2, 0x5D6D
    MOVI  r3, UNITS + 1
c_byte:
    LDT   r1, r3, 0
    SHL   r1, 24
    LDLC  8
c_bit:
    SHL   r0, 1                             ; C = the CRC's top bit
    JC    c_top
    SHL   r1, 1                             ; feedback = the data bit
    JNC   c_next
    XOR   r0, r2
    JMP   c_next
c_top:
    SHL   r1, 1                             ; feedback = not the data bit
    JC    c_next
    XOR   r0, r2
c_next:
    DJNZ  c_bit
    ADDI  r3, 1
    CMPI  r3, UNITS + NB - 2
    JNZ   c_byte
    ; ---- the frame CRC's three bytes, as units NB - 2 .. NB
    MOV   r1, r0
    SHR   r1, 24
    ADDI  r1, 0x200
    STT   r1, r3, 0
    MOV   r1, r0
    SHL   r1, 8
    SHR   r1, 24
    ADDI  r1, 0x200
    STT   r1, r3, 1
    SHL   r0, 16
    SHR   r0, 24
    ADDI  r0, 0x200
    STT   r0, r3, 2
    ; ---- three units to a word, MSB first: bits 31..22, 21..12, 11..2
    MOVI  r2, UNITS
    MOVI  r3, WORDS
    LDLC  NW
pack:
    LDT   r0, r2, 0
    SHL   r0, 22
    LDT   r1, r2, 1
    SHL   r1, 12
    OR    r0, r1
    LDT   r1, r2, 2
    SHL   r1, 2
    OR    r0, r1
    STT   r0, r3, 0
    ADDI  r2, 3
    ADDI  r3, 1
    DJNZ  pack
    ; ---- send: 30 bits a word, one DRIVE event a bit
    RDTIME r3
    ADDI  r3, LEAD
    SETT  r3
    MOVI  r2, WORDS
send:
    LDT   r0, r2, 0
    LDSH  r0
    LDLC  2                                 ; 15 bits a pass
half:
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    EMITX TX, MSB, TX
    ADDT  TB
    DJNZ  half
    ADDI  r2, 1
    CMPI  r2, WORDS + NW
    JNZ   send
    EMIT  TX, TX, TX                        ; idle
    ADDT  11 * TB                           ; the channel idle delimiter: 11 bits
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   frame
