; UDP over IPv4 over 10BASE-T (v1.6): the host gives a payload, the chip puts a complete
; datagram on the wire, one a PC's network stack accepts: Ethernet header, IPv4 header
; with its checksum computed here, UDP header, the payload, padding to the 802.3 minimum,
; and the CRC-32 frame check sequence (the line unit). Link pulses every 16 ms while idle,
; as eth_tx.asm (whose wiring and line coding this shares; 40 MHz).
;
; The headers are a template in the frame buffer, fixed at assembly from the addresses and
; ports below; per datagram only the lengths and the checksum change. The IPv4 header
; checksum is the one's complement of the one's-complement sum of its 16-bit words: the
; constant words are summed by the assembler (CSUM), the total length added here. The UDP
; checksum is 0, which IPv4 defines as "not computed". No fragmentation (DF set), TTL 64.
;
; Host (POP): the payload's length in bytes (0..916), then its bytes as little-endian
; words (byte 0 in [7:0]), zero after the last byte. Host (PUSH): the line unit's status
; once the frame is on the wire (job SEND, cause 0).

.default CTX, 0
.default TDP, 0
.default TDN, 1
.default CLK_KHZ, 40000
.assert CLK_KHZ == 40000, 10BASE-T needs Manchester half-bits of exactly 2 ticks (40 MHz)
.default DMAC_HI, 0xFFFF                    ; destination MAC 47:32 (broadcast)
.default DMAC_LO, 0xFFFFFFFF                ;                 31:0
.default SMAC_HI, 0x0200                    ; source MAC (locally administered)
.default SMAC_LO, 0x00000001
.default SIP, 0xC0A80164                    ; 192.168.1.100
.default DIP, 0xFFFFFFFF                    ; 255.255.255.255 (limited broadcast)
.default SPORT, 5000
.default DPORT, 5000
.default TTL, 64
.default TB, 0xF0                           ; tape: the two configurations (10 words), scratch
.default FB, 0x00                           ; frame buffer: up to 240 words
.assert FB + 240 <= TB, the frame buffer (240 words) ends before the configurations
.equ GAP, CLK_KHZ * 16                      ; 16 ms between link pulses
.equ LEAD, 64
.equ POLLS, (GAP - 400) // 12
.equ W0P, TDP | (TDN << 3) | (1 << 9) | (1 << 11) | (1 << 16) | (1 << 20)
.equ W0F, TDP | (TDN << 3) | (1 << 9) | (1 << 11) | (3 << 16) | (1 << 20) | (0x55 << 24)
.equ W1E, 2
.equ W2P, 1 << 25
.equ W2F, 0x1DB7 | (1 << 16) | (1 << 17) | (1 << 18) | (1 << 25)
.equ W3E, 1 | (1 << 3) | (2 << 11)
.equ W4P, 1 << 19
.equ W4F, 0x04C1 | (6 << 16) | (1 << 19)
.equ CFGP, TB
.equ CFGF, TB + 5
.equ SCR, TB + 10                           ; the payload length, while the frame is built
; the header template, bytes 0..41 as little-endian words (network order is big-endian)
.equ H0, (DMAC_HI >> 8) | ((DMAC_HI & 255) << 8) | (((DMAC_LO >> 24) & 255) << 16) | (((DMAC_LO >> 16) & 255) << 24)
.equ H1, ((DMAC_LO >> 8) & 255) | ((DMAC_LO & 255) << 8) | ((SMAC_HI >> 8) << 16) | ((SMAC_HI & 255) << 24)
.equ H2, ((SMAC_LO >> 24) & 255) | (((SMAC_LO >> 16) & 255) << 8) | (((SMAC_LO >> 8) & 255) << 16) | ((SMAC_LO & 255) << 24)
.equ H3, 0x08 | (0x45 << 16)                ; EtherType IPv4; version 4, 5 header words
.equ H5, 0x40 | (TTL << 16) | (17 << 24)    ; DF; TTL; protocol UDP
.equ SIPU, (SIP >> 24) | (((SIP >> 16) & 255) << 8)
.equ H7, ((SIP >> 8) & 255) | ((SIP & 255) << 8) | ((DIP >> 24) << 16) | (((DIP >> 16) & 255) << 24)
.equ H8, ((DIP >> 8) & 255) | ((DIP & 255) << 8) | ((SPORT >> 8) << 16) | ((SPORT & 255) << 24)
.equ DPSW, (DPORT >> 8) | ((DPORT & 255) << 8)
.equ CSUM0, 0x4500 + 0x4000 + ((TTL << 8) | 17) + (SIP >> 16) + (SIP & 0xFFFF) + (DIP >> 16) + (DIP & 0xFFFF)
.equ CSUM1, (CSUM0 & 0xFFFF) + (CSUM0 >> 16)
.equ CSUM, (CSUM1 & 0xFFFF) + (CSUM1 >> 16)

.entry CTX, main
.own   TDP, CTX
.own   TDN, CTX
.tape  CFGP, W0P, W1E, W2P, W3E, W4P
.tape  CFGF, W0F, W1E, W2F, W3E, W4F
.tape  FB, H0, H1, H2, H3, 0, H5, SIPU << 16, H7, H8, DPSW, 0

main:
    MOVI  r0, CFGP
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: link pulses, this context owns the line unit
    ADDT  0xFFFFFF
    GETL  r1
    ADDT  LEAD
pulse:
    MOVI  r2, 0
    SEND  r2                 ; a link pulse at tmr
    ADDT  0xFFFFFF
    GETL  r1
    ADDT  GAP - 4
    LDLC  POLLS
poll:
    RXREADY
    JNZ   frame
    DJNZ  poll
    JMP   pulse

frame:
    POP   r1                 ; payload bytes
    MOVI  r0, 0
    STT   r1, r0, SCR
    STT   r0, r0, FB + 11    ; zeros up to byte 60, for padding
    STT   r0, r0, FB + 12
    STT   r0, r0, FB + 13
    STT   r0, r0, FB + 14
    ADDI  r1, 3
    SHR   r1, 2
    ADDI  r1, FB + 10        ; r1: the last word the payload reaches
    MOVI  r0, FB + 10
    MOVI  r3, 0              ; the half word carried over (the UDP checksum, 0)
; The payload starts at byte 42, mid-word: each host word's low half ends one frame word,
; its high half starts the next.
store:
    CMP   r0, r1
    JZ    last
    POP   r2
    LDSH  r2
    SHL   r2, 16
    OR    r2, r3
    STT   r2, r0, 0
    STSH  r3
    SHR   r3, 16
    ADDI  r0, 1
    JMP   store
last:
    STT   r3, r0, 0
    MOVI  r0, 0
    LDT   r1, r0, SCR        ; r1 = payload bytes
    MOV   r2, r1             ; UDP length, after the destination port
    ADDI  r2, 8
    MOV   r3, r2
    SHR   r3, 8
    SHL   r2, 8
    OR    r2, r3             ; byte-swapped in [15:0]
    SHL   r2, 16
    ADDI  r2, DPSW
    STT   r2, r0, FB + 9
    MOV   r2, r1             ; IPv4 total length; identification 0
    ADDI  r2, 28
    MOV   r3, r2
    SHR   r3, 8
    SHL   r2, 8
    OR    r2, r3
    MOVHI r2, 0
    STT   r2, r0, FB + 4
    MOV   r2, r1             ; the header checksum: CSUM + total length, folded once
    ADDI  r2, 28             ; (at most 0xFFFF + 944: one fold suffices), complemented
    ADDI  r2, CSUM
    MOV   r3, r2
    SHR   r3, 16
    SHL   r2, 16
    SHR   r2, 16
    ADD   r2, r3
    MOVI  r3, 0xFFFF
    XOR   r2, r3
    MOV   r3, r2
    SHR   r3, 8
    SHL   r2, 8
    OR    r2, r3
    MOVHI r2, SIPU           ; the source address's first two bytes above it
    STT   r2, r0, FB + 6
    ADDI  r1, 42             ; frame bytes, at least 60
    CMPI  r1, 60
    JNC   long
    MOVI  r1, 60
long:
    MOVI  r0, CFGF
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: frames
    ADDT  0xFFFFFF
    GETL  r2
    SHL   r1, 3              ; bits
    MOV   r0, r1
    SHL   r0, 25
    SHR   r0, 17             ; (bits mod 128) << 8
    SHR   r1, 7
    SHL   r1, 24             ; (bits / 128) << 24
    OR    r0, r1
    MOVI  r1, FB | 0x8000    ; stream words, append the CRC
    MOVHI r1, 0xD5           ; the header: the SFD
    OR    r0, r1
    ADDT  LEAD
    SEND  r0                 ; the datagram at tmr
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
