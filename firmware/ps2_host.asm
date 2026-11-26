; PS/2 host: the chip is the system side of a PS/2 keyboard or mouse port. The device makes
; the clock both ways; the host reads the device's bytes, sends it commands, and holds the
; clock low to stop it (inhibit) while it cannot take another byte. Held-out round, written
; 2026-11-24 on v3.1 (cb47436), from IBM "Personal System/2 Hardware Interface Technical
; Reference: Common Interfaces" (84F9735, Oct 1990), Keyboard/Auxiliary Device Controller,
; "Auxiliary Device and System Timings" (Figures 7 to 9), and IBM "Keyboards (101- and
; 102-Key)" (Oct 1990), "Data Stream", "Data Output", "Data Input".
;
;   pin CLK_PIN (0)    open drain: CLK, made by the device; the host pulls it low to inhibit
;   pin DATA_PIN (1)   open drain: DATA
; Context CTX (0). Clock CLK_KHZ, 4 to 50 MHz (25 MHz by default).
;
; A byte is 11 bits: start (0), 8 data bits LSB first, odd parity, stop (1) (Figure 7).
; - Device to host (Figure 8): the device puts each bit on DATA T1 = 5 to 25 us before CLK
;   falls and holds it T2 >= 5 us after CLK rises; CLK low and high T3, T4 = 30 to 50 us. The
;   host reads DATA as soon as it sees CLK low. After the 11th clock the host may inhibit
;   within T5 = 50 us so the device does not start another byte: this program does that only
;   while its transmit mailbox is full, and holds CLK low until the host has taken a word.
; - Host to device (Figure 9, Data Input): CLK held low RTS_US (default 100 us; "more than
;   60 us"), DATA pulled low (the start bit), CLK released (request to send). The device
;   clocks: each time it pulls CLK low the host puts the next bit on DATA (8 data bits,
;   parity, then the stop bit: DATA released); the device samples while CLK is high (T9 = 5
;   to 25 us after it rises; data stable within 1 us of the rising edge). On the 11th clock
;   the device pulls DATA low (the line-control bit): the host reads it, then waits for both
;   lines high. The keyboard looks for a request at least every 10 ms: the first clock may
;   take up to FIRST_MS (15 ms).
; Every edge the device makes is waited for with a deadline of EDGE_US (100 us, twice T3/T4's
; maximum). A byte from the device that stops part way is ended by inhibiting for RTS_US
; (line contention: the device keeps its byte to send again) and reported.
; Not done: the commands themselves (reset, resend, set LEDs, typematic, scan code sets,
; mouse modes) are the host's: it sends the bytes and reads the replies; after a command it
; should wait for the device's answer (within 20 ms) before the next. No resend on its own.
;
; Host (mailbox, context CTX):
;   in:   a byte to send to the device, [7:0]
;   out:  a byte from the device: [7:0] the data, [8] parity error, [9] start bit not 0,
;         [10] stop bit not 1, [11] CLK stopped part way (the data is not valid)
;         the result of a send: [31] 1, [0] no line-control bit (DATA not low at the 11th
;         clock), [1] the device did not start clocking within FIRST_MS, [2] it stopped part
;         way; 0x80000000 is a byte sent and acknowledged

.default CTX, 0
.default CLK_PIN, 0
.default DATA_PIN, 1
.default CLK_KHZ, 25000
.default RTS_US, 100
.default FIRST_MS, 15
.default EDGE_US, 100
.equ CLKM, 1 << CLK_PIN
.equ DATM, 1 << DATA_PIN
.equ US, CLK_KHZ // 1000
.assert 4000 <= CLK_KHZ <= 50000, 4 to 50 MHz
.assert 60 < RTS_US <= 1000, the request to send holds CLK low more than 60 us
.assert 50 < EDGE_US <= 1000, the device's clock phases are at most 50 us
.equ EDGE, EDGE_US * US
.equ LEAD, 24                      ; an output's deadline after RDTIME: past the instructions
.equ SETTLE, 2 * US                ; after the program releases CLK: it reads high again

.entry CTX, main
.own CLK_PIN, CTX
.own DATA_PIN, CTX
.od CLK_PIN
.od DATA_PIN

main:
    ADDT  40
    EMIT  CLKM | DATM, CLKM | DATM, CLKM | DATM    ; both released
    ADDT  SETTLE
    WAITT
idle:
    RDTIME r2
    ADDI  r2, EDGE
    SETT  r2
    WAITP CLKM, 0              ; the device's first clock: its start bit is on DATA
    JZ    rx
    RXREADY
    JZ    idle
    POP   r0
    JMP   tx

; ------------------------------------------------------------ device to host
rx:
    MOVI  r0, 0                ; the 11 bits arrive at [10], shifted down: start ends at [0]
    LDLC  11
r_bit:                         ; CLK is low: DATA holds the bit
    GETP  r3
    SHL   r3, 31 - DATA_PIN
    SHR   r3, 21
    SHR   r0, 1
    OR    r0, r3
    RDTIME r2
    ADDI  r2, EDGE
    SETT  r2
    WAITP CLKM, CLKM           ; CLK rises
    JNZ   r_stuck
    DJNZ  r_fall
    JMP   r_done
r_fall:
    RDTIME r2
    ADDI  r2, EDGE
    SETT  r2
    WAITP CLKM, 0              ; the next bit
    JZ    r_bit
r_stuck:                       ; CLK stopped part way: inhibit, so the device gives up the
    RDTIME r2                  ; byte (line contention) and starts again later
    ADDI  r2, LEAD
    SETT  r2
    EMIT  CLKM, 0, CLKM
    ADDT  RTS_US * US
    EMIT  CLKM, CLKM, CLKM
    ADDT  SETTLE
    WAITT
    MOVI  r1, 0x800
    JMP   deliver
r_done:                        ; r0[0] start, [8:1] data, [9] parity, [10] stop
    MOV   r1, r0
    SHR   r1, 1
    SHL   r1, 23
    SHR   r1, 23               ; the data and the parity bit
    MOV   r2, r1
    SHR   r2, 8
    XOR   r2, r1
    MOV   r3, r2
    SHR   r3, 4
    XOR   r2, r3
    MOV   r3, r2
    SHR   r3, 2
    XOR   r2, r3
    MOV   r3, r2
    SHR   r3, 1
    XOR   r2, r3               ; r2[0]: the nine bits' XOR, 1 when the parity is right
    SHL   r2, 31
    SHR   r2, 23
    MOVI  r3, 0x100
    XOR   r2, r3               ; [8]: parity error
    SHL   r1, 24
    SHR   r1, 24               ; the data
    OR    r1, r2
    MOV   r2, r0
    SHL   r2, 31
    SHR   r2, 22               ; [9]: the start bit as read
    OR    r1, r2
    MOV   r2, r0
    SHR   r2, 10
    SHL   r2, 31
    SHR   r2, 21
    MOVI  r3, 0x400
    XOR   r2, r3               ; [10]: the stop bit missing
    OR    r1, r2
    ; (fall through)

; ------------------------------------------------------------ to the host
deliver:                       ; r1: the word. With room in the mailbox, at once; without,
    TXREADY                    ; CLK held low (inhibit) until the host takes a word, so the
    JZ    d_full               ; device keeps its next byte
    PUSH  r1
    JMP   idle
d_full:
    RDTIME r2
    ADDI  r2, LEAD
    SETT  r2
    EMIT  CLKM, 0, CLKM        ; within T5 of the 11th clock
    PUSH  r1
    RDTIME r2
    ADDI  r2, LEAD
    SETT  r2
    EMIT  CLKM, CLKM, CLKM
    ADDT  SETTLE
    WAITT
    JMP   idle

; ------------------------------------------------------------ host to device
tx:                            ; r0[7:0]: the byte. sh = data, parity, stop (1), LSB first
    MOVI  r1, 0xFF
    AND   r0, r1
    MOV   r1, r0
    MOV   r2, r1
    SHR   r2, 4
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 2
    XOR   r1, r2
    MOV   r2, r1
    SHR   r2, 1
    XOR   r1, r2               ; r1[0]: the data's XOR
    SHL   r1, 31
    SHR   r1, 23
    MOVI  r2, 0x100
    XOR   r1, r2               ; [8]: odd parity
    OR    r0, r1
    MOVI  r2, 0x200
    OR    r0, r2               ; [9]: stop
    LDSH  r0
    RDTIME r2
    ADDI  r2, LEAD
    SETT  r2
    EMIT  CLKM, 0, CLKM        ; CLK low: the device stops or does not start
    ADDT  RTS_US * US
    EMIT  DATM, 0, DATM        ; the start bit
    ADDT  5 * US
    EMIT  CLKM, CLKM, CLKM     ; CLK released: request to send
    ADDT  SETTLE
    WAITT
    ADDT  FIRST_MS * CLK_KHZ - SETTLE
    WAITP CLKM, 0              ; the first clock
    JNZ   t_late
    LDLC  10
t_bit:                         ; CLK is low: the next bit on DATA
    RDTIME r2
    ADDI  r2, LEAD
    SETT  r2
    EMITX DATM, LSB, DATM
    ADDT  EDGE
    WAITP CLKM, CLKM           ; CLK rises: the device samples
    JNZ   t_stuck
    RDTIME r2
    ADDI  r2, EDGE
    SETT  r2
    WAITP CLKM, 0
    JNZ   t_stuck
    DJNZ  t_bit
    GETP  r3                   ; the 11th clock: the line-control bit (DATA low)
    MOVI  r1, 0
    TSTI  r3, DATM
    JZ    t_ack
    MOVI  r1, 1
t_ack:
    RDTIME r2
    ADDI  r2, EDGE
    SETT  r2
    WAITP CLKM | DATM, CLKM | DATM   ; the device releases both
    JZ    t_end
    MOVI  r2, 4
    OR    r1, r2
t_end:
    MOVHI r1, 0x8000
    JMP   deliver
t_late:
    MOVI  r1, 2
    JMP   t_out
t_stuck:
    MOVI  r1, 4
t_out:                         ; give up: DATA released; CLK low RTS_US (the device drops
    RDTIME r2                  ; the request), then released
    ADDI  r2, LEAD
    SETT  r2
    EMIT  CLKM | DATM, DATM, CLKM | DATM
    ADDT  RTS_US * US
    EMIT  CLKM, CLKM, CLKM
    ADDT  SETTLE
    WAITT
    MOVHI r1, 0x8000
    JMP   deliver
