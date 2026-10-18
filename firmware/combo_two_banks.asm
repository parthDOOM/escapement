; Concurrent image on both pin banks (v1.2): eleven protocol signals at once, more than
; the eight bidirectional pins could carry. Four unchanged firmware modules, linked with
; .include; the two bank B contexts name their pins 0..4, which are B0..B4.
;
;   ctx 0  ps2_device   bank A  CLK uio0, DATA uio1 (open-drain); keyboard side
;   ctx 1  jtag_idcode  bank A  TCK uio4, TMS uio5, TDI uio6, TDO uio7
;   ctx 2  spi_ctrl     bank B  SCK B0, MOSI B1, MISO B2, CS B3 (uo_out[3], [4], ui_in[5],
;                               uo_out[6]); host-driven, any SPI mode
;   ctx 3  uart_tx      bank B  TX B4 (uo_out[7]), 115200; sends "UK!" + newline, then halts
;
; Bank B pins are push-pull, one direction each, so the open-drain buses (PS/2) and the
; bidirectional ones stay on bank A, and the clocked push-pull buses move to bank B.

.bank 2
.bank 3

.equ ps2_device.CTX, 0
.equ ps2_device.CLK_PIN, 0
.equ ps2_device.DATA_PIN, 1
.equ jtag_idcode.CTX, 1
.equ spi_ctrl.CTX, 2
.equ spi_ctrl.SCK_PIN, 0
.equ spi_ctrl.MOSI_PIN, 1
.equ spi_ctrl.MISO_PIN, 2
.equ spi_ctrl.CS_PIN, 3
.equ uart_tx.CTX, 3
.equ uart_tx.PIN, 4

.include "ps2_device.asm"
.include "jtag_idcode.asm"
.include "spi_ctrl.asm"
.include "uart_tx.asm"
