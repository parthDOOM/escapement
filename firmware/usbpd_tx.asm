; USB Power Delivery message transmitter on the CC wire (USB PD 3.x physical layer, ch. 5):
; Biphase Mark Coding at 270-330 kbit/s, push-pull on pin PIN at logic level (a CC driver and
; the termination are outside). One message per host request:
;   preamble: 64 bits alternating 0 and 1, starting with 0
;   SOP:      the K-codes Sync-1, Sync-1, Sync-1, Sync-2
;   header (16 bits) and N = header[14:12] data objects (32 bits each), 4b5b coded
;   CRC-32 over the header and data objects, 4b5b coded
;   EOP:      the K-code EOP
; Bytes go least significant first, each byte low nibble first, and each 5-bit symbol least
; significant bit first. BMC: a transition at the start of every bit, and one more in the
; middle of a 1. After EOP the line is driven low for one unit interval (tHoldLowBMC is at
; least 1 us), then released.
;
; The chip computes the CRC (IEEE 802.3 CRC-32: reflected polynomial 0xEDB88320, initial
; value and final XOR 0xFFFFFFFF) and the coding: it builds the whole bit stream in the tape
; first (no deadlines), then plays it. Each stream word holds up to 31 bits MSB first,
; followed by a 1 marking where its bits end; a zero word ends the stream.
;
; Host (POP): the header in [15:0], then its N data objects. Host (PUSH): 1 once sent.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default TB, 0x80                           ; tape: 4b5b table, message, scratch, stream
.equ HALF, (CLK_KHZ + 300) // 600           ; 42 ticks at 25 MHz: 297.6 kbit/s
.equ UI, 2 * HALF
.equ D, 1 << PIN
.equ T, TB                                  ; 16 words: the 4b5b codes of 0..F
.equ MSG, TB + 16                           ; header, up to 7 data objects, CRC
.equ NW, TB + 25                            ; index of the CRC word in MSG (N + 1)
.equ CNT, TB + 26                           ; bits in the stream word being built
.equ IDX, TB + 27                           ; the next stream word
.equ WK, TB + 28                            ; the message word being coded
.equ NLEFT, TB + 29                         ; its nibbles still to code
.equ CUR, TB + 30                           ; what is left of it
.equ OUT, TB + 32                           ; the stream: up to 15 words and the end word
.assert TB + 48 <= 256, "the tape area does not fit"
.assert 270000 <= CLK_KHZ * 1000 // UI <= 330000, "fBitRate is 270 to 330 kbit/s"
.assert CLK_KHZ >= 20000, "the BMC loop needs 20 MHz: escert finds a possible late edge at 16 MHz"

; 4b5b data symbols 0..F (USB PD Table 5.1), as numbers whose bit 0 is sent first
.tape T, 0x1E, 0x09, 0x14, 0x15, 0x0A, 0x0B, 0x0E, 0x0F, 0x12, 0x13, 0x16, 0x17, 0x1A, 0x1B, 0x1C, 0x1D
.equ K_SYNC1, 0x18                          ; 11000
.equ K_SYNC2, 0x11                          ; 10001
.equ K_EOP, 0x0D                            ; 01101

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  16
    EMIT  D, 0, 0            ; released
msg:
    POP   r0                 ; the header
    MOVI  r3, 0
    STT   r0, r3, MSG
    MOV   r1, r0
    SHL   r1, 17
    SHR   r1, 29             ; N = header[14:12]
    MOVI  r2, 1
dloop:                       ; N data objects into MSG[1..N]
    CMPI  r1, 0
    JZ    crc
    POP   r0
    STT   r0, r2, MSG
    ADDI  r2, 1
    MOVI  r0, 1
    SUB   r1, r0
    JMP   dloop

; ---- CRC-32 over the header's 2 bytes and the data objects (r2 = N + 1)
crc:
    MOVI  r3, 0
    STT   r2, r3, NW
    MOVI  r0, 0xFFFF
    MOVHI r0, 0xFFFF         ; r0 = crc
    LDT   r1, r3, MSG
    SHL   r1, 16
    SHR   r1, 16
    XOR   r0, r1
    MOVI  r2, 0x8320
    MOVHI r2, 0xEDB8         ; the reflected polynomial
    LDLC  16
c16:
    SHR   r0, 1
    JNC   c16n
    XOR   r0, r2
c16n:
    DJNZ  c16
    MOVI  r3, 1
cw:                          ; MSG[1..N]
    MOVI  r1, 0
    LDT   r1, r1, NW
    CMP   r3, r1
    JZ    cdone
    LDT   r1, r3, MSG
    XOR   r0, r1
    LDLC  32
c32:
    SHR   r0, 1
    JNC   c32n
    XOR   r0, r2
c32n:
    DJNZ  c32
    ADDI  r3, 1
    JMP   cw
cdone:
    MOVI  r1, 0xFFFF
    MOVHI r1, 0xFFFF
    XOR   r0, r1
    STT   r0, r3, MSG        ; MSG[N + 1] = the CRC (r3 = N + 1)

; ---- the bit stream: preamble, SOP, symbols, EOP (r1 = the word being built)
    MOVI  r3, 0
    STT   r3, r3, CNT
    STT   r3, r3, IDX
    MOVI  r1, 0
    MOVI  r0, 0xAAAA
    MOVHI r0, 0xAAAA         ; 0, 1, 0, 1, ... from bit 0
    LDLC  32
    CALL  putn
    MOVI  r0, 0xAAAA
    MOVHI r0, 0xAAAA
    LDLC  32
    CALL  putn
    MOVI  r0, K_SYNC1
    LDLC  5
    CALL  putn
    MOVI  r0, K_SYNC1
    LDLC  5
    CALL  putn
    MOVI  r0, K_SYNC1
    LDLC  5
    CALL  putn
    MOVI  r0, K_SYNC2
    LDLC  5
    CALL  putn
    MOVI  r3, 0              ; the header: 4 nibbles
    STT   r3, r3, WK
    MOVI  r0, 4
    STT   r0, r3, NLEFT
    LDT   r0, r3, MSG
    STT   r0, r3, CUR
nib:
    MOVI  r2, 0
    LDT   r3, r2, CUR
    MOV   r0, r3
    SHL   r0, 28
    SHR   r0, 28             ; the next nibble
    SHR   r3, 4
    STT   r3, r2, CUR
    LDT   r0, r0, T          ; its 5-bit symbol
    LDLC  5
    CALL  putn
    MOVI  r2, 0
    LDT   r3, r2, NLEFT
    MOVI  r0, 1
    SUB   r3, r0
    STT   r3, r2, NLEFT
    JNZ   nib
    LDT   r3, r2, WK         ; the next word: 8 nibbles each, up to and with the CRC
    ADDI  r3, 1
    STT   r3, r2, WK
    LDT   r0, r2, NW
    CMP   r0, r3             ; borrow once WK is past the CRC
    JC    eop
    LDT   r0, r3, MSG
    STT   r0, r2, CUR
    MOVI  r0, 8
    STT   r0, r2, NLEFT
    JMP   nib
eop:
    MOVI  r0, K_EOP
    LDLC  5
    CALL  putn
    MOVI  r3, 0              ; the last word: its bits, the end mark, zeros
    LDT   r2, r3, CNT
    CMPI  r2, 0
    JZ    endw
    SHL   r1, 1
    ADDI  r1, 1
    ADDI  r2, 1
left:
    CMPI  r2, 32
    JZ    last
    SHL   r1, 1
    ADDI  r2, 1
    JMP   left
last:
    LDT   r3, r3, IDX
    STT   r1, r3, OUT
    ADDI  r3, 1
    MOVI  r2, 0
    STT   r3, r2, IDX
endw:
    MOVI  r2, 0
    LDT   r3, r2, IDX
    STT   r2, r3, OUT        ; a zero word ends the stream

; ---- play it: BMC from the stream, every edge an event
    WAITT
    RDTIME r3
    ADDI  r3, 64
    SETT  r3
    EMIT  D, 0, D            ; drive low for one unit interval before the preamble
    ADDT  UI
    MOVI  r1, 0              ; the line level (0 or D)
    MOVI  r2, D
    MOVI  r3, 0              ; the next stream word
    MOVI  r0, 0
    MOVHI r0, 0x8000         ; an empty word: the first SHL loads one
bit:
    SHL   r0, 1              ; C = the next bit; Z = the word is used up
    JZ    reload
back:
    XOR   r1, r2             ; a transition at the start of every bit
    EMITR D, r1, D
    JC    one
    ADDT  UI
    JMP   bit
one:
    ADDT  HALF
    XOR   r1, r2             ; and one in the middle of a 1
    EMITR D, r1, D
    ADDT  HALF
    JMP   bit
reload:
    LDT   r0, r3, OUT
    ADDI  r3, 1
    SHL   r0, 1
    JNZ   back               ; (a zero word, or one with no bits: the end)
    EMIT  D, 0, D            ; low at the end of the last bit (tHoldLowBMC)
    ADDT  UI
    EMIT  D, 0, 0            ; released
    WAITT
    MOVI  r0, 1
    PUSH  r0
    JMP   msg

; ---- append the low lc bits of r0 to the stream, bit 0 first (r1 = the word being built;
; CNT and IDX in the tape); a word holds 31 bits, then a 1 marks its end
putn:
    MOVI  r3, 0
    LDT   r2, r3, CNT
pn:
    SHL   r1, 1
    SHR   r0, 1
    JNC   pn0
    ADDI  r1, 1
pn0:
    ADDI  r2, 1
    CMPI  r2, 31
    JNZ   pn1
    SHL   r1, 1
    ADDI  r1, 1              ; the end mark
    MOVI  r3, 0
    LDT   r3, r3, IDX
    STT   r1, r3, OUT
    ADDI  r3, 1
    MOVI  r2, 0
    STT   r3, r2, IDX
    MOVI  r1, 0
pn1:
    DJNZ  pn
    MOVI  r3, 0
    STT   r2, r3, CNT
    RET
