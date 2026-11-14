; Pong on a 128x64 SSD1306 OLED: vga_pong.asm's game, with a title screen and a countdown.
; Four contexts, one program page each:
;   ctx 0  oled_disp.asm  page 0  the display engine: I2C to the OLED (uio[2] SCL, uio[3] SDA)
;   ctx 1  oled_pong.asm  page 1  this file: the game
;   ctx 2  oled_text.asm  page 2  text into strips, between the producers and the engine
;   ctx 3  oled_menu.asm  page 3  the title, the countdown, and a new game's first picture
; The menu and the game take turns (PHASE: 0 the menu's, 1 the game's), so one context at
; a time queues commands in TQ (oled_text.asm's header); every step first waits until
; everything queued before it is drawn.
;
; The game is vga_pong.asm's, step for step (its header has the rules): a 64 x 48 court of
; cells, rows counted doubled; odd steps move the paddles, even steps the ball; one step
; every TICK_US. Buttons: ui_in[4], ui_in[5] the left paddle up and down, ui_in[6], ui_in[7]
; the right (active high); a paddle whose buttons are idle is played by the chip, so one
; player takes the left paddle against the chip, or two players one each. The changes: at
; 10 points the game is over (PHASE = 0, back to the menu, which shows the winner) instead
; of starting again, and ui_in[3] (Esc on the laptop) ends it at the next paddle step.
;
; On the OLED a cell is 2 x 1 pixels: the court is pages 2..7 (rows 16..63), the score is
; above it in pages 0..1. The display RAM keeps the picture, so a step redraws only cell
; columns that may have changed: after a paddle step both paddles', after a ball step the
; ball's old and new; each such strip is drawn whole (borders, net, paddle and ball,
; whichever are in it), and the score when it changed.

.default CLK_KHZ, 25000
.default TICK_US, 16667      ; one step: 60 Hz, as vga_pong's frames
.equ TICK, TICK_US * CLK_KHZ // 1000
.assert 0 < TICK < (1 << 24), TICK_US too long for ADDT

; ---- the tape (test_oled_pong checks that every name shared by two files agrees)
.equ TQ,     22
.equ TQH,    38
.equ TQT,    39
.equ DQH,    48
.equ DQT,    49
.equ STEPS,  95              ; steps since the start (the tests' clock)
.equ MASK,   96              ; MASK + i = 1 << i
.equ L_SCORE, 135
; vga_pong.asm's game state, at its addresses
.equ BX,     140             ; ball column 0..63
.equ BY,     141             ; ball row, doubled
.equ DX,     142
.equ DY,     143
.equ PL,     144             ; paddle top rows, doubled
.equ PR,     145
.equ SL,     146             ; scores
.equ SR,     147
.equ FRAME,  148             ; steps of this game
.equ RNG,    150
.equ K_DXS,  152
.equ K_M2,   154
.equ BTN,    155
.equ AIM,    157
.equ FLIP,   159
.equ K_TAPS, 161
.equ AIMS,   162
.equ OBX,    170             ; the ball's column before the step
.equ SCF,    171             ; a point was scored (nonzero)
.equ PHASE,  172
.equ SK,     175             ; strip: the cell column
.equ SLO,    176             ; strip: court rows 0..31
.equ SHI,    177             ; strip: rows 32..47
.equ SBF,    178             ; strip: the ball's turn
.equ S_SL,   179             ; the scores as one-character strings
.equ S_SR,   180
.equ GDATA,  189             ; 4 strips' data, two words each: slot TQT & 3
.equ K_STRIP, 249
.equ K_NETL, 250
.equ K_NETH, 251
.equ ESC,    1               ; bank B pin 0 = ui_in[3]: back to the title

.equ TXT,     3 << 28        ; TEXT (oled_text.asm): | p << 25 | s << 22 | x << 15 | a
.equ C_SL,    TXT | (0 << 25) | (2 << 22) | (40 << 15) | S_SL
.equ C_SR,    TXT | (0 << 25) | (2 << 22) | (80 << 15) | S_SR

.tape MASK, 1<<0, 1<<1, 1<<2, 1<<3, 1<<4, 1<<5, 1<<6, 1<<7, 1<<8, 1<<9, 1<<10, 1<<11
.tape MASK + 12, 1<<12, 1<<13, 1<<14, 1<<15, 1<<16, 1<<17, 1<<18, 1<<19, 1<<20, 1<<21
.tape MASK + 22, 1<<22, 1<<23, 1<<24, 1<<25, 1<<26, 1<<27, 1<<28, 1<<29, 1<<30, 1<<31
.tape L_SCORE, C_SL, C_SR, 0
.tape RNG, 0xACE1
.tape K_DXS, 1, 0xFFFFFFFF, 0xFFFFFFFE
.tape FLIP, 0, 63, 0xB400
.tape AIMS, 100, 102, 104, 106, 108, 110, 102, 108
.tape K_STRIP, (2 << 28) | (2 << 25) | (7 << 22) | (1 << 8)
.tape K_NETL, 0xCCCCCCCD, 0xCCCC      ; the net (rows 2, 3 of every 4) and both borders

.bank  1
.entry 1, game

game:
    MOVI  r3, 0              ; r3 = 0: the base of every fixed tape address
turn:
    LDT   r0, r3, PHASE
    TSTI  r0, 1
    JZ    turn               ; the menu's turn
    RDTIME r0
    SETT  r0                 ; the first step is due now
play:
    CALL  tick
    LDT   r0, r3, BX
    STT   r0, r3, OBX
; ---------------------------------------------------------------- vga_pong.asm's step
    LDT   r0, r3, FRAME
    ADDI  r0, 1
    STT   r0, r3, FRAME
    TSTI  r0, 1
    JZ    ball                    ; even steps move the ball, odd ones the paddles

; paddles, left then right (r1 = side), one row per move: the buttons, or else the chip,
; which watches only its own half and steers the row it has picked to meet the ball
    GETP  r2
    TSTI  r2, ESC
    JNZ   over                    ; Esc: the game is over, with no winner
    STT   r2, r3, BTN
    SHR   r2, 2
    STT   r2, r3, BTN + 1         ; the right side's buttons, in the left side's bits
    MOVI  r1, 0
p_side:
    LDT   r2, r1, BTN
    LDT   r0, r1, PL
    TSTI  r2, 6                   ; up | down (bank B pins 1, 2)
    JZ    p_auto
    TSTI  r2, 2                   ; up
    JNZ   p_up
    JMP   p_dn
p_auto:
    LDT   r2, r3, BX
    LDT   r0, r1, FLIP
    XOR   r2, r0                  ; the ball's column counted from this side
    LDT   r0, r1, PL
    TSTI  r2, 32
    JNZ   p_done                  ; in the other half
    LDT   r2, r3, BY
    SUB   r2, r0
    ADDI  r2, 100                 ; 100 + ball - paddle top
    LDT   r0, r1, AIM             ; 100 + the paddle row picked to meet it
    CMP   r2, r0
    LDT   r0, r1, PL
    JZ    p_done
    JNC   p_dn
p_up:
    CMPI  r0, 2
    JZ    p_done
    MOVI  r2, 2
    SUB   r0, r2
    JMP   p_done
p_dn:
    CMPI  r0, 82
    JZ    p_done
    ADDI  r0, 2
p_done:
    STT   r0, r1, PL
    ADDI  r1, 1
    CMPI  r1, 2
    JNZ   p_side
    MOVI  r0, 2                   ; both paddles' columns
    CALL  strip
    MOVI  r0, 61
    CALL  strip
    JMP   play

; the ball: bounce off the borders, then off a paddle, or score
ball:
    LDT   r0, r3, RNG             ; 16-bit Galois LFSR, one step per ball move
    SHR   r0, 1
    JNC   b_move
    LDT   r2, r3, K_TAPS
    XOR   r0, r2
b_move:
    STT   r0, r3, RNG
    LDT   r0, r3, BY
    LDT   r1, r3, DY
    ADD   r0, r1
    CMPI  r0, 2
    JC    flip_y                  ; above row 1 (or wrapped)
    CMPI  r0, 93
    JC    y_ok                    ; at or above row 46
flip_y:
    MOVI  r2, 0
    SUB   r2, r1
    STT   r2, r3, DY
    ADD   r0, r2
    ADD   r0, r2
y_ok:
    STT   r0, r3, BY
    LDT   r0, r3, BX
    LDT   r1, r3, DX
    ADD   r0, r1
    STT   r0, r3, BX
    MOVI  r1, 0                   ; side: left
    CMPI  r0, 3
    JZ    hit
    CMPI  r0, 63
    JZ    point
    MOVI  r1, 1                   ; right
    CMPI  r0, 60
    JZ    hit
    CMPI  r0, 0
    JNZ   draw
point:                            ; r1 scores and the ball is served towards the other
    LDT   r0, r1, SL
    ADDI  r0, 1
    STT   r0, r1, SL
    LDT   r2, r1, K_DXS
    STT   r2, r3, DX
    STT   r2, r3, SCF             ; (K_DXS is never 0)
    CMPI  r0, 10
    JZ    over                    ; 10 points: the game is over (vga_pong starts another)
    MOVI  r0, 32
    STT   r0, r3, BX
    LDT   r0, r3, RNG
    SHL   r0, 27
    SHR   r0, 26                  ; a random row: 2 * (rng mod 32)
    ADDI  r0, 16
    STT   r0, r3, BY
    JMP   aim
hit:                              ; r1's paddle: ball row - paddle top 0..10 is a hit
    LDT   r2, r1, PL
    LDT   r0, r3, BY
    SUB   r0, r2
    CMPI  r0, 11
    JNC   draw
    LDT   r2, r1, K_DXS           ; back the other way
    STT   r2, r3, DX
    CMPI  r0, 4
    JC    go_up                   ; the top two rows send it up
    CMPI  r0, 8
    JC    aim                     ; the middle two keep its angle
    MOVI  r2, 2                   ; the bottom two send it down
    JMP   set_dy
go_up:
    LDT   r2, r3, K_M2
set_dy:
    STT   r2, r3, DY
aim:                              ; the side the ball now heads for picks a paddle row
    LDT   r0, r3, RNG
    SHL   r0, 29
    SHR   r0, 29
    LDT   r2, r0, AIMS
    MOVI  r0, 1
    XOR   r0, r1
    STT   r2, r0, AIM
; ---------------------------------------------------------------- the ball's columns, the score
draw:
    LDT   r0, r3, OBX
    CALL  strip
    LDT   r0, r3, BX
    CALL  strip
    LDT   r0, r3, SCF
    TSTI  r0, 0xFFFF
    JZ    play
    STT   r3, r3, SCF
    MOVI  r1, 0xFF00              ; the scores as strings
    LDT   r0, r3, SL
    OR    r0, r1
    STT   r0, r3, S_SL
    LDT   r0, r3, SR
    OR    r0, r1
    STT   r0, r3, S_SR
    MOVI  r2, L_SCORE
    LDT   r0, r2, 0               ; queue the two TEXTs
    LDT   r1, r3, TQT
    STT   r0, r1, TQ
    ADDI  r1, 1
    MOVI  r0, 15
    AND   r1, r0
    LDT   r0, r2, 1
    STT   r0, r1, TQ
    ADDI  r1, 1
    MOVI  r0, 15
    AND   r1, r0
    STT   r1, r3, TQT
    JMP   play
over:
    STT   r3, r3, SCF
    STT   r3, r3, PHASE           ; the menu's turn
    JMP   turn

; tick: wait for the next step (TICK after the last one's deadline) and until everything
; queued is drawn: TQ passed on, then DQ drawn. Uses r0, r1. (As oled_menu.asm's.)
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

; strip: queue cell column r0 (pixel columns 2 r0, 2 r0 + 1, pages 2..7), drawn whole from
; the state: both borders, the net, a paddle, the ball. Uses r0, r1, r2, lc.
strip:
    STT   r0, r3, SK
    MOVI  r1, 1
    STT   r1, r3, SLO             ; row 0: the top border
    MOVI  r1, 0x8000
    STT   r1, r3, SHI             ; row 47: the bottom border
    STT   r3, r3, SBF             ; the paddle's turn first
    CMPI  r0, 32
    JNZ   sp_pad
    LDT   r1, r3, K_NETL
    STT   r1, r3, SLO
    LDT   r1, r3, K_NETH
    STT   r1, r3, SHI
sp_pad:
    CMPI  r0, 2
    JZ    sp_l
    CMPI  r0, 61
    JNZ   sp_ball
    LDT   r2, r3, PR
    JMP   sp_p
sp_l:
    LDT   r2, r3, PL
sp_p:
    SHR   r2, 1                   ; the top row
    LDLC  6
    JMP   sp_set
sp_ball:
    MOVI  r1, 1
    STT   r1, r3, SBF
    LDT   r0, r3, SK
    LDT   r1, r3, BX
    CMP   r0, r1
    JNZ   sp_push
    LDT   r2, r3, BY
    SHR   r2, 1
    LDLC  1
sp_set:                           ; rows r2 .. r2 + lc - 1
    CMPI  r2, 32
    JNC   sp_hi
    LDT   r0, r2, MASK
    LDT   r1, r3, SLO
    OR    r0, r1
    STT   r0, r3, SLO
    JMP   sp_nx
sp_hi:
    LDT   r0, r2, MASK - 32
    LDT   r1, r3, SHI
    OR    r0, r1
    STT   r0, r3, SHI
sp_nx:
    ADDI  r2, 1
    DJNZ  sp_set
    LDT   r0, r3, SBF
    TSTI  r0, 1
    JZ    sp_ball
sp_push:
    LDT   r2, r3, TQT
    MOV   r1, r2
    MOVI  r0, 3
    AND   r1, r0
    SHL   r1, 1                   ; the data slot
    LDT   r0, r3, SLO
    STT   r0, r1, GDATA
    LDT   r0, r3, SHI
    STT   r0, r1, GDATA + 1
    ADDI  r1, GDATA
    LDT   r0, r3, SK
    SHL   r0, 16                  ; x0 = 2k
    OR    r1, r0
    LDT   r0, r3, SK
    SHL   r0, 9                   ; x1 = 2k + 1 (the 1 is in K_STRIP)
    OR    r1, r0
    LDT   r0, r3, K_STRIP
    OR    r1, r0
    STT   r1, r2, TQ
    ADDI  r2, 1
    MOVI  r0, 15
    AND   r2, r0
    STT   r2, r3, TQT
    RET
