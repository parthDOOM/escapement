; USB low-speed HID keyboard, self-contained: enumeration, descriptors, SET_ADDRESS with
; the token CRC-5 computed on chip, and key reports from the host mailbox (v1.4 line unit).
;
; Board: 24 MHz clock (16 ticks per bit). D+ on pin 0, D- on pin 1, 27 ohm in series each,
; 1.5 kohm from D- to 3.3 V (a low-speed device). The line unit does NRZI, bit stuffing,
; the CRCs, SYNC and EOP; this firmware handles packets.
;
; Context 0 is the device. Every reply leaves exactly TURN ticks after the end of the
; host's packet (TURN + 3 at the pins: 4.69 bit times, inside USB's 2 to 6.5), and escert
; proves it on time on every path where the host keeps to USB timing; a reply that would
; be late is dropped (v1.4 SEND) and the host's retry is answered. Context 1
; moves key reports from its mailbox into the tape: the host pushes a report as two words
; (bytes 0-3, bytes 4-7, little-endian) and they go out on the next IN to endpoint 1.
;
; VID:PID 1209:0001 is the pid.codes test ID: it is not unique and must not be used
; outside test environments.

.default CTX, 0
.default FEED, 1
.default DP, 0
.default DM, 1
.default P, 16                    ; ticks per bit
.equ TURN, 4 * P + 8              ; reply time after the end of the host's packet, ticks
.equ ACKWAIT, 160 * P             ; our longest packet, the gap and the host's ACK
.equ DATAWAIT, 140 * P            ; the gap and an 8-byte DATA stage
.equ FAR, 0xFFFFFF

; ---- tape: buffers and state
.equ RXB, 0x00                    ; every received packet: PID, then its bytes (12 max)
.equ ZERO, 0x03                   ; GET_STATUS reply
.equ TOK, 0x04                    ; CRC-5 input: address, endpoint 0
.equ REPORT, 0x05                 ; the staged key report (8 bytes)
.equ ONE, 0x07                    ; GET_CONFIGURATION reply
.equ T_IN0, 0x08                  ; the token word of an IN to our endpoint 0
.equ INCMD, 0x09                  ; the reply to the next IN on endpoint 0
.equ LEFT, 0x0A                   ; bytes of the data stage from the current packet on
.equ PEND, 0x0B                   ; address << 1 | 1 while SET_ADDRESS waits for its status
.equ EP1CMD, 0x0C                 ; the reply to the next IN on endpoint 1
.equ T1, 0x0D                     ; endpoint 1's next report command (DATA0 or DATA1)
.equ DTAB, 0x10                   ; requests answered with data: key, reply
.equ DEV, 0x23
.equ CONF, 0x28
.equ REP, 0x31
.equ STR0, 0x41
.equ STR2, 0x42
.equ K, 0xE0                      ; constants
.equ CFG16, 0xF0                  ; line unit profile: packets, CRC-16
.equ CFG5, 0xF3                   ; the same with CRC-5, for the token computation

; ---- SEND commands: header (PID) << 16 | append CRC << 15 | bits << 8 | first word
.equ PID_DATA0, 0xC3
.equ PID_DATA1, 0x4B
.equ C_DATA1, (PID_DATA1 << 16) | (1 << 15)
.equ C_REP0, (PID_DATA0 << 16) | (1 << 15) | (64 << 8) | REPORT
.equ TOG, (PID_DATA0 ^ PID_DATA1) << 16

; ---- line unit configuration (docs/11-SEMANTICS.md §6.6)
.equ W0, DM | (DP << 3) | (1 << 9) | (1 << 10) | (1 << 11) | (6 << 12) | (2 << 16) | (1 << 19) | (1 << 20) | (0x80 << 24)
.equ W1, P | ((P // 2) << 20)
.equ W2B, (1 << 16) | (1 << 17) | (1 << 18)
.tape CFG16, W0, W1, 0x8005 | W2B
.tape CFG5, W0, W1, (0x05 << 11) | W2B

.tape K, (1 << 28) | (12 << 8) | RXB, (3 << 28) | CFG16, (3 << 28) | CFG5
.tape K + 3, (2 << 28) | (1 << 16) | (11 << 8) | TOK, 0x5A << 16, 0xD2 << 16, 0x1E << 16
.tape K + 7, C_DATA1 | (64 << 8), 0xB08000, TOG, 0x1058800D, C_REP0
.equ K_RECV, K
.equ K_CFG16, K + 1
.equ K_CFG5, K + 2
.equ K_CRCTOK, K + 3
.equ K_NAK, K + 4
.equ K_ACK, K + 5
.equ K_STALL, K + 6
.equ K_FULL1, K + 7                ; DATA1, CRC, a full 64-bit packet
.equ K_IN1, K + 8                 ; an IN to endpoint 1 differs from one to endpoint 0 by this
.equ K_TOG, K + 9
.equ K_SETUPOK, K + 10            ; status of a good 8-byte DATA stage: 88 bits, residual 0x800D
.equ K_REP0, K + 11

.tape ZERO, 0
.tape ONE, 1
.tape EP1CMD, 0x5A << 16, C_REP0   ; EP1CMD = NAK, T1 = DATA0 with the report

; ---- requests with a data stage: key = bmRequestType | bRequest << 8 | wValue << 16;
; reply = length << 24 | the SEND command of a full first packet (DATA1, CRC, 64 bits, word)
.equ FULL1, C_DATA1 | (64 << 8)
.tape DTAB, 0x01000680, (18 << 24) | FULL1 | DEV, 0x02000680, (34 << 24) | FULL1 | CONF
.tape DTAB + 4, 0x22000681, (63 << 24) | FULL1 | REP, 0x03000680, (4 << 24) | FULL1 | STR0
.tape DTAB + 8, 0x03020680, (22 << 24) | FULL1 | STR2, 0x00000080, (2 << 24) | FULL1 | ZERO
.tape DTAB + 12, 0x00000081, (2 << 24) | FULL1 | ZERO, 0x00000082, (2 << 24) | FULL1 | ZERO
.tape DTAB + 16, 0x00000880, (1 << 24) | FULL1 | ONE, 0

; device: USB 1.1, 8-byte endpoint 0, 1209:0001, version 1.04, product string 2
.tapeb DEV, 18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x01, 0x00, 0x04, 0x01, 0, 2, 0, 1
; configuration 1: one HID boot keyboard interface, endpoint 1 IN, 8 bytes every 10 ms
.tapeb CONF, 9, 2, 34, 0, 1, 1, 0, 0x80, 50, 9, 4, 0, 0, 1, 3, 1, 1, 0, 9, 0x21, 0x11, 0x01, 0, 1, 0x22, 63, 0, 7, 5, 0x81, 3, 8, 0, 10
; the boot keyboard report descriptor (HID 1.11, appendix B.1)
.tapeb REP, 0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01, 0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xC0
.tapeb STR0, 4, 3, 0x09, 0x04
.tapeb STR2, 22, 3, 0x45, 0, 0x73, 0, 0x63, 0, 0x61, 0, 0x70, 0, 0x65, 0, 0x6D, 0, 0x65, 0, 0x6E, 0, 0x74, 0

.entry CTX, busreset
.own DP, CTX
.own DM, CTX
.entry FEED, feed

; =========================================================================== device
; r3 = 0 from start: the base of every tape address.

busreset:
    MOVI  r1, 1                   ; address 0, as a pending address (below)
pend:
    SHR   r1, 1                   ; r1 = the device's address from now on
    STT   r1, r3, TOK
    LDT   r2, r3, K_CFG5
    LINE  r2                      ; the CRC-5 profile (the first CONFIG makes this context
    LDT   r2, r3, K_CRCTOK        ; the line unit's owner: D+ and D- are its own, released)
    LINE  r2                      ; the token CRC-5 by the line unit, in the order it is sent
    ADDT  FAR
    GETL  r2                      ; r2[4:0] = the CRC-5 field
    SHL   r2, 27
    SHR   r2, 8                   ; at token word bits 19..23
    SHL   r1, 8                   ; address at bits 8..14
    OR    r1, r2
    ADDI  r1, 0x69                ; PID IN
    STT   r1, r3, T_IN0           ; the token word of an IN to (address, endpoint 0)
    LDT   r2, r3, K_CFG16
    LINE  r2                      ; the packet profile
    STT   r3, r3, PEND
    LDT   r0, r3, K_NAK
    STT   r0, r3, INCMD           ; (endpoint 1 restarts at SET_CONFIGURATION, which a host
arm:                              ; must send before it polls it again: 9.1.1.5)
    LDT   r2, r3, K_RECV
    LINE  r2                      ; (after any packet we are sending)
next:
    ADDT  FAR
    GETL  r1                      ; a packet ended; tmr = its end tick
    JNZ   next                    ; (a timeout: keep waiting)
    LDT   r2, r3, RXB
fresh:                            ; r2 = the packet's first word, r1 = its status
    LDT   r0, r3, K_RECV
    LINE  r0                      ; armed at once for what follows
    LDT   r0, r3, T_IN0
    XOR   r0, r2                  ; 0: an IN to endpoint 0
    JZ    in0
    LDT   r2, r3, K_IN1
    CMP   r0, r2
    JZ    in1
    CMPI  r0, 0x2D ^ 0x69         ; SETUP to endpoint 0
    JZ    setup
    CMPI  r0, 0xE1 ^ 0x69         ; OUT to endpoint 0
    JZ    out0
    SHR   r1, 16                  ; a bus reset is an SE0 of 2.5 us or more (60 ticks)
    CMPI  r1, 0x1400 + 60         ; (RECV << 12 | SE0 << 9 | length)
    JC    next
    CMPI  r1, 0x1500
    JC    busreset
    JMP   next

in0:
    LDT   r0, r3, INCMD
    ADDT  TURN
    SEND  r0                      ; the reply, TURN after the token, to the tick
    CALL  acked
    JNZ   next
    LDT   r1, r3, PEND            ; acknowledged
    TSTI  r1, 1
    JNZ   pend                    ; the status stage of a SET_ADDRESS: the address applies
    LDT   r1, r3, LEFT            ; bytes from the packet just acknowledged on
    CMPI  r1, 9
    JC    nak                     ; it was the last: NAK until the next SETUP
    MOVI  r2, 8
    SUB   r1, r2
    LDT   r2, r3, K_TOG           ; the next packet: the other DATA PID, two words on
    XOR   r0, r2
    ADDI  r0, 2
    JMP   chunk

in1:
    LDT   r0, r3, EP1CMD
    ADDT  TURN
    SEND  r0
    CALL  acked
    JNZ   next
    LDT   r2, r3, K_TOG
    XOR   r0, r2
    STT   r0, r3, T1              ; the next report goes with the other DATA PID
    LDT   r2, r3, K_NAK
    STT   r2, r3, EP1CMD          ; consumed: the feeder may stage the next one
    JMP   next

; After a SEND of r0: arm for the host's handshake. Z = 1 if r0 carried data and the host
; acknowledged it. (A SEND that would have been late was dropped and the host repeats its
; token: the packet that comes instead of an ACK is answered like any other.)
acked:
    STT   r3, r3, RXB             ; (a packet that stores nothing leaves no token behind)
    LDT   r2, r3, K_RECV
    LINE  r2                      ; waits for the end of our packet, then armed
    TSTI  r0, 1 << 15
    JZ    not_acked               ; we sent a handshake ourselves
    ADDT  ACKWAIT
    GETL  r1
    JNZ   not_acked               ; no answer: the host will repeat the IN
    LDT   r2, r3, RXB
    CMPI  r2, 0xD2                ; ACK
    JNZ   fresh                   ; anything else is the host's next packet: answer it
    LDT   r2, r3, K_RECV
    LINE  r2                      ; armed at once for that next packet (Z stays 1)
    RET
not_acked:
    MOVI  r2, 1                   ; Z = 0
    RET

; ACK the packet that ended at tmr, TURN after it, and arm for the next one
ack:
    ADDT  TURN
    LDT   r2, r3, K_ACK
    SEND  r2
    LDT   r2, r3, K_RECV
    LINE  r2
    RET

setup:
    STT   r3, r3, RXB
    ADDT  DATAWAIT
    GETL  r1                      ; the DATA0 stage
    JNZ   next
    LDT   r2, r3, K_SETUPOK
    CMP   r1, r2
    JNZ   arm                     ; corrupt: no handshake, the host repeats the SETUP
    CALL  ack
    LDT   r0, r3, RXB             ; PID | bmRequestType << 8 | bRequest << 16 | wValue.lo << 24
    LDT   r1, r3, RXB + 1         ; wValue.hi | wIndex << 8 | wLength.lo << 24
    SHR   r0, 8
    MOV   r2, r1
    SHL   r2, 24
    OR    r0, r2                  ; r0 = bmRequestType | bRequest << 8 | wValue << 16
    TSTI  r0, 0x80
    JNZ   get
    MOV   r2, r0                  ; host to device: accepted with an empty status stage
    SHL   r2, 16
    SHR   r2, 16                  ; r2 = bmRequestType | bRequest << 8
    CMPI  r2, 0x0500
    JZ    set_address
    CMPI  r2, 0x0900              ; SET_CONFIGURATION and CLEAR_FEATURE(ENDPOINT_HALT):
    JZ    restart                 ; endpoint 1 restarts with DATA0 (9.4.5)
    CMPI  r2, 0x0102
    JNZ   accept
restart:                          ; the next report goes with DATA0, one already staged too
    LDT   r0, r3, K_REP0          ; (the feeder writes EP1CMD only while it holds NAK, so it
    STT   r0, r3, T1              ; cannot race this)
    LDT   r1, r3, EP1CMD
    LDT   r2, r3, K_NAK
    CMP   r1, r2
    JZ    accept
    STT   r0, r3, EP1CMD
accept:
    LDT   r0, r3, K_FULL1
    MOVI  r1, 0
    JMP   chunk                   ; a zero-length DATA1
set_address:                      ; it applies after the status stage: PEND = address << 1 | 1
    SHR   r0, 15
    ADDI  r0, 1
    STT   r0, r3, PEND
    JMP   accept
get:
    MOVI  r2, DTAB
look:
    LDT   r1, r2, 0
    CMPI  r1, 0
    JZ    stall                   ; not in the table: STALL
    CMP   r1, r0
    JZ    found
    ADDI  r2, 2
    JMP   look
found:
    LDT   r0, r2, 1               ; length << 24 | command
    LDT   r1, r3, RXB + 1
    SHR   r1, 24                  ; wLength.lo
    LDT   r2, r3, RXB + 2
    SHL   r2, 24
    SHR   r2, 16                  ; wLength.hi << 8
    OR    r1, r2                  ; r1 = wLength
    MOV   r2, r0
    SHR   r2, 24                  ; r2 = length
    CMP   r2, r1
    JNC   chunk
    MOV   r1, r2                  ; r1 = min(length, wLength)

; r0 = the command of a full packet, r1 = the bytes from it on: INCMD and LEFT for them
chunk:
    STT   r1, r3, LEFT
    CMPI  r1, 8
    JNC   setcmd
    MOVI  r2, 8                   ; fewer than 8 bytes: (8 - r1) * 8 bits fewer
    SUB   r2, r1
    SHL   r2, 11
    SUB   r0, r2
setcmd:
    STT   r0, r3, INCMD
    JMP   next
nak:
    LDT   r0, r3, K_NAK
    JMP   setcmd
stall:
    LDT   r0, r3, K_STALL
    JMP   setcmd

out0:
    STT   r3, r3, RXB
    ADDT  DATAWAIT
    GETL  r1                      ; the DATA stage
    JNZ   next
    SHL   r1, 16
    SHR   r1, 16
    CMPI  r1, 0x800D              ; CRC-16 residual
    JNZ   arm
    CALL  ack                     ; status stages and LED reports are acknowledged
    JMP   next

; =========================================================================== report feeder
feed:                             ; r3 = 0
    LDT   r2, r3, K_NAK
wait:
    LDT   r1, r3, EP1CMD
    CMP   r1, r2
    JNZ   wait                    ; the last report has not gone out yet
    POP   r0
    STT   r0, r3, REPORT
    POP   r0
    STT   r0, r3, REPORT + 1
    LDT   r0, r3, T1
    STT   r0, r3, EP1CMD          ; DATA0 or DATA1 with the report
    JMP   wait
