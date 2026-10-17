; NEC infrared remote receiver: decodes the demodulated output of an IR receiver module
; (TSOP style: active low, idle high with a pull-up) and reports frames to the host.
; Context 1, input on pin 1 (not driven).
;
; Timing (Vishay doc. 80071 and SB-Projects, as in nec_tx.asm): 9 ms leader, then a
; 4.5 ms space for a frame or 2.25 ms for a repeat code; 32 bits LSB first, each a
; 562.5 us burst and a 562.5 us (0) or 1687.5 us (1) space; a 562.5 us stop burst.
; Acceptance windows are those of the Linux kernel decoder
; (drivers/media/rc/ir-nec-decoder.c), in units of 562.5 us: leader 16 +/- 2, frame
; space 8 +/- 1, repeat space 4 +/- 0.5, bursts 1 +/- 0.5, bit spaces 1 or 3 +/- 0.5.
; They absorb the receiver module's pulse-width distortion and a remote's oscillator
; error (about +/-10% overall).
;
; Every mark and space is measured edge to edge: WAITP (with a timeout of 18 units,
; so a stuck line is rejected) and RDTIME, in 4-tick units (exact to the slot grid,
; 0.16 us). No events are queued, so there is nothing for the certificate to refuse.
;
; Host: PUSH one word per valid transmission:
;   repeat code   0x80000000
;   frame         [23:16] command, [15:0] address, [24] extended: set when the second
;                 byte is not the inverse of the first, so [15:0] is a 16-bit address
; A frame whose command byte is not followed by its inverse, or any timing outside
; the windows, is dropped and the receiver waits for the line to idle. CLK_KHZ up to
; 25000 (the 18-unit bound must fit 16 bits in 4-tick units).

.default CTX, 1
.default PIN, 1
.default CLK_KHZ, 25000
.equ RX, 1 << PIN
.equ H, CLK_KHZ * 9                  ; H * k // 128 = k half units (281.25 us), 4-tick units
.equ TOUT, CLK_KHZ * 9 * 36 // 32    ; 18 units, in ticks: the longest level accepted

.entry CTX, main

main:
resync:                              ; wait for the idle (high) level
    RDTIME r2
    SETT  r2
    ADDT  0xFFFFFF
    WAITP RX, RX
    JNZ   resync
idle:                                ; wait for a burst
    RDTIME r2
    SETT  r2
    ADDT  0xFFFFFF
    WAITP RX, 0
    JNZ   idle
    RDTIME r2                        ; burst start (8 ticks after WAITP, as in meas)
    CALL  rise
    CMPI  r0, H * 28 // 128 + 1      ; leader 9 ms +/- 1.125 ms
    JC    resync
    CMPI  r0, H * 36 // 128
    JNC   resync
    CALL  fall
    CMPI  r0, H * 7 // 128 + 1
    JC    resync
    CMPI  r0, H * 9 // 128
    JC    repeat                     ; 2.25 ms +/- 0.28 ms
    CMPI  r0, H * 14 // 128 + 1      ; 4.5 ms +/- 0.5625 ms
    JC    resync
    CMPI  r0, H * 18 // 128
    JNC   resync
    LDLC  32
bits:
    CALL  rise                       ; bit burst
    CMPI  r0, H // 128 + 1
    JC    resync
    CMPI  r0, H * 3 // 128
    JNC   resync
    CALL  fall                       ; bit space
    SHR   r1, 1                      ; bits enter at 31: LSB first
    CMPI  r0, H // 128 + 1
    JC    resync
    CMPI  r0, H * 3 // 128
    JC    zero                       ; 562.5 us +/- 281 us
    CMPI  r0, H * 5 // 128 + 1
    JC    resync
    CMPI  r0, H * 7 // 128
    JNC   resync                     ; 1687.5 us +/- 281 us
    MOVI  r0, 0
    MOVHI r0, 0x8000
    OR    r1, r0
zero:
    DJNZ  bits
    CALL  rise                       ; stop burst
    CMPI  r0, H // 128 + 1
    JC    resync
    CMPI  r0, H * 3 // 128
    JNC   resync
    MOV   r0, r1                     ; r1 = ~cmd, cmd, ~addr, addr
    SHR   r0, 8
    XOR   r0, r1                     ; [23:16] cmd ^ ~cmd, [7:0] addr ^ ~addr
    MOV   r3, r0
    SHL   r3, 8
    SHR   r3, 24
    CMPI  r3, 0xFF
    JNZ   idle                       ; command not followed by its inverse: drop
    MOV   r3, r1
    SHL   r3, 8
    SHR   r3, 24
    SHL   r3, 16                     ; command << 16
    SHL   r0, 24
    SHR   r0, 24
    CMPI  r0, 0xFF
    JZ    std
    MOV   r0, r1                     ; extended: 16-bit address and flag
    SHL   r0, 16
    SHR   r0, 16
    OR    r3, r0
    MOVI  r0, 0
    MOVHI r0, 0x0100
    OR    r3, r0
    JMP   report
std:
    MOV   r0, r1
    SHL   r0, 24
    SHR   r0, 24
    OR    r3, r0
report:
    PUSH  r3
    JMP   idle
repeat:
    CALL  rise                       ; stop burst
    CMPI  r0, H // 128 + 1
    JC    resync
    CMPI  r0, H * 3 // 128
    JNC   resync
    MOVI  r3, 0
    MOVHI r3, 0x8000
    PUSH  r3
    JMP   idle

; ---- rise / fall: wait (from the edge at r2) for the line to go high / low;
; r0 = the level's length in 4-tick units, r2 = this edge.
rise:
    SETT  r2
    ADDT  TOUT
    WAITP RX, RX
    JMP   meas
fall:
    SETT  r2
    ADDT  TOUT
    WAITP RX, 0
    JMP   meas
meas:
    RDTIME r0
    MOV   r3, r0
    SUB   r0, r2
    MOV   r2, r3
    SHR   r0, 2
    RET
