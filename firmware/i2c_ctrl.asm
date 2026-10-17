; I2C controller, Fast-mode (~380-395 kHz at a 25 MHz core clock), with clock stretching.
; Context 0, SCL = pin 2, SDA = pin 3, both open-drain (value 1 = release, 0 = drive low).
;
; Host protocol (mailboxes): POP a command word, PUSH one result word per command.
;   command [30:24] 7-bit address, [23:16] register, [15:8] write data,
;           [0] write the data byte after the register, [1] then read one byte back
;           (repeated START, address+R, read with NACK).
;   result  [15:8] ACK bits, one per byte sent, oldest first (1 = NACK), [7:0] read data
; A stuck SCL (no rise within 100 us) raises IRQ 0xEE, releases the bus and halts.
;
; Timing per bit (ticks of 40 ns), every edge an event at an absolute deadline:
;   SCL falls at F; SDA changes at F+10 (tHD;DAT 400 ns, tSU;DAT 1.04 us);
;   SCL released at F+36 (tLOW 1.44 us >= 1.3 us); the high phase is measured from
;   the *observed* SCL rise (stretch-safe), >= 20 ticks (tHIGH 0.8 us >= 0.6 us).
;   Worst-case period 63-66 ticks => fSCL <= 397 kHz. Read bits use SAMPLE events
;   at an exact tick inside the high phase.
;   After any variable-length code (call/return, flag tests) the bit timeline is
;   re-anchored to `now + 12` (REANCHOR): that only lengthens an SCL low phase, which
;   UM10204 allows, and it is always after the scheduled SCL fall (>= 3 instructions
;   = 12 ticks have passed since the fall was scheduled 20 ticks after its RDTIME).
;   Found by the ISS: without it the first data bit of a byte missed its deadline
;   and the hardware rejected it (ADMIT/PAST) instead of emitting a late edge.

.default CTX,     0         ; module parameters
.default SCL_PIN, 2
.default SDA_PIN, 3
.equ SCL,     1 << SCL_PIN
.equ SDA,     1 << SDA_PIN
.equ BOTH,    SCL | SDA
.equ T_DAT,   10
.equ T_LOW,   36
.equ T_HI,    20
.equ TO,      2500           ; 100 us stretch/stuck timeout
.equ RA,      12             ; re-anchor offset

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
    MOVI  r2, 0              ; ACK accumulator
    RDTIME r1
    ADDI  r1, 40             ; >= tBUF after the previous STOP
    SETT  r1
    ; ---- START: SDA falls while SCL is high, then SCL falls (tHD;STA 0.8 us)
    EMIT  SDA, 0, SDA
    ADDT  20
    EMIT  SCL, 0, SCL
    ; ---- address + W, register
    MOV   r0, r3
    SHR   r0, 24
    SHL   r0, 1
    CALL  write_byte
    MOV   r0, r3
    SHR   r0, 16
    CALL  write_byte
    TSTI  r3, 1
    JZ    no_wdata
    MOV   r0, r3
    SHR   r0, 8
    CALL  write_byte
no_wdata:
    MOVI  r1, 0
    TSTI  r3, 2
    JZ    stop
    ; ---- repeated START
    RDTIME r0
    ADDI  r0, RA
    SETT  r0
    ADDT  T_DAT
    EMIT  SDA, SDA, SDA      ; release SDA while SCL is low
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL      ; release SCL
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, T_HI           ; tSU;STA 0.8 us
    SETT  r1
    EMIT  SDA, 0, SDA
    ADDT  20                 ; tHD;STA 0.8 us
    EMIT  SCL, 0, SCL
    ; ---- address + R, one byte with NACK
    MOV   r0, r3
    SHR   r0, 24
    SHL   r0, 1
    ADDI  r0, 1
    CALL  write_byte
    MOVI  r0, SDA            ; NACK: release SDA in the 9th clock
    CALL  read_byte          ; -> r1
stop:
    ; ---- STOP: SDA low while SCL low, release SCL, then release SDA
    RDTIME r0
    ADDI  r0, RA
    SETT  r0
    ADDT  T_DAT
    EMIT  SDA, 0, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r0
    ADDI  r0, T_HI           ; tSU;STO 0.8 us
    SETT  r0
    EMIT  SDA, SDA, SDA
    SHL   r2, 8
    OR    r2, r1
    PUSH  r2
    JMP   loop

; write_byte: send r0[7:0] MSB first, then read ACK into r2 (r2 = r2<<1 | NACK).
; On entry tmr = the tick SCL falls (scheduled); on exit likewise for the next bit.
write_byte:
    MOV   r1, r0
    SHL   r1, 24
    LDSH  r1
    LDLC  8
    RDTIME r1
    ADDI  r1, RA
    SETT  r1
wb_bit:
    ADDT  T_DAT
    EMITX SDA, MSB, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
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
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    GETP  r1
    SHL   r2, 1
    TSTI  r1, SDA
    JZ    wb_ack
    ADDI  r2, 1              ; NACK
wb_ack:
    RDTIME r1
    ADDI  r1, T_HI
    SETT  r1
    EMIT  SCL, 0, SCL
    RET

; read_byte: receive 8 bits MSB first into r1, then send r0 in the 9th clock
; (r0 = 0 for ACK, SDA for NACK). SDA is sampled by a SAMPLE event 16 ticks after
; the observed SCL rise.
read_byte:
    LDLC  8
    RDTIME r1
    ADDI  r1, RA
    SETT  r1
rb_bit:
    ADDT  T_DAT
    EMIT  SDA, SDA, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, 16
    SETT  r1
    SAMPLE SDA_PIN, MSB
    ADDT  8
    EMIT  SCL, 0, SCL
    DJNZ  rb_bit
    ADDT  T_DAT
    EMITR SDA, r0, SDA       ; ACK/NACK
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, T_HI
    SETT  r1
    EMIT  SCL, 0, SCL
    GETI  r1
    RET

timeout:
    IRQ   0xEE
    RDTIME r1
    ADDI  r1, 14                ; EMIT commits 12 ticks after RDTIME: lead 2. (Was 8:
    SETT  r1                    ; lead -4, a refused edge; found by escert, 2026-10-13.)
    EMIT  BOTH, BOTH, BOTH
    HALT
