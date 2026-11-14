; Wiegand reader interface, the panel's side (SIA AC-01-1996.10 limits as vendors quote them:
; DATA0 and DATA1 idle high, each bit a low pulse of 20 to 100 us on DATA0 for a 0 or DATA1
; for a 1, pulse starts 200 us to 20 ms apart). Any bit count up to 64 (26-, 34-, 37-bit
; cards ...): a frame ends when no pulse has started for END_MS (22 ms: above the 20 ms
; longest interval). Inputs only: nothing is driven.
;
; Chip to host (PUSH), per frame, three words: the bit count in [6:0] ([7] set if more than
; 64 bits came: only the last 64 are kept; [8] set if a line stayed low longer than 1 ms),
; then the bits as a 64-bit number, the last received in bit 0: bits 63..32, then 31..0.
;
; While a frame is open the lines are polled every 44 ticks (11 instructions), against a pulse
; of at least 20 us (500 ticks at 25 MHz, 80 at 4 MHz). A pulse on both lines at once reads
; as a 0. More than 127 pulses wrap the count ([7] is set by then).

.default CTX, 0
.default D0_PIN, 0
.default D1_PIN, 1
.default CLK_KHZ, 25000
.default END_MS, 22
.default TB, 0xFC                           ; tape: the frame's deadline, its high word
.equ D0, 1 << D0_PIN
.equ D1, 1 << D1_PIN
.equ M, D0 | D1
.equ END, CLK_KHZ * END_MS
.equ STUCK, CLK_KHZ                         ; 1 ms: a pulse longer than this is a fault
.equ DL, TB
.equ HI, TB + 1
.assert CLK_KHZ * 20 // 1000 > 44, "the poll loop must see a 20 us pulse"
.assert END < 0x80000000, "the frame timeout must fit the signed compare"

.entry CTX, main

main:
    MOVI  r1, 0              ; the bits: the low word (the high one in the tape)
    MOVI  r2, 0              ; count and flags
    STT   r1, r1, HI
idle:                        ; no frame open: wait for a pulse, with no deadline
    GETP  r0
    TSTI  r0, D0
    JZ    got0
    TSTI  r0, D1
    JZ    got1
    JMP   idle
got0:
    MOVI  r0, 0
    JMP   bit
got1:
    MOVI  r0, 1
bit:                         ; r0 = the bit: shift it into the 64-bit number
    MOVI  r3, 0
    LDT   r3, r3, HI
    SHL   r3, 1
    JNC   b1
    TSTI  r2, 0x80           ; a bit fell off the top: more than 64 bits
    JNZ   b1
    ADDI  r2, 0x80
b1:
    SHL   r1, 1
    JNC   b2
    ADDI  r3, 1              ; the low word's top bit into the high word
b2:
    OR    r1, r0
    MOVI  r0, 0
    STT   r3, r0, HI
    ADDI  r2, 1
    RDTIME r0                ; the frame ends END after this pulse unless another starts
    MOVI  r3, END & 0xFFFF
    MOVHI r3, END >> 16
    ADD   r0, r3
    MOVI  r3, 0
    STT   r0, r3, DL
    RDTIME r0                ; wait for the pulse to end (both lines high)
    SETT  r0
    ADDT  STUCK
    WAITP M, M
    JZ    wait_next
    MOVI  r3, 0x100          ; a line low for 1 ms: flag it, then wait for the release
    OR    r2, r3
    ADDT  0xFFFFFF
    WAITP M, M
wait_next:                   ; the next pulse, or the frame's end
    GETP  r0
    TSTI  r0, D0
    JZ    got0
    TSTI  r0, D1
    JZ    got1
    RDTIME r0
    MOVI  r3, 0
    LDT   r3, r3, DL
    SUB   r0, r3             ; now - deadline: negative while the frame is open
    SHL   r0, 1              ; C = its sign
    JC    wait_next
    MOV   r0, r2
    SHL   r0, 23             ; count [6:0] and the flags [8:7]
    SHR   r0, 23
    PUSH  r0
    MOVI  r0, 0
    LDT   r0, r0, HI
    PUSH  r0
    PUSH  r1
    MOVI  r1, 0
    MOVI  r2, 0
    STT   r1, r1, HI
    JMP   idle
