; SPI NOR flash programmer (Winbond W25Q80DV datasheet, the 25-series instruction set): the
; chip reads, programs and erases a serial flash through its SPI pins. Mode 0, MSB first,
; SCK at SCK_KHZ, /CS low for each instruction. Every 4 bytes go as one 32-bit transfer; the
; flash's 4 bytes come back in r0.
;
; Host (POP), [31:28] the command:
;   1: Read JEDEC ID (9Fh); reply: [23:0] manufacturer, memory type, capacity (W25Q80DV:
;      0xEF4014)
;   2: Read Data (03h) from address [23:0]; the next word: N; reply: N words, each 4 bytes
;      in address order from [31:24]
;   3: Page Program (02h) at address [23:0]: Write Enable (06h), then the next word N (1 to
;      64) and N words of data (4 bytes each, from [31:24]); /CS high after the last byte,
;      then Read Status Register-1 (05h) until BUSY (bit 0) clears; reply: status (0 done;
;      1 WEL was not set after Write Enable, nothing sent; 2 still busy after POLLS reads)
;   4: Sector Erase (20h) of the 4 KB sector holding address [23:0]: as 3, no data
;   5: Read Status Register-1; reply: [7:0]
; A page holds 256 bytes and the flash wraps a program within its page: the host splits
; data at page boundaries.

.default CTX, 0
.default SCK_PIN, 4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default CS_PIN, 7
.default CLK_KHZ, 25000
.default SCK_KHZ, 500
.default POLLS, 0xFFFF                      ; status reads before giving up (tSE max 300 ms)
.equ SCK, 1 << SCK_PIN
.equ MOSI, 1 << MOSI_PIN
.equ CS, 1 << CS_PIN
.equ H, (CLK_KHZ + 2 * SCK_KHZ - 1) // (2 * SCK_KHZ)
.equ LAT, 40
.assert H >= 18, "the bit loop needs a half period of 18 ticks"

.entry CTX, main
.own   SCK_PIN, CTX
.own   MOSI_PIN, CTX
.own   CS_PIN, CTX

main:
    ADDT  40
    EMIT  SCK|MOSI|CS, CS, SCK|MOSI|CS      ; /CS high, SCK low
cmd:
    POP   r1
    MOV   r0, r1
    SHR   r0, 28
    SHL   r1, 8
    SHR   r1, 8              ; the address, [23:0]
    CMPI  r0, 1
    JZ    jedec
    CMPI  r0, 2
    JZ    read
    CMPI  r0, 3
    JZ    prog
    CMPI  r0, 4
    JZ    erase
    CMPI  r0, 5
    JNZ   cmd
    MOVI  r2, 0              ; 5: Read Status Register-1
    MOVHI r2, 0x0500
    CALL  csl
    CALL  x4
    CALL  csh
    SHL   r0, 24
    SHR   r0, 24
    PUSH  r0
    JMP   cmd

jedec:
    MOVI  r2, 0
    MOVHI r2, 0x9F00
    CALL  csl
    CALL  x4
    CALL  csh
    SHL   r0, 8
    SHR   r0, 8              ; the 3 bytes after the instruction
    PUSH  r0
    JMP   cmd

; ---- 2: Read Data
read:
    POP   r3                 ; N
    CALL  csl
    MOV   r2, r1
    MOVI  r0, 0
    MOVHI r0, 0x0300
    OR    r2, r0             ; 03h, then the address
    CALL  x4
rd:
    MOVI  r2, 0
    CALL  x4
    PUSH  r0
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   rd
    CALL  csh
    JMP   cmd

; ---- 3: Page Program; 4: Sector Erase
prog:
    MOVI  r3, 0x0200
    JMP   wcmd
erase:
    MOVI  r3, 0x2000
wcmd:                        ; r3[15:8] = the instruction
    MOVI  r2, 0
    MOVHI r2, 0x0600         ; Write Enable: 8 bits alone
    CALL  csl
    CALL  x8
    CALL  csh
    MOVI  r2, 0
    MOVHI r2, 0x0500         ; WEL set?
    CALL  csl
    CALL  x4
    CALL  csh
    TSTI  r0, 2
    JNZ   wel
    MOVI  r0, 1
    PUSH  r0
    CMPI  r3, 0x2000         ; a program's data words are still to come: take them
    JZ    cmd
    POP   r3
skip:
    POP   r0
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   skip
    JMP   cmd
wel:
    CMPI  r3, 0x2000
    JZ    se
    SHL   r3, 16             ; [31:24] = the instruction
    OR    r1, r3
    MOV   r2, r1
    POP   r3                 ; N
    CALL  csl
    CALL  x4
pw:
    POP   r2
    CALL  x4
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   pw
    CALL  csh                ; /CS high after the eighth bit of the last byte: it starts
    JMP   poll
se:
    SHL   r3, 16
    OR    r1, r3
    MOV   r2, r1
    CALL  csl
    CALL  x4
    CALL  csh
poll:
    MOVI  r1, POLLS
pl:
    MOVI  r2, 0
    MOVHI r2, 0x0500
    CALL  csl
    CALL  x4
    CALL  csh
    TSTI  r0, 1
    JZ    done
    MOVI  r0, 1
    SUB   r1, r0
    JNZ   pl
    MOVI  r0, 2
    PUSH  r0
    JMP   cmd
done:
    MOVI  r0, 0
    PUSH  r0
    JMP   cmd

; ---- /CS low (a fresh time base; uses r0), /CS high (uses r2)
csl:
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    EMIT  CS, 0, CS
    RET
csh:                         ; (after a PUSH perhaps: a fresh time base, after the last edge;
    WAITT                    ; r2, not r0: r0 holds what x4 read)
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMIT  CS, CS, CS
    ADDT  H                  ; /CS high for H at least (tSHSL is 50 ns)
    RET

; ---- 8 bits of r2[31:24] (Write Enable); the 32 bits of r2: back in r0. Each starts on a
; fresh time base (SPI lets the controller pause anywhere while /CS is low), at least H after
; what came before
x8:
    LDSH  r2
    LDLC  8
    JMP   xa
x4:
    LDSH  r2
    LDLC  32
xa:
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    ADDT  H
xb:
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; data out, a tick after the falling edge
    ADDT  H - 1
    EMIT  SCK, SCK, SCK      ; rising edge: the flash samples MOSI
    ADDT  1
    SAMPLE MISO_PIN, MSB
    ADDT  H - 1
    EMIT  SCK, 0, SCK        ; falling edge: the flash's next bit
    DJNZ  xb
    GETI  r0
    RET
