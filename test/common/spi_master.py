"""cocotb SPI mode-0 master for the Escapement host port (docs/12-HOST-PROTOCOL.md).

Timing is expressed in core clock periods and can be randomised within the protocol's
limits (SCK high/low >= 4 clocks, CS setup/hold/gap >= 4 clocks) to exercise the
oversampling synchronisers at their edge, not just at a comfortable rate.
"""

import random

from cocotb.triggers import ClockCycles, FallingEdge


class SpiMaster:
    def __init__(self, dut, clk, cs_n, sck, mosi, miso, half=8, jitter=0, seed=1):
        self.dut, self.clk = dut, clk
        self.cs_n, self.sck, self.mosi, self.miso = cs_n, sck, mosi, miso
        self.half, self.jitter = half, jitter
        self.rng = random.Random(seed)
        cs_n.value = 1
        sck.value = 0
        mosi.value = 0

    def _h(self):
        return max(4, self.half + (self.rng.randint(-self.jitter, self.jitter) if self.jitter else 0))

    async def _wait(self, n):
        await ClockCycles(self.clk, n, rising=False)

    async def xfer(self, data, abort_after_bits=None):
        """One CS_n-low frame. Returns MISO bytes (sampled at each SCK rise)."""
        await FallingEdge(self.clk)
        self.cs_n.value = 0
        await self._wait(self._h())
        out, bits = bytearray(), 0
        for byte in data:
            rx = 0
            for i in range(7, -1, -1):
                self.mosi.value = (byte >> i) & 1
                await self._wait(self._h())          # SCK low phase
                rx = (rx << 1) | int(self.miso.value)  # host samples at the rise
                self.sck.value = 1
                await self._wait(self._h())          # SCK high phase
                self.sck.value = 0
                bits += 1
                if abort_after_bits is not None and bits >= abort_after_bits:
                    break
            out.append(rx)
            if abort_after_bits is not None and bits >= abort_after_bits:
                break
        await self._wait(self._h())
        self.cs_n.value = 1
        await self._wait(self._h())
        return bytes(out)
