; Classic CAN 2.0A transmitter at line rate on the line unit (v1.6): up to 1 Mbit/s, with
; arbitration. Context 0; TXD on pin TX (1 = recessive, driven) to a transceiver, RXD on
; pin RX. The host interface is can_tx.asm's, which bit-bangs at 250 kbit/s and can share
; a bus with can_rx.asm; this one owns the line unit, so a node needing both uses those.
;
; The line unit sends SOF to the end of the CRC as one packet: MSB first, a complement
; after five equal bits, CRC-15 (0x4599) appended, and its monitor compares RXD with each
; bit it drives at the sample point and stops at the first difference: another node won
; arbitration (or a bit error), and the frame is sent again once the bus has been
; recessive for 11 bit times. After the packet the line is recessive (the idle state): the
; CRC delimiter, the ACK slot, its delimiter and the end of frame. One SAMPLE in the ACK
; slot, scheduled from the packet's end tick, tells whether a receiver acknowledged.
;
; Host: POP {id[10:0] << 16 | dlc}, POP data bytes 0..3 (byte 0 in [31:24]), POP bytes
; 4..7; the chip sends the frame and PUSHes 1 if the ACK slot was dominant, else 0.
; Bit rate KBPS (default 1000 kbit/s) from CLK_KHZ, sample point 70%.

.default CTX, 0
.default TX, 0
.default RX, 1
.default KBPS, 1000
.default CLK_KHZ, 25000
.default TB, 0xF8                           ; tape: the configuration and three frame words
.equ PI, CLK_KHZ // KBPS                    ; bit period, whole ticks
.equ PF, (CLK_KHZ % KBPS) * 256 // KBPS     ;   and 1/256 tick
.equ SP, PI * 7 // 10                       ; sample point
.assert PI >= 8, the line unit needs a bit of at least 8 ticks
.equ RXM, 1 << RX
.equ W0, TX | (RX << 6) | (5 << 12) | (1 << 15) | (1 << 18) | (1 << 20)   ; MSB first, stuff, idle 1
.equ W1, PI | (PF << 12) | (SP << 20)
.equ W2, 0x8B32 | (1 << 20)                 ; CRC-15 left-aligned, stop at a monitor mismatch
.equ CFG, TB
.equ FB, TB + 3
.equ LEAD, 32

.entry CTX, main
.own   TX, CTX
.tape  CFG, W0, W1, W2

main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: TXD recessive, this context owns the line unit
    ADDT  0xFFFFFF
    GETL  r1
next:
    POP   r1                 ; id << 16 | dlc
    POP   r2                 ; bytes 0-3
    POP   r3                 ; bytes 4-7
    ; the frame from SOF: SOF, id[10:0], RTR, IDE, r0, dlc[3:0], data; MSB first, so stream
    ; bit 19 + k is data bit k: word 0 = id << 20 | dlc << 13 | d03 >> 19, word 1 =
    ; d03 << 13 | d47 >> 19, word 2 = d47 << 13
    MOV   r0, r3
    SHR   r0, 19
    SHL   r3, 13
    LDSH  r3                 ; sh = word 2
    MOV   r3, r2
    SHL   r3, 13
    OR    r3, r0             ; r3 = word 1
    SHR   r2, 19
    MOV   r0, r1
    SHR   r0, 16
    SHL   r0, 20
    OR    r0, r2
    MOV   r2, r1
    SHL   r2, 28
    SHR   r2, 15             ; dlc << 13
    OR    r0, r2             ; r0 = word 0
    MOVI  r2, FB
    STT   r0, r2, 0
    STT   r3, r2, 1
    STSH  r0
    STT   r0, r2, 2
    MOV   r3, r1             ; the length: 19 + 8 * min(dlc, 8) bits
    SHL   r3, 28
    SHR   r3, 28
    CMPI  r3, 9
    JC    l8
    MOVI  r3, 8
l8: SHL   r3, 3
    ADDI  r3, 19
    SHL   r3, 8
    ADDI  r3, FB | 0x8000    ; r3 = the SEND operand: stream word, length, append the CRC
send:
    RDTIME r2
    SETT  r2
    ADDT  LEAD
    SEND  r3                 ; SOF to CRC at tmr
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = the end of the CRC
    SHL   r1, 4
    SHR   r1, 29
    CMPI  r1, 6              ; MISMATCH: lost arbitration
    JZ    lost
    ADDT  PI + SP            ; the ACK slot's sample point (after the CRC delimiter)
    SAMPLE RX, MSB
    GETI  r1                 ; bit 0: RXD at the ACK slot
    MOVI  r0, 1
    XOR   r1, r0
    AND   r1, r0             ; 1 if dominant (acknowledged)
    PUSH  r1
    JMP   next
lost:                        ; wait for 11 recessive bit times, then send again
    RDTIME r2
    SETT  r2
    ADDT  11 * PI
    WAITP RXM, 0             ; RXD dominant before tmr: the bus is busy, wait again
    JZ    lost
    JMP   send
