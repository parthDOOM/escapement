; CAN 2.0A and 2.0B receiver on the line unit: every frame on the bus, standard and
; extended identifiers, checked by CRC-15 and handed to the host. A listening node
; (as a bus analyser in listen-only mode: it never acknowledges; another node must).
; Context 1, RXD on pin 1 (from a transceiver); no pin is driven, so a transmitter in
; another context (can_tx.asm) can share the bus.
;
; The line unit does the bit work (19-LINE-UNIT): a hard sync on the start-of-frame
; edge after RIDLE recessive bits, a resync on every recessive-to-dominant edge (`fall`),
; one sample per bit at SP, and destuffing (a complement after five equal bits). The
; reception ends at the first stuffing violation, which in a good frame is the sixth
; recessive bit: the ACK delimiter and five of the seven end-of-frame bits. Firmware
; then computes the frame's length from its DLC, has the line unit's CRC engine run
; CRC-15 (ISO 11898-1, 0x4599) over exactly SOF to the CRC field (a good frame leaves 0),
; re-arms the receiver into the other of two buffers, and only then parses and pushes,
; so the host may drain slowly; a frame that arrives while the receiver is not armed is
; missed, never corrupted. From the end of a reception to the next arm takes at most
; about 240 ticks: frames back to back (three bits of intermission) are all caught at
; 250 kbit/s; at 500 kbit/s the bus must leave two more idle bits between frames (a
; frame that starts sooner is lost, or reported as a CRC error).
;
; Host (PUSH): per good frame, word 0 = [31] extended, [30] remote, [28:0] identifier;
; word 1 = DLC; then the data bytes (none for a remote frame or DLC 0, one word for DLC
; 1-4, two for 5-8), byte 0 in [31:24]. A frame that fails its CRC: one word, 0x20000000.
; Bit rate KBPS (default 250 kbit/s) from CLK_KHZ, sample point 75%.

.default CTX, 1
.default RX, 1
.default KBPS, 250
.default CLK_KHZ, 25000
.default RIDLE, 1                           ; recessive bits before a start of frame
.default TB, 0xC0                           ; tape: 16 words
.equ PI, CLK_KHZ // KBPS                    ; bit period, whole ticks
.equ PF, (CLK_KHZ % KBPS) * 256 // KBPS     ;   and 1/256 tick
.equ SP, PI * 3 // 4                        ; sample point
.equ W0, (RX << 6) | (5 << 12) | (1 << 15)  ; receive pin, MSB first, stuff after 5 equal bits
.equ W1, PI | (PF << 12) | (SP << 20)
.equ W2, 0x8B32 | (1 << 19) | (RIDLE << 21) ; CRC-15 left-aligned, resync on falling edges
.equ CFG, TB
.equ BUFA, TB + 3                           ; two buffers of four words (128 bits)
.equ BUFB, TB + 7
.equ MASKT, TB + 11                         ; data word masks for 0-4 bytes

.entry CTX, main
.tape  CFG, W0, W1, W2
.tape  MASKT, 0, 0xFF000000, 0xFFFF0000, 0xFFFFFF00, 0xFFFFFFFF

main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG: this context owns the line unit
    MOVI  r3, BUFB
    CALL  arm                ; r3 = the buffer receiving
wait:
    ADDT  0xFFFFFF
    GETL  r1                 ; a reception ended
    JNZ   wait
    MOV   r0, r1
    SHL   r0, 4
    SHR   r0, 29
    CMPI  r0, 1              ; cause VIOL: the end of a frame
    JNZ   again
    LDT   r0, r3, 0          ; stream bits 0-31: SOF, identifier, ...
    MOV   r2, r0
    SHR   r2, 18             ; [0] IDE, [1] RTR of a standard frame
    TSTI  r2, 1
    JNZ   lext
    TSTI  r2, 2
    JNZ   lsrem
    MOVI  r2, 34             ; standard: SOF to CRC is 19 + 8n + 15 bits
    SHR   r0, 13             ; DLC in [3:0]
    JMP   ldlc
lsrem:
    MOVI  r2, 34             ; a remote frame has no data
    JMP   lhave
lext:
    LDT   r0, r3, 1          ; bits 32-63: RTR, r1, r0, DLC (35-38)
    SHL   r0, 1              ; C = RTR
    JC    lerem
    MOVI  r2, 54             ; extended: 39 + 8n + 15 bits
    SHR   r0, 26             ; DLC in [3:0]
    JMP   ldlc
lerem:
    MOVI  r2, 54
    JMP   lhave
ldlc:
    SHL   r0, 28
    SHR   r0, 28
    CMPI  r0, 9
    JC    l8
    MOVI  r0, 8              ; DLC 9-15 mean 8 bytes
l8: SHL   r0, 3
    ADD   r2, r0
lhave:                       ; r2 = bits from SOF to the end of the CRC
    MOV   r0, r1
    SHL   r0, 7
    SHR   r0, 23             ; bits received
    CMP   r0, r2
    JC    again              ; shorter than its own DLC says: not a frame
    MOV   r0, r2
    SHL   r0, 8
    OR    r0, r3
    MOVHI r0, 0x2000
    LINE  r0                 ; CRC job over exactly those bits
    ADDT  0xFFFFFF
    GETL  r1
    SHL   r1, 16             ; the CRC register: 0 for a good frame
    CALL  arm                ; the next frame goes to the other buffer
    MOVI  r0, BUFA + BUFB
    SUB   r0, r3             ; r0 = this frame's buffer
    MOV   r2, r1
    JZ    good
    MOVI  r1, 0
    MOVHI r1, 0x2000
    PUSH  r1                 ; CRC error
    JMP   wait
again:
    CALL  arm
    JMP   wait

good:
    LDT   r1, r0, 0
    MOV   r2, r1
    SHR   r2, 18
    TSTI  r2, 1
    JNZ   pext
    MOV   r2, r1             ; standard: identifier = W0[31:20] (SOF is 0)
    SHR   r2, 20
    SHR   r1, 13             ; [6] RTR, [3:0] DLC
    TSTI  r1, 0x40
    JZ    sdat
    MOVHI r2, 0x4000         ; remote
    PUSH  r2
    SHL   r1, 28
    SHR   r1, 28
    PUSH  r1
    JMP   wait
sdat:
    PUSH  r2
    SHL   r1, 28
    SHR   r1, 28
    PUSH  r1
    CALL  clip               ; sh = bytes; Z if none
    JZ    wait
    CMPI  r1, 5
    JC    s1
    CALL  sw0
    LDT   r1, r0, 1          ; bytes 4-7: W1 << 19 | W2 >> 13
    SHL   r1, 19
    LDT   r2, r0, 2
    SHR   r2, 13
    OR    r1, r2
    STSH  r2
    LDT   r2, r2, MASKT - 4
    AND   r1, r2
    PUSH  r1
    JMP   wait
s1: CALL  sw0
    JMP   wait
sw0:                         ; bytes 0-3: W0 << 19 | W1 >> 13
    LDT   r1, r0, 0
    SHL   r1, 19
    LDT   r2, r0, 1
    SHR   r2, 13
    JMP   w0out

pext:                        ; extended: identifier = W0[30:20] << 18 | W0[17:0]
    MOV   r2, r1
    SHR   r2, 20
    SHL   r2, 18
    SHL   r1, 14
    SHR   r1, 14
    OR    r2, r1
    MOVI  r1, 0
    MOVHI r1, 0x8000
    OR    r2, r1
    LDT   r1, r0, 1
    SHL   r1, 1              ; C = RTR
    JNC   edat
    MOVI  r1, 0
    MOVHI r1, 0x4000
    OR    r2, r1
    PUSH  r2
    LDT   r1, r0, 1
    SHL   r1, 3
    SHR   r1, 28             ; DLC = W1[28:25]
    PUSH  r1
    JMP   wait
edat:
    PUSH  r2
    LDT   r1, r0, 1
    SHL   r1, 3
    SHR   r1, 28
    PUSH  r1
    CALL  clip
    JZ    wait
    CMPI  r1, 5
    JC    e1
    CALL  ew0
    LDT   r1, r0, 2          ; bytes 4-7: W2 << 7 | W3 >> 25
    SHL   r1, 7
    LDT   r2, r0, 3
    SHR   r2, 25
    OR    r1, r2
    STSH  r2
    LDT   r2, r2, MASKT - 4
    AND   r1, r2
    PUSH  r1
    JMP   wait
e1: CALL  ew0
    JMP   wait
ew0:                         ; bytes 0-3: W1 << 7 | W2 >> 25
    LDT   r1, r0, 1
    SHL   r1, 7
    LDT   r2, r0, 2
    SHR   r2, 25
w0out:                       ; the first data word, bytes past the DLC cleared
    OR    r1, r2
    STSH  r2
    CMPI  r2, 5
    JNC   w0all
    LDT   r2, r2, MASKT
    AND   r1, r2
w0all:
    PUSH  r1
    RET

clip:                        ; r1 = DLC: sh = r1 = data bytes (DLC 9-15 mean 8), Z if 0
    CMPI  r1, 9
    JC    clipped
    MOVI  r1, 8
clipped:
    LDSH  r1
    MOV   r1, r1
    RET

arm:                         ; RECV into the buffer that is not r3, which becomes r3
    CMPI  r3, BUFA           ; (two paths, so that each LINE operand is a constant)
    JZ    armb
    MOVI  r2, (16 << 8) | BUFA
    MOVHI r2, 0x1000         ; RECV, limit 16 bytes
    MOVI  r3, BUFA
    LINE  r2
    RET
armb:
    MOVI  r2, (16 << 8) | BUFB
    MOVHI r2, 0x1000
    MOVI  r3, BUFB
    LINE  r2
    RET
