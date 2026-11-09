; Morse code keyer (Recommendation ITU-R M.1677-1, International Morse code): the chip keys a
; transmitter on pin KEY_PIN (1: key down) from text the host sends. Part 2 of the
; Recommendation: a dash is three dots; the space between the signals of one character is
; one dot, between two characters three dots, between two words seven dots. The characters
; are those of Part 1: the letters, the figures, and . , : ? ' - / ( ) " = + @ (the table
; below, generated from the Recommendation's list).
;
; Host (POP), [31:28] the command:
;   1: the dot length, [9:0] ms (1 to 1023)
;   2: up to three characters, [23:16] first, then [15:8], [7:0] (0: none): ASCII, lower
;      case sent as capitals; a space is the word space; others are skipped
; The host keeps the mailbox fed (it holds two words): a word that is there when the last
; one ends follows with the exact spacing; after a pause the keyer starts again afresh.

.default CTX, 0
.default KEY_PIN, 0
.default CLK_KHZ, 25000
.default TB, 0xA0                           ; tape: the table (64 words), state
.equ K, 1 << KEY_PIN
.equ MS, CLK_KHZ
.equ LAT, 1024                             ; up to three characters are looked up before a signal
.equ TABLE, TB                              ; ASCII 0x20-0x5F: length [11:8], the signals
.equ DOT, TB + 64                           ; from bit 7 (1: a dash)
.equ W, TB + 65
.assert TB + 66 <= 256, "the tape area does not fit"
.assert CLK_KHZ >= 1279, "escert certifies morse from 1279 kHz"

.tape DOT, 60
.tape TABLE + 0, 0x000, 0x000, 0x648, 0x000, 0x000, 0x000, 0x000, 0x678
.tape TABLE + 8, 0x5B0, 0x6B4, 0x000, 0x550, 0x6CC, 0x684, 0x654, 0x590
.tape TABLE + 16, 0x5F8, 0x578, 0x538, 0x518, 0x508, 0x500, 0x580, 0x5C0
.tape TABLE + 24, 0x5E0, 0x5F0, 0x6E0, 0x000, 0x000, 0x588, 0x000, 0x630
.tape TABLE + 32, 0x668, 0x240, 0x480, 0x4A0, 0x380, 0x100, 0x420, 0x3C0
.tape TABLE + 40, 0x400, 0x200, 0x470, 0x3A0, 0x440, 0x2C0, 0x280, 0x3E0
.tape TABLE + 48, 0x460, 0x4D0, 0x340, 0x300, 0x180, 0x320, 0x410, 0x360
.tape TABLE + 56, 0x490, 0x4B0, 0x4C0, 0x000, 0x000, 0x000, 0x000, 0x000

.entry CTX, main
.own   KEY_PIN, CTX

main:
    ADDT  LAT
    EMIT  K, 0, K            ; key up
cmd:
    POP   r0
    MOV   r1, r0
    SHR   r1, 28
    MOVI  r3, 0
    CMPI  r1, 1
    JNZ   text
    SHL   r0, 22             ; 1: the dot length, [9:0]
    SHR   r0, 22
    CMPI  r0, 0
    JZ    cmd
    STT   r0, r3, DOT
    JMP   cmd
text:
    CMPI  r1, 2
    JNZ   cmd
    WAITT                    ; a fresh time base after the host (any space before has passed)
    RDTIME r1
    ADDI  r1, LAT
    SETT  r1
word:                        ; r0 = the host word
    MOVI  r3, 0
    STT   r0, r3, W
    LDLC  3                  ; three characters (a counted loop: escert follows it exactly)
; The character loop is written twice: in chra nothing has been keyed in this word yet, in
; chrb something has. A word that keys nothing ends in a pause (a fresh time base later), so
; a run of empty words cannot eat the time before the next signal.
chra:                        ; the next character, [23:16] of W
    MOVI  r3, 0
    LDT   r0, r3, W
    MOV   r1, r0
    SHL   r1, 8
    SHR   r1, 24
    SHL   r0, 8
    STT   r0, r3, W
    CMPI  r1, 0x20
    JZ    space
    CMPI  r1, 0x61           ; lower case: as capitals
    JC    uppera
    CMPI  r1, 0x7B
    JNC   cnexta
    MOVI  r2, 0x20
    SUB   r1, r2
uppera:
    CMPI  r1, 0x60
    JNC   cnexta             ; not in the table
    CMPI  r1, 0x21
    JC    cnexta             ; control characters (and 0: none)
    MOVI  r2, 0x20
    SUB   r1, r2
    LDT   r2, r1, TABLE
    CMPI  r2, 0
    JZ    cnexta             ; not in the Recommendation
    JMP   key
cnexta:
    DJNZ  chra
    JMP   pause              ; nothing keyed
chrb:
    MOVI  r3, 0
    LDT   r0, r3, W
    MOV   r1, r0
    SHL   r1, 8
    SHR   r1, 24
    SHL   r0, 8
    STT   r0, r3, W
    CMPI  r1, 0x20
    JZ    space
    CMPI  r1, 0x61
    JC    upperb
    CMPI  r1, 0x7B
    JNC   cnext
    MOVI  r2, 0x20
    SUB   r1, r2
upperb:
    CMPI  r1, 0x60
    JNC   cnext
    CMPI  r1, 0x21
    JC    cnext
    MOVI  r2, 0x20
    SUB   r1, r2
    LDT   r2, r1, TABLE
    CMPI  r2, 0
    JZ    cnext
key:
    MOV   r3, r2
    SHR   r3, 8              ; r3 = signals, r2 = them from bit 7
sig:
    EMIT  K, K, K            ; key down
    TSTI  r2, 0x80
    JZ    sdot
    CALL  dot                ; a dash: three dots
    CALL  dot
sdot:
    CALL  dot
    EMIT  K, 0, K            ; key up
    CALL  dot                ; one dot between signals
    SHL   r2, 1
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   sig
    CALL  dot                ; three between characters
    CALL  dot
cnext:
    DJNZ  chrb
    RXREADY                  ; the next word already there: on, on the same time base
    JZ    pause
    POP   r0                 ; (RXREADY saw it: no wait)
    MOV   r1, r0
    SHR   r1, 28
    CMPI  r1, 2
    JZ    word
    WAITT                    ; (another command: after the gap)
    MOVI  r3, 0
    CMPI  r1, 1
    JNZ   cmd
    SHL   r0, 22
    SHR   r0, 22
    CMPI  r0, 0
    JZ    cmd
    STT   r0, r3, DOT
    JMP   cmd
pause:
    WAITT                    ; the gap after the last character has passed
    JMP   cmd
space:                       ; seven between words: four more after the three
    CALL  dot
    CALL  dot
    CALL  dot
    CALL  dot
    JMP   cnext

; ---- one dot: wait for tmr (the end of what came before), then tmr += DOT ms: 1 ms, then
; one constant delay per set bit of DOT - 1 (never less than 1 ms, whatever the tape holds;
; uses r0, r1)
dot:
    WAITT                    ; (every call starts at its tmr: one state for escert's join)
    MOVI  r0, 0
    LDT   r0, r0, DOT
    MOVI  r1, 1
    SUB   r0, r1
    ADDT  MS
    TSTI  r0, 1
    JZ    d1
    ADDT  MS
d1:
    TSTI  r0, 2
    JZ    d2
    ADDT  2 * MS
d2:
    TSTI  r0, 4
    JZ    d3
    ADDT  4 * MS
d3:
    TSTI  r0, 8
    JZ    d4
    ADDT  8 * MS
d4:
    TSTI  r0, 0x10
    JZ    d5
    ADDT  0x10 * MS
d5:
    TSTI  r0, 0x20
    JZ    d6
    ADDT  0x20 * MS
d6:
    TSTI  r0, 0x40
    JZ    d7
    ADDT  0x40 * MS
d7:
    TSTI  r0, 0x80
    JZ    d8
    ADDT  0x80 * MS
d8:
    TSTI  r0, 0x100
    JZ    d9
    ADDT  0x100 * MS
d9:
    TSTI  r0, 0x200
    JZ    d10
    ADDT  0x100 * MS         ; (512 ms in two: an ADDT holds 24 bits)
    ADDT  0x100 * MS
d10:
    RET
