; Concurrent image: UART TX + UART RX + I2C controller on one chip, one program load.
; Three independent firmware modules, each on its own context and pins, linked with
; .include. Every module keeps its own deadline timeline; the fixed 4-slot barrel
; means one context's work can never shift another's timing.
;
;   ctx 0  i2c_ctrl  SCL pin 2, SDA pin 3 (open-drain), Fast-mode, host-driven
;   ctx 1  uart_rx   RX pin 1, 115200, pushes received bytes to the host
;   ctx 2  uart_tx   TX pin 0, 115200, sends "UK!" + newline, then halts

.equ uart_tx.CTX, 2
.equ uart_tx.PIN, 0

.include "uart_tx.asm"
.include "uart_rx.asm"
.include "i2c_ctrl.asm"
