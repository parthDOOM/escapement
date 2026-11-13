; SPI sniffer (v3, the line unit's external-clock receiver): logs every frame a controller
; sends to a target on a shared bus, the chip only listening. The line unit takes one bit
; per edge of SCK (the edge each SPI mode samples on) from MOSI into the tape, so a frame
; runs at the controller's clock however fast, up to one bit every 2 ticks (25 MHz SCK at
; 50 MHz). The firmware arms the receiver when CS_n falls (in external-clock mode a receive
; starts at once: a clocked bus has no start bit), and the frame ends when SCK has been
; quiet for 1.5 x QUIET ticks; it then reports and waits for CS_n to rise before the next.
; The first SCK edge must come at least 16 ticks after CS_n falls (the arm latency; 320 ns
; at 50 MHz, inside most targets' own CS setup time) and the controller's pauses inside a
; frame must be shorter than QUIET.
;
; Host (PUSH): per frame, the bit count, then the received words (MSB first: the frame's
; first bit is word 0's bit 31), the bytes in order, the last word's spare bits 0.

.default CTX, 0
.default SCK, 0
.default MOSI, 1
.default CS, 3
.default MODE, 0                            ; SPI mode (CPOL, CPHA)
.default QUIET, 2000                        ; ticks of SCK silence that end a frame (x 1.5)
.default LIM, 8                             ; buffer: 8 x 32 bytes
.equ CPOL, (MODE >> 1) & 1
.equ CPHA, MODE & 1
.equ FALLS, CPOL ^ CPHA                     ; modes 1 and 2 sample on SCK falling edges
.equ FB, 0x00
.equ CFG, 0xF0
.equ W0, MOSI | (SCK << 3) | (MOSI << 6)    ; data MOSI, clock SCK, MSB first
.equ W1, QUIET | (4 << 20)
.equ W2, 1 << 25                            ; extended, no CRC
.equ W3, 0
.equ W4, (2 << 21) | (FALLS << 23) | (1 << 25)   ; external clock on SCK, its edge; long
.equ CSM, 1 << CS

.entry CTX, main
.tape  CFG, W0, W1, W2, W3, W4

main:
    MOVI  r0, CFG
    MOVHI r0, 0x3000
    LINE  r0                 ; CONFIG
    ADDT  0xFFFFFF
    GETL  r1
idle:
    MOVI  r0, FB | (LIM << 8)
    MOVHI r0, 0x1000         ; the RECV, ready before the select (the arm latency)
    ADDT  0xFFFFFF
    WAITP CSM, CSM           ; deselected (a frame already under way is not joined)
    JNZ   idle
sel:
    ADDT  0xFFFFFF
    WAITP CSM, 0             ; CS_n falls
    JNZ   sel
    LINE  r0                 ; RECV: the next SCK edge is the frame's first bit
wait:
    ADDT  0xFFFFFF
    GETL  r1
    JNZ   wait
    GETM  r2, 3              ; bits
    PUSH  r2
    MOV   r3, r2
    ADDI  r3, 31
    SHR   r3, 5
    ADDI  r3, FB
    MOVI  r0, FB
    CMP   r0, r3
    JZ    idle
send:
    LDT   r2, r0, 0
    PUSH  r2
    ADDI  r0, 1
    CMP   r0, r3
    JNZ   send
    JMP   idle
