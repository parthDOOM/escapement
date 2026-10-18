; Logic analyser: arm the capture unit and halt. The machine keeps running (contexts
; halted, tick counter alive), so every change of a watched pin is recorded into the
; tape as {levels[7:0], tick[23:0]} until the region fills (or forever in ring mode).
; The host STOPs, reads the capture status (READ_DBG sel 17) and the tape (READ_TAPE),
; and decodes it with sw/esctape. No pins are owned: observation only (v1.1, D-24).
; STREAM = 1 (v1.3): the host reads records during the run and acknowledges them
; (TAPE_ACK); the unit never writes over an unacknowledged record, so a capture can run
; for as long as the host keeps up (docs/12-HOST-PROTOCOL.md, "Streaming capture").

.default CTX, 0
.default MASK, 0xFF            ; watched pins
.default BASE, 0               ; region [BASE, LIM) of the tape; LIM == BASE: all 256
.default LIM, 0
.default RING, 0               ; 1: overwrite the oldest records (pre-trigger history)
.default STREAM, 0             ; 1: stream to the host during the run (v1.3)

.entry CTX, main

main:
    MOVI  r0, (LIM << 8) | BASE
    MOVHI r0, 0x8000 | (STREAM << 10) | (RING << 8) | MASK
    CAPT  r0
    HALT
