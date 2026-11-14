; AVR in-system programmer (ATmega328P datasheet, 27.8 "Serial Downloading"): the chip
; programs and reads an AVR through its SPI pins and RESET. Mode 0, MSB first, SCK at
; SCK_KHZ (125 kHz: SCK high and low each more than 2 cycles of a 1 MHz target); RESET low
; holds the target in programming mode.
;
; Every instruction is 4 bytes, sent as one 32-bit transfer; the target's 4 bytes come back
; in r0 (the third is the echo of the second, the fourth the data out). Entering: RESET
; and SCK low, 20 ms, Programming Enable (0xAC 0x53 0x00 0x00); if 0x53 does not echo in
; the third byte, a positive RESET pulse and again (up to 8 times, as the datasheet says).
;
; Host (POP), [31:28] the command:
;   1: enter programming mode; reply: status (0 good, 1 no echo), then the signature
;      bytes 0-2 in [23:0] (an ATmega328P: 0x1E950F)
;   2: one raw instruction, in the next word; reply: the 4 bytes the target sent
;   3: program the flash page at word address [15:0]: PAGEW more words follow, each one
;      program word in [15:0]; load low then high byte for each, Write Program Memory Page,
;      then Poll RDY/BSY until ready; reply: status (0 good, 2 still busy after 2000 polls)
;   4: read [23:16] program words from word address [15:0]; reply: one word each
;   5: leave programming mode (RESET high)

.default CTX, 0
.default SCK_PIN, 4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default RST_PIN, 7
.default CLK_KHZ, 25000
.default SCK_KHZ, 125
.default PAGEW, 64                          ; words per flash page (ATmega328P: 64)
.default TB, 0xF8                           ; tape: scratch
.equ SCK, 1 << SCK_PIN
.equ MOSI, 1 << MOSI_PIN
.equ RST, 1 << RST_PIN
.equ H, (CLK_KHZ + 2 * SCK_KHZ - 1) // (2 * SCK_KHZ)
.equ LAT, 40
.equ W, TB                                  ; the program word being loaded
.equ ADR, TB + 1
.equ N, TB + 2
.assert H >= 18, "the bit loop needs a half period of 18 ticks"

.entry CTX, main
.own   SCK_PIN, CTX
.own   MOSI_PIN, CTX
.own   RST_PIN, CTX

main:
    ADDT  40
    EMIT  SCK|MOSI|RST, 0, SCK|MOSI|RST     ; RESET low, SCK low
cmd:
    POP   r1
    MOV   r0, r1
    SHR   r0, 28
    CMPI  r0, 1
    JZ    enter
    CMPI  r0, 2
    JZ    raw
    CMPI  r0, 3
    JZ    page
    CMPI  r0, 4
    JZ    read
    CMPI  r0, 5
    JNZ   cmd
    CALL  anchor             ; 5: RESET high
    EMIT  RST, RST, RST
    JMP   cmd

; ---- 1: programming enable, in sync, then the signature
enter:
    MOVI  r1, 8              ; attempts
en:
    CALL  anchor             ; 20 ms with RESET low
    ADDT  CLK_KHZ * 20
    WAITT
    MOVI  r2, 0
    MOVHI r2, 0xAC53
    CALL  x4
    SHR   r0, 8
    SHL   r0, 24
    SHR   r0, 24
    CMPI  r0, 0x53
    JZ    insync
    CALL  anchor             ; no echo: a positive RESET pulse (100 us), then again
    EMIT  RST, RST, RST
    ADDT  CLK_KHZ // 10
    EMIT  RST, 0, RST
    MOVI  r0, 1
    SUB   r1, r0
    JNZ   en
    PUSH  r0                 ; 1: no echo
    JMP   cmd
insync:
    MOVI  r1, 0
    PUSH  r1                 ; good
    MOVI  r3, 0              ; the signature bytes 0, 1, 2
sig:
    MOV   r2, r3
    SHL   r2, 8
    MOVHI r2, 0x3000
    CALL  x4
    SHL   r0, 24
    SHR   r0, 24
    SHL   r1, 8
    OR    r1, r0
    ADDI  r3, 1
    CMPI  r3, 3
    JNZ   sig
    PUSH  r1
    JMP   cmd

; ---- 2: one raw instruction
raw:
    POP   r2
    CALL  x4
    PUSH  r0
    JMP   cmd

; ---- 3: a flash page
page:
    SHL   r1, 16
    SHR   r1, 16             ; the page's word address
    MOVI  r3, 0
    STT   r1, r3, ADR
    STT   r3, r3, N
pw:
    POP   r0
    STT   r0, r3, W          ; (r3 = 0 here)
    LDT   r1, r3, ADR
    LDT   r2, r3, N
    ADD   r1, r2             ; this word's address: its LSB goes in byte 3
    SHL   r1, 24
    SHR   r1, 16
    MOV   r2, r0             ; Load Program Memory Page, low byte: 0x40 0x00 adr data
    SHL   r2, 24
    SHR   r2, 24
    OR    r2, r1
    MOVHI r2, 0x4000
    CALL  x4
    LDT   r2, r3, W          ; high byte: 0x48 0x00 adr data
    SHL   r2, 16
    SHR   r2, 24
    OR    r2, r1
    MOVHI r2, 0x4800
    CALL  x4
    LDT   r2, r3, N
    ADDI  r2, 1
    STT   r2, r3, N
    CMPI  r2, PAGEW
    JNZ   pw
    LDT   r2, r3, ADR        ; Write Program Memory Page: 0x4C adr_MSB adr_LSB 0x00
    SHL   r2, 8
    MOVI  r1, 0
    MOVHI r1, 0x4C00
    OR    r2, r1             ; (MOVHI would replace [23:16], the address MSB)
    CALL  x4
    MOVI  r1, 2000           ; Poll RDY/BSY: 0xF0 0x00 0x00, bit 0 of the data: busy
poll:
    MOVI  r2, 0
    MOVHI r2, 0xF000
    CALL  x4
    TSTI  r0, 1
    JZ    ready
    MOVI  r0, 1
    SUB   r1, r0
    JNZ   poll
    MOVI  r0, 2
    PUSH  r0
    JMP   cmd
ready:
    MOVI  r0, 0
    PUSH  r0
    JMP   cmd

; ---- 4: read program words
read:
    MOV   r3, r1
    SHL   r3, 8
    SHR   r3, 24             ; the count, [23:16]
    SHL   r1, 16
    SHR   r1, 16             ; the address
rw:
    MOV   r2, r1             ; Read Program Memory, high byte: 0x28 adr_MSB adr_LSB 0
    SHL   r2, 8
    MOVI  r0, 0
    MOVHI r0, 0x2800
    OR    r2, r0
    CALL  x4
    SHL   r0, 24
    SHR   r0, 16             ; the high byte in [15:8]
    MOVI  r2, 0
    STT   r0, r2, W
    MOV   r2, r1             ; low byte: 0x20 adr_MSB adr_LSB 0
    SHL   r2, 8
    MOVI  r0, 0
    MOVHI r0, 0x2000
    OR    r2, r0
    CALL  x4
    SHL   r0, 24
    SHR   r0, 24
    MOVI  r2, 0
    LDT   r2, r2, W
    OR    r0, r2
    PUSH  r0
    ADDI  r1, 1
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   rw
    JMP   cmd

; ---- a fresh time base (after waiting on the host or the target)
anchor:
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    RET

; ---- one instruction: the 4 bytes of r2, MSB first; the 4 bytes back in r0 (uses r0)
x4:
    LDSH  r2
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    LDLC  32
xb:
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; data out, a tick after the falling edge
    ADDT  H - 1
    EMIT  SCK, SCK, SCK      ; rising edge: the target samples MOSI
    ADDT  1
    SAMPLE MISO_PIN, MSB
    ADDT  H - 1
    EMIT  SCK, 0, SCK        ; falling edge: the target's next MISO bit
    DJNZ  xb
    WAITT
    GETI  r0
    RET
