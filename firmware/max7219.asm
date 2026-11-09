; MAX7219 LED display driver (Maxim MAX7219/MAX7221 datasheet): the chip drives a chain of
; CHAIN MAX7219s (DIN of the first on DIN_PIN, DOUT to the next DIN; CLK and LOAD shared).
; Each register write is a 16-bit packet, D15 first: D11-D8 the address, D7-D0 the data,
; shifted in on CLK's rising edge and latched on LOAD's rising edge, which comes after the
; 16th (16 x CHAIN-th) rising edge and before any next one. A frame sends one packet per
; device, the farthest first; devices not written get No-Op (address 0). LOAD idles high, is
; low while a frame shifts and rises to latch it. CLK at CLK_OUT_KHZ (10 MHz at most).
;
; Host (POP), [31:28] the command:
;   1: set up every device: Display Test off, Scan Limit [10:8] (digits 0 to n), Decode Mode
;      (all digits code B if [11], else none), Intensity [3:0], the 8 digits blank (code B
;      blank 0x0F, or 0x00), then Shutdown register 1 (normal operation)
;   2: write register [11:8] = [7:0] on device [26:24] (0: nearest the chip)
;   3: show [26:0] in decimal on device 0 (code B decode set up): digit 0 the units, leading
;      zeros blank (not by the scan limit, as the datasheet advises); above 99,999,999: dashes
; A host that reads back gets nothing: the MAX7219 has no read.

.default CTX, 0
.default CLK_PIN, 4
.default DIN_PIN, 5
.default LOAD_PIN, 7
.default CLK_KHZ, 25000
.default CLK_OUT_KHZ, 500
.default CHAIN, 1                           ; devices in the chain, 1 to 8
.default TB, 0xF0                           ; tape: digits, scratch
.equ CK, 1 << CLK_PIN
.equ DIN, 1 << DIN_PIN
.equ LD, 1 << LOAD_PIN
.equ H, (CLK_KHZ + 2 * CLK_OUT_KHZ - 1) // (2 * CLK_OUT_KHZ)
.equ LAT, 40
.equ GAP, 64
.equ DIG, TB                                ; 8 digits, digit 0 first
.equ IDX, TB + 8
.equ ARG, TB + 9                            ; the set-up word
.assert TB + 10 <= 256, "the tape area does not fit"
.assert 1 <= CHAIN <= 8, "1 to 8 devices"
.assert H * 1000000 // CLK_KHZ >= 50, "CLK high and low at least 50 ns: tCH and tCL"
.assert H >= 16, "the bit loop needs a half period of 16 ticks"
.equ ALL, 0xFF

.entry CTX, main
.own   CLK_PIN, CTX
.own   DIN_PIN, CTX
.own   LOAD_PIN, CTX

main:
    ADDT  40
    EMIT  CK|DIN|LD, LD, CK|DIN|LD          ; CLK low, LOAD high
cmd:
    POP   r0
    MOV   r1, r0
    SHR   r1, 28
    CMPI  r1, 1
    JZ    setup
    CMPI  r1, 2
    JZ    one
    CMPI  r1, 3
    JZ    dec
    JMP   cmd

one:                         ; 2: one register of one device
    MOV   r2, r0
    SHL   r2, 5
    SHR   r2, 29             ; the device, [26:24]
    MOV   r1, r0
    SHL   r1, 20
    SHR   r1, 20             ; [11:0]
    CALL  frame
    JMP   cmd

; ---- 1: set up every device
setup:
    MOVI  r3, 0
    STT   r0, r3, ARG
    MOVI  r1, 0x0F00         ; Display Test off
    MOVI  r2, ALL
    CALL  frame
    MOVI  r3, 0
    LDT   r0, r3, ARG
    MOV   r1, r0
    SHL   r1, 21
    SHR   r1, 29             ; the scan limit, [10:8]
    MOVI  r2, 0x0B00
    OR    r1, r2
    MOVI  r2, ALL
    CALL  frame
    MOVI  r3, 0
    LDT   r0, r3, ARG
    MOVI  r1, 0x0900         ; Decode Mode: none
    TSTI  r0, 0x800
    JZ    sdec
    MOVI  r1, 0x09FF         ; all digits code B
sdec:
    MOVI  r2, ALL
    CALL  frame
    MOVI  r3, 0
    LDT   r0, r3, ARG
    MOV   r1, r0
    SHL   r1, 28
    SHR   r1, 28             ; the intensity, [3:0]
    MOVI  r2, 0x0A00
    OR    r1, r2
    MOVI  r2, ALL
    CALL  frame
    MOVI  r3, 1              ; digits 0-7 (registers 1-8) blank
sblank:
    MOVI  r0, 0
    STT   r3, r0, IDX
    MOV   r1, r3
    SHL   r1, 8
    LDT   r0, r0, ARG
    TSTI  r0, 0x800
    JZ    sb0
    ADDI  r1, 0x0F           ; code B blank
sb0:
    MOVI  r2, ALL
    CALL  frame
    MOVI  r0, 0
    LDT   r3, r0, IDX
    ADDI  r3, 1
    CMPI  r3, 9
    JNZ   sblank
    MOVI  r1, 0x0C01         ; Shutdown register: normal operation
    MOVI  r2, ALL
    CALL  frame
    JMP   cmd

; ---- 3: a number in decimal on device 0
dec:
    SHL   r0, 5
    SHR   r0, 5              ; [26:0]
    MOVI  r1, 0xE0FF
    MOVHI r1, 0x05F5         ; 99,999,999
    CMP   r1, r0             ; borrow if the number is larger
    MOVI  r3, 0
    JNC   dfit
dash:
    MOVI  r1, 0x0A           ; code B '-'
    STT   r1, r3, DIG
    ADDI  r3, 1
    CMPI  r3, 8
    JNZ   dash
    JMP   dshow
dfit:                        ; r0 = the number, r3 = the digit
    MOVI  r1, 0              ; r0 / 10 into r0, the remainder into r1 (restoring division)
    LDLC  32
dv:
    SHL   r1, 1
    SHL   r0, 1              ; C = the next bit of the dividend
    JNC   dv0
    ADDI  r1, 1
dv0:
    CMPI  r1, 10
    JC    dv1                ; borrow: less than 10
    MOVI  r2, 10
    SUB   r1, r2
    ADDI  r0, 1              ; a quotient bit (bit 0, freed by the shift)
dv1:
    DJNZ  dv
    STT   r1, r3, DIG
    ADDI  r3, 1
    CMPI  r3, 8
    JNZ   dfit
    MOVI  r3, 7              ; leading zeros (not digit 0) blank
dz:
    LDT   r1, r3, DIG
    CMPI  r1, 0
    JNZ   dshow
    MOVI  r1, 0x0F
    STT   r1, r3, DIG
    MOVI  r1, 1
    SUB   r3, r1
    JNZ   dz
dshow:                       ; digits 0-7 into registers 1-8 of device 0
    MOVI  r3, 0
dsl:
    MOVI  r0, 0
    STT   r3, r0, IDX
    LDT   r1, r3, DIG
    ADDI  r3, 1
    SHL   r3, 8
    OR    r1, r3
    MOVI  r2, 0
    CALL  frame
    MOVI  r0, 0
    LDT   r3, r0, IDX
    ADDI  r3, 1
    CMPI  r3, 8
    JNZ   dsl
    JMP   cmd

; ---- one frame: packet r1[11:0] to device r2 (ALL: every device), No-Op to the others;
; the farthest device's packet first. Uses r0-r3.
frame:
    WAITT                    ; a fresh time base after the host
    RDTIME r0
    ADDI  r0, LAT
    SETT  r0
    EMIT  LD, 0, LD          ; LOAD low while the frame shifts
    MOVI  r3, CHAIN
fp:                          ; r3 = devices still to send, so device r3 - 1 now
    MOVI  r0, 1
    SUB   r3, r0
    MOV   r0, r1             ; this device's packet, or No-Op
    CMPI  r2, ALL
    JZ    fsend
    CMP   r2, r3
    JZ    fsend
    MOVI  r0, 0
fsend:
    SHL   r0, 16             ; D15 first, from sh[31]
    LDSH  r0
    LDLC  16
    ADDT  H + GAP            ; (CLK low longer before a packet: time to pick it)
fb:
    ADDT  1
    EMITX DIN, MSB, DIN      ; data a tick after the falling edge (tDS: H - 1 ticks)
    ADDT  H - 1
    EMIT  CK, CK, CK         ; rising edge: shifted in
    ADDT  H
    EMIT  CK, 0, CK          ; falling edge
    DJNZ  fb
    CMPI  r3, 0
    JNZ   fp
    ADDT  H
    EMIT  LD, LD, LD         ; LOAD rises: latched (after the last rising edge, before any next)
    ADDT  H                  ; LOAD high at least H (tCSW 50 ns, tLDCK 50 ns)
    RET
