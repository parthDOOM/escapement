; UART TX, 8N1, 115200 baud at a 25 MHz core clock (217 ticks/bit; 0.0064% rate error).
; Context 0 sends the bytes in TXDATA (low byte first) on pin 0, then halts.
; Exact-tick output: every bit edge is a DRIVE event at an absolute deadline.

.equ BIT, 217
.default CTX, 0              ; module parameters (override with .equ uart_tx.CTX, ...)
.default PIN, 0
.equ TX, 1 << PIN

.entry CTX, start
.own   PIN, CTX

start:
    ADDT  16                  ; tmr = 16: first deadline comfortably after commit
    EMIT  TX, TX, TX          ; idle high
    ADDT  BIT
    MOVI  r1, 4               ; bytes to send
    MOVI  r2, 1
    MOVI  r0, 0x4B55          ; "U" (0x55) then "K" (0x4B)
    MOVHI r0, 0x0A21          ; then "!" (0x21), "\n" (0x0A)
next_byte:
    LDSH  r0
    EMIT  TX, 0, TX           ; start bit
    ADDT  BIT
    LDLC  8
bit:
    EMITX TX, LSB, TX         ; data bit (blocks while the FIFO is full)
    ADDT  BIT
    DJNZ  bit
    EMIT  TX, TX, TX          ; stop bit
    ADDT  BIT
    SHR   r0, 8
    SUB   r1, r2              ; one byte fewer to send (Z when done)
    JNZ   next_byte
    HALT
