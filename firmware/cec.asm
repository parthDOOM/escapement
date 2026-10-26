; HDMI-CEC adapter: the one-wire control bus of TVs, players and receivers. It follows
; every frame on the bus, acknowledges those sent to its logical address, and sends the
; host's frames as an initiator, with arbitration, acknowledgement and retries.
; Context 0, the CEC line on pin 0 (open-drain, an external pull-up).
;
; Timing (Linux, drivers/media/cec/core/cec-pin.c, after HDMI 1.4 CEC): start bit low
; 3.7 ms of 4.5 ms; data bit 2.4 ms, low 1.5 ms for a 0 and 0.6 ms for a 1; sampled at
; 1.05 ms. A block is 8 bits MSB first, EOM and ACK. A follower acknowledges a frame
; addressed to it by holding the ACK bit low until 1.5 ms. Signal free time before a
; frame: 5 bit periods (7 after one's own frame, 3 for a retry).
;
; Receiving, every bit is checked against the receive windows of cec-pin.c (start bit
; low 3.5-3.9 ms and 4.3-4.7 ms long, bits 2.05-2.75 ms, low part over by 1.75 ms);
; anything else is dropped and the adapter waits for a free bus. The sample point and
; the acknowledgement are events anchored to the falling edge as WAITP saw it (at most
; 10 ticks, 0.4 us, after it reached the pin). Sending, the bits are events too: low at
; the bit start, at 0.6 ms the bit itself (EMITX: a 1 releases the line, a 0 holds it),
; a sample at 1.05 ms, released at 1.5 ms. A 1 sent and a 0 sampled is lost arbitration:
; the adapter stops and tries again later (the frame that won is not received).
;
; Host to chip (POP): [31] = 1: set the logical address to [3:0]. Otherwise [4:0] = n,
; then n words, one byte each: a frame of n bytes (header first) to send.
; Chip to host (PUSH): each block received, [10] first block of a frame, [9:2] the byte,
; [1] EOM, [0] the ACK bit as sampled (0: acknowledged, for a frame to one device). Each
; frame sent: 0x40000000 acknowledged (a broadcast: not refused), 0x40000001 not
; acknowledged after RETRIES retries, 0x40000002 arbitration lost as often.

.default CTX, 0
.default PIN, 0
.default LA, 4                              ; logical address: frames to it are acknowledged
.default RETRIES, 4
.default CLK_KHZ, 25000
.default TB, 0xB0                           ; tape: 20 words
.equ C, 1 << PIN
.equ MS, CLK_KHZ                            ; ticks per millisecond
.equ T_SL, 37 * MS // 10                    ; start bit: low 3.7 ms
.equ T_ST, 45 * MS // 10                    ;   4.5 ms
.equ T_1L, 6 * MS // 10                     ; a 1: low 0.6 ms
.equ T_0L, 15 * MS // 10                    ; a 0: low 1.5 ms
.equ T_BIT, 24 * MS // 10                   ; a bit: 2.4 ms
.equ T_SA, 105 * MS // 100                  ; sample point: 1.05 ms
.equ R_SLMIN, 35 * MS // 10                 ; receive windows, from the edge as seen
.equ R_SLMAX, 39 * MS // 10 + 12
.equ R_STMIN, 43 * MS // 10
.equ R_STMAX, 47 * MS // 10 + 12
.equ R_HI, 175 * MS // 100 + 12
.equ R_BMIN, 205 * MS // 100
.equ R_BMAX, 275 * MS // 100 + 12
.equ ALEAD, 48                              ; edge seen to the acknowledgement drive
.equ LEAD, 16                               ; RDTIME to the start bit: 3 instructions
.equ LAW, TB                                ; logical address
.equ CNT, TB + 1                            ; bytes of the frame to send (0: none)
.equ TRY, TB + 2                            ; retries left
.equ BUF, TB + 3                            ; the frame to send, one byte per word

.entry CTX, main
.own   PIN, CTX
.od    PIN

main:
    ADDT  20
    EMIT  C, C, C            ; released
    MOVI  r0, LA
    MOVI  r3, 0
    STT   r0, r3, LAW
    STT   r3, r3, CNT
; ---- wait for a free bus: released, then quiet for the signal free time
free5:
    CALL  high
    ADDT  5 * T_BIT
wfree:
    WAITP C, 0               ; a falling edge, or the bus free until tmr
    JZ    rx
    MOVI  r3, 0
    LDT   r0, r3, CNT
    MOV   r0, r0
    JNZ   tx                 ; a frame to send, and the bus is free
    RXREADY
    JNZ   host
    ADDT  MS                 ; still free: look again in 1 ms
    JMP   wfree
free3:
    CALL  high
    ADDT  3 * T_BIT
    JMP   wfree
free7:
    CALL  high
    ADDT  7 * T_BIT
    JMP   wfree
high:                        ; wait until our events are done and the line is released;
    WAITT                    ; tmr = that moment (tmr was the last event's deadline)
    ADDT  0xFFFFFF
    WAITP C, C
    RDTIME r1
    SETT  r1
    RET

; ---- a host word: a new logical address, or a frame to send
host:
    POP   r0
    MOVI  r3, 0
    MOV   r2, r0
    SHL   r2, 1              ; C = [31]
    JNC   load
    MOVI  r2, 15
    AND   r0, r2
    STT   r0, r3, LAW
    JMP   wfree
load:
    MOVI  r2, 31
    AND   r0, r2             ; n bytes
    STT   r0, r3, CNT
    MOVI  r1, RETRIES
    STT   r1, r3, TRY
    MOVI  r2, 0
lbyte:
    POP   r1
    STT   r1, r2, BUF
    ADDI  r2, 1
    CMP   r2, r0
    JNZ   lbyte
    JMP   free5              ; (the bus may have been busy meanwhile)

; ---- receive. r0 = the block, r1 = the falling edge of the current bit (as seen),
; r3 = [1] first block, [0] the frame is to us
rx:
    RDTIME r1
    SETT  r1
    ADDT  R_SLMIN
    WAITP C, C               ; rises before 3.5 ms: not a start bit
    JZ    junk
    SETT  r1
    ADDT  R_SLMAX
    WAITP C, C               ; ... but by 3.9 ms
    JZ    sthi
    JMP   junk
sthi:
    SETT  r1
    ADDT  R_STMIN
    WAITP C, 0               ; the first bit: not before 4.3 ms
    JZ    junk
    SETT  r1
    ADDT  R_STMAX
    WAITP C, 0               ; ... but by 4.7 ms
    RDTIME r1
    JZ    rfirst
    JMP   junk
rfirst:
    MOVI  r3, 2
rblock:
    MOVI  r0, 0
    LDLC  8
rbit:
    SETT  r1
    ADDT  T_SA
    SAMPLE PIN, MSB
    GETI  r2
    SHL   r0, 1
    OR    r0, r2
    CALL  edge
    DJNZ  rbit
    TSTI  r3, 2              ; the header block: is the frame to us?
    JZ    reom
    MOV   r2, r0
    SHL   r2, 28
    SHR   r2, 28             ; destination
    CMPI  r2, 15
    JZ    reom               ; broadcast: never driven
    LDSH  r0                 ; (sh keeps the byte)
    MOVI  r0, 0
    LDT   r0, r0, LAW
    CMP   r2, r0
    STSH  r0
    JNZ   reom
    ADDI  r3, 1
reom:                        ; r1 = the EOM bit's edge
    SETT  r1
    ADDT  T_SA
    SAMPLE PIN, MSB
    GETI  r2
    SHL   r0, 1
    OR    r0, r2             ; byte << 1 | EOM
    CALL  edge
    SETT  r1                 ; the ACK bit
    TSTI  r3, 1
    JZ    rnoack
    ADDT  ALEAD
    EMIT  C, 0, C            ; acknowledge: hold it low until 1.5 ms
    ADDT  T_SA - ALEAD
    SAMPLE PIN, MSB
    ADDT  T_0L - T_SA
    EMIT  C, C, C
    JMP   rack
rnoack:
    ADDT  T_SA
    SAMPLE PIN, MSB
rack:
    GETI  r2
    SHL   r0, 1
    OR    r0, r2             ; byte << 2 | EOM << 1 | ACK
    TSTI  r3, 2
    JZ    rpush
    ADDI  r0, 0x400          ; the first block
    MOVI  r2, 1
    AND   r3, r2
rpush:
    PUSH  r0
    TSTI  r0, 2
    JNZ   free5              ; EOM: the frame is over
    CALL  edge               ; the next block
    JMP   rblock
junk:                        ; not a frame we can follow: wait for a free bus
    JMP   free5

edge:                        ; the bit at r1 ends its low part, and the next one starts in
    SETT  r1                 ; its window: r1 = that edge. Otherwise the frame is dropped
    ADDT  R_HI               ; (no return)
    WAITP C, C
    JNZ   junk
    SETT  r1
    ADDT  R_BMIN
    WAITP C, 0
    JZ    junk
    SETT  r1
    ADDT  R_BMAX
    WAITP C, 0
    RDTIME r1
    JNZ   junk
    RET

; ---- send the frame in BUF: start bit, then blocks; every edge an event
tx:
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1
    EMIT  C, 0, C            ; start bit
    ADDT  T_SL
    EMIT  C, C, C
    ADDT  T_ST - T_SL
    MOVI  r2, 0              ; block index
tblock:
    LDT   r0, r2, BUF
    SHL   r0, 24
    LDSH  r0
    LDLC  8
tbit:
    CALL  txbit
    JZ    lost               ; a 1 sent, a 0 sampled
    DJNZ  tbit
    MOVI  r3, 0
    LDT   r3, r3, CNT
    MOV   r0, r2
    ADDI  r0, 1
    CMP   r0, r3
    JZ    tlast
    MOVI  r0, 0              ; EOM 0
    JMP   teom
tlast:
    MOVI  r0, 0
    MOVHI r0, 0x8000         ; EOM 1
teom:
    LDSH  r0
    CALL  txbit
    JZ    lost
    MOVI  r0, 0
    MOVHI r0, 0x8000
    LDSH  r0
    CALL  txbit              ; ACK, sent as a 1: Z = 1 if a follower held it low
    JZ    tack0
    MOVI  r1, 1
    JMP   tack
tack0:
    MOVI  r1, 0
tack:                        ; r1 = the ACK bit as sampled
    MOVI  r3, 0
    LDT   r0, r3, BUF
    MOVI  r3, 15
    AND   r0, r3
    CMP   r0, r3
    JZ    tbcast
    MOV   r1, r1             ; to one device: acknowledged if 0
    JNZ   nack
    JMP   tnext
tbcast:
    MOV   r1, r1             ; broadcast: refused if 0
    JZ    nack
tnext:
    ADDI  r2, 1
    MOVI  r3, 0
    LDT   r3, r3, CNT
    CMP   r2, r3
    JNZ   tblock
    MOVI  r3, 0              ; sent
    STT   r3, r3, CNT
    MOVI  r0, 0
    MOVHI r0, 0x4000
    PUSH  r0
    JMP   free7
nack:
    MOVI  r0, 1
    CALL  retry
    JMP   free3
lost:
    MOVI  r0, 2
    CALL  retry
    JMP   free5
retry:                       ; one retry less; after the last, report r0 and drop the frame
    MOVI  r3, 0
    LDT   r1, r3, TRY
    MOV   r1, r1
    JZ    rgive
    MOVI  r2, 1
    SUB   r1, r2
    STT   r1, r3, TRY
    RET
rgive:
    STT   r3, r3, CNT
    MOVHI r0, 0x4000
    PUSH  r0
    RET

txbit:                       ; the next bit of sh at tmr, tmr += 2.4 ms; Z = 1 if a 1 was
    EMIT  C, 0, C            ; sent and a 0 sampled
    ADDT  T_1L
    EMITX C, MSB, C          ; 0.6 ms: a 1 releases the line, a 0 holds it; C = the bit
    ADDT  T_SA - T_1L
    SAMPLE PIN, MSB
    ADDT  T_0L - T_SA
    EMIT  C, C, C            ; 1.5 ms: released
    ADDT  T_BIT - T_0L
    JC    tone
    GETI  r1                 ; (clears the sample)
    MOVI  r1, 1              ; Z = 0
    RET
tone:
    GETI  r1                 ; Z = 1 if the line was low
    RET
