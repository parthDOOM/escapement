#!/bin/bash
# sigrok-cli's ols driver against the FPGA bench's SUMP bridge, through a socat pty and a
# Windows byte relay (WSL cannot open the Pico's COM port). A pty ignores the baud rate, so
# serialcomm only sets libsigrok's per-byte timeouts: 300 baud gives the relay's round trip
# time. socat ends when sigrok-cli closes the pty, so each attempt starts a new one.
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
OUT=${1:-$HERE/out}   # olsrelay_loop.py watches $OUT/done: pass the same folder to both
mkdir -p $OUT; rm -f $OUT/*.log $OUT/done $OUT/meta.txt $OUT/version.txt $OUT/cap.*
cc -shared -fPIC -o /tmp/pty_serial_shim.so "$REPO/sw/tests/pty_serial_shim.c" -ldl || { echo "shim build failed"; exit 1; }
sigrok-cli --version | head -1 > $OUT/version.txt
ok=0
for a in 1 2 3 4 5 6; do
  socat -d -d pty,raw,echo=0 tcp-listen:9555,reuseaddr > $OUT/socat$a.log 2>&1 &
  SP=$!
  for i in $(seq 1 60); do
    ss -tn state established '( sport = :9555 )' | grep -q 9555 && break
    sleep 0.5
  done
  sleep 1
  PTS=$(grep -o "PTY is /dev/pts/[0-9]*" $OUT/socat$a.log | awk '{print $3}')
  LD_PRELOAD=/tmp/pty_serial_shim.so timeout 180 sigrok-cli -d "ols:conn=$PTS:serialcomm=300/8n1" \
      --config samplerate=1m --samples 2000 -t 3=1 -o $OUT/cap.sr > $OUT/cap$a.log 2>&1
  rc=$?
  echo "attempt $a pty $PTS rc=$rc: $(tr '\n' ' ' < $OUT/cap$a.log)"
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
  if [ $rc -eq 0 ] && [ -s $OUT/cap.sr ]; then ok=1; break; fi
  sleep 2
done
touch $OUT/done
[ $ok = 1 ] || exit 1
sigrok-cli -i $OUT/cap.sr --show > $OUT/meta.txt 2>&1
sigrok-cli -i $OUT/cap.sr -O binary -o $OUT/cap.bin && sigrok-cli -i $OUT/cap.sr -O vcd -o $OUT/cap.vcd && echo converted
ls -la $OUT/cap.sr $OUT/cap.bin
