; nRF24L01+ transmitter (Nordic nRF24L01+ Product Specification 1.0): the chip drives the
; radio through SPI (mode 0, MSB first, CSN low per command), CE and IRQ, as a primary
; transmitter with Enhanced ShockBurst: auto-acknowledge and retransmit on pipe 0, a 2-byte
; CRC. Each SPI command is built in the tape (one byte a word) and sent by `spi`, which
; returns the STATUS register the radio shifts out during the command byte.
;
; Host (POP), [31:28] the command:
;   1: set up: RF_SETUP [23:16], SETUP_RETR [15:8] (ARD, ARC), RF_CH [6:0]. Writes CONFIG
;      (EN_CRC, CRCO 2 bytes, PWR_UP, PTX), EN_AA and EN_RXADDR pipe 0, SETUP_RETR, RF_CH,
;      RF_SETUP, clears STATUS, FLUSH_TX, then waits Tpd2stby (1.5 ms); reply: STATUS
;   2: the 5-byte address: [7:0] its last (most significant) byte; the next word its bytes
;      0-3 from [31:24] (LSByte first, as the radio takes it): into TX_ADDR and RX_ADDR_P0
;      (the ACK comes back on pipe 0)
;   3: send [5:0] bytes (1 to 32): the next words carry them, from [31:24]; W_TX_PAYLOAD,
;      CE high 15 us (Thce 10), wait for IRQ low (up to 100 ms), read STATUS (NOP), clear
;      TX_DS and MAX_RT, FLUSH_TX after MAX_RT; reply: STATUS bits 6-4 as read (0x20 sent
;      and acknowledged, 0x10 no ACK after ARC retransmits), or 0xFF: no IRQ

.default CTX, 0
.default IRQ_PIN, 2
.default CE_PIN, 3
.default SCK_PIN, 4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default CSN_PIN, 7
.default CLK_KHZ, 25000
.default SCK_KHZ, 500
.default TB, 0xC0                           ; tape: the command being built, scratch
.equ SCK, 1 << SCK_PIN
.equ MOSI, 1 << MOSI_PIN
.equ CSN, 1 << CSN_PIN
.equ CE, 1 << CE_PIN
.equ IRQ, 1 << IRQ_PIN
.equ H, (CLK_KHZ + 2 * SCK_KHZ - 1) // (2 * SCK_KHZ)
.equ LAT, 40
.equ MS, CLK_KHZ
.equ SB, TB                                 ; up to 33 bytes: command, data
.equ ST, TB + 33                            ; STATUS from the last command
.equ ARG, TB + 34
.assert TB + 35 <= 256, "the tape area does not fit"
.assert H >= 18, "the bit loop needs a half period of 18 ticks"
.assert H * 1000000 // CLK_KHZ >= 50, "SCK at most 10 MHz"

.entry CTX, main
.own   SCK_PIN, CTX
.own   MOSI_PIN, CTX
.own   CSN_PIN, CTX
.own   CE_PIN, CTX

main:
    ADDT  40
    EMIT  SCK|MOSI|CSN|CE, CSN, SCK|MOSI|CSN|CE    ; CSN high, SCK low, CE low
cmd:
    POP   r1
    MOV   r0, r1
    SHR   r0, 28
    CMPI  r0, 1
    JZ    setup
    CMPI  r0, 2
    JZ    addr
    CMPI  r0, 3
    JZ    send
    JMP   cmd

; ---- 1: set up: register, value pairs, then FLUSH_TX
setup:
    MOVI  r3, 0
    STT   r1, r3, ARG
    MOVI  r0, 0x20           ; CONFIG: EN_CRC, CRCO (2 bytes), PWR_UP, PTX
    MOVI  r2, 0x0E
    CALL  wreg
    MOVI  r0, 0x21           ; EN_AA: pipe 0
    MOVI  r2, 0x01
    CALL  wreg
    MOVI  r0, 0x22           ; EN_RXADDR: pipe 0
    MOVI  r2, 0x01
    CALL  wreg
    MOVI  r3, 0
    LDT   r2, r3, ARG        ; SETUP_RETR = [15:8]
    SHL   r2, 16
    SHR   r2, 24
    MOVI  r0, 0x24
    CALL  wreg
    MOVI  r3, 0
    LDT   r2, r3, ARG        ; RF_CH = [6:0]
    SHL   r2, 25
    SHR   r2, 25
    MOVI  r0, 0x25
    CALL  wreg
    MOVI  r3, 0
    LDT   r2, r3, ARG        ; RF_SETUP = [23:16]
    SHL   r2, 8
    SHR   r2, 24
    MOVI  r0, 0x26
    CALL  wreg
    MOVI  r0, 0x27           ; STATUS: clear RX_DR, TX_DS, MAX_RT
    MOVI  r2, 0x70
    CALL  wreg
    MOVI  r0, 0xE1           ; FLUSH_TX
    CALL  one
    WAITT                    ; Tpd2stby: 1.5 ms from PWR_UP before any CE
    RDTIME r0
    SETT  r0
    ADDT  MS * 3 // 2
    MOVI  r0, 0xFF           ; NOP: STATUS
    CALL  one
    PUSH  r0
    JMP   cmd

; ---- 2: the address, into TX_ADDR (10h) and RX_ADDR_P0 (0Ah)
addr:
    POP   r2                 ; bytes 0-3
    MOVI  r3, 1
ab:                          ; SB[1..4] = bytes 0-3, SB[5] = byte 4
    MOV   r0, r2
    SHR   r0, 24
    STT   r0, r3, SB
    SHL   r2, 8
    ADDI  r3, 1
    CMPI  r3, 5
    JNZ   ab
    SHL   r1, 24
    SHR   r1, 24
    STT   r1, r3, SB
    MOVI  r0, 0x30           ; W_REGISTER TX_ADDR
    STT   r0, r2, SB         ; (r2 = 0 after four shifts)
    MOVI  r1, 6
    CALL  spi
    MOVI  r0, 0x2A           ; W_REGISTER RX_ADDR_P0
    MOVI  r2, 0
    STT   r0, r2, SB
    MOVI  r1, 6
    CALL  spi
    JMP   cmd

; ---- 3: a packet: SB = A0h, then the payload
send:
    SHL   r1, 26
    SHR   r1, 26             ; bytes
    MOVI  r3, 0
    STT   r1, r3, ARG
    MOVI  r0, 0xA0           ; W_TX_PAYLOAD
    STT   r0, r3, SB
    MOVI  r3, 1              ; next SB index
sw:
    POP   r2
    LDLC  4
swb:
    MOV   r0, r2
    SHR   r0, 24
    STT   r0, r3, SB
    SHL   r2, 8
    ADDI  r3, 1
    DJNZ  swb
    MOVI  r0, 0
    LDT   r0, r0, ARG
    CMP   r0, r3             ; borrow when every byte is in
    JNC   sw
    ADDI  r0, 1              ; command + bytes
    MOV   r1, r0
    CALL  spi
    WAITT                    ; CE high 15 us: the transmission
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    EMIT  CE, CE, CE
    ADDT  MS * 15 // 1000
    EMIT  CE, 0, CE
    WAITT
    RDTIME r0                ; IRQ low within 100 ms
    SETT  r0
    ADDT  MS * 100
    WAITP IRQ, 0
    JZ    irq
    MOVI  r0, 0xFF
    PUSH  r0
    JMP   cmd
irq:
    RDTIME r0                ; (tmr still holds the timeout: from now instead)
    SETT  r0
    MOVI  r0, 0xFF           ; NOP: STATUS
    CALL  one
    MOVI  r3, 0x70
    AND   r3, r0             ; TX_DS, MAX_RT (RX_DR) as read
    MOVI  r2, 0
    STT   r3, r2, ARG
    MOVI  r0, 0x27           ; clear them
    MOVI  r2, 0x70
    CALL  wreg
    MOVI  r2, 0
    LDT   r3, r2, ARG
    TSTI  r3, 0x10
    JZ    sdone
    MOVI  r0, 0xE1           ; MAX_RT: the payload stays in the FIFO; FLUSH_TX
    CALL  one
    MOVI  r2, 0
    LDT   r3, r2, ARG
sdone:
    PUSH  r3
    JMP   cmd

; ---- a register write: command r0, value r2 (falls into spi)
wreg:
    MOVI  r3, 0
    STT   r0, r3, SB
    STT   r2, r3, SB + 1
    MOVI  r1, 2
    JMP   spi
; ---- a command byte alone: r0 (falls into spi)
one:
    MOVI  r3, 0
    STT   r0, r3, SB
    MOVI  r1, 1
; ---- spi: CSN low, the r1 bytes SB[0..], CSN high; STATUS (shifted out during SB[0]) in r0.
; Every byte starts on a fresh time base (the radio's SPI may pause anywhere while CSN is low).
spi:
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    EMIT  CSN, 0, CSN
    MOVI  r3, 0
sbyte:
    LDT   r2, r3, SB
    SHL   r2, 24
    LDSH  r2
    LDLC  8
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    ADDT  H
sbit:
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; data out, a tick after the falling edge
    ADDT  H - 1
    EMIT  SCK, SCK, SCK      ; rising edge: the radio samples MOSI
    ADDT  1
    SAMPLE MISO_PIN, MSB
    ADDT  H - 1
    EMIT  SCK, 0, SCK        ; falling edge: the radio's next bit
    DJNZ  sbit
    GETI  r0
    CMPI  r3, 0
    JNZ   snext
    STT   r0, r3, ST         ; STATUS: the byte read during the command
snext:
    ADDI  r3, 1
    CMP   r3, r1
    JNZ   sbyte
    WAITT
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMIT  CSN, CSN, CSN
    ADDT  H                  ; CSN high for H at least (Tcwh 50 ns)
    MOVI  r2, 0
    LDT   r0, r2, ST
    RET
