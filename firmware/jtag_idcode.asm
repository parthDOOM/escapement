; JTAG controller in firmware: reset the TAP, walk to Shift-DR, read the 32-bit IDCODE,
; return to Run-Test/Idle (IEEE 1149.1). Another bus the design never names.
; Context 2; TCK pin 4, TMS pin 5, TDI pin 6 (push-pull outputs), TDO pin 7 (input).
; TCK = 625 kHz (40 ticks). Host: POP anything; the chip PUSHes the IDCODE.
;
; One TCK cycle from slot T: TCK low at T, TMS/TDI at T + 1 (EMITX from sh), TDO sampled
; at T + H - 1 (an exact-tick SAMPLE, while TDO is stable since the falling edge), TCK
; high at T + H (the target samples TMS/TDI). The TMS path is a bit pattern in sh, so the
; TAP state machine is walked by data, not by code.

.default CTX, 2
.default TCK_PIN, 4
.default TMS_PIN, 5
.default TDI_PIN, 6
.default TDO_PIN, 7
.equ TCK, 1 << TCK_PIN
.equ TMS, 1 << TMS_PIN
.equ TDI, 1 << TDI_PIN
.equ H, 20

.entry CTX, main
.own TCK_PIN, CTX
.own TMS_PIN, CTX
.own TDI_PIN, CTX

main:
    ADDT  12
    EMIT  TCK | TMS | TDI, 0, TCK | TMS | TDI       ; TCK low, TMS 0, TDI 0
next:
    POP   r0
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    ; ---- TMS = 1,1,1,1,1 (Test-Logic-Reset), 0 (Idle), 1 (Select-DR), 0 (Capture), 0 (Shift)
    MOVI  r0, 0b001011111           ; LSB first
    LDSH  r0
    LDLC  9
nav:
    EMIT  TCK, 0, TCK               ; T: TCK low
    ADDT  1
    EMITX TMS, LSB, TMS             ; T + 1: TMS
    ADDT  H - 1
    EMIT  TCK, TCK, TCK             ; T + H: TCK high
    ADDT  H
    DJNZ  nav
    ; ---- Shift-DR: 32 bits, TMS 0 except the last (to Exit1-DR); TDI 0; sample TDO
    LDLC  31
shift:
    EMIT  TCK, 0, TCK
    ADDT  H - 1
    SAMPLE TDO_PIN, LSB             ; T + H - 1: TDO (LSB first: IDCODE bit 0 first)
    ADDT  1
    EMIT  TCK, TCK, TCK
    ADDT  H
    DJNZ  shift
    EMIT  TCK, 0, TCK               ; last bit: TMS 1 to leave Shift-DR
    ADDT  1
    EMIT  TMS, TMS, TMS
    ADDT  H - 2
    SAMPLE TDO_PIN, LSB
    ADDT  1
    EMIT  TCK, TCK, TCK
    ADDT  H
    ; ---- TMS 1 (Update-DR), 0 (Idle)
    EMIT  TCK, 0, TCK
    ADDT  H
    EMIT  TCK, TCK, TCK
    ADDT  H
    EMIT  TCK, 0, TCK
    ADDT  1
    EMIT  TMS, 0, TMS
    ADDT  H - 1
    EMIT  TCK, TCK, TCK
    ADDT  H
    EMIT  TCK, 0, TCK
    WAITT                           ; drain the queue before the next RDTIME re-anchors:
    GETI  r0                        ; a new request must not start under queued events
    PUSH  r0
    JMP   next
