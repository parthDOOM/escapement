; MIL-STD-1553B remote terminal (RT) in firmware: Manchester II at 1 Mbit/s.
; Held-out round, written 2026-11-24 on v3.1 (cb47436), from MIL-STD-1553B (21 Sep 1978).
;
; The chip sits behind a 1553 transceiver (the digital side of a part such as a HI-1573):
;   pin TXA (0), TXB (1)   out: TXA = 1, TXB = 0 drives the bus positive, 0/1 negative;
;                          equal levels (both released, high) keep the transmitter off
;   pin RXA (4), RXB (5)   in:  RXA = 1 while the bus is positive, RXB = 1 while negative,
;                          both 0 while it is idle
; Bank A, context 0 (paired: context 2 stays off), host context 1. Clock: CLK_KHZ, a whole
; multiple of 2 MHz so that a half bit is a whole number of ticks (the transmitter must keep
; every zero crossing within 25 ns of the ideal, 4.5.2.1.1.2), from 40 to 50 MHz (below 40
; the decode does not fit between the samples of the first data word; escert proves 40 to
; 50); 50 MHz by default. The chip's default 25 MHz cannot carry 1553: a half bit would be
; 12.5 ticks.
;
; What it does (section numbers of MIL-STD-1553B):
; - Receives every word on the bus and validates it (4.4.1.1): a sync of the right kind,
;   16 bits and parity, every bit Manchester II, odd parity. Each word is read with SAMPLE
;   events at a quarter and three quarters of each bit (both halves of every bit, so a bit
;   without its mid-bit crossing is refused), anchored on the word's mid-sync crossing.
;   36 samples a word: 2 in the second half of the sync, 2 per bit for 16 bits and parity.
;   Every sample is timed from that one crossing, so the receiver takes zero-crossing
;   deviations (4.5.2.2.2.1) of up to about 200 ns between the mid-sync crossing and any
;   later one: every crossing moved by up to 100 ns (tested), and any single crossing moved
;   by 150 ns (inside the measured eye, about +/-175 ns at 40 MHz). It does not take the
;   full 150 ns on every crossing at once, which needs each bit re-timed from its own
;   mid-bit crossing (see "Not done").
; - A command word (4.3.3.5.1) addressed to RT_ADDR, or to 31 (broadcast), is then acted
;   on; any other command is ignored. The words of a message must be contiguous (4.4.1.2):
;   the next word's sync must cross mid-sync 20 bit times after this one's, within TOL.
; - Receive commands, subaddress 1 to 30, word count 1 to 32 (0 means 32): the data words
;   are stored; when all are valid and no more follow, the RT answers with its status word
;   (4.3.3.6.1) and hands the message to the host. Any invalid or missing word sets the
;   message error bit and suppresses the status (4.4.3.6). Broadcast: no status, the
;   broadcast-received bit is set instead (4.3.3.6.7.1).
; - Transmit commands, subaddress 1 to 30: the status word and the first N words of the
;   transmit buffer, contiguous (4.3.3.6.2). One buffer serves every subaddress.
; - Mode codes (4.3.3.5.1.7): transmit status word (00010) answers with the status of the
;   last command and leaves it as it is; synchronize (00001), reset (01000) and synchronize
;   with data word (10001, one data word, given to the host) are carried out. Every other
;   mode code is an illegal command for this RT: it takes the data word a code from 10000
;   up brings, and answers with the message error bit set (4.4.3.4).
; - Status word (4.3.3.5.3): RT address, message error, broadcast command received; the
;   other flags are 0. It is reset after each valid command except transmit status
;   (4.3.3.5.4).
; - Response time (4.3.3.8): RESP_US (8 us) from the mid-bit crossing of the parity bit of
;   the last word to the mid-sync crossing of the status word, within a tick or two; the
;   allowed range is 4 to 12 us. RESP_US is 8 to 11: the longest path from the end of a
;   message to its SEND (decode, the status update) needs about 7.7 us at 40 MHz.
; - Superseding (4.4.3.2): a command that follows a command at once is taken instead.
;
; Not done, and why: RT-to-RT transfers in which this RT receives (the second command word
; and the other RT's status are not skipped: the message ends in a message error); transmit
; vector word, transmit last command, transmit BIT word, dynamic bus control, transmitter
; shutdown and self test are illegal commands here; one transmit buffer for all subaddresses.
; The full receiver tolerance: a decoder that re-times each bit from its own mid-bit
; crossing. In software that means a WAITP on every crossing, which can come 350 ns (14
; ticks at 40 MHz) after the last one, closer than a WAITP can be re-armed; the line unit's
; cell decoder does exactly this re-timing, but it starts on an idle-to-active edge and
; takes the first edge after it as a bit's middle, and ends a packet after 1.5 bit times
; with no mid-bit edge, so it cannot start on a 1553 sync (not Manchester) and stops at
; every word's 2 us from parity to the next mid-sync.
;
; Transmit: the line unit sends each reply as one packet of half-bit cells, TXA the cell and
; TXB its complement (differential), from the tape: the prefix byte (sync and the address's
; first bit), the header byte (the address's other four bits), then 24 cells of status and
; 40 cells for each data word. Released between packets, so TXA = TXB = 1: off.
;
; Host (context 1):
;   host -> chip: 0x01ii_vvvv   transmit data word ii (0 to 31) = vvvv
;   chip -> host: per message received (receive or synchronize with data word),
;                 [31] earlier messages lost, [21:16] N data words, [15:0] the command word;
;                 then the N data words, one per mailbox word.

.default CTX, 0
.default HCTX, 1
.default CLK_KHZ, 50000
.default RT_ADDR, 5
.default RESP_US, 8
.default TXA, 0
.default TXB, 1
.default RXA, 4
.default RXB, 5
.assert CLK_KHZ % 2000 == 0, a half bit must be a whole number of ticks
.assert 40000 <= CLK_KHZ <= 50000, the clock range this program is timed for (escert)
.assert RT_ADDR < 31, address 31 is the broadcast address
.assert 8 <= RESP_US <= 11, from 8 us the decode fits (escert: every clock); to 11, 1 us inside 12

.equ B, CLK_KHZ // 1000            ; ticks per bit
.equ H, B // 2                     ; ticks per half bit (a cell)
.equ RXAM, 1 << RXA
.equ RXBM, 1 << RXB
.equ RXM, RXAM | RXBM
.equ TOL, 2 * B // 5               ; contiguity window: a data sync 20 bits on, +/- 0.4 bit
; A WAITP sees an edge 2 to 5 ticks after it; the RDTIME after it commits 2 ticks later
; (paired): the edge was 5.5 ticks before on average. A SAMPLE reads the synchronised
; input, which is 2 ticks behind the pin. The first sample is 0.75 bit after the mid-sync
; crossing, then one every half bit. (Measured against the bus model with no distortion,
; words decode for offsets from -7 to +8 ticks around this one at 40 MHz, -11 to +11 at
; 50 MHz.)
.equ SAMPOFF, (3 * B - 14) // 4
.equ NX1, (20 * B - TOL - 5) - (SAMPOFF + 36 * H)   ; from a word's last sample + H to the
.equ NX2, 2 * TOL + 7                               ; next sync's first half, then its end
; A quiet line at the GETP (in the first half of where the next sync would be: neither RXA
; nor RXB) ends the message. From that point to the status word: its mid-sync crossing,
; 3 cells into the packet and one tick after the deadline, lands RESP_US after the last
; word's mid-parity.
.equ RESP, RESP_US * B - 2 * B - 3 * H - TOL - 9 + NX2
.assert RESP > 0, the response time is too short for the contiguity window

; status word cells: prefix (sync and address bit 4), header (address bits 3..0)
.equ A4, (RT_ADDR >> 4) & 1
.equ A3, (RT_ADDR >> 3) & 1
.equ A2, (RT_ADDR >> 2) & 1
.equ A1, (RT_ADDR >> 1) & 1
.equ A0, RT_ADDR & 1
.equ PREFIX, 0xE1 + A4             ; cells 111000 then the address's first bit (10 or 01)
.equ HDR, 0x55 + (A3 << 6) + (A2 << 4) + (A1 << 2) + A0
.equ ADDRC, 0x155 + (A4 << 8) + (A3 << 6) + (A2 << 4) + (A1 << 2) + A0   ; address cells
.equ PCA, A4 + A3 + A2 + A1 + A0
; VAR[me << 1 | bcr]: the status word's last 12 bits as cells in bits 31..8: message error
; (bit time 9), broadcast received (15), odd parity (20); every other flag 0
.equ VAR0, 0x55555500 + ((1 - (PCA & 1)) << 8)
.equ VAR1, 0x55555500 + (1 << 18) + ((1 - ((PCA + 1) & 1)) << 8)
.equ VAR2, 0x55555500 + (1 << 30) + ((1 - ((PCA + 1) & 1)) << 8)
.equ VAR3, 0x55555500 + (1 << 30) + (1 << 18) + ((1 - ((PCA + 2) & 1)) << 8)

; tape
.default RXB0, 0x00                ; two receive buffers: the command word, then up to 32
.default RXB1, 0x21
.default T0, 0x42                  ; the transmit stream: 41 words
.default SOPT, 0x70                ; SEND operands for 0 to 32 data words
.default VAR, 0x91
.default BTAB, 0xA0                ; the last two bits of a word, as sampled
.default MASKT, 0xB0               ; 1 << (31 - j)
.default CFG, 0xD0                 ; the line unit configuration (5 words)
.equ CMDW, 0xE0                    ; the last command word, as sampled
.equ BCF, 0xE1                     ; 1: it was a broadcast
.equ OUTV, 0xE2                    ; the status the message leaves (me << 1 | bcr)
.equ STV, 0xE3                     ; the status in force
.equ ENDP, 0xE4                    ; the receive buffer's end
.equ RXN, 0xE5                     ; its data words
.equ RXSEL, 0xE6                   ; the buffer the next message goes into
.equ MSGW, 0xE7                    ; [31:16] messages so far, [15:8] N, [7:0] buffer
.equ HEAD, 0xE8                    ; data word 0's first 8 cells (stream word 0's low byte)
.equ LSEQ, 0xE9                    ; host context: the messages it has reported
.equ HFLAG, 0xEA
.equ ENCV, 0xEB

.equ CL_NONE, 0
.equ CL_RX, 1
.equ CL_TX, 2
.equ CL_STAT, 3
.equ CL_MODE, 4

; W0: TXA data, TXB its complement, differential, MSB first, released when idle, claim the
; pins, the prefix. W1: a cell of H ticks. W2: header byte, extended. W3: one cell per bit.
.tape CFG, TXA | (TXB << 3) | (RXA << 6) | (1 << 9) | (1 << 20) | (PREFIX << 24), H | ((H // 2) << 20), (1 << 18) | (1 << 25), 1 << 11, 0
.tape VAR, VAR0, VAR1, VAR2, VAR3
.tape RXSEL, RXB0
.tape HEAD, 0x1D
.tape STV, 0
.tape MSGW, 0
.tape BTAB, 0x000, 0x000, 0x002, 0x002, 0x000, 0x100, 0x102, 0x002, 0x002, 0x102, 0x100, 0x000, 0x002, 0x002, 0x000, 0x000
.tape MASKT + 0, 1 << 31, 1 << 30, 1 << 29, 1 << 28, 1 << 27, 1 << 26, 1 << 25, 1 << 24
.tape MASKT + 8, 1 << 23, 1 << 22, 1 << 21, 1 << 20, 1 << 19, 1 << 18, 1 << 17, 1 << 16
.tape MASKT + 16, 1 << 15, 1 << 14, 1 << 13, 1 << 12, 1 << 11, 1 << 10, 1 << 9, 1 << 8
.tape MASKT + 24, 1 << 7, 1 << 6, 1 << 5, 1 << 4, 1 << 3, 1 << 2, 1 << 1, 1 << 0
.tape SOPT + 0, ((24 + 40 * 0) >> 7) << 24 | HDR << 16 | ((24 + 40 * 0) & 127) << 8 | T0
.tape SOPT + 1, ((24 + 40 * 1) >> 7) << 24 | HDR << 16 | ((24 + 40 * 1) & 127) << 8 | T0
.tape SOPT + 2, ((24 + 40 * 2) >> 7) << 24 | HDR << 16 | ((24 + 40 * 2) & 127) << 8 | T0
.tape SOPT + 3, ((24 + 40 * 3) >> 7) << 24 | HDR << 16 | ((24 + 40 * 3) & 127) << 8 | T0
.tape SOPT + 4, ((24 + 40 * 4) >> 7) << 24 | HDR << 16 | ((24 + 40 * 4) & 127) << 8 | T0
.tape SOPT + 5, ((24 + 40 * 5) >> 7) << 24 | HDR << 16 | ((24 + 40 * 5) & 127) << 8 | T0
.tape SOPT + 6, ((24 + 40 * 6) >> 7) << 24 | HDR << 16 | ((24 + 40 * 6) & 127) << 8 | T0
.tape SOPT + 7, ((24 + 40 * 7) >> 7) << 24 | HDR << 16 | ((24 + 40 * 7) & 127) << 8 | T0
.tape SOPT + 8, ((24 + 40 * 8) >> 7) << 24 | HDR << 16 | ((24 + 40 * 8) & 127) << 8 | T0
.tape SOPT + 9, ((24 + 40 * 9) >> 7) << 24 | HDR << 16 | ((24 + 40 * 9) & 127) << 8 | T0
.tape SOPT + 10, ((24 + 40 * 10) >> 7) << 24 | HDR << 16 | ((24 + 40 * 10) & 127) << 8 | T0
.tape SOPT + 11, ((24 + 40 * 11) >> 7) << 24 | HDR << 16 | ((24 + 40 * 11) & 127) << 8 | T0
.tape SOPT + 12, ((24 + 40 * 12) >> 7) << 24 | HDR << 16 | ((24 + 40 * 12) & 127) << 8 | T0
.tape SOPT + 13, ((24 + 40 * 13) >> 7) << 24 | HDR << 16 | ((24 + 40 * 13) & 127) << 8 | T0
.tape SOPT + 14, ((24 + 40 * 14) >> 7) << 24 | HDR << 16 | ((24 + 40 * 14) & 127) << 8 | T0
.tape SOPT + 15, ((24 + 40 * 15) >> 7) << 24 | HDR << 16 | ((24 + 40 * 15) & 127) << 8 | T0
.tape SOPT + 16, ((24 + 40 * 16) >> 7) << 24 | HDR << 16 | ((24 + 40 * 16) & 127) << 8 | T0
.tape SOPT + 17, ((24 + 40 * 17) >> 7) << 24 | HDR << 16 | ((24 + 40 * 17) & 127) << 8 | T0
.tape SOPT + 18, ((24 + 40 * 18) >> 7) << 24 | HDR << 16 | ((24 + 40 * 18) & 127) << 8 | T0
.tape SOPT + 19, ((24 + 40 * 19) >> 7) << 24 | HDR << 16 | ((24 + 40 * 19) & 127) << 8 | T0
.tape SOPT + 20, ((24 + 40 * 20) >> 7) << 24 | HDR << 16 | ((24 + 40 * 20) & 127) << 8 | T0
.tape SOPT + 21, ((24 + 40 * 21) >> 7) << 24 | HDR << 16 | ((24 + 40 * 21) & 127) << 8 | T0
.tape SOPT + 22, ((24 + 40 * 22) >> 7) << 24 | HDR << 16 | ((24 + 40 * 22) & 127) << 8 | T0
.tape SOPT + 23, ((24 + 40 * 23) >> 7) << 24 | HDR << 16 | ((24 + 40 * 23) & 127) << 8 | T0
.tape SOPT + 24, ((24 + 40 * 24) >> 7) << 24 | HDR << 16 | ((24 + 40 * 24) & 127) << 8 | T0
.tape SOPT + 25, ((24 + 40 * 25) >> 7) << 24 | HDR << 16 | ((24 + 40 * 25) & 127) << 8 | T0
.tape SOPT + 26, ((24 + 40 * 26) >> 7) << 24 | HDR << 16 | ((24 + 40 * 26) & 127) << 8 | T0
.tape SOPT + 27, ((24 + 40 * 27) >> 7) << 24 | HDR << 16 | ((24 + 40 * 27) & 127) << 8 | T0
.tape SOPT + 28, ((24 + 40 * 28) >> 7) << 24 | HDR << 16 | ((24 + 40 * 28) & 127) << 8 | T0
.tape SOPT + 29, ((24 + 40 * 29) >> 7) << 24 | HDR << 16 | ((24 + 40 * 29) & 127) << 8 | T0
.tape SOPT + 30, ((24 + 40 * 30) >> 7) << 24 | HDR << 16 | ((24 + 40 * 30) & 127) << 8 | T0
.tape SOPT + 31, ((24 + 40 * 31) >> 7) << 24 | HDR << 16 | ((24 + 40 * 31) & 127) << 8 | T0
.tape SOPT + 32, ((24 + 40 * 32) >> 7) << 24 | HDR << 16 | ((24 + 40 * 32) & 127) << 8 | T0
; the transmit stream after word 0: 32 data words of value 0 (cells 000111 01, 01 x 15, 10)
.tape T0 + 1, 0x55555556, 0x1D555555, 0x561D5555, 0x55561D55, 0x5555561D, 0x55555556, 0x1D555555, 0x561D5555
.tape T0 + 9, 0x55561D55, 0x5555561D, 0x55555556, 0x1D555555, 0x561D5555, 0x55561D55, 0x5555561D, 0x55555556
.tape T0 + 17, 0x1D555555, 0x561D5555, 0x55561D55, 0x5555561D, 0x55555556, 0x1D555555, 0x561D5555, 0x55561D55
.tape T0 + 25, 0x5555561D, 0x55555556, 0x1D555555, 0x561D5555, 0x55561D55, 0x5555561D, 0x55555556, 0x1D555555
.tape T0 + 33, 0x561D5555, 0x55561D55, 0x5555561D, 0x55555556, 0x1D555555, 0x561D5555, 0x55561D55, 0x55555600

.entry CTX, main
.entry HCTX, host
.pair CTX
.span CTX                          ; context 0 also takes context 2's page (host code below 256)
.own TXA, CTX
.own TXB, CTX

; ---------------------------------------------------------------- the host context
; It never touches the bus. It writes transmit data words into the stream as cells, and
; reads each received message out of its buffer.
host:
    MOVI  r1, 0
    STT   r1, r1, LSEQ
hloop:
    RXREADY
    JZ    hl_msg               ; no host word
    POP   r0
    MOV   r1, r0
    SHR   r1, 24
    CMPI  r1, 1
    JNZ   hloop                ; only 0x01: set a transmit data word
    MOV   r3, r0
    SHL   r3, 16
    SHR   r3, 16
    MOVI  r2, 0
    STT   r3, r2, ENCV         ; the value
    MOV   r1, r0
    SHL   r1, 11
    SHR   r1, 27               ; the word's index i
    MOV   r2, r1
    SHL   r2, 5
    SHL   r1, 3
    ADD   r1, r2
    ADDI  r1, 24               ; its first cell: stream bit 24 + 40 i
    MOV   r2, r3
    SHR   r2, 15               ; bit 15 (sent first)
    ADDI  r2, 0x1D             ; cells 000111, then 10 or 01
    SHL   r2, 24
    LDLC  8
    CALL  putc
    MOVI  r3, 0
    LDT   r0, r3, ENCV
    MOV   r2, r0
    SHL   r2, 17               ; bit 14 at the top
    MOVI  r3, 0
    LDLC  15
spread:
    SHL   r3, 2
    ADDI  r3, 1                ; cells 01 for a 0 ...
    SHL   r2, 1
    JNC   spr1
    ADDI  r3, 1                ; ... 10 for a 1
spr1:
    DJNZ  spread
    MOV   r2, r0               ; odd parity over the 16 bits
    SHR   r2, 8
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 4
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 2
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 1
    XOR   r0, r2
    SHL   r3, 2
    ADDI  r3, 1
    TSTI  r0, 1
    JNZ   spr2                 ; an odd number of ones: parity 0, cells 01
    ADDI  r3, 1                ; else parity 1, cells 10
spr2:
    MOV   r2, r3
    LDLC  32
    CALL  putc
    JMP   hloop

; putc: write lc cells, r2[31] first, at stream bit r1 on (r1 advances). Stream word 0 is
; composed by the RT context when it answers; its last 8 cells live in HEAD.
putc:
    LDSH  r2
pc:
    MOV   r2, r1
    SHR   r2, 5
    ADDI  r2, T0
    CMPI  r2, T0
    JNZ   pc1
    MOVI  r2, HEAD
pc1:
    MOV   r3, r1
    SHL   r3, 27
    SHR   r3, 27
    LDT   r3, r3, MASKT        ; the cell's bit
    STSH  r0
    SHL   r0, 1                ; C = the cell
    LDSH  r0
    LDT   r0, r2, 0
    JC    pc_one
    OR    r0, r3
    XOR   r0, r3               ; clear the bit
    JMP   pc_st
pc_one:
    OR    r0, r3
pc_st:
    STT   r0, r2, 0
    ADDI  r1, 1
    DJNZ  pc
    RET

hl_msg:                        ; a new message from the RT context?
    MOVI  r1, 0
    LDT   r0, r1, MSGW
    MOV   r2, r0
    SHR   r2, 16
    LDT   r3, r1, LSEQ
    CMP   r2, r3
    JZ    hloop
    STT   r2, r1, LSEQ
    SUB   r2, r3
    MOVI  r3, 0
    CMPI  r2, 1
    JZ    hm1
    MOVHI r3, 0x8000           ; messages were lost in between
hm1:
    STT   r3, r1, HFLAG
    MOV   r1, r0
    SHL   r1, 24
    SHR   r1, 24               ; the buffer
    MOV   r3, r0
    SHL   r3, 16
    SHR   r3, 24               ; N
    LDT   r0, r1, 0
    CALL  compact              ; the command word
    SHL   r3, 16
    OR    r2, r3
    SHR   r3, 16
    MOVI  r0, 0
    LDT   r0, r0, HFLAG
    OR    r2, r0
    PUSH  r2
    ADD   r3, r1               ; the last data word
hm_w:
    ADDI  r1, 1
    LDT   r0, r1, 0
    CALL  compact
    PUSH  r2
    CMP   r1, r3
    JNZ   hm_w
    JMP   hloop

; compact: r0 = a word as sampled (bit k's two halves at bits 31-2k, 30-2k) -> r2 = its 16 bits
compact:
    MOVI  r2, 0
    LDLC  16
cp:
    SHL   r2, 1
    SHL   r0, 1
    JNC   cp1
    ADDI  r2, 1
cp1:
    SHL   r0, 1
    DJNZ  cp
    RET

; ---------------------------------------------------------------- the remote terminal
main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                   ; CONFIG: this context owns the line unit
m_cfg:
    ADDT  0xFFFFFF
    GETL  r1
    JNZ   m_cfg
hunt:                          ; find a command sync: positive 1.5 bits, then negative
    RDTIME r1                  ; (no timeout in practice: 2^30 ticks, so an idle bus is a
    MOVI  r2, 0                ; steady wait, which the bring-up's prediction needs)
    MOVHI r2, 0x4000
    ADD   r1, r2
    SETT  r1
    WAITP RXM, RXAM
    RDTIME r2
    JNZ   hunt
    MOV   r1, r2
    ADDI  r1, 5 * B // 2
    SETT  r1
    WAITP RXM, RXBM            ; the mid-sync crossing
    RDTIME r1
    JNZ   hunt                 ; positive too long: no sync
    MOV   r3, r1
    SUB   r3, r2
    CMPI  r3, 5 * B // 4
    JC    hunt                 ; positive too short: a data bit, not a sync
    ADDI  r1, SAMPOFF
    SETT  r1
cmd_go:
    CALL  getcmd
    JNZ   hunt                 ; not a valid command/status word
    MOVI  r1, 0
    STT   r0, r1, CMDW
    ADDT  NX1                  ; a data word would be in its sync's first half now
    WAITT
    GETP  r1
    TSTI  r1, RXAM
    JNZ   c_pos                ; positive: a command follows at once
    TSTI  r1, RXBM
    JZ    c_quiet              ; quiet: nothing follows
    ADDT  NX2
    WAITP RXM, RXAM            ; a data word's mid-sync crossing
    RDTIME r1
    JNZ   c_bad                ; none: not a word
    ADDI  r1, SAMPOFF
    SETT  r1
    SAMPLE RXB, MSB            ; the data word's first four samples, then the command
    ADDT  H
    SAMPLE RXB, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    CALL  deca                 ; the command's address and count ...
    JNZ   c_flush
    SAMPLE RXA, MSB            ; (the queue is filled again before each part, so it
    ADDT  H                    ; never runs dry)
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    CALL  decb                 ; ... what it asks ...
    CMPI  r3, CL_RX
    JNZ   c_extra
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    CALL  rxbuf                ; ... and where its data words go
    CALL  getrest
    JNZ   merr
    JMP   rx_store
c_extra:                       ; a data word after a command that takes none
    GETI  r0
    JMP   merr
c_flush:                       ; not for this RT
    GETI  r0
    JMP   hunt
c_pos:                         ; superseded: take the new command (4.4.3.2)
    ADDT  NX2
    WAITP RXM, RXBM
    RDTIME r1
    JNZ   hunt
    ADDI  r1, SAMPOFF
    SETT  r1
    JMP   cmd_go
c_bad:                         ; something that is not a word followed the command
    LDLC  1
    CALL  deca
    JNZ   hunt
    JMP   merr
c_quiet:                       ; no data word followed: answer the command
    LDLC  1                    ; (only so that escert keeps this path apart from the one
                               ; above, which calls the same routines with samples queued)
    CALL  deca
    JNZ   hunt
    CALL  decb
    CMPI  r3, CL_TX
    JZ    answer_upd
    CMPI  r3, CL_STAT
    JZ    answer_n
    CMPI  r3, CL_MODE
    JZ    answer_upd
    CMPI  r3, CL_RX
    JZ    merr                 ; its data words are missing
    JMP   hunt

rx_store:                      ; r0 = a valid data word, sh = where it goes
    STSH  r2
    MOVI  r3, 0
    LDT   r3, r3, ENDP
    CMP   r2, r3
    JZ    merr                 ; more words than the count
    STT   r0, r2, 0
    ADDI  r2, 1
    LDSH  r2
    ADDT  NX1
    WAITT
    GETP  r1
    TSTI  r1, RXAM
    JNZ   merr                 ; a command where a data word or the gap should be
    TSTI  r1, RXBM
    JZ    rx_quiet             ; quiet: the message has ended
    ADDT  NX2
    WAITP RXM, RXAM
    RDTIME r1
    JNZ   merr                 ; not a word
    ADDI  r1, SAMPOFF
    SETT  r1
    CALL  getdata
    JNZ   merr
    JMP   rx_store
rx_quiet:                      ; the message has ended
    STSH  r2
    MOVI  r1, 0
    LDT   r3, r1, ENDP
    CMP   r2, r3
    JNZ   merr                 ; fewer words than the count
    LDT   r3, r1, OUTV
    TSTI  r3, 2
    JNZ   rx_ans               ; an illegal mode code: the data is not used
    LDT   r0, r1, MSGW         ; hand the message to the host context
    SHR   r0, 16
    ADDI  r0, 1
    SHL   r0, 16
    LDT   r2, r1, RXN
    SHL   r2, 8
    OR    r0, r2
    LDT   r2, r1, RXSEL
    OR    r0, r2
    STT   r0, r1, MSGW
    MOVI  r3, RXB0 ^ RXB1
    XOR   r2, r3
    STT   r2, r1, RXSEL        ; the next message goes into the other buffer
rx_ans:
    MOVI  r2, 0
answer_upd:                    ; the status this message leaves; r2 = N data words
    MOVI  r1, 0
    LDT   r0, r1, OUTV
    STT   r0, r1, STV
    LDT   r0, r1, BCF
    CMPI  r0, 0
    JNZ   hunt                 ; broadcast: no status word
answer_n:                      ; status and r2 data words, RESP_US after the last word
    ADDT  RESP
    MOVI  r1, 0
    LDT   r3, r1, STV
    LDT   r3, r3, VAR
    LDT   r0, r1, HEAD
    OR    r3, r0
    STT   r3, r1, T0
    LDT   r0, r2, SOPT
    SEND  r0
a_end:
    ADDT  0xFFFFFF
    GETL  r1                   ; tmr = the packet's end: listen again from there
    JZ    hunt
    JMP   a_end                ; (not ended within 2^24 ticks: cannot happen, but then
                               ; escert knows the unit is idle at every SEND)

merr:                          ; message error: no status word (4.4.3.6)
    MOVI  r1, 0
    LDT   r0, r1, BCF
    ADDI  r0, 2
    STT   r0, r1, STV
    JMP   hunt

; Decode the command word in CMDW, in three parts (so the first data word's samples can be
; queued in between). deca: Z = 0 if it is not for this RT; else Z = 1, r0 = the word,
; r2 = the first three bits of its count / mode code field (decb adds the last two),
; r3 = 1 for a broadcast, and BCF. decb: r3 = the class,
; r2 = N; OUTV. rxbuf, for CL_RX: the buffer (the command word stored, sh = the first data
; word's place, ENDP, RXN).
deca:
    MOVI  r1, 0
    LDT   r0, r1, CMDW
    MOV   r2, r0
    SHR   r2, 22               ; the address cells
    MOVI  r3, 0
    CMPI  r2, ADDRC
    JZ    dc_ours
    CMPI  r2, 0x2AA            ; 11111: broadcast
    JNZ   dca_none
    MOVI  r3, 1
dc_ours:
    STT   r3, r1, BCF
    MOVI  r2, 0                ; the word count / mode code
    TSTI  r0, 0x200
    JZ    dc_w8
    ADDI  r2, 16
dc_w8:
    TSTI  r0, 0x80
    JZ    dc_w4
    ADDI  r2, 8
dc_w4:
    TSTI  r0, 0x20
    JZ    dc_w2
    ADDI  r2, 4
dc_w2:
    MOVI  r1, 0                ; Z = 1: for this RT
    RET
dca_none:
    MOVI  r1, 1                ; Z = 0
    RET
decb:
    TSTI  r0, 0x8              ; the count's last two bits
    JZ    dc_w1
    ADDI  r2, 2
dc_w1:
    TSTI  r0, 0x2
    JZ    dc_a
    ADDI  r2, 1
dc_a:
    MOV   r1, r0
    SHL   r1, 12
    SHR   r1, 22               ; the subaddress cells
    CMPI  r1, 0x155            ; 00000
    JZ    dc_mode
    CMPI  r1, 0x2AA            ; 11111
    JZ    dc_mode
    CMPI  r2, 0
    JNZ   dc_n
    MOVI  r2, 32               ; a count of 0 is 32
dc_n:
    MOV   r1, r0
    SHR   r1, 21
    TSTI  r1, 1                ; T/R
    JNZ   dc_tx
    MOVI  r1, 0
    STT   r3, r1, OUTV         ; receive: broadcast-received if broadcast
    MOVI  r3, CL_RX
    RET
dc_tx:
    CMPI  r3, 0
    JNZ   dc_none              ; a broadcast transmit command is not for this RT
    MOVI  r1, 0
    STT   r1, r1, OUTV
    MOVI  r3, CL_TX
    RET
dc_mode:                       ; r2 = the mode code
    MOV   r1, r0
    SHR   r1, 21
    TSTI  r1, 1
    JZ    dc_mrx
    CMPI  r2, 2                ; transmit status word
    JZ    dc_stat
    CMPI  r2, 1                ; synchronize
    JZ    dc_mok
    CMPI  r2, 8                ; reset remote terminal
    JZ    dc_mok
dc_ill:                        ; illegal: status with message error, no data
    ADDI  r3, 2
dc_mok:
    MOVI  r1, 0
    STT   r3, r1, OUTV
    MOVI  r2, 0
    MOVI  r3, CL_MODE
    RET
dc_stat:
    CMPI  r3, 0
    JNZ   dc_none              ; not allowed as a broadcast
    MOVI  r2, 0
    MOVI  r3, CL_STAT
    RET
dc_mrx:                        ; T/R = 0: codes 16 and up bring one data word
    CMPI  r2, 16
    JC    dc_ill
    CMPI  r2, 17               ; synchronize with data word
    JZ    dc_m17
    ADDI  r3, 2                ; any other: illegal
dc_m17:
    MOVI  r1, 0
    STT   r3, r1, OUTV
    MOVI  r2, 1
    MOVI  r3, CL_RX
    RET
dc_none:
    MOVI  r3, CL_NONE
    RET
rxbuf:                         ; r0 = the command word, r2 = N
    MOVI  r1, 0
    STT   r2, r1, RXN
    LDT   r3, r1, RXSEL
    STT   r0, r3, 0            ; the command word, for the host
    ADDI  r3, 1
    LDSH  r3
    ADD   r3, r2
    STT   r3, r1, ENDP
    RET

; Read one word: 36 samples of RXA, one every half bit from tmr (the sync check samples
; RXB for a data word: both must read 0). Returns Z = 1 and r0 = the 16 bits as sampled
; (bit k's halves at bits 31-2k, 30-2k), or Z = 0 for an invalid word. tmr ends 36 H on.
getcmd:
    SAMPLE RXA, MSB
    ADDT  H
    SAMPLE RXA, MSB
    ADDT  H
    LDLC  30
    JMP   gw
getdata:
    SAMPLE RXB, MSB
    ADDT  H
    SAMPLE RXB, MSB
    ADDT  H
    LDLC  30
    JMP   gw
getrest:                       ; the first twelve samples are already queued
    LDLC  20
gw:
    SAMPLE RXA, MSB
    ADDT  H
    DJNZ  gw
    GETI  r0                   ; sync check, bits 0 to 14
    LDLC  4
gw2:
    SAMPLE RXA, MSB            ; bit 15 and the parity bit
    ADDT  H
    DJNZ  gw2
    MOV   r3, r0
    SHR   r3, 30
    JNZ   gw_bad               ; the sync's second half is wrong
    SHL   r0, 2                ; bits 0 to 14 at 31..2
    MOV   r2, r0               ; parity: fold the first halves into bit 1
    MOV   r1, r2
    SHR   r1, 16
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 8
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 4
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 2
    XOR   r2, r1
    MOVI  r3, 0x5554           ; Manchester: the halves of bits 0 to 14 differ
    MOVHI r3, 0x5555
    MOV   r1, r0
    SHR   r1, 1
    XOR   r1, r0
    AND   r1, r3
    CMP   r1, r3
    JNZ   gw_bad
    GETI  r1                   ; bit 15 and parity
    LDT   r3, r1, BTAB
    TSTI  r3, 0x100
    JZ    gw_bad
    XOR   r2, r3
    TSTI  r2, 2
    JZ    gw_bad               ; even parity
    SHR   r1, 2
    OR    r0, r1
    MOVI  r2, 0                ; Z = 1
    RET
gw_bad:
    GETI  r1                   ; let every queued sample land
    MOVI  r2, 1                ; Z = 0
    RET
