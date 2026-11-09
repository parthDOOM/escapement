; Qi power receiver, its side of the communications (Qi Specification v1.3, Communications
; Protocol, Baseline Protocol; bit coding as the Communications Physical Layer): the chip
; modulates its load on pin MOD_PIN (1: the modulation load on) and sees the Power Signal on
; pin PWR_PIN (1: present, from a comparator outside).
;
; Bits at 2 kHz (tCLK 500 us), differential bi-phase: a transition at the start of every bit
; and one more in its middle for a ONE. A byte is 11 bits: a ZERO start bit, 8 data bits
; least significant first, a parity bit (ONE if the data byte has an even number of ONEs:
; odd parity) and a ONE stop bit. A packet is a preamble of 11 ONEs, the header, the message
; and the checksum (XOR of the header and the message bytes); the chip computes parity and
; checksum. After the last bit the modulation goes off (a transition at the end of the stop
; bit when it was on).
;
; The protocol (times from the specification's tables 11, 12 and 16):
;   Power Signal appears (a Digital Ping): SIG at tWake = 40 ms (19 to 64 ms)
;   ID, then CFG, each tStart = 15 ms (11.5 to 19 ms) after the end of the packet before
;   power transfer: CE every tInterval = 250 ms (start to start; at most 350 ms), the first
;   250 ms after the end of CFG; RP8 after every RP_EVERY-th CE (1.5 s; at most 4,050 ms),
;   45 ms after the end of that CE (at least tDelay 5 ms + tControl 24 ms + tWindow 8 ms +
;   tOffset 8 ms - 11 preamble bits x 0.5 ms = 39.5 ms)
;   Power Signal gone (checked between packets): back to waiting for a Digital Ping
;   end requested: EPT instead of the next packet (45 ms after a CE, else 15 ms), then EPT
;   every 15 ms while the Power Signal stays; also EPT, not SIG, at the next Digital Ping
;
; CFG: Reference Power REF_POWER x 0.5 W, Count 0, Window Size 8 ms and Offset 8 ms, no
; negotiation (Baseline Protocol). ID: version 1.2; the Manufacturer Code (the PRMC the
; manufacturer registered with the WPC) and Basic Device Identifier from the host.
;
; Host (POP): one word before each packet the chip sends (words sent while there is no
; Power Signal wait in the mailbox, two at most); [31:28] the command, then:
;   1: SIG value [7:0]   2: CE value [7:0] (two's complement)   3: RP8 value [7:0]
;   4: end power transfer, EPT code [7:0]      5: engage again (cancels 4 for the next
;      Digital Ping; an EPT already sent repeats until the Power Signal goes)
;   6: ID bytes 1-3 [23:0] (Manufacturer Code msb, lsb; Ext and Basic Device Identifier msb)
;   7: ID bytes 4-6 [23:0] (the rest of the Basic Device Identifier)
; Host (PUSH, when there is room): 1 power transfer phase begun, 2 Power Signal lost.

.default CTX, 0
.default MOD_PIN, 0
.default PWR_PIN, 1
.default CLK_KHZ, 25000
.default REF_POWER, 10                      ; x 0.5 W: 5 W
.default RP_EVERY, 6                        ; an RP8 after every 6th CE
.default TB, 0x90                           ; tape: packets, encoded words, state
.equ M, 1 << MOD_PIN
.equ P, 1 << PWR_PIN
.equ MS, CLK_KHZ
.equ HALF, (CLK_KHZ + 2) // 4               ; half of tCLK
.equ PREP, 2048                             ; ticks before a packet: the host word, encoding
.equ T_WAKE, 40 * MS
.equ T_START, 15 * MS
.equ T_AFTER_CE, 45 * MS
.equ T_INTERVAL, 250 * MS
.equ CE_LEN, 88 * HALF                      ; preamble 11 + 3 bytes x 11 bits, 2 halves each
.equ RP_LEN, 88 * HALF
; packets in the tape, 9 words each: header, message, zeros (the checksum, an XOR over all
; 9, is the same, so encoding is a loop of fixed length)
.equ SIGP, TB                               ; 0x01 SIG
.equ CEP, TB + 9                            ; 0x03 CE
.equ RPP, TB + 18                           ; 0x04 RP8
.equ EPTP, TB + 27                          ; 0x02 EPT
.equ IDP, TB + 36                           ; 0x71 ID, 7 bytes
.equ CFGP, TB + 45                          ; 0x51 CFG, 5 bytes
.equ SRCT, TB + 54                          ; per state: where its packet is
.equ LENT, TB + 60                          ; per state: header + message bytes
.equ PK, TB + 66                            ; the packet being encoded, its checksum
.equ OUT, TB + 75                           ; its 11-bit words; a zero word after the checksum
.equ STATE, TB + 85                         ; the packet being sent (below)
.equ CNT, TB + 86                           ; CEs until the next RP8
.equ ENDQ, TB + 87                          ; nonzero: end power transfer requested
.equ NB, TB + 88
.assert TB + 89 <= 256, "the tape area does not fit"
.assert REF_POWER <= 10, "Reference Power is at most 5 W (10)"
.assert 1920 <= CLK_KHZ * 1000 // (2 * HALF) <= 2080, "fCLK is 2 kHz +-4%"
.assert T_START >= PREP + 1024, "the clock is too slow for the 15 ms packet spacing"
.equ S_SIG, 0
.equ S_ID, 1
.equ S_CFG, 2
.equ S_CE, 3
.equ S_RP, 4
.equ S_EPT, 5

.tape SIGP, 0x01, 0x80, 0, 0, 0, 0, 0, 0, 0
.tape CEP, 0x03, 0x00, 0, 0, 0, 0, 0, 0, 0
.tape RPP, 0x04, 0x00, 0, 0, 0, 0, 0, 0, 0
.tape EPTP, 0x02, 0x00, 0, 0, 0, 0, 0, 0, 0
.tape IDP, 0x71, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0
.tape CFGP, 0x51, REF_POWER, 0x00, 0x00, 2 << 3 | 2, 0x00, 0, 0, 0
.tape SRCT, SIGP, IDP, CFGP, CEP, RPP, EPTP
.tape LENT, 2, 8, 6, 2, 2, 2

.entry CTX, main
.own   MOD_PIN, CTX

main:
    ADDT  20
    EMIT  M, 0, M            ; modulation off
idle:                        ; no Power Signal: wait for a Digital Ping
    RDTIME r3
    SETT  r3
    ADDT  CLK_KHZ            ; 1 ms
    WAITP P, P
    JNZ   idle
    RDTIME r3                ; the Power Signal is here: SIG (or EPT) at tWake
    SETT  r3
    ADDT  T_WAKE - PREP
    MOVI  r0, S_SIG
    MOVI  r2, 0
    STT   r0, r2, STATE
    STT   r2, r2, CNT        ; the first RP8 after the first CE
gap:                         ; tmr = the next packet's start - PREP
    WAITP P, 0               ; the Power Signal gone in the gap: wait for the next ping
    JZ    lost
    WAITT                    ; (no match: this is tmr)
    CALL  host               ; (called from here only: escert joins a routine's callers)
    MOVI  r3, 0
    LDT   r0, r3, STATE
    CMPI  r0, S_SIG
    JNZ   pkt
    LDT   r1, r3, ENDQ       ; end requested: EPT, not SIG, at this Digital Ping
    CMPI  r1, 0
    JZ    pkt
    MOVI  r0, S_EPT
    STT   r0, r3, STATE
pkt:
    LDT   r2, r0, SRCT
    LDT   r0, r0, LENT
    CALL  build
    ADDT  PREP
; ---- the packet: the preamble, then the words in OUT (r1 = the modulation level)
    MOVI  r1, 0
    MOVI  r2, 0
    MOVI  r0, 0x7FF          ; 11 ONEs
    LDLC  11
pb:
    ADDI  r1, M              ; a transition at the start of every bit
    EMITR M, r1, M
    ADDT  HALF
    SHR   r0, 1
    JNC   pz
    ADDI  r1, M              ; and one in the middle of a ONE
    EMITR M, r1, M
pz:
    ADDT  HALF
    DJNZ  pb
    LDT   r0, r2, OUT
    ADDI  r2, 1
    LDLC  11
    CMPI  r0, 0
    JNZ   pb
    EMIT  M, 0, M            ; modulation off at the end of the last bit
; ---- what comes next (tmr = the end of the packet)
    MOVI  r3, 0
    LDT   r0, r3, STATE
    CMPI  r0, S_EPT
    JZ    gstart             ; once EPT is sent, EPT until the Power Signal goes
    LDT   r1, r3, ENDQ
    CMPI  r1, 0
    JZ    noend
    MOVI  r1, S_EPT          ; end requested: EPT next
    STT   r1, r3, STATE
    CMPI  r0, S_CE
    JZ    gce
    JMP   gstart
noend:
    CMPI  r0, S_SIG
    JZ    nid
    CMPI  r0, S_ID
    JZ    ncfg
    CMPI  r0, S_CFG
    JZ    npt
    CMPI  r0, S_CE
    JZ    nce
    MOVI  r1, S_CE           ; (S_RP) the next CE, tInterval after the start of the last one
    STT   r1, r3, STATE
    ADDT  T_INTERVAL - CE_LEN - T_AFTER_CE - RP_LEN - PREP
    JMP   gap
nid:
    MOVI  r1, S_ID
    STT   r1, r3, STATE
    JMP   gstart
ncfg:
    MOVI  r1, S_CFG
    STT   r1, r3, STATE
    JMP   gstart
npt:                         ; CFG sent: the power transfer phase
    MOVI  r1, S_CE
    STT   r1, r3, STATE
    WAITT                    ; the end of CFG; report (a PUSH may wait), then a fresh time base
    TXREADY
    JZ    npt1
    MOVI  r1, 1
    PUSH  r1
npt1:
    RDTIME r3
    SETT  r3
    ADDT  T_INTERVAL - PREP
    JMP   gap
nce:                         ; a CE sent: an RP8 now and then, else the next CE
    LDT   r1, r3, CNT
    CMPI  r1, 0
    JZ    nce_rp
    MOVI  r2, 1
    SUB   r1, r2
    STT   r1, r3, CNT
    ADDT  T_INTERVAL - CE_LEN - PREP
    JMP   gap
nce_rp:
    MOVI  r1, RP_EVERY - 1
    STT   r1, r3, CNT
    MOVI  r1, S_RP
    STT   r1, r3, STATE
gce:
    ADDT  T_AFTER_CE - PREP
    JMP   gap
gstart:
    ADDT  T_START - PREP
    JMP   gap

lost:                        ; the Power Signal went away between packets
    TXREADY
    JZ    idle
    MOVI  r1, 2
    PUSH  r1
    JMP   idle

; ---- one host word, if there is one
host:
    RXREADY
    JZ    hdone
    POP   r0                 ; (RXREADY saw it: no wait)
    MOV   r1, r0
    SHR   r1, 28
    SHL   r0, 8
    SHR   r0, 8              ; [23:0]
    MOVI  r3, 0
    CMPI  r1, 1
    JZ    hsig
    CMPI  r1, 2
    JZ    hce
    CMPI  r1, 3
    JZ    hrp
    CMPI  r1, 4
    JZ    hend
    CMPI  r1, 5
    JZ    heng
    MOVI  r2, IDP + 2        ; 6: ID bytes 1-3; 7: 4-6
    CMPI  r1, 6
    JZ    hid
    MOVI  r2, IDP + 5
    CMPI  r1, 7
    JNZ   hdone
hid:                         ; [23:16] first
    MOV   r1, r0
    SHR   r1, 16
    STT   r1, r2, 0
    MOV   r1, r0
    SHL   r1, 16
    SHR   r1, 24
    STT   r1, r2, 1
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r2, 2
    RET
hsig:
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r3, SIGP + 1
    RET
hce:
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r3, CEP + 1
    RET
hrp:
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r3, RPP + 1
    RET
hend:
    SHL   r0, 24
    SHR   r0, 24
    STT   r0, r3, EPTP + 1
    MOVI  r1, 1
    STT   r1, r3, ENDQ
    RET
heng:
    STT   r3, r3, ENDQ
hdone:
    RET

; ---- encode the packet of r0 bytes at tape address r2 into OUT: its 9 words (header,
; message, zeros) and their XOR (the checksum) into PK, the checksum after the message;
; each byte of PK as an 11-bit word sent from bit 0; a zero word after the checksum's
build:
    MOVI  r3, 0
    STT   r0, r3, NB
    MOVI  r0, 0
    LDLC  9
bc:
    LDT   r1, r2, 0
    STT   r1, r3, PK
    XOR   r0, r1
    ADDI  r2, 1
    ADDI  r3, 1
    DJNZ  bc
    MOVI  r3, 0
    LDT   r3, r3, NB
    STT   r0, r3, PK         ; PK[n] = the checksum
    MOVI  r3, 0
    LDLC  9
be:                          ; r3 = byte index
    LDT   r1, r3, PK
    MOV   r0, r1             ; parity of the byte: fold to bit 0
    MOV   r2, r0
    SHR   r2, 4
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 2
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 1
    XOR   r0, r2
    TSTI  r0, 1
    JZ    beven
    MOVI  r2, 0x400          ; odd number of ONEs: parity ZERO; stop ONE
    JMP   bw
beven:
    MOVI  r2, 0x600          ; even: parity ONE
bw:
    MOV   r0, r1
    SHL   r0, 1              ; start ZERO in bit 0, the data in bits 1-8
    OR    r0, r2
    STT   r0, r3, OUT
    ADDI  r3, 1
    DJNZ  be
    MOVI  r3, 0
    LDT   r3, r3, NB
    MOVI  r2, 0
    STT   r2, r3, OUT + 1    ; the end
    RET
