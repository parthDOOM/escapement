; DALI control gear: the lighting bus of IEC 62386, as a ballast or LED driver answers
; it. Context 0, the bus on pin 0 (open-drain, idle high: a powered bus).
;
; Format (Microchip, "DALI" application documentation, after IEC 62386-101/102):
; Manchester at 1200 bit/s, half bit Te = 416.67 us; a 1 is low then high (a rising
; edge mid-bit), a 0 high then low; MSB first. A forward frame is a start bit (1),
; an address byte, a data byte and a stop condition (the bus high for 2 bits). A
; backward frame (an answer) is a start bit and one byte, 5.5 ms to 10.5 ms after the
; forward frame. Address byte YAAAAAAS: Y = 0, short address A; 1111111S broadcast;
; S = 0: the data byte is a direct arc power level (255: no change), S = 1: a command.
;
; Receiving resynchronises on every mid-bit edge, so a controller up to 25% off in bit
; timing is followed: each bit's first half is sampled 1.5 Te after the previous mid-bit
; edge, and its mid-bit edge must come by 2.5 Te. A frame is taken only if the bus then
; stays high for the stop condition (a 24-bit frame is not for control gear). Answers
; start 7.5 ms after the end of the forward frame's last bit, to within 4 ticks (Te =
; 10417 ticks at 25 MHz, 0.003% long), every half bit an event.
;
; Commands answered here (IEC 62386-102): direct arc power; OFF (0x00); RECALL MAX
; LEVEL (0x05: 254); RECALL MIN LEVEL (0x06: 1); QUERY STATUS (0x90: bit 2, lamp on);
; QUERY CONTROL GEAR PRESENT (0x91: 0xFF); QUERY ACTUAL LEVEL (0xA0).
;
; Host to chip (POP): [31] = 1: short address [5:0]. Chip to host (PUSH): every forward
; frame, [15:0] as received, [16] addressed to this gear (its short address or
; broadcast), [17] answered with a backward frame.

.default CTX, 0
.default PIN, 0
.default ADDR, 0                            ; short address 0-63
.default CLK_KHZ, 25000
.default TB, 0x90                           ; tape: 2 words
.equ D, 1 << PIN
.equ TE, (CLK_KHZ * 5 + 6) // 12            ; half bit: 416.67 us
.equ SETTLE, CLK_KHZ * 15 // 2              ; 7.5 ms: end of a forward frame to the answer
.equ ALEAD, 64                              ; WAITT to the answer's first half
.equ AW, TB                                 ; short address
.equ LV, TB + 1                             ; actual level

.entry CTX, main
.own   PIN, CTX
.od    PIN

main:
    ADDT  20
    EMIT  D, D, D            ; released
    MOVI  r0, ADDR
    MOVI  r3, 0
    STT   r0, r3, AW
    STT   r3, r3, LV
idle:                        ; drain, then wait for the bus high
    WAITT
    ADDT  0xFFFFFF
    WAITP D, D
look:
    RDTIME r1
    SETT  r1
    ADDT  CLK_KHZ            ; a falling edge, or look at the host every 1 ms
    WAITP D, 0
    JZ    rx
    RXREADY
    JZ    look
    POP   r0                 ; a host word: [31] set the short address
    MOV   r2, r0
    SHL   r2, 1
    JNC   look
    MOVI  r2, 63
    AND   r0, r2
    MOVI  r3, 0
    STT   r0, r3, AW
    JMP   look

; ---- a forward frame. r0 = the bits so far, r1 = the last mid-bit edge (as seen)
rx:
    RDTIME r1                ; the start bit's falling edge
    SETT  r1
    ADDT  TE // 2
    SAMPLE PIN, MSB
    GETI  r2                 ; its first half: low
    JNZ   idle
    SETT  r1
    ADDT  TE * 3 // 2
    WAITP D, D               ; its mid-bit edge (rising) by 1.5 Te
    RDTIME r1
    JNZ   idle               ; (Z = 0: no edge)
    MOVI  r0, 0
    LDLC  16
fbit:
    SETT  r1
    ADDT  TE * 3 // 2
    SAMPLE PIN, MSB
    GETI  r2                 ; first half: low for a 1, high for a 0
    JZ    fone
    SETT  r1
    ADDT  TE * 5 // 2
    WAITP D, 0               ; a 0 falls mid-bit
    RDTIME r1
    JNZ   idle
    SHL   r0, 1
    JMP   fnext
fone:
    SETT  r1
    ADDT  TE * 5 // 2
    WAITP D, D               ; a 1 rises mid-bit
    RDTIME r1
    JNZ   idle
    SHL   r0, 1
    ADDI  r0, 1
fnext:
    DJNZ  fbit
    SETT  r1                 ; stop condition: high by 1.25 Te after the last mid-bit edge
    ADDT  TE * 5 // 4
    WAITP D, D
    JNZ   idle
    SETT  r1                 ; ... and no falling edge for 3 Te more
    ADDT  TE * 17 // 4
    WAITP D, 0
    JZ    idle
    RDTIME r1                ; (completed at that timeout, within 3 ticks)
    SETT  r1                 ; tmr = ALEAD before an answer would start
    ADDT  TE + SETTLE - TE * 17 // 4 - ALEAD
    MOV   r2, r0
    SHR   r2, 8              ; address byte
    MOV   r3, r2
    SHR   r3, 1
    CMPI  r3, 0x7F
    JZ    ours               ; broadcast
    TSTI  r2, 0x80
    JNZ   notours            ; a group address or a special command
    MOVI  r1, 0
    LDT   r1, r1, AW
    CMP   r3, r1
    JNZ   notours
ours:
    MOV   r1, r0
    SHL   r1, 24
    SHR   r1, 24             ; the data byte
    TSTI  r2, 1
    JNZ   cmd
    CMPI  r1, 0xFF           ; direct arc power (255: no change)
    JZ    taken
setlv:
    MOVI  r3, 0
    STT   r1, r3, LV
taken:
    MOVI  r1, 1              ; [16]: addressed
    JMP   report
cmd:
    CMPI  r1, 0x00
    JZ    c_off
    CMPI  r1, 0x05
    JZ    c_max
    CMPI  r1, 0x06
    JZ    c_min
    CMPI  r1, 0x90
    JZ    q_status
    CMPI  r1, 0x91
    JZ    q_present
    CMPI  r1, 0xA0
    JZ    q_level
    JMP   taken
c_off:
    MOVI  r1, 0
    JMP   setlv
c_max:
    MOVI  r1, 254
    JMP   setlv
c_min:
    MOVI  r1, 1
    JMP   setlv
q_status:
    MOVI  r3, 0
    LDT   r1, r3, LV
    MOV   r1, r1
    JZ    answer             ; lamp off: 0
    MOVI  r1, 4              ; bit 2: lamp on
    JMP   answer
q_present:
    MOVI  r1, 0xFF
    JMP   answer
q_level:
    MOVI  r3, 0
    LDT   r1, r3, LV
answer:                      ; r1 = the byte: a backward frame from tmr, half bit by half bit
    MOVI  r2, 1              ; the start bit: halves 0, 1
    SHL   r1, 24
    LDLC  8
abit:
    SHL   r2, 2
    SHL   r1, 1              ; C = the next bit
    JC    aone
    ADDI  r2, 2              ; a 0: halves 1, 0
    JMP   anext
aone:
    ADDI  r2, 1              ; a 1: halves 0, 1
anext:
    DJNZ  abit
    SHL   r2, 14             ; 18 halves in [31:14]
    LDSH  r2
    WAITT                    ; start close to the first half's deadline
    ADDT  ALEAD
    LDLC  18
ahalf:
    EMITX D, MSB, D          ; a 1 releases the bus, a 0 pulls it low
    ADDT  TE
    DJNZ  ahalf
    EMIT  D, D, D            ; stop: released
    MOVI  r1, 3              ; [17:16]: addressed and answered
report:
    SHL   r1, 16
    OR    r0, r1
notours:
    PUSH  r0
    JMP   idle
