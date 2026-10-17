; Replay: re-emit a captured trace from the tape at its original tick spacing (v1.1).
; The host turns a capture into records {levels[7:0], tick[23:0]} at tape 0..N-1 (or
; builds one by hand) and assembles this file with N, PINS and CTX set; sw/esctape's
; build_replay() does both. Record k is driven at tick24(k) + offset, where the offset
; puts record 0 LEAD ticks after the start. Each record costs 10 instructions (40
; ticks); the 4-entry FIFO absorbs bursts. A trace denser than that is not replayed
; late or approximately: admission refuses the first edge that cannot be met
; (ADMIT/PAST), the context faults, and its pins release.
; Limits: one 2^24-tick epoch (0.67 s at 25 MHz); drives only the pins in PINS.

.default CTX, 0
.default PINS, 0x01            ; pins driven (must be owned: .own lines from the host)
.default N, 1                  ; records at tape 0 .. N-1
.default LEAD, 200

.entry CTX, main

main:
    MOVI  r3, 0xFFFF
    MOVHI r3, 0x00FF            ; r3 = tick mask 0x00FFFFFF
    MOVI  r1, 0                 ; r1 = record index
    LDT   r0, r1, 0
    AND   r0, r3                ; t0 = first record's tick
    RDTIME r2
    ADDI  r2, LEAD
    SUB   r2, r0                ; r2 = offset
loop:
    LDT   r0, r1, 0
    AND   r0, r3
    ADD   r0, r2
    SETT  r0                    ; tmr = record tick + offset
    LDT   r0, r1, 0
    SHR   r0, 24                ; levels
    EMITR PINS, r0, PINS
    ADDI  r1, 1
    CMPI  r1, N
    JNZ   loop
    HALT
