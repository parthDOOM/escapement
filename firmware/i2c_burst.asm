; I2C frame burst: a generic I2C bulk writer, Fast-mode (fSCL <= 400 kHz at any CLK_KHZ from
; 23 MHz), with clock stretching. Made for a display's frame (an SSD1306 OLED takes its 128x64
; picture as 1024 bytes after a 0x40 control byte), but it knows nothing of any display: the
; host sends the display's commands, and the picture is the tape.
; Context 0, SCL = pin 2, SDA = pin 3, both open-drain (value 1 = release, 0 = drive low);
; the target's 7-bit address is ADDR (default 0x3C).
;
; Host protocol (mailboxes): POP a command word, run one write transaction, PUSH one result.
;   command [31] = 0: command bytes: [25:24] n = 1..3, then [23:16], [15:8], [7:0], in that order
;           [31] = 1, [30] = 0: tape burst: [23:16] a prefix byte, then the tape words [7:0]
;                     first .. [15:8] last (inclusive), each as 4 bytes, most significant first.
;                     All 256 words are one 128x64 frame: 1025 bytes, about 24 ms at 400 kHz.
;           [31] = 1, [30] = 1: stream: [23:16] a prefix byte, then [15:0] words (0 sends the
;                     prefix alone) taken from the mailbox as they come, each as 4 bytes, most
;                     significant first: a frame the host renders while the chip runs, since the
;                     host cannot write the tape then. A word not yet sent holds SCL low, which
;                     only lengthens a low phase (I2C sets no maximum); each byte re-anchors.
;   result  [31] = 1 if a byte was NACKed: the transaction stops with a STOP right after it;
;           [15:0] the number of bytes after the address that the target ACKed.
; A stuck SCL (no rise within 100 us) raises IRQ 0xEE, releases the bus and halts.
;
; Bit timing is i2c_ctrl.asm's, unchanged (every edge an event at an absolute deadline, the high
; phase measured from the observed SCL rise, a re-anchor after variable-length code that only
; ever lengthens an SCL low phase); see i2c_ctrl.asm for the derivation of each constant. The
; one change: write_byte returns the ACK in Z (1 = ACK) instead of shifting it into a register,
; so a burst can count 1025 bytes and stop at the first NACK.
; The tape word is read again (LDT) for each of its four bytes: write_byte needs r0, r1, sh and
; lc, and r3 holds the burst's place ([7:0] the word, [15:8] the last word). A streamed word is
; kept the same way, in tape word STW, with r3 = the words left << 8 (so r3[7:0], LDT's base,
; is 0).

.default CTX,     0         ; module parameters
.default SCL_PIN, 2
.default SDA_PIN, 3
.default ADDR,    0x3C
.default STW,     0xFF      ; tape: the word being streamed
.equ SCL,     1 << SCL_PIN
.equ SDA,     1 << SDA_PIN
.equ BOTH,    SCL | SDA
.default CLK_KHZ, 25000
.assert CLK_KHZ >= 23000, Fast-mode at 400 kHz needs about 23 MHz (as i2c_ctrl.asm)
.assert ADDR < 128, ADDR is a 7-bit address
.equ T_DAT,   (400 * CLK_KHZ + 999999) // 1000000     ; 400 ns
.equ T_HI,    (800 * CLK_KHZ + 999999) // 1000000     ; 0.8 us
.equ T_LOW0,  (1440 * CLK_KHZ + 999999) // 1000000    ; 1.44 us
.equ T_PER,   (2500 * CLK_KHZ + 999999) // 1000000    ; 2.5 us: 400 kHz
.equ T_LOW,   T_LOW0 + (T_PER - T_HI - 7 - T_LOW0) * (T_PER - T_HI - 7 > T_LOW0)
.equ T_HDSTA, (800 * CLK_KHZ + 999999) // 1000000     ; tHD;STA 0.8 us
.equ T_BUF,   (1600 * CLK_KHZ + 999999) // 1000000    ; tBUF 1.6 us
.equ TO,      100 * CLK_KHZ // 1000                   ; 100 us stretch/stuck timeout
.equ RA,      12             ; re-anchor offset (instruction time, in ticks)

.entry CTX, main
.own   SCL_PIN, CTX
.own   SDA_PIN, CTX
.od    SCL_PIN
.od    SDA_PIN

main:
    ADDT  40
    EMIT  BOTH, BOTH, BOTH   ; bus idle: release SCL and SDA
loop:
    POP   r3                 ; wait for a command
    MOVI  r2, 0              ; bytes ACKed
    RDTIME r1
    ADDI  r1, T_BUF          ; >= tBUF after the previous STOP
    SETT  r1
    ; ---- START: SDA falls while SCL is high, then SCL falls (tHD;STA 0.8 us)
    EMIT  SDA, 0, SDA
    ADDT  T_HDSTA
    EMIT  SCL, 0, SCL
    MOVI  r0, ADDR << 1      ; address + W
    CALL  write_byte
    JNZ   nack
    MOV   r0, r3
    SHL   r0, 1              ; C = bit 31
    JC    burst
    ; ---- command bytes: [23:16], then [15:8] if n >= 2, then [7:0] if n = 3
    MOV   r0, r3
    SHR   r0, 16
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    MOV   r0, r3
    SHR   r0, 24
    CMPI  r0, 2
    JC    stop               ; n < 2
    MOV   r0, r3
    SHR   r0, 8
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    MOV   r0, r3
    SHR   r0, 24
    CMPI  r0, 3
    JC    stop               ; n < 3
    MOV   r0, r3
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    JMP   stop
    ; ---- tape burst: the prefix byte, then words first..last, 4 bytes each
burst:
    MOV   r0, r3
    SHR   r0, 16
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    MOV   r0, r3
    SHL   r0, 2              ; C = bit 30
    JC    stream
    SHL   r3, 16
    SHR   r3, 16             ; r3 = [15:8] last, [7:0] this word
word:
    LDT   r0, r3, 0
    SHR   r0, 24
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    LDT   r0, r3, 0
    SHR   r0, 16
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    LDT   r0, r3, 0
    SHR   r0, 8
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    LDT   r0, r3, 0
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    MOV   r0, r3
    SHR   r0, 8              ; the last word
    MOV   r1, r3
    SHL   r1, 24
    SHR   r1, 24             ; this word
    CMP   r0, r1
    JZ    stop
    ADDI  r3, 1
    JMP   word
    ; ---- stream: [15:0] words from the mailbox, 4 bytes each
stream:
    SHL   r3, 16
    SHR   r3, 8              ; r3 = the words left << 8; Z: none
    JZ    stop
sword:
    POP   r0                 ; the next word, when the host sends it (SCL held low meanwhile)
    STT   r0, r3, STW
    SHR   r0, 24
    CALL  write_byte
    JNZ   snack
    ADDI  r2, 1
    LDT   r0, r3, STW
    SHR   r0, 16
    CALL  write_byte
    JNZ   snack
    ADDI  r2, 1
    LDT   r0, r3, STW
    SHR   r0, 8
    CALL  write_byte
    JNZ   snack
    ADDI  r2, 1
    LDT   r0, r3, STW
    CALL  write_byte
    JNZ   snack
    ADDI  r2, 1
    MOVI  r1, 256
    SUB   r3, r1             ; one word fewer; Z: the last was sent
    JNZ   sword
    JMP   stop
nack:
    MOVHI r2, 0x8000         ; [31] NACK, [15:0] kept: the bytes ACKed before it
stop:
    CALL  bus_stop
    PUSH  r2
    JMP   loop
    ; ---- a stream NACKed: STOP, then take the words the host still sends for it, so the
    ; mailbox stays in step with the commands (r3 counts the word already taken)
snack:
    MOVHI r2, 0x8000         ; [31] NACK, [15:0] the bytes ACKed before it
    CALL  bus_stop
sdrop:
    MOVI  r1, 256
    SUB   r3, r1
    JZ    sdone
    POP   r0
    JMP   sdrop
sdone:
    PUSH  r2
    JMP   loop

; bus_stop: STOP: SDA low while SCL low, release SCL, then release SDA. Uses r0.
bus_stop:
    WAITT                    ; the scheduled SCL fall has happened
    RDTIME r0
    ADDI  r0, RA
    SETT  r0
    ADDT  T_DAT
    EMIT  SDA, 0, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    WAITT                    ; SCL released: watch for its rise from here
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r0
    ADDI  r0, T_HI           ; tSU;STO 0.8 us
    SETT  r0
    EMIT  SDA, SDA, SDA
    RET

; write_byte: send r0[7:0] MSB first, then the 9th clock; Z = 1 if the target ACKed.
; On entry tmr = the tick SCL falls (scheduled); on exit likewise for the next bit.
; Uses r0, r1, sh, lc; keeps r2 and r3.
write_byte:
    MOV   r1, r0
    SHL   r1, 24
    LDSH  r1
    LDLC  8
    WAITT                    ; the scheduled SCL fall has happened
    RDTIME r1
    ADDI  r1, RA
    SETT  r1
wb_bit:
    ADDT  T_DAT
    EMITX SDA, MSB, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    WAITT                    ; SCL released: watch for its rise from here
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, T_HI
    SETT  r1
    EMIT  SCL, 0, SCL
    DJNZ  wb_bit
    ADDT  T_DAT              ; 9th clock: release SDA, target drives ACK
    EMIT  SDA, SDA, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    WAITT                    ; SCL released: watch for its rise from here
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    GETP  r1                 ; SDA in the high phase of the 9th clock: 0 = ACK
    RDTIME r0
    ADDI  r0, T_HI
    SETT  r0
    EMIT  SCL, 0, SCL
    TSTI  r1, SDA            ; Z = 1: ACK
    RET

timeout:
    IRQ   0xEE
    RDTIME r1
    ADDI  r1, 14                ; EMIT commits 12 ticks after RDTIME: lead 2 (as i2c_ctrl.asm)
    SETT  r1
    EMIT  BOTH, BOTH, BOTH
    HALT
