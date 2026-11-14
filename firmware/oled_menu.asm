; The menu of oled_pong.asm (whose header has the whole picture): the title screen, the
; countdown, and a new game's state and first picture; then the game's turn until it is over.
; Context 3, bank B (the buttons), page 3 of the program store.
;
; Title: "PONG", "PRESS ANY KEY", "KEY 2: 2 PLAYERS", held until a button (ui_in[4..7]) is
; pressed, after all are released, so a button held over from a game does not count. The
; firmware has one game for both: a paddle nobody presses is played by the chip; the
; laptop's key map (sw/eschost/pong_keys.py) gives "2" the right paddle's keys.
; Then 3, 2, 1, GO, COUNT steps of TICK_US each, then a new game: vga_pong.asm's start, the
; LFSR going on from the last game, and its picture: the court, 0 : 0, both paddles and the
; ball on the net, which are always the same, so they are constant strips (K_NET0, K_PAD0).
; When the game is over with a side at ten points, the winner: the final score where the
; game showed it, "PLAYER 1" or "PLAYER 2", "WINS", for SHOW steps; then the title again.
; A game over with no winner (Esc) goes straight to the title. Esc is ui_in[3] (bank B pin
; 0): in the countdown and on the winner screen it goes to the title too, and the game
; (oled_pong.asm) ends on it; on the title it does nothing.

.default CLK_KHZ, 25000
.default TICK_US, 16667      ; one step: 60 Hz, as oled_pong.asm's
.default COUNT,   60         ; steps per countdown screen: one second
.default SHOW,    240        ; steps of the winner screen: four seconds
.equ TICK, TICK_US * CLK_KHZ // 1000
.assert 0 < TICK < (1 << 24), TICK_US too long for ADDT
.assert 0 < COUNT < 65536, COUNT out of range
.assert 0 < SHOW < 65536, SHOW out of range
.equ BTNS, 0x1E              ; bank B pins 1..4 = ui_in[4..7]
.equ ESC,  1                 ; bank B pin 0 = ui_in[3]

; ---- the tape (test_oled_pong checks that every name shared by two files agrees)
.equ TQ,     22
.equ TQH,    38
.equ TQT,    39
.equ DQH,    48
.equ DQT,    49
.equ STEPS,  95
.equ L_SCORE, 135            ; oled_pong.asm's: C_SL, C_SR, 0
.equ K_NET0, 138             ; the net's column with the ball on it (2 words)
.equ BX,     140             ; vga_pong.asm's state from here: BX .. FRAME
.equ AIM,    157
.equ SL,     146
.equ SR,     147
.equ G_W,    149             ; oled_text.asm's W and I
.equ G_I,    151
.equ PHASE,  172
.equ K_PAD0, 173             ; a paddle's column at the start (2 words)
.equ S_SL,   179
.equ S_SR,   180
.equ S_PONG, 197
.equ S_PRESS, 199
.equ S_KEY2, 203
.equ S_3,    208
.equ S_2,    209
.equ S_1,    210
.equ S_GO,   211
.equ L_TITLE, 212
.equ L_CD,   218
.equ L_CDE,  230
.equ L_COURT, 230
.equ NEWG,   240
.equ S_PLAYER, 252
.equ S_WINS, 254

; ---- commands (oled_disp.asm, oled_text.asm)
.equ C_INIT,  4 << 28
.equ FILL,    1 << 28        ; | p0 << 25 | p1 << 22 | v
.equ C_CLEAR, FILL | (0 << 25) | (7 << 22)
.equ C_MID,   FILL | (2 << 25) | (5 << 22)
.equ C_TOP,   FILL | (0 << 25) | (1 << 22)
.equ C_ROW16, FILL | (2 << 25) | (2 << 22) | 0x01
.equ C_COURT, FILL | (3 << 25) | (6 << 22)
.equ C_ROW63, FILL | (7 << 25) | (7 << 22) | 0x80
.equ STRIP,   (2 << 28) | (2 << 25) | (7 << 22)   ; the court's pages; | x0 << 15 | x1 << 8 | a
.equ C_NET0,  STRIP | (64 << 15) | (65 << 8) | K_NET0
.equ C_PADL0, STRIP | (4 << 15) | (5 << 8) | K_PAD0
.equ C_PADR0, STRIP | (122 << 15) | (123 << 8) | K_PAD0
.equ TXT,     3 << 28        ; | p << 25 | s << 22 | x << 15 | a
.equ C_PONG,  TXT | (1 << 25) | (3 << 22) | (34 << 15) | S_PONG
.equ C_PRESS, TXT | (5 << 25) | (1 << 22) | (32 << 15) | S_PRESS
.equ C_KEY2,  TXT | (7 << 25) | (1 << 22) | (24 << 15) | S_KEY2
.equ C_3,     TXT | (2 << 25) | (4 << 22) | (56 << 15) | S_3
.equ C_2,     TXT | (2 << 25) | (4 << 22) | (56 << 15) | S_2
.equ C_1,     TXT | (2 << 25) | (4 << 22) | (56 << 15) | S_1
.equ C_GO,    TXT | (2 << 25) | (4 << 22) | (46 << 15) | S_GO
.equ C_SL,    TXT | (0 << 25) | (2 << 22) | (40 << 15) | S_SL
.equ C_SR,    TXT | (0 << 25) | (2 << 22) | (80 << 15) | S_SR
.equ C_PLAYER, TXT | (3 << 25) | (2 << 22) | (25 << 15) | S_PLAYER
.equ C_WHO,   TXT | (3 << 25) | (2 << 22) | (95 << 15) | S_1   ; - side: S_1 or S_2
.assert S_2 == S_1 - 1, the winner's number is S_1 - side
.equ C_WINS,  TXT | (5 << 25) | (2 << 22) | (45 << 15) | S_WINS

; strings: 0-9 are themselves, 10 space, 11 ':', 12 A, 13 E, 14 G, 15 K, 16 L, 17 N,
; 18 P, 19 R, 20 S, 21 Y, G_W W, G_I I; O is 0; 0xFF ends a string
.tapeb S_PONG,  18, 0, 17, 14, 0xFF
.tapeb S_PRESS, 18, 19, 13, 20, 20, 10, 12, 17, 21, 10, 15, 13, 21, 0xFF
.tapeb S_KEY2,  15, 13, 21, 10, 2, 11, 10, 2, 10, 18, 16, 12, 21, 13, 19, 20, 0xFF
.tapeb S_3,     3, 0xFF
.tapeb S_2,     2, 0xFF
.tapeb S_1,     1, 0xFF
.tapeb S_GO,    14, 0, 0xFF
.tapeb S_PLAYER, 18, 16, 12, 21, 13, 19, 0xFF
.tapeb S_WINS,  G_W, G_I, 17, 20, 0xFF
.tape L_TITLE, C_INIT, C_CLEAR, C_PONG, C_PRESS, C_KEY2, 0
.tape L_CD,    C_CLEAR, C_3, 0, C_MID, C_2, 0, C_MID, C_1, 0, C_MID, C_GO, 0
.tape L_COURT, C_TOP, C_ROW16, C_COURT, C_ROW63, C_SL, C_SR, C_NET0, C_PADL0, C_PADR0, 0
.tape NEWG,    32, 40, 1, 2, 40, 40, 0, 0, 0
; the start's columns, court rows 0..31 then 32..47: both borders; the net (rows 2, 3 of
; every 4) and the ball (row 20); a paddle (rows 20..25)
.tape K_NET0,  0xCCCCCCCD | (1 << 20), 0xCCCC
.tape K_PAD0,  1 | (0x3F << 20), 0x8000

.bank  3
.entry 3, menu

menu:
    MOVI  r3, 0              ; r3 = 0: the base of every fixed tape address
    RDTIME r0
    SETT  r0                 ; the first step is due now
title:
    MOVI  r2, L_TITLE
    CALL  plist
t_up:                        ; until no button is held
    CALL  tick
    GETP  r0
    TSTI  r0, BTNS
    JNZ   t_up
t_down:                      ; until one is pressed
    CALL  tick
    GETP  r0
    TSTI  r0, BTNS
    JZ    t_down
    MOVI  r2, L_CD
cd:                          ; 3, 2, 1, GO
    CALL  plist
    ADDI  r2, 1
    LDLC  COUNT
cd_wait:
    CALL  tick
    GETP  r0
    TSTI  r0, ESC
    JNZ   title
    DJNZ  cd_wait
    CMPI  r2, L_CDE
    JNZ   cd
    MOVI  r2, 0              ; a new game: vga_pong's start
new:
    LDT   r0, r2, NEWG
    STT   r0, r2, BX
    ADDI  r2, 1
    CMPI  r2, 9
    JNZ   new
    MOVI  r0, 104
    STT   r0, r3, AIM
    STT   r0, r3, AIM + 1
    MOVI  r0, 0xFF00         ; 0 : 0
    STT   r0, r3, S_SL
    STT   r0, r3, S_SR
    MOVI  r2, L_COURT
    CALL  plist
    CALL  tick               ; drawn
    MOVI  r0, 1
    STT   r0, r3, PHASE      ; the game's turn
g_wait:
    LDT   r0, r3, PHASE
    TSTI  r0, 1
    JNZ   g_wait
    RDTIME r0
    SETT  r0
    MOVI  r2, 0              ; over: a side at ten points is the winner (r2 = side)
    LDT   r1, r3, SL
    CMPI  r1, 10
    JZ    won
    MOVI  r2, 1
    LDT   r1, r3, SR
    CMPI  r1, 10
    JNZ   title              ; no winner: Esc
won:
    MOVI  r1, 0xFF00         ; the scores as strings; the winner's 10 below
    LDT   r0, r3, SL
    OR    r0, r1
    STT   r0, r3, S_SL
    LDT   r0, r3, SR
    OR    r0, r1
    STT   r0, r3, S_SR
    MOVI  r0, 0x0001         ; "10": 1, 0, the end
    MOVHI r0, 0x00FF
    STT   r0, r2, S_SL
    MOVI  r0, C_CLEAR & 0xFFFF
    MOVHI r0, C_CLEAR >> 16
    CALL  q
    MOVI  r0, C_PLAYER & 0xFFFF
    MOVHI r0, C_PLAYER >> 16
    CALL  q
    MOVI  r0, C_WHO & 0xFFFF
    MOVHI r0, C_WHO >> 16
    SUB   r0, r2             ; "1" or "2"
    CALL  q
    MOVI  r0, C_WINS & 0xFFFF
    MOVHI r0, C_WINS >> 16
    CALL  q
    MOVI  r2, L_SCORE
    CALL  plist
    LDLC  SHOW
w_wait:                      ; drawn after the first step; SHOW steps, or Esc
    CALL  tick
    GETP  r0
    TSTI  r0, ESC
    JNZ   title
    DJNZ  w_wait
    JMP   title

; tick: wait for the next step (TICK after the last one's deadline) and until everything
; queued is drawn: TQ passed on, then DQ drawn. Uses r0, r1. (As oled_pong.asm's.)
tick:
    WAITT
    ADDT  TICK
tk_q:
    LDT   r0, r3, TQH
    LDT   r1, r3, TQT
    CMP   r0, r1
    JNZ   tk_q
    LDT   r0, r3, DQH
    LDT   r1, r3, DQT
    CMP   r0, r1
    JNZ   tk_q
    LDT   r0, r3, STEPS
    ADDI  r0, 1
    STT   r0, r3, STEPS
    RET

; plist: queue the commands from tape word r2 up to a 0, and leave r2 at the 0. Uses r0, r1.
plist:
    LDT   r0, r2, 0
    MOV   r0, r0
    JZ    pl_end
    LDT   r1, r3, TQT
    STT   r0, r1, TQ
    ADDI  r1, 1
    CMPI  r1, 16
    JNZ   pl_put
    MOVI  r1, 0
pl_put:
    STT   r1, r3, TQT
    ADDI  r2, 1
    JMP   plist
pl_end:
    RET

; q: queue the command r0. Uses r0, r1.
q:
    LDT   r1, r3, TQT
    STT   r0, r1, TQ
    ADDI  r1, 1
    MOVI  r0, 15
    AND   r1, r0
    STT   r1, r3, TQT
    RET
