; SPI controller, write-only, at line rate on the line unit (v1.6): one bit every 4 ticks
; with the default 2-tick cells (10 Mb/s at 40 MHz, 6.25 Mb/s at 25 MHz), any SPI mode.
; For shift registers, display and LED drivers, DACs, flash page writes: devices that only
; listen. (spi_ctrl.asm bit-bangs full duplex, slower.)
;
; The line unit drives MOSI on pin MOSI and SCK on pin SCK: each bit is two cells, MOSI
; holding the bit through both, SCK carrying the clock pattern (the extended
; configuration's `clk`), so MOSI changes a cell before every sampling edge. This context
; drives CS_n on pin CS itself, with DRIVE events around each packet.
;
; Host (POP): a transfer's length in bytes (1..1016), then the bytes MSB first, four per
; word (byte 0 in [31:24]). Host (PUSH): 1 when the transfer is done (CS_n high again).

.default CTX, 0
.default SCK, 4
.default MOSI, 5
.default CS, 7
.default MODE, 0                            ; CPOL = MODE >> 1, CPHA = MODE & 1
.default CELL, 2                            ; ticks per half bit
.default TB, 0xF8                           ; tape: the configuration (5 words)
.default FB, 0x00                           ; transfer buffer: up to 254 words
.assert CELL >= 2, a cell lasts at least 2 ticks
.equ CPOL, (MODE >> 1) & 1
.equ CPHA, MODE & 1
.equ PC, 1 + ((CPOL ^ CPHA) ^ 1)            ; the clock pattern: 0b10 (shift low, sample high) or 0b01
.equ CSM, 1 << CS
.equ W0, MOSI | (SCK << 3) | (1 << 9) | (1 << 18) | (1 << 20)   ; MSB first, driven when idle
.equ W1, CELL
.equ W2, 1 << 25                            ; ext, no CRC
.equ W3, 1 | (0 << 3) | (3 << 11) | (1 << 19) | (PC << 20) | (CPOL << 28)
.equ W4, 0
.equ CFG, TB
.equ LEAD, 32

.entry CTX, main
.own   SCK, CTX
.own   MOSI, CTX
.own   CS, CTX
.tape  CFG, W0, W1, W2, W3, W4

main:
    ADDT  8
    EMIT  CSM, CSM, CSM      ; CS_n high
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: SCK at CPOL, MOSI high
    ADDT  0xFFFFFF
    GETL  r1
next:
    POP   r1                 ; bytes
    MOV   r3, r1
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
    EMIT  CSM, 0, CSM        ; CS_n low
    ADDT  CELL * 2
    SEND  r0                 ; the bits, a bit time after CS_n falls
    ADDT  0xFFFFFF
    GETL  r1                 ; the last bit has ended (SCK back at CPOL)
    RDTIME r2                ; (from now: GETL's tmr would be stale after a dropped SEND)
    SETT  r2
    ADDT  LEAD
    EMIT  CSM, CSM, CSM      ; CS_n high
    MOVI  r1, 1
    PUSH  r1
    JMP   next
