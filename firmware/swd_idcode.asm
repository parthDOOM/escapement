; ARM Serial Wire Debug (SWD) host in firmware: connect to an SWJ-DP, read the debug port
; ID register (DPIDR), and run any DP or AP register access the host asks for.
; Arm Debug Interface ADIv5.0-5.2, ARM IHI 0031G: B4.1-B4.2 (packets, turnaround, ACK,
; parity, WAIT/FAULT), B4.3.3 (line reset), B5.2.2 (JTAG-to-SWD switching: 0xE79E).
; Context 2; SWCLK pin 4 (push-pull), SWDIO pin 5 (push-pull, released for the target:
; output enable off, the target's pull-up holds the line). Same pins as jtag_idcode's
; TCK/TMS, as on an SWJ-DP connector.
;
; Host protocol: POP a command word, PUSH a status word and then a data word.
;   command [31]  connect first: 56 cycles high, 0xE79E, 57 cycles high, 8 idle cycles
;                 (the access after a connect must be a DPIDR read, 0x80000004; B4.3.3)
;           [30]  wake first (SWD protocol version 2, ADIv5.2 B5.3): 56 cycles high and
;                 0xE3BC (SWD to dormant), 56 high, the 128-bit selection alert, 4 low,
;                 the activation code 0x1A, then a line reset as for [31]. A multi-drop
;                 target (RP2040) then needs a TARGETSEL write before the DPIDR read:
;                 0x40000018, write data TARGETID | instance << 28 (B4.3.4)
;           [4:1] APnDP, RnW, A[2], A[3], as they go on the wire (bits 1-4 of the request
;                 byte); start, parity, stop and park are added here. DPIDR read = 0x04.
;           If RnW = 0, POP a second word: the write data.
;   status  [2:0] ACK as received: 1 OK, 2 WAIT (after WAIT_MAX retries), 4 FAULT,
;                 0 for a TARGETSEL write (DP write to 0xC), which no target answers: its
;                 ACK cycles are left to the pull-up and the data follows regardless,
;                 anything else = no valid response (protocol error or no target)
;           [3]   read data parity error   [4] line reset done after a missing ACK
;           [15:8] WAIT responses received
;   data    RDATA of an OK read, else 0.
; WAIT: the request is repeated after 8 idle cycles; the WAIT_MAX-th WAIT is reported.
; FAULT: reported; the host clears the sticky flag with an ABORT write (B4.2.4). No
; valid ACK: the line stays released for a whole potential data phase, then a line
; reset (B4.2.5).
; Data parity errors are reported, not retried (an AP read is not idempotent).
;
; One bit cycle from tick T: SWCLK falls at T; the host changes SWDIO at T + 1 (EMITX
; from sh) or samples it at T + 2 (the bit the target drove at the previous rising edge,
; an exact-tick SAMPLE in the middle of its cycle); SWCLK rises at T + H, where the
; target samples and changes SWDIO. H = CLK_KHZ * 1000 / (2 SWCLK_HZ), rounded up: 16 at
; 25 MHz, SWCLK 781 kHz. A bit is a 7-instruction loop (28 ticks), so H = 14 is the
; fastest clock; escert certifies H = 16 and H = 14 and refutes H = 13.
; The packet is played as segments (request; turnaround + ACK; data; parity; turnaround
; + idle). Each starts on an empty queue with a fresh time base, GAP ticks after the
; previous one ended, with SWCLK resting high in between (48-60 ticks instead of H; 143
; while the read parity is checked).
; The target acts only on rising SWCLK edges (B4.3.1) and the specification sets no
; minimum clock rate, so a longer high phase is harmless; bit-banged debug probes
; stretch SWCLK the same way. So no segment's timing depends on the code before it
; (escert proves each one alone), and the ACK is decoded while SWCLK is high after its
; third rising edge, which every response needs anyway.
; Limits: one turnaround cycle (DLCR default), overrun detection (CTRL/STAT.ORUNDETECT)
; must stay off. SWD protocol versions 1 and 2: dormant wake-up and TARGETSEL as above,
; the sequences OpenOCD sends (src/jtag/swd.h).

.default CTX, 2
.default CLK_PIN, 4
.default DIO_PIN, 5
.default CLK_KHZ, 25000
.default SWCLK_HZ, 781250
.default H, (CLK_KHZ * 1000 + 2 * SWCLK_HZ - 1) // (2 * SWCLK_HZ)   ; rounded up
.assert H >= 14, a bit is a 7-instruction loop (28 ticks): H of 14 ticks or more
.default WAIT_MAX, 64
.equ CLK, 1 << CLK_PIN
.equ DIO, 1 << DIO_PIN
.equ GAP, 28                    ; RDTIME to a segment's first edge

.entry CTX, main
.own CLK_PIN, CTX
.own DIO_PIN, CTX

main:
    ADDT  12
    EMIT  CLK | DIO, CLK | DIO, CLK | DIO   ; SWCLK high, SWDIO driven high
next:
    POP   r3
    MOVI  r1, 0
    TSTI  r3, 4                 ; RnW
    JNZ   build
    POP   r1                    ; write data
build:
    MOVI  r0, 0x1E
    AND   r0, r3
    CALL  fold                  ; r0[0] = parity of APnDP, RnW, A[2:3]
    MOVI  r2, 0x1E
    MOVHI r2, 0xC000
    AND   r3, r2                ; keep the connect and wake bits and APnDP, RnW, A[2:3]
    MOVI  r2, 0x1E
    AND   r2, r3
    CMPI  r2, 0x18
    JNZ   not_sel
    MOVI  r2, 0x200
    OR    r3, r2                ; r3[9]: TARGETSEL (a DP write to 0xC), never answered
not_sel:
    MOVI  r2, 0x81              ; start 1, stop 0, park 1
    TSTI  r0, 1
    JZ    req_even
    MOVI  r2, 0xA1              ; and parity 1
req_even:
    OR    r3, r2                ; r3[7:0] = request byte, LSB first on the wire
    MOV   r0, r1
    CALL  fold                  ; r0[0] = WDATA parity
    TSTI  r0, 1
    JZ    wd_even
    MOVI  r2, 0x100
    OR    r3, r2                ; r3[8] = WDATA parity
wd_even:
    MOVI  r2, 0                 ; status
    MOV   r0, r3
    SHR   r0, 30                ; [1] connect, [0] wake
    JZ    xfer
    TSTI  r0, 1
    JNZ   wake
    ; ---- connect (B5.2.2): >= 50 cycles high, JTAG-to-SWD select, then a line reset
    CALL  ones
    MOVI  r0, 0xE79E
    LDSH  r0
    LDLC  16
    CALL  wbits                 ; 0xE79E, LSB first
    JMP   lreset
    ; ---- wake (ADIv5.2 B5.3): SWD to dormant (ignored by a dormant target), then
    ; dormant to SWD, then a line reset
wake:
    CALL  ones                  ; >= 50 cycles high
    MOVI  r0, 0xE3BC
    LDSH  r0
    LDLC  16
    CALL  wbits                 ; SWD-to-dormant select
    CALL  ones                  ; >= 8 cycles high
    MOVI  r0, 0xF392
    MOVHI r0, 0x6209
    LDSH  r0
    LDLC  32
    CALL  wbits                 ; the selection alert, bits 0-31
    MOVI  r0, 0x2D95
    MOVHI r0, 0x8685
    LDSH  r0
    LDLC  32
    CALL  wbits                 ; bits 32-63
    MOVI  r0, 0xAFE9
    MOVHI r0, 0xE3DD
    LDSH  r0
    LDLC  32
    CALL  wbits                 ; bits 64-95
    MOVI  r0, 0x0EA2
    MOVHI r0, 0x19BC
    LDSH  r0
    LDLC  32
    CALL  wbits                 ; bits 96-127
    MOVI  r0, 0x1A0
    LDSH  r0
    LDLC  12
    CALL  wbits                 ; 4 cycles low, the SWD activation code 0x1A
lreset:
    CALL  ones                  ; line reset (B4.3.3): >= 50 cycles high,
    CALL  tail                  ; one more, then 8 idle cycles low (>= 2)
    TSTI  r2, 0x10
    JZ    xfer
    MOVI  r1, 0                 ; after a missing ACK: report
    JMP   finish

    ; ---- one transfer
xfer:
    LDSH  r3
    LDLC  8
    CALL  wbits                 ; start, APnDP, RnW, A[2:3], parity, stop, park
    LDLC  4
    CALL  rbits                 ; turnaround (SWDIO released), ACK[0], ACK[1], ACK[2]
    GETI  r0
    TSTI  r3, 0x200
    JNZ   wr_ok                 ; TARGETSEL: no ACK comes (B4.3.4); the data follows
    SHR   r0, 29                ; ACK[2:0]
    CMPI  r0, 1
    JZ    ack_ok
    CMPI  r0, 2
    JZ    ack_wait
    CMPI  r0, 4
    JNZ   no_ack
    OR    r2, r0                ; FAULT: the target has released SWDIO
    CALL  tail                  ; turnaround, idle
    MOVI  r1, 0
    JMP   finish
ack_wait:
    ADDI  r2, 0x100             ; WAIT: count it, turnaround and idle, retry
    CALL  tail
    MOV   r0, r2
    SHR   r0, 8
    CMPI  r0, WAIT_MAX
    JC    xfer
    ADDI  r2, 2
    MOVI  r1, 0
    JMP   finish
no_ack:
    ; no valid ACK (B4.2.5): stay off the line for a whole data phase and a turnaround,
    ; then line reset
    OR    r2, r0
    MOVI  r0, 0x10
    OR    r2, r0
    LDLC  34
    CALL  rbits
    GETI  r0                    ; discard
    JMP   lreset

ack_ok:
    TSTI  r3, 4
    JZ    wr_ok
    LDLC  32
    CALL  rbits                 ; RDATA[0:31]
    GETI  r1
    LDLC  1
    CALL  rbits                 ; parity; the target releases SWDIO at its rising edge
    GETI  r0                    ; parity in bit 31
    MOV   r3, r2
    SHR   r0, 31
    XOR   r0, r1
    CALL  fold                  ; even parity over RDATA and its parity bit
    MOV   r2, r3
    ADDI  r2, 1                 ; ACK OK
    TSTI  r0, 1
    JZ    rd_tail
    ADDI  r2, 8                 ; parity error
rd_tail:
    CALL  tail                  ; turnaround, idle
    JMP   finish

wr_ok:
    LDLC  1
    CALL  rbits                 ; turnaround: the target released at the last ACK edge
    GETI  r0
    LDSH  r1
    LDLC  32
    CALL  wbits                 ; WDATA[0:31]; the first bit takes the line back
    MOV   r0, r3
    SHR   r0, 8
    LDSH  r0
    LDLC  1
    CALL  wbits                 ; parity
    MOVI  r0, 0
    LDSH  r0
    LDLC  8
    CALL  wbits                 ; idle
    TSTI  r3, 0x200
    JNZ   sel_done              ; TARGETSEL: status 0
    ADDI  r2, 1
sel_done:
    MOVI  r1, 0
finish:
    WAITT                       ; the last segment has ended
    PUSH  r2
    PUSH  r1
    JMP   next

; Segments. Each waits for the previous one to end and starts GAP ticks after RDTIME.
; They clobber r0 and lc, and leave tmr = the falling edge after their last bit.

; wbits: drive lc bits from sh, LSB first.
wbits:
    WAITT
    RDTIME r0
    ADDI  r0, GAP
    SETT  r0
wb_bit:
    EMIT  CLK, 0, CLK           ; T
    ADDT  1
    EMITX DIO, LSB, DIO         ; T + 1: data
    ADDT  H - 1
    EMIT  CLK, CLK, CLK         ; T + H: the target samples
    ADDT  H
    DJNZ  wb_bit
    RET

; rbits: release SWDIO, then sample lc bits into ish, LSB first.
rbits:
    WAITT
    RDTIME r0
    ADDI  r0, GAP
    SETT  r0
    EMIT  DIO, 0, 0             ; release (SWCLK is high: the target sampled the last bit)
    ADDT  1
rb_bit:
    EMIT  CLK, 0, CLK           ; T
    ADDT  2
    SAMPLE DIO_PIN, LSB         ; T + 2: the bit driven at the previous rising edge
    ADDT  H - 2
    EMIT  CLK, CLK, CLK         ; T + H: the target drives the next bit
    ADDT  H
    DJNZ  rb_bit
    RET

; ones: 56 cycles with SWDIO driven high.
ones:
    WAITT
    LDLC  56
    RDTIME r0
    ADDI  r0, GAP
    SETT  r0
one_bit:
    EMIT  CLK, 0, CLK
    ADDT  1
    EMIT  DIO, DIO, DIO
    ADDT  H - 1
    EMIT  CLK, CLK, CLK
    ADDT  H
    DJNZ  one_bit
    RET

; tail: one turnaround cycle, then 8 idle cycles with the host driving SWDIO low.
tail:
    WAITT
    RDTIME r0
    ADDI  r0, GAP
    SETT  r0
    EMIT  CLK, 0, CLK
    ADDT  H
    EMIT  CLK, CLK, CLK
    ADDT  H
    LDLC  8
idle_bit:
    EMIT  CLK, 0, CLK
    ADDT  1
    EMIT  DIO, 0, DIO
    ADDT  H - 1
    EMIT  CLK, CLK, CLK
    ADDT  H
    DJNZ  idle_bit
    RET

; fold: r0[0] = XOR of all 32 bits of r0. Clobbers r2.
fold:
    MOV   r2, r0
    SHR   r2, 16
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 8
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 4
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 2
    XOR   r0, r2
    MOV   r2, r0
    SHR   r2, 1
    XOR   r0, r2
    RET
