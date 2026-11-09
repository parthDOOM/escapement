# Recordings from sigrok-dumps

Logic analyser recordings of real transmitters, from
[sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps), whose README says: "Unless
noted otherwise, all dump files are hereby released into the public domain by their
authors." test_esclearn.py learns each one, decodes it and checks the decode against
sigrok's own decoder where sigrok-cli has one that reads the file.

| File | sigrok-dumps path | What it is |
| --- | --- | --- |
| nec_enter.sr | ir/nec/joy-it_sbc-irc01/joy_it_sbc_irc01_enter_no_repeat.sr | NEC remote, one button, 5 presses; probe 0 demodulated, probe 1 raw (38 kHz carrier) |
| nec_all.sr | ir/nec/joy-it_sbc-irc01/joy_it_sbc_irc01_all.sr | the same remote, every button, with repeat codes |
| nec_extended.sr | ir/nec/extended/unknown_ceiling_light.sr | extended NEC (16-bit address), a ceiling light remote |
| nec_matsui.sr | ir/nec/hama_8in1/tv_matsui_0001/tv_matsui_0001_button_power_hold_3x.sr | Matsui TV standby, held 3 times; probe 6 demodulated, probe 7 raw |
| rc5_button1.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button1_hold.sr | Philips RC-5 (VCR, button 1 held); probe 0 raw carrier, probe 1 demodulated |
| rc5_button2.sr, rc5_standby.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button2_hold.sr, ..._button_standby_hold.sr | the same remote, button 2 and standby |
| rc5_bogus.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button1_hold_one_bogus_packet.sr | button 1 with one bad packet on the receiver's probe (probes swapped: 0 demodulated, 1 raw) |
| sirc1.sr, sirc2.sr | ir/sirc/sirc-1.sr, ir/sirc/sirc-2.sr | Sony SIRC, 20-bit frames |
| pt2262_three_a.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_three_a.sr | PT2262 433 MHz socket remote via an RR3 receiver, button A three times |
| pt2262_abcd.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_a_b_c_d_on.sr | the same, buttons A to D |

The NEC remote is outside the 5% timing tolerance of sigrok's ir_nec decoder (its
README says so), and sigrok-cli 0.7.2 misreads it; the test checks NEC's own redundancy
instead (address 0x00 then 0xFF, every command byte then its complement).

## Real devices against the bus models (test_real_devices.py)

| File | sigrok-dumps path | What it is |
| --- | --- | --- |
| qi_nexus5.sr | qi/gpmq8005a-nexus5/gpmq8005a-nexus5.sr | Qi: a Nexus 5 on a GPMQ8005A transmitter, demodulated data, 1 MHz |
| qi_noname_samsung5.sr | qi/noname-samsung5/noname-samsung5.sr | Qi: a no-name receiver for a Samsung phone, 250 kHz (it keys its bits at 545 us, outside the specification) |
| pd_apple_av_hdmi.sr, pd_apple_power_brick.sr | usb_power_delivery/macbook_2015/ | USB PD: a 2015 MacBook with Apple's AV adapter and power brick (Twinkie, CC1/CC2) |
| pd_hdmi_dongle.sr, pd_power_supply_20V.sr | usb_power_delivery/pixel_2015/ | USB PD: a 2015 Pixel with an HDMI dongle and a 20 V supply |
| pd_thinkpad_*.sr | usb_power_delivery/thinkpad_yoga_370/ | USB PD: a ThinkPad Yoga 370 with an Anker power bank (the plug turned during the capture), an Aukey 45 W supply and a pass-through dongle |
| pd_zy12pds_*.sr | usb_power_delivery/zy12pds_sink_module/ | USB PD: a ZY12PDS trigger module with a no-name 65 W supply and an Anker power bank |
| wiegand36_8a640910.sr | wiegand/36bit/wiegand-8a640910.sr | Wiegand: a 13.56 MHz reader's D0/D1, 20 kHz (its notes say 36-bit; the frames are 34-bit) |
| em4100_*_card.sr, _keyfob.sr, _glass.sr | rfid/em4100/rdm630/ | EM4100: ten tags read by an RDM630 module: its RFID pin and its 9600 8N1 output |
| em4100_0200f5ed8d_coil.sr | rfid/em4100/rfid_copier/em4100_0200f5ed8d.sr | EM4100: one tag on a reader coil |
| dcf77_pollin_120s.sr, dcf77_pollin_1800s.sr | dcf77/pollin_dcf1_module/dcf77_120s.sr, dcf77_1800s.sr | DCF77: a Pollin DCF1 receiver module off the air, 2 and 30 minutes (January 2012) |
| stepper_smoothieware.sr | stepper_motor/smoothieware/smoothieware.sr | Step/direction: Smoothieware running test.gcode (in that directory), X and Y, 12 MHz |
| stepper_grbl_3.sr | stepper_motor/grbl_cnc/grbl_cnc_3.sr | Step: GRBL on a CNC's Y axis, 2 MHz |
| cec_sony_denon_off.sr, cec_sony_yamaha_arc.sr | cec/tv_sony_amp_denon_switch_off_seq.sr, cec/tv_sony_amp_yamaha_arc_handshake.sr | HDMI-CEC: a Sony TV with a Denon amplifier (TV switched off) and a Yamaha amplifier on ARC, 1 MHz |
| dcc_easycontrol_*.sr | dcc/easycontrol/decoder_*.sr | NMRA DCC: a tams EasyControl command station through a B-2 booster (loco 2's light; accessory decoders 120/121, 133, 140, 310), 1 MHz |
| dali_rako_query_ballast.sr | dali/rako_rsrdali/rako_rsrdali_query_ballast.sr | DALI: a Rako RSR-DALI controller querying a ballast, with its answers, 100 kHz |
| sent_motor_position_3us.sr | sent/SENT2010_03p0us_6dn_pp_nsp_A6.sr | SENT: a motor position sensor (J2716 2010, 3 us ticks, six data nibbles, pause pulse), 100 MHz |
| uart_stm32_8n1_*.sr | uart/hello_world/8n1/hello_world_8n1_*.sr | UART: an Olimex STM32-H103 sending "Hello World!\r\n" at 1200 to 921600 baud, 8N1, 625 kHz to 5 MHz |
| uart_gps_mtk3339_9600.sr | uart/gps/mtk3339/mtk3339_8n1_9600.sr | UART: an MTK3339 GPS module (Adafruit breakout) sending NMEA at 9600 8N1, 200 kHz |
| modbus_brainchild_io16do.sr | uart/modbus_rtu/brainchild_io_16do/brainchild-io-16do.sr | Modbus RTU: a PC (minimalmodbus) polling a Brainchild IO-16DO module, 19200 8E1, functions 1-6, 15, 16, on the RS-232 side (inverted levels), 1 MHz |
| i2c_24lc02b_fx2_hantek6022be.sr | i2c/eeprom_24xx/microchip_24lc02b/hantek_6022be_powerup.sr | I2C: the Cypress FX2 in a Hantek 6022BE reading its 24LC02B boot EEPROM at power-up, 8 MHz |
| i2c_x24c02_dual_tds744a.sr | i2c/eeprom_24xx/xicor_x24c02/x24c02_dual.sr | I2C: a Tektronix TDS 744A's internal bus, two X24C02 EEPROMs (0x50, 0x51) and probes of 0x52, 2 MHz |
| can_mcp2515_125k_*.sr | can/microchip_mcp2515dm-bm/mcp2515dm-bm-125kbits_*.sr | CAN: a Microchip MCP2515DM-BM demo board at 125 kbit/s, its MCP2515's CAN_RX pin (one standard, one extended message; 25% and 100% bus load), 4 MHz |
| onewire_2xds18b20_stm32.sr | onewire/ds18b20/2xds18b20.sr | 1-Wire: a custom STM32F103 master with two DS18B20s (Search, Match, Read/Write/Copy Scratchpad, Convert T), 1 MHz |
| sd_spi_cmd17_sigrok_rocks.sr | sdcard/spi_mode/cmds/cmd17.sr | SD card in SPI mode: CMD17 reading a block that starts "Sigrok rocks", 24 MHz |
| sd_spi_xmore_512mb_read_3blocks.sr | sdcard/spi_mode/xmore_512mb/xmore_512mb_read_3blocks_A.sr | SD card in SPI mode: an xmore 512 MB card, initialisation, CSD and three blocks, 4 MHz |
| max7219_arduino_4x_cascaded.sr | spi/max7219/max7219_4x_cascaded_chips.sr | MAX7219: an Arduino UNO driving four cascaded chips with Sebski123/MAX72xx-Sigrok's test sketch (three intentional errors), 2 MHz |
