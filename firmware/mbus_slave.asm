; M-Bus slave, data link layer: the chip is a meter's bus interface behind an M-Bus transceiver
; (such as the TSS721, which turns the master's voltage levels into a logic RX line and the
; chip's TX line into the slave's current levels). It receives the master's telegrams,
; checks them, answers SND_NKE and SND_UD with E5h and REQ_UD2 with an RSP_UD telegram
; holding the data the host has given it, and tells the host what it did. Held-out round,
; written 2026-11-25 on v3.1 (cb47436), from "The M-Bus: A Documentation", Rev. 4.8 (M-Bus
; Usergroup; the wired M-Bus specification at m-bus.com, which follows EN 1434-3 and IEC
; 870-5; EN 13757-2 itself is not public): 4.1 and 4.4 (Mark = 1 = idle; the TSS721), 5.1
; (transmission parameters), 5.2 (the FT 1.2 telegram formats), 5.3 (C, A, CI fields, check
; sum), 5.4 (the reply window, fault checks), 5.5 (FCB), 6.1 to 6.3 (CI codes, data
; structures).
;
;   pin RX_PIN (0)   in:  the master's characters (Mark = 1, idle high)
;   pin TX_PIN (1)   out: the reply (Mark = 1, idle high)
; Context CTX (0), spanning (context 2's page). Clock CLK_KHZ (25 MHz by default). BAUD
; (2400 by default; 300 to 38400).
;
; A character (5.1): start (Space), 8 data bits LSB first, even parity, stop (Mark); 11
; bits. Each received bit is sampled in its middle, timed from the start bit's falling edge;
; start, parity and stop are checked. A telegram (5.2) is E5h, a short frame (10h C A CS 16h)
; or a long/control frame (68h L L 68h C A CI data CS 16h, L = data + 3); the slave checks
; what 5.4 lists: the start and stop characters, both L fields equal, the second 68h, L + 6
; characters, and the check sum (the sum of C, A, CI and the data, without carries). A
; character may follow the last one by at most GAP_BITS bits more than none ("no pauses
; within a telegram"; GAP_BITS 11 by default tolerates a master's small gaps); a fault ends
; the telegram unanswered, and the slave then waits for 11 bits of Mark.
; Addresses (5.3): the slave's primary address (ADDR, 0 at manufacture), 254 (test: answered
; with the slave's own address) and 255 (broadcast: acted on, never answered). 253 (network
; layer) and secondary addressing are not done.
; Functions (Table 1, 5.4): SND_NKE (40h) -> E5h; SND_UD (53h/73h, a long or control frame)
; -> E5h, and its CI and data go to the host; REQ_UD2 (5Bh/7Bh) -> RSP_UD (08h, a long
; frame: CI and data from the host, or a CI 72h header of zeros until the host gives one).
; Other C fields are not answered. The reply starts REPLY_BITS bit times after the end of the
; telegram's last stop bit (5.4: between 11 bit times and 330 bit times + 50 ms; 22 by
; default). FCB/FCV are ignored, as a slave with a single RSP_UD telegram may (5.5.1); the
; access number and the rest of the application data are the host's.
; Limits: a received telegram of at most RX_MAX (64) characters (L <= 58); a response of at
; most RSP_MAX (120) bytes of CI and data.
;
; Host (mailbox, context CTX):
;   in:   [31:24] = 1: set the primary address to [7:0] (0 to 250; others ignored)
;         [31:24] = 2: the RSP_UD's CI and data follow, n = [7:0] bytes (1 to RSP_MAX), as
;                      n words, one byte each in [7:0], CI first (n > RSP_MAX: read and
;                      dropped). Sent between telegrams: while it reads them, the slave does
;                      not listen to the bus.
;   out:  after each telegram acted on: [31:24] kind (1 SND_NKE, 2 REQ_UD2 answered, 3
;         SND_UD), [23:16] the A field, [15:8] the C field, [7:0] n; for SND_UD then n words,
;         one byte each: the CI field and the data. The host should keep its mailbox drained.

.default CTX, 0
.default RX_PIN, 0
.default TX_PIN, 1
.default CLK_KHZ, 25000
.default BAUD, 2400
.default ADDR, 0
.default REPLY_BITS, 22
.default GAP_BITS, 11
.equ RXM, 1 << RX_PIN
.equ TXM, 1 << TX_PIN
.equ B, (CLK_KHZ * 1000 + BAUD // 2) // BAUD     ; ticks a bit
.equ HB, B // 2
.assert 300 <= BAUD <= 38400, 300 to 38400 baud
.assert B >= 400, a character's checks between its stop bit sample and the next start bit
.assert ADDR <= 250, primary addresses 0 to 250
.assert 11 <= REPLY_BITS <= 330, the reply window opens 11 bit times after the telegram
.assert GAP_BITS <= 22, a gap within a telegram
.equ REPLY_T, REPLY_BITS * B
.equ GAP_T, GAP_BITS * B
.equ IDLE_T, 11 * B
.equ POLL_T, 4 * B
.equ LEAD, 24                      ; a sent character's start after RDTIME

; tape: one byte a word
.equ RXB, 0x00                     ; the received telegram
.equ RX_MAX, 64
.equ DEND, 0xF0                    ; the response's CI and data end here; CS, 16h after
.equ TEND, DEND + 2                ; a reply is sent from TEND - n to TEND
.equ RSP_MAX, 120
.equ ADDRW, 0xF8                   ; the primary address
.equ RSPNW, 0xF9                   ; the response's CI and data: n
.equ NEEDW, 0xFA                   ; characters expected in this telegram
.equ CW, 0xFB                      ; the C field
.equ AW, 0xFC                      ; the A field
.equ NOTEW, 0xFD                   ; the word for the host
.equ NDW, 0xFE                     ; SND_UD bytes for the host
.assert DEND - RSP_MAX - 6 >= RXB + RX_MAX, the response and the receive buffer apart

.entry CTX, main
.span CTX                          ; 308 words: context 0 also takes context 2's page
.own TX_PIN, CTX
.tape ADDRW, ADDR
.tape RSPNW, 13
.tape DEND - 13, 0x72              ; CI 72h: the variable data structure, a header of zeros

main:
    ADDT  40
    EMIT  TXM, TXM, TXM        ; Mark
idle:
    RDTIME r2
    SETT  r2
    ADDT  POLL_T
    WAITP RXM, 0               ; a start bit
    JZ    t_new
    RXREADY
    JZ    idle
    POP   r0
    JMP   host

; ------------------------------------------------------------ receive a telegram
t_new:
    MOVI  r3, 0                ; characters so far
    MOVI  r2, RX_MAX
    STT   r2, r3, NEEDW
rx_char:                       ; a start bit's falling edge has just been seen
    RDTIME r1
    SETT  r1
    ADDT  HB
    SAMPLE RX_PIN, LSB         ; the start bit's middle
    LDLC  10
rc:
    ADDT  B
    SAMPLE RX_PIN, LSB         ; data 0 to 7, parity, stop
    DJNZ  rc
    GETI  r0                   ; [21] start, [29:22] data, [30] parity, [31] stop
    MOV   r2, r0
    SHR   r2, 21
    TSTI  r2, 1
    JNZ   bad                  ; start bit not Space
    MOV   r2, r0
    SHR   r2, 31
    CMPI  r2, 1
    JNZ   bad                  ; stop bit not Mark
    MOV   r2, r0
    SHL   r2, 1
    SHR   r2, 23               ; data and parity
    MOV   r1, r2
    SHR   r1, 8
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 4
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 2
    XOR   r2, r1
    MOV   r1, r2
    SHR   r1, 1
    XOR   r2, r1
    TSTI  r2, 1
    JNZ   bad                  ; parity not even
    SHL   r0, 2
    SHR   r0, 24               ; the character
    STT   r0, r3, RXB
    ADDI  r3, 1
    CMPI  r3, 1
    JNZ   rx_2
    CMPI  r0, 0x10             ; the first character: the format
    JNZ   rx_68
    MOVI  r2, 5
    MOVI  r1, 0
    STT   r2, r1, NEEDW
    JMP   rx_next
rx_68:
    CMPI  r0, 0x68
    JNZ   bad                  ; (E5h, or not a start character: nothing for a slave)
    JMP   rx_next
rx_2:
    CMPI  r3, 2
    JNZ   rx_chk
    MOVI  r1, 0
    LDT   r2, r1, RXB
    CMPI  r2, 0x68
    JNZ   rx_chk
    CMPI  r0, 3                ; a long frame's L: 3 at least, L + 6 characters in all
    JC    bad
    MOV   r2, r0
    ADDI  r2, 6
    CMPI  r2, RX_MAX + 1
    JNC   bad
    STT   r2, r1, NEEDW
rx_chk:
    MOVI  r1, 0
    LDT   r2, r1, NEEDW
    CMP   r3, r2
    JZ    done
rx_next:                       ; tmr: this character's stop bit sample
    ADDT  B - HB + GAP_T
    WAITP RXM, 0               ; the next start bit
    JZ    rx_char
    JMP   bad

; ------------------------------------------------------------ check it (tmr is kept)
done:
    MOVI  r3, 0
    LDT   r0, r3, RXB
    CMPI  r0, 0x10
    JNZ   d_long
    LDT   r0, r3, RXB + 1      ; short frame: C, A, CS = C + A, 16h
    LDT   r1, r3, RXB + 2
    STT   r0, r3, CW
    STT   r1, r3, AW
    ADD   r0, r1
    SHL   r0, 24
    SHR   r0, 24
    LDT   r1, r3, RXB + 3
    CMP   r0, r1
    JNZ   bad
    LDT   r1, r3, RXB + 4
    CMPI  r1, 0x16
    JNZ   bad
    MOVI  r2, 0                ; a short frame
    JMP   dispatch
d_long:
    LDT   r0, r3, RXB + 1      ; L, L, 68h
    LDT   r1, r3, RXB + 2
    CMP   r0, r1
    JNZ   bad
    LDT   r1, r3, RXB + 3
    CMPI  r1, 0x68
    JNZ   bad
    MOV   r2, r0
    ADDI  r2, 4                ; the check sum's index
    MOVI  r1, 0
    MOVI  r3, 4
d_sum:
    LDT   r0, r3, RXB
    ADD   r1, r0
    ADDI  r3, 1
    CMP   r3, r2
    JNZ   d_sum
    SHL   r1, 24
    SHR   r1, 24
    LDT   r0, r3, RXB
    CMP   r0, r1
    JNZ   bad
    LDT   r0, r3, RXB + 1
    CMPI  r0, 0x16
    JNZ   bad
    MOVI  r3, 0
    LDT   r0, r3, RXB + 4
    STT   r0, r3, CW
    LDT   r0, r3, RXB + 5
    STT   r0, r3, AW
    MOVI  r2, 1                ; a long frame
dispatch:                      ; r2: 0 short, 1 long
    MOVI  r3, 0
    STT   r3, r3, NDW
    LDT   r1, r3, AW
    CMPI  r1, 0xFF
    JZ    d_ok
    CMPI  r1, 0xFE
    JZ    d_ok
    LDT   r0, r3, ADDRW
    CMP   r1, r0
    JNZ   idle                 ; for another slave
d_ok:
    LDT   r0, r3, CW
    CMPI  r2, 0
    JNZ   d_lf
    CMPI  r0, 0x40
    JZ    nke
    MOVI  r2, 0xDF
    AND   r0, r2               ; FCB off
    CMPI  r0, 0x5B
    JZ    req2
    JMP   idle
d_lf:
    MOVI  r2, 0xDF
    AND   r0, r2
    CMPI  r0, 0x53
    JZ    snd_ud
    JMP   idle

; ------------------------------------------------------------ answer
nke:                           ; SND_NKE: E5h
    MOVI  r0, 1
    JMP   ack
snd_ud:                        ; SND_UD: E5h; the CI and data for the host
    MOVI  r3, 0
    LDT   r0, r3, RXB + 1
    MOVI  r1, 2
    SUB   r0, r1               ; L - 2: CI and data
    STT   r0, r3, NDW
    MOVI  r0, 3
ack:                           ; r0: the kind. NOTEW = kind << 24 | A << 16 | C << 8 | NDW
    MOVI  r3, 0
    SHL   r0, 8
    LDT   r1, r3, AW
    OR    r0, r1
    SHL   r0, 8
    LDT   r1, r3, CW
    OR    r0, r1
    SHL   r0, 8
    LDT   r1, r3, NDW
    OR    r0, r1
    STT   r0, r3, NOTEW
    LDT   r1, r3, AW
    CMPI  r1, 0xFF
    JZ    after                ; broadcast: no answer
    MOVI  r0, 0xE5
    STT   r0, r3, TEND - 1
    MOVI  r1, 1
    MOVI  r3, 0
    SUB   r3, r1               ; one character
    ADDT  B - HB + REPLY_T
    JMP   send
req2:                          ; REQ_UD2: RSP_UD
    MOVI  r3, 0
    LDT   r1, r3, AW
    MOVI  r0, 2 << 8           ; NOTEW: 2 << 24 | A << 16 | C << 8 | 0
    OR    r0, r1
    SHL   r0, 8
    LDT   r1, r3, CW
    OR    r0, r1
    SHL   r0, 8
    STT   r0, r3, NOTEW
    LDT   r1, r3, AW
    CMPI  r1, 0xFF
    JZ    idle                 ; broadcast: no answer, nothing done
    LDT   r2, r3, RSPNW        ; n: CI and data
    LDT   r1, r3, ADDRW
    ADDI  r1, 8                ; the check sum: C (08h) + A + CI + data
    SUB   r3, r2
rs_sum:
    LDT   r0, r3, DEND
    ADD   r1, r0
    ADDI  r3, 1
    JNZ   rs_sum
    SHL   r1, 24
    SHR   r1, 24
    STT   r1, r3, DEND         ; CS
    MOVI  r1, 0x16
    STT   r1, r3, DEND + 1
    SUB   r3, r2               ; the header before the data: 68h L L 68h 08h A
    MOVI  r0, 0x68
    STT   r0, r3, DEND - 6
    STT   r0, r3, DEND - 3
    MOV   r0, r2
    ADDI  r0, 2                ; L: C, A, CI and data
    STT   r0, r3, DEND - 5
    STT   r0, r3, DEND - 4
    MOVI  r0, 0x08
    STT   r0, r3, DEND - 2
    MOVI  r1, 0
    LDT   r0, r1, ADDRW
    STT   r0, r3, DEND - 1
    MOVI  r0, 8
    SUB   r3, r0               ; n + 8 characters
    ADDT  B - HB + REPLY_T
send:                          ; r3 = -count, the characters at TEND - count .. TEND - 1;
    LDT   r0, r3, TEND         ; tmr: the first start bit
    MOV   r1, r0
    MOV   r2, r1
    SHR   r2, 4
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 2
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 1
    XOR   r1, r2
    SHL   r1, 31
    SHR   r1, 22               ; [9]: even parity
    SHL   r0, 1                ; [0] start (Space), [8:1] data
    OR    r0, r1
    MOVI  r1, 0x400            ; [10] stop (Mark)
    OR    r0, r1
    LDSH  r0                   ; (built while the last character goes out)
    LDLC  11
    WAITT                      ; the last character's stop bit has lasted one bit
    RDTIME r1                  ; this one starts LEAD ticks later: each character's
    ADDI  r1, LEAD             ; events timed from now, which escert can bound (it
    SETT  r1                   ; cannot through an uncounted run of characters)
s_bit:
    EMITX TXM, LSB, TXM
    ADDT  B
    DJNZ  s_bit
    ADDI  r3, 1
    JNZ   send                 ; the next character: no pause but that
after:                         ; the host's word, and SND_UD's bytes
    WAITT                      ; (the reply's last stop bit is over)
    MOVI  r3, 0
    LDT   r0, r3, NOTEW
    PUSH  r0
    LDT   r2, r3, NDW
    CMPI  r2, 0
    JZ    idle
    ADDI  r2, 6
    MOVI  r3, 6
a_data:
    LDT   r0, r3, RXB
    PUSH  r0
    ADDI  r3, 1
    CMP   r3, r2
    JNZ   a_data
    JMP   idle

; ------------------------------------------------------------ faults, host
bad:                           ; drop the telegram; wait for 11 bits of Mark
    RDTIME r2
    SETT  r2
    ADDT  IDLE_T
    WAITP RXM, 0
    JNZ   idle                 ; Mark all along
    RDTIME r2
    SETT  r2
    ADDT  IDLE_T
    WAITP RXM, RXM             ; back to Mark (or the window again)
    JMP   bad

host:                          ; r0: the host's word
    MOV   r1, r0
    SHR   r1, 24
    SHL   r0, 24
    SHR   r0, 24
    CMPI  r1, 1
    JZ    h_addr
    CMPI  r1, 2
    JNZ   idle
    MOVI  r3, 0
    SUB   r3, r0               ; -n
    CMPI  r0, RSP_MAX + 1
    JNC   h_drop
    CMPI  r0, 0
    JZ    idle
    MOVI  r1, 0
    STT   r0, r1, RSPNW
h_byte:
    POP   r1
    SHL   r1, 24
    SHR   r1, 24
    STT   r1, r3, DEND
    ADDI  r3, 1
    JNZ   h_byte
    JMP   idle
h_drop:
    POP   r1
    ADDI  r3, 1
    JNZ   h_drop
    JMP   idle
h_addr:
    CMPI  r0, 251
    JNC   idle
    MOVI  r3, 0
    STT   r0, r3, ADDRW
    JMP   idle
