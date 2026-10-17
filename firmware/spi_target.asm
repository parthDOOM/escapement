; SPI target: a 256-byte serial memory in the style of the 25xx/23xx parts, so the chip
; is a peripheral on someone else's SPI bus. The memory is the TAPE, one byte per tape
; word in bits 7:0 (preload with .tape or WRITE_TAPE, read back with READ_TAPE).
; Context 2: SCK pin 4, MOSI pin 5 and CS_n pin 7 are inputs. MISO pin 6 is push-pull and
; is driven only while a READ returns data; otherwise it is released, as on a real part.
;
; Frames (MSB first, one address byte, the address wraps from 255 to 0):
;   0x03 addr            READ: the bytes at addr, addr+1, ... until CS_n rises
;   0x02 addr d0 d1 ...  WRITE: each complete byte is stored as it arrives; a byte cut
;                        short by CS_n rising is dropped
;   anything else        ignored until CS_n rises
; SPI modes 0 and 3, with nothing to configure: MOSI is read on the rising edge, and the
; next MISO bit goes out just after it, so MISO is ready for the next rising edge
; whichever level SCK idles at. Modes 1 and 2 (falling-edge sampling) are not supported.
;
; Timing at 25 MHz (1 tick = 40 ns). SPI has no clock stretching, so every bit must be
; finished before the next one starts. Each bit is two polls of the synchronised pins
; (inputs lag the pads by 2 ticks):
;   lo  SCK low with CS_n low: GETP, TSTI, JZ (one pass if SCK is already low, else a
;       20-tick loop in lo_wait). CS_n high here is the end of the frame.
;   hi  SCK high or CS_n high: a 12-tick GETP loop, so a rising edge is seen 2..13 ticks
;       after it reaches the pad, and MOSI is read from the same snapshot.
; From that snapshot to the next hi poll no bit takes more than 15 instructions (60 ticks:
; READ address bit 0, and the first and last bit of each READ data byte), and the next
; MISO bit reaches the pad at most 55 ticks after the rising edge (READ address bit 0; 51
; for the other bits) and no sooner than 36. Hence:
;   SCK period >= 60 ticks (2.4 us, 416 kHz); SCK high >= 12 ticks, low >= 24 ticks;
;   MISO is set up >= (period - 55) ticks before each rising edge, held >= 36 after it.
;   CS_n low to the first SCK edge >= 1 us; CS_n high >= 4 us between frames.
; MISO is released at most 4 us after CS_n rises (a real part takes about 100 ns).
;
; Registers: r0 command, then address (its low 8 bits address the tape); r1 pin
; snapshot; r2 time, candidate byte, WRITE data byte; r3 candidate byte, next READ byte.

.default CTX,      2        ; module parameters
.default SCK_PIN,  4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default CS_PIN,   7
.equ SCK,   1 << SCK_PIN
.equ MOSI,  1 << MOSI_PIN
.equ MISO,  1 << MISO_PIN
.equ CS,    1 << CS_PIN
.equ READ,  0x03
.equ WRITE, 0x02
.equ LAT,   13              ; RDTIME..EMIT is 12 ticks: deadline lead 1
.equ FAR,   0xFFFFFF

.entry CTX, main
.own   MISO_PIN, CTX

main:                       ; skip a frame already under way at start, and ignored frames
    RDTIME r2
    SETT  r2
    ADDT  FAR
    WAITP CS, CS
    JNZ   main
idle:                       ; CS_n rose and MISO is not driven
    RDTIME r2
    SETT  r2
    JMP   arm
deselect:                   ; CS_n rose while MISO may be driven: release it
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMIT  MISO, 0, 0
arm:
    ADDT  FAR               ; tmr far ahead, so that the wait for CS_n low is exact
    MOVI  r0, 1             ; the command bit counter
selwait:
    WAITP CS, 0
    JZ    cmd_lo
    RDTIME r2               ; 0.67 s without a frame
    SETT  r2
    JMP   arm

; ---- lo poll, slow path (shared): entered with r1 showing SCK or CS_n high. Waits for
; SCK low with CS_n low and returns to the hi poll that follows the call.
lo_wait:
    TSTI  r1, CS
    JNZ   deselect
    GETP  r1
    TSTI  r1, SCK | CS
    JNZ   lo_wait
    RET

; ---- command bits 7..1 into r0, until the counter bit reaches bit 7
cmd_lo:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    cmd_hi
    CALL  lo_wait
cmd_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    cmd_hi
    TSTI  r1, CS
    JNZ   idle
    SHL   r0, 1
    TSTI  r1, MOSI
    JZ    cmd_0
    ADDI  r0, 1
cmd_0:
    TSTI  r0, 0x80
    JZ    cmd_lo
; ---- command bit 0: READ (0x03) and WRITE (0x02) differ only here
cmd8_lo:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    cmd8_hi
    CALL  lo_wait
cmd8_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    cmd8_hi
    TSTI  r1, CS
    JNZ   idle
    TSTI  r1, MOSI
    JZ    write
    CMPI  r0, 0x80 | (READ >> 1)
    JNZ   main              ; any other command: ignore the frame
    LDLC  7

; ---- READ: address bits 7..1, then load the two candidates for address bit 0, so the
; first data bit can go out right after the last address bit
ra_lo:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    ra_hi
    CALL  lo_wait
ra_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    ra_hi
    SHL   r0, 1
    TSTI  r1, MOSI
    JZ    ra_0
    ADDI  r0, 1
ra_0:
    DJNZ  ra_lo
    SHL   r0, 1             ; r0 = address with bit 0 clear
    LDT   r2, r0, 0         ; the first data byte if bit 0 is 0
    LDT   r3, r0, 1         ; and if it is 1
ra8_lo:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    ra8_hi
    CALL  lo_wait
ra8_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    ra8_hi
    TSTI  r1, MOSI
    JNZ   ra8_1
    SHL   r2, 24
    LDSH  r2
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMITX MISO, MSB, MISO   ; data bit 7: MISO is driven from here until CS_n rises
    JMP   rd_b1
ra8_1:
    SHL   r3, 24
    LDSH  r3
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMITX MISO, MSB, MISO
    ADDI  r0, 1

; ---- READ data: sh = the byte going out, r0 = its address, r3 = the next byte << 24.
; After each rising edge the next bit goes out. The per-byte work (fetch the next byte,
; load it, count) is split between the byte's first and last bit, so that neither is
; longer than the address bit 0 above.
rd_b1:                      ; bit 6 out; fetch the next byte
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_b1_hi
    CALL  lo_wait
rd_b1_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_b1_hi
    TSTI  r1, CS
    JNZ   deselect
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMITX MISO, MSB, MISO
    LDT   r3, r0, 1
    SHL   r3, 24
    LDLC  6
rd_bit:                     ; bits 5..0 out
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_bit_hi
    CALL  lo_wait
rd_bit_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_bit_hi
    TSTI  r1, CS
    JNZ   deselect
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMITX MISO, MSB, MISO
    DJNZ  rd_bit
rd_b8:                      ; bit 7 of the next byte out
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_b8_hi
    CALL  lo_wait
rd_b8_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    rd_b8_hi
    TSTI  r1, CS
    JNZ   deselect
    LDSH  r3
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMITX MISO, MSB, MISO
    ADDI  r0, 1
    JMP   rd_b1

; ---- WRITE: the address into r0, then data bytes into r2, each stored on its 8th edge
write:
    CMPI  r0, 0x80 | (WRITE >> 1)
    JNZ   main
    LDLC  8
wa_lo:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wa_hi
    CALL  lo_wait
wa_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wa_hi
    TSTI  r1, CS
    JNZ   idle
    SHL   r0, 1
    TSTI  r1, MOSI
    JZ    wa_0
    ADDI  r0, 1
wa_0:
    DJNZ  wa_lo
    JMP   wd_b1
wd_b8:                      ; data bit 0: store the byte, unless CS_n cut it short
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_b8_hi
    CALL  lo_wait
wd_b8_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_b8_hi
    TSTI  r1, CS
    JNZ   idle
    SHL   r2, 1
    TSTI  r1, MOSI
    JZ    wd_b8_0
    ADDI  r2, 1
wd_b8_0:
    STT   r2, r0, 0
    ADDI  r0, 1
wd_b1:                      ; data bit 7 starts a new byte
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_b1_hi
    CALL  lo_wait
wd_b1_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_b1_hi
    TSTI  r1, CS
    JNZ   idle
    MOVI  r2, 0
    TSTI  r1, MOSI
    JZ    wd_b1_0
    ADDI  r2, 1
wd_b1_0:
    LDLC  6
wd_bit:                     ; data bits 6..1
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_bit_hi
    CALL  lo_wait
wd_bit_hi:
    GETP  r1
    TSTI  r1, SCK | CS
    JZ    wd_bit_hi
    TSTI  r1, CS
    JNZ   idle
    SHL   r2, 1
    TSTI  r1, MOSI
    JZ    wd_bit_0
    ADDI  r2, 1
wd_bit_0:
    DJNZ  wd_bit
    JMP   wd_b8
