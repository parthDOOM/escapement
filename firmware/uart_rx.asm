; UART RX, 8N1, 115200 baud at 25 MHz (217 ticks/bit). Context 1, RX on pin 1.
; Each received byte is PUSHed to the host mailbox; a missing stop bit raises IRQ 0xFE
; (the byte is still pushed, with bit 8 set).
;
; The start edge is found with WAITP (quantised to the 4-tick slot plus the 2-tick
; synchroniser: the edge happened 2..6 ticks before the observing commit). Data bits
; are then captured by SAMPLE events at exact ticks: bit k centre = edge + (k+1.5)*217,
; using edge ~= observed - 4, so the worst-case sampling error is +/-2 ticks (1% of a bit).

.default CTX, 1              ; module parameters
.default RX_PIN, 1
.equ RX,     1 << RX_PIN
.equ BIT,    217
.equ FIRST,  325 - 4           ; 1.5 bits after the edge, minus the mean detection lag

.entry CTX, main

main:
    MOVI  r3, 0x0100           ; framing-error flag value
wait_start:
    RDTIME r1                  ; re-arm a long timeout (0.67 s) from now
    SETT  r1
    ADDT  0xFFFFFF
    WAITP RX, 0                ; wait for the line to go low (start bit)
    JNZ   wait_start           ; timeout: keep waiting
    RDTIME r1
    ADDI  r1, FIRST - 4        ; RDTIME commits one slot (4 ticks) after the WAITP
    SETT  r1                   ; tmr = centre of data bit 0
    LDLC  8
bits:
    SAMPLE RX_PIN, LSB         ; exact-tick sample, shifted in LSB-first
    ADDT  BIT
    DJNZ  bits
    WAITT                      ; tmr = centre of the stop bit
    GETP  r2
    GETI  r0
    SHR   r0, 24               ; the 8 samples sit in ish[31:24]
    TSTI  r2, RX
    JNZ   good_stop
    OR    r0, r3               ; framing error: flag in bit 8
    IRQ   0xFE
good_stop:
    PUSH  r0
    JMP   wait_start
