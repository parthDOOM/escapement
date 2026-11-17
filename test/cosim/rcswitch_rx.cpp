// rc-switch's receiver (github.com/sui77/rc-switch, RCSwitch.cpp, built by outside_decoders.sh)
// off its microcontroller: each line on stdin is the time of one edge in microseconds, and
// is played through the library's own interrupt handler (RCSwitch::handleInterrupt, as
// enableReceive() attaches it), with micros() returning that time. Every code the library decodes is printed as
//     value=<decimal> bits=<n> protocol=<n> delay=<us>
// RCSwitch.cpp is compiled with -DRPI, its Raspberry Pi build; the wiringPi calls it
// makes are the stubs below.
#include <cstdio>
#include "RCSwitch.h"

unsigned long now_us = 0;
unsigned int micros(void) { return now_us; }
void (*rc_isr)(void) = nullptr;

int main() {
  RCSwitch rx;
  rx.enableReceive(0);
  unsigned long t;
  while (std::scanf("%lu", &t) == 1) {
    now_us = t;
    rc_isr();
    if (rx.available()) {
      std::printf("value=%lu bits=%u protocol=%u delay=%u\n", rx.getReceivedValue(),
                  rx.getReceivedBitlength(), rx.getReceivedProtocol(), rx.getReceivedDelay());
      rx.resetAvailable();
    }
  }
  return 0;
}
