; 10BASE-T Ethernet transmitter on the line unit (v1.6): normal link pulses every 16 ms
; while idle, and each frame the host sends as one packet on the wire: preamble, SFD, the
; frame, its CRC-32 frame check sequence and the end-of-transmission pulse (TP_IDL).
; TD+ on pin TDP and TD- on pin TDN, through a 10BASE-T transformer (or an RJ45 jack with
; magnetics) and series resistors; the pins are released between transmissions.
;
; The line unit does all the bit work (19-LINE-UNIT, the extended configuration): each
; bit is two Manchester cells of 2 ticks (50 ns at 40 MHz, so 10 Mb/s exactly), seven
; prefix bytes 0x55 and the SFD 0xD5 as the header, CRC-32 (0x04C11DB7, from all ones,
; inverted) over the frame only, three bit times of TD+ high, then release. A link pulse
; is a second configuration with no prefix, header or data: one bit time (100 ns) high.
;
; Host (POP): a frame's length in bytes (1..960; pad to 60 for the 802.3 minimum), then
; its bytes as little-endian words (byte 0 in [7:0]), destination address first, no FCS.
; Host (PUSH): the line unit's status once the frame is on the wire (job SEND, cause 0).
; While a frame is being sent no link pulse is due; the pulses resume 16 ms after it.

.default CTX, 0
.default TDP, 0
.default TDN, 1
.default CLK_KHZ, 40000
.assert CLK_KHZ == 40000, 10BASE-T needs Manchester half-bits of exactly 2 ticks (40 MHz)
.default TB, 0xF0                           ; tape: the two configurations (10 words)
.default FB, 0x00                           ; frame buffer: up to 240 words (960 bytes)
.equ GAP, CLK_KHZ * 16                      ; 16 ms between link pulses
.equ LEAD, 64
.equ POLLS, (GAP - 400) // 12               ; mailbox polls in a gap, 12 ticks each
.equ W0P, TDP | (TDN << 3) | (1 << 9) | (1 << 11) | (1 << 16) | (1 << 20)    ; a pulse
.equ W0F, TDP | (TDN << 3) | (1 << 9) | (1 << 11) | (3 << 16) | (1 << 20) | (0x55 << 24)
.equ W1E, 2                                 ; a cell: 2 ticks
.equ W2P, 1 << 25                           ; ext, no CRC
.equ W2F, 0x1DB7 | (1 << 16) | (1 << 17) | (1 << 18) | (1 << 25)   ; CRC-32 low half, header
.equ W3E, 1 | (1 << 3) | (2 << 11)          ; two cells: a 0 is high-low, a 1 low-high
.equ W4P, 1 << 19                           ; end symbols L = 1, no J
.equ W4F, 0x04C1 | (6 << 16) | (1 << 19)    ; CRC-32 high half, the prefix 7 times
.equ CFGP, TB
.equ CFGF, TB + 5

.entry CTX, main
.own   TDP, CTX
.own   TDN, CTX
.tape  CFGP, W0P, W1E, W2P, W3E, W4P
.tape  CFGF, W0F, W1E, W2F, W3E, W4F

main:
    MOVI  r0, CFGP
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: link pulses, this context owns the line unit
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = the end of the CONFIG
    ADDT  LEAD
pulse:
    MOVI  r2, 0
    SEND  r2                 ; a link pulse at tmr
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = its end
    ADDT  GAP - 4            ; the next one 16 ms after this one
    LDLC  POLLS
poll:
    RXREADY                  ; Z: no word from the host
    JNZ   frame
    DJNZ  poll
    JMP   pulse

frame:
    POP   r1                 ; length in bytes
    MOV   r3, r1
    ADDI  r3, 3
    SHR   r3, 2
    ADDI  r3, FB             ; r3 = the buffer's end
    MOVI  r0, FB
store:
    POP   r2
    STT   r2, r0, 0
    ADDI  r0, 1
    CMP   r0, r3
    JNZ   store
    MOVI  r0, CFGF
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: frames
    ADDT  0xFFFFFF
    GETL  r2                 ; tmr = the end of the CONFIG
    SHL   r1, 3              ; bits
    MOV   r0, r1
    SHL   r0, 25
    SHR   r0, 17             ; (bits mod 128) << 8
    SHR   r1, 7
    SHL   r1, 24             ; (bits / 128) << 24
    OR    r0, r1
    MOVI  r1, FB | 0x8000    ; stream word, append the CRC
    MOVHI r1, 0xD5           ; the header: the SFD
    OR    r0, r1
    ADDT  LEAD
    SEND  r0                 ; the frame at tmr
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = its end (TP_IDL done, pins released)
    PUSH  r1
    MOVI  r0, CFGP
    MOVHI r0, 0x3000
    LINE  r0                 ; back to link pulses
    ADDT  0xFFFFFF
    GETL  r2
    ADDT  GAP
    JMP   pulse
