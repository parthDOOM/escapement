; WS2812(B) LED chain on the line unit (v1.6): a whole chain from one SEND, the context
; free while it streams. (ws2812.asm bit-bangs it: three events a bit, its context busy.)
;
; A bit is a symbol of three cells of 0.417 us (the extended configuration's cells and
; patterns): a 0 is high for one cell (T0H 0.417 us), a 1 for two (T1H 0.833 us); a bit is
; 1.25 us. Cells are timed by the line unit's fractional period (a cell is P or P + 1
; ticks), so this holds within the datasheet's +/-150 ns at any CLK_KHZ from 12000 up.
; The line idles low (ilow) and stays driven.
;
; Host (POP): the number of LEDs (1..330), then their colours as bytes, G R B for each LED
; in chain order, four per word, first byte in [31:24]. Host (PUSH): 1 once the frame has
; latched (the line low for RESET_US).

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default RESET_US, 300                      ; newer parts latch after 280 us, older 50
.default TB, 0xF8                           ; tape: the configuration (5 words)
.default FB, 0x00                           ; colour buffer: up to 248 words
.equ PX, (CLK_KHZ * 256 + 1200) // 2400     ; a cell in 1/256 ticks (0.41667 us)
.assert CLK_KHZ >= 12000, a tick of at most 83 ns keeps every pulse within 150 ns
.equ W0, PIN | (1 << 18) | (1 << 20)        ; NRZ, MSB first, no stuffing, driven when idle
.equ W1, (PX >> 8) | ((PX & 255) << 12) | (4 << 20)
.equ W2, 1 << 25                            ; ext, no CRC
.equ W3, 2 | (0b001 << 3) | (0b011 << 11)   ; three cells: 0 = 100, 1 = 110
.equ W4, 1 << 20                            ; idle low
.equ RESET, (CLK_KHZ * RESET_US + 999) // 1000
.equ LEAD, 32

.entry CTX, main
.own   PIN, CTX
.tape  TB, W0, W1, W2, W3, W4

main:
    MOVI  r0, TB
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: the line low
    ADDT  0xFFFFFF
    GETL  r1
frame:
    POP   r1                 ; LEDs
    MOV   r3, r1
    ADD   r3, r1
    ADD   r3, r1             ; bytes
    MOV   r1, r3             ; (kept: bits = 8 x bytes)
    ADDI  r3, 3
    SHR   r3, 2
    ADDI  r3, FB             ; the buffer's end
    MOVI  r0, FB
store:
    POP   r2
    STT   r2, r0, 0
    ADDI  r0, 1
    CMP   r0, r3
    JNZ   store
    SHL   r1, 3              ; bits
    MOV   r0, r1
    SHL   r0, 25
    SHR   r0, 17             ; (bits mod 128) << 8
    SHR   r1, 7
    SHL   r1, 24             ; (bits / 128) << 24
    OR    r0, r1
    ADDI  r0, FB             ; the SEND operand
    RDTIME r2
    SETT  r2
    ADDT  LEAD
    SEND  r0                 ; the chain
    ADDT  0xFFFFFF
    GETL  r1                 ; the last bit has ended
    RDTIME r2
    SETT  r2
    ADDT  RESET
    WAITT                    ; the line low for the reset time: the LEDs latch
    MOVI  r1, 1
    PUSH  r1
    JMP   frame
