; S/PDIF (IEC 60958) / AES3 transmitter: two channels of 16-bit audio from the host, biphase
; mark on one pin. Held-out round, written 2026-11-24 on v3.1 (cb47436), from EBU Tech 3250-E,
; third edition 2004 ("the AES/EBU interface"; the IEC 60958 framing, which is not public).
;
;   pin PIN (0)   out: the line at logic level (an S/PDIF coax or optical driver, or an AES3
;                 balanced driver, goes after it)
; Context CTX (0), paired and spanning (context 2 stays off). Clock: 128 x FS x CELL
; exactly, so that a unit interval (half a time slot, 2.1.13) is CELL whole ticks and every
; transition is on the ideal clock (6.2.5.1 allows 0.025 UI of intrinsic jitter: there is
; none here). CELL is 8 or more (the line unit's one-cell period): 48 kHz at 49.152 MHz,
; 44.1 kHz at 45.1584 MHz, 32 kHz at 32.768 MHz. At the chip's default 25 MHz it sends
; 24.4 kHz frames.
;
; What it does (section numbers of Tech 3250):
; - Frames of two sub-frames, 32 time slots each (2.2.1): preamble, 24 audio bits, validity,
;   user, channel status, even parity over slots 4 to 31. The host's samples are 16 bits,
;   MSB-justified in slots 12 to 27, slots 4 to 11 zero (2.2.1: unused LSBs are 0).
; - Preambles X, Y and Z (2.4), Z on sub-frame 1 of every 192nd frame (2.2.2): blocks.
; - Biphase mark (2.3), sent by the line unit as NRZI: each bit is the pair "0, not b" (a 0
;   toggles the line): the first state of every symbol differs from the last of the one
;   before, and a 1 changes in the middle. The preambles are their states written the same
;   way, from the preceding state 0.
; - Validity (2.5): 0 for every host sample; when the host has no sample ready for a frame,
;   that frame carries zero samples with the validity bits 1 (not suitable for conversion).
; - User data (3): 0.
; - Channel status (4): two 192-bit blocks in the tape, one per sub-frame, sent from bit 0
;   of byte 0 in the frame with preamble Z. The default is Tech 3250's professional block in
;   its standard implementation for bytes 0 and 23 (5.2.2 asks bytes 1 and 2 too: their
;   defaults are 0): byte 0 = professional use and the sampling frequency (4, byte 0 bits 6
;   and 7, from FS), byte 23 = CRCC (Appendix 1, computed for each FS below). The host may
;   write any other block (a consumer block, IEC 60958-3) into the tape while the chip is
;   stopped.
;
; The stream never stops, though the line unit sends it as packets of PF frames: every
; preamble starts with three states at level 1 (the level before every sub-frame is 0,
; since the parity is even and the preambles are DC free), and the line unit idles at
; level 1. So each packet starts 3 cells into a sub-frame and stops 3 cells before the next
; packet's, and the idle line stands in for those 3 cells while the next SEND starts. Its
; deadline is the last one plus a packet, PF x 128 x CELL.
;
; Host (mailbox, context CTX):
;   host -> chip: one word a frame, [31:16] sub-frame 1 (left, A), [15:0] sub-frame 2
;                 (right, B), 16-bit two's complement
;   chip -> host: nothing

.default CTX, 0
.default PIN, 0
.default CELL, 8                   ; ticks per unit interval
.default FS, 48000                 ; the sampling frequency named in channel status
.assert CELL >= 8, a one-cell line unit configuration needs a period of 8 ticks or more
.equ PF, 1                         ; frames per packet: one, so the code is one straight pass

.equ PKT, PF * 128 * CELL          ; ticks per packet
.equ START, 2 * PKT                ; the first packet's start, after the first encoding
.equ NB, PF * 128 - 3              ; stream cells per packet
.equ BUF0, 0x00                    ; tape: two packet buffers (4 words each)
.equ BUF1, 0x04
.equ CSA, 0x40                     ; channel status, sub-frame 1 (6 words, byte 0 first)
.equ CSB, 0x46                     ; channel status, sub-frame 2
.equ CFG, 0x50                     ; line unit configuration (5 words)
.equ SMPL, 0x58                    ; scratch: this frame's host word
.equ VF, 0x59                      ;   its validity bits (1 << 24 or 0)
.equ NFR, 0x5A                     ;   the frame's number in the block, 0 to 191
.equ CA, 0x5B                      ;   channel status bits still to send from this word, A
.equ CB, 0x5C                      ;   ... and B
.equ PB, 0x5D                      ;   the buffer being filled
; SEND operands: a packet of NB stream cells from a buffer (ext: [29:24] NB / 128)
.equ SND0, ((NB >> 7) << 24) | ((NB & 127) << 8) | BUF0
.assert BUF0 == 0, the SEND operand is SND0 + the buffer

; the last five states of each preamble (cells 3 to 7), as NRZI from state 1 (cells 0 to 2
; are 1, 1, 1 in all three): X 11100010, Y 11100100, Z 11101000 (2.4, preceding state 0)
.equ PX, 0b00110
.equ PY, 0b10010
.equ PZ, 0b11000

; channel status byte 0 and the CRCC (Appendix 1: x^8 + x^4 + x^3 + x^2 + 1, all ones first,
; over bytes 0 to 22; for these blocks, all zero but byte 0)
.equ IS48, FS == 48000
.equ IS44, FS == 44100
.equ IS32, FS == 32000
.equ ISNO, 1 - IS48 - IS44 - IS32
.equ CS0, 0x01 + 0x80 * IS48 + 0x40 * IS44 + 0xC0 * IS32
.equ CRCC, 0x9B * IS48 + 0xDE * IS44 + 0x77 * IS32 + 0x32 * ISNO

; W0: PIN, NRZI, LSB first, idle level 1 driven, claim the pin. W1: a cell of CELL ticks
; (and a sample point of 4, unused, which a valid configuration needs: 4 <= S < CELL).
; W2: extended. W3: one cell a bit, 0 -> 0, 1 -> 1.
.tape CFG, PIN | (1 << 10) | (1 << 11) | (1 << 18) | (1 << 20), CELL | (4 << 20), 1 << 25, 1 << 11, 0
.tape CSA, CS0, 0, 0, 0, 0, CRCC << 24
.tape CSB, CS0, 0, 0, 0, 0, CRCC << 24
.tape NFR, 0
.tape PB, BUF1                     ; (the first packet toggles it to BUF0)

; Each sub-frame (written out twice above): r0 = slots 4 to 30 (bit 0 = slot 4), r2 = the
; preamble's code; sh = the address of its two stream words, advanced by 2. Stream cells are
; NRZI codes, LSB first, and each packet starts at cell 3 of a sub-frame, so word 0 holds
; cells 3 to 34 and word 1 cells 35 to 66 (66 being the next sub-frame's cell 2).
;   word 0: preamble cells 3..7 at bits 0..4; slot 4 + i: "0" at bit 5 + 2i, "not b" at 6 + 2i
;           (slots 4 to 16), and slot 17's first cell at bit 31
;   word 1: slot 17 + i: "not b" at bit 2i, "0" at 2i + 1 (slots 17 to 31, bits 0..29),
;           then the next preamble's cells 1 and 2: bits 30, 31 = 1
.entry CTX, main
.pair CTX
.span CTX                          ; 255 words fit one page; the span is kept, so context 2 stays off
.own PIN, CTX

main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                   ; CONFIG: this context owns the line unit; the pin idles high
m_cfg:
    ADDT  0xFFFFFF
    GETL  r1
    JNZ   m_cfg
    RDTIME r1
    ADDI  r1, START
    SETT  r1
    MOVI  r0, 0
    SEND  r0                   ; an empty packet two frames from now (the line stays idle):
                               ; the first frame starts 3 cells after it ends

; One pass a frame: encode it into the buffer the line unit is not sending, wait (GETL) for
; the packet before to end, which sets tmr to its end tick, and SEND this one 3 cells
; later. No CALLs, so that escert keeps the two sub-frames' code apart; and the deadline
; comes from GETL because escert bounds a packet's end only from below.
loop:
    MOVI  r3, 0
    LDT   r2, r3, PB
    MOVI  r1, BUF0 ^ BUF1
    XOR   r2, r1
    STT   r2, r3, PB
    LDSH  r2                   ; sh: where the next sub-frame's two words go
    RXREADY
    JZ    fr_none
    POP   r0
    MOVI  r1, 0
    JMP   fr_go
fr_none:                       ; no sample: zero, validity 1 (2.5)
    MOVI  r0, 0
    MOVI  r1, 1
fr_go:
    STT   r0, r3, SMPL
    SHL   r1, 24
    STT   r1, r3, VF
    LDT   r2, r3, NFR
    MOV   r1, r2
    SHL   r1, 27
    JNZ   fr_a                 ; not a multiple of 32: the channel status words go on
    SHR   r2, 5                ; the next word of each block
    LDT   r1, r2, CSA
    STT   r1, r3, CA
    LDT   r1, r2, CSB
    STT   r1, r3, CB
fr_a:                          ; sub-frame 1
    LDT   r0, r3, SMPL
    SHR   r0, 16
    SHL   r0, 8                ; slots 12 to 27
    LDT   r1, r3, VF
    OR    r0, r1               ; slot 28
    LDT   r1, r3, CA
    MOV   r2, r1
    SHR   r2, 1
    STT   r2, r3, CA
    SHL   r1, 31
    SHR   r1, 5
    OR    r0, r1               ; slot 30
    LDT   r2, r3, NFR
    CMPI  r2, 0
    JZ    fr_z
    MOVI  r2, PX
    JMP   fr_a1
fr_z:
    MOVI  r2, PZ               ; the block's first frame
fr_a1:
    MOV   r1, r0               ; even parity over slots 4 to 31 (2.2.1)
    MOV   r3, r1
    SHR   r3, 16
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 8
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 4
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 2
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 1
    XOR   r1, r3
    SHL   r1, 31
    SHR   r1, 4
    OR    r0, r1               ; slot 31
    MOV   r1, r0
    SHL   r1, 19
    SHR   r1, 19               ; slots 4 to 16, then bit i -> bit 2i:
    MOV   r3, r1
    SHL   r3, 8
    OR    r1, r3
    MOVI  r3, 0x00FF
    MOVHI r3, 0x00FF
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 4
    OR    r1, r3
    MOVI  r3, 0x0F0F
    MOVHI r3, 0x0F0F
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 2
    OR    r1, r3
    MOVI  r3, 0x3333
    MOVHI r3, 0x3333
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 1
    OR    r1, r3
    MOVI  r3, 0x5555
    MOVHI r3, 0x0155           ; (also the complement of 13 bits: "not b")
    AND   r1, r3
    XOR   r1, r3
    SHL   r1, 6
    OR    r1, r2               ; the preamble
    STSH  r3
    STT   r1, r3, 0
    MOV   r1, r0
    SHR   r1, 13               ; slots 17 to 31
    MOV   r2, r1
    SHL   r2, 8
    OR    r1, r2
    MOVI  r2, 0x00FF
    MOVHI r2, 0x00FF
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 4
    OR    r1, r2
    MOVI  r2, 0x0F0F
    MOVHI r2, 0x0F0F
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 2
    OR    r1, r2
    MOVI  r2, 0x3333
    MOVHI r2, 0x3333
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 1
    OR    r1, r2
    MOVI  r2, 0x5555
    MOVHI r2, 0x1555
    AND   r1, r2
    MOVHI r2, 0xD555           ; "not b" for 15 bits, and the next preamble's 1, 1
    XOR   r1, r2
    STT   r1, r3, 1
    ADDI  r3, 2
    LDSH  r3
    MOVI  r3, 0                ; sub-frame 2
    LDT   r0, r3, SMPL
    SHL   r0, 16
    SHR   r0, 8
    LDT   r1, r3, VF
    OR    r0, r1
    LDT   r1, r3, CB
    MOV   r2, r1
    SHR   r2, 1
    STT   r2, r3, CB
    SHL   r1, 31
    SHR   r1, 5
    OR    r0, r1
    MOVI  r2, PY
    MOV   r1, r0               ; even parity over slots 4 to 31 (2.2.1)
    MOV   r3, r1
    SHR   r3, 16
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 8
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 4
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 2
    XOR   r1, r3
    MOV   r3, r1
    SHR   r3, 1
    XOR   r1, r3
    SHL   r1, 31
    SHR   r1, 4
    OR    r0, r1               ; slot 31
    MOV   r1, r0
    SHL   r1, 19
    SHR   r1, 19               ; slots 4 to 16, then bit i -> bit 2i:
    MOV   r3, r1
    SHL   r3, 8
    OR    r1, r3
    MOVI  r3, 0x00FF
    MOVHI r3, 0x00FF
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 4
    OR    r1, r3
    MOVI  r3, 0x0F0F
    MOVHI r3, 0x0F0F
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 2
    OR    r1, r3
    MOVI  r3, 0x3333
    MOVHI r3, 0x3333
    AND   r1, r3
    MOV   r3, r1
    SHL   r3, 1
    OR    r1, r3
    MOVI  r3, 0x5555
    MOVHI r3, 0x0155           ; (also the complement of 13 bits: "not b")
    AND   r1, r3
    XOR   r1, r3
    SHL   r1, 6
    OR    r1, r2               ; the preamble
    STSH  r3
    STT   r1, r3, 0
    MOV   r1, r0
    SHR   r1, 13               ; slots 17 to 31
    MOV   r2, r1
    SHL   r2, 8
    OR    r1, r2
    MOVI  r2, 0x00FF
    MOVHI r2, 0x00FF
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 4
    OR    r1, r2
    MOVI  r2, 0x0F0F
    MOVHI r2, 0x0F0F
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 2
    OR    r1, r2
    MOVI  r2, 0x3333
    MOVHI r2, 0x3333
    AND   r1, r2
    MOV   r2, r1
    SHL   r2, 1
    OR    r1, r2
    MOVI  r2, 0x5555
    MOVHI r2, 0x1555
    AND   r1, r2
    MOVHI r2, 0xD555           ; "not b" for 15 bits, and the next preamble's 1, 1
    XOR   r1, r2
    STT   r1, r3, 1
    ADDI  r3, 2
    LDSH  r3
    MOVI  r3, 0                ; the next frame of the block
    LDT   r2, r3, NFR
    ADDI  r2, 1
    CMPI  r2, 192
    JNZ   fr_n
    MOVI  r2, 0
fr_n:
    STT   r2, r3, NFR
    MOVI  r0, SND0 & 0xFFFF
    MOVHI r0, SND0 >> 16
    LDT   r2, r3, PB
    ADD   r0, r2               ; this buffer's SEND operand (BUF0 is 0)
l_end:
    ADDT  0xFFFFFF
    GETL  r1                   ; the packet before has ended: tmr = its end
    JNZ   l_end
    ADDT  3 * CELL             ; this one's first cell (cell 3 of its first sub-frame)
    SEND  r0
    JZ    loop                 ; Z = 1: queued
late:                          ; dropped as late (escert: never): start the stream again
    RDTIME r1                  ; (its own copy of the start, so that escert does not join
    ADDI  r1, START            ; a busy line unit with the idle one after CONFIG)
    SETT  r1
    MOVI  r0, 0
    SEND  r0
    JMP   loop

