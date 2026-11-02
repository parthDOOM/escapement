; I2C controller, Fast-mode (fSCL <= 400 kHz at any CLK_KHZ from 23 MHz), with clock stretching.
; Context 0, SCL = pin 2, SDA = pin 3, both open-drain (value 1 = release, 0 = drive low).
;
; Host protocol (mailboxes): POP a command word, PUSH one result word per command.
;   command [30:24] 7-bit address, [23:16] register, [15:8] write data,
;           [0] write the data byte after the register, [1] then read one byte back
;           (repeated START, address+R, read with NACK).
;   result  [15:8] ACK bits, one per byte sent, oldest first (1 = NACK), [7:0] read data
; A stuck SCL (no rise within 100 us) raises IRQ 0xEE, releases the bus and halts.
;
; Timing per bit, every edge an event at an absolute deadline (ticks at 25 MHz, 40 ns):
;   SCL falls at F; SDA changes at F+T_DAT (10: tHD;DAT 400 ns, tSU;DAT 1.04 us);
;   SCL released at F+T_LOW (36: tLOW 1.44 us >= 1.3 us); the high phase is measured
;   from the *observed* SCL rise (stretch-safe), >= T_HI (20: tHIGH 0.8 us >= 0.6 us).
;   Seeing the rise and scheduling the fall adds at least 7 ticks, so the period is at
;   least T_LOW + T_HI + 7 (63 at 25 MHz): T_LOW is lengthened where needed to keep that
;   at 2.5 us or more (fSCL <= 400 kHz) at any clock, since 7 ticks is less time at a
;   faster one (at 50 MHz T_LOW is 78). Read bits use SAMPLE events at an exact tick
;   inside the high phase.
;   After any variable-length code (call/return, flag tests) the bit timeline is
;   re-anchored to `now + RA` once the scheduled SCL fall has happened (WAITT first):
;   that only lengthens an SCL low phase, which UM10204 allows, and never shortens one,
;   whatever the clock. (v1.5's program, fixed at 25 MHz, relied on the fall having
;   passed by tick count alone: true with its constants, but rebuilt from CLK_KHZ for
;   33 MHz that would cut tLOW short.)
;   Found by the ISS: without a re-anchor the first data bit of a byte missed its
;   deadline and the hardware rejected it (ADMIT/PAST) instead of emitting a late edge.
;   The rise of SCL is watched for only once its release is due (WAITT), so every edge
;   scheduled from the observed rise follows the release in the FIFO at any clock.

.default CTX,     0         ; module parameters
.default SCL_PIN, 2
.default SDA_PIN, 3
.equ SCL,     1 << SCL_PIN
.equ SDA,     1 << SDA_PIN
.equ BOTH,    SCL | SDA
.default CLK_KHZ, 25000
.assert CLK_KHZ >= 23000, Fast-mode at 400 kHz needs about 23 MHz (escert certifies it from 22917 kHz)
.equ T_DAT,   (400 * CLK_KHZ + 999999) // 1000000     ; 400 ns
.equ T_HI,    (800 * CLK_KHZ + 999999) // 1000000     ; 0.8 us
.equ T_LOW0,  (1440 * CLK_KHZ + 999999) // 1000000    ; 1.44 us
.equ T_PER,   (2500 * CLK_KHZ + 999999) // 1000000    ; 2.5 us: 400 kHz
.equ T_LOW,   T_LOW0 + (T_PER - T_HI - 7 - T_LOW0) * (T_PER - T_HI - 7 > T_LOW0)
.equ T_HDSTA, (800 * CLK_KHZ + 999999) // 1000000     ; tHD;STA 0.8 us
.equ T_BUF,   (1600 * CLK_KHZ + 999999) // 1000000    ; tBUF 1.6 us
.equ T_SMP,   (640 * CLK_KHZ + 999999) // 1000000     ; read sample, 0.64 us into the high phase
.equ T_RHI,   (960 * CLK_KHZ + 999999) // 1000000     ; read high phase 0.96 us
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
    MOVI  r2, 0              ; ACK accumulator
    RDTIME r1
    ADDI  r1, T_BUF          ; >= tBUF after the previous STOP
    SETT  r1
    ; ---- START: SDA falls while SCL is high, then SCL falls (tHD;STA 0.8 us)
    EMIT  SDA, 0, SDA
    ADDT  T_HDSTA
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
    WAITT                    ; the scheduled SCL fall has happened
    RDTIME r0
    ADDI  r0, RA
    SETT  r0
    ADDT  T_DAT
    EMIT  SDA, SDA, SDA      ; release SDA while SCL is low
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL      ; release SCL
    WAITT                    ; SCL released: watch for its rise from here
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, T_HI           ; tSU;STA 0.8 us
    SETT  r1
    EMIT  SDA, 0, SDA
    ADDT  T_HDSTA            ; tHD;STA 0.8 us
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
; (r0 = 0 for ACK, SDA for NACK). SDA is sampled by a SAMPLE event T_SMP ticks after
; the observed SCL rise.
read_byte:
    LDLC  8
    WAITT                    ; the scheduled SCL fall has happened
    RDTIME r1
    ADDI  r1, RA
    SETT  r1
rb_bit:
    ADDT  T_DAT
    EMIT  SDA, SDA, SDA
    ADDT  T_LOW - T_DAT
    EMIT  SCL, SCL, SCL
    WAITT                    ; SCL released: watch for its rise from here
    ADDT  TO
    WAITP SCL, SCL
    JNZ   timeout
    RDTIME r1
    ADDI  r1, T_SMP
    SETT  r1
    SAMPLE SDA_PIN, MSB
    ADDT  T_RHI - T_SMP
    EMIT  SCL, 0, SCL
    DJNZ  rb_bit
    ADDT  T_DAT
    EMITR SDA, r0, SDA       ; ACK/NACK
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
    GETI  r1
    RET

timeout:
    IRQ   0xEE
    RDTIME r1
    ADDI  r1, 14                ; EMIT commits 12 ticks after RDTIME: lead 2. (Was 8:
    SETT  r1                    ; lead -4, a refused edge; found by escert, 2026-10-13.)
    EMIT  BOTH, BOTH, BOTH
    HALT
