; 10BASE-T both ways in one program, for the ping bench (README.md, Ethernet): firmware/eth_rx's
; receiver and firmware/eth_tx's transmitter on the chip's one line unit, which this context
; switches between them. 10BASE-T is half duplex, so a frame is either arriving or leaving.
;
; Listening, the line unit is configured to receive and armed. The context waits in slices of
; SLICE ticks: each slice ends early if a reception ends, and between slices the context checks
; the host's mailbox. Every SLICES slices (16 ms) it sends a normal link pulse on TD+/TD- with
; timed pin events, since the line unit is busy receiving.
;
; A received frame stays in the tape (words RB..), and the host reads it there: the context
; pushes only a header word and keeps sending link pulses while the host reads, with the
; receiver disarmed until the host releases the buffer. So the host's slow link never stalls
; the pulses, as it would if every word went through the mailbox. Where the context does
; wait on the host (a frame's words to send, room in the mailbox for a status word), it
; waits in slices too and keeps the pulses: a link partner drops the link after 50 to 150 ms
; without them, and the bit-banged host link takes about 0.7 s to push a large frame.
;
; Host (POP), one command word at a time:
;   1 << 28                       release the receive buffer: arm the receiver again
;   2 << 28 | length in bytes     send a frame: its bytes follow as little-endian words
;                                 (byte 0 in [7:0], destination first, no FCS; 1..448 bytes,
;                                 padded to 60 for the 802.3 minimum)
; Host (PUSH):
;   1 << 24 | [31] FCS good | [30:28] cause (0 OK, 4 the buffer full) | [13:0] bits received
;                                 a frame is in the tape at RB, its FCS included
;   2 << 24 | the line unit's status >> 25 (job, cause)
;                                 a frame was sent (job 4, cause 0)
; A frame that arrives while a frame is sent, or before the host releases the buffer, is
; missed, never corrupted. A send that finds a frame arriving waits for its end.

.default CTX, 0
.default TDP, 0
.default TDN, 1
.default RXP, 2
.default CLK_KHZ, 40000
.default BOTH, 1
.assert CLK_KHZ == 40000, 10BASE-T transmit needs Manchester half-bits of exactly 2 ticks (40 MHz)
.equ GAP, CLK_KHZ * 16                      ; 16 ms between link pulses
.equ SLICE, 400                             ; 10 us: the mailbox is checked this often
.equ SLICES, GAP // SLICE
.equ LEAD, 64
.equ W, CLK_KHZ // 10000                    ; a link pulse: one bit time, 100 ns
.equ M, (1 << TDP) | (1 << TDN)
.equ RB, 0x00                               ; tape: the receive buffer, 128 words
.equ RLIM, 16                               ;   at most 16 x 32 bytes
.equ TB, 0x80                               ;   the transmit buffer, 112 words
.equ CFGF, 0xF0                             ;   the transmit configuration (5 words)
.equ CFGR, 0xF5                             ;   the receive configuration (5 words)
.equ TBMAX, (CFGF - TB) * 4                 ; bytes: the longest frame send stores (448)

; transmit (firmware/eth_tx.asm): two 2-tick cells a bit, seven 0x55 and the SFD, CRC-32
.equ W0F, TDP | (TDN << 3) | (1 << 9) | (1 << 11) | (3 << 16) | (1 << 20) | (0x55 << 24)
.equ W1F, 2
.equ W2F, 0x1DB7 | (1 << 16) | (1 << 17) | (1 << 18) | (1 << 25)
.equ W3F, 1 | (1 << 3) | (2 << 11)
.equ W4F, 0x04C1 | (6 << 16) | (1 << 19)
; receive (firmware/eth_rx.asm): the cell decoder, both edges unless BOTH = 0, CRC-32
.equ PB, (CLK_KHZ // 10000) * (1 + BOTH)
.equ SB, (3 * PB + 3) // 4
.equ W0R, RXP | (RXP << 3) | (RXP << 6) | (1 << 11) | (1 << 19)
.equ W1R, PB | (SB << 20)
.equ W2R, 0x1DB7 | (1 << 16) | (1 << 25)
.equ W3R, 1 | (1 << 3) | (2 << 11)
.equ W4R, 0x04C1 | (1 << 21) | (BOTH << 24) | (1 << 25)

.entry CTX, main
.own   TDP, CTX
.own   TDN, CTX
.own   RXP, CTX
.tape  CFGF, W0F, W1F, W2F, W3F, W4F
.tape  CFGR, W0R, W1R, W2R, W3R, W4R

main:
    MOVI  r0, CFGR
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: receive; this context owns the line unit
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = the end of the CONFIG
    LDLC  SLICES             ; lc counts the slices to the next link pulse, in both states,
                             ; and only a pulse reloads it, so traffic never delays the pulses

; ---- listening: the receiver armed
arm:
    MOVI  r0, RB | (RLIM << 8)
    MOVHI r0, 0x1000
    LINE  r0                 ; RECV
listen:
    ADDT  SLICE
    GETL  r1                 ; Z: a reception ended (tmr = its end)
    JZ    got
    RXREADY                  ; Z: no word from the host
    JNZ   lcmd
    DJNZ  listen
    CALL  pulse
    LDLC  SLICES
    JMP   listen
lcmd:
    POP   r2
    CALL  anchor
    MOV   r3, r2
    SHR   r3, 28
    CMPI  r3, 2
    JNZ   listen             ; a release while armed: nothing to do
    CALL  send
    JMP   arm

; ---- a reception ended: r1 = the line unit's status
got:
    CALL  anchor             ; tmr = now (the end tick GETL gave may be any age)
    GETM  r2, 3              ; bits received
    CMPI  r2, 0
    JZ    arm                ; nothing after the hunt: noise, or no SFD
    SHL   r1, 4
    SHR   r1, 29             ; cause
    SHL   r1, 28
    OR    r1, r2
    MOVI  r0, 0
    MOVHI r0, 0x0100
    OR    r1, r0             ; tag 1: a received frame
    GETM  r3, 4              ; the CRC register
    MOVI  r0, 0xDD7B
    MOVHI r0, 0xC704
    CMP   r3, r0
    JNZ   tell               ; a bad FCS (or a frame cut short)
    MOVI  r0, 0
    MOVHI r0, 0x8000
    OR    r1, r0
tell:
    TXREADY                  ; Z: the mailbox is full (the host has not read the last word)
    JNZ   tput
    ADDT  SLICE
    WAITT
    DJNZ  tell
    CALL  pulse
    LDLC  SLICES
    JMP   tell
tput:
    PUSH  r1                 ; (there is room: only this context fills the mailbox)
    CALL  anchor

; ---- holding a frame for the host: the receiver disarmed
held:
    ADDT  SLICE
    WAITT
    RXREADY
    JNZ   hcmd
    DJNZ  held
    CALL  pulse
    LDLC  SLICES
    JMP   held
hcmd:
    POP   r2
    CALL  anchor
    MOV   r3, r2
    SHR   r3, 28
    CMPI  r3, 1
    JZ    arm                ; released
    CMPI  r3, 2
    JNZ   held
    CALL  send
    JMP   held

; ---- a link pulse LEAD ticks after tmr; tmr = its end
pulse:
    ADDT  LEAD
    EMIT  M, 1 << TDP, M     ; TD+ high, TD- low
    ADDT  W
    EMIT  M, 0, 0            ; released
    RET

; ---- tmr = now, after a wait on the host
anchor:
    RDTIME r0
    SETT  r0
    RET

; ---- send the frame the host is pushing: r2 = its command word. Returns with the line unit
; configured to receive (not armed) and tmr = the end of that CONFIG, or of the wait for
; room to push the status word after it. Every word the host pushes is popped, so the
; mailbox stays in step whatever the length; only the words that fit before CFGF are
; stored and sent, so a length over TBMAX can never overwrite the configurations (the host
; keeps to TBMAX; this is the firmware's own guard). It waits for the host in slices, as
; listening does, and sends the link pulses that fall due: inline, as send is itself a
; CALL. A slice that tmr has already passed (words came back to back) ends at once, so the
; slices catch up with the time, and each pulse is re-anchored first.
send:
    MOV   r1, r2
    SHL   r1, 16
    SHR   r1, 16             ; length in bytes
    MOV   r3, r1
    ADDI  r3, 3
    SHR   r3, 2
    ADDI  r3, TB             ; r3 = TB + the words the host pushes
    MOVI  r0, TB
store:
    CMP   r0, r3
    JZ    stored             ; (a length of 0 pushes nothing)
swait:
    RXREADY                  ; Z: no word from the host yet
    JNZ   sword
    ADDT  SLICE
    WAITT
    DJNZ  swait
    RDTIME r2                ; a link pulse (r2 is free until the POP)
    SETT  r2
    ADDT  LEAD
    EMIT  M, 1 << TDP, M
    ADDT  W
    EMIT  M, 0, 0
    LDLC  SLICES
    JMP   swait
sword:
    POP   r2
    CMPI  r0, CFGF
    JNC   skip               ; C: borrow, r0 < CFGF
    STT   r2, r0, 0
skip:
    ADDI  r0, 1
    JMP   store
stored:
    CMPI  r1, TBMAX + 1
    JC    fits
    MOVI  r1, TBMAX          ; send what was stored
fits:
    MOVI  r0, CFGF
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: transmit (cancels an armed receiver)
    RDTIME r0                ; tmr = now: the POPs above wait on the host, and the LINE on a
    SETT  r0                 ; frame arriving, for any time
    ADDT  0xFFFFFF
    GETL  r2                 ; tmr = the end of the CONFIG
    SHL   r1, 3              ; bits
    MOV   r0, r1
    SHL   r0, 25
    SHR   r0, 17             ; (bits mod 128) << 8
    SHR   r1, 7
    SHL   r1, 24             ; (bits / 128) << 24
    OR    r0, r1
    MOVI  r1, TB | 0x8000    ; stream word, append the CRC
    MOVHI r1, 0xD5           ; the header: the SFD
    OR    r0, r1
    ADDT  LEAD
    SEND  r0                 ; the frame at tmr
    ADDT  0xFFFFFF
    GETL  r1                 ; tmr = its end (TP_IDL done, pins released)
    SHR   r1, 25
    MOVI  r0, 0
    MOVHI r0, 0x0200
    OR    r1, r0             ; tag 2: a frame sent
    MOVI  r0, CFGR
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: receive (the unit lets go of TD+/TD- for the pulses)
    RDTIME r0                ; tmr = now: the LINE waits for the send to end, for any time
    SETT  r0
    ADDT  0xFFFFFF
    GETL  r2                 ; tmr = the end of the CONFIG
sput:
    TXREADY                  ; Z: the mailbox is full
    JNZ   sputn
    ADDT  SLICE
    WAITT
    DJNZ  sput
    RDTIME r0                ; a link pulse (r0 is free here)
    SETT  r0
    ADDT  LEAD
    EMIT  M, 1 << TDP, M
    ADDT  W
    EMIT  M, 0, 0
    LDLC  SLICES
    JMP   sput
sputn:
    PUSH  r1                 ; (there is room: only this context fills the mailbox)
    RDTIME r0                ; tmr = now (escert does not tie this PUSH to the TXREADY
    SETT  r0                 ; above, so it lets the PUSH wait)
    RET
