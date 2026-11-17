// The wiringPi calls rc-switch's -DRPI (Raspberry Pi) build makes, for rcswitch_rx.cpp: time
// comes from the harness, wiringPiISR keeps the interrupt handler that enableReceive() attaches
// (the harness calls it once per edge), and the pin calls do nothing.
#pragma once
#define INT_EDGE_BOTH 3
#define OUTPUT 1
#define HIGH 1
#define LOW 0
unsigned int micros(void);
extern void (*rc_isr)(void);
static inline int wiringPiISR(int, int, void (*f)(void)) { rc_isr = f; return 0; }
static inline void pinMode(int, int) {}
static inline void digitalWrite(int, int) {}
static inline void delayMicroseconds(unsigned int) {}
