; SD card host in SPI mode (SD Association, Physical Layer Simplified Specification, ch. 7):
; mode 0, MSB first, SCK at SCK_KHZ (400 kHz: the identification-mode limit) on pins SCK,
; MOSI, MISO (input) and CS_n. After power-up: 80 clocks with CS_n high, CMD0 (into SPI
; mode), CMD8 (2.7-3.6 V, check pattern 0xAA echoed), ACMD41 with HCS until the card leaves
; the idle state, CMD58 (the OCR). Then, per host request, CMD17: one 512-byte block, its
; CRC-16 checked, the block in the tape.
;
; A command is 6 bytes: 0x40 | index, the 32-bit argument (most significant byte first),
; then CRC7 << 1 | 1. The chip computes CRC7 (x^7 + x^3 + 1) for every command, and the data
; block's CRC-16 (x^16 + x^12 + x^5 + 1, initial 0). A response comes within 8 bytes of 0xFF
; (Ncr); a data block after its start token 0xFE. Each command has CS_n low from one byte
; before it to its response (CMD17: to the end of the block), then CS_n high for 8 clocks.
;
; Chip to host (PUSH): after start-up, a status word, then (if good) the OCR; after each
; block, a status word. Status: [7:0] 0 = good, 1 CMD0, 2 CMD8 (or its echo), 3 ACMD41 (the
; card stayed idle), 4 CMD58, 5 CMD17, 6 no data token (or an error token), 7 a data CRC-16
; error; [15:8] the response byte (or token) that failed; [31:16] ACMD41 attempts (start-up).
; A start-up failure halts. Host to chip (POP): the CMD17 argument (a block number on
; SDHC/SDXC, OCR bit 30 set; a byte address on SDSC). The block lands in tape words
; TB..TB+127, least significant byte first.

.default CTX, 0
.default SCK_PIN, 4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default CS_PIN, 7
.default CLK_KHZ, 25000
.default SCK_KHZ, 400
.default TB, 0x40                           ; tape: the block (128 words), then scratch
.default TRIES, 1000                        ; ACMD41 attempts
.equ SCK, 1 << SCK_PIN
.equ MOSI, 1 << MOSI_PIN
.equ CS, 1 << CS_PIN
.equ H, (CLK_KHZ + 2 * SCK_KHZ - 1) // (2 * SCK_KHZ)
.equ LAT, 40                                ; the first event of a byte after its anchor
.equ ARG, TB + 128                          ; the command argument
.equ NEXT, TB + 129                         ; where docmd goes on to
.equ TRY, TB + 130                          ; ACMD41 attempts, and other counts
.equ CMDB, TB + 131                         ; the command's first byte
.equ FR, TB + 132                           ; a failure's response byte
.equ CT, TB + 132                           ; per step 1-6: the command index (CT + step)
.equ AT, TB + 138                           ; per step: its argument (step 6: the host's)
.equ FC, TB + 144                           ; per step: its failure code
; steps: 1 CMD0, 2 CMD8, 3 CMD55, 4 ACMD41 (HCS), 5 CMD58, 6 CMD17
.tape CT + 1, 0, 8, 55, 41, 58, 17
.tape AT + 1, 0, 0x1AA, 0, 0x40000000, 0
.tape FC + 1, 1, 2, 3, 3, 4, 5
.assert H >= 18, "the bit loop needs a half period of 18 ticks: escert finds a possible late edge at 17"
.assert TB + 151 <= 256, "the tape area does not fit"

.entry CTX, main
.own   SCK_PIN, CTX
.own   MOSI_PIN, CTX
.own   CS_PIN, CTX

main:
    ADDT  40
    EMIT  SCK|MOSI|CS, MOSI|CS, SCK|MOSI|CS    ; CS_n high, MOSI high, SCK low
    MOVI  r2, 10             ; 80 clocks with CS_n high (more than 74)
pw:
    MOVI  r0, 0xFF
    CALL  xfer
    MOVI  r3, 1
    SUB   r2, r3
    JNZ   pw
; ---- CMD0: back to idle, into SPI mode (R1 = 0x01)
    MOVI  r1, 1
    JMP   docmd
n_cmd0:
    CMPI  r0, 0x01
    JNZ   failn
    CALL  csoff
; ---- CMD8: 2.7-3.6 V (VHS 1), check pattern 0xAA; R7 = R1 and 4 bytes, echo 0x1AA
    MOVI  r1, 2
    JMP   docmd
n_cmd8:
    CMPI  r0, 0x01
    JNZ   failn
    MOVI  r2, 0
    MOVI  r3, 4              ; the 4 bytes after R1: voltage accepted [11:8], pattern [7:0]
r7:
    SHL   r2, 8
    MOVI  r0, 0xFF
    CALL  xfer
    OR    r2, r0
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   r7
    SHL   r2, 20
    SHR   r2, 20             ; 12 bits
    MOV   r0, r2
    CMPI  r2, 0x1AA
    JNZ   failn
    CALL  csoff
; ---- ACMD41 with HCS until R1 = 0x00, CMD55 before each
    MOVI  r3, 0
    STT   r3, r3, TRY
a41:
    MOVI  r1, 3
    JMP   docmd
n_cmd55:
    TSTI  r0, 0xFE           ; R1 0x01 (idle) or 0x00
    JNZ   failn
    CALL  csoff
    MOVI  r1, 4
    JMP   docmd
n_acmd41:
    MOVI  r3, 0
    LDT   r1, r3, TRY
    ADDI  r1, 1
    STT   r1, r3, TRY
    CMPI  r0, 0x00
    JZ    ready
    CMPI  r0, 0x01
    JNZ   failn
    CALL  csoff
    MOVI  r3, 0
    LDT   r1, r3, TRY
    CMPI  r1, TRIES
    JNZ   a41
    MOVI  r0, 0x01           ; still idle after TRIES attempts
    JMP   failn
; ---- CMD58: R3 = R1 and the OCR
ready:
    CALL  csoff
    MOVI  r1, 5
    JMP   docmd
n_cmd58:
    CMPI  r0, 0x00
    JNZ   failn
    MOVI  r2, 0
    MOVI  r3, 4
r3b:
    SHL   r2, 8
    MOVI  r0, 0xFF
    CALL  xfer
    OR    r2, r0
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   r3b                ; the OCR
    CALL  csoff
    MOVI  r3, 0
    LDT   r1, r3, TRY        ; good, with the ACMD41 attempts
    SHL   r1, 16
    PUSH  r1
    PUSH  r2
; ---- blocks: POP the argument, CMD17, the data
block:
    POP   r1
    MOVI  r3, 0
    STT   r1, r3, AT + 6     ; step 6's argument: the host's
    MOVI  r1, 6
    JMP   docmd
n_cmd17:
    CMPI  r0, 0x00
    JNZ   failn
    MOVI  r2, 2000           ; the start token within 2000 bytes
tok:
    MOVI  r0, 0xFF
    CALL  xfer
    CMPI  r0, 0xFF
    JNZ   gottok
    MOVI  r3, 1
    SUB   r2, r3
    JNZ   tok
gottok:
    CMPI  r0, 0xFE
    JNZ   f6
    MOVI  r1, 0              ; CRC-16
    MOVI  r2, 0              ; the word being packed
    MOVI  r3, 0              ; byte index
data:
    MOVI  r0, 0xFF
    CALL  xfer
    SHL   r0, 8
    XOR   r1, r0             ; crc ^= byte << 8
    SHL   r0, 16             ; the byte to [31:24]
    SHR   r2, 8
    OR    r2, r0             ; little endian: after four bytes the first is in [7:0]
    LDLC  8
c16:
    SHL   r1, 1
    MOV   r0, r1
    SHR   r0, 16
    JZ    c16n
    MOVI  r0, 0x1021
    XOR   r1, r0
c16n:
    SHL   r1, 16
    SHR   r1, 16
    DJNZ  c16
    MOV   r0, r3
    ADDI  r3, 1
    TSTI  r3, 3
    JNZ   data_n
    SHR   r0, 2
    STT   r2, r0, TB         ; a whole word
data_n:
    CMPI  r3, 512
    JNZ   data
    MOVI  r0, 0xFF
    CALL  xfer               ; the CRC-16, most significant byte first
    MOV   r2, r0
    SHL   r2, 8
    MOVI  r0, 0xFF
    CALL  xfer
    OR    r2, r0
    MOV   r0, r2
    CMP   r2, r1
    JNZ   f7
    CALL  csoff
    MOVI  r1, 0
    PUSH  r1                 ; good
    JMP   block

; ---- failures: r0 = the response; report it; a start-up failure halts
failn:                       ; the failing step's code
    MOVI  r3, 0
    LDT   r1, r3, NEXT
    LDT   r1, r1, FC
    JMP   fail
f6:
    MOVI  r1, 6
    JMP   fail
f7:
    MOVI  r1, 7
fail:
    MOV   r2, r0             ; (csoff keeps r1-r3)
    CALL  csoff
    MOV   r0, r2
    SHL   r0, 24
    SHR   r0, 16             ; the response in [15:8]
    OR    r0, r1
    PUSH  r0
    CMPI  r1, 5
    JC    stop               ; codes 1-4: start-up
    JMP   block
stop:
    HALT

; ---- step r1's command (index CT[r1], argument AT[r1]); R1 in r0, CS_n still low; then
; on to that step's handler
docmd:
    MOVI  r3, 0
    STT   r1, r3, NEXT
    LDT   r0, r1, CT
    ADDI  r0, 0x40
    STT   r0, r3, CMDB
    LDT   r2, r1, AT
    STT   r2, r3, ARG
    CALL  cson               ; CS_n low, one byte of 0xFF (r3 is still 0)
    LDT   r0, r3, CMDB       ; CRC7 over the first byte and the argument, into r1
    SHL   r0, 24
    MOVI  r1, 0
    LDLC  8
    CALL  crc7
    LDT   r0, r3, ARG
    LDLC  32
    CALL  crc7
    SHL   r1, 1
    ADDI  r1, 1              ; CRC7 << 1 | 1
    LDT   r0, r3, CMDB
    CALL  xfer
    LDT   r2, r3, ARG
    MOVI  r3, 4              ; the argument, most significant byte first (xfer keeps r1-r3)
ab:
    MOV   r0, r2
    SHR   r0, 24
    CALL  xfer
    SHL   r2, 8
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   ab
    MOV   r0, r1
    CALL  xfer               ; the CRC byte
    MOVI  r2, 8              ; R1 within 8 bytes (Ncr)
r1w:
    MOVI  r0, 0xFF
    CALL  xfer
    TSTI  r0, 0x80
    JZ    r1got
    MOVI  r3, 1
    SUB   r2, r3
    JNZ   r1w
r1got:
    MOVI  r3, 0
    LDT   r1, r3, NEXT
    CMPI  r1, 1
    JZ    n_cmd0
    CMPI  r1, 2
    JZ    n_cmd8
    CMPI  r1, 3
    JZ    n_cmd55
    CMPI  r1, 4
    JZ    n_acmd41
    CMPI  r1, 5
    JZ    n_cmd58
    JMP   n_cmd17

; ---- CRC7 (x^7 + x^3 + 1): shift lc bits of r0, from bit 31 down, through r1 (uses r2)
crc7:
    SHL   r0, 1
    JC    k1
    TSTI  r1, 0x40
    JZ    k0
    JMP   kf
k1:
    TSTI  r1, 0x40
    JNZ   k0
kf:
    SHL   r1, 1
    MOVI  r2, 0x09
    XOR   r1, r2
    JMP   km
k0:
    SHL   r1, 1
km:
    MOVI  r2, 0x7F
    AND   r1, r2
    DJNZ  crc7
    RET

; ---- one byte: out r0[7:0], in r0 (mode 0: MOSI set, SCK rises, MISO sampled, SCK
; falls); uses only r0, lc and the shift registers
xfer:
    SHL   r0, 24
    LDSH  r0
    WAITT                    ; whatever came before has gone out
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    LDLC  8
xb:                          ; T = the bit's start (the last bit's falling edge)
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; T+1: data out (one event per tick: not on the falling edge)
    ADDT  H - 1
    EMIT  SCK, SCK, SCK      ; T+H: rising edge
    ADDT  1
    SAMPLE MISO_PIN, MSB
    ADDT  H - 1
    EMIT  SCK, 0, SCK
    DJNZ  xb
    WAITT
    GETI  r0
    RET

; ---- CS_n low, then one byte of 0xFF / CS_n high, then one byte of 0xFF (8 clocks);
; the level comes in through sh's bit 31
cson:
    MOVI  r0, 0
    JMP   csx
csoff:
    MOVI  r0, 0
    MOVHI r0, 0x8000
csx:
    LDSH  r0
    WAITT
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    EMITX CS, MSB, CS
    ADDT  H
    MOVI  r0, 0xFF
    JMP   xfer
