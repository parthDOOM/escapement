"""Unit tests for esc_core against docs/11-SEMANTICS.md (v1.0.1).

Every expected value below is derived by hand from the spec; the derivation is in
the comments next to it.  Conventions used throughout:

* The testbench samples and drives at the FALLING edge of each clock.  Sample
  index t == run cycle t (state "during cycle t", i.e. after edge E_(t-1)).  An
  input set at the falling edge in cycle t is what the core samples at E_t, i.e.
  "uio_in during cycle t".  So sync during t == ext set in cycle t-2 (sec. 5).
* Context k fetches in cycles t = k (mod 4) and commits at t+3 (sec. 3); a
  straight-line program of context 0 commits its i-th instruction (i from 0,
  no waits) at tick 4i+3, context k at 4i+3+k.
* A commit at tick c is reported on dbg_commit_* during cycle c+1 (sec. 10).
"""

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sw"))
from escasm import assemble  # noqa: E402

C, W, F = 0, 1, 2
IDLE, RUN, HALTED, FAULTED = 0, 1, 2, 3
ILLEGAL, PC_RANGE, ADMIT, LATE_HEAD = 1, 2, 3, 4
MASK, OWNER, PAST, ORDER, OVERLAP = 1, 2, 3, 4, 5


def ff_word(cause, sub, ctx, pc):
    """dbg_sel 14 layout: [31] valid, [27:24] cause, [23:20] subcode, [17:16] ctx, [7:0] PC."""
    return (1 << 31) | (cause << 24) | (sub << 20) | (ctx << 16) | pc


def iv(sig):
    try:
        return int(sig.value)
    except ValueError:
        return None



def ctx_reg(core, ctx, sel):
    """dbg_sel 0-8 of context ctx computed from its register outputs (sec. 10 map)."""
    c = core.g_ctx[ctx].u_ctx

    def v(name):
        return int(getattr(c, name).value)
    if sel < 4:
        return v(f"o_r{sel}")
    if sel == 7:
        return (v("o_lr") << 16) | (v("o_pc") << 8) | (v("o_state") << 2) | (v("o_z") << 1) | v("o_c")
    return v({4: "o_sh", 5: "o_lc", 6: "o_tmr", 8: "o_ish"}[sel])

class Env:
    def __init__(self, dut):
        self.dut = dut
        self.img = None
        self.trace = []

    async def reset(self):
        d = self.dut
        Clock(d.clk, 40, unit="ns").start()
        # a real falling edge: the behavioural register models clear on negedge rst_n, and a
        # clock-gated bank sees no clock edge while its enable is low (silicon flops reset on
        # the level; tb_core starts rst_n at 0, which is no edge)
        d.rst_n.value = 1
        await Timer(1, unit="ns")
        d.rst_n.value = 0
        for name in ("start", "stop", "ld_en", "ld_we", "lb"):
            getattr(d, name).value = 0
        for name in ("ext", "mbx_rx_push", "mbx_tx_pop", "irq_clear", "mbx_rx_data",
                     "dbg_sel", "dbg_sel_ctx", "ctx_enable", "entry_pcs", "pin_owned",
                     "pin_owner", "pin_od", "ctx_bank", "ib_in", "ld_addr", "ld_data"):
            getattr(d, name).value = 0
        for _ in range(3):
            await FallingEdge(d.clk)
        d.rst_n.value = 1
        await FallingEdge(d.clk)

    async def load_and_start(self, src, ext=0, lb=0):
        """Stop, write all 256 words, apply configuration, pulse start.
        Returns at the falling edge inside run cycle 0."""
        d = self.dut
        img = assemble(src)
        self.img = img
        d.stop.value = 1
        await FallingEdge(d.clk)
        d.stop.value = 0
        d.ld_en.value = 1
        for a, w in enumerate(img.words):
            d.ld_addr.value = a
            d.ld_data.value = w
            d.ld_we.value = 1
            await FallingEdge(d.clk)
        d.ld_we.value = 0
        d.ld_en.value = 0
        d.ctx_enable.value = img.ctx_enable_mask
        d.entry_pcs.value = img.entry_pcs_packed
        d.pin_owned.value = img.pin_owned_mask
        d.pin_owner.value = img.pin_owner_packed
        d.pin_od.value = img.pin_od_mask
        d.ctx_bank.value = img.ctx_bank_mask
        d.ext.value = ext
        d.lb.value = lb
        await FallingEdge(d.clk)
        d.start.value = 1          # start high during cycle s; init at E_s
        await FallingEdge(d.clk)
        d.start.value = 0          # now in run cycle 0
        self.trace = []

    async def rd(self, sel, ctx=0):
        """dbg_sel `sel` of context `ctx` now. Selects 0-8 are the context's registers, read
        from the context itself: the debug port shows them as of the context's last commit
        slot (D-41; test_debug_reads_commit_slot checks the port)."""
        d = self.dut
        d.dbg_sel.value = sel
        d.dbg_sel_ctx.value = ctx
        await Timer(1, unit="ns")
        if sel <= 8:
            return ctx_reg(d.u_core, ctx, sel)
        return int(d.dbg_rdata.value)

    async def run(self, n, stim=None, sels=(), full=True):
        """Run n cycles from the current cycle.  stim(t) drives inputs for cycle t;
        sels is a list of (sel, ctx) sampled every cycle into rec['sel'][(sel, ctx)]."""
        d = self.dut
        t0 = len(self.trace)
        for i in range(n):
            t = t0 + i
            if stim:
                stim(t)
            rec = {"t": t, "out": iv(d.uio_out), "oe": iv(d.uio_oe),
                   "cv": iv(d.dbg_commit_valid)}
            if full:
                rec.update(running=iv(d.running), state=iv(d.ctx_state),
                           irqp=iv(d.irq_pending), fv=iv(d.fault_valid),
                           imem=iv(d.imem_addr), rxf=iv(d.mbx_rx_full),
                           txe=iv(d.mbx_tx_empty), txd=iv(d.mbx_tx_data))
            if rec["cv"]:
                rec["commit"] = dict(
                    c=t - 1, ctx=iv(d.dbg_commit_ctx), pc=iv(d.dbg_commit_pc),
                    insn=iv(d.dbg_commit_insn), oc=iv(d.dbg_commit_outcome),
                    r0=iv(d.dbg_post_r0), r1=iv(d.dbg_post_r1), r2=iv(d.dbg_post_r2),
                    r3=iv(d.dbg_post_r3), sh=iv(d.dbg_post_sh), lc=iv(d.dbg_post_lc),
                    tmr=iv(d.dbg_post_tmr), z=iv(d.dbg_post_z), cf=iv(d.dbg_post_c),
                    npc=iv(d.dbg_post_pc))
            if sels:
                rec["sel"] = {}
                for s, k in sels:
                    rec["sel"][(s, k)] = await self.rd(s, k)
            self.trace.append(rec)
            await FallingEdge(d.clk)

    def commits(self):
        return [r["commit"] for r in self.trace if "commit" in r]

    def commit_summary(self):
        return [(c["c"], c["ctx"], c["pc"], c["oc"]) for c in self.commits()]

    def commit_at(self, tick):
        for c in self.commits():
            if c["c"] == tick:
                return c
        raise AssertionError(f"no commit at tick {tick}")


async def new_env(dut):
    env = Env(dut)
    await env.reset()
    return env


def seq_commits(ctx, pcs_ticks, oc=C):
    return [(t, ctx, pc, oc) for pc, t in pcs_ticks]


# --------------------------------------------------------------------------- sec. 11
@cocotb.test()
async def test_worked_example(dut):
    """Sec. 11 exactly, plus GETP probes in contexts 1 and 2 to see sync[0]."""
    env = await new_env(dut)
    src = """
    .entry 0, a
    .entry 1, b
    .entry 2, c
    .own 0, 0
    a: ADDT 10              ; pc0  fetch 0, commit E_3: tmr = 10
       EMIT 0x01,0x01,0x01  ; pc1  fetch 4, commit E_7: d = 10, lead 3
       HALT                 ; pc2  fetch 8, commit E_11 (FIFO empty after E_10)
    b: GETP r0              ; pc3  ctx1 commits at 4, 8, 12
       GETP r1              ; pc4
       GETP r2              ; pc5  tick 12: sync during 12
       HALT                 ; pc6
    c: GETP r0              ; pc7  ctx2 commits at 5, 9, 13
       GETP r1              ; pc8
       GETP r2              ; pc9  tick 13: sync during 13
       HALT                 ; pc10
    """
    # loopback uio_in = oe ? out : 0  (sec. 11 "uio_in[0] = output")
    await env.load_and_start(src, ext=0x00, lb=1)
    await env.run(20)
    tr = env.trace
    exp = [(3, 0, 0, C), (4, 1, 3, C), (5, 2, 7, C), (7, 0, 1, C), (8, 1, 4, C),
           (9, 2, 8, C), (11, 0, 2, C), (12, 1, 5, C), (13, 2, 9, C), (16, 1, 6, C),
           (17, 2, 10, C)]
    assert env.commit_summary() == exp, env.commit_summary()
    assert env.commit_at(3)["tmr"] == 10
    # output rises during cycle 11 (DRIVE retires at E_10)
    for t in range(20):
        want = 1 if t >= 11 else 0
        assert tr[t]["out"] & 1 == want and tr[t]["oe"] & 1 == want, (t, tr[t])
    # sync[0] = uio_in during t-2 = output during t-2 -> 1 from cycle 13
    assert env.commit_at(12)["r2"] == 0      # ctx1 GETP at tick 12: sync during 12 = 0
    assert env.commit_at(13)["r2"] == 1      # ctx2 GETP at tick 13: sync during 13 = 1
    assert env.commit_at(9)["r1"] == 0
    # min_lead = 3, wait_full = 0 (sel 9); max_occupancy 1, FIFO empty (sel 10)
    assert await env.rd(9, 0) == (3 << 16) | 0
    assert await env.rd(10, 0) == (1 << 8)
    # sel 7: lr 0, PC 2 (HALT keeps PC), HALTED, Z = C = 0
    assert await env.rd(7, 0) == (2 << 8) | (HALTED << 2)


# --------------------------------------------------------------------------- sec. 3
@cocotb.test()
async def test_four_context_slots(dut):
    """Fixed 4-slot barrel: ctx k commits at k+3 (mod 4); disabled ctx 2 leaves its slot empty."""
    env = await new_env(dut)
    src = """
    .entry 0, a
    .entry 1, b
    .entry 3, d
    a: NOP          ; 0
       NOP          ; 1
       HALT         ; 2
    b: NOP          ; 3
       HALT         ; 4
    d: MOVI r0, 7   ; 5
       NOP          ; 6
       HALT         ; 7
    """
    await env.load_and_start(src)
    await env.run(20)
    # ctx0: 3,7,11; ctx1: 4,8; ctx3: 6,10,14; ctx2 (disabled): never
    exp = sorted([(3, 0, 0, C), (7, 0, 1, C), (11, 0, 2, C), (4, 1, 3, C), (8, 1, 4, C),
                  (6, 3, 5, C), (10, 3, 6, C), (14, 3, 7, C)])
    assert env.commit_summary() == exp, env.commit_summary()
    tr = env.trace
    # imem_addr = PC of ctx (t mod 4) during cycle t: entry pcs 0, 3, (ctx2: 0), 5
    assert [tr[t]["imem"] for t in range(4)] == [0, 3, 0, 5]
    assert tr[4]["imem"] == 1 and tr[5]["imem"] == 4 and tr[7]["imem"] == 6
    assert env.commit_at(6)["r0"] == 7 and env.commit_at(6)["z"] == 0
    # ctx0 HALTED during 12 (HALT at E_11); final states 3:H 2:IDLE 1:H 0:H
    assert tr[11]["state"] & 3 == RUN and tr[12]["state"] & 3 == HALTED
    assert tr[19]["state"] == (HALTED << 6) | (IDLE << 4) | (HALTED << 2) | HALTED
    assert tr[19]["running"] == 1
    assert await env.rd(0, 3) == 7


# --------------------------------------------------------------------------- sec. 6.2
@cocotb.test()
async def test_admit_subcodes(dut):
    """Each ADMIT subcode, check order, and the boundary cases that are admitted."""
    env = await new_env(dut)
    cases = [
        # (name, src, fault tick, pc, sub)   tick = 4*pc + 3 for ctx 0
        ("mask-drive", "ADDT 10\nEMIT 0, 0, 0", 7, 1, MASK),
        ("mask-expect", "ADDT 10\nEXPECT 0, 0, HOLD, 0", 7, 1, MASK),
        ("owner-other", ".own 0, 1\nADDT 10\nEMIT 1, 1, 1", 7, 1, OWNER),
        ("owner-unowned", ".own 0, 0\nADDT 10\nEMIT 3, 3, 3", 7, 1, OWNER),
        # OWNER is checked before PAST: tmr = 0 is also in the past at tick 3
        ("owner-before-past", ".own 0, 1\nEMIT 1, 1, 1", 3, 0, OWNER),
        ("past-zero", ".own 0, 0\nEMIT 1, 1, 1", 3, 0, PAST),
        # d == c: ahead(7, 7) is false
        ("past-equal", ".own 0, 0\nADDT 7\nEMIT 1, 1, 1", 7, 1, PAST),
        # d = c + 2^31 (c = 15): not ahead
        ("past-half", ".own 0, 0\nMOVI r0, 15\nMOVHI r0, 0x8000\nSETT r0\nEMIT 1, 1, 1",
         15, 3, PAST),
        # same deadline as the tail (20 not yet retired at tick 11)
        ("order", ".own 0, 0\nADDT 20\nEMIT 1, 1, 1\nEMIT 1, 0, 1", 11, 2, ORDER),
        # PAST before ORDER: SETT r0 makes d = 0, which is both past and not after the tail
        ("past-before-order", ".own 0, 0\nADDT 20\nEMIT 1, 1, 1\nSETT r0\nEMIT 1, 0, 1",
         15, 3, PAST),
        # first EXPECT d=20 w=5 -> last_expect_end 25; second d=25: not ahead(25, 25)
        ("overlap", "ADDT 20\nEXPECT 1, 0, HOLD, 5\nADDT 5\nEXPECT 1, 0, HOLD, 0",
         15, 3, OVERLAP),
        # ORDER is checked before OVERLAP: second EXPECT d = 20 == tail
        ("order-before-overlap", "ADDT 20\nEXPECT 1, 0, HOLD, 5\nEXPECT 1, 0, HOLD, 0",
         11, 2, ORDER),
    ]
    for name, src, tick, pc, sub in cases:
        await env.load_and_start(".entry 0, s\ns:\n" + src)
        await env.run(tick + 6)
        cm = env.commit_at(tick)
        assert (cm["pc"], cm["oc"]) == (pc, F), (name, cm)
        assert await env.rd(14) == ff_word(ADMIT, sub, 0, pc), name
        assert await env.rd(15) == tick, name
        assert env.trace[tick + 1]["state"] & 3 == FAULTED, name
        assert env.trace[tick]["fv"] == 0 and env.trace[tick + 1]["fv"] == 1, name
        # no further commits from a faulted context
        assert all(c["c"] <= tick for c in env.commits()), name

    admitted = [
        # (name, src, expected min_lead)
        # d = c+1 (tick 7, d 8): lead 1
        ("lead-1", ".own 0, 0\nADDT 8\nEMIT 1, 1, 1\nHALT", 1),
        # d = c + 2^31 - 1 (c = 15, d = 0x8000000E): admitted, lead saturates to 0xFFFF
        ("half-minus-1", ".own 0, 0\nMOVI r0, 14\nMOVHI r0, 0x8000\nSETT r0\nEMIT 1, 1, 1\nHALT",
         0xFFFF),
        # second EXPECT starts at 26 = last end 25 + 1; leads 20-7 = 13, 26-15 = 11
        ("overlap-ok", "ADDT 20\nEXPECT 1, 0, HOLD, 5\nADDT 6\nEXPECT 1, 0, HOLD, 0\nHALT", 11),
        # SAMPLE needs no mask/ownership; EXPECT needs no ownership; leads 10-7, 20-15
        ("sample-expect-noown", "ADDT 10\nSAMPLE 0, LSB\nADDT 10\nEXPECT 0x80, 0x00, HOLD, 0\nHALT", 3),
    ]
    for name, src, minlead in admitted:
        await env.load_and_start(".entry 0, s\ns:\n" + src)
        await env.run(40)
        assert env.trace[-1]["fv"] == 0, name
        assert all(c["oc"] != F for c in env.commits()), name
        assert (await env.rd(9)) >> 16 == minlead, name
    # half-minus-1 leaves its entry queued -> HALT keeps waiting (sec. 7.2)
    await env.load_and_start(".entry 0, s\n.own 0, 0\ns:\nMOVI r0, 14\nMOVHI r0, 0x8000\n"
                             "SETT r0\nEMIT 1, 1, 1\nHALT")
    await env.run(40)
    assert env.commit_at(19)["oc"] == W and env.commit_at(39 - 4)["oc"] == W
    assert ((await env.rd(10)) >> 4) & 7 == 1


# --------------------------------------------------------------------------- sec. 6.2 step 1, 7.4
@cocotb.test()
async def test_fifo_full_wait(dut):
    env = await new_env(dut)
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 99          ; pc0 t3  tmr 99
       EMIT 1, 1, 1     ; pc1 t7  d 99  lead 92, |Q|+1 = 1
       ADDT 1           ; pc2 t11 tmr 100
       EMIT 1, 0, 1     ; pc3 t15 d 100 lead 85, occ 2
       ADDT 1           ; pc4 t19
       EMIT 1, 1, 1     ; pc5 t23 d 101 lead 78, occ 3
       ADDT 1           ; pc6 t27
       EMIT 1, 0, 1     ; pc7 t31 d 102 lead 71, occ 4
       ADDT 1           ; pc8 t35 tmr 103
       EMIT 1, 1, 1     ; pc9 t39..t95 WAIT (Q full: 15 times); t99: head 99 retires at
                        ;     E_99 so |Q| = 3 -> admitted, lead 4, occ 4
       HALT             ; pc10 t103: 100..102 retired, 103 retires at E_103 -> empty -> C
    """
    await env.load_and_start(src)
    await env.run(110, sels=[(10, 0)])
    exp = [(4 * pc + 3, 0, pc, C) for pc in range(9)]
    exp += [(t, 0, 9, W) for t in range(39, 96, 4)] + [(99, 0, 9, C), (103, 0, 10, C)]
    assert env.commit_summary() == exp, env.commit_summary()
    assert await env.rd(9) == (4 << 16) | 15        # min_lead 4, wait_full 15
    tr = env.trace
    assert tr[40]["sel"][(10, 0)] == (4 << 8) | (4 << 4)     # max_occ 4, count 4
    assert tr[99]["sel"][(10, 0)] == (4 << 8) | (4 << 4)
    assert tr[100]["sel"][(10, 0)] == (4 << 8) | (4 << 4)    # 99 retired, 103 appended
    assert tr[104]["sel"][(10, 0)] == (4 << 8)
    # outputs: value of the entry with deadline d appears during d+1
    for t in range(110):
        if t <= 99:
            want = (0, 0)
        else:
            want = ({100: 1, 101: 0, 102: 1, 103: 0}.get(t, 1), 1)
        assert (tr[t]["out"], tr[t]["oe"]) == want, (t, tr[t])


# --------------------------------------------------------------------------- sec. 7.3
@cocotb.test()
async def test_waitt_waitp(dut):
    env = await new_env(dut)
    # WAITT completes at the first slot c with reached(c, tmr)
    await env.load_and_start(".entry 0, s\ns: ADDT 11\nWAITT\nRDTIME r0\nHALT")
    await env.run(24)
    # tmr = 11 = a commit tick: W at 7, C at 11; RDTIME at 15
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, W), (11, 0, 1, C),
                                    (15, 0, 2, C), (19, 0, 3, C)]
    assert env.commit_at(15)["r0"] == 15
    await env.load_and_start(".entry 0, s\ns: ADDT 12\nWAITT\nRDTIME r0\nHALT")
    await env.run(26)
    # tmr = 12: not reached at 11 -> W; reached at 15
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, W), (11, 0, 1, W),
                                    (15, 0, 1, C), (19, 0, 2, C), (23, 0, 3, C)]
    assert env.commit_at(19)["r0"] == 19

    # WAITP match: ext[3] = 1 from cycle 21 -> sync[3] = 1 from cycle 23 (2 FF)
    def stim21(t):
        dut.ext.value = 0x08 if t >= 21 else 0

    src = ".entry 0, s\ns: ADDT 1000\nWAITP 0x08, 0x08\nRDTIME r1\nHALT"
    await env.load_and_start(src)
    await env.run(34, stim=stim21)
    s = env.commit_summary()
    assert s == [(3, 0, 0, C)] + [(t, 0, 1, W) for t in (7, 11, 15, 19)] + \
        [(23, 0, 1, C), (27, 0, 2, C), (31, 0, 3, C)], s
    assert env.commit_at(23)["z"] == 1 and env.commit_at(27)["r1"] == 27

    def stim22(t):
        dut.ext.value = 0x08 if t >= 22 else 0

    await env.load_and_start(src)
    await env.run(36, stim=stim22)
    # sync[3] from cycle 24: tick 23 still sees 0 -> completes at 27
    assert env.commit_at(23)["oc"] == W and env.commit_at(27)["oc"] == C
    assert env.commit_at(27)["z"] == 1 and env.commit_at(31)["r1"] == 31

    # WAITP timeout: Z = 0 when it completes because reached(c, tmr)
    await env.load_and_start(".entry 0, s\ns: ADDT 15\nMOVI r0, 0\nWAITP 0x08, 0x08\n"
                             "RDTIME r1\nHALT")
    await env.run(26)
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, C), (11, 0, 2, W),
                                    (15, 0, 2, C), (19, 0, 3, C), (23, 0, 4, C)]
    assert env.commit_at(7)["z"] == 1 and env.commit_at(15)["z"] == 0
    assert env.commit_at(19)["r1"] == 19


# --------------------------------------------------------------------------- sec. 7.1
@cocotb.test()
async def test_flags_shifts(dut):
    env = await new_env(dut)
    prog = [
        # (instruction, r0, r1, r2, r3, sh, Z, C)  -- state after the commit
        ("MOVI r0, 0xFFFF",   0x0000FFFF, 0, 0, 0, 0, 0, 0),
        ("MOVHI r0, 0xFFFF",  0xFFFFFFFF, 0, 0, 0, 0, 0, 0),
        ("MOVI r1, 1",        0xFFFFFFFF, 1, 0, 0, 0, 0, 0),
        ("ADD r0, r1",        0, 1, 0, 0, 0, 1, 1),            # carry out of bit 31
        ("SUB r0, r1",        0xFFFFFFFF, 1, 0, 0, 0, 0, 1),   # 0 < 1: borrow
        ("CMP r1, r1",        0xFFFFFFFF, 1, 0, 0, 0, 1, 0),
        ("CMPI r1, 2",        0xFFFFFFFF, 1, 0, 0, 0, 0, 1),   # 1 < 2: borrow
        ("TSTI r0, 0",        0xFFFFFFFF, 1, 0, 0, 0, 1, 1),   # (r0 & 0) == 0; C kept
        ("ADDI r1, 0xFFFF",   0xFFFFFFFF, 0x10000, 0, 0, 0, 0, 0),
        ("SHL r1, 16",        0xFFFFFFFF, 0, 0, 0, 0, 1, 1),   # C = r1[32-16] = r1[16]
        ("MOVI r2, 0x8001",   0xFFFFFFFF, 0, 0x8001, 0, 0, 0, 1),
        ("SHR r2, 1",         0xFFFFFFFF, 0, 0x4000, 0, 0, 0, 1),  # C = r2[0]
        ("SHL r2, 0",         0xFFFFFFFF, 0, 0x4000, 0, 0, 0, 0),  # n=0: C=0, Z of value
        ("SHR r2, 15",        0xFFFFFFFF, 0, 0, 0, 0, 1, 1),   # C = r2[14]
        ("MOVI r3, 3",        0xFFFFFFFF, 0, 0, 3, 0, 0, 1),
        ("SHL r3, 31",        0xFFFFFFFF, 0, 0, 0x80000000, 0, 0, 1),  # C = r3[1]
        ("SHR r3, 31",        0xFFFFFFFF, 0, 0, 1, 0, 0, 0),   # C = r3[30]
        ("MOV r1, r0",        0xFFFFFFFF, 0xFFFFFFFF, 0, 1, 0, 0, 0),
        ("AND r1, r3",        0xFFFFFFFF, 1, 0, 1, 0, 0, 0),
        ("XOR r1, r3",        0xFFFFFFFF, 0, 0, 1, 0, 1, 0),
        ("OR r1, r0",         0xFFFFFFFF, 0xFFFFFFFF, 0, 1, 0, 0, 0),
        ("ADD r1, r3",        0xFFFFFFFF, 0, 0, 1, 0, 1, 1),
        ("LDSH r3",           0xFFFFFFFF, 0, 0, 1, 1, 1, 1),   # flags unchanged
        ("STSH r2",           0xFFFFFFFF, 0, 1, 1, 1, 1, 1),
        ("RDTIME r0",         99, 0, 1, 1, 1, 1, 1),           # tick 4*24+3
        ("SHL r0, 0",         99, 0, 1, 1, 1, 0, 0),
        ("MOVI r0, 0",        0, 0, 1, 1, 1, 1, 0),
        ("SHL r0, 0",         0, 0, 1, 1, 1, 1, 0),
    ]
    src = ".entry 0, s\ns:\n" + "\n".join(p[0] for p in prog) + "\nHALT\n"
    await env.load_and_start(src)
    await env.run(4 * len(prog) + 8)
    for pc, (txt, r0, r1, r2, r3, sh, z, c) in enumerate(prog):
        cm = env.commit_at(4 * pc + 3)
        got = (cm["pc"], cm["oc"], cm["r0"], cm["r1"], cm["r2"], cm["r3"], cm["sh"],
               cm["z"], cm["cf"], cm["npc"])
        assert got == (pc, C, r0, r1, r2, r3, sh, z, c, pc + 1), (txt, got)
        assert cm["insn"] == env.img.words[pc]
    assert env.commit_at(4 * len(prog) + 3)["oc"] == C


# --------------------------------------------------------------------------- sec. 7.2
@cocotb.test()
async def test_control_flow(dut):
    env = await new_env(dut)
    src = """
    .entry 0, main
    main: CALL sub      ; 0  t3  lr = 1, PC = 5
          LDLC 3        ; 1  t15 lc = 3
    loop: DJNZ loop     ; 2  t19 lc 2 ->2, t23 lc 1 ->2, t27 lc 0 ->3
          JMP l7        ; 3  t31
          .word 0       ; 4
    sub:  MOVI r0, 0    ; 5  t7  Z = 1
          RET           ; 6  t11 PC = lr = 1
    l7:   JZ l9         ; 7  t35 Z=1 taken
          .word 0       ; 8
    l9:   JNZ bad       ; 9  t39 not taken
          JC bad        ; 10 t43 C=0 not taken
          JNC l13       ; 11 t47 taken
    bad:  .word 0       ; 12
    l13:  HALT          ; 13 t51
    """
    await env.load_and_start(src)
    await env.run(60)
    pcs = [0, 5, 6, 1, 2, 2, 2, 3, 7, 9, 10, 11, 13]
    npcs = [5, 6, 1, 2, 2, 2, 3, 7, 9, 10, 11, 13, 13]
    exp = [(4 * i + 3, 0, pc, C) for i, pc in enumerate(pcs)]
    assert env.commit_summary() == exp, env.commit_summary()
    assert [c["npc"] for c in env.commits()] == npcs
    assert [env.commit_at(t)["lc"] for t in (15, 19, 23, 27)] == [3, 2, 1, 0]
    # sel 7: lr 1, PC 13, HALTED, Z 1, C 0
    assert await env.rd(7) == (1 << 16) | (13 << 8) | (HALTED << 2) | (1 << 1)


# --------------------------------------------------------------------------- sec. 7 PC_RANGE
@cocotb.test()
async def test_pc_range(dut):
    env = await new_env(dut)
    ff255 = ff_word(PC_RANGE, 0, 0, 255)
    await env.load_and_start(".entry 0, s\n.org 254\ns: NOP\nNOP")
    await env.run(12)
    assert env.commit_summary() == [(3, 0, 254, C), (7, 0, 255, F)]
    assert await env.rd(14) == ff255 and await env.rd(15) == 7
    # CALL at 255 always faults; lr unchanged
    await env.load_and_start(".entry 0, s\n.org 255\ns: CALL 0")
    await env.run(8)
    assert env.commit_summary() == [(3, 0, 255, F)]
    assert await env.rd(14) == ff255 and await env.rd(7) == (255 << 8) | (FAULTED << 2)
    # JMP at 255 is not sequential
    await env.load_and_start(".entry 0, s\n.org 0\nHALT\n.org 255\ns: JMP 0")
    await env.run(10)
    assert env.commit_summary() == [(3, 0, 255, C), (7, 0, 0, C)]
    assert await env.rd(14) == 0
    # HALT at 255: PC unchanged, not sequential
    await env.load_and_start(".entry 0, s\n.org 255\ns: HALT")
    await env.run(6)
    assert env.commit_summary() == [(3, 0, 255, C)]
    # not-taken branch at 255
    await env.load_and_start(".entry 0, s\n.org 255\ns: JZ 0")
    await env.run(6)
    assert env.commit_summary() == [(3, 0, 255, F)]
    # wait-class at 255: WAIT is fine, completion faults
    await env.load_and_start(".entry 0, s\n.org 254\ns: ADDT 10\nWAITT")
    await env.run(14)
    assert env.commit_summary() == [(3, 0, 254, C), (7, 0, 255, W), (11, 0, 255, F)]
    assert await env.rd(15) == 11


# --------------------------------------------------------------------------- sec. 7 ILLEGAL
@cocotb.test()
async def test_illegal(dut):
    env = await new_env(dut)
    for words in ([0x00000000, 0x01000001, 0xFFFFFFFF, 0x05000000],
                  [0x08080000, 0x2C080000, 0x29000100, 0x31000001]):
        # ctx k at pc k -> fetch k, commit k+3
        src = "\n".join(f".entry {k}, w{k}" for k in range(4)) + "\n" + \
              "\n".join(f"w{k}: .word {w:#x}" for k, w in enumerate(words))
        await env.load_and_start(src)
        await env.run(10)
        assert env.commit_summary() == [(k + 3, k, k, F) for k in range(4)]
        assert [c["insn"] for c in env.commits()] == words
        # first fault is ctx 0 at tick 3; later faults do not overwrite it
        assert await env.rd(14) == ff_word(ILLEGAL, 0, 0, 0)
        assert await env.rd(15) == 3
        assert env.trace[-1]["state"] == 0xFF


# --------------------------------------------------------------------------- sec. 7.4
@cocotb.test()
async def test_emitx_emitr(dut):
    env = await new_env(dut)
    src = """
    .entry 0, s
    .own 0, 0
    .own 1, 0
    .own 2, 0
    .own 3, 0
    s: MOVI r0, 5             ; 0  t3
       MOVHI r0, 0x8000       ; 1  t7  r0 = 0x80000005
       LDSH r0                ; 2  t11 sh = 0x80000005
       ADDT 60                ; 3  t15 tmr 60
       EMITX 1, MSB, 1        ; 4  t19 b = sh[31] = 1 -> {60, 1, 1, 1}; sh = 0xA, C = 1
       ADDT 1                 ; 5  t23
       EMITX 1, LSB, 1        ; 6  t27 b = sh[0] = 0 -> value 0; sh = 5, C = 0
       ADDT 1                 ; 7  t31
       MOVI r1, 0x0C          ; 8  t35
       EMITR 0x0E, r1, 0x0E   ; 9  t39 d 62 value r1[7:0] = 0x0C
       ADDT 1                 ; 10 t43
       EMITX 2, LSB, 2        ; 11 t47 b = sh[0] = 1 -> value 0x02; sh = 2, C = 1
       STSH r2                ; 12 t51 r2 = 2
       HALT                   ; 13 t55 W, t59 W, t63 C (63 retires at E_63)
    """
    await env.load_and_start(src)
    await env.run(70)
    s = env.commit_summary()
    assert s == [(4 * pc + 3, 0, pc, C) for pc in range(13)] + \
        [(55, 0, 13, W), (59, 0, 13, W), (63, 0, 13, C)], s
    for t, sh, cf in ((19, 0xA, 1), (27, 5, 0), (47, 2, 1)):
        cm = env.commit_at(t)
        assert (cm["sh"], cm["cf"]) == (sh, cf), (t, cm)
    assert env.commit_at(51)["r2"] == 2
    tr = env.trace
    for t in range(70):
        if t <= 60:
            want = (0, 0)
        elif t == 61:
            want = (0x01, 0x01)
        elif t == 62:
            want = (0x00, 0x01)
        elif t == 63:
            want = (0x0C, 0x0F)
        else:
            want = (0x0E, 0x0F)
        assert (tr[t]["out"], tr[t]["oe"]) == want, (t, tr[t])


# --------------------------------------------------------------------------- sec. 5
@cocotb.test()
async def test_open_drain(dut):
    env = await new_env(dut)
    src = """
    .entry 0, a
    .entry 1, b
    .own 4, 0
    .own 5, 0
    .od 4
    a: ADDT 20                 ; t3
       EMIT 0x30, 0x20, 0x30   ; t7  d 20: pin4 val 0 oe 1 (drive low), pin5 val 1 oe 1
       ADDT 4                  ; t11
       EMIT 0x10, 0x10, 0x10   ; t15 d 24: pin4 val 1 oe 1 -> released
       ADDT 4                  ; t19
       EMIT 0x10, 0x00, 0x00   ; t23 d 28: pin4 val 0 oe 0 -> released
       HALT                    ; t27 W, t31 C
    b: NOP                     ; t4
       NOP                     ; t8
       NOP                     ; t12
       NOP                     ; t16
       NOP                     ; t20
       GETP r0                 ; t24: sync during 24 = uio_in during 22: pin4 low -> 0xEF
       GETP r1                 ; t28: uio_in during 26: released, pulled up -> 0xFF
       HALT                    ; t32
    """
    # wired bus: uio_in = oe ? out : 1 (pull-ups)
    await env.load_and_start(src, ext=0xFF, lb=1)
    await env.run(40)
    tr = env.trace
    for t in range(40):
        if t <= 20:
            want = (0x00, 0x00)
        elif t <= 24:
            want = (0x20, 0x30)
        else:
            want = (0x20, 0x20)
        assert (tr[t]["out"], tr[t]["oe"]) == want, (t, tr[t])
        assert tr[t]["out"] & 0x10 == 0       # open-drain pin never driven high
    assert env.commit_at(24)["r0"] == 0xEF and env.commit_at(28)["r1"] == 0xFF
    assert env.commit_at(27)["oc"] == W and env.commit_at(31)["oc"] == C


# --------------------------------------------------------------------------- sec. 6.3 SAMPLE, 7.3 GETI
@cocotb.test()
async def test_sample_geti(dut):
    """v1.1 (D-20): GETI waits while the FIFO holds a SAMPLE, so the sample due at the
    GETI's own commit tick is included instead of spilling into the next word."""
    env = await new_env(dut)
    src = """
    .entry 0, s
    s: ADDT 20          ; t3
       SAMPLE 2, LSB    ; t7  d 20
       ADDT 1           ; t11
       SAMPLE 2, LSB    ; t15 d 21
       ADDT 6           ; t19
       SAMPLE 2, MSB    ; t23 d 27 (= first commit tick of the GETI below)
       GETI r0          ; t27 WAIT (SAMPLE d 27 in the FIFO); t31 r0 = all three bits
       GETI r1          ; t35 r1 = 0
       HALT             ; t39
    """

    # want sync[2]: 1 at 20, 0 at 21, 1 at 27 -> uio_in[2] = 1 (<=18), 0 (19..24), 1 (>=25)
    def stim(t):
        dut.ext.value = 0x04 if (t <= 18 or t >= 25) else 0

    await env.load_and_start(src, ext=0x04)
    await env.run(44, stim=stim, sels=[(8, 0)])
    tr = env.trace
    # E_20: ish = (0 >> 1) | 1<<31; E_21: (0x80000000 >> 1) | 0 = 0x40000000
    assert tr[20]["sel"][(8, 0)] == 0
    assert tr[21]["sel"][(8, 0)] == 0x80000000
    assert all(tr[t]["sel"][(8, 0)] == 0x40000000 for t in range(22, 28))
    # E_27: no GETI clear (it waited); MSB-first shift of sync_27[2] = 1 -> 0x80000001
    assert all(tr[t]["sel"][(8, 0)] == 0x80000001 for t in range(28, 32))
    assert tr[32]["sel"][(8, 0)] == 0
    assert env.commit_at(27)["oc"] == W
    assert (env.commit_at(31)["r0"], env.commit_at(31)["z"]) == (0x80000001, 0)
    assert (env.commit_at(35)["r1"], env.commit_at(35)["z"]) == (0, 1)


@cocotb.test()
async def test_geti_ignores_retired_sample(dut):
    """GETI waits only for SAMPLEs still in the FIFO: an entry slot keeps its old contents
    after it retires, so a retired SAMPLE one slot past the tail must not count."""
    env = await new_env(dut)
    src = """
    .entry 0, s
    .own 0, 0
    s: ADDT 40          ; t3
       EMIT 1, 0, 1     ; t7   slot 0, d 40
       ADDT 1           ; t11
       SAMPLE 2, LSB    ; t15  slot 1, d 41
       ADDT 1           ; t19
       EMIT 1, 0, 1     ; t23  slot 2, d 42
       ADDT 1           ; t27
       EMIT 1, 0, 1     ; t31  slot 3, d 43; all four retire at E_40..E_43: head 0, count 0
       ADDT 100         ; t35
       NOP              ; t39
       NOP              ; t43
       EMIT 1, 0, 1     ; t47  slot 0, d 143: count 1, slot 1 still holds the old SAMPLE
       GETI r0          ; t51  no SAMPLE queued: completes
       HALT             ; t55
    """
    await env.load_and_start(src)
    await env.run(60)
    assert env.commit_at(51)["oc"] == C, env.commit_summary()[-4:]
    assert env.commit_at(55)["pc"] == 13


# --------------------------------------------------------------------------- sec. 6.4
@cocotb.test()
async def test_expect_checker(dut):
    env = await new_env(dut)
    src = """
    .entry 0, s
    s: ADDT 30                       ; 0 t3
       EXPECT 0x01, 0x01, HOLD, 3    ; 1 t7  d 30: ticks 30..33          -> pass at 33
       ADDT 10                       ; 2 t11
       EXPECT 0x01, 0x01, HOLD, 4    ; 3 t15 d 40: 40..44, sync[0]=0 at 42 -> fail at 42
       ADDT 10                       ; 4 t19
       EXPECT 0x03, 0x02, ARRIVE, 5  ; 5 t23 d 50: 50..55, (sync&3)==2 at 53 -> pass at 53
       ADDT 10                       ; 6 t27
       EXPECT 0x01, 0x01, ARRIVE, 2  ; 7 t31 d 60: 60..62 never         -> fail at 62
       GETX r0                       ; 8 t35 pass 1, fail 0, inactive
       HALT                          ; 9 t39..t59 W (60 queued), t63 C
    """

    # sync(u): <=41: 0x01, 42..49: 0x04, 50..52: 0x01, >=53: 0x02; ext(t) = sync(t+2)
    def sync_at(u):
        if u <= 41:
            return 0x01
        if u <= 49:
            return 0x04
        if u <= 52:
            return 0x01
        return 0x02

    def stim(t):
        dut.ext.value = sync_at(t + 2)

    await env.load_and_start(src, ext=0x01)
    await env.run(70, stim=stim, sels=[(10, 0)])
    tr = env.trace

    def s10(t):
        return tr[t]["sel"][(10, 0)]

    def act(t):
        return (s10(t) >> 1) & 1

    # active = between start (E_d) and stop (E_u): during d+1 .. u
    active = set(range(31, 34)) | set(range(41, 43)) | set(range(51, 54)) | set(range(61, 63))
    for t in range(70):
        assert act(t) == (1 if t in active else 0), (t, hex(s10(t)))
    # counts: pass from 34 and 54, fail from 43 and 63
    for t in range(70):
        p = (t >= 34) + (t >= 54)
        f = (t >= 43) + (t >= 63)
        assert (s10(t) >> 16) & 0xFF == p and s10(t) >> 24 == f, (t, hex(s10(t)))
        assert s10(t) & 1 == (1 if t >= 43 else 0)
    assert env.commit_at(35)["r0"] == 0x00010000
    assert env.commit_at(59)["oc"] == W and env.commit_at(63)["oc"] == C
    # final: fail 2, pass 2, max_occ 3 (t23: Q {30, 40}), count 0, inactive, failed
    assert await env.rd(10) == 0x02020301
    assert await env.rd(11) == 42
    assert await env.rd(12) == (0x01 << 16) | (0x01 << 8) | 0x04


@cocotb.test()
async def test_halt_waits_checker(dut):
    env = await new_env(dut)
    src = ".entry 0, s\ns: ADDT 10\nEXPECT 1, 1, HOLD, 17\nHALT"
    # sync[0] = 1 throughout: window 10..27 passes at E_27 -> not active after E_27
    await env.load_and_start(src, ext=0x01)
    await env.run(34)
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, C)] + \
        [(t, 0, 2, W) for t in (11, 15, 19, 23)] + [(27, 0, 2, C)]
    assert await env.rd(10) == (1 << 16) | (1 << 8)
    # sync[0] = 0: HOLD fails at tick 10, so HALT completes at its first slot (11)
    await env.load_and_start(src, ext=0x00)
    await env.run(16)
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, C), (11, 0, 2, C)]
    assert await env.rd(10) == (1 << 24) | (1 << 8) | 1
    assert await env.rd(11) == 10
    assert await env.rd(12) == (1 << 16) | (1 << 8) | 0


# --------------------------------------------------------------------------- sec. 8
@cocotb.test()
async def test_fault_flush_release(dut):
    env = await new_env(dut)
    src = """
    .entry 0, a
    .entry 1, b
    .entry 2, c
    .own 0, 0
    .own 1, 0
    .own 2, 1
    a:  ADDT 20         ; 0 t3
        EMIT 3, 3, 3    ; 1 t7  d 20: pins 0,1 high from 21
        ADDT 10         ; 2 t11
        EMIT 1, 0, 1    ; 3 t15 d 30: pin0 low from 31
        ADDT 20         ; 4 t19
        EMIT 2, 0, 2    ; 5 t23 d 50: flushed by the fault, never applied
        LDLC 3          ; 6 t27
    la: DJNZ la         ; 7 t31, t35, t39
        .word 0         ; 8 t43 ILLEGAL: flush, release pins 0,1 (oe=0, val kept)
    b:  ADDT 20         ; 9  t4
        EMIT 4, 4, 4    ; 10 t8  d 20
        ADDT 40         ; 11 t12
        EMIT 4, 0, 4    ; 12 t16 d 60
        HALT            ; 13 t20..t56 W, t60 C
    c:  LDLC 20         ; 14 t5
    lc: DJNZ lc         ; 15 t9 .. t85
        .word 0         ; 16 t89 ILLEGAL (second fault: first-fault kept)
    """
    await env.load_and_start(src)
    await env.run(96, sels=[(10, 0)])
    tr = env.trace
    for t in range(96):
        if t <= 20:
            want = (0, 0)
        elif t <= 30:
            want = (0x07, 0x07)
        elif t <= 43:
            want = (0x06, 0x07)
        elif t <= 60:
            want = (0x06, 0x04)
        else:
            want = (0x02, 0x04)
        assert (tr[t]["out"], tr[t]["oe"]) == want, (t, tr[t])
    assert env.commit_at(43)["oc"] == F and env.commit_at(43)["pc"] == 8
    assert env.commit_at(89)["oc"] == F and env.commit_at(60)["oc"] == C
    assert (tr[43]["sel"][(10, 0)] >> 4) & 7 == 1 and (tr[44]["sel"][(10, 0)] >> 4) & 7 == 0
    assert tr[43]["fv"] == 0 and tr[44]["fv"] == 1
    assert tr[44]["state"] & 3 == FAULTED
    assert await env.rd(14) == ff_word(ILLEGAL, 0, 0, 8)
    assert await env.rd(15) == 43
    assert tr[-1]["state"] == (IDLE << 6) | (FAULTED << 4) | (HALTED << 2) | FAULTED


# --------------------------------------------------------------------------- sec. 9
@cocotb.test()
async def test_stop(dut):
    env = await new_env(dut)
    src = """
    .entry 0, a
    .entry 3, m
    .own 0, 0
    a: ADDT 20                  ; 0 t3
       EXPECT 2, 0, HOLD, 100   ; 1 t7  d 20 (active 21..)
       ADDT 1                   ; 2 t11
       EMIT 1, 1, 1             ; 3 t15 d 21
       ADDT 9                   ; 4 t19
       EMIT 1, 0, 1             ; 5 t23 d 30: due at the stop edge -> must not retire
    l: JMP l                    ; 6 t27, t31 ...
    m: JMP m                    ; 7 ctx3 commits 6, 10, ..., 30 (30 discarded by stop)
    """
    await env.load_and_start(src)

    def stim(t):
        dut.stop.value = 1 if t == 30 else 0

    await env.run(50, stim=stim, sels=[(10, 0)])
    tr = env.trace
    assert max(c["c"] for c in env.commits()) == 27      # no commit at E_30 or later
    assert tr[31]["cv"] == 0
    assert tr[30]["running"] == 1 and tr[31]["running"] == 0
    assert all((tr[t]["out"], tr[t]["oe"]) == (1, 1) for t in range(22, 31))
    assert all((tr[t]["out"], tr[t]["oe"]) == (1, 0) for t in range(31, 50))   # val kept
    assert tr[31]["state"] == (HALTED << 6) | (HALTED)
    assert (tr[30]["sel"][(10, 0)] >> 1) & 1 == 1
    # after stop: count 0, inactive, no pass/fail; max_occ 2 (t15: Q {20})
    assert all(tr[t]["sel"][(10, 0)] == (2 << 8) for t in range(31, 50))
    assert tr[49]["imem"] is not None


LU_STOP_SRC = """
.entry 0, s
.tape 0xF0, 0, 0x00800010, 0
.tape 0xE0, 0x300000F0
s: LDT r0, r3, 0xE0
   LINE r0                  ; CONFIG: the unit reads T[F0], T[F1], T[F2] in its own cycles
l: JMP l
"""


async def lu_stop_run(dut, stop_at=None, n=60):
    """LU_STOP_SRC from a fresh reset, tape loaded while stopped; per cycle, the line unit's
    port request and the tape strobes. Stop is asserted in cycle `stop_at`."""
    env = await new_env(dut)
    img = assemble(LU_STOP_SRC)
    for a, w in enumerate(img.tape):
        if w:
            dut.tl_addr.value, dut.tl_data.value, dut.tl_we.value = a, w, 1
            await FallingEdge(dut.clk)
    dut.tl_we.value = 0
    await env.load_and_start(LU_STOP_SRC)
    rows = []
    for t in range(n):
        dut.stop.value = 1 if t == stop_at else 0
        await Timer(1, unit="ns")
        rows.append((iv(dut.u_core.lu_port), iv(dut.tape_re), iv(dut.tape_we)))
        await FallingEdge(dut.clk)
    dut.stop.value = 0
    return rows


@cocotb.test()
async def test_line_unit_makes_no_access_at_a_stop_edge(dut):
    """11-SEMANTICS 6.6: at a start or stop edge the line unit makes no tape access, even in
    a cycle it scheduled one (here a CONFIG's second word). Found by mutation: an access
    allowed at the stop edge changed nothing any other test looked at."""
    rows = await lu_stop_run(dut)
    sched = [t for t, (port, re, we) in enumerate(rows) if port]
    assert sched and all(rows[t][1] == 1 for t in sched), rows   # the reads happen
    rows = await lu_stop_run(dut, stop_at=sched[0])
    assert rows[sched[0]][0] == 1 and rows[sched[0]][1:] == (0, 0), rows[sched[0]]


@cocotb.test()
async def test_debug_port_after_stop_shows_final_registers(dut):
    """After a stop, selects 0-8 through the debug port read the registers as the run left
    them, whichever of the context's four slot phases the stop fell in: the port reloads
    its cached field once when the run stops (D-41). Found by mutation: a cache that kept
    its last in-run value passed every other test."""
    src = ".entry 0, a\na:\n" + "".join(f"   MOVI r0, {k}\n" for k in range(1, 21)) + "l: JMP l\n"
    for stop_at in range(40, 44):
        env = await new_env(dut)
        await env.load_and_start(src)

        def stim(t):
            dut.stop.value = 1 if t == stop_at else 0

        await env.run(stop_at + 12, stim=stim, full=False)
        dut.dbg_sel.value = 0
        dut.dbg_sel_ctx.value = 0
        await Timer(1, unit="ns")
        final = ctx_reg(dut.u_core, 0, 0)
        assert 1 < final < 20 and int(dut.dbg_rdata.value) == final, (stop_at, final, int(dut.dbg_rdata.value))


@cocotb.test()
async def test_start_reinitialises_context(dut):
    """sec. 9 Start: a second START clears r0-r3, sh and tmr even when the previous run
    left them non-zero (with clock-gated banks, D-29, START is an enable term of its own;
    a run from reset alone cannot tell it apart from the reset value)."""
    env = await new_env(dut)
    await env.load_and_start("""
    .entry 0, s
    s: MOVI r0, 7
       MOVI r1, 9
       MOVI r2, 11
       MOVI r3, 13
       LDSH r0
       ADDT 100
       HALT
    """)
    await env.run(40)
    last = env.commits()[-2]                       # ADDT
    assert (last["r0"], last["r3"], last["sh"], last["tmr"]) == (7, 13, 7, 100), last
    await env.load_and_start(".entry 0, s\ns: NOP\nHALT")
    await env.run(8)
    c = env.commit_at(3)
    assert (c["r0"], c["r1"], c["r2"], c["r3"], c["sh"], c["tmr"]) == (0, 0, 0, 0, 0, 0), c


# --------------------------------------------------------------------------- sec. 9 Reset
# Every observable register before the first START. A host can read the debug selects
# over SPI at any time, so a wrong reset value is a visible bug, not a hidden one; the
# runs above always START first, which hides it.
RESET_SEL = {9: 0xFFFF0000,             # min_lead 0xFFFF, wait_full 0, as after START
             16: 0xFFFFFFFF}            # dg (sec. 10.1); every other select reads 0


@cocotb.test()
async def test_reset_state(dut):
    env = await new_env(dut)
    await env.run(6, full=True)
    tr = env.trace
    assert all((r["out"], r["oe"], r["running"], r["state"], r["fv"], r["irqp"]) == (0, 0, 0, 0, 0, 0)
               for r in tr), tr[0]
    assert all((r["rxf"], r["txe"]) == (0, 0xF) for r in tr), (tr[0]["rxf"], tr[0]["txe"])
    bad = {}
    for s in range(32):
        for k in range(4):
            dut.dbg_sel.value, dut.dbg_sel_ctx.value = s, k
            await Timer(1, unit="ns")
            v = dut.dbg_rdata.value
            if not v.is_resolvable or int(v) != RESET_SEL.get(s, 0):
                bad[f"sel {s} ctx {k}"] = str(v)
    assert not bad, sorted({(k.split()[1], v if "X" not in v else "X") for k, v in bad.items()})


@cocotb.test()
async def test_debug_reads_commit_slot(dut):
    """D-41: selects 0-8 come through the commit stage's multiplexers. During a run the port
    shows a context's registers as of its last commit slot (at most 4 cycles old); after
    the context halts, and while stopped, it shows them exactly, for every context."""
    env = await new_env(dut)
    src = """
    .entry 0, go0
    .entry 1, go1
    .entry 2, go2
    .entry 3, go3
    go0: MOVI r0, 0x1234
         LDLC 4
    l0:  ADDI r2, 1
         DJNZ l0
         HALT
    go1: MOVI r3, 0xBEEF
         LDLC 7
         HALT
    go2: MOVI r0, 3
         SHL r0, 4
         HALT
    go3: MOVI r1, 9
         LDSH r1
         HALT
    """
    await env.load_and_start(src)
    d = dut
    seen = []
    for t in range(80):
        d.dbg_sel.value, d.dbg_sel_ctx.value = 2, 0
        await Timer(1, unit="ns")
        seen.append((int(d.dbg_rdata.value), ctx_reg(d.u_core, 0, 2)))
        await FallingEdge(d.clk)
    # the port lags the register by at most 4 cycles and catches up
    for t, (port, reg) in enumerate(seen):
        assert port in [r for _, r in seen[max(0, t - 4):t + 1]], (t, port, reg)
    assert seen[-1][0] == seen[-1][1]
    for ctx in range(4):
        for sel in range(9):
            d.dbg_sel.value, d.dbg_sel_ctx.value = sel, ctx
            for _ in range(5):
                await FallingEdge(d.clk)
            assert int(d.dbg_rdata.value) == ctx_reg(d.u_core, ctx, sel), (ctx, sel)
    d.stop.value = 1
    await FallingEdge(d.clk)
    d.stop.value = 0
    for ctx in range(4):
        for sel in range(9):
            d.dbg_sel.value, d.dbg_sel_ctx.value = sel, ctx
            await FallingEdge(d.clk)
            await FallingEdge(d.clk)
            assert int(d.dbg_rdata.value) == ctx_reg(d.u_core, ctx, sel), ("stopped", ctx, sel)


# --------------------------------------------------------------------------- sec. 7.1 flags
@cocotb.test()
async def test_logic_zero_flag(dut):
    """Z of AND/OR/XOR is the result == 0, not the adder's: 0x80000000 | 0x80000000 sums to
    0 mod 2^32 but is not zero."""
    env = await new_env(dut)
    await env.load_and_start("""
    .entry 0, s
    s: MOVI r0, 0          ; 0 t3
       MOVHI r0, 0x8000    ; 1 t7   r0 = 0x80000000
       MOV r1, r0          ; 2 t11
       OR r0, r1           ; 3 t15  0x80000000, Z 0
       XOR r1, r1          ; 4 t19  0, Z 1
       MOV r1, r0          ; 5 t23
       AND r0, r1          ; 6 t27  0x80000000, Z 0
       HALT
    """)
    await env.run(40)
    assert (env.commit_at(15)["r0"], env.commit_at(15)["z"]) == (0x80000000, 0)
    assert (env.commit_at(19)["r1"], env.commit_at(19)["z"]) == (0, 1)
    assert (env.commit_at(27)["r0"], env.commit_at(27)["z"]) == (0x80000000, 0)


# --------------------------------------------------------------------------- sec. 7 PC_RANGE
@cocotb.test()
async def test_taken_branch_at_255(dut):
    """A taken JC, JNC or DJNZ at PC 255 is not sequential, so it must not fault PC_RANGE
    (test_pc_range covers JMP and a not-taken JZ)."""
    env = await new_env(dut)
    cases = [
        ("s: CMPI r0, 1\nJMP b", "b: JC t"),       # 0 < 1: C = 1, taken
        ("s: NOP\nJMP b", "b: JNC t"),             # C = 0 after start, taken
        ("s: LDLC 2\nJMP b", "b: DJNZ t"),         # lc 2 -> 1, taken
    ]
    for head, at255 in cases:
        await env.load_and_start(f".entry 0, s\n{head}\n.org 16\nt: HALT\n.org 255\n{at255}")
        await env.run(20)
        assert env.commit_summary()[2:4] == [(11, 0, 255, C), (15, 0, 16, C)], (at255, env.commit_summary())
        assert await env.rd(14) == 0, at255


# --------------------------------------------------------------------------- D-29b
@cocotb.test()
async def test_program_store_reads_on_demand(dut):
    """D-29b: the program store is read only in the fetch slot of a running context, so a
    one-context program leaves it idle three cycles in four, and a stopped chip leaves it
    idle (a power claim: docs/15-POWER.md)."""
    env = await new_env(dut)
    await env.load_and_start(".entry 0, s\ns: JMP s")
    re = []
    for _ in range(40):
        re.append(int(dut.imem_re.value))
        await FallingEdge(dut.clk)
    # context 0 fetches in cycles t = 0 mod 4 of the run (sec. 3)
    assert re == [1 if t % 4 == 0 else 0 for t in range(40)], re
    dut.stop.value = 1
    await FallingEdge(dut.clk)
    dut.stop.value = 0
    for _ in range(8):
        await FallingEdge(dut.clk)
        assert int(dut.imem_re.value) == 0


# --------------------------------------------------------------------------- sec. 9 Stop, 10.1
@cocotb.test()
async def test_digest_frozen_by_stop(dut):
    """sec. 9: after STOP `dg` stops changing, although STOP itself releases driven pins
    (an output change that would fold into the digest during a run)."""
    env = await new_env(dut)
    await env.load_and_start("""
    .entry 0, s
    .own 0, 0
    s: ADDT 10
       EMIT 1, 1, 1         ; drive pin 0 high
    l: JMP l
    """)

    def stim(t):
        dut.stop.value = 1 if t == 40 else 0

    await env.run(60, stim=stim, sels=[(16, 0)])
    tr = env.trace
    assert tr[39]["oe"] == 1 and tr[45]["oe"] == 0      # driven before STOP, released after
    frozen = {tr[t]["sel"][(16, 0)] for t in range(41, 60)}
    assert len(frozen) == 1, [hex(v) for v in sorted(frozen)]


# --------------------------------------------------------------------------- sec. 7.2 IRQ, sec. 10 v1.0.1
@cocotb.test()
async def test_irq_and_clear(dut):
    env = await new_env(dut)
    src = """
    .entry 0, s
    s: IRQ 0x5A    ; t3
       IRQ 0x33    ; t7  code overwritten, pending stays
       NOP         ; t11
       IRQ 0x77    ; t15 (same edge as a host clear: set wins)
       HALT        ; t19
    """

    def stim(t):
        dut.irq_clear.value = 0b0011 if t in (9, 15) else 0

    await env.load_and_start(src)
    await env.run(24, stim=stim, sels=[(13, 0)])
    tr = env.trace
    for t in range(24):
        if t <= 3:
            want = (0, 0x000)
        elif t <= 7:
            want = (1, 0x15A)
        elif t <= 9:
            want = (1, 0x133)
        elif t <= 15:
            want = (0, 0x033)      # cleared at E_9; code kept (spec only clears pending)
        else:
            want = (1, 0x177)
        assert (tr[t]["irqp"], tr[t]["sel"][(13, 0)]) == want, (t, tr[t])
    # start clears the IRQ state
    await env.load_and_start(".entry 0, s\ns: HALT")
    await env.run(2)
    assert env.trace[0]["irqp"] == 0 and await env.rd(13) == 0


# --------------------------------------------------------------------------- sec. 7.5 / 10 mailboxes
@cocotb.test()
async def test_mailboxes(dut):
    env = await new_env(dut)
    src = """
    .entry 0, s
    s: RXREADY      ; 0 t3  Z = RX empty = 1
       POP r0       ; 1 t7  0x11111111
       POP r1       ; 2 t11 0x22222222 (0x33333333 was pushed while full: ignored)
       POP r2       ; 3 t15 W (host push at E_15 not visible), t19 0x44444444
       PUSH r0      ; 4 t23
       PUSH r1      ; 5 t27 TX full
       TXREADY      ; 6 t31 Z = TX full = 1
       PUSH r2      ; 7 t35 W (host pop at E_35 not visible), t39 C
       RXREADY      ; 8 t43 Z = 1
       HALT         ; 9 t47
    """
    pushes = {4: 0x11111111, 5: 0x22222222, 6: 0x33333333, 15: 0x44444444}

    def stim(t):
        if t in pushes:
            dut.mbx_rx_push.value = 1
            dut.mbx_rx_data.value = pushes[t]
        else:
            dut.mbx_rx_push.value = 0
        dut.mbx_tx_pop.value = 1 if t in (35, 41, 42, 43) else 0

    await env.load_and_start(src)
    await env.run(52, stim=stim)
    s = env.commit_summary()
    assert s == [(3, 0, 0, C), (7, 0, 1, C), (11, 0, 2, C), (15, 0, 3, W), (19, 0, 3, C),
                 (23, 0, 4, C), (27, 0, 5, C), (31, 0, 6, C), (35, 0, 7, W), (39, 0, 7, C),
                 (43, 0, 8, C), (47, 0, 9, C)], s
    assert env.commit_at(3)["z"] == 1 and env.commit_at(31)["z"] == 1
    assert env.commit_at(43)["z"] == 1
    assert env.commit_at(7)["r0"] == 0x11111111
    assert env.commit_at(11)["r1"] == 0x22222222
    assert env.commit_at(19)["r2"] == 0x44444444
    tr = env.trace
    assert [tr[t]["rxf"] & 1 for t in (5, 6, 7, 8)] == [0, 1, 1, 0]
    assert tr[23]["txe"] & 1 == 1 and tr[24]["txe"] & 1 == 0
    assert all(tr[t]["txd"] & 0xFFFFFFFF == 0x11111111 for t in range(24, 36))
    assert tr[36]["txd"] & 0xFFFFFFFF == 0x22222222
    assert tr[40]["txd"] & 0xFFFFFFFF == 0x22222222
    assert tr[42]["txd"] & 0xFFFFFFFF == 0x44444444
    assert tr[42]["txe"] & 1 == 0 and tr[43]["txe"] & 1 == 1 and tr[44]["txe"] & 1 == 1
    assert all(tr[t]["txe"] >> 1 == 0b111 for t in range(52))    # other contexts untouched


# --------------------------------------------------------------------------- firmware
@cocotb.test()
async def test_uart_firmware(dut):
    """firmware/uart_tx.asm: 8N1 at 217 ticks/bit on pin 0, bytes 55 4B 21 0A.

    Derivation (sec. 3, 6, 7.4): DRIVE deadlines are 16 (idle high), then
    E_n = 233 + 217(n-1) for n = 1..40 (per byte: start, 8 data bits LSB-first,
    stop).  Pin 0 shows entry n during E_n + 1 onward.  The last entry (n = 40,
    stop bit of byte 3) is at 8696; HALT completes at the first ctx-0 slot
    c = 3 (mod 4) with c >= 8696, i.e. tick 8699.
    """
    env = await new_env(dut)
    src = (REPO / "firmware" / "uart_tx.asm").read_text()
    await env.load_and_start(src)
    await env.run(8820, full=False)
    tr = env.trace
    data = [0x55, 0x4B, 0x21, 0x0A]
    events = [(16, 1)]
    for j, byte in enumerate(data):
        bits = [0] + [(byte >> k) & 1 for k in range(8)] + [1]
        for p, b in enumerate(bits):
            events.append((233 + 217 * (10 * j + p), b))
    assert events[-1][0] == 8696

    def expected(t):
        level, oe = 0, 0
        for d, b in events:
            if t >= d + 1:
                level, oe = b, 1
        return level, oe

    for t in range(len(tr)):
        assert (tr[t]["out"] & 1, tr[t]["oe"] & 1) == expected(t), (t, tr[t])
        assert tr[t]["out"] & 0xFE == 0 and tr[t]["oe"] & 0xFE == 0

    # independent UART decode from the pin waveform
    line = [tr[t]["out"] & 1 if tr[t]["oe"] & 1 else 1 for t in range(len(tr))]
    got, t = [], 0
    while t < len(line) - 1 and len(got) < 4:
        if line[t] == 1 and line[t + 1] == 0:
            s0 = t + 1
            assert line[s0 + 108] == 0                 # start bit centre
            byte = sum(line[s0 + 217 * (k + 1) + 108] << k for k in range(8))
            assert line[s0 + 217 * 9 + 108] == 1       # stop bit
            got.append(byte)
            t = s0 + 217 * 9 + 108
        t += 1
    assert got == data, [hex(b) for b in got]

    halt = [c for c in env.commits() if c["pc"] == 19 and c["oc"] == C]
    assert len(halt) == 1 and halt[0]["c"] == 8699, halt
    assert max(c["c"] for c in env.commits()) == 8699
    # telemetry: min_lead 9 (first EMIT: d 16 at tick 7); max_occupancy 4.
    # wait_full: event 5 (bit 3, first try t83) waits until tick 235: 38 WAITs.
    # For n >= 6 the FIFO paces admission: admit_n = first c = 3 (mod 4) with c >= E_(n-4),
    # i.e. E_(n-4) + ((3-n) mod 4) since E_m = m (mod 4).  The instruction is first tried
    # gap_n ticks after admit_(n-1) (gap 24 for a start bit after a stop bit, else 12), so
    # waits_n = (217 + ((3-n) mod 4) - ((-n) mod 4) - gap_n) / 4
    #         = (220 - gap)/4 if n = 0 (mod 4) else (216 - gap)/4.
    wf = 38
    for n in range(6, 41):
        gap = 24 if (n - 1) % 10 == 0 else 12
        wf += ((220 if n % 4 == 0 else 216) - gap) // 4
    assert wf == 1823
    assert await env.rd(9) == (9 << 16) | wf
    assert await env.rd(10) == (4 << 8)
    dut._log.info("UART: final tick %d, sel9 %#010x, sel10 %#010x",
                  halt[0]["c"], await env.rd(9), await env.rd(10))


@cocotb.test()
async def test_fault_suppresses_same_edge(dut):
    """Sec. 6.3 / 8: an entry due at the faulting edge does not retire; PC_RANGE has no effects."""
    env = await new_env(dut)
    # DRIVE due at E_11, ILLEGAL commit at E_11: pin 0 never changes
    await env.load_and_start(".entry 0, s\n.own 0, 0\ns: ADDT 11\nEMIT 1, 1, 1\n.word 0")
    await env.run(16, sels=[(10, 0)])
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, C), (11, 0, 2, F)]
    assert all((r["out"], r["oe"]) == (0, 0) for r in env.trace)
    assert (env.trace[11]["sel"][(10, 0)] >> 4) & 7 == 1
    assert (env.trace[12]["sel"][(10, 0)] >> 4) & 7 == 0
    # SAMPLE due at E_11 with sync[0] = 1, fault at E_11: ish stays 0
    await env.load_and_start(".entry 0, s\ns: ADDT 11\nSAMPLE 0, MSB\n.word 0", ext=0x01)
    await env.run(16)
    assert env.commit_at(11)["oc"] == F and await env.rd(8) == 0
    # control: same program without the fault shifts the sample in at E_11
    await env.load_and_start(".entry 0, s\ns: ADDT 11\nSAMPLE 0, MSB\nNOP\nHALT", ext=0x01)
    await env.run(16)
    assert await env.rd(8) == 1
    # EMIT at PC 255 that would be admitted -> PC_RANGE, nothing appended, telemetry unchanged
    await env.load_and_start(".entry 0, s\n.own 0, 0\n.org 254\ns: ADDT 10\nEMIT 1, 1, 1")
    await env.run(16)
    assert env.commit_summary() == [(3, 0, 254, C), (7, 0, 255, F)]
    assert await env.rd(14) == ff_word(PC_RANGE, 0, 0, 255)
    assert await env.rd(9) == 0xFFFF0000 and await env.rd(10) == 0
    assert all((r["out"], r["oe"]) == (0, 0) for r in env.trace)


@cocotb.test()
async def test_checker_at_kill_edge(dut):
    """Spec question Q2 (see src/ESC_CORE_SPEC_QUESTIONS.md): a fault or stop at E_u
    deactivates the checker WITHOUT evaluating tick u (no pass/fail, no first_fail)."""
    env = await new_env(dut)
    # EXPECT d 10, HOLD, window 5 (ticks 10..15). sync[0] = 0 only at tick 11
    # (ext 0 only in cycle 9).  The context faults (ILLEGAL) at E_11.
    def stim(t):
        dut.ext.value = 0 if t == 9 else 1

    await env.load_and_start(".entry 0, s\ns: ADDT 10\nEXPECT 1, 1, HOLD, 5\n.word 0", ext=1)
    await env.run(20, stim=stim)
    assert env.commit_at(11)["oc"] == F
    assert await env.rd(10) == (1 << 8)          # max_occ 1; no fail, not active, not failed
    assert await env.rd(11) == 0
    # control: without the fault the same input fails at tick 11
    await env.load_and_start(".entry 0, s\ns: ADDT 10\nEXPECT 1, 1, HOLD, 5\nNOP\nHALT", ext=1)
    await env.run(20, stim=stim)
    assert await env.rd(10) == (1 << 24) | (1 << 8) | 1 and await env.rd(11) == 11

    # stop during the last tick of a passing HOLD window (10..15, stop in cycle 15): no pass
    def stim_stop(t):
        dut.stop.value = 1 if t == 15 else 0

    await env.load_and_start(".entry 0, s\ns: ADDT 10\nEXPECT 1, 1, HOLD, 5\nl: JMP l", ext=1)
    await env.run(20, stim=stim_stop)
    assert await env.rd(10) == (1 << 8)

    # start and stop in the same cycle: start wins (Q5)
    await env.load_and_start(".entry 0, s\ns: NOP\nHALT")
    d = dut
    d.start.value = 1
    d.stop.value = 1
    await FallingEdge(d.clk)
    d.start.value = 0
    d.stop.value = 0
    env.trace = []
    await env.run(10)
    assert env.trace[0]["running"] == 1
    assert env.commit_summary() == [(3, 0, 0, C), (7, 0, 1, C)]
