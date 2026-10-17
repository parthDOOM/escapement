; I2C EEPROM emulator: a 24C02-compatible target (256 bytes, 8-byte write pages) whose
; memory is the TAPE (one byte per tape word). The chip becomes a real device on a
; real bus - a protocol it was never designed for, in firmware (v1.1 tape, D-24).
; Context 0, SCL on pin 2, SDA on pin 3, both open-drain (value 1 = release).
;
; Supported: byte/page write (S A|W word data.. P), random read (S A|W word Sr A|R ..),
; current-address read (S A|R ..), sequential reads with auto-increment, NACK on a
; foreign address. The address pointer persists across transactions like a 24C02.
; Preload the memory with .tape (or WRITE_TAPE); read it back with READ_TAPE.
;
; Timing, Standard and Fast mode (25 MHz, 1 tick = 40 ns):
;  * tmr is kept far in the future (ADDT FAR after every EMIT), so every WAITP is an
;    exact wait: it completes on the first 4-tick slot whose sync shows the edge.
;  * Each pin change is RDTIME; ADDI LAT; SETT; EMIT: deadline lead exactly 2 ticks
;    (min_lead telemetry reads 2), or the chip refuses the edge (ADMIT/PAST).
;  * The clock is stretched only BEFORE an ACK: SCL is held low within 28 ticks of the
;    8th falling edge (Fast-mode tLOW >= 33), so the ACK decision and tape access run
;    while the bus waits. After the ACK clock the controller paces again: SDA is
;    released (or read bit 7 driven) within 28 ticks of the 9th falling edge, and the
;    next bit is sampled <= 11 ticks after its rising edge, before a repeated START can
;    begin (tSU;STA >= 15). START/STOP polls run every 12 ticks (tHD;STA >= 15).

.default CTX, 0
.default SCL_PIN, 2
.default SDA_PIN, 3
.default DEV, 0x50
.equ SCL, 1 << SCL_PIN
.equ SDA, 1 << SDA_PIN
.equ BUS, SCL | SDA
.equ LAT, 14                    ; RDTIME..EMIT is 12 ticks: deadline lead 2
.equ FAR, 0xFFFFFF              ; tmr this far ahead turns WAITP into an exact wait
.equ SU, 8                      ; SDA set 8 ticks (320 ns) before SCL is released

.entry CTX, main
.own SCL_PIN, CTX
.own SDA_PIN, CTX
.od SCL_PIN
.od SDA_PIN

main:
    ADDT  10                    ; first EMIT commits at tick 7: deadline 10
    EMIT  BUS, BUS, BUS         ; release both lines
    ADDT  FAR
    MOVI  r3, 0                 ; r3 = EEPROM address pointer

; ---- START = SDA falls while SCL stays high: wait for the SDA edge exactly, then look
; at SCL <= 11 ticks after it.
idle:
    WAITP BUS, BUS              ; both high
    JNZ   idle
idle_hi:
    WAITP SDA, 0
    JNZ   idle_rearm            ; tmr window (0.67 s) ran out on an idle bus
    GETP  r1
    TSTI  r1, SCL
    JZ    idle                  ; SCL was low: an ordinary bit, not a START
start:                          ; S or Sr: SCL is still high, the first bit has not begun
    WAITP SCL, 0
    JNZ   start
    CALL  rx_byte               ; address byte; SCL held low after it
    MOV   r2, r0
    SHR   r2, 1
    CMPI  r2, DEV
    JNZ   release               ; not us: no ACK
    TSTI  r0, 1
    JNZ   rd

; ---- write: word address, then data bytes until STOP / Sr
    CALL  ack_rx                ; word address
    MOV   r3, r0
wloop:
    CALL  ack_rx                ; data byte; a STOP / Sr never returns
    STT   r0, r3, 0
    MOV   r1, r3                ; 8-byte page roll-over: r3 = (r3 & ~7) | ((r3 + 1) & 7)
    ADDI  r1, 1
    MOVI  r2, 7
    AND   r1, r2
    MOVI  r2, 0xF8
    AND   r3, r2
    OR    r3, r1
    JMP   wloop

; ---- read: bytes from the pointer until the controller NACKs
rd:
    LDT   r0, r3, 0
    SHL   r0, 24
    LDSH  r0                    ; sh = byte, MSB first
    ADDI  r3, 1
    CALL  ack_tx                ; ACK, byte, controller's ACK/NACK -> r2, SCL held
rnext:
    TSTI  r2, SDA
    JNZ   release               ; NACK: done
    LDT   r0, r3, 0
    SHL   r0, 24
    LDSH  r0
    ADDI  r3, 1
    CALL  tx_byte
    JMP   rnext

release:                        ; NACK / foreign address: let go of the bus
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  BUS, BUS, BUS
    ADDT  FAR
    JMP   idle
idle_rearm:
    RDTIME r1
    SETT  r1
    ADDT  FAR
    JMP   idle_hi

; ---- after a STOP the bus is idle, so the next SDA fall can only be a START (nobody
; may clock without one): no SCL check, which Fast-mode tBUF + tHD;STA leave no time for.
stopped:
    WAITP SDA, 0
    JNZ   stop_rearm
    JMP   start
stop_rearm:
    RDTIME r1
    SETT  r1
    ADDT  FAR
    JMP   stopped

; ---- rx_byte: 8 bits MSB first into r0; returns with SCL held low after bit 8. If SDA
; moves while SCL is high it was a STOP (-> stopped) or a repeated START (-> start):
; the call is abandoned, which is safe with a single link register.
rx_byte:
    MOVI  r0, 0
    LDLC  8
rx_hi:
    WAITP SCL, SCL
    JNZ   rx_hi
    GETP  r1                    ; pins while SCL is high (<= 11 ticks after the edge)
    DJNZ  rx_more
rx_f:                           ; bit 8: stretch as soon as SCL falls
    WAITP SCL, 0
    JNZ   rx_f
    RDTIME r2
    ADDI  r2, LAT
    SETT  r2
    EMIT  SCL, 0, SCL
    ADDT  FAR
    SHL   r0, 1
    TSTI  r1, SDA
    JZ    rx_ret
    ADDI  r0, 1
rx_ret:
    RET
rx_more:                        ; poll for the next event FIRST and shift the bit in
    TSTI  r1, SDA               ; after SCL falls (the low phase has >= 33 ticks): a
    JZ    lo0                   ; repeated START can begin 15 ticks into the high phase.
lo1:                            ; After a 1: SCL falls, or SDA falls = repeated START.
    GETP  r2                    ; 12-tick poll; first look <= 27 ticks after the edge
    CMP   r2, r1
    JZ    lo1
    TSTI  r2, SCL
    JZ    sh1                   ; SCL fell: next bit
    TSTI  r2, SDA
    JZ    start                 ; SDA fell with SCL high: repeated START
    GETP  r1                    ; an unwatched pin changed: re-take the sample
    JMP   lo1
sh1:
    SHL   r0, 1
    ADDI  r0, 1
    JMP   rx_hi
lo0:                            ; After a 0: SCL falls, or SDA rises = STOP, which
    GETP  r2                    ; reaches `stopped` <= 42 ticks after the edge
    CMP   r2, r1                ; (Fast-mode tBUF + tHD;STA leave 52).
    JZ    lo0
    TSTI  r2, SCL
    JZ    sh0
    TSTI  r2, SDA
    JNZ   stopped               ; SDA rose with SCL high: STOP
    GETP  r1
    JMP   lo0
sh0:
    SHL   r0, 1
    JMP   rx_hi

; ---- ack_rx: SCL held low on entry. ACK, let the controller clock it, release SDA in
; the following low phase, and receive the next byte (rx_byte returns to our caller).
ack_rx:
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  SDA, 0, SDA           ; ACK
    ADDT  SU
    EMIT  SCL, SCL, SCL         ; release SCL: the controller clocks the ACK
    ADDT  FAR
    MOVI  r0, 0
    LDLC  8
ar_hi:
    WAITP SCL, SCL
    JNZ   ar_hi
ar_lo:
    WAITP SCL, 0
    JNZ   ar_lo
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  SDA, SDA, SDA         ; release SDA (<= 28 ticks after the 9th falling edge)
    ADDT  FAR
    JMP   rx_hi

; ---- ack_tx: SCL held low on entry, sh = first byte << 24. ACK, then drive bit 7 in
; the low phase after the ACK clock and send the byte (tx_byte's tail returns).
ack_tx:
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  SDA, 0, SDA           ; ACK
    ADDT  SU
    EMIT  SCL, SCL, SCL
    ADDT  FAR
    LDLC  7
at_hi:
    WAITP SCL, SCL
    JNZ   at_hi
at_lo:
    WAITP SCL, 0
    JNZ   at_lo
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMITX SDA, MSB, SDA         ; bit 7
    ADDT  FAR
    JMP   tx_bit

; ---- tx_byte: SCL held low on entry (after the previous byte's ACK clock), sh = byte
; << 24. Drives bit 7, releases SCL, sends bits 6..0, releases SDA for the controller's
; ACK/NACK, returns its pins in r2 with SCL held low again.
tx_byte:
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMITX SDA, MSB, SDA         ; bit 7 (open drain: a 1 releases)
    ADDT  SU
    EMIT  SCL, SCL, SCL
    ADDT  FAR
    LDLC  7
tx_bit:
    WAITP SCL, SCL
    JNZ   tx_bit
tx_lo:
    WAITP SCL, 0
    JNZ   tx_lo
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMITX SDA, MSB, SDA         ; bits 6..0, each right after a falling edge
    ADDT  FAR
    DJNZ  tx_bit
tx_hi2:
    WAITP SCL, SCL
    JNZ   tx_hi2
tx_lo2:
    WAITP SCL, 0
    JNZ   tx_lo2
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  SDA, SDA, SDA         ; release SDA for the controller's ACK
    ADDT  FAR
tx_hi3:
    WAITP SCL, SCL
    JNZ   tx_hi3
    GETP  r2
tx_lo3:
    WAITP SCL, 0
    JNZ   tx_lo3
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
    EMIT  SCL, 0, SCL           ; stretch: the next byte is prepared while the bus waits
    ADDT  FAR
    RET
