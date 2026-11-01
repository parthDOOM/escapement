; RC servo outputs (v1.6): NCH channels on bank A pins 0..NCH-1 (it owns the whole bank),
; one pulse each per 22 ms frame (45 Hz), 500 to 2547 us wide (1500 us centres a standard
; servo), the pulses one after another so a single context times them all.
;
; A pulse's width is 500 us plus an 11-bit value in microseconds, built from constant
; delays: one ADDT of 2^i us for each set bit i of the value. Every delay is a constant, so
; escert certifies the program for any value the host sends, at any CLK_KHZ from 1346
; (below that it cannot bound one lead, and the .assert stops the assembly).
;
; Host (POP, any time): {channel << 16 | value}, value 0..2047 = 500..2547 us; it takes
; effect from the next frame, one update a frame (so the next frame is never late).
; Channels start at 1500 us. A later channel's pulse starts when the earlier ones end, so
; its frame-to-frame period changes by the change in their widths.

.default CTX, 0
.default NCH, 8
.default CLK_KHZ, 25000
.default TB, 0xF0                           ; tape: the channels' values (NCH words)
.assert (NCH >= 1) & (NCH <= 8), one to eight channels on bank A
.assert CLK_KHZ >= 1346, escert certifies servo from 1346 kHz
.equ FRAME, CLK_KHZ * 22                    ; 22 ms: eight pulses of 2547 us fit
.equ BASE, CLK_KHZ // 2                     ; 500 us
.equ ALL, (1 << NCH) - 1
.equ LEAD, 64

.entry CTX, main
.own   0, CTX
.own   1, CTX
.own   2, CTX
.own   3, CTX
.own   4, CTX
.own   5, CTX
.own   6, CTX
.own   7, CTX
.tape  TB, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000

main:
    ADDT  LEAD
    EMIT  ALL, 0, ALL        ; every output low
    RDTIME r0
    ADDI  r0, LEAD + 32
    SETT  r0                 ; r0 = tmr = the first frame's start
frame:
    MOVI  r2, 0              ; channel
    MOVI  r3, 1              ; its pin
chan:
    EMITR ALL, r3, ALL       ; this channel's pin high (the others low)
    LDT   r1, r2, TB         ; its value
    ADDT  BASE
    TSTI  r1, 0x001
    JZ    b1
    ADDT  (CLK_KHZ * 1 + 500) // 1000
b1: TSTI  r1, 0x002
    JZ    b2
    ADDT  (CLK_KHZ * 2 + 500) // 1000
b2: TSTI  r1, 0x004
    JZ    b3
    ADDT  (CLK_KHZ * 4 + 500) // 1000
b3: TSTI  r1, 0x008
    JZ    b4
    ADDT  (CLK_KHZ * 8 + 500) // 1000
b4: TSTI  r1, 0x010
    JZ    b5
    ADDT  (CLK_KHZ * 16 + 500) // 1000
b5: TSTI  r1, 0x020
    JZ    b6
    ADDT  (CLK_KHZ * 32 + 500) // 1000
b6: TSTI  r1, 0x040
    JZ    b7
    ADDT  (CLK_KHZ * 64 + 500) // 1000
b7: TSTI  r1, 0x080
    JZ    b8
    ADDT  (CLK_KHZ * 128 + 500) // 1000
b8: TSTI  r1, 0x100
    JZ    b9
    ADDT  (CLK_KHZ * 256 + 500) // 1000
b9: TSTI  r1, 0x200
    JZ    b10
    ADDT  (CLK_KHZ * 512 + 500) // 1000
b10: TSTI r1, 0x400
    JZ    b11
    ADDT  (CLK_KHZ * 1024 + 500) // 1000
b11:
    SHL   r3, 1              ; the pulse ends where the next channel's starts, in one event
    ADDI  r2, 1
    CMPI  r2, NCH
    JNZ   chan
    EMIT  ALL, 0, ALL        ; the last pulse ends
    MOVI  r1, FRAME & 0xFFFF ; the next frame starts 22 ms after this one
    MOVHI r1, FRAME >> 16
    ADD   r0, r1
    SETT  r0
    RXREADY                  ; an update from the host?
    JNZ   upd
    JMP   frame
upd:
    POP   r1
    MOV   r2, r1
    SHR   r2, 16             ; channel
    SHL   r1, 21
    SHR   r1, 21             ; value, 11 bits
    STT   r1, r2, TB
    JMP   frame
