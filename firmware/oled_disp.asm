; SSD1306 display engine: draws what another context asks for, from a command queue on the
; tape, on a 128x64 SSD1306 OLED over I2C (Fast-mode, 400 kHz). The display's own RAM is the
; framebuffer: a command rewrites one rectangle, so a game redraws only what moved.
; Context 0, bank A, page 0 of the program store; SCL = pin 2, SDA = pin 3 (open drain), the
; display at 7-bit address ADDR. It knows nothing of what it draws: oled_text.asm (context 2)
; feeds it, turning text into strips, for oled_pong.asm (context 1).
;
; Queue: DQ[0..7] on the tape, DQH (the next to draw, written here) and DQT (the next free,
; written by the one producer); DQH == DQT is empty. One word per command, [31:28] the operation:
;   1 FILL   [27:25] p0, [24:22] p1, [7:0] v       pages p0..p1, all 128 columns, every byte v
;   2 STRIP  [27:25] p0, [24:22] p1, [21:15] x0,    columns x0..x1, pages p0..p1, every column
;            [14:8] x1, [7:0] a                     the same bytes: tape word a ([7:0] first),
;                                                   then word a + 1
;   4 INIT   the controller's set-up (INITB): vertical addressing, display on
; DQH moves on only when a command is drawn, so DQH == DQT also means "all drawn". A byte the
; display does not ACK ends the command (NACKS counts them); the next one is tried as usual.
;
; Bit timing is i2c_ctrl.asm's, as in i2c_burst.asm: every edge an event at an absolute
; deadline, the high phase measured from the observed SCL rise, and a re-anchor after variable
; code that only ever lengthens an SCL low phase, so the work between bytes costs time, never a
; timing violation. One addition: STOP waits for its own SDA release, so the next START, tBUF
; after RDTIME, is tBUF after the STOP however soon it comes.

.default SCL_PIN, 2
.default SDA_PIN, 3
.default ADDR,    0x3C
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

; ---- the tape (DQ, DQH, DQT are shared with oled_text.asm; test_oled_pong checks they agree)
.equ DQ,     40              ; the queue, 8 words
.equ DQH,    48
.equ DQT,    49
.equ WIN,    50              ; one transaction: 0x00, 0x21, x0, x1, 0x22, p0, p1
.equ D_X0,   WIN + 2
.equ D_X1,   WIN + 3
.equ D_P0,   WIN + 5
.equ D_P1,   WIN + 6
.equ INITB,  57              ; 0x00, then the 25 set-up bytes, one per word
.equ INITE,  INITB + 26
.equ D_CMD,  83              ; scratch
.equ D_ARG,  84
.equ D_END,  85
.equ D_CL,   86
.equ D_B0,   87
.equ D_B1,   88
.equ NACKS,  89

.tape WIN, 0x00, 0x21, 0, 127, 0x22, 0, 7
; 128x64 set-up (the datasheet's application example, as Adafruit_SSD1306 sends it), but
; vertical addressing (0x20, 0x01): a window's bytes go down a column, page by page
.tape INITB, 0x00, 0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x01
.tape INITB + 13, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF

.entry 0, main
.own   SCL_PIN, 0
.own   SDA_PIN, 0
.od    SCL_PIN
.od    SDA_PIN

main:
    ADDT  40
    EMIT  BOTH, BOTH, BOTH   ; bus idle: release SCL and SDA
    MOVI  r3, 0              ; r3 = 0: the base of every fixed tape address
idle:
    LDT   r0, r3, DQH
    LDT   r1, r3, DQT
    CMP   r0, r1
    JZ    idle               ; nothing queued
    LDT   r1, r0, DQ         ; the command at the head
    STT   r1, r3, D_CMD
    MOV   r0, r1
    SHL   r0, 4
    SHR   r0, 29
    STT   r0, r3, D_P0
    MOV   r0, r1
    SHL   r0, 7
    SHR   r0, 29
    STT   r0, r3, D_P1
    MOV   r0, r1
    SHL   r0, 10
    SHR   r0, 25
    STT   r0, r3, D_X0
    MOV   r0, r1
    SHL   r0, 17
    SHR   r0, 25
    STT   r0, r3, D_X1
    MOV   r0, r1
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r3, D_ARG
    SHR   r1, 28             ; the operation
    MOVI  r2, INITB          ; INIT: the set-up bytes are the whole command
    MOVI  r0, INITE
    CMPI  r1, 4
    JZ    seq
    CMPI  r1, 2
    JZ    window             ; STRIP: its own columns
    MOVI  r0, 0              ; FILL: every column
    STT   r0, r3, D_X0
    MOVI  r0, 127
    STT   r0, r3, D_X1
    ; ---- the window: one transaction 0x00, 0x21, x0, x1, 0x22, p0, p1
window:
    MOVI  r2, WIN
    MOVI  r0, WIN + 7
seq:                         ; START, then tape words r2 .. r0 - 1 as bytes, then STOP
    STT   r0, r3, D_END
    CALL  start
    JNZ   nack
s_next:
    LDT   r0, r2, 0
    CALL  write_byte
    JNZ   nack
    ADDI  r2, 1
    LDT   r0, r3, D_END
    CMP   r2, r0
    JNZ   s_next
    CALL  stop
    LDT   r1, r3, D_CMD
    SHR   r1, 28
    CMPI  r1, 4
    JZ    next               ; INIT: done
    ; ---- the data: 0x40, then the window's bytes, column by column, page by page
    CALL  start
    JNZ   nack
    MOVI  r0, 0x40
    CALL  write_byte
    JNZ   nack
    LDT   r2, r3, D_P1
    LDT   r0, r3, D_P0
    SUB   r2, r0
    ADDI  r2, 1              ; r2 = the pages: the bytes of one column
    LDT   r1, r3, D_CMD      ; (write_byte took r1)
    SHR   r1, 28
    CMPI  r1, 2
    JZ    strip
    SHL   r2, 7              ; FILL: 128 columns
f_byte:
    LDT   r0, r3, D_ARG
    CALL  write_byte
    JNZ   nack
    MOVI  r1, 1
    SUB   r2, r1
    JNZ   f_byte
    JMP   done
strip:
    LDT   r0, r3, D_X1
    LDT   r1, r3, D_X0
    SUB   r0, r1
    ADDI  r0, 1
    STT   r0, r3, D_CL       ; columns
    STT   r2, r3, D_END      ; bytes per column
s_col:
    LDT   r1, r3, D_ARG
    LDT   r0, r1, 0
    STT   r0, r3, D_B0
    LDT   r0, r1, 1
    STT   r0, r3, D_B1
    LDT   r2, r3, D_END
s_byte:
    LDT   r0, r3, D_B0
    CALL  write_byte
    JNZ   nack
    LDT   r0, r3, D_B0       ; the 64 bits B1:B0 move down a byte
    SHR   r0, 8
    LDT   r1, r3, D_B1
    SHL   r1, 24
    OR    r0, r1
    STT   r0, r3, D_B0
    LDT   r1, r3, D_B1
    SHR   r1, 8
    STT   r1, r3, D_B1
    MOVI  r1, 1
    SUB   r2, r1
    JNZ   s_byte
    LDT   r0, r3, D_CL
    MOVI  r1, 1
    SUB   r0, r1
    STT   r0, r3, D_CL
    JNZ   s_col
    JMP   done

nack:
    LDT   r0, r3, NACKS
    ADDI  r0, 1
    STT   r0, r3, NACKS
done:
    CALL  stop
next:
    LDT   r0, r3, DQH
    ADDI  r0, 1
    MOVI  r1, 7
    AND   r0, r1
    STT   r0, r3, DQH        ; drawn
    JMP   idle

; start: START, then the address byte; Z = 1 if ACKed. Ends as write_byte (tmr = SCL fall).
start:
    RDTIME r1
    ADDI  r1, T_BUF          ; tBUF after the last STOP's release (STOP waited for it)
    SETT  r1
    EMIT  SDA, 0, SDA        ; SDA falls while SCL is high
    ADDT  T_HDSTA
    EMIT  SCL, 0, SCL
    MOVI  r0, ADDR << 1      ; address + W
    JMP   write_byte         ; its RET returns to start's caller

; stop: SDA low while SCL low, release SCL, then release SDA, and wait for that release.
stop:
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
    WAITT                    ; the bus is free from here
    RET

; write_byte: send r0[7:0] MSB first, then the 9th clock; Z = 1 if the target ACKed.
; On entry tmr = the tick SCL falls (scheduled); on exit likewise for the next bit.
; Uses r0, r1, sh, lc; keeps r2 and r3. (i2c_burst.asm's, unchanged)
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
    ADDI  r1, 14             ; EMIT commits 12 ticks after RDTIME: lead 2 (as i2c_ctrl.asm)
    SETT  r1
    EMIT  BOTH, BOTH, BOTH
    HALT
