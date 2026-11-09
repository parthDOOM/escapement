; UART RX, 8N1, BAUD (115200) at CLK_KHZ (25 MHz: 217 ticks/bit). Context 1, RX on pin 1.
; Each received byte is PUSHed to the host mailbox; a missing stop bit raises IRQ 0xFE
; (the byte is still pushed, with bit 8 set).
;
; The start edge is found with WAITP (quantised to the 4-tick slot plus the 2-tick
; synchroniser: the edge happened 2..6 ticks before the observing commit). Data bits
; are then captured by SAMPLE events at exact ticks: bit k centre = edge + (k+1.5)*BIT,
; using edge ~= observed - 4, so the worst-case sampling error is +/-2 ticks (1% of a bit
; at 25 MHz).
;
; Back to back (a sender that starts the next byte straight after a stop bit, as most
; UARTs do), the next start edge comes half a bit after the stop bit's centre, so the line
; must be watched again at once. The data are read at bit 7's centre and the stop bit is
; a separate SAMPLE, so the byte before is pushed during the stop bit, and the line is
; watched again two instructions after the stop bit's centre. A byte that starts within
; 2.5 bits of it (up to 2 idle bits: 1.5 and 2 stop bits too) is taken at once, and this
; byte is pushed while the new one's samples are queued. Otherwise this byte is pushed
; (a message's last byte, 2.5 bits after its stop's centre) and the line is watched again
; four instructions later (16 ticks), without RDTIME. A start edge in those 16 ticks is
; seen up to 16 ticks late, which still lands every sample inside its bit from 54 ticks
; per bit. So every gap between bytes is read from 54 ticks per bit (460800 baud at
; 25 MHz, 921600 at 50 MHz); from 28 (the clock floor below) every gap but 2 to 2.8 idle
; bits (sw/tests/test_firmware.py sweeps both).
; Found on a real STM32's continuous output (sw/tests/test_real_devices.py): the earlier
; version pushed first and missed back-to-back bytes below about 100 ticks per bit.

.default CTX, 1              ; module parameters
.default RX_PIN, 1
.equ RX,     1 << RX_PIN
.default CLK_KHZ, 25000
.default BAUD, 115200
.equ BIT,    (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.equ FIRST,  (3 * BIT) // 2 - 4    ; 1.5 bits after the edge, minus the mean detection lag
.equ NONE,   0xFFFF                ; r2: no byte waiting to be pushed
.equ WIN,    (5 * BIT) // 2        ; 2.5 bits after the stop's centre: gaps of up to 2 idle bits
.assert BIT >= 28, "escert certifies uart_rx from 28 ticks per bit"

.entry CTX, main

main:
    MOVI  r3, 0x0100           ; framing-error flag value
    RDTIME r1
    SETT  r1
    JMP   rearm
ferr:                          ; no stop bit: flag the byte, raise IRQ, push it
    XOR   r0, r3
    IRQ   0xFE
    JMP   push_now
idle:                          ; no next byte within the window: push this one
    TSTI  r2, 1                ; the stop bit
    JZ    ferr
push_now:
    PUSH  r0
rearm:
    ADDT  0xFFFFFF             ; a long timeout (0.67 s) from the last deadline
    WAITP RX, 0                ; wait for the line to go low (start bit)
    JNZ   rearm                ; timeout: keep waiting
    RDTIME r1
    ADDI  r1, FIRST - 4        ; RDTIME commits one slot (4 ticks) after the WAITP
    SETT  r1                   ; tmr = centre of data bit 0
    MOVI  r2, NONE             ; no byte before this one to push
go:
    LDLC  8
bits:
    SAMPLE RX_PIN, LSB         ; exact-tick sample, shifted in LSB-first
    ADDT  BIT
    DJNZ  bits
    GETI  r0                   ; the data, at bit 7's centre: ish[31:24]
    SAMPLE RX_PIN, MSB         ; the stop bit, at its centre: ish[0]
    SHR   r0, 24
    CMPI  r2, NONE             ; the byte before this one, if it is still to be pushed
    JZ    watch
    TSTI  r2, 0x100
    JZ    push_prev
    IRQ   0xFE
push_prev:
    PUSH  r2
watch:
    GETI  r2                   ; the stop bit (at its centre)
    ADDT  WIN                  ; a start edge within 2.5 bits is the next byte
    WAITP RX, 0
    JNZ   idle
chain:
    RDTIME r1                  ; as after rearm: the same lag from WAITP
    ADDI  r1, FIRST - 4
    SETT  r1
    SHL   r2, 8                ; 0x100: stop bit seen
    XOR   r2, r3               ; 0x100: no stop bit
    OR    r2, r0               ; this byte, pushed after the next one's samples are queued
    JMP   go
