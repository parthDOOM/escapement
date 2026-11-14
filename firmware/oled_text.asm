; Text for the SSD1306 display engine (oled_disp.asm): takes commands from one queue, passes
; them on to the display's queue, and turns each TEXT command into the column strips the
; engine draws, so the engine needs no font and no text code of its own.
; Context 2, page 2 of the program store; no pins. Written for oled_pong.asm (context 1).
;
; In: TQ[0..15], TQH (written here) and TQT (by the producer), the display's command words
; (oled_disp.asm's header), plus
;   3 TEXT   [27:25] p, [24:22] s (1..4),   the string at tape word a (one character a byte,
;            [21:15] x, [7:0] a             the word's [7:0] first, 0xFF ends it): 4x7 glyphs,
;                                           scaled by s, in cells of 5s x 8s from column x, page p
; Out: DQ, DQT (written here), DQH (by the engine). A character is five strips of s columns,
; s pages each: its four glyph columns and a blank one. A strip's data word is in TXD at the
; strip's own queue index: that index is free when the strip is queued, and until the engine
; has drawn it nothing else is queued there.
; TQH moves on only when a command is wholly passed on, so the producer knows everything it
; queued is drawn when TQH == TQT and then DQH == DQT.

; ---- the tape (shared: TQ.. with oled_pong.asm, DQ.. with oled_disp.asm; test_oled_pong checks)
.equ FONT,   0               ; 22 glyphs: byte k of a word = column k, bit 0 the top row
.equ G_W,    149             ; two more, W and I: a character's code is its glyph's tape
.equ G_I,    151             ; word, so a glyph can go in any free word (not 0xFF)
.equ TQ,     22
.equ TQH,    38
.equ TQT,    39
.equ DQ,     40
.equ DQH,    48
.equ DQT,    49
.equ ONES,   90              ; ONES + s = (1 << s) - 1, s = 0..4
.equ T_SC,   128             ; scratch
.equ T_X,    129
.equ T_STR,  130
.equ T_GLY,  131
.equ T_GC,   132
.equ T_HEAD, 133
.equ T_W,    134
.equ TXD,    181             ; 8 words: the data of the strip at DQ + i is TXD + i

; glyphs 0-9, then space, ':', A E G K L N P R S Y (the letter O is 0), and W, I, from the
; drawings in sw/tests/oled_pong_model.py (test_oled_pong.py checks these words)
.tape FONT,      0x3E41413E, 0x407F4200, 0x46495162, 0x36494941, 0x7F08080F
.tape FONT + 5,  0x39454527, 0x3049493E, 0x030D7101, 0x36494936, 0x3E494906
.tape FONT + 10, 0x00000000, 0x00363600, 0x7E09097E, 0x4149497F, 0x7A49413E
.tape FONT + 15, 0x6314087F, 0x4040407F, 0x7F18067F, 0x0609097F, 0x6619097F
.tape FONT + 20, 0x31494946, 0x037C7C03
.tape G_W,       0x7F30307F
.tape G_I,       0x417F4100
.tape ONES, 0, 1, 3, 7, 15

.entry 2, main

main:
    MOVI  r3, 0              ; r3 = 0: the base of every fixed tape address
idle:
    LDT   r0, r3, TQH
    LDT   r1, r3, TQT
    CMP   r0, r1
    JZ    idle
    LDT   r1, r0, TQ         ; the command at the head
    MOV   r0, r1
    SHR   r0, 28
    CMPI  r0, 3
    JZ    text
    CALL  push               ; any other command, as it is
next:
    LDT   r0, r3, TQH
    ADDI  r0, 1
    MOVI  r1, 15
    AND   r0, r1
    STT   r0, r3, TQH        ; passed on
    JMP   idle

text:
    MOV   r0, r1
    SHL   r0, 7
    SHR   r0, 29
    STT   r0, r3, T_SC       ; s
    MOV   r0, r1
    SHL   r0, 10
    SHR   r0, 25
    STT   r0, r3, T_X
    MOV   r0, r1
    SHL   r0, 24
    SHR   r0, 22
    STT   r0, r3, T_STR      ; the string's byte address, 4a
    SHL   r1, 4
    SHR   r1, 29             ; p
    LDT   r0, r3, T_SC
    ADD   r0, r1
    MOVI  r2, 1
    SUB   r0, r2             ; p + s - 1
    SHL   r0, 22
    SHL   r1, 25
    OR    r0, r1
    MOVI  r1, 0
    MOVHI r1, 0x2000         ; STRIP
    OR    r0, r1
    STT   r0, r3, T_HEAD     ; the strips' pages
t_char:
    LDT   r0, r3, T_STR
    MOV   r1, r0
    SHR   r1, 2
    LDT   r1, r1, 0
    TSTI  r0, 1
    JZ    t_b1
    SHR   r1, 8
t_b1:
    TSTI  r0, 2
    JZ    t_b2
    SHR   r1, 16
t_b2:
    SHL   r1, 24
    SHR   r1, 24             ; the character
    CMPI  r1, 0xFF
    JZ    next               ; the end of the string
    ADDI  r0, 1
    STT   r0, r3, T_STR
    LDT   r1, r1, FONT
    STT   r1, r3, T_GLY
    MOVI  r0, 5
    STT   r0, r3, T_GC       ; five columns to go
t_col:
    LDT   r0, r3, T_GLY      ; the next column is the glyph's low byte: after four, 0
    MOV   r1, r0
    SHR   r1, 8
    STT   r1, r3, T_GLY
    SHL   r0, 24
    SHR   r0, 24
    LDT   r2, r3, T_SC
    MOV   r3, r2             ; r3 = s (no fixed tape address until r3 is 0 again)
    LDT   r2, r3, ONES       ; r2 = s ones: one glyph bit, scaled
    MOVI  r1, 0
    LDLC  8
t_spread:                    ; r1 = the column with each bit repeated s times
    SHR   r0, 1
    JNC   t_s0
    OR    r1, r2
t_s0:
    SHL   r2, 1              ; r2 <<= s
    CMPI  r3, 2
    JC    t_s1
    SHL   r2, 1
    CMPI  r3, 3
    JC    t_s1
    SHL   r2, 1
    CMPI  r3, 4
    JC    t_s1
    SHL   r2, 1
t_s1:
    DJNZ  t_spread
    MOVI  r3, 0
    STT   r1, r3, T_W
    LDT   r0, r3, T_X        ; columns x .. x + s - 1
    LDT   r1, r3, T_SC
    ADD   r1, r0
    STT   r1, r3, T_X        ; the next strip's x
    MOVI  r2, 1
    SUB   r1, r2
    SHL   r1, 8
    SHL   r0, 15
    OR    r1, r0
    LDT   r0, r3, T_HEAD
    OR    r1, r0
    LDT   r2, r3, T_W
    CALL  push_d
    LDT   r0, r3, T_GC
    MOVI  r1, 1
    SUB   r0, r1
    STT   r0, r3, T_GC
    JNZ   t_col
    JMP   t_char

; push_d: r1 = a STRIP with [7:0] = 0, r2 = its data word: the word into the slot of the
; entry it gets (free: the entry at DQT is never one being drawn), then as push.
push_d:
    LDT   r0, r3, DQT
    STT   r2, r0, TXD
    ADDI  r1, TXD
    ADD   r1, r0
; push: r1 = a command, into DQ once there is room (DQT + 1 != DQH). Uses r0, r2.
push:
    LDT   r0, r3, DQT
    ADDI  r0, 1
    MOVI  r2, 7
    AND   r0, r2             ; the next DQT
p_wait:
    LDT   r2, r3, DQH
    CMP   r0, r2
    JZ    p_wait             ; full
    LDT   r2, r3, DQT
    STT   r1, r2, DQ
    STT   r0, r3, DQT
    RET
