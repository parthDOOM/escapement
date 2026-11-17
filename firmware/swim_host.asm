; STM8 SWIM host: the chip debugs an STM8 over its single wire interface module, as an
; ST-LINK does: the entry sequence, the target's synchronization frame, SWIM_CSR = 0xA0 to
; open the whole memory to the debugger, then reads of the target's memory on request.
; SWIM on pin 0, open drain (the target pulls it up; value 1 = release).
;
; (ST UM0470, "STM8 SWIM communication protocol and debug module", rev. 4.) Entry: the pin
; low for 16 us, then four pulses at 1 kHz and four at 2 kHz, ending high; the target
; answers with 128 SWIM clocks low. Low speed bit format, SWIM clock 8 MHz (HSI / 2): a
; bit is 22 clocks, a 1 is 2 clocks low and a 0 is 20; the receiver counts the low
; samples (8 or fewer: 1). Every packet is a header bit (0 from the host, 1 from the
; target), the bits MSB first, an XOR parity bit, and the receiver's acknowledge (1 = ACK).
; Commands: SRST 000, ROTF 001, WOTF 010; ROTF and WOTF take N and a 24-bit address.
;
; Host: one word per read, [23:0] the target address, [26:24] N - 1 (1 to 8 bytes). The
; first read also activates the SWIM. The chip PUSHes N, then the N bytes; or 0xFFFFFFFF
; if the target did not answer (and activates again on the next read).
;
; Bits the chip sends are DRIVE events at deadlines from an RDTIME taken just before
; them; bits the target sends are taken 1.4 us after their falling edge (between the
; 1 us of a 1 and the 2.5 us of a 0). The read's bytes are kept on the tape until the
; read is over, so the host link never holds up the wire. A 0 from the target leaves the
; line high for 250 ns before its next bit falls, and a 1 is low for 250 ns: the receive
; loop's two waits have only a DJNZ between them, and the program needs 33 MHz or more
; (50 MHz by default) so that a 1 after a 0 is always seen.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 50000
.assert CLK_KHZ >= 33000, receiving needs 33 MHz: after a 0 the next bit falls 250 ns later and a 1 is low for 250 ns
.equ SW, 1 << PIN
.equ BITT, CLK_KHZ * 2750 // 1000000              ; 22 SWIM clocks
.equ SHORT, (CLK_KHZ * 250 + 999999) // 1000000   ; 2 clocks low: a 1
.equ LONG, CLK_KHZ * 2500 // 1000000              ; 20 clocks low: a 0
.equ MID, CLK_KHZ * 1400 // 1000000               ; a target bit is taken here
.equ LOW16, CLK_KHZ * 16 // 1000
.equ P1, CLK_KHZ // 2                             ; half of 1 kHz
.equ P2, CLK_KHZ // 4                             ; half of 2 kHz
.equ TO_SYNC, CLK_KHZ * 2                         ; 2 ms for the synchronization frame
.equ TO_BIT, CLK_KHZ * 40 // 1000                 ; 40 us for a target bit to begin
.equ LEAD, 32
.assert SHORT * 1000000 // CLK_KHZ <= 1000, a 1 must be low for at most 8 SWIM clocks
.default REQ, 0                                   ; tape: the request
.default ACTIVE, 1                                ; 1 once the SWIM is active
.default BUF, 2                                   ; the bytes read, up to 8

.entry CTX, start
.own   PIN, CTX
.od    PIN

start:
    ADDT  16
    EMIT  SW, SW, SW                    ; released
req:
    POP   r0
    MOVI  r3, 0
    STT   r0, r3, REQ
    LDT   r1, r3, ACTIVE
    TSTI  r1, 1
    JNZ   read
    ; ---- entry sequence
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1
    EMIT  SW, 0, SW
    ADDT  LOW16
    EMIT  SW, SW, SW
    ADDT  P1
    LDLC  4
e1:
    EMIT  SW, 0, SW
    ADDT  P1
    EMIT  SW, SW, SW
    ADDT  P1
    DJNZ  e1
    LDLC  3
e2:
    EMIT  SW, 0, SW
    ADDT  P2
    EMIT  SW, SW, SW
    ADDT  P2
    DJNZ  e2
    EMIT  SW, 0, SW                     ; the eighth pulse
    ADDT  P2
    EMIT  SW, SW, SW
    WAITT                               ; its rising edge: the sequence is on the wire
    ; ---- the synchronization frame: a low, then high again, within 2 ms
    RDTIME r1
    SETT  r1
    ADDT  TO_SYNC
    WAITP SW, 0                         ; (Z = 0: timed out)
    JNZ   fail
    WAITP SW, SW
    JNZ   fail
    ; ---- WOTF 1 byte to SWIM_CSR (0x007F80): 0xA0, debug mode and the whole memory
    MOVI  r0, 0                         ; WOTF 010, parity 1: 0 010 1 << 27
    MOVHI r0, 0x2800
    CALL  tx5
    JZ    fail
    MOVI  r0, 1
    CALL  txb
    JZ    fail
    MOVI  r0, 0x00
    CALL  txb
    JZ    fail
    MOVI  r0, 0x7F
    CALL  txb
    JZ    fail
    MOVI  r0, 0x80
    CALL  txb
    JZ    fail
    MOVI  r0, 0xA0
    CALL  txb
    JZ    fail
    MOVI  r3, 0
    MOVI  r1, 1
    STT   r1, r3, ACTIVE
read:
    ; ---- ROTF N, @E, @H, @L
    MOVI  r0, 0
    MOVHI r0, 0x1800                    ; ROTF 001, parity 1: 0 001 1 << 27
    CALL  tx5
    JZ    fail
    MOVI  r3, 0
    LDT   r0, r3, REQ
    SHR   r0, 24
    MOVI  r1, 7
    AND   r0, r1
    ADDI  r0, 1                         ; N
    CALL  txb
    JZ    fail
    MOVI  r3, 0
    LDT   r0, r3, REQ
    SHR   r0, 16
    MOVI  r1, 0xFF
    AND   r0, r1
    CALL  txb                           ; @E
    JZ    fail
    MOVI  r3, 0
    LDT   r0, r3, REQ
    SHR   r0, 8
    MOVI  r1, 0xFF
    AND   r0, r1
    CALL  txb                           ; @H
    JZ    fail
    MOVI  r3, 0
    LDT   r0, r3, REQ
    MOVI  r1, 0xFF
    AND   r0, r1
    CALL  txb                           ; @L
    JZ    fail
    ; ---- N data packets from the target, each ACKed
    MOVI  r2, BUF
rd_byte:                                ; (tmr: the timeout for the header, set by whoever
    LDLC  10                            ; came here, so the target's first bit is not missed)
    MOVI  r0, 0
rd_bit:                                 ; (a 0 leaves the line high for only 250 ns before the
    WAITP SW, 0                         ; next bit falls, and a 1 is low for 250 ns: nothing but
    JNZ   fail                          ; DJNZ runs between the two WAITPs)
    RDTIME r1
    ADDI  r1, MID
    SETT  r1
    WAITT
    GETP  r1
    ADDT  TO_BIT                        ; the next bit's timeout
    SHL   r0, 1
    TSTI  r1, SW
    JZ    rd_zero
    ADDI  r0, 1
rd_zero:
    WAITP SW, SW                        ; this bit's low is over
    DJNZ  rd_bit
    ; r0 = header << 9 | byte << 1 | parity: the header must be 1 and the byte and parity
    ; even, so the ten bits' XOR is 1
    MOV   r3, r0
    SHR   r3, 8
    XOR   r3, r0                        ; bits 9 and 8 folded onto 1 and 0
    MOV   r1, r3
    SHR   r1, 4
    XOR   r3, r1
    MOV   r1, r3
    SHR   r1, 2
    XOR   r3, r1
    MOV   r1, r3
    SHR   r1, 1
    XOR   r3, r1                        ; bit 0: the XOR of all ten
    TSTI  r3, 1
    JZ    nack
    TSTI  r0, 0x200
    JZ    nack
    SHR   r0, 1
    MOVI  r1, 0xFF
    AND   r0, r1
    STT   r0, r2, 0
    ADDI  r2, 1
    RDTIME r1                           ; ACK: a 1
    ADDI  r1, LEAD
    SETT  r1
    EMIT  SW, 0, SW
    ADDT  SHORT
    EMIT  SW, SW, SW
    WAITT                               ; our own pulse is past: listen from here
    ADDT  TO_BIT
    MOVI  r3, 0
    LDT   r0, r3, REQ
    SHR   r0, 24
    MOVI  r1, 7
    AND   r0, r1
    ADDI  r0, BUF + 1
    CMP   r2, r0
    JNZ   rd_byte
    ; ---- report: N, then the bytes
    MOV   r1, r2
    MOVI  r0, BUF
    SUB   r1, r0                        ; N
    PUSH  r1
    MOVI  r3, BUF
push:
    LDT   r0, r3, 0
    PUSH  r0
    ADDI  r3, 1
    CMP   r3, r2
    JNZ   push
    JMP   req
nack:                                   ; NACK: a 0; the target sends the byte again
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1
    EMIT  SW, 0, SW
    ADDT  LONG
    EMIT  SW, SW, SW
    WAITT
    ADDT  TO_BIT
    JMP   rd_byte
fail:
    MOVI  r3, 0
    STT   r3, r3, ACTIVE
    MOVI  r0, 0xFFFF
    MOVHI r0, 0xFFFF
    PUSH  r0
    JMP   req

; ---- tx5 / txb: send a command (r0 = its 5 bits at 31..27) or a byte (r0[7:0]: header,
; parity added), then take the target's acknowledge: Z = 0 for an ACK, Z = 1 for a NACK or
; no answer.
txb:
    MOV   r1, r0                        ; the byte's parity
    SHR   r1, 4
    XOR   r1, r0
    MOV   r3, r1
    SHR   r3, 2
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 1
    XOR   r1, r3
    MOVI  r3, 1
    AND   r1, r3
    SHL   r0, 1
    OR    r0, r1                        ; byte << 1 | parity; the header 0 above it
    SHL   r0, 22                        ; 10 bits at 31..22
    LDLC  10
    JMP   tx_go
tx5:
    LDLC  5
tx_go:
    RDTIME r1
    ADDI  r1, LEAD
    SETT  r1
tx_bit:
    SHL   r0, 1
    EMIT  SW, 0, SW
    JC    tx_one
    ADDT  LONG
    EMIT  SW, SW, SW
    ADDT  BITT - LONG
    JMP   tx_next
tx_one:
    ADDT  SHORT
    EMIT  SW, SW, SW
    ADDT  BITT - SHORT
tx_next:
    DJNZ  tx_bit
    WAITT                               ; the last bit's slot is over: listen
    ADDT  TO_BIT                        ; for the acknowledge
    WAITP SW, 0
    JNZ   tx_none
    RDTIME r1
    ADDI  r1, MID
    SETT  r1
    WAITT
    GETP  r1
    ADDT  TO_BIT                        ; (the timeout for whatever the caller waits for next)
    WAITP SW, SW
    TSTI  r1, SW                        ; high at MID: a 1, ACK (Z = 0)
    RET
tx_none:
    MOVI  r1, 0                         ; Z = 1
    RET
