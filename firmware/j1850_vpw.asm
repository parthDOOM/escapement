; SAE J1850 VPW: the variable pulse width bus of OBD-II (GM Class 2, Chrysler), as a scan
; tool or a module sends a frame. 10.4 kbit/s, one symbol per bit, the bus level changing
; at every symbol: the frame starts with an active (high) 200 us SOF, and each bit is the
; next symbol, passive and active in turn; a passive 64 us symbol is a 0 and a passive
; 128 us one a 1, an active 64 us symbol a 1 and an active 128 us one a 0. Bytes go MSB
; first; the last is a CRC-8 (x^8 + x^4 + x^3 + x^2 + 1, start 0xFF, inverted) over the
; others. The frame ends with the bus passive: 200 us is the end of data, 280 us the end
; of the frame. Output on pin 0 to a VPW transceiver's TX input (high = active).
;
; (SAE J1850 timing as the Motorola/Freescale and Texas Instruments VPW application notes
; give it: Tv1 64 us, Tv2 128 us, Tv3 SOF 200 us, Tv4 EOD 200 us, EOF 280 us; the CRC is
; the standard's, CRC-8/SAE-J1850 in the CRC catalogue, check value 0x4B.)
;
; Host: one word per byte, [7:0] the byte and [8] = 1 on the frame's last byte (header,
; then data; up to 11 bytes, the 11th ends the frame). The chip buffers the frame, adds
; the CRC, sends it, waits out the inter-frame space and PUSHes 1. Every byte of a frame
; is in hand before its SOF, so no symbol depends on the host's timing.
;
; This is a transmitter on its own bus segment: it does not arbitrate (a VPW node that
; loses arbitration sees an active level where it sent a passive one) and sends no
; in-frame response.
;
; Each byte starts on a passive symbol: 8 bits a byte, and the SOF before it is active.
; Every symbol is one DRIVE event; its length is chosen by the bit and by whether the
; symbol is passive or active, which alternate, two bits a pass.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.equ TX, 1 << PIN
.equ T_SHORT, CLK_KHZ * 64 // 1000          ; 64 us
.equ T_LONG, CLK_KHZ * 128 // 1000          ; 128 us
.equ T_SOF, CLK_KHZ * 200 // 1000           ; 200 us
.equ T_IFS, CLK_KHZ * 300 // 1000           ; the passive bus after the last bit: EOD, EOF, IFS
.equ LEAD, 24                               ; RDTIME to the SOF's EMIT: three instructions
.default BUF, 0                             ; tape: up to 12 bytes, one a word
.default CRCT, 16                           ; tape: CRC-8 by nibble, 16 words
.equ MAXB, 11

.entry CTX, start
.own   PIN, CTX
; the remainders of x^4 n(x) modulo x^8 + x^4 + x^3 + x^2 + 1 for each nibble n
.tape  CRCT, 0x00, 0x1D, 0x3A, 0x27, 0x74, 0x69, 0x4E, 0x53
.tape  CRCT + 8, 0xE8, 0xF5, 0xD2, 0xCF, 0x9C, 0x81, 0xA6, 0xBB

start:
    ADDT  16
    EMIT  TX, 0, TX              ; passive
frame:
    MOVI  r1, 0xFF               ; the CRC's start value
    MOVI  r2, 0                  ; bytes buffered
    MOVI  r3, 0xFF
get:
    POP   r0
    STT   r0, r2, BUF
    AND   r0, r3                 ; the byte
    XOR   r1, r0                 ; crc ^= byte, then two nibble steps:
    MOV   r0, r1
    SHR   r0, 4
    LDT   r0, r0, CRCT           ;   T[crc >> 4]
    SHL   r1, 4
    AND   r1, r3                 ;   ^ (crc << 4) & 0xFF
    XOR   r1, r0
    MOV   r0, r1
    SHR   r0, 4
    LDT   r0, r0, CRCT
    SHL   r1, 4
    AND   r1, r3
    XOR   r1, r0
    LDT   r0, r2, BUF
    ADDI  r2, 1
    CMPI  r2, MAXB
    JZ    crc                    ; the eleventh byte ends the frame
    TSTI  r0, 0x100
    JZ    get                    ; not the last byte yet
crc:
    XOR   r1, r3                 ; inverted
    STT   r1, r2, BUF            ; the CRC is the last byte
    ADDI  r2, 1
    MOV   r1, r2                 ; r1 = bytes to send
    RDTIME r3
    ADDI  r3, LEAD
    SETT  r3
    EMIT  TX, TX, TX             ; SOF
    ADDT  T_SOF
    MOVI  r2, 0
byte:
    LDT   r0, r2, BUF
    SHL   r0, 24                 ; MSB first: bit 7 to bit 31
    LDLC  4
pair:
    EMIT  TX, 0, TX              ; an even bit: a passive symbol, long for a 1
    SHL   r0, 1
    JNC   p_short
    ADDT  T_LONG
    JMP   odd
p_short:
    ADDT  T_SHORT
odd:
    EMIT  TX, TX, TX             ; an odd bit: an active symbol, short for a 1
    SHL   r0, 1
    JNC   a_long
    ADDT  T_SHORT
    JMP   next
a_long:
    ADDT  T_LONG
next:
    DJNZ  pair
    ADDI  r2, 1
    CMP   r2, r1
    JNZ   byte
    EMIT  TX, 0, TX              ; passive: EOD, EOF and the inter-frame space
    ADDT  T_IFS
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   frame
