; 1-Wire bus controller (standard speed) - another protocol the design never names.
; Context 3, DQ on pin 1, open-drain (value 1 = release, 0 = drive low), external pull-up.
;
; One time slot does both directions: low at T, the bit to write at T+6 us (EMITX:
; a 1 releases the line = also a read slot, a 0 keeps it low), SAMPLE at T+15 us
; (exact tick), release at T+60 us, next slot at T+70 us. Writing 0xFF reads a byte.
;
; Host protocol: POP a command word:
;   [31] reset first (presence result in reply bit 8, 1 = device present)
;   [7:0] byte to write (use 0xFF to read)   -> PUSH {presence<<8 | byte read}

.default CTX,    3           ; module parameters
.default DQ_PIN, 1
.equ DQ,     1 << DQ_PIN
.equ US,     25              ; ticks per microsecond at 25 MHz

.entry CTX, main
.own   DQ_PIN, CTX
.od    DQ_PIN

main:
    ADDT  40
    EMIT  DQ, DQ, DQ         ; release
cmd:
    POP   r0
    MOVI  r2, 0              ; reply
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    MOVI  r1, 0x8000
    SHL   r1, 16             ; r1 = bit 31
    AND   r1, r0
    JZ    no_reset
    ; ---- reset: low 500 us, release, sample presence at +70 us, recover 430 us
    EMIT  DQ, 0, DQ
    ADDT  500*US
    EMIT  DQ, DQ, DQ
    ADDT  70*US
    SAMPLE DQ_PIN, LSB       ; presence: device pulls low => sample 0
    ADDT  430*US
    WAITT
    GETI  r1                 ; bit 31 = DQ level at +70 us
    SHR   r1, 31
    TSTI  r1, 1
    JNZ   no_reset           ; line high: nobody answered
    MOVI  r2, 0x100          ; presence detected
no_reset:
    LDSH  r0                 ; byte LSB first: EMITX LSB sends sh[0] and shifts right
    LDLC  8
    RDTIME r1                ; re-anchor: after the reset's WAITT, tmr is in the past
    ADDI  r1, 16             ; 4 instructions = 12 ticks to the EMIT, deadline must be later
    SETT  r1
slot:
    EMIT  DQ, 0, DQ          ; T        start of slot
    ADDT  6*US
    EMITX DQ, LSB, DQ        ; T+6us    1 = release (read slot), 0 = stay low
    ADDT  9*US
    SAMPLE DQ_PIN, LSB       ; T+15us   read the line
    ADDT  45*US
    EMIT  DQ, DQ, DQ         ; T+60us   release
    ADDT  10*US              ; recovery, next slot at T+70us
    DJNZ  slot
    WAITT
    GETI  r1
    SHR   r1, 24             ; 8 samples, LSB first, in ish[31:24]
    OR    r2, r1
    PUSH  r2
    JMP   cmd
