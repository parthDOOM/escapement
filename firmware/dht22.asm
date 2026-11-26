; DHT22 (AM2302) host: reads relative humidity and temperature over the sensor's single
; open-drain data line. Held-out round, written 2026-11-24 on v3.1 (cb47436), from the Aosong
; "AM2302 Product Manual" (Table 5 format, Table 6 timing, 7.1-7.4).
;
;   pin DQ_PIN (0)   in/out, open drain: SDA, pulled up (about 5.1 k, 7.1)
; Context CTX (0). Clock CLK_KHZ (25 MHz by default; 4 to 50 MHz). Times are counted in
; US = CLK_KHZ // 1000 ticks a microsecond: at a clock that is not a whole number of MHz
; every interval is up to 1/US short (1 % at 33.333 MHz), inside every Table 6 window.
;
; What it does, per host request:
; - Waits until MIN_MS (2 s) after the last start signal, and after power-up (7.1 item 3,
;   7.4 step 1: the sensor answers no faster).
; - Start signal (Table 6 Tbe, 0.8 to 20 ms): DQ low for START_US (1 ms), then released.
; - Response (Tgo 20 to 200 us, then Trel 80 us low and Treh 80 us high, 75 to 85 each): the
;   low must come within 300 us of the release, and each half must last 70 to 90 us.
; - 40 bits, MSB first (7.2): each a low of TLOW (48 to 55 us; 40 to 65 accepted) and a high of
;   TH0 (22 to 30 us) for a 0 or TH1 (68 to 75 us) for a 1; accepted 15 to 36 and 60 to 90 us.
;   Every edge is timed from WAITP and RDTIME (a 4-tick uncertainty, 160 ns at 25 MHz).
; - The parity byte must equal the sum of the four data bytes (7.2), else the read fails.
; The margins around Table 6 cover cable capacitance on the rising edges and the WAITP lag.
;
; Host (mailbox, context CTX):
;   host -> chip: any word: read the sensor
;   chip -> host: [31:16] humidity x 10, [15:0] temperature x 10 (bit 15 = 1: below zero),
;                 as the sensor sent them; or 0xFFFF0000 | error:
;                 1 no response, 2 response timing, 3 a bit's timing, 4 parity
;                 (a valid humidity is at most 999, so 0xFFFF in the top half is never data)

.default CTX, 0
.default DQ_PIN, 0
.default CLK_KHZ, 25000
.default MIN_MS, 2000
.default START_US, 1000
.equ DQ, 1 << DQ_PIN
.equ US, CLK_KHZ // 1000
.assert 4000 <= CLK_KHZ <= 50000, 4 to 50 MHz
.assert START_US * US * 1000 >= 800 * CLK_KHZ, Tbe is 0.8 ms at least (as the clock counts it)
.assert START_US <= 20000, Tbe is 20 ms at most
.equ MINT, MIN_MS * CLK_KHZ
.assert MINT < 1 << 31, MIN_MS too long for the tick counter's half range
.equ LAST, 0x00                    ; tape: the last start signal's tick
.equ DATA, 0x01                    ;   the 32 data bits

.entry CTX, main
.own DQ_PIN, CTX
.od DQ_PIN
.tape LAST, 0

main:
    ADDT  40
    EMIT  DQ, DQ, DQ           ; released
loop:
    POP   r0
    MOVI  r3, 0
    LDT   r1, r3, LAST
    MOVI  r2, MINT & 0xFFFF
    MOVHI r2, MINT >> 16
    ADD   r1, r2               ; the earliest start
    RDTIME r2
    SUB   r2, r1
    SHR   r2, 31
    JZ    go                   ; already past it
    SETT  r1
    WAITT
go:
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    MOVI  r3, 0
    STT   r1, r3, LAST
    EMIT  DQ, 0, DQ            ; T0: the start signal
    ADDT  START_US * US
    EMIT  DQ, DQ, DQ           ; T0 + START_US: released
    ADDT  10 * US
    WAITT                      ; the line is back high (Tgo >= 20 us)
    ADDT  290 * US
    WAITP DQ, 0                ; the response's low
    RDTIME r1
    JNZ   e_none
    MOV   r2, r1
    ADDI  r2, 100 * US
    SETT  r2
    WAITP DQ, DQ
    RDTIME r2
    JNZ   e_resp
    MOV   r3, r2
    SUB   r3, r1               ; Trel
    CMPI  r3, 70 * US
    JC    e_resp
    CMPI  r3, 90 * US + 1
    JNC   e_resp
    MOV   r1, r2
    ADDI  r2, 100 * US
    SETT  r2
    WAITP DQ, 0
    RDTIME r2
    JNZ   e_resp
    MOV   r3, r2
    SUB   r3, r1               ; Treh
    CMPI  r3, 70 * US
    JC    e_resp
    CMPI  r3, 90 * US + 1
    JNC   e_resp
    MOV   r1, r2               ; r1: the first bit's low began
    MOVI  r0, 0
    LDLC  32
b32:
    CALL  bit
    JNZ   e_bit
    DJNZ  b32
    MOVI  r3, 0
    STT   r0, r3, DATA
    MOVI  r0, 0
    LDLC  8
b8:
    CALL  bit
    JNZ   e_bit
    DJNZ  b8
    MOVI  r3, 0                ; parity: the sum of the four bytes, mod 256
    LDT   r2, r3, DATA
    MOV   r3, r2
    SHR   r3, 24
    MOV   r1, r2
    SHL   r1, 8
    SHR   r1, 24
    ADD   r3, r1
    MOV   r1, r2
    SHL   r1, 16
    SHR   r1, 24
    ADD   r3, r1
    MOV   r1, r2
    SHL   r1, 24
    SHR   r1, 24
    ADD   r3, r1
    SHL   r3, 24
    SHR   r3, 24
    CMP   r3, r0
    JNZ   e_par
    PUSH  r2
    JMP   loop
e_none:
    MOVI  r0, 1
    JMP   err
e_resp:
    MOVI  r0, 2
    JMP   err
e_bit:
    MOVI  r0, 3
    JMP   err
e_par:
    MOVI  r0, 4
err:
    MOVHI r0, 0xFFFF
    PUSH  r0
    JMP   loop

; bit: r1 = the tick the line fell (this bit's low began). Shifts the bit into r0, returns
; r1 = the tick the line fell again (the next bit's low), Z = 1; Z = 0 for a bit out of time.
bit:
    MOV   r2, r1
    ADDI  r2, 70 * US
    SETT  r2
    WAITP DQ, DQ               ; the low ends
    RDTIME r2
    JNZ   b_bad
    MOV   r3, r2
    SUB   r3, r1               ; TLOW
    CMPI  r3, 40 * US
    JC    b_bad
    MOV   r1, r2
    ADDI  r2, 90 * US
    SETT  r2
    WAITP DQ, 0                ; the high ends
    RDTIME r2
    JNZ   b_bad
    MOV   r3, r2
    SUB   r3, r1               ; TH0 or TH1
    MOV   r1, r2
    SHL   r0, 1
    CMPI  r3, 15 * US
    JC    b_bad
    CMPI  r3, 36 * US + 1
    JC    b_ok                 ; a 0
    CMPI  r3, 60 * US
    JC    b_bad                ; neither
    ADDI  r0, 1                ; a 1
b_ok:
    MOVI  r3, 0                ; Z = 1
    RET
b_bad:
    MOVI  r3, 1                ; Z = 0
    RET
