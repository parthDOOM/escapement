; EM4100 read-only RFID tag (EM Microelectronic EM4100 datasheet, 2004): the transponder's
; modulator control at logic level on pin PIN, for a 125 kHz coil driver. Low switches the
; modulator ON (high current, the field damped), high switches it OFF.
;
; The tag sends its 64-bit memory array over and over while it is powered: 9 header bits
; (all 1), 10 rows of 4 data bits each followed by an even row parity bit, 4 even column
; parity bits and a stop bit 0. Manchester, one bit every 64 periods of the field (RF/64,
; 512 us at 125 kHz), with a transition in the middle of every bit: a 1 is ON then OFF
; (low, high), a 0 is OFF then ON (high, low), as in the datasheet's Fig. 6.
;
; Host (POP): the 64 bits as two words, bits 63..32 then 31..0, MSB first (sw/escmodels/
; em4100.py builds them from a 40-bit ID). The tag stays OFF until the first pair; a new
; pair is taken between two repetitions.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default RF_KHZ, 125                        ; the reader's field
.default TB, 0xF8                           ; tape: the frame's two words
.equ BIT, CLK_KHZ * 64 // RF_KHZ            ; RF/64: 12800 ticks at 25 MHz and 125 kHz
.equ H, BIT // 2
.equ D, 1 << PIN
.equ W0, TB
.equ W1, TB + 1
.assert BIT % 2 == 0, "a bit must split into two equal halves"

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  16
    EMIT  D, D, D            ; modulator OFF until there is a frame
    MOVI  r3, 0
    POP   r0
    STT   r0, r3, W0
    POP   r0
    STT   r0, r3, W1
anchor:                      ; a fresh schedule after waiting on the host
    WAITT
    RDTIME r1
    ADDI  r1, 64
    SETT  r1
frame:                       ; the 64 bits, then at once the next repetition
    MOVI  r3, 0
    LDT   r0, r3, W0
    CALL  word
    MOVI  r3, 0
    LDT   r0, r3, W1
    CALL  word
    RXREADY
    JZ    frame
    POP   r0                 ; a new frame: (RXREADY saw its first word)
    MOVI  r3, 0
    STT   r0, r3, W0
    POP   r0
    STT   r0, r3, W1
    JMP   anchor

word:                        ; the 32 bits of r0, MSB first
    LDLC  32
wbit:
    SHL   r0, 1              ; C = the next bit
    JC    wone
    EMIT  D, D, D            ; 0: OFF, then ON
    ADDT  H
    EMIT  D, 0, D
    ADDT  H
    DJNZ  wbit
    RET
wone:
    EMIT  D, 0, D            ; 1: ON, then OFF
    ADDT  H
    EMIT  D, D, D
    ADDT  H
    DJNZ  wbit
    RET
