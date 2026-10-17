; Classic CAN 2.0A transmitter in firmware: CRC-15 and bit stuffing computed on-chip,
; the frame played out at exact ticks, the ACK slot sampled at 70% - a bus the design
; never names, with the tape as frame buffer (v1.1).
; Context 0; TXD on pin 0 (push-pull, 1 = recessive) to a transceiver, RXD on pin 1.
; 250 kbit/s at 25 MHz (BIT = 100 ticks; J1939 rate). Single transmitter: no arbitration.
;
; Host: POP {id[10:0] << 16 | dlc}, POP data bytes 0..3 (byte 0 in [31:24]), POP bytes
; 4..7; the chip composes and sends the frame and PUSHes 1 if the ACK slot was dominant.
;
; Compose, then play. Composition has no deadline: each frame bit becomes one tape word
; (0 or TXM), the CRC is updated per bit, and after five equal bits the complement is
; inserted - found by looking back in the frame itself (LDT r1, r0, 254 is T[idx-2]), so
; no run-length state is kept. Playing is then one EMITR per bit; the ACK slot alone
; also queues a SAMPLE. Data-independent timing: escert certifies it.

.default CTX, 0
.default TX_PIN, 0
.default RX_PIN, 1
.default BIT, 100
.equ TXM, 1 << TX_PIN
.equ SAMP, (BIT * 7) // 10         ; sample point
.equ F0, 8                         ; first frame bit; T[4..7] are run-breaking sentinels
.equ SRC, 240                      ; scratch words (absolute, r3 = 0 while composing)
.equ CRC, 241
.equ IDX, 242
.equ FLG, 243                      ; bit 0: CRC on, bit 1: stuffing on
.equ CNT, 244                      ; bits left, counted up from -n
.equ W1,  245
.equ W2,  246
.equ ACKP, 247
.equ NDAT, 248

.entry CTX, main
.own TX_PIN, CTX

main:
    ADDT  10
    EMIT  TXM, TXM, TXM            ; TXD recessive
    MOVI  r3, 0                    ; r3 = 0: base for the scratch words
    MOVI  r2, 0xFFFF
    MOVHI r2, 0xFFFF
    STT   r2, r3, F0 - 4           ; sentinels: never equal to a bit word
    STT   r2, r3, F0 - 3
    STT   r2, r3, F0 - 2
    STT   r2, r3, F0 - 1
frame:
    POP   r0                       ; {id << 16 | dlc}
    POP   r1
    STT   r1, r3, W1
    POP   r1
    STT   r1, r3, W2
    MOVI  r1, F0
    STT   r1, r3, IDX
    MOVI  r1, 3                    ; CRC and stuffing on
    STT   r1, r3, FLG
    MOV   r2, r0
    SHL   r2, 28
    SHR   r2, 25                   ; dlc * 8
    STT   r2, r3, NDAT
    ; ---- 19 header bits: SOF 0, ID[10:0], RTR 0, IDE 0, r0 0, DLC[3:0]
    MOV   r1, r0
    SHR   r1, 16
    SHL   r1, 21
    SHR   r1, 1                    ; ID at [30:20], SOF (0) at 31
    MOV   r2, r0
    SHL   r2, 28
    SHR   r2, 15                   ; DLC at [16:13]
    OR    r1, r2
    MOV   r0, r1
    MOVI  r1, 0                    ; CRC register
    MOVI  r2, 0xFFED               ; -19
    MOVHI r2, 0xFFFF
    STT   r2, r3, CNT
    CALL  emit
    ; ---- data: dlc * 8 bits, bytes 0..3 from W1 then 4..7 from W2
    LDT   r2, r3, NDAT
    CMPI  r2, 0
    JZ    crc_f
    CMPI  r2, 33
    JNC   data2                    ; n >= 33
    MOVI  r0, 0
    SUB   r0, r2
    STT   r0, r3, CNT              ; -n
    LDT   r0, r3, W1
    CALL  emit
    JMP   crc_f
data2:
    MOVI  r0, 0xFFE0               ; -32
    MOVHI r0, 0xFFFF
    STT   r0, r3, CNT
    LDT   r0, r3, W1
    CALL  emit
    LDT   r2, r3, NDAT
    MOVI  r0, 32
    SUB   r0, r2                   ; -(n - 32)
    STT   r0, r3, CNT
    LDT   r0, r3, W2
    CALL  emit
    ; ---- CRC sequence: stuffed, not itself CRC'd
crc_f:
    MOV   r0, r1
    SHL   r0, 17
    MOVI  r2, 2
    STT   r2, r3, FLG
    MOVI  r2, 0xFFF1               ; -15
    MOVHI r2, 0xFFFF
    STT   r2, r3, CNT
    CALL  emit
    ; ---- CRC delimiter, ACK slot, ACK delimiter, EOF, intermission: 13 recessive
    LDT   r2, r3, IDX
    ADDI  r2, 1                    ; the ACK slot follows the CRC delimiter
    STT   r2, r3, ACKP
    MOVI  r2, 0
    STT   r2, r3, FLG
    MOVI  r2, 0xFFF3               ; -13
    MOVHI r2, 0xFFFF
    STT   r2, r3, CNT
    MOVI  r0, 0xFFFF
    MOVHI r0, 0xFFFF
    CALL  emit
    ; ---- play T[F0 .. IDX) at BIT ticks per bit
    LDT   r1, r3, IDX              ; end
    LDT   r2, r3, ACKP
    MOVI  r3, F0                   ; r3 = bit index from here on
    RDTIME r0
    ADDI  r0, 40
    SETT  r0
tx:
    LDT   r0, r3, 0
    EMITR TXM, r0, TXM
    CMP   r3, r2
    JNZ   tx_n
    ADDT  SAMP                     ; ACK slot: sample RXD at 70%
    SAMPLE RX_PIN, MSB
    ADDT  BIT - SAMP
    JMP   tx_c
tx_n:
    ADDT  BIT
tx_c:
    ADDI  r3, 1
    CMP   r3, r1
    JNZ   tx
    WAITT                          ; the whole frame is on the bus before the next one
    GETI  r0                       ; the ACK-slot sample
    MOVI  r1, 1
    XOR   r0, r1                   ; dominant (0) = acknowledged
    PUSH  r0
    MOVI  r3, 0
    JMP   frame

; ---- emit: r0 = bits, MSB first; T[CNT] = -n; T[FLG] = flags; r1 = CRC; r3 = 0.
; Appends each bit as one tape word at T[IDX] (CRC update, stuffing per the flags).
emit:
    SHL   r0, 1                    ; C = next bit
    STT   r0, r3, SRC              ; spill the source: r0 is a temporary below
    MOVI  r2, 0
    JNC   e_crc
    MOVI  r2, TXM                  ; r2 = bit word
e_crc:
    LDT   r0, r3, FLG
    TSTI  r0, 1
    JZ    e_app
    TSTI  r1, 0x4000               ; crc_nxt = bit ^ crc[14]
    JZ    e_c0
    TSTI  r2, TXM
    JZ    e_x
    JMP   e_nx
e_c0:
    TSTI  r2, TXM
    JZ    e_nx
e_x:
    SHL   r1, 1
    MOVI  r0, 0x4599               ; ISO 11898-1 CRC-15 polynomial
    XOR   r1, r0
    JMP   e_m
e_nx:
    SHL   r1, 1
e_m:
    MOVI  r0, 0x7FFF
    AND   r1, r0
e_app:
    LDT   r0, r3, IDX
    STT   r2, r0, 0                ; T[idx] = bit
    ADDI  r0, 1
    STT   r0, r3, IDX
    STT   r1, r3, CRC              ; spill the CRC: r1 is the compare temporary
    LDT   r1, r3, FLG
    TSTI  r1, 2
    JZ    e_ns
    LDT   r1, r0, 254              ; the four bits before this one, newest first
    CMP   r1, r2
    JNZ   e_ns
    LDT   r1, r0, 253
    CMP   r1, r2
    JNZ   e_ns
    LDT   r1, r0, 252
    CMP   r1, r2
    JNZ   e_ns
    LDT   r1, r0, 251
    CMP   r1, r2
    JNZ   e_ns
    MOVI  r1, TXM                  ; five equal bits: stuff the complement
    XOR   r2, r1
    STT   r2, r0, 0
    ADDI  r0, 1
    STT   r0, r3, IDX
e_ns:
    LDT   r1, r3, CRC
    LDT   r0, r3, CNT
    ADDI  r0, 1
    STT   r0, r3, CNT
    JZ    e_done
    LDT   r0, r3, SRC
    JMP   emit
e_done:
    RET
