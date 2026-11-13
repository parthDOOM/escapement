; 10BASE-T Ethernet receiver on the line unit (v3): every frame on the wire, its frame
; check sequence verified, handed to the host. RD+/RD- through a 10BASE-T transformer to a
; line comparator biased so that the idle line reads 1 (TP_IDL's level, and link pulses),
; its output on pin RXP. No pin is driven: eth_tx.asm in another context can share the
; jack's transmit pair.
;
; The line unit does the bit work (19-LINE-UNIT, v3 receive): the cell decoder takes each
; mid-bit edge (an edge at least S after the last one; a cell boundary comes sooner) and
; stores the level after it as the bit, hunting first for the SFD's closing two 1s; the
; packet ends after 1.5 bit times with no mid-bit edge (TP_IDL); CRC-32 runs over the frame
; and its FCS, so a good frame leaves the residue 0xC704DD7B. At 50 MHz a bit is 5 ticks
; and its cell boundary falls between two ticks: with both-edge sampling (BOTH = 1) the
; decoder counts half ticks and sees every edge to 10 ns. Measured on the simulator
; (sw/tests/test_eth_rx.py, a transmitter 100 ppm off): with both edges every frame decodes
; with each edge moved by up to 10 ns; with one sample a tick (BOTH = 0), only to 5 ns.
;
; Host (PUSH): per reception, a header word [31] FCS good, [30:28] cause (0 OK, 4 the
; buffer full), [13:0] bits received (frame and FCS: 8 per byte), then the received
; words, byte 0 in [7:0], FCS included. The receiver is armed again once the words are
; pushed: a frame that arrives before that is missed, never corrupted.

.default CTX, 0
.default RXP, 2
.default CLK_KHZ, 50000
.default BOTH, 1
.assert CLK_KHZ % 10000 == 0, 10BASE-T needs a whole number of ticks per bit (40 or 50 MHz)
.equ PB, (CLK_KHZ // 10000) * (1 + BOTH)    ; a bit (100 ns): ticks, or half ticks with BOTH
.equ SB, (3 * PB + 3) // 4                  ; mask point: 3/4 of a bit after the mid-bit edge
.equ LIM, 28                                ; buffer limit: 28 x 32 bytes (896)
.equ FB, 0x00                               ; tape: the buffer, words 0x00..0xDF
.equ CFG, 0xF0                              ;   and the configuration (5 words)
.equ W0, RXP | (RXP << 3) | (RXP << 6) | (1 << 11) | (1 << 19)  ; LSB first, hunt for the SFD
.equ W1, PB | (SB << 20)
.equ W2, 0x1DB7 | (1 << 16) | (1 << 25)     ; CRC-32 low half, from all ones; extended
.equ W3, 1 | (1 << 3) | (2 << 11)           ; two cells a bit (Manchester)
.equ W4, 0x04C1 | (1 << 21) | (BOTH << 24) | (1 << 25)   ; CRC-32 high half; the cell decoder,
                                                          ; both edges, a long buffer

.entry CTX, main
.own   RXP, CTX
.tape  CFG, W0, W1, W2, W3, W4

main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: this context owns the line unit
    ADDT  0xFFFFFF
    GETL  r1                 ; its end (a RECV before it would fault: not configured)
arm:
    MOVI  r0, FB | (LIM << 8)
    MOVHI r0, 0x1000
    LINE  r0                 ; RECV
wait:
    ADDT  0xFFFFFF
    GETL  r1                 ; a reception ended
    JNZ   wait
    GETM  r2, 3              ; bits received (the status word holds only 9 bits of it)
    CMPI  r2, 0
    JZ    arm                ; nothing after the hunt: noise, or no SFD
    SHL   r1, 4
    SHR   r1, 29             ; cause
    SHL   r1, 28
    OR    r1, r2
    GETM  r3, 4              ; the CRC register
    MOVI  r0, 0xDD7B
    MOVHI r0, 0xC704
    CMP   r3, r0
    JNZ   push               ; a bad FCS (or a frame cut short)
    MOVI  r0, 0
    MOVHI r0, 0x8000
    OR    r1, r0
push:
    PUSH  r1
    MOV   r3, r2
    ADDI  r3, 31
    SHR   r3, 5
    ADDI  r3, FB             ; r3 = the end of the received words
    MOVI  r0, FB
send:
    LDT   r2, r0, 0
    PUSH  r2
    ADDI  r0, 1
    CMP   r0, r3
    JNZ   send
    JMP   arm
